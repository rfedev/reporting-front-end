"""File watcher service for automatic scanning of the working directory."""

import logging
from pathlib import Path
from typing import Optional
from PySide6.QtCore import QObject, QTimer, Signal

logger = logging.getLogger(__name__)


class FileWatcherService(QObject):
    """Monitors directory for changes using periodic lightweight polling."""

    directory_changed = Signal()

    def __init__(self, check_interval_ms: int = 5000, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.check_interval_ms = check_interval_ms
        self.target_dir: Optional[Path] = None
        self._last_mtime_sum: float = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._check_for_changes)
        self._enabled = True

    def set_target_directory(self, target_dir: Path) -> None:
        self.target_dir = target_dir
        self._last_mtime_sum = self._compute_mtime_sum()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if enabled:
            if not self._timer.isActive():
                self._timer.start(self.check_interval_ms)
        else:
            self._timer.stop()

    def is_enabled(self) -> bool:
        return self._enabled

    def _compute_mtime_sum(self) -> float:
        if not self.target_dir or not self.target_dir.exists():
            return 0.0
        total: float = 0.0
        try:
            # Check up to depth 3 (working_dir / report / [code] / files)
            for path in self.target_dir.glob("**/*"):
                if path.suffix in {".sql", ".json"} or path.is_dir():
                    total += path.stat().st_mtime
        except Exception as e:
            logger.debug(f"Error computing mtime: {e}")
        return total

    def _check_for_changes(self) -> None:
        if not self._enabled or not self.target_dir or not self.target_dir.exists():
            return
        current_sum = self._compute_mtime_sum()
        if abs(current_sum - self._last_mtime_sum) > 1e-4:
            self._last_mtime_sum = current_sum
            logger.info("File system change detected, notifying listeners.")
            self.directory_changed.emit()
