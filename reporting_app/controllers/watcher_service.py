"""Event-driven file watcher service using QFileSystemWatcher with self-write suppression."""

import logging
import time
from pathlib import Path
from typing import Dict, List, Optional, Set
from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

logger = logging.getLogger(__name__)


class FileWatcherService(QObject):
    """Monitors active report's queries directory and files using QFileSystemWatcher."""

    directory_changed = Signal()
    file_changed = Signal(Path)

    def __init__(self, check_interval_ms: int = 5000, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_qt_file_changed)
        self._watcher.directoryChanged.connect(self._on_qt_directory_changed)

        self._enabled: bool = True
        self._target_report_dir: Optional[Path] = None
        self._target_queries_dir: Optional[Path] = None
        self.target_dirs: List[Path] = []

        # Suppression tracking: path -> expiration_timestamp (float)
        self._suppressed_paths: Dict[Path, float] = {}

        # Debouncer timer to coalesce rapid inotify events (250ms)
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(250)
        self._debounce_timer.timeout.connect(self._on_debounce_timeout)

        self._pending_files: Set[Path] = set()
        self._pending_dir_change: bool = False

    def set_target_report(self, report_dir: Optional[Path]) -> None:
        """Scope file monitoring strictly to the active report's queries/ directory and its files."""
        if report_dir:
            self._target_report_dir = Path(report_dir).resolve()
            queries_dir = self._target_report_dir / "queries"
            if queries_dir.exists():
                self._target_queries_dir = queries_dir.resolve()
            else:
                self._target_queries_dir = None
        else:
            self._target_report_dir = None
            self._target_queries_dir = None

        self._rebuild_watches()

    def set_target_directories(self, target_dirs: List[Path]) -> None:
        """Set fallback target directories for scanning if no active report is chosen."""
        self.target_dirs = [Path(d).resolve() for d in target_dirs if d and Path(d).exists()]
        if not self._target_report_dir:
            self._rebuild_watches()

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable watcher reactions."""
        self._enabled = enabled
        if not enabled:
            self._pending_files.clear()
            self._pending_dir_change = False
            self._debounce_timer.stop()

    def stop(self) -> None:
        """Stop file watcher, remove all watched paths, and stop timers."""
        self.set_enabled(False)
        try:
            files = self._watcher.files()
            if files:
                self._watcher.removePaths(files)
            dirs = self._watcher.directories()
            if dirs:
                self._watcher.removePaths(dirs)
        except Exception as e:
            logger.debug(f"Error stopping watcher paths: {e}")

    def is_enabled(self) -> bool:
        return self._enabled

    def suppress(self, file_path: Path, duration_seconds: float = 1.0) -> None:
        """Register a path to suppress file watcher reactions for self-writes."""
        try:
            resolved = Path(file_path).resolve()
            self._suppressed_paths[resolved] = time.time() + duration_seconds
            logger.debug(f"Suppressed watcher events for {resolved} for {duration_seconds}s")
        except Exception as e:
            logger.debug(f"Error suppressing file path {file_path}: {e}")

    def update_file_mtime(self, file_path: Path) -> None:
        """Backward-compatible hook that routes to self.suppress()."""
        self.suppress(file_path)

    def _rebuild_watches(self) -> None:
        """Clear and re-add paths to the native QFileSystemWatcher."""
        existing_files = self._watcher.files()
        if existing_files:
            self._watcher.removePaths(existing_files)
        existing_dirs = self._watcher.directories()
        if existing_dirs:
            self._watcher.removePaths(existing_dirs)

        if not self._enabled:
            return

        dirs_to_watch: Set[str] = set()
        files_to_watch: Set[str] = set()

        if self._target_queries_dir and self._target_queries_dir.exists():
            dirs_to_watch.add(str(self._target_queries_dir))
            for f in self._target_queries_dir.iterdir():
                if f.is_file() and f.suffix.lower() in {".sql", ".json"}:
                    files_to_watch.add(str(f.resolve()))
        elif self.target_dirs:
            for td in self.target_dirs:
                if td.exists():
                    dirs_to_watch.add(str(td))
                    qdir = td / "queries"
                    if qdir.exists():
                        dirs_to_watch.add(str(qdir.resolve()))
                        for f in qdir.iterdir():
                            if f.is_file() and f.suffix.lower() in {".sql", ".json"}:
                                files_to_watch.add(str(f.resolve()))

        if dirs_to_watch:
            self._watcher.addPaths(list(dirs_to_watch))
        if files_to_watch:
            self._watcher.addPaths(list(files_to_watch))

    def _on_qt_file_changed(self, path_str: str) -> None:
        if not self._enabled:
            return

        p = Path(path_str).resolve()
        now = time.time()

        # Clean expired suppressions
        self._suppressed_paths = {k: v for k, v in self._suppressed_paths.items() if v > now}

        if p in self._suppressed_paths:
            logger.debug(f"Ignoring suppressed self-write for {p}")
            if p.exists() and path_str not in self._watcher.files():
                self._watcher.addPath(path_str)
            return

        if p.exists() and path_str not in self._watcher.files():
            self._watcher.addPath(path_str)

        self._pending_files.add(p)
        self._debounce_timer.start()

    def _on_qt_directory_changed(self, path_str: str) -> None:
        if not self._enabled:
            return

        self._pending_dir_change = True
        self._debounce_timer.start()

    def _on_debounce_timeout(self) -> None:
        if not self._enabled:
            return

        now = time.time()
        self._suppressed_paths = {k: v for k, v in self._suppressed_paths.items() if v > now}

        if self._pending_dir_change:
            self._pending_dir_change = False
            self._rearm_unwatched_files()
            self.directory_changed.emit()

        changed_files = list(self._pending_files)
        self._pending_files.clear()

        for p in changed_files:
            if p in self._suppressed_paths:
                continue
            self.file_changed.emit(p)

    def _rearm_unwatched_files(self) -> None:
        if self._target_queries_dir and self._target_queries_dir.exists():
            current_files = set(self._watcher.files())
            for f in self._target_queries_dir.iterdir():
                if f.is_file() and f.suffix.lower() in {".sql", ".json"}:
                    f_str = str(f.resolve())
                    if f_str not in current_files:
                        self._watcher.addPath(f_str)
