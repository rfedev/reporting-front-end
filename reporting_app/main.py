import logging
import os
from pathlib import Path
import sys
import warnings

from reporting_app.core.bigquery_client_manager import (
    close_bigquery_clients,
    configure_bigquery_env,
    prewarm_bigquery_async,
)

# Configure NO_GCE_CHECK=true and suppress noisy ADC warnings before any SDK import
configure_bigquery_env()

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from reporting_app.controllers.app_controller import AppController
from reporting_app.persistence.database import DatabaseManager
from reporting_app.presentation.flow_editor.editor_window import ProcessFlowEditorWindow

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
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.DisableProcessWindowsGhosting()
        except Exception as e:
            logging.getLogger(__name__).debug(f"DisableProcessWindowsGhosting failed: {e}")

    app = QApplication(sys.argv)
    app.setApplicationName("Reporting Front End")
    apply_dark_theme(app)

    db_path = Path(".reporting_app.db")
    db_manager = DatabaseManager(db_path=db_path)

    controller = AppController(db_manager=db_manager)
    warm_project = None
    try:
        wb = controller.repo.get_workbench_dataset().strip()
        if wb and "." in wb:
            proj = wb.split(".")[0].strip()
            if proj:
                warm_project = proj
                if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
                    os.environ["GOOGLE_CLOUD_PROJECT"] = proj
    except Exception:
        pass

    controller.initialize()
    window = ProcessFlowEditorWindow(app_controller=controller)
    window.show()

    # Pre-warm google-cloud-bigquery, pandas, and pooled client off the UI thread after window appears
    prewarm_bigquery_async(project_id=warm_project, include_pandas=True)

    def on_about_to_quit():
        if hasattr(controller, "watcher"):
            try:
                controller.watcher.stop()
            except Exception:
                pass
        try:
            if window.isVisible():
                window.close()
        except Exception:
            pass
        close_bigquery_clients()
        QApplication.processEvents()

    app.aboutToQuit.connect(on_about_to_quit)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
