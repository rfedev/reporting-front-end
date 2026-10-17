"""Lower-right Execution Status & Progress Tab and Canvas Execution Controller."""

from pathlib import Path
import time
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from reporting_app.controllers.flow_runner import FlowRunner, FlowWorker
from reporting_app.presentation.flow_editor.error_dialog import FlowErrorDialog
from reporting_app.presentation.flow_editor.nodes import ImportCsvNode, QueryNode


def format_mm_ss(seconds: float) -> str:
    """Format elapsed seconds as mm:ss."""
    total_sec = max(0, int(seconds))
    mins, secs = divmod(total_sec, 60)
    return f"{mins:02d}:{secs:02d}"


class StatusIconWidget(QWidget):
    """Custom painted status icon supporting animated spinner, green tick, red X, and grey skipped/pending."""

    def __init__(self, status: str = "PENDING", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._status = status.upper()
        self._angle = 0
        self.setFixedSize(18, 18)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def set_status(self, status: str) -> None:
        new_st = status.upper()
        if new_st != self._status:
            self._status = new_st
            self.update()

    def set_spinner_angle(self, angle: int) -> None:
        self._angle = angle % 360
        if self._status == "RUNNING":
            self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(2.5, 2.5, 13.0, 13.0)

        if self._status == "RUNNING":
            track_pen = QPen(QColor(110, 120, 135, 90), 2.0)
            painter.setPen(track_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(rect)

            arc_pen = QPen(QColor(210, 220, 235, 245), 2.2)
            arc_pen.setCapStyle(Qt.RoundCap)
            painter.setPen(arc_pen)
            start_angle = int((-self._angle) * 16)
            span_angle = -110 * 16
            painter.drawArc(rect, start_angle, span_angle)

        elif self._status == "SUCCESS":
            pen = QPen(QColor(46, 204, 113), 2.2)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            painter.drawLine(QPointF(4.0, 9.5), QPointF(7.8, 13.2))
            painter.drawLine(QPointF(7.8, 13.2), QPointF(14.2, 5.2))

        elif self._status == "FAILED":
            pen = QPen(QColor(231, 76, 60), 2.2)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.drawLine(QPointF(4.5, 4.5), QPointF(13.5, 13.5))
            painter.drawLine(QPointF(13.5, 4.5), QPointF(4.5, 13.5))

        else:  # PENDING or SKIPPED
            pen = QPen(QColor(135, 145, 160, 190), 1.8)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.drawLine(QPointF(5.0, 9.0), QPointF(13.0, 9.0))


class ExecutionStepRowWidget(QFrame):
    """Single row in the expanded execution tab representing one node in execution order."""

    clicked_failed = Signal(str)

    def __init__(self, index: int, node_name: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.index = index
        self.node_name = node_name
        self.status = "PENDING"
        self.elapsed_sec = 0.0
        self.error_msg = ""

        self.setFixedHeight(28)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(8)

        self.icon_widget = StatusIconWidget("PENDING", self)
        layout.addWidget(self.icon_widget)

        self.name_label = QLabel(node_name, self)
        self.name_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.name_label.setStyleSheet("color: #d1d5db; font-size: 11px; background: transparent;")
        layout.addWidget(self.name_label, stretch=1)

        self.time_label = QLabel("00:00", self)
        self.time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.time_label.setStyleSheet(
            "color: #9ca3af; font-family: monospace; font-size: 11px; font-weight: bold; background: transparent;"
        )
        layout.addWidget(self.time_label)

        self._apply_row_style()

    def set_step_state(
        self,
        status: Optional[str] = None,
        elapsed_sec: Optional[float] = None,
        error_msg: Optional[str] = None,
    ) -> None:
        if status is not None:
            self.status = status.upper()
            self.icon_widget.set_status(self.status)
        if elapsed_sec is not None:
            self.elapsed_sec = max(0.0, float(elapsed_sec))
            self.time_label.setText(format_mm_ss(self.elapsed_sec))
        if error_msg is not None:
            self.error_msg = error_msg
        self._apply_row_style()

    def advance_spinner(self, angle: int, elapsed_sec: Optional[float] = None) -> None:
        self.icon_widget.set_spinner_angle(angle)
        if elapsed_sec is not None and self.status == "RUNNING":
            self.elapsed_sec = max(0.0, float(elapsed_sec))
            self.time_label.setText(format_mm_ss(self.elapsed_sec))

    def _apply_row_style(self) -> None:
        if self.status == "RUNNING":
            self.setStyleSheet(
                "ExecutionStepRowWidget { background-color: rgba(59, 130, 246, 0.14); border-radius: 4px; }"
            )
            self.name_label.setStyleSheet("color: #ffffff; font-size: 11px; font-weight: bold; background: transparent;")
            self.time_label.setStyleSheet(
                "color: #e5e7eb; font-family: monospace; font-size: 11px; font-weight: bold; background: transparent;"
            )
            self.setCursor(Qt.ArrowCursor)
            self.setToolTip(f"Running: {self.node_name}")
        elif self.status == "SUCCESS":
            self.setStyleSheet("ExecutionStepRowWidget { background: transparent; border-radius: 4px; }")
            self.name_label.setStyleSheet("color: #d1fae5; font-size: 11px; background: transparent;")
            self.time_label.setStyleSheet(
                "color: #6ee7b7; font-family: monospace; font-size: 11px; font-weight: bold; background: transparent;"
            )
            self.setCursor(Qt.ArrowCursor)
            self.setToolTip(f"{self.node_name} — Completed in {format_mm_ss(self.elapsed_sec)}")
        elif self.status == "FAILED":
            self.setStyleSheet(
                "ExecutionStepRowWidget { background-color: rgba(231, 76, 60, 0.18); border-radius: 4px; }"
                "ExecutionStepRowWidget:hover { background-color: rgba(231, 76, 60, 0.30); }"
            )
            self.name_label.setStyleSheet("color: #fca5a5; font-size: 11px; font-weight: bold; background: transparent;")
            self.time_label.setStyleSheet(
                "color: #f87171; font-family: monospace; font-size: 11px; font-weight: bold; background: transparent;"
            )
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip("Click to view error details")
        else:  # PENDING or SKIPPED
            self.setStyleSheet("ExecutionStepRowWidget { background: transparent; border-radius: 4px; }")
            self.name_label.setStyleSheet("color: #9ca3af; font-size: 11px; background: transparent;")
            self.time_label.setStyleSheet(
                "color: #6b7280; font-family: monospace; font-size: 11px; background: transparent;"
            )
            self.setCursor(Qt.ArrowCursor)
            self.setToolTip(f"{self.node_name} ({self.status.title()})")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self.status == "FAILED":
            self.clicked_failed.emit(self.node_name)
            event.accept()
            return
        super().mousePressEvent(event)


class FlowExecutionTab(QWidget):
    """Collapsible lower-right execution status & progress tab and live canvas coordinator."""

    selection_changed = Signal(bool)
    cancel_requested = Signal()
    error_dialog_requested = Signal(str)
    run_state_changed = Signal()

    COLLAPSED_WIDTH = 340
    COLLAPSED_HEIGHT = 42
    EXPANDED_WIDTH = 360
    EXPANDED_MAX_HEIGHT = 310
    MARGIN_RIGHT = 16
    MARGIN_BOTTOM = 16

    def __init__(self, parent: Optional[QWidget] = None, editor_window: Optional[Any] = None):
        super().__init__(parent)
        self.editor_window = editor_window
        self._expanded: bool = False
        self._selected: bool = True
        self._run_status: str = "IDLE"  # IDLE, RUNNING, SUCCESS, FAILED
        self._dash_offset: float = 0.0
        self._spinner_angle: int = 0

        self._step_rows: List[ExecutionStepRowWidget] = []
        self._step_rows_by_name: Dict[str, ExecutionStepRowWidget] = {}
        self._completed_count: int = 0
        self._total_steps: int = 0
        self._current_node_name: str = ""
        self._failed_node_name: str = ""

        # Live execution orchestration state (tracks previous run only)
        self._active_worker: Optional[FlowWorker] = None
        self.last_run_node_states: Dict[str, Dict[str, Any]] = {}
        self._last_run_errors: List[str] = []
        self._current_running_node_name: Optional[str] = None
        self._current_step_start_mono: float = 0.0
        self._current_run_context_title: str = "Process Flow"
        self._canvas_press_pos: Optional[Any] = None
        self._canvas_press_button: Optional[Any] = None
        self._canvas_press_modifiers: Any = Qt.NoModifier
        self._current_lan_output_folder: str = ""

        self._exec_anim_timer = QTimer(self)
        self._exec_anim_timer.setInterval(50)
        self._exec_anim_timer.timeout.connect(self._on_exec_anim_tick)

        self.setAttribute(Qt.WA_StyledBackground, False)
        self._build_ui()
        self._sync_geometry()

        self.selection_changed.connect(self.sync_canvas_nodes)
        self.cancel_requested.connect(self.cancel_running_flow)
        self.error_dialog_requested.connect(self.open_error_dialog)

        if self.editor_window is not None:
            self._install_canvas_filters()

    def _install_canvas_filters(self) -> None:
        """Install event filters on the graph widget and viewer to handle resize and canvas click deselection."""
        try:
            gw = getattr(self.editor_window, "graph_widget", None)
            if gw:
                gw.installEventFilter(self)
            viewer = self.editor_window.graph.viewer()
            if viewer:
                viewer.installEventFilter(self)
                if hasattr(viewer, "viewport") and viewer.viewport():
                    viewer.viewport().installEventFilter(self)
        except Exception:
            pass

    def _build_ui(self) -> None:
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(7, 7, 7, 7)
        self.main_layout.setSpacing(0)

        # 1. Expanded body container (sits above bottom tab bar)
        self.expanded_container = QWidget(self)
        self.expanded_container.setVisible(False)
        exp_layout = QVBoxLayout(self.expanded_container)
        exp_layout.setContentsMargins(6, 6, 6, 4)
        exp_layout.setSpacing(6)

        self.scroll_area = QScrollArea(self.expanded_container)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:vertical { width: 6px; background: #16181d; border-radius: 3px; }"
            "QScrollBar::handle:vertical { background: #374151; border-radius: 3px; min-height: 20px; }"
        )

        self.rows_container = QWidget()
        self.rows_container.setStyleSheet("background: transparent;")
        self.rows_layout = QVBoxLayout(self.rows_container)
        self.rows_layout.setContentsMargins(0, 0, 2, 0)
        self.rows_layout.setSpacing(2)
        self.rows_layout.addStretch(1)
        self.scroll_area.setWidget(self.rows_container)
        exp_layout.addWidget(self.scroll_area, stretch=1)

        self.error_footer_btn = QPushButton("View Error Details", self.expanded_container)
        self.error_footer_btn.setCursor(Qt.PointingHandCursor)
        self.error_footer_btn.setVisible(False)
        self.error_footer_btn.setFixedHeight(26)
        self.error_footer_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: rgba(231, 76, 60, 0.22);"
            "  color: #fca5a5;"
            "  border: 1px solid #ef4444;"
            "  border-radius: 4px;"
            "  font-size: 11px;"
            "  font-weight: bold;"
            "  padding: 2px 10px;"
            "}"
            "QPushButton:hover { background-color: rgba(231, 76, 60, 0.38); color: #ffffff; }"
        )
        self.error_footer_btn.clicked.connect(self._on_open_error_clicked)
        exp_layout.addWidget(self.error_footer_btn)

        self.lan_progress_widget = QFrame(self.expanded_container)
        self.lan_progress_widget.setFixedHeight(28)
        self.lan_progress_widget.setStyleSheet(
            "QFrame { background: transparent; border: none; }"
        )
        self.lan_progress_widget.setVisible(False)
        lan_layout = QHBoxLayout(self.lan_progress_widget)
        lan_layout.setContentsMargins(8, 2, 8, 2)
        lan_layout.setSpacing(8)

        self.lan_icon = StatusIconWidget("PENDING", self.lan_progress_widget)
        lan_layout.addWidget(self.lan_icon)

        self.lan_label = QLabel("LAN Transfer", self.lan_progress_widget)
        self.lan_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.lan_label.setStyleSheet("color: #d1d5db; font-size: 11px; background: transparent;")
        lan_layout.addWidget(self.lan_label, stretch=1)

        self.lan_pct_label = QLabel("", self.lan_progress_widget)
        self.lan_pct_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lan_pct_label.setStyleSheet(
            "color: #9ca3af; font-family: monospace; font-size: 11px; font-weight: bold; background: transparent;"
        )
        lan_layout.addWidget(self.lan_pct_label)

        self.lan_open_btn = QPushButton("📁", self.lan_progress_widget)
        self.lan_open_btn.setCursor(Qt.PointingHandCursor)
        self.lan_open_btn.setToolTip("Open LAN destination folder in file manager")
        self.lan_open_btn.setFixedSize(22, 20)
        self.lan_open_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: transparent;"
            "  color: #8b949e;"
            "  border: 1px solid #30363d;"
            "  border-radius: 3px;"
            "  font-size: 11px;"
            "  padding: 0px;"
            "}"
            "QPushButton:hover {"
            "  background-color: #262b33;"
            "  color: #c9d1d9;"
            "  border-color: #484f58;"
            "}"
            "QPushButton:pressed {"
            "  background-color: #1b1f24;"
            "  color: #8b949e;"
            "}"
            "QPushButton:disabled {"
            "  color: #4b5563;"
            "  border-color: transparent;"
            "  background-color: transparent;"
            "}"
        )
        self.lan_open_btn.clicked.connect(self._on_open_lan_folder)
        lan_layout.addWidget(self.lan_open_btn)

        exp_layout.addWidget(self.lan_progress_widget)

        self.separator = QFrame(self.expanded_container)
        self.separator.setFrameShape(QFrame.HLine)
        self.separator.setFixedHeight(1)
        self.separator.setStyleSheet("background-color: #2f3542; border: none;")
        exp_layout.addWidget(self.separator)

        self.main_layout.addWidget(self.expanded_container, stretch=1)

        # 2. Persistent Tab Bar (bottom bar)
        self.header_bar = QWidget(self)
        self.header_bar.setFixedHeight(28)
        header_layout = QHBoxLayout(self.header_bar)
        header_layout.setContentsMargins(8, 0, 6, 0)
        header_layout.setSpacing(8)

        self.header_icon = StatusIconWidget("PENDING", self.header_bar)
        header_layout.addWidget(self.header_icon)

        self.summary_label = QLabel("Ready", self.header_bar)
        self.summary_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.summary_label.setStyleSheet(
            "color: #e5e7eb; font-size: 11px; font-weight: bold; background: transparent;"
        )
        header_layout.addWidget(self.summary_label, stretch=1)

        self.compact_error_btn = QPushButton("Error", self.header_bar)
        self.compact_error_btn.setCursor(Qt.PointingHandCursor)
        self.compact_error_btn.setToolTip("Open Error Details")
        self.compact_error_btn.setFixedHeight(20)
        self.compact_error_btn.setVisible(False)
        self.compact_error_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: rgba(231, 76, 60, 0.28);"
            "  color: #fca5a5;"
            "  border: 1px solid #ef4444;"
            "  border-radius: 3px;"
            "  font-size: 10px;"
            "  font-weight: bold;"
            "  padding: 0px 6px;"
            "}"
            "QPushButton:hover { background-color: rgba(231, 76, 60, 0.45); color: #ffffff; }"
        )
        self.compact_error_btn.clicked.connect(self._on_open_error_clicked)
        header_layout.addWidget(self.compact_error_btn)

        self.cancel_btn = QPushButton("⏹", self.header_bar)
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.setToolTip("Cancel Running Flow")
        self.cancel_btn.setFixedSize(22, 20)
        self.cancel_btn.setVisible(False)
        self.cancel_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #374151;"
            "  color: #f87171;"
            "  border: 1px solid #4b5563;"
            "  border-radius: 3px;"
            "  font-size: 10px;"
            "  font-weight: bold;"
            "  padding: 0px;"
            "}"
            "QPushButton:hover { background-color: #4b5563; color: #ef4444; }"
        )
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        header_layout.addWidget(self.cancel_btn)

        self.toggle_btn = QPushButton("^", self.header_bar)
        self.toggle_btn.setCursor(Qt.PointingHandCursor)
        self.toggle_btn.setToolTip("Expand Execution Progress")
        self.toggle_btn.setFixedSize(22, 20)
        self.toggle_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #262b33;"
            "  color: #d1d5db;"
            "  border: 1px solid #3d4450;"
            "  border-radius: 3px;"
            "  font-size: 11px;"
            "  font-weight: bold;"
            "  padding: 0px;"
            "}"
            "QPushButton:hover { background-color: #323944; color: #ffffff; }"
        )
        self.toggle_btn.clicked.connect(self.toggle_expanded)
        header_layout.addWidget(self.toggle_btn)

        self.main_layout.addWidget(self.header_bar)

    # ------------------------------------------------------------------
    # Execution Orchestration & Canvas Sync
    # ------------------------------------------------------------------

    def is_running(self) -> bool:
        return self._active_worker is not None and self._active_worker.isRunning()

    def shutdown(self) -> None:
        """Stop timers and cancel any running worker on window close."""
        if self._exec_anim_timer.isActive():
            self._exec_anim_timer.stop()
        if self.is_running():
            try:
                self._active_worker.cancel()
            except Exception:
                pass

    def find_exec_node(self, node_name: str, node_id: Optional[str] = None) -> Optional[Any]:
        """Locate the QueryNode or ImportCsvNode on the editor canvas matching node_id or node_name."""
        if not self.editor_window or not hasattr(self.editor_window, "graph"):
            return None
        for n in self.editor_window.graph.all_nodes():
            if not (
                isinstance(n, (QueryNode, ImportCsvNode))
                or getattr(n, "type_", "") in ("reporting.nodes.QueryNode", "reporting.nodes.ImportCsvNode")
            ):
                continue
            if node_id and n.id == node_id:
                return n
            qprop = n.get_property("query_name") if n.has_property("query_name") else ""
            if n.name() == node_name or qprop == node_name:
                return n
        return None

    def clear_all_node_execution_states(self) -> None:
        """Reset execution overlays on all canvas nodes."""
        if not self.editor_window or not hasattr(self.editor_window, "graph"):
            return
        for n in self.editor_window.graph.all_nodes():
            view = getattr(n, "view", None)
            if view and hasattr(view, "set_execution_state"):
                view.set_execution_state(status="IDLE", elapsed_sec=0.0, visible=False)

    def sync_canvas_nodes(self, is_selected: Optional[bool] = None) -> None:
        """Show or hide canvas node halos and elapsed times for the previous run only."""
        if not self.editor_window or not hasattr(self.editor_window, "graph"):
            return
        sel = self._selected if is_selected is None else bool(is_selected)
        for n in self.editor_window.graph.all_nodes():
            view = getattr(n, "view", None)
            if not (view and hasattr(view, "set_execution_state")):
                continue
            qprop = n.get_property("query_name") if n.has_property("query_name") else ""
            state_info = self.last_run_node_states.get(n.name()) or self.last_run_node_states.get(qprop)
            if state_info and sel:
                view.set_execution_state(
                    status=state_info.get("status", "IDLE"),
                    elapsed_sec=state_info.get("elapsed_sec", 0.0),
                    visible=True,
                )
            else:
                view.set_execution_state(visible=False)

    def execute_steps(
        self,
        steps: List[Dict[str, Any]],
        report_folder: Path,
        report_name: str,
        flow_name: str,
        param_values: Dict[str, str],
        filename_date: str = "",
        csv_map: Optional[Dict[str, str]] = None,
        outputs_dir: Optional[Path] = None,
        lan_output_folder: Optional[str] = None,
        repo: Optional[Any] = None,
        context_title: str = "Process Flow",
    ) -> None:
        """Start non-blocking execution of ordered steps with live canvas and tab tracking."""
        if self.is_running():
            self._show_status("A process flow is already running.", 3000)
            return

        self.clear_all_node_execution_states()
        self.last_run_node_states.clear()
        self._last_run_errors.clear()
        self._failed_node_name = ""
        self._current_running_node_name = None
        self._current_run_context_title = context_title

        for s in steps:
            sname = s["name"]
            self.last_run_node_states[sname] = {
                "status": "PENDING",
                "elapsed_sec": 0.0,
                "error_msg": "",
            }
            node = self.find_exec_node(sname, s.get("id"))
            if node and getattr(node, "view", None) and hasattr(node.view, "set_execution_state"):
                node.view.set_execution_state(status="PENDING", elapsed_sec=0.0, visible=True)

        self.start_new_run(steps)
        self._current_lan_output_folder = str(lan_output_folder).strip() if lan_output_folder else ""

        if lan_output_folder and str(lan_output_folder).strip():
            lan_dest_name = Path(str(lan_output_folder).strip()).name or str(lan_output_folder).strip()
            self.lan_progress_widget.setVisible(True)
            self.lan_progress_widget.setStyleSheet("QFrame { background: transparent; border: none; }")
            self.lan_icon.set_status("PENDING")
            self.lan_label.setText(f"LAN: Queued ({lan_dest_name})")
            self.lan_label.setToolTip(f"Destination: {lan_output_folder}")
            self.lan_pct_label.setText("")
            if hasattr(self, "lan_open_btn"):
                self.lan_open_btn.setEnabled(True)
            self._sync_geometry()

        runner = FlowRunner(parent_window=self.editor_window or self, repo=repo)
        self._active_worker = runner.create_worker(
            steps=steps,
            report_folder=report_folder,
            report_name=report_name,
            flow_name=flow_name,
            param_values=param_values,
            filename_date=filename_date,
            csv_map=csv_map,
            outputs_dir=outputs_dir,
            lan_output_folder=lan_output_folder,
        )
        self._active_worker.step_started.connect(self._on_worker_step_started)
        self._active_worker.step_completed.connect(self._on_worker_step_completed)
        self._active_worker.flow_finished.connect(self._on_worker_flow_finished)
        self._active_worker.lan_transfer_started.connect(self._on_lan_transfer_started)
        self._active_worker.lan_transfer_progress.connect(self._on_lan_transfer_progress)
        self._active_worker.lan_transfer_completed.connect(self._on_lan_transfer_completed)
        self._active_worker.lan_transfer_finished.connect(self._on_lan_transfer_finished)

        self.run_state_changed.emit()
        self._exec_anim_timer.start()
        self._active_worker.start()

    def cancel_running_flow(self) -> None:
        """Cancel the currently running process flow (including remote BigQuery job cancellation)."""
        if self.is_running():
            self._show_status("Cancelling running process flow...", 4000)
            self._active_worker.cancel()

    def open_error_dialog(self, failed_node: str = "") -> None:
        """Open the Error Details dialog for the failed run."""
        target_node = failed_node or self._failed_node_name
        errors = list(self._last_run_errors)
        if not errors and target_node and target_node in self.last_run_node_states:
            err_msg = self.last_run_node_states[target_node].get("error_msg", "")
            if err_msg:
                errors = [err_msg]
        flow_name = "Process Flow"
        if self.editor_window and hasattr(self.editor_window, "flow_controller"):
            flow_name = self.editor_window.flow_controller.flow_name
        dlg = FlowErrorDialog(
            flow_name=flow_name,
            failed_node=target_node,
            errors=errors,
            parent=self.editor_window or self,
        )
        dlg.exec()

    def _on_exec_anim_tick(self) -> None:
        if not self.is_running():
            self._exec_anim_timer.stop()
            return

        cur_name = self._current_running_node_name
        elapsed = max(0.0, time.monotonic() - self._current_step_start_mono) if cur_name else 0.0

        if cur_name:
            if cur_name in self.last_run_node_states:
                self.last_run_node_states[cur_name]["elapsed_sec"] = elapsed
            node = self.find_exec_node(cur_name)
            if node and getattr(node, "view", None) and hasattr(node.view, "advance_execution_animation"):
                node.view.advance_execution_animation(delta_offset=0.9, elapsed_sec=elapsed)

        self.advance_animation(
            delta_dash=0.9,
            delta_angle=16,
            current_step_elapsed=elapsed if cur_name else None,
        )

    def _on_worker_step_started(self, step_idx: int, total_steps: int, node_name: str) -> None:
        self._current_running_node_name = node_name
        self._current_step_start_mono = time.monotonic()
        self.last_run_node_states[node_name] = {
            "status": "RUNNING",
            "elapsed_sec": 0.0,
            "error_msg": "",
        }
        node = self.find_exec_node(node_name)
        if node and getattr(node, "view", None) and hasattr(node.view, "set_execution_state"):
            node.view.set_execution_state(status="RUNNING", elapsed_sec=0.0, visible=True)

        self.mark_step_started(step_idx, total_steps, node_name)
        self._show_status(f"Running [{step_idx}/{total_steps}]: {node_name}...")

    def _on_worker_step_completed(
        self,
        step_idx: int,
        total_steps: int,
        node_name: str,
        status: str,
        duration_sec: float,
        error_msg: str,
    ) -> None:
        st = status.upper()
        self.last_run_node_states[node_name] = {
            "status": st,
            "elapsed_sec": duration_sec,
            "error_msg": error_msg,
        }
        if st == "FAILED":
            self._failed_node_name = node_name

        if self._current_running_node_name == node_name:
            self._current_running_node_name = None

        node = self.find_exec_node(node_name)
        if node and getattr(node, "view", None) and hasattr(node.view, "set_execution_state"):
            node.view.set_execution_state(status=st, elapsed_sec=duration_sec, visible=self._selected)

        self.mark_step_completed(step_idx, total_steps, node_name, st, duration_sec, error_msg)

    def _on_worker_flow_finished(
        self,
        success: bool,
        summary: str,
        results_log: list,
        errors: list,
    ) -> None:
        self._exec_anim_timer.stop()
        self._current_running_node_name = None
        self._last_run_errors = list(errors or [])

        self.mark_flow_finished(success)
        self.sync_canvas_nodes(True)
        self.run_state_changed.emit()

        if success:
            self._show_status(f"{self._current_run_context_title} completed successfully.", 5000)
        elif self._active_worker and self._active_worker.is_cancelled():
            self._show_status(f"{self._current_run_context_title} was cancelled.", 5000)
        else:
            self._show_status(f"{self._current_run_context_title} failed.", 5000)

    def _show_status(self, message: str, timeout: int = 0) -> None:
        if self.editor_window and hasattr(self.editor_window, "status_bar"):
            self.editor_window.status_bar.showMessage(message, timeout)

    def _on_lan_transfer_started(self, file_idx: int, total_files: int, filename: str) -> None:
        self.lan_progress_widget.setVisible(True)
        self.lan_progress_widget.setStyleSheet("QFrame { background: transparent; border: none; }")
        self.lan_icon.set_status("RUNNING")
        self.lan_label.setText(f"LAN: Copying {filename} ({file_idx}/{total_files})")
        self.lan_pct_label.setText("0%")
        if hasattr(self, "lan_open_btn"):
            self.lan_open_btn.setEnabled(True)
        self._sync_geometry()

    def _on_lan_transfer_progress(self, file_idx: int, total_files: int, filename: str, pct: float) -> None:
        self.lan_progress_widget.setVisible(True)
        self.lan_icon.set_status("RUNNING")
        self.lan_label.setText(f"LAN: Copying {filename} ({file_idx}/{total_files})")
        self.lan_pct_label.setText(f"{int(pct)}%")

    def _on_lan_transfer_completed(
        self, file_idx: int, total_files: int, filename: str, success: bool, error_msg: str
    ) -> None:
        if not success:
            self.lan_label.setText(f"LAN: Failed {filename}")
            self.lan_label.setToolTip(error_msg)
            self.lan_pct_label.setText("")
        else:
            self.lan_label.setText(f"LAN: Transferred {filename} ({file_idx}/{total_files})")
            self.lan_pct_label.setText("")

    def _on_lan_transfer_finished(
        self, all_success: bool, total_copied: int, total_files: int, summary: str
    ) -> None:
        if total_files > 0:
            self.lan_progress_widget.setVisible(True)
            self.lan_progress_widget.setStyleSheet("QFrame { background: transparent; border: none; }")
            self.lan_icon.set_status("SUCCESS" if all_success else "FAILED")
            self.lan_label.setText(f"LAN: {total_copied}/{total_files} file(s) copied")
            self.lan_pct_label.setText("")
            self.lan_label.setToolTip(summary)
            if hasattr(self, "lan_open_btn"):
                self.lan_open_btn.setEnabled(True)
            self._sync_geometry()

    def get_lan_folder_path(self) -> Optional[Path]:
        """Return the resolved LAN output directory path if configured."""
        folder_str = self._current_lan_output_folder
        if not folder_str and self.editor_window and hasattr(self.editor_window, "flow_controller"):
            try:
                folder_str = self.editor_window.flow_controller.get_lan_output_folder()
            except Exception:
                pass
        if folder_str and str(folder_str).strip():
            return Path(str(folder_str).strip())
        return None

    def _on_open_lan_folder(self) -> None:
        """Open the configured LAN destination folder in the system file manager."""
        self.set_selected(True)
        folder_path = self.get_lan_folder_path()
        if not folder_path:
            self._show_status("No LAN destination folder configured.", 3000)
            return

        try:
            folder_path.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        folder_url = QUrl.fromLocalFile(str(folder_path.resolve()))
        opened = QDesktopServices.openUrl(folder_url)
        if not opened:
            import os
            import platform
            import subprocess

            sys_name = platform.system()
            path_str = str(folder_path.resolve())
            try:
                if sys_name == "Windows":
                    os.startfile(path_str)
                elif sys_name == "Darwin":
                    subprocess.Popen(["open", path_str])
                else:
                    subprocess.Popen(["xdg-open", path_str])
            except Exception as e:
                self._show_status(f"Could not open LAN folder: {e}", 3000)

    # ------------------------------------------------------------------
    # Tab UI State API
    # ------------------------------------------------------------------

    def is_expanded(self) -> bool:
        return self._expanded

    def is_selected(self) -> bool:
        return self._selected

    def run_status(self) -> str:
        return self._run_status

    def set_selected(self, selected: bool, emit_signal: bool = True) -> None:
        """Select or deselect the tab (controls halo color and canvas node halo visibility)."""
        selected = bool(selected)
        if self._selected != selected:
            self._selected = selected
            self.update()
            if emit_signal:
                self.selection_changed.emit(self._selected)
        elif emit_signal and selected:
            self.selection_changed.emit(True)

    def set_expanded(self, expanded: bool) -> None:
        """Expand upwards or collapse downwards."""
        expanded = bool(expanded)
        if expanded:
            self.set_selected(True)
        if self._expanded == expanded:
            return
        self._expanded = expanded
        self.expanded_container.setVisible(self._expanded)
        self.toggle_btn.setText("▼" if self._expanded else "^")
        self.toggle_btn.setToolTip("Collapse Execution Progress" if self._expanded else "Expand Execution Progress")
        self._update_error_controls_visibility()
        self._sync_geometry()
        self.update()

    def toggle_expanded(self) -> None:
        self.set_expanded(not self._expanded)

    def start_new_run(self, steps: List[Dict[str, Any]]) -> None:
        """Initialize tab state for a new process flow run (replacing any prior run state)."""
        self._clear_rows()
        self._total_steps = len(steps)
        self._completed_count = 0
        self._current_node_name = steps[0].get("name", "Step 1") if steps else ""
        self._failed_node_name = ""
        self._run_status = "RUNNING"
        self._selected = True
        self._current_lan_output_folder = ""

        if hasattr(self, "lan_progress_widget"):
            self.lan_progress_widget.setVisible(False)
            self.lan_progress_widget.setStyleSheet("QFrame { background: transparent; border: none; }")
            self.lan_icon.set_status("PENDING")
            self.lan_label.setText("LAN Transfer")
            self.lan_label.setToolTip("")
            self.lan_pct_label.setText("")

        for idx, step in enumerate(steps, start=1):
            sname = step.get("name", f"Step {idx}")
            row = ExecutionStepRowWidget(idx, sname, self.rows_container)
            row.clicked_failed.connect(self._on_failed_row_clicked)
            self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
            self._step_rows.append(row)
            self._step_rows_by_name[sname] = row

        self.header_icon.set_status("RUNNING")
        self.cancel_btn.setVisible(True)
        self.cancel_btn.setEnabled(True)
        self._update_error_controls_visibility()
        self._update_summary_text()
        self._sync_geometry()
        self.show()
        self.raise_()
        self.update()
        self.selection_changed.emit(True)

    def mark_step_started(self, step_idx: int, total_steps: int, node_name: str) -> None:
        """Mark a specific step as RUNNING."""
        self._total_steps = total_steps
        self._current_node_name = node_name
        row = self._get_row(step_idx, node_name)
        if row:
            row.set_step_state(status="RUNNING", elapsed_sec=0.0)
            self.scroll_area.ensureWidgetVisible(row)
        self._update_summary_text()
        self.update()

    def mark_step_completed(
        self,
        step_idx: int,
        total_steps: int,
        node_name: str,
        status: str,
        duration_sec: float,
        error_msg: str = "",
    ) -> None:
        """Mark a step as SUCCESS, FAILED, or SKIPPED."""
        self._total_steps = total_steps
        st = status.upper()
        row = self._get_row(step_idx, node_name)
        if row:
            row.set_step_state(status=st, elapsed_sec=duration_sec, error_msg=error_msg)

        if st == "SUCCESS":
            self._completed_count = sum(1 for r in self._step_rows if r.status == "SUCCESS")
        elif st == "FAILED":
            self._failed_node_name = node_name

        self._update_summary_text()
        self.update()

    def mark_flow_finished(self, success: bool) -> None:
        """Transition tab state when the entire flow finishes."""
        self._run_status = "SUCCESS" if success else "FAILED"
        self.header_icon.set_status("SUCCESS" if success else "FAILED")
        self.cancel_btn.setVisible(False)
        self._selected = True
        self._update_error_controls_visibility()
        self._update_summary_text()
        self._sync_geometry()
        self.update()
        self.selection_changed.emit(True)

    def advance_animation(
        self,
        delta_dash: float = 1.2,
        delta_angle: int = 18,
        current_step_elapsed: Optional[float] = None,
    ) -> None:
        """Advance clockwise rotating dashed halo and spinner animations."""
        self._dash_offset = (self._dash_offset + delta_dash) % 1000.0
        self._spinner_angle = (self._spinner_angle + delta_angle) % 360
        if self._run_status == "RUNNING":
            self.header_icon.set_spinner_angle(self._spinner_angle)
            if hasattr(self, "lan_icon") and self.lan_icon._status == "RUNNING":
                self.lan_icon.set_spinner_angle(self._spinner_angle)
            for row in self._step_rows:
                if row.status == "RUNNING":
                    row.advance_spinner(self._spinner_angle, elapsed_sec=current_step_elapsed)
        self.update()

    def reposition_in_parent(self) -> None:
        """Anchor the tab in the lower-right corner of its parent widget."""
        self._sync_geometry()

    # ------------------------------------------------------------------
    # Internal Helpers & Event Filter
    # ------------------------------------------------------------------

    def _clear_rows(self) -> None:
        for row in self._step_rows:
            self.rows_layout.removeWidget(row)
            row.deleteLater()
        self._step_rows.clear()
        self._step_rows_by_name.clear()

    def _get_row(self, step_idx: int, node_name: str) -> Optional[ExecutionStepRowWidget]:
        if 1 <= step_idx <= len(self._step_rows):
            return self._step_rows[step_idx - 1]
        return self._step_rows_by_name.get(node_name)

    def _update_summary_text(self) -> None:
        done = self._completed_count
        total = self._total_steps
        node = self._current_node_name or "Flow"

        if self._run_status == "RUNNING":
            text = f"{node} ({done}/{total} complete)"
        elif self._run_status == "SUCCESS":
            text = f"{node} ({done}/{total} complete)"
        elif self._run_status == "FAILED":
            failed_name = self._failed_node_name or node
            text = f"{failed_name} ({done}/{total} complete)"
        else:
            text = "Ready"

        self.summary_label.setText(text)
        self.summary_label.setToolTip(text)

    def _update_error_controls_visibility(self) -> None:
        has_error = self._run_status == "FAILED"
        self.error_footer_btn.setVisible(has_error and self._expanded)
        self.compact_error_btn.setVisible(has_error and not self._expanded)

    def _sync_geometry(self) -> None:
        if self._expanded:
            w = self.EXPANDED_WIDTH
            rows_h = len(self._step_rows) * 30 + 16
            lan_h = 32 if (hasattr(self, "lan_progress_widget") and self.lan_progress_widget.isVisible()) else 0
            err_h = 34 if self._run_status == "FAILED" else 0
            desired_h = min(self.EXPANDED_MAX_HEIGHT, max(120, rows_h + lan_h + err_h + 52))
            h = desired_h
        else:
            w = self.COLLAPSED_WIDTH
            h = self.COLLAPSED_HEIGHT

        self.setFixedSize(w, h)
        parent = self.parentWidget()
        if parent:
            px = max(8, parent.width() - w - self.MARGIN_RIGHT)
            py = max(8, parent.height() - h - self.MARGIN_BOTTOM)
            self.move(px, py)

    def _on_cancel_clicked(self) -> None:
        self.cancel_btn.setEnabled(False)
        self.cancel_requested.emit()

    def _on_open_error_clicked(self) -> None:
        self.set_selected(True)
        self.error_dialog_requested.emit(self._failed_node_name)

    def _on_failed_row_clicked(self, node_name: str) -> None:
        self.set_selected(True)
        self.error_dialog_requested.emit(node_name)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Resize and watched is self.parentWidget():
            self.reposition_in_parent()
            self.raise_()
        elif event.type() == QEvent.MouseButtonPress and watched is not self and watched is not self.parentWidget():
            if isinstance(event, QMouseEvent):
                self._canvas_press_pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                self._canvas_press_button = event.button()
                self._canvas_press_modifiers = event.modifiers()
        elif event.type() == QEvent.MouseButtonRelease and watched is not self and watched is not self.parentWidget():
            if isinstance(event, QMouseEvent):
                press_pos = self._canvas_press_pos
                press_btn = self._canvas_press_button
                press_mods = self._canvas_press_modifiers

                self._canvas_press_pos = None
                self._canvas_press_button = None
                self._canvas_press_modifiers = Qt.NoModifier

                # Panning is done with MiddleButton, Alt+drag, or moving/dragging the mouse.
                # Never deselect on panning or dragging.
                curr_pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                is_middle_pan = (press_btn == Qt.MiddleButton or event.button() == Qt.MiddleButton)
                is_alt_pan = bool((press_mods & Qt.AltModifier) or (event.modifiers() & Qt.AltModifier))
                is_drag_or_pan = False
                if press_pos is not None:
                    delta = (curr_pos - press_pos).manhattanLength()
                    if delta >= 5:
                        is_drag_or_pan = True

                if (
                    press_btn == Qt.LeftButton
                    and event.button() == Qt.LeftButton
                    and not is_middle_pan
                    and not is_alt_pan
                    and not is_drag_or_pan
                ):
                    if not self.is_running() and self._selected:
                        self.set_selected(False)

        return super().eventFilter(watched, event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self.set_selected(True)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self.toggle_expanded()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = QRectF(3.5, 3.5, self.width() - 7.0, self.height() - 7.0)
        radius = 7.0

        if self._selected or self._run_status == "RUNNING":
            bg_color = QColor(26, 29, 36, 248)
        else:
            bg_color = QColor(22, 24, 30, 215)

        painter.setBrush(QBrush(bg_color))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(rect, radius, radius)

        if self._run_status == "RUNNING":
            glow_pen = QPen(QColor(175, 190, 210, 50), 5.5)
            glow_pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(glow_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect, radius, radius)

            dash_pen = QPen(QColor(195, 205, 220, 245), 2.2)
            dash_pen.setCapStyle(Qt.FlatCap)
            dash_pen.setJoinStyle(Qt.RoundJoin)
            dash_pen.setDashPattern([5.0, 4.0])
            dash_pen.setDashOffset(-self._dash_offset)
            painter.setPen(dash_pen)
            painter.drawRoundedRect(rect, radius, radius)

        elif self._selected and self._run_status == "SUCCESS":
            glow_pen = QPen(QColor(46, 204, 113, 75), 6.0)
            glow_pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(glow_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect, radius, radius)

            solid_pen = QPen(QColor(46, 204, 113, 255), 2.2)
            solid_pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(solid_pen)
            painter.drawRoundedRect(rect, radius, radius)

        elif self._selected and self._run_status == "FAILED":
            glow_pen = QPen(QColor(231, 76, 60, 85), 6.0)
            glow_pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(glow_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect, radius, radius)

            solid_pen = QPen(QColor(231, 76, 60, 255), 2.2)
            solid_pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(solid_pen)
            painter.drawRoundedRect(rect, radius, radius)

        else:
            neutral_pen = QPen(QColor(62, 68, 81, 180), 1.2)
            neutral_pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(neutral_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect, radius, radius)
