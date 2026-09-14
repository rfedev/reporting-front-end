"""Application entry point and setup."""

import logging
import sys
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from reporting_app.controllers.app_controller import AppController
from reporting_app.persistence.database import DatabaseManager
from reporting_app.presentation.main_window import MainWindow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def apply_dark_theme(app: QApplication) -> None:
    """Apply a modern clean dark palette matching NodeGraphQt visual styling."""
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(36, 39, 44))
    palette.setColor(QPalette.WindowText, QColor(220, 220, 220))
    palette.setColor(QPalette.Base, QColor(25, 27, 30))
    palette.setColor(QPalette.AlternateBase, QColor(36, 39, 44))
    palette.setColor(QPalette.ToolTipBase, QColor(25, 27, 30))
    palette.setColor(QPalette.ToolTipText, QColor(220, 220, 220))
    palette.setColor(QPalette.Text, QColor(220, 220, 220))
    palette.setColor(QPalette.Button, QColor(45, 49, 56))
    palette.setColor(QPalette.ButtonText, QColor(220, 220, 220))
    palette.setColor(QPalette.BrightText, QColor(255, 100, 100))
    palette.setColor(QPalette.Link, QColor(42, 130, 218))
    palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    # Disabled state colors
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(110, 118, 129))
    palette.setColor(QPalette.Disabled, QPalette.WindowText, QColor(110, 118, 129))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor(110, 118, 129))
    palette.setColor(QPalette.Disabled, QPalette.Button, QColor(32, 35, 40))
    app.setPalette(palette)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Reporting Front End")
    apply_dark_theme(app)

    db_path = Path(".reporting_app.db")
    db_manager = DatabaseManager(db_path=db_path)

    controller = AppController(db_manager=db_manager)
    window = MainWindow(controller=controller)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
