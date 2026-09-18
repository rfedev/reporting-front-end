"""File watcher service for automatic scanning of the working directory."""

import logging
from pathlib import Path
from typing import Dict, List, Optional
from PySide6.QtCore import QObject, QTimer, Signal

logger = logging.getLogger(__name__)


class FileWatcherService(QObject):
    """Monitors directory for changes using periodic lightweight polling."""

    directory_changed = Signal()
    file_changed = Signal(Path)

    def __init__(self, check_interval_ms: int = 5000, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.check_interval_ms = check_interval_ms
        self.target_dirs: List[Path] = []
        self._file_mtimes: Dict[Path, float] = {}
        self._last_mtime_sum: float = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._check_for_changes)
        self._enabled = True

    def set_target_directories(self, target_dirs: List[Path]) -> None:
        self.target_dirs = target_dirs
        self._file_mtimes = self._compute_file_mtimes()
        self._last_mtime_sum = sum(self._file_mtimes.values())

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if enabled:
            if not self._timer.isActive():
                self._timer.start(self.check_interval_ms)
        else:
            self._timer.stop()

    def is_enabled(self) -> bool:
        return self._enabled

    def update_file_mtime(self, file_path: Path) -> None:
        """Update cached mtime for a file to prevent self-modification trigger loops."""
        try:
            resolved = file_path.resolve()
            if resolved.exists():
                self._file_mtimes[resolved] = resolved.stat().st_mtime
                self._last_mtime_sum = sum(self._file_mtimes.values())
        except Exception:
            pass

    def _compute_file_mtimes(self) -> Dict[Path, float]:
        mtimes: Dict[Path, float] = {}
        for target_dir in self.target_dirs:
            if not target_dir or not target_dir.exists():
                continue
            try:
                for path in target_dir.glob("**/*"):
                    if any(part in {"outputs", ".git", ".venv", "__pycache__", "node_modules"} for part in path.parts):
                        continue
                    if path.is_file() and path.suffix in {".sql", ".json"}:
                        try:
                            mtimes[path.resolve()] = path.stat().st_mtime
                        except Exception:
                            pass
            except Exception as e:
                logger.debug(f"Error computing mtime for {target_dir}: {e}")
        return mtimes

    def _compute_mtime_sum(self) -> float:
        return sum(self._compute_file_mtimes().values())

    def _check_for_changes(self) -> None:
        if not self._enabled or not self.target_dirs:
            return
        current_mtimes = self._compute_file_mtimes()
        if current_mtimes != self._file_mtimes:
            added_or_removed = set(current_mtimes.keys()) != set(self._file_mtimes.keys())
            modified_files = [
                p for p, mt in current_mtimes.items()
                if p in self._file_mtimes and abs(mt - self._file_mtimes[p]) > 1e-4
            ]
            self._file_mtimes = current_mtimes
            self._last_mtime_sum = sum(current_mtimes.values())

            for mf in modified_files:
                logger.info(f"File modified: {mf}")
                self.file_changed.emit(mf)

            if added_or_removed:
                logger.info("Files added or removed, notifying directory_changed listeners.")
                self.directory_changed.emit()

