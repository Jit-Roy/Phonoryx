"""Left, Right, and Bottom dock panels for AudioFlow Studio."""

from __future__ import annotations

import json
from typing import Dict, List, Optional

from PySide6.QtCore import QMimeData, QPoint, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from audioflow.engine_bridge import (
    DataFlow,
    DeviceInfo,
    EngineStats,
    LogEntry,
    NodeMeter,
    NodeType,
    ProcessInfo,
)
from audioflow.models.graph_model import (
    NODE_SPECS,
    ConnectionModel,
    NodeCategory,
    NodeModel,
)
from audioflow.ui.canvas import NODE_DRAG_MIME_TYPE
from audioflow.ui.widget_helpers import (
    build_device_combo_entries,
    build_process_combo_entries,
    virtual_driver_missing_text,
)


class DraggablePaletteList(QListWidget):
    """QListWidget supporting ComfyUI-style drag-to-canvas node creation."""

    NODE_PAYLOAD_ROLE = Qt.ItemDataRole.UserRole + 1

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)

    @staticmethod
    def build_drag_pixmap(title: str) -> QPixmap:
        label = title if len(title) <= 28 else title[:25] + "..."
        width, height = 210, 38
        pix = QPixmap(width, height)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(35, 43, 56, 235))
        p.setPen(QPen(QColor("#3a76f0"), 1.5))
        p.drawRoundedRect(1, 1, width - 2, height - 2, 7, 7)
        p.setPen(QColor("#e8eef7"))
        p.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        p.drawText(12, 0, width - 24, height, Qt.AlignmentFlag.AlignVCenter, label)
        p.end()
        return pix

    def startDrag(self, supportedActions) -> None:  # noqa: N802
        item = self.currentItem()
        if item is None:
            return
        payload = item.data(self.NODE_PAYLOAD_ROLE)
        if not isinstance(payload, dict):
            return
        mime = QMimeData()
        mime.setData(NODE_DRAG_MIME_TYPE, json.dumps(payload).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        pix = self.build_drag_pixmap(item.text().strip())
        drag.setPixmap(pix)
        drag.setHotSpot(QPoint(pix.width() // 2, 14))
        drag.exec(Qt.DropAction.CopyAction)


class LeftPalettePanel(QWidget):
    """Left sidebar: Devices, Running Applications, Source/Processing/Output node palette."""

    add_node_requested = Signal(int, dict)  # NodeType int, kwargs
    refresh_requested = Signal()
    close_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("PanelBody")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        hdr = QHBoxLayout()
        hdr.setContentsMargins(2, 0, 0, 0)
        hdr.setSpacing(6)
        lbl_title = QLabel("Palette & Sources")
        lbl_title.setStyleSheet("color: #FFFFFF; font-weight: 600;")
        hdr.addWidget(lbl_title)
        hdr.addStretch(1)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.refresh_requested.emit)
        hdr.addWidget(self.btn_refresh)
        self.btn_close = QToolButton(self)
        self.btn_close.setObjectName("TerminalCloseButton")
        self.btn_close.setText("×")
        self.btn_close.setToolTip("Close Node Palette Sidebar")
        self.btn_close.setAccessibleName("Close Palette")
        self.btn_close.clicked.connect(self.close_requested.emit)
        hdr.addWidget(self.btn_close)
        layout.addLayout(hdr)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # Tab 1: Palette (Sources / Processing / Outputs)
        pal_widget = QWidget()
        pal_lay = QVBoxLayout(pal_widget)
        self.palette_list = DraggablePaletteList()
        self._populate_palette()
        self.palette_list.itemDoubleClicked.connect(self._on_palette_activate)
        pal_lay.addWidget(QLabel("Drag onto canvas or double-click a node:"))
        pal_lay.addWidget(self.palette_list)
        self.tabs.addTab(pal_widget, "Nodes")

        # Tab 2: Windows Audio Devices
        dev_widget = QWidget()
        dev_lay = QVBoxLayout(dev_widget)
        self.device_list = DraggablePaletteList()
        self.device_list.itemDoubleClicked.connect(self._on_device_activate)
        dev_lay.addWidget(QLabel("Drag onto canvas or double-click a device:"))
        dev_lay.addWidget(self.device_list)
        self.tabs.addTab(dev_widget, "Devices")

        # Tab 3: Running Applications
        proc_widget = QWidget()
        proc_lay = QVBoxLayout(proc_widget)
        self.proc_filter = QLineEdit()
        self.proc_filter.setPlaceholderText("Filter applications / PID...")
        self.proc_filter.textChanged.connect(self._filter_processes)
        self.chk_audio_only = QCheckBox("Audio-producing sessions only")
        self.chk_audio_only.stateChanged.connect(lambda _: self._filter_processes())
        self.proc_list = DraggablePaletteList()
        self.proc_list.itemDoubleClicked.connect(self._on_process_activate)
        proc_lay.addWidget(self.proc_filter)
        proc_lay.addWidget(self.chk_audio_only)
        proc_lay.addWidget(QLabel("Drag onto canvas or double-click an app:"))
        proc_lay.addWidget(self.proc_list)
        self.tabs.addTab(proc_widget, "Apps")

        self._processes: List[ProcessInfo] = []

    def _populate_palette(self) -> None:
        self.palette_list.clear()
        sections = [
            ("--- SOURCE NODES ---", NodeCategory.SOURCE),
            ("--- PROCESSING NODES ---", NodeCategory.PROCESSING),
            ("--- OUTPUT NODES ---", NodeCategory.OUTPUT),
        ]
        for header, cat in sections:
            h_item = QListWidgetItem(header)
            h_item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.palette_list.addItem(h_item)
            for nt, spec in NODE_SPECS.items():
                if spec.category == cat:
                    it = QListWidgetItem(f"  {spec.title}")
                    it.setData(Qt.ItemDataRole.UserRole, int(nt))
                    it.setData(
                        DraggablePaletteList.NODE_PAYLOAD_ROLE,
                        {"node_type": int(nt), "kwargs": {}},
                    )
                    it.setToolTip(spec.description)
                    self.palette_list.addItem(it)

    @staticmethod
    def _device_drag_payload(d: DeviceInfo) -> dict:
        if d.flow == DataFlow.CAPTURE:
            nt = NodeType.VIRTUAL_INPUT if d.is_virtual else NodeType.MIC_INPUT
        else:
            nt = NodeType.VIRTUAL_OUTPUT if d.is_virtual else NodeType.SPEAKER_OUTPUT
        return {
            "node_type": int(nt),
            "kwargs": {"device_id": d.id, "device_name": d.name},
        }

    def set_devices(self, devices: List[DeviceInfo]) -> None:
        self.device_list.clear()
        for d in devices:
            flow_tag = "IN" if d.flow == DataFlow.CAPTURE else "OUT"
            virt_tag = " [VIRTUAL]" if d.is_virtual else ""
            def_tag = " *" if d.is_default else ""
            it = QListWidgetItem(f"[{flow_tag}]{virt_tag}{def_tag} {d.name}")
            it.setData(Qt.ItemDataRole.UserRole, d)
            it.setData(
                DraggablePaletteList.NODE_PAYLOAD_ROLE,
                self._device_drag_payload(d),
            )
            it.setToolTip(f"ID: {d.id}\nRate: {d.sample_rate} Hz | {d.channels}ch")
            self.device_list.addItem(it)

    def set_processes(self, processes: List[ProcessInfo]) -> None:
        self._processes = processes
        self._filter_processes()

    def _filter_processes(self) -> None:
        needle = self.proc_filter.text().strip().lower()
        audio_only = self.chk_audio_only.isChecked()
        self.proc_list.clear()
        for p in self._processes:
            if audio_only and not p.has_audio_session:
                continue
            label = f"{p.exe_name} (PID {p.pid})"
            if p.window_title:
                label += f" — {p.window_title}"
            if needle and needle not in label.lower():
                continue
            if p.has_audio_session:
                pct = int(min(100, round(p.session_peak * 100)))
                badge = f"[AUDIO {pct}%] " if pct > 0 else "[AUDIO] "
            else:
                badge = ""
            it = QListWidgetItem(badge + label)
            it.setData(Qt.ItemDataRole.UserRole, p)
            it.setData(
                DraggablePaletteList.NODE_PAYLOAD_ROLE,
                {
                    "node_type": int(NodeType.APP_CAPTURE),
                    "kwargs": {"pid": p.pid, "process_name": p.exe_name},
                },
            )
            it.setToolTip(p.exe_path or label)
            self.proc_list.addItem(it)

    def _emit_payload_item(self, item: QListWidgetItem) -> None:
        payload = item.data(DraggablePaletteList.NODE_PAYLOAD_ROLE)
        if isinstance(payload, dict) and "node_type" in payload:
            self.add_node_requested.emit(
                int(payload["node_type"]),
                dict(payload.get("kwargs") or {}),
            )

    def _on_palette_activate(self, item: QListWidgetItem) -> None:
        self._emit_payload_item(item)

    def _on_device_activate(self, item: QListWidgetItem) -> None:
        self._emit_payload_item(item)

    def _on_process_activate(self, item: QListWidgetItem) -> None:
        self._emit_payload_item(item)


class InspectorPanel(QWidget):
    """Right sidebar: Selected node properties, device/process selection, DSP parameters."""

    graph_rebuild_needed = Signal()
    float_param_changed = Signal(int, str, float)  # node_id, param, value
    int_param_changed = Signal(int, str, int)      # node_id, param, value

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._node: Optional[NodeModel] = None
        self._devices: List[DeviceInfo] = []
        self._processes: List[ProcessInfo] = []
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack)

        empty = QLabel("Select a node on the canvas\nto inspect and configure it.")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(empty)

        editor = QWidget()
        form_root = QVBoxLayout(editor)
        form = QFormLayout()
        form_root.addLayout(form)

        self.lbl_title = QLabel("-")
        self.lbl_title.setStyleSheet("font-weight: bold; font-size: 13px; color: #F8FAFC;")

        self.lbl_missing_warn = QLabel()
        self.lbl_missing_warn.setWordWrap(True)
        self.lbl_missing_warn.setStyleSheet(
            "background-color: #450A0A; color: #FCA5A5; border: 1px solid #DC2626; "
            "border-radius: 4px; padding: 6px;"
        )
        self.lbl_missing_warn.hide()

        self.cmb_device = QComboBox()
        self.cmb_device.currentIndexChanged.connect(self._on_device_changed)

        self.cmb_process = QComboBox()
        self.cmb_process.currentIndexChanged.connect(self._on_process_changed)

        self.file_row = QWidget()
        f_lay = QHBoxLayout(self.file_row)
        f_lay.setContentsMargins(0, 0, 0, 0)
        self.edit_file = QLineEdit()
        self.edit_file.editingFinished.connect(self._on_file_changed)
        btn_browse = QPushButton("Browse")
        btn_browse.clicked.connect(self._browse_wav)
        f_lay.addWidget(self.edit_file)
        f_lay.addWidget(btn_browse)

        self.slider_vol = QSlider(Qt.Orientation.Horizontal)
        self.slider_vol.setRange(0, 200)
        self.lbl_vol_val = QLabel("100%")
        self.slider_vol.valueChanged.connect(self._on_volume_slider)

        self.slider_gain = QSlider(Qt.Orientation.Horizontal)
        self.slider_gain.setRange(-600, 240)  # tenths of dB
        self.lbl_gain_val = QLabel("0.0 dB")
        self.slider_gain.valueChanged.connect(self._on_gain_slider)

        self.chk_mute = QCheckBox("Mute")
        self.chk_mute.stateChanged.connect(self._on_mute_changed)

        self.cmb_map_l = QComboBox()
        self.cmb_map_r = QComboBox()
        for idx, lbl in enumerate(("Left", "Right", "Mono Mix", "Silence")):
            self.cmb_map_l.addItem(lbl, idx)
            self.cmb_map_r.addItem(lbl, idx)
        self.cmb_map_l.currentIndexChanged.connect(self._on_channel_map_changed)
        self.cmb_map_r.currentIndexChanged.connect(self._on_channel_map_changed)

        self.eq_sliders: List[QSlider] = []
        self.eq_container = QFrame()
        eq_lay = QVBoxLayout(self.eq_container)
        eq_lay.setContentsMargins(0, 4, 0, 4)
        for idx, freq in enumerate(("100 Hz", "350 Hz", "1 kHz", "3.5 kHz", "10 kHz")):
            row = QHBoxLayout()
            row.addWidget(QLabel(freq))
            s = QSlider(Qt.Orientation.Horizontal)
            s.setRange(-120, 120)
            s.valueChanged.connect(lambda val, b=idx: self._on_eq_changed(b, val))
            self.eq_sliders.append(s)
            row.addWidget(s)
            eq_lay.addLayout(row)

        form.addRow("Node Type:", self.lbl_title)
        form_root.addWidget(self.lbl_missing_warn)

        self._row_device = (QLabel("Audio Device:"), self.cmb_device)
        form.addRow(*self._row_device)
        self._row_process = (QLabel("Application:"), self.cmb_process)
        form.addRow(*self._row_process)
        self._row_file = (QLabel("WAV Path:"), self.file_row)
        form.addRow(*self._row_file)

        vol_box = QHBoxLayout()
        vol_box.addWidget(self.slider_vol)
        vol_box.addWidget(self.lbl_vol_val)
        self._row_vol_widget = QWidget()
        self._row_vol_widget.setLayout(vol_box)
        self._row_vol = (QLabel("Volume:"), self._row_vol_widget)
        form.addRow(*self._row_vol)

        gain_box = QHBoxLayout()
        gain_box.addWidget(self.slider_gain)
        gain_box.addWidget(self.lbl_gain_val)
        self._row_gain_widget = QWidget()
        self._row_gain_widget.setLayout(gain_box)
        self._row_gain = (QLabel("Gain:"), self._row_gain_widget)
        form.addRow(*self._row_gain)

        form.addRow("", self.chk_mute)

        self._row_map_l = (QLabel("Left Out:"), self.cmb_map_l)
        self._row_map_r = (QLabel("Right Out:"), self.cmb_map_r)
        form.addRow(*self._row_map_l)
        form.addRow(*self._row_map_r)
        form_root.addWidget(self.eq_container)
        form_root.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(editor)
        self.stack.addWidget(scroll)

    def update_catalog(self, devices: List[DeviceInfo], processes: List[ProcessInfo]) -> None:
        self._devices = devices
        self._processes = processes
        if self._node is not None:
            self.set_node(self._node)

    def set_node(self, node: Optional[NodeModel]) -> None:
        self._node = node
        if node is None:
            self.stack.setCurrentIndex(0)
            return

        self._loading = True
        self.stack.setCurrentIndex(1)
        spec = node.spec()
        self.lbl_title.setText(f"#{node.id} {spec.title}")

        # Device selector visibility & contents
        needs_device = node.node_type in (
            NodeType.SYSTEM_CAPTURE,
            NodeType.MIC_INPUT,
            NodeType.VIRTUAL_INPUT,
            NodeType.SPEAKER_OUTPUT,
            NodeType.VIRTUAL_OUTPUT,
        )
        self._set_row_visible(self._row_device, needs_device)
        if needs_device:
            self._populate_device_combo(node)

        if needs_device and node.device_id and not node.device_available:
            self.lbl_missing_warn.setText(
                f"Saved device unavailable: {node.device_name or node.device_id}. "
                "Preserved original ID without switching devices."
            )
            self.lbl_missing_warn.show()
        else:
            warn = virtual_driver_missing_text(node.node_type, self._devices)
            if warn:
                self.lbl_missing_warn.setText(warn)
                self.lbl_missing_warn.show()
            else:
                self.lbl_missing_warn.hide()

        needs_proc = node.node_type == NodeType.APP_CAPTURE
        self._set_row_visible(self._row_process, needs_proc)
        if needs_proc:
            self._populate_process_combo(node)

        needs_file = node.node_type == NodeType.FILE_RECORDING
        self._set_row_visible(self._row_file, needs_file)
        if needs_file:
            self.edit_file.setText(node.file_path)

        show_vol = node.node_type in (
            NodeType.VOLUME,
            NodeType.MIXER,
            NodeType.SPEAKER_OUTPUT,
            NodeType.VIRTUAL_OUTPUT,
        )
        self._set_row_visible(self._row_vol, show_vol)
        self.slider_vol.setValue(int(round(node.volume * 100)))
        self.lbl_vol_val.setText(f"{int(round(node.volume * 100))}%")

        show_gain = node.node_type == NodeType.GAIN
        self._set_row_visible(self._row_gain, show_gain)
        self.slider_gain.setValue(int(round(node.gain_db * 10)))
        self.lbl_gain_val.setText(f"{node.gain_db:+.1f} dB")

        show_mute = node.node_type in (
            NodeType.APP_CAPTURE,
            NodeType.SYSTEM_CAPTURE,
            NodeType.MIC_INPUT,
            NodeType.VIRTUAL_INPUT,
            NodeType.VOLUME,
            NodeType.GAIN,
            NodeType.MUTE,
            NodeType.SPEAKER_OUTPUT,
            NodeType.VIRTUAL_OUTPUT,
        )
        self.chk_mute.setVisible(show_mute)
        self.chk_mute.setChecked(node.muted)

        show_map = node.node_type == NodeType.CHANNEL_MAP
        self._set_row_visible(self._row_map_l, show_map)
        self._set_row_visible(self._row_map_r, show_map)
        self.cmb_map_l.setCurrentIndex(max(0, min(3, node.channel_map[0])))
        self.cmb_map_r.setCurrentIndex(max(0, min(3, node.channel_map[1])))

        self.eq_container.setVisible(node.node_type == NodeType.EQUALIZER)
        for i, s in enumerate(self.eq_sliders):
            s.setValue(int(round(node.eq_gains_db[i] * 10)))

        self._loading = False

    def _set_row_visible(self, row: tuple, visible: bool) -> None:
        row[0].setVisible(visible)
        row[1].setVisible(visible)

    def _populate_device_combo(self, node: NodeModel) -> None:
        self.cmb_device.clear()
        for label, data in build_device_combo_entries(node, self._devices):
            self.cmb_device.addItem(label, data)

        idx = 0
        for i in range(self.cmb_device.count()):
            data = self.cmb_device.itemData(i)
            if data and data[0] == node.device_id:
                idx = i
                break
        self.cmb_device.setCurrentIndex(idx)

    def _populate_process_combo(self, node: NodeModel) -> None:
        self.cmb_process.clear()
        for label, data in build_process_combo_entries(node, self._processes):
            self.cmb_process.addItem(label, data)
        idx = 0
        for i in range(self.cmb_process.count()):
            data = self.cmb_process.itemData(i)
            if data and data[0] == node.pid:
                idx = i
                break
        self.cmb_process.setCurrentIndex(idx)

    def _on_device_changed(self) -> None:
        if self._loading or not self._node:
            return
        data = self.cmb_device.currentData()
        if data:
            self._node.device_id = data[0]
            self._node.device_name = data[1]
            self._node.device_available = True
            self.lbl_missing_warn.hide()
            self.graph_rebuild_needed.emit()

    def _on_process_changed(self) -> None:
        if self._loading or not self._node:
            return
        data = self.cmb_process.currentData()
        if data:
            self._node.pid = int(data[0])
            self._node.process_name = str(data[1])
            self.graph_rebuild_needed.emit()

    def _browse_wav(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Select Output WAV File", "", "WAV Files (*.wav)")
        if path:
            self.edit_file.setText(path)
            self._on_file_changed()

    def _on_file_changed(self) -> None:
        if self._loading or not self._node:
            return
        self._node.file_path = self.edit_file.text().strip()
        self.graph_rebuild_needed.emit()

    def _on_volume_slider(self, val: int) -> None:
        if self._loading or not self._node:
            return
        v = val / 100.0
        self._node.volume = v
        self.lbl_vol_val.setText(f"{val}%")
        self.float_param_changed.emit(self._node.id, "volume", v)

    def _on_gain_slider(self, val: int) -> None:
        if self._loading or not self._node:
            return
        db = val / 10.0
        self._node.gain_db = db
        self.lbl_gain_val.setText(f"{db:+.1f} dB")
        self.float_param_changed.emit(self._node.id, "gain_db", db)

    def _on_mute_changed(self) -> None:
        if self._loading or not self._node:
            return
        self._node.muted = self.chk_mute.isChecked()
        self.int_param_changed.emit(self._node.id, "muted", 1 if self._node.muted else 0)

    def _on_channel_map_changed(self) -> None:
        if self._loading or not self._node:
            return
        l_idx = int(self.cmb_map_l.currentData() or 0)
        r_idx = int(self.cmb_map_r.currentData() or 1)
        self._node.channel_map = [l_idx, r_idx]
        self.int_param_changed.emit(self._node.id, "channel_map_l", l_idx)
        self.int_param_changed.emit(self._node.id, "channel_map_r", r_idx)

    def _on_eq_changed(self, band: int, tenths_db: int) -> None:
        if self._loading or not self._node:
            return
        db = tenths_db / 10.0
        self._node.eq_gains_db[band] = db
        self.float_param_changed.emit(self._node.id, f"eq_band_{band}", db)


class BottomTelemetryPanel(QWidget):
    """ComfyUI-style bottom terminal dock: Meters, Active Connections, Engine & Devices, Logs."""

    close_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("BottomTerminal")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("BottomTerminalTabs")
        layout.addWidget(self.tabs)

        corner = QWidget(self)
        corner_lay = QHBoxLayout(corner)
        corner_lay.setContentsMargins(0, 2, 4, 2)
        corner_lay.setSpacing(4)
        self.btn_close = QToolButton(corner)
        self.btn_close.setObjectName("TerminalCloseButton")
        self.btn_close.setText("×")
        self.btn_close.setToolTip("Close Terminal (Ctrl+`)")
        self.btn_close.setAccessibleName("Close Terminal")
        self.btn_close.clicked.connect(self.close_requested.emit)
        corner_lay.addWidget(self.btn_close)
        self.tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)

        # Tab 1: Audio Level Meters
        self.meter_table = QTableWidget(0, 4)
        self.meter_table.setHorizontalHeaderLabels(["Node", "Left Peak", "Right Peak", "XRUNs"])
        self.meter_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tabs.addTab(self.meter_table, "Meters")

        # Tab 2: Active Connections
        self.conn_table = QTableWidget(0, 4)
        self.conn_table.setHorizontalHeaderLabels(["Source Node", "Out Port", "Dest Node", "In Port"])
        self.conn_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tabs.addTab(self.conn_table, "Active Connections")

        # Tab 3: Engine & Device Status
        status_w = QWidget()
        s_lay = QVBoxLayout(status_w)
        self.lbl_engine_summary = QLabel("Engine Stopped")
        self.device_status_table = QTableWidget(0, 4)
        self.device_status_table.setHorizontalHeaderLabels(["Device Name", "Direction", "Rate", "Kind"])
        self.device_status_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        s_lay.addWidget(self.lbl_engine_summary)
        s_lay.addWidget(self.device_status_table)
        self.tabs.addTab(status_w, "Engine && Devices")

        # Tab 4: Logs
        self.log_console = QPlainTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setMaximumBlockCount(400)
        self.tabs.addTab(self.log_console, "Logs")

    def append_log(self, level: str, text: str) -> None:
        self.log_console.appendPlainText(f"[{level}] {text}")

    def push_native_logs(self, entries: List[LogEntry]) -> None:
        level_map = {0: "INFO", 1: "WARN", 2: "ERROR"}
        for e in entries:
            self.append_log(level_map.get(e.level, "INFO"), f"(Node {e.node_id}) {e.message}")

    def update_connections(self, nodes: Dict[int, NodeModel], connections: List[ConnectionModel]) -> None:
        self.conn_table.setRowCount(len(connections))
        for row, c in enumerate(connections):
            sn = nodes.get(c.src_node)
            dn = nodes.get(c.dst_node)
            s_label = f"#{c.src_node} {sn.display_title()}" if sn else str(c.src_node)
            d_label = f"#{c.dst_node} {dn.display_title()}" if dn else str(c.dst_node)
            self.conn_table.setItem(row, 0, QTableWidgetItem(s_label))
            self.conn_table.setItem(row, 1, QTableWidgetItem(str(c.src_port + 1)))
            self.conn_table.setItem(row, 2, QTableWidgetItem(d_label))
            self.conn_table.setItem(row, 3, QTableWidgetItem(str(c.dst_port + 1)))

    def update_devices_table(self, devices: List[DeviceInfo]) -> None:
        self.device_status_table.setRowCount(len(devices))
        for row, d in enumerate(devices):
            self.device_status_table.setItem(row, 0, QTableWidgetItem(d.name))
            self.device_status_table.setItem(
                row, 1, QTableWidgetItem("Capture" if d.flow == DataFlow.CAPTURE else "Render")
            )
            self.device_status_table.setItem(row, 2, QTableWidgetItem(f"{d.sample_rate} Hz / {d.channels}ch"))
            self.device_status_table.setItem(row, 3, QTableWidgetItem("Virtual" if d.is_virtual else "Physical"))

    def update_telemetry(self, stats: EngineStats, nodes: Dict[int, NodeModel], meters: Dict[int, NodeMeter]) -> None:
        state = "RUNNING" if stats.running else "STOPPED"
        self.lbl_engine_summary.setText(
            f"State: {state} | {stats.sample_rate} Hz / {stats.block_frames} frames | "
            f"Nodes: {stats.active_nodes} | Links: {stats.active_connections} | "
            f"CPU: {stats.cpu_load_percent:.2f}% | XRUNs: {stats.total_xruns} | "
            f"Virtual Endpoints: {stats.virtual_endpoints}"
        )

        ordered = sorted(nodes.values(), key=lambda n: n.id)
        if self.meter_table.rowCount() != len(ordered):
            self.meter_table.setRowCount(len(ordered))
            for row, n in enumerate(ordered):
                self.meter_table.setItem(row, 0, QTableWidgetItem(f"#{n.id} {n.display_title()}"))
                for col in (1, 2):
                    bar = QProgressBar()
                    bar.setRange(0, 100)
                    self.meter_table.setCellWidget(row, col, bar)
                self.meter_table.setItem(row, 3, QTableWidgetItem("0"))

        for row, n in enumerate(ordered):
            m = meters.get(n.id)
            name_item = self.meter_table.item(row, 0)
            desired_name = f"#{n.id} {n.display_title()}"
            if name_item is None or name_item.text() != desired_name:
                self.meter_table.setItem(row, 0, QTableWidgetItem(desired_name))
            for col, val in ((1, m.peak_left if m else 0.0), (2, m.peak_right if m else 0.0)):
                bar = self.meter_table.cellWidget(row, col)
                if not isinstance(bar, QProgressBar):
                    bar = QProgressBar()
                    bar.setRange(0, 100)
                    self.meter_table.setCellWidget(row, col, bar)
                pct = int(min(100, max(0, val * 100)))
                if bar.value() != pct:
                    bar.setValue(pct)
                    bar.setFormat(f"{pct}%")
            xrun_item = self.meter_table.item(row, 3)
            xrun_text = str(m.xruns if m else 0)
            if xrun_item is None:
                self.meter_table.setItem(row, 3, QTableWidgetItem(xrun_text))
            elif xrun_item.text() != xrun_text:
                xrun_item.setText(xrun_text)

