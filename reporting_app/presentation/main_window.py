"""Main application window compatibility module for the unified Process Flow Editor."""

from typing import Optional
from PySide6.QtWidgets import QWidget

from reporting_app.controllers.app_controller import AppController
from reporting_app.presentation.flow_editor.editor_window import ProcessFlowEditorWindow
from reporting_app.presentation.flow_editor.report_tree_widget import (
    AddReportDialog,
    NewProcessFlowDialog,
)


class MainWindow(ProcessFlowEditorWindow):
    """Unified MainWindow backed directly by ProcessFlowEditorWindow."""

    def __init__(self, controller: Optional[AppController] = None, parent: Optional[QWidget] = None):
        if controller and not controller.reports_by_key:
            controller.initialize()
        super().__init__(
            report=controller.active_report if controller else None,
            app_controller=controller,
            parent=parent,
        )


__all__ = ["AddReportDialog", "MainWindow", "NewProcessFlowDialog"]
