"""Main Studio Window for AudioFlow — ComfyUI-faithful layout, workflow tabs & terminal."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from shiboken6 import isValid
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QPushButton,
    QTabBar,
    QToolButton,
    QWidget,
)

from audioflow.engine_bridge import AudioFlowEngine, DeviceInfo, ProcessInfo
from audioflow.models.graph_model import NodeType, ProjectModel
from audioflow.ui.canvas import NodeCanvas
from audioflow.ui.panels import BottomTelemetryPanel, LeftPalettePanel
from audioflow.ui.theme import COMFY_STROKE, STUDIO_STYLESHEET

DEFAULT_CACHE_PATH = Path.home() / ".audioflow" / "workspace_cache.json"


@dataclass
class WorkflowTabState:
    """In-memory state backing one open ComfyUI workflow tab."""

    tab_id: int
    title: str
    project: ProjectModel
    file_path: Optional[Path] = None
    dirty: bool = False
    view_state: Optional[Dict[str, float]] = None

    def base_title(self) -> str:
        return self.file_path.stem if self.file_path is not None else self.title

    def display_title(self) -> str:
        name = self.base_title()
        return f"{name} *" if self.dirty else name


class _TabRenameLineEdit(QLineEdit):
    """Inline workflow tab title editor supporting Enter commit and Escape cancel."""

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        self.cancelled = False

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled = True
            self.clearFocus()
            self.deleteLater()
            event.accept()
            return
        super().keyPressEvent(event)


class AudioFlowMainWindow(QMainWindow):
    """Studio main window with ComfyUI workflow tabs, inline node canvas, and closable terminal."""

    def __init__(
        self,
        engine: Optional[AudioFlowEngine] = None,
        auto_start: bool = True,
        cache_path: Optional[Path] = None,
        restore_cache: bool = True,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Untitled Workflow — AudioFlow Studio")
        self.setMinimumSize(780, 500)
        self.resize(1380, 820)
        screen = QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            if self.width() > avail.width() - 40 or self.height() > avail.height() - 40:
                self.resize(min(self.width(), avail.width() - 40), min(self.height(), avail.height() - 40))

        self.setStyleSheet(STUDIO_STYLESHEET)

        self.engine = engine if engine is not None else AudioFlowEngine(sample_rate=48000, block_frames=256)
        self._owns_engine = engine is None
        self.cache_path = cache_path if cache_path is not None else DEFAULT_CACHE_PATH
        self._persist_cache = bool(restore_cache or cache_path is not None)
        self._cache_dirty = False
        self._restoring_cache = False
        self._switching_tabs = False
        self._closing = False
        self._ui_ready = False
        self._shown_once = False
        self._cached_palette_width = 260
        self._cached_terminal_height = 210
        self._palette_visible = True
        self._terminal_visible = True
        self._pending_ui_restore: Optional[Dict[str, Any]] = None
        self._baseline_ui_state: Optional[Dict[str, Any]] = None
        self._next_tab_seq = 1

        self.project = ProjectModel()
        self.project_path: Optional[Path] = None
        self._tabs: List[WorkflowTabState] = [
            WorkflowTabState(tab_id=self._next_tab_seq, title="Untitled Workflow", project=self.project)
        ]
        self._active_tab_id: int = self._tabs[0].tab_id
        self._tab_rename_editor: Optional[_TabRenameLineEdit] = None

        self._devices: List[DeviceInfo] = []
        self._processes: List[ProcessInfo] = []
        self._last_device_seq = -1

        self._cache_timer = QTimer(self)
        self._cache_timer.setSingleShot(True)
        self._cache_timer.setInterval(450)
        self._cache_timer.timeout.connect(self.save_workspace_cache)

        # Central infinite node patchbay canvas
        self.canvas = NodeCanvas(self.project, self)
        self.setCentralWidget(self.canvas)

        # Left Palette + Closable Bottom Terminal (Right sidebar removed: arguments live inline on nodes!)
        self.left_panel = LeftPalettePanel(self)
        self.bottom_panel = BottomTelemetryPanel(self)

        self._build_docks()
        self._build_topbar_and_menus()
        self._build_status_strip()
        self._connect_signals()

        self.refresh_devices_and_processes()
        if restore_cache:
            self.load_workspace_cache()
        self._sync_window_title()

        if auto_start:
            self.start_engine()

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(120)
        self._poll_timer.timeout.connect(self._poll_engine_telemetry)
        self._poll_timer.start()

    # ----- Layout Construction -----

    def _build_docks(self) -> None:
        self.left_dock = QDockWidget("Palette && Sources", self)
        empty_left_title = QWidget(self.left_dock)
        empty_left_title.setFixedHeight(0)
        self.left_dock.setTitleBarWidget(empty_left_title)
        self.left_dock.setWidget(self.left_panel)
        self.left_dock.setMinimumWidth(200)
        self.left_dock.setMaximumWidth(560)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.left_dock)

        self.bottom_dock = QDockWidget("Meters, Status & Logs", self)
        # ComfyUI terminal style: tab header + right-corner 'x' button without redundant OS title strip
        empty_title = QWidget(self.bottom_dock)
        empty_title.setFixedHeight(0)
        self.bottom_dock.setTitleBarWidget(empty_title)
        self.bottom_dock.setWidget(self.bottom_panel)
        self.bottom_dock.setMinimumHeight(120)
        self.bottom_dock.setMaximumHeight(560)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.bottom_dock)

        self.resizeDocks([self.left_dock], [self._cached_palette_width], Qt.Orientation.Horizontal)
        self.resizeDocks([self.bottom_dock], [self._cached_terminal_height], Qt.Orientation.Vertical)
        self.left_dock.installEventFilter(self)
        self.bottom_dock.installEventFilter(self)
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.installEventFilter(self)

    def _build_topbar_and_menus(self) -> None:
        self.top_bar = QWidget(self)
        self.top_bar.setObjectName("ComfyTopBar")
        self.top_bar.setFixedHeight(38)
        top_lay = QHBoxLayout(self.top_bar)
        top_lay.setContentsMargins(6, 0, 8, 0)
        top_lay.setSpacing(6)

        # Actions (bound with global shortcuts so Ctrl+A / Ctrl+S / Ctrl+Z / Ctrl+Y / Ctrl+` always work)
        self.act_new = QAction("New Workflow", self)
        self.act_new.setShortcut(QKeySequence.StandardKey.New)
        self.act_new.triggered.connect(self.new_project_tab)

        self.act_open = QAction("Open Workflow...", self)
        self.act_open.setShortcut(QKeySequence.StandardKey.Open)
        self.act_open.triggered.connect(self.open_project_dialog)

        self.act_save = QAction("Save Workflow", self)
        self.act_save.setShortcut(QKeySequence.StandardKey.Save)
        self.act_save.triggered.connect(self.save_project)

        self.act_save_as = QAction("Save Workflow As...", self)
        self.act_save_as.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.act_save_as.triggered.connect(self.save_project_as_dialog)

        self.act_close_tab = QAction("Close Workflow Tab", self)
        self.act_close_tab.setShortcut(QKeySequence("Ctrl+W"))
        self.act_close_tab.triggered.connect(
            lambda: self.close_workflow_tab(self.workflow_tabs.currentIndex())
        )

        self.act_undo = self.canvas.undo_stack.createUndoAction(self, "Undo")
        self.act_undo.setShortcut(QKeySequence.StandardKey.Undo)

        self.act_redo = self.canvas.undo_stack.createRedoAction(self, "Redo")
        self.act_redo.setShortcuts([QKeySequence.StandardKey.Redo, QKeySequence("Ctrl+Shift+Z")])

        self.act_select_all = QAction("Select All Nodes", self)
        self.act_select_all.setShortcut(QKeySequence.StandardKey.SelectAll)
        self.act_select_all.triggered.connect(self.select_all_action)

        self.act_group = QAction("Group Selected Nodes", self)
        self.act_group.setShortcut(QKeySequence("Ctrl+G"))
        self.act_group.triggered.connect(lambda: self.canvas.group_selected_action())

        self.act_delete = QAction("Delete Selected", self)
        self.act_delete.triggered.connect(self.canvas.delete_selected_action)

        self.act_toggle_terminal = QAction("Meters, Status & Logs Terminal", self)
        self.act_toggle_terminal.setCheckable(True)
        self.act_toggle_terminal.setChecked(True)
        self.act_toggle_terminal.setShortcut(QKeySequence("Ctrl+`"))
        self.act_toggle_terminal.triggered.connect(self.set_terminal_visible)

        self.act_toggle_palette = QAction("Node Palette Sidebar", self)
        self.act_toggle_palette.setCheckable(True)
        self.act_toggle_palette.setChecked(True)
        self.act_toggle_palette.triggered.connect(self.set_palette_visible)

        self.act_zoom_in = QAction("Zoom In", self)
        self.act_zoom_in.setShortcut(QKeySequence("Ctrl+="))
        self.act_zoom_in.triggered.connect(self.canvas.zoom_in)

        self.act_zoom_out = QAction("Zoom Out", self)
        self.act_zoom_out.setShortcut(QKeySequence("Ctrl+-"))
        self.act_zoom_out.triggered.connect(self.canvas.zoom_out)

        self.act_zoom_reset = QAction("Reset View", self)
        self.act_zoom_reset.setShortcut(QKeySequence("Ctrl+0"))
        self.act_zoom_reset.triggered.connect(self.canvas.fit_view)

        self.act_engine_toggle = QAction("Start Audio Engine", self)
        self.act_engine_toggle.setCheckable(True)
        self.act_engine_toggle.triggered.connect(self._toggle_engine)

        self.act_refresh = QAction("Refresh Devices & Processes", self)
        self.act_refresh.setShortcut(QKeySequence("F5"))
        self.act_refresh.triggered.connect(self.refresh_devices_and_processes)

        # Compact inline menu bar on left of ComfyUI top bar
        self.menu_bar = QMenuBar(self.top_bar)
        self.menu_bar.setNativeMenuBar(False)
        file_m = self.menu_bar.addMenu("File")
        file_m.addAction(self.act_new)
        file_m.addAction(self.act_open)
        file_m.addAction(self.act_save)
        file_m.addAction(self.act_save_as)
        file_m.addAction(self.act_close_tab)
        file_m.addSeparator()
        file_m.addAction("Exit", self.close)

        edit_m = self.menu_bar.addMenu("Edit")
        edit_m.addAction(self.act_undo)
        edit_m.addAction(self.act_redo)
        edit_m.addSeparator()
        edit_m.addAction(self.act_select_all)
        edit_m.addAction(self.act_group)
        edit_m.addAction(self.act_delete)

        view_m = self.menu_bar.addMenu("View")
        view_m.addAction(self.act_toggle_terminal)
        view_m.addAction(self.act_toggle_palette)
        view_m.addSeparator()
        view_m.addAction(self.act_zoom_in)
        view_m.addAction(self.act_zoom_out)
        view_m.addAction(self.act_zoom_reset)

        eng_m = self.menu_bar.addMenu("Engine")
        eng_m.addAction(self.act_engine_toggle)
        eng_m.addAction(self.act_refresh)

        self.menu_bar.adjustSize()
        self.menu_bar.setFixedHeight(self.menu_bar.sizeHint().height())
        top_lay.addWidget(self.menu_bar, 0, Qt.AlignmentFlag.AlignVCenter)

        sep_left = QFrame(self.top_bar)
        sep_left.setFrameShape(QFrame.Shape.VLine)
        sep_left.setFixedWidth(1)
        sep_left.setStyleSheet(f"background-color: {COMFY_STROKE};")
        top_lay.addWidget(sep_left)

        # ComfyUI Workflow Tabs + '+' New Blank Workflow Button
        self.workflow_tabs = QTabBar(self.top_bar)
        self.workflow_tabs.setObjectName("WorkflowTabs")
        self.workflow_tabs.setTabsClosable(False)
        self.workflow_tabs.setMovable(True)
        self.workflow_tabs.setExpanding(False)
        self.workflow_tabs.setDrawBase(False)
        self.workflow_tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self._add_workflow_tab_item(self._tabs[0])
        self.workflow_tabs.currentChanged.connect(self._on_workflow_tab_changed)
        self.workflow_tabs.tabMoved.connect(self._on_workflow_tab_moved)
        self.workflow_tabs.tabBarDoubleClicked.connect(self._begin_tab_rename)
        top_lay.addWidget(self.workflow_tabs)

        self.btn_new_tab = QToolButton(self.top_bar)
        self.btn_new_tab.setObjectName("NewWorkflowButton")
        self.btn_new_tab.setText("+")
        self.btn_new_tab.setFixedSize(28, 28)
        self.btn_new_tab.setToolTip("New Blank Workflow (Ctrl+N)")
        self.btn_new_tab.setAccessibleName("New Blank Workflow")
        self.btn_new_tab.clicked.connect(self.new_project_tab)
        top_lay.addWidget(self.btn_new_tab, 0, Qt.AlignmentFlag.AlignVCenter)

        top_lay.addStretch(1)

        # ComfyUI Right Action Cluster (compact Undo / Redo / Save / Refresh + primary Run button)
        self.btn_undo = QToolButton(self.top_bar)
        self.btn_undo.setObjectName("ComfyIconBtn")
        self.btn_undo.setText("↶")
        self.btn_undo.setToolTip("Undo (Ctrl+Z)")
        self.btn_undo.setEnabled(False)
        self.btn_undo.clicked.connect(self.canvas.undo_stack.undo)
        top_lay.addWidget(self.btn_undo)

        self.btn_redo = QToolButton(self.top_bar)
        self.btn_redo.setObjectName("ComfyIconBtn")
        self.btn_redo.setText("↷")
        self.btn_redo.setToolTip("Redo (Ctrl+Y)")
        self.btn_redo.setEnabled(False)
        self.btn_redo.clicked.connect(self.canvas.undo_stack.redo)
        top_lay.addWidget(self.btn_redo)

        self.btn_save = QToolButton(self.top_bar)
        self.btn_save.setObjectName("ComfyIconBtn")
        self.btn_save.setText("Save")
        self.btn_save.setToolTip("Save Workflow (Ctrl+S)")
        self.btn_save.clicked.connect(self.save_project)
        top_lay.addWidget(self.btn_save)

        self.btn_refresh = QToolButton(self.top_bar)
        self.btn_refresh.setObjectName("ComfyIconBtn")
        self.btn_refresh.setText("Refresh")
        self.btn_refresh.setToolTip("Refresh Windows Audio Devices & Processes (F5)")
        self.btn_refresh.clicked.connect(self.refresh_devices_and_processes)
        top_lay.addWidget(self.btn_refresh)

        self.btn_engine = QPushButton("▶ Run", self.top_bar)
        self.btn_engine.setObjectName("ComfyRunButton")
        self.btn_engine.setProperty("running", False)
        self.btn_engine.setToolTip("Start / Stop Real-Time Audio Engine")
        self.btn_engine.clicked.connect(self._on_run_button_clicked)
        top_lay.addWidget(self.btn_engine)

        self.setMenuWidget(self.top_bar)

        # Register actions directly on window so shortcuts fire regardless of focus
        for act in (
            self.act_new,
            self.act_open,
            self.act_save,
            self.act_save_as,
            self.act_close_tab,
            self.act_undo,
            self.act_redo,
            self.act_select_all,
            self.act_group,
            self.act_toggle_terminal,
            self.act_zoom_in,
            self.act_zoom_out,
            self.act_zoom_reset,
            self.act_refresh,
        ):
            self.addAction(act)

    def _build_status_strip(self) -> None:
        strip = QWidget(self)
        strip.setObjectName("BottomToggleStrip")
        strip.setFixedHeight(26)
        lay = QHBoxLayout(strip)
        lay.setContentsMargins(8, 2, 10, 2)
        lay.setSpacing(10)

        self.btn_status_terminal = QToolButton(strip)
        self.btn_status_terminal.setObjectName("TerminalToggleBtn")
        self.btn_status_terminal.setText(">_ Terminal")
        self.btn_status_terminal.setCheckable(True)
        self.btn_status_terminal.setChecked(True)
        self.btn_status_terminal.setToolTip("Open / Close Meters, Status & Logs Terminal (Ctrl+`)")
        self.btn_status_terminal.clicked.connect(self.toggle_terminal_panel)
        lay.addWidget(self.btn_status_terminal)

        self.btn_status_palette = QToolButton(strip)
        self.btn_status_palette.setObjectName("TerminalToggleBtn")
        self.btn_status_palette.setText("Node Palette")
        self.btn_status_palette.setCheckable(True)
        self.btn_status_palette.setChecked(True)
        self.btn_status_palette.setToolTip("Open / Close Node Palette Sidebar")
        self.btn_status_palette.clicked.connect(self.toggle_palette_panel)
        lay.addWidget(self.btn_status_palette)

        self.lbl_status_engine = QLabel("Engine Stopped", strip)
        self.lbl_status_engine.setStyleSheet("color: #999999; font-size: 11px;")
        lay.addWidget(self.lbl_status_engine)
        lay.addStretch(1)

        self.lbl_status_hint = QLabel("Ctrl+A Select All  •  Ctrl+Click Multi-Select  •  Ctrl+S Save", strip)
        self.lbl_status_hint.setStyleSheet("color: #777777; font-size: 11px;")
        lay.addWidget(self.lbl_status_hint)

        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        sb.setContentsMargins(0, 0, 0, 0)
        sb.addPermanentWidget(strip, 1)

    def _connect_signals(self) -> None:
        self.left_panel.add_node_requested.connect(self._on_add_node_from_palette)
        self.left_panel.close_requested.connect(lambda: self.set_palette_visible(False))

        self.bottom_panel.close_requested.connect(lambda: self.set_terminal_visible(False))
        self.bottom_dock.visibilityChanged.connect(self._on_terminal_dock_visibility)
        self.left_dock.visibilityChanged.connect(self._on_palette_dock_visibility)
        self.left_panel.tabs.currentChanged.connect(lambda _idx: self._on_ui_state_changed())
        self.bottom_panel.tabs.currentChanged.connect(lambda _idx: self._on_ui_state_changed())

        self.canvas.graph_changed.connect(self.sync_graph_to_engine)
        self.canvas.workspace_modified.connect(self._on_canvas_workspace_modified)
        self.canvas.viewport_changed.connect(self.schedule_cache_save)
        self.canvas.validation_error.connect(self._on_validation_error)
        self.canvas.float_param_changed.connect(self._on_float_param_changed)
        self.canvas.int_param_changed.connect(self._on_int_param_changed)

        self.canvas.undo_stack.canUndoChanged.connect(lambda _=None: self._update_undo_redo_buttons())
        self.canvas.undo_stack.canRedoChanged.connect(lambda _=None: self._update_undo_redo_buttons())
        self.canvas.undo_stack.undoTextChanged.connect(lambda _=None: self._update_undo_redo_buttons())
        self.canvas.undo_stack.redoTextChanged.connect(lambda _=None: self._update_undo_redo_buttons())

    # ----- Bottom Terminal & Palette Open/Close -----

    def _on_ui_state_changed(self) -> None:
        if self._ui_ready and not self._closing and not self._restoring_cache:
            self.schedule_cache_save()

    def _on_palette_dock_visibility(self, visible: bool) -> None:
        self._sync_palette_toggle_state(visible)
        if not self._closing and not self._restoring_cache:
            self._palette_visible = bool(visible)
            self._on_ui_state_changed()

    def _on_terminal_dock_visibility(self, visible: bool) -> None:
        self._sync_terminal_toggle_state(visible)
        if not self._closing and not self._restoring_cache:
            self._terminal_visible = bool(visible)
            self._on_ui_state_changed()

    def set_palette_visible(self, visible: bool) -> None:
        if not self._closing and not self._restoring_cache:
            self._palette_visible = bool(visible)
        self.left_dock.setVisible(visible)
        if visible:
            self.left_dock.raise_()
            self.resizeDocks([self.left_dock], [self._cached_palette_width], Qt.Orientation.Horizontal)
        self._sync_palette_toggle_state(visible)
        self._on_ui_state_changed()

    def toggle_palette_panel(self) -> None:
        self.set_palette_visible(not self.left_dock.isVisible())

    def _sync_palette_toggle_state(self, visible: bool) -> None:
        for ctrl in (self.btn_status_palette, self.act_toggle_palette):
            ctrl.blockSignals(True)
            ctrl.setChecked(visible)
            ctrl.blockSignals(False)

    def set_terminal_visible(self, visible: bool) -> None:
        if not self._closing and not self._restoring_cache:
            self._terminal_visible = bool(visible)
        self.bottom_dock.setVisible(visible)
        if visible:
            self.bottom_dock.raise_()
            self.resizeDocks([self.bottom_dock], [self._cached_terminal_height], Qt.Orientation.Vertical)
        self._sync_terminal_toggle_state(visible)
        self._on_ui_state_changed()

    def toggle_terminal_panel(self) -> None:
        self.set_terminal_visible(not self.bottom_dock.isVisible())

    def _sync_terminal_toggle_state(self, visible: bool) -> None:
        for ctrl in (self.btn_status_terminal, self.act_toggle_terminal):
            ctrl.blockSignals(True)
            ctrl.setChecked(visible)
            ctrl.blockSignals(False)

    def _collect_ui_state(self) -> Dict[str, Any]:
        is_max = bool(self.isMaximized())
        ng = self.normalGeometry()
        geom = (
            ng
            if (is_max and ng.isValid() and ng.width() >= 200 and ng.height() >= 200)
            else self.geometry()
        )
        if not self._closing:
            if self._palette_visible and self.left_dock.isVisible() and self.left_dock.width() > 0:
                self._cached_palette_width = max(200, int(self.left_dock.width()))
            if self._terminal_visible and self.bottom_dock.isVisible() and self.bottom_dock.height() > 0:
                self._cached_terminal_height = max(120, int(self.bottom_dock.height()))
        return {
            "window_x": int(geom.x()),
            "window_y": int(geom.y()),
            "window_width": int(geom.width()),
            "window_height": int(geom.height()),
            "window_maximized": is_max,
            "palette_visible": bool(self._palette_visible),
            "palette_width": int(self._cached_palette_width),
            "palette_tab": int(self.left_panel.tabs.currentIndex()),
            "terminal_visible": bool(self._terminal_visible),
            "terminal_height": int(self._cached_terminal_height),
            "terminal_tab": int(self.bottom_panel.tabs.currentIndex()),
        }

    def _apply_pending_ui_restore(self, include_maximized: bool = False) -> None:
        ui = self._pending_ui_restore
        if not isinstance(ui, dict):
            return
        self._restoring_cache = True
        try:
            self.left_dock.setVisible(bool(ui["palette_visible"]))
            self._sync_palette_toggle_state(bool(ui["palette_visible"]))
            self.bottom_dock.setVisible(bool(ui["terminal_visible"]))
            self._sync_terminal_toggle_state(bool(ui["terminal_visible"]))
            self.resizeDocks([self.left_dock], [int(ui["palette_width"])], Qt.Orientation.Horizontal)
            self.resizeDocks([self.bottom_dock], [int(ui["terminal_height"])], Qt.Orientation.Vertical)
            if include_maximized and ui.get("window_maximized"):
                self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)
        finally:
            self._restoring_cache = False

    def _finalize_initial_ui(self) -> None:
        if self._closing:
            return
        if self._pending_ui_restore is not None:
            self._apply_pending_ui_restore(include_maximized=True)
            self._pending_ui_restore = None
        self._baseline_ui_state = self._collect_ui_state()
        self._ui_ready = True

    def _restore_ui_state(self, ui: Dict[str, Any]) -> None:
        if not isinstance(ui, dict):
            return
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else None
        try:
            w = max(780, int(ui.get("window_width", self.width())))
            h = max(500, int(ui.get("window_height", self.height())))
            x = int(ui.get("window_x", self.x()))
            y = int(ui.get("window_y", self.y()))
            if avail is not None:
                w = min(w, avail.width())
                h = min(h, avail.height())
                x = max(avail.left(), min(x, avail.right() - min(w, 240)))
                y = max(avail.top(), min(y, avail.bottom() - min(h, 180)))
            self.setGeometry(x, y, w, h)
        except (TypeError, ValueError):
            pass

        pal_vis = bool(ui.get("palette_visible", True))
        term_vis = bool(ui.get("terminal_visible", True))
        pal_w = max(200, min(560, int(ui.get("palette_width", self._cached_palette_width))))
        term_h = max(120, min(560, int(ui.get("terminal_height", self._cached_terminal_height))))
        pal_tab = int(ui.get("palette_tab", 0))
        term_tab = int(ui.get("terminal_tab", 0))
        is_max = bool(ui.get("window_maximized", False))

        self._palette_visible = pal_vis
        self._terminal_visible = term_vis
        self._cached_palette_width = pal_w
        self._cached_terminal_height = term_h

        if 0 <= pal_tab < self.left_panel.tabs.count():
            self.left_panel.tabs.setCurrentIndex(pal_tab)
        if 0 <= term_tab < self.bottom_panel.tabs.count():
            self.bottom_panel.tabs.setCurrentIndex(term_tab)

        self._pending_ui_restore = {
            "window_maximized": is_max,
            "palette_visible": pal_vis,
            "palette_width": pal_w,
            "terminal_visible": term_vis,
            "terminal_height": term_h,
        }
        self._apply_pending_ui_restore(include_maximized=is_max)

    # ----- Workflow Tabs & Dirty (*) State -----

    def _active_tab(self) -> WorkflowTabState:
        idx = self.workflow_tabs.currentIndex()
        if 0 <= idx < len(self._tabs):
            return self._tabs[idx]
        return self._tabs[0]

    def _refresh_tab_title(self, idx: int) -> None:
        if 0 <= idx < len(self._tabs):
            tab = self._tabs[idx]
            self.workflow_tabs.setTabText(idx, tab.display_title())
            if idx == self.workflow_tabs.currentIndex():
                self._sync_window_title()

    def _sync_window_title(self) -> None:
        tab = self._active_tab()
        self.setWindowTitle(f"{tab.display_title()} — AudioFlow Studio")

    def _on_canvas_workspace_modified(self) -> None:
        if not self._switching_tabs and not self._restoring_cache:
            idx = self.workflow_tabs.currentIndex()
            if 0 <= idx < len(self._tabs) and not self._tabs[idx].dirty:
                self._tabs[idx].dirty = True
                self._refresh_tab_title(idx)
        self.schedule_cache_save()

    def _on_workflow_tab_changed(self, new_idx: int) -> None:
        if not (0 <= new_idx < len(self._tabs)):
            return
        if not self._restoring_cache:
            for prev in self._tabs:
                if prev.tab_id == self._active_tab_id:
                    prev.view_state = self.canvas.get_view_state()
                    break
        tab = self._tabs[new_idx]
        self._active_tab_id = tab.tab_id
        self._switching_tabs = True
        try:
            self.project = tab.project
            self.project_path = tab.file_path
            self.canvas.project = self.project
            self.canvas.rebuild_from_project()
            self.canvas.update_catalog(self._devices, self._processes)
            self.canvas.set_view_state(
                tab.view_state
                if tab.view_state is not None
                else {"center_x": 260.0, "center_y": 180.0, "zoom": 1.0}
            )
            self.sync_graph_to_engine()
            self._sync_window_title()
            self._update_undo_redo_buttons()
        finally:
            self._switching_tabs = False

    def _on_workflow_tab_moved(self, from_idx: int, to_idx: int) -> None:
        if 0 <= from_idx < len(self._tabs) and 0 <= to_idx < len(self._tabs):
            tab = self._tabs.pop(from_idx)
            self._tabs.insert(to_idx, tab)

    def _begin_tab_rename(self, idx: int) -> None:
        if not (0 <= idx < len(self._tabs)):
            return
        if self._tab_rename_editor is not None and isValid(self._tab_rename_editor):
            self._tab_rename_editor.cancelled = True
            self._tab_rename_editor.deleteLater()
            self._tab_rename_editor = None

        tab = self._tabs[idx]
        target_tab_id = tab.tab_id
        editor = _TabRenameLineEdit(tab.base_title(), self.workflow_tabs)
        editor.setGeometry(self.workflow_tabs.tabRect(idx).adjusted(4, 3, -24, -4))
        editor.setStyleSheet(
            "background-color: #222222; color: #FFFFFF; border: 1px solid #31B9F4; "
            "border-radius: 4px; padding: 0px 4px; font-size: 12px;"
        )
        self._tab_rename_editor = editor
        done = False

        def _commit() -> None:
            nonlocal done
            if done:
                return
            done = True
            cancelled = editor.cancelled
            new_text = editor.text()
            if self._tab_rename_editor is editor:
                self._tab_rename_editor = None
            if isValid(editor):
                editor.deleteLater()
            if cancelled:
                return
            for i, t in enumerate(self._tabs):
                if t.tab_id == target_tab_id:
                    self.rename_workflow_tab(i, new_text)
                    break

        editor.returnPressed.connect(_commit)
        editor.editingFinished.connect(_commit)
        editor.show()
        editor.selectAll()
        editor.setFocus()

    def rename_workflow_tab(self, idx: int, new_name: str) -> bool:
        if not (0 <= idx < len(self._tabs)):
            return False
        tab = self._tabs[idx]
        clean = new_name.strip().replace("/", "_").replace("\\", "_")
        if not clean or clean == tab.base_title():
            return False
        if tab.file_path is not None:
            target = tab.file_path.with_name(f"{clean}{tab.file_path.suffix}")
            if target != tab.file_path:
                if target.exists():
                    QMessageBox.warning(
                        self,
                        "Rename Workflow",
                        f"A workflow file named '{target.name}' already exists.",
                    )
                    return False
                if tab.file_path.is_file():
                    tab.file_path.rename(target)
                tab.file_path = target
                if idx == self.workflow_tabs.currentIndex():
                    self.project_path = target
        tab.title = clean
        self._refresh_tab_title(idx)
        if idx == self.workflow_tabs.currentIndex():
            self.schedule_cache_save()
        return True

    def _add_workflow_tab_item(self, tab: WorkflowTabState) -> int:
        idx = self.workflow_tabs.addTab(tab.display_title())
        close_btn = QToolButton(self.workflow_tabs)
        close_btn.setObjectName("TerminalCloseButton")
        close_btn.setText("×")
        close_btn.setFixedSize(16, 16)
        close_btn.setStyleSheet("padding: 0px; margin: 0px; font-size: 13px;")
        close_btn.setToolTip("Close Workflow")
        close_btn.clicked.connect(lambda _=False, t=tab: self._close_tab_by_id(t.tab_id))
        self.workflow_tabs.setTabButton(idx, QTabBar.ButtonPosition.RightSide, close_btn)
        return idx

    def _close_tab_by_id(self, tab_id: int) -> None:
        for i, t in enumerate(self._tabs):
            if t.tab_id == tab_id:
                self.close_workflow_tab(i)
                return

    def new_project_tab(self) -> int:
        self._next_tab_seq += 1
        proj = ProjectModel()
        title = f"Untitled Workflow {self._next_tab_seq}"
        tab = WorkflowTabState(tab_id=self._next_tab_seq, title=title, project=proj, dirty=False)
        self._tabs.append(tab)
        new_idx = self._add_workflow_tab_item(tab)
        self.workflow_tabs.setCurrentIndex(new_idx)
        return new_idx

    def close_workflow_tab(self, idx: int) -> None:
        if not (0 <= idx < len(self._tabs)):
            return
        if len(self._tabs) == 1:
            self.new_project()
            return
        self._tabs.pop(idx)
        self.workflow_tabs.removeTab(idx)
        self._sync_window_title()

    def select_all_action(self) -> None:
        fw = QApplication.focusWidget()
        if isinstance(fw, QLineEdit):
            fw.selectAll()
            return
        self.canvas.select_all_nodes()

    def _update_undo_redo_buttons(self) -> None:
        if self._closing or not hasattr(self, "canvas") or not isValid(self.canvas) or not isValid(self.canvas.undo_stack):
            return
        stack = self.canvas.undo_stack
        self.btn_undo.setEnabled(stack.canUndo())
        self.btn_redo.setEnabled(stack.canRedo())
        undo_txt = stack.undoText()
        redo_txt = stack.redoText()
        self.btn_undo.setToolTip(f"Undo {undo_txt} (Ctrl+Z)" if undo_txt else "Undo (Ctrl+Z)")
        self.btn_redo.setToolTip(f"Redo {redo_txt} (Ctrl+Y)" if redo_txt else "Redo (Ctrl+Y)")

    # ----- Engine / Catalog -----

    def refresh_devices_and_processes(self) -> None:
        self._devices = self.engine.enumerate_devices()
        self._processes = self.engine.enumerate_processes()
        self._last_device_seq = self.engine.device_change_seq()

        avail_ids = {d.id for d in self._devices}
        for node in self.project.nodes.values():
            if node.device_id:
                node.device_available = node.device_id in avail_ids

        self.left_panel.set_devices(self._devices)
        self.left_panel.set_processes(self._processes)
        self.canvas.update_catalog(self._devices, self._processes)
        self.bottom_panel.update_devices_table(self._devices)
        self.bottom_panel.push_native_logs(self.engine.poll_logs())

    def _on_run_button_clicked(self) -> None:
        self._toggle_engine(not self.act_engine_toggle.isChecked())

    def _sync_run_button(self, running: bool) -> None:
        self.act_engine_toggle.blockSignals(True)
        self.act_engine_toggle.setChecked(running)
        self.act_engine_toggle.setText("Stop Audio Engine" if running else "Start Audio Engine")
        self.act_engine_toggle.blockSignals(False)

        self.btn_engine.setText("■ Stop" if running else "▶ Run")
        self.btn_engine.setProperty("running", running)
        self.btn_engine.style().unpolish(self.btn_engine)
        self.btn_engine.style().polish(self.btn_engine)

    def start_engine(self) -> None:
        if self.engine.start():
            self._sync_run_button(True)
            self.sync_graph_to_engine()
            self.bottom_panel.append_log("INFO", "Real-time audio engine started.")
        else:
            self._sync_run_button(False)
            self.bottom_panel.append_log("ERROR", "Failed to start audio engine.")

    def stop_engine(self) -> None:
        self.engine.stop()
        self._sync_run_button(False)
        self.lbl_status_engine.setText("Engine Stopped")
        self.bottom_panel.append_log("INFO", "Audio engine stopped.")

    def _toggle_engine(self, checked: bool) -> None:
        if checked:
            self.start_engine()
        else:
            self.stop_engine()

    def sync_graph_to_engine(self) -> None:
        ok, err = self.engine.apply_graph(self.project.to_engine_payload())
        if not ok and err:
            self.bottom_panel.append_log("ERROR", f"Graph sync error: {err}")
        self.bottom_panel.update_connections(self.project.nodes, self.project.connections)

    def _on_add_node_from_palette(self, node_type_val: int, kwargs: dict) -> None:
        center = self.canvas.mapToScene(self.canvas.viewport().rect().center())
        self.canvas.add_node_action(NodeType(node_type_val), scene_pos=center, **kwargs)

    def _on_float_param_changed(self, node_id: int, param: str, val: float) -> None:
        if not self.engine.set_float_param(node_id, param, val):
            self.sync_graph_to_engine()

    def _on_int_param_changed(self, node_id: int, param: str, val: int) -> None:
        if not self.engine.set_int_param(node_id, param, val):
            self.sync_graph_to_engine()

    def _on_validation_error(self, msg: str) -> None:
        self.lbl_status_engine.setText(msg)
        self.bottom_panel.append_log("WARN", msg)

    def _poll_engine_telemetry(self) -> None:
        if self.isMinimized():
            return
        seq = self.engine.device_change_seq()
        if seq != self._last_device_seq:
            self.refresh_devices_and_processes()
            self.bottom_panel.append_log("INFO", "Windows audio device change detected; catalog updated.")

        stats = self.engine.get_engine_stats()
        meters = self.engine.get_node_meters()
        logs = self.engine.poll_logs()

        self.canvas.update_meters(meters)
        self.bottom_panel.update_telemetry(stats, self.project.nodes, meters)
        state = "Running" if stats.running else "Stopped"
        self.lbl_status_engine.setText(
            f"Engine {state}  •  {stats.sample_rate} Hz  •  {stats.active_nodes} nodes  •  Blocks: {stats.processed_blocks}"
        )
        if logs:
            self.bottom_panel.push_native_logs(logs)

    # ----- Project Cache & Persistence -----

    def schedule_cache_save(self) -> None:
        if not self._restoring_cache and self._persist_cache:
            self._cache_dirty = True
            self._cache_timer.start()

    def save_workspace_cache(self) -> None:
        if not self._persist_cache:
            return
        try:
            if self.cache_path.is_file():
                try:
                    existing = self.cache_path.read_text(encoding="utf-8")
                    if '"nodes": []' not in existing and self.project.nodes:
                        bak = self.cache_path.with_suffix(".bak.json")
                        bak.write_text(existing, encoding="utf-8")
                except OSError:
                    pass
            data = self.project.to_project_dict()
            data["viewport"] = self.canvas.get_view_state()
            data["workflow_title"] = self._active_tab().title
            data["ui"] = self._collect_ui_state()
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            self._baseline_ui_state = dict(data["ui"])
            self._cache_dirty = False
        except OSError as exc:
            self.bottom_panel.append_log("WARN", f"Failed to write workspace cache: {exc}")

    def load_workspace_cache(self) -> bool:
        if not self.cache_path.is_file():
            return False
        self._restoring_cache = True
        try:
            avail_ids = {d.id for d in self._devices}
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            missing = self.project.load_project_dict(raw, available_device_ids=avail_ids)
            self.canvas.rebuild_from_project()
            self.canvas.update_catalog(self._devices, self._processes)
            vp = raw.get("viewport")
            if isinstance(vp, dict):
                self.canvas.set_view_state(vp)
            ui_state = raw.get("ui")
            if isinstance(ui_state, dict):
                self._restore_ui_state(ui_state)
            self.sync_graph_to_engine()
            idx = self.workflow_tabs.currentIndex()
            if 0 <= idx < len(self._tabs):
                saved_title = raw.get("workflow_title")
                if isinstance(saved_title, str) and saved_title.strip():
                    self._tabs[idx].title = saved_title.strip()
                self._tabs[idx].dirty = False
                self._tabs[idx].view_state = dict(vp) if isinstance(vp, dict) else None
                self._refresh_tab_title(idx)
            if missing:
                self.bottom_panel.append_log(
                    "WARN",
                    f"Restored cache with {len(missing)} unavailable device binding(s) preserved.",
                )
            if self.project.nodes:
                self.bottom_panel.append_log(
                    "INFO",
                    f"Restored {len(self.project.nodes)} node(s) from workspace cache.",
                )
            return True
        except Exception as exc:
            self.bottom_panel.append_log("WARN", f"Failed to restore workspace cache: {exc}")
            return False
        finally:
            self._restoring_cache = False

    def new_project(self) -> None:
        self._switching_tabs = True
        try:
            self.project.clear()
            self.project_path = None
            idx = self.workflow_tabs.currentIndex()
            if 0 <= idx < len(self._tabs):
                self._tabs[idx].file_path = None
                self._tabs[idx].dirty = False
                self._tabs[idx].view_state = None
                self._refresh_tab_title(idx)
            self.canvas.rebuild_from_project()
            self.canvas.set_view_state({"center_x": 260.0, "center_y": 180.0, "zoom": 1.0})
            self.sync_graph_to_engine()
            self._cache_dirty = True
            self.save_workspace_cache()
        finally:
            self._switching_tabs = False

    def save_project(self) -> None:
        if self.project_path is None:
            self.save_project_as_dialog()
            return
        self.project.save_file(self.project_path)
        idx = self.workflow_tabs.currentIndex()
        if 0 <= idx < len(self._tabs):
            self._tabs[idx].file_path = self.project_path
            self._tabs[idx].dirty = False
            self._refresh_tab_title(idx)
        self.lbl_status_engine.setText(f"Saved {self.project_path.name}")

    def save_project_as_dialog(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save AudioFlow Project", "", "AudioFlow Project (*.aflow *.json)")
        if not path:
            return
        self.project_path = Path(path)
        self.project.save_file(self.project_path)
        idx = self.workflow_tabs.currentIndex()
        if 0 <= idx < len(self._tabs):
            self._tabs[idx].file_path = self.project_path
            self._tabs[idx].dirty = False
            self._refresh_tab_title(idx)
        self.lbl_status_engine.setText(f"Saved {self.project_path.name}")

    def open_project_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open AudioFlow Project", "", "AudioFlow Project (*.aflow *.json)")
        if path:
            self.load_project_path(Path(path))

    def load_project_path(self, path: Path) -> None:
        self._switching_tabs = True
        try:
            self._devices = self.engine.enumerate_devices()
            avail_ids = {d.id for d in self._devices}
            missing = self.project.load_file(path, available_device_ids=avail_ids)
            self.project_path = path
            idx = self.workflow_tabs.currentIndex()
            if 0 <= idx < len(self._tabs):
                self._tabs[idx].file_path = path
                self._tabs[idx].dirty = False
                self._refresh_tab_title(idx)
            self.canvas.rebuild_from_project()
            self.canvas.update_catalog(self._devices, self._processes)
            self.sync_graph_to_engine()
            self.schedule_cache_save()
        finally:
            self._switching_tabs = False

        if missing:
            details = "\n".join(f"- #{m.id} {m.display_title()}: {m.device_name or m.device_id}" for m in missing)
            self.bottom_panel.append_log("WARN", f"Missing devices in loaded project (preserved without fallback): {len(missing)}")
            QMessageBox.warning(
                self,
                "Unavailable Audio Devices",
                "The following saved audio devices are currently unavailable.\n"
                "Original device bindings have been preserved and were NOT replaced:\n\n" + details,
            )

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if self._closing:
            return super().eventFilter(obj, event)
        etype = event.type()
        if etype == QEvent.Type.MouseButtonPress and hasattr(self, "canvas"):
            gpos = event.globalPosition().toPoint() if hasattr(event, "globalPosition") else None
            self.canvas.hide_open_popups(gpos)
        elif etype == QEvent.Type.Resize and self._ui_ready and not self._restoring_cache:
            if obj is self.left_dock and self._palette_visible and self.left_dock.width() > 0:
                self._cached_palette_width = max(200, int(self.left_dock.width()))
                self._on_ui_state_changed()
            elif obj is self.bottom_dock and self._terminal_visible and self.bottom_dock.height() > 0:
                self._cached_terminal_height = max(120, int(self.bottom_dock.height()))
                self._on_ui_state_changed()
        return super().eventFilter(obj, event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._shown_once:
            self._shown_once = True
            self._apply_pending_ui_restore(include_maximized=True)
            QTimer.singleShot(0, self._finalize_initial_ui)

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        self._on_ui_state_changed()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._on_ui_state_changed()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._on_ui_state_changed()

    def closeEvent(self, event) -> None:
        if (
            self._ui_ready
            and not self._restoring_cache
            and not self._cache_dirty
            and self._baseline_ui_state is not None
            and self._collect_ui_state() != self._baseline_ui_state
        ):
            self._cache_dirty = True
        self._closing = True
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.removeEventFilter(self)
        self._poll_timer.stop()
        self._cache_timer.stop()
        if self._cache_dirty:
            self.save_workspace_cache()
        if self._owns_engine:
            self.engine.close()
        super().closeEvent(event)
