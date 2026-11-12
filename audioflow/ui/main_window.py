"""Main studio QMainWindow integrating PySide6 canvas, sidebars, and native audio engine."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QToolBar,
)

from audioflow.engine_bridge import AudioFlowEngine, DeviceInfo, NodeType, ProcessInfo
from audioflow.models.graph_model import ProjectModel
from audioflow.ui.canvas import NodeCanvas
from audioflow.ui.panels import (
    BottomTelemetryPanel,
    InspectorPanel,
    LeftPalettePanel,
)
from audioflow.ui.theme import STUDIO_STYLESHEET


class AudioFlowMainWindow(QMainWindow):
    """Top-level studio window orchestrating UI actions and real-time audio graph sync."""

    DEFAULT_CACHE_PATH = Path.home() / ".audioflow" / "workspace_cache.json"

    def __init__(
        self,
        engine: Optional[AudioFlowEngine] = None,
        auto_start: bool = True,
        cache_path: Optional[Path] = None,
        restore_cache: bool = True,
    ) -> None:
        super().__init__()
        self.setWindowTitle("AudioFlow — Windows Audio Routing Studio")
        init_w, init_h = 1360, 760
        screen = QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            if avail.width() > 0 and avail.height() > 0:
                init_w = min(init_w, max(640, avail.width() - 40))
                init_h = min(init_h, max(480, avail.height() - 40))
        self.resize(init_w, init_h)
        self.setStyleSheet(STUDIO_STYLESHEET)

        self.engine = engine if engine is not None else AudioFlowEngine(sample_rate=48000, block_frames=256)
        self._owns_engine = engine is None
        self.project = ProjectModel()
        self.project_path: Optional[Path] = None
        self.cache_path: Path = Path(cache_path) if cache_path is not None else self.DEFAULT_CACHE_PATH
        self._restoring_cache = False
        self._devices: List[DeviceInfo] = []
        self._processes: List[ProcessInfo] = []
        self._last_device_seq: int = 0

        self._cache_timer = QTimer(self)
        self._cache_timer.setSingleShot(True)
        self._cache_timer.setInterval(300)
        self._cache_timer.timeout.connect(self.save_workspace_cache)

        # Central infinite node canvas
        self.canvas = NodeCanvas(self.project, self)
        self.setCentralWidget(self.canvas)

        # Dock panels (Left, Right, Bottom)
        self.left_panel = LeftPalettePanel(self)
        self.right_panel = InspectorPanel(self)
        self.bottom_panel = BottomTelemetryPanel(self)

        self._build_docks()
        self._build_menus_and_toolbar()
        self._connect_signals()

        self.refresh_devices_and_processes()
        if restore_cache:
            self.load_workspace_cache()
        if auto_start:
            self.start_engine()

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(80)
        self._poll_timer.timeout.connect(self._poll_engine_telemetry)
        self._poll_timer.start()

    def _build_docks(self) -> None:
        left_dock = QDockWidget("Sources & Devices", self)
        left_dock.setWidget(self.left_panel)
        left_dock.setMinimumWidth(250)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, left_dock)

        right_dock = QDockWidget("Node Properties", self)
        right_dock.setWidget(self.right_panel)
        right_dock.setMinimumWidth(260)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, right_dock)

        bottom_dock = QDockWidget("Meters, Status & Logs", self)
        bottom_dock.setWidget(self.bottom_panel)
        bottom_dock.setMinimumHeight(140)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, bottom_dock)

    def _build_menus_and_toolbar(self) -> None:
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("&File")
        edit_menu = menu_bar.addMenu("&Edit")
        view_menu = menu_bar.addMenu("&View")
        engine_menu = menu_bar.addMenu("&Engine")

        act_new = QAction("New Project", self)
        act_new.setShortcut(QKeySequence.StandardKey.New)
        act_new.triggered.connect(self.new_project)

        act_open = QAction("Open Project...", self)
        act_open.setShortcut(QKeySequence.StandardKey.Open)
        act_open.triggered.connect(self.open_project_dialog)

        act_save = QAction("Save Project", self)
        act_save.setShortcut(QKeySequence.StandardKey.Save)
        act_save.triggered.connect(self.save_project)

        act_save_as = QAction("Save Project As...", self)
        act_save_as.setShortcut(QKeySequence.StandardKey.SaveAs)
        act_save_as.triggered.connect(self.save_project_as_dialog)

        file_menu.addAction(act_new)
        file_menu.addAction(act_open)
        file_menu.addAction(act_save)
        file_menu.addAction(act_save_as)

        act_undo = self.canvas.undo_stack.createUndoAction(self, "Undo")
        act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        act_redo = self.canvas.undo_stack.createRedoAction(self, "Redo")
        act_redo.setShortcut(QKeySequence.StandardKey.Redo)
        act_group = QAction("Group Selected", self)
        act_group.setShortcut("Ctrl+G")
        act_group.triggered.connect(lambda: self.canvas.group_selected_action())
        act_delete = QAction("Delete Selected", self)
        act_delete.triggered.connect(self.canvas.delete_selected_action)

        edit_menu.addAction(act_undo)
        edit_menu.addAction(act_redo)
        edit_menu.addSeparator()
        edit_menu.addAction(act_group)
        edit_menu.addAction(act_delete)

        act_zin = QAction("Zoom In", self)
        act_zin.setShortcut("Ctrl+=")
        act_zin.triggered.connect(self.canvas.zoom_in)
        act_zout = QAction("Zoom Out", self)
        act_zout.setShortcut("Ctrl+-")
        act_zout.triggered.connect(self.canvas.zoom_out)
        act_zreset = QAction("Reset Zoom", self)
        act_zreset.setShortcut("Ctrl+0")
        act_zreset.triggered.connect(self.canvas.reset_zoom)

        view_menu.addAction(act_zin)
        view_menu.addAction(act_zout)
        view_menu.addAction(act_zreset)

        self.act_engine_toggle = QAction("Start Audio Engine", self)
        self.act_engine_toggle.setCheckable(True)
        self.act_engine_toggle.triggered.connect(self._toggle_engine)
        act_refresh = QAction("Refresh Devices & Processes", self)
        act_refresh.triggered.connect(self.refresh_devices_and_processes)

        engine_menu.addAction(self.act_engine_toggle)
        engine_menu.addAction(act_refresh)

        tb = QToolBar("Studio Toolbar", self)
        tb.addAction(act_new)
        tb.addAction(act_open)
        tb.addAction(act_save)
        tb.addSeparator()
        tb.addAction(act_undo)
        tb.addAction(act_redo)
        tb.addAction(act_group)
        tb.addAction(act_delete)
        tb.addSeparator()
        tb.addAction(act_zin)
        tb.addAction(act_zout)
        tb.addAction(act_zreset)
        tb.addSeparator()
        tb.addAction(self.act_engine_toggle)
        tb.addAction(act_refresh)
        self.addToolBar(tb)

    def _connect_signals(self) -> None:
        self.left_panel.add_node_requested.connect(self._on_add_node_from_palette)
        self.left_panel.refresh_requested.connect(self.refresh_devices_and_processes)

        self.canvas.graph_changed.connect(self.sync_graph_to_engine)
        self.canvas.workspace_modified.connect(self.schedule_cache_save)
        self.canvas.selection_changed.connect(self.right_panel.set_node)
        self.canvas.validation_error.connect(self._on_validation_error)

        self.right_panel.graph_rebuild_needed.connect(self._on_inspector_graph_rebuild)
        self.right_panel.float_param_changed.connect(self._on_float_param_changed)
        self.right_panel.int_param_changed.connect(self._on_int_param_changed)

    def refresh_devices_and_processes(self) -> None:
        self._devices = self.engine.enumerate_devices()
        self._processes = self.engine.enumerate_processes()
        self._last_device_seq = self.engine.device_change_seq()

        avail_ids = {d.id for d in self._devices}
        missing = self.project.refresh_device_availability(avail_ids)
        for item in self.canvas.node_items.values():
            item.update()

        self.left_panel.set_devices(self._devices)
        self.left_panel.set_processes(self._processes)
        self.right_panel.update_catalog(self._devices, self._processes)
        self.bottom_panel.update_devices_table(self._devices)

        if missing:
            names = ", ".join(m.device_name or m.device_id for m in missing)
            self.bottom_panel.append_log("WARN", f"Unavailable node endpoints detected: {names}")

    def start_engine(self) -> None:
        ok = self.engine.start()
        self.act_engine_toggle.setChecked(ok)
        self.act_engine_toggle.setText("Stop Audio Engine" if ok else "Start Audio Engine")
        self.statusBar().showMessage("Audio Engine Running" if ok else "Failed to start Audio Engine", 4000)
        if ok:
            self.sync_graph_to_engine()

    def stop_engine(self) -> None:
        self.engine.stop()
        self.act_engine_toggle.setChecked(False)
        self.act_engine_toggle.setText("Start Audio Engine")
        self.statusBar().showMessage("Audio Engine Stopped", 3000)

    def _toggle_engine(self, checked: bool) -> None:
        if checked:
            self.start_engine()
        else:
            self.stop_engine()

    def sync_graph_to_engine(self) -> None:
        self.bottom_panel.update_connections(self.project.nodes, self.project.connections)
        ok, err = self.engine.apply_graph(self.project.to_engine_payload())
        if not ok:
            self.bottom_panel.append_log("ERROR", f"Graph apply failed: {err}")
            self.statusBar().showMessage(f"Graph error: {err}", 5000)

    def _on_add_node_from_palette(self, node_type_val: int, kwargs: dict) -> None:
        node = self.canvas.add_node_action(NodeType(node_type_val), **kwargs)
        self.statusBar().showMessage(f"Added #{node.id} {node.display_title()}", 3000)

    def _on_inspector_graph_rebuild(self) -> None:
        for item in self.canvas.node_items.values():
            item.update()
        self.sync_graph_to_engine()
        self.schedule_cache_save()

    def _on_float_param_changed(self, node_id: int, param: str, val: float) -> None:
        self.engine.set_float_param(node_id, param, val)
        item = self.canvas.node_items.get(node_id)
        if item:
            item.update()
        self.schedule_cache_save()

    def _on_int_param_changed(self, node_id: int, param: str, val: int) -> None:
        self.engine.set_int_param(node_id, param, val)
        item = self.canvas.node_items.get(node_id)
        if item:
            item.update()
        self.schedule_cache_save()

    def _on_validation_error(self, msg: str) -> None:
        self.bottom_panel.append_log("WARN", msg)
        self.statusBar().showMessage(msg, 4000)

    def _poll_engine_telemetry(self) -> None:
        seq = self.engine.device_change_seq()
        if seq != self._last_device_seq:
            self.bottom_panel.append_log("INFO", "Windows audio device change detected; refreshing devices...")
            self.refresh_devices_and_processes()

        stats = self.engine.get_engine_stats()
        meters = self.engine.get_node_meters()
        logs = self.engine.poll_logs()

        if logs:
            self.bottom_panel.push_native_logs(logs)
        if not self.isVisible() or self.isMinimized():
            return

        self.canvas.update_meters(meters)
        self.bottom_panel.update_telemetry(stats, self.project.nodes, meters)

    # ----- Workspace Cache & File Operations -----

    def schedule_cache_save(self) -> None:
        if not self._restoring_cache:
            self._cache_timer.start()

    def save_workspace_cache(self) -> None:
        try:
            self.project.save_file(self.cache_path)
        except OSError as exc:
            self.bottom_panel.append_log("WARN", f"Failed to write workspace cache: {exc}")

    def load_workspace_cache(self) -> bool:
        if not self.cache_path.is_file():
            return False
        self._restoring_cache = True
        try:
            avail_ids = {d.id for d in self._devices}
            missing = self.project.load_file(self.cache_path, available_device_ids=avail_ids)
            self.canvas.rebuild_from_project()
            self.right_panel.set_node(None)
            self.sync_graph_to_engine()
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
        self.project.clear()
        self.project_path = None
        self.canvas.rebuild_from_project()
        self.right_panel.set_node(None)
        self.sync_graph_to_engine()
        self.save_workspace_cache()

    def save_project(self) -> None:
        if self.project_path is None:
            self.save_project_as_dialog()
            return
        self.project.save_file(self.project_path)
        self.statusBar().showMessage(f"Saved {self.project_path}", 3000)

    def save_project_as_dialog(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save AudioFlow Project", "", "AudioFlow Project (*.aflow *.json)")
        if not path:
            return
        self.project_path = Path(path)
        self.project.save_file(self.project_path)
        self.statusBar().showMessage(f"Saved {self.project_path}", 3000)

    def open_project_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open AudioFlow Project", "", "AudioFlow Project (*.aflow *.json)")
        if path:
            self.load_project_path(Path(path))

    def load_project_path(self, path: Path) -> None:
        self._devices = self.engine.enumerate_devices()
        avail_ids = {d.id for d in self._devices}
        missing = self.project.load_file(path, available_device_ids=avail_ids)
        self.project_path = path
        self.canvas.rebuild_from_project()
        self.right_panel.set_node(None)
        self.sync_graph_to_engine()
        self.schedule_cache_save()

        if missing:
            details = "\n".join(f"- #{m.id} {m.display_title()}: {m.device_name or m.device_id}" for m in missing)
            self.bottom_panel.append_log("WARN", f"Missing devices in loaded project (preserved without fallback): {len(missing)}")
            QMessageBox.warning(
                self,
                "Unavailable Audio Devices",
                "The following saved audio devices are currently unavailable.\n"
                "Original device bindings have been preserved and were NOT replaced:\n\n" + details,
            )

    def closeEvent(self, event) -> None:
        self._poll_timer.stop()
        self._cache_timer.stop()
        self.save_workspace_cache()
        if self._owns_engine:
            self.engine.close()
        super().closeEvent(event)

