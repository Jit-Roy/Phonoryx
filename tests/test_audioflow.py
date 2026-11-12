"""Comprehensive integration tests for AudioFlow Python bridge, models, persistence, and PySide6 studio."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from audioflow.engine_bridge import AudioFlowEngine, DataFlow, DeviceInfo, DeviceRoleHint, NodeType
from audioflow.models.graph_model import NODE_SPECS, NodeModel, ProjectModel
from audioflow.ui.canvas import HEADER_HEIGHT, NODE_DRAG_MIME_TYPE, NODE_WIDTH
from audioflow.ui.main_window import AudioFlowMainWindow
from audioflow.ui.panels import DraggablePaletteList, InspectorPanel


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_native_engine_discovery_and_lifecycle():
    with AudioFlowEngine(sample_rate=48000, block_frames=256) as eng:
        devices = eng.enumerate_devices()
        assert len(devices) > 0, "Expected real Windows MMDevices"
        assert any(d.flow in (DataFlow.RENDER, DataFlow.CAPTURE) for d in devices)

        procs = eng.enumerate_processes()
        assert len(procs) > 0, "Expected running Windows processes"
        assert any(p.pid > 0 and p.exe_name for p in procs)

        assert eng.start()
        time.sleep(0.08)
        stats = eng.get_engine_stats()
        assert stats.running is True
        assert stats.sample_rate == 48000
        assert stats.processed_blocks > 0
        assert stats.virtual_endpoints == sum(1 for d in devices if d.is_virtual)
        eng.stop()
        assert eng.get_engine_stats().running is False


def test_section5_multi_branch_graph_sync_and_cycle_rejection():
    proj = ProjectModel()
    chrome = proj.add_node(NodeType.APP_CAPTURE, x=0, y=100, pid=99999, process_name="chrome.exe")
    splitter = proj.add_node(NodeType.SPLITTER, x=260, y=100)
    vol1 = proj.add_node(NodeType.VOLUME, x=520, y=0, volume=0.75)
    hp = proj.add_node(NodeType.SPEAKER_OUTPUT, x=780, y=0, device_id="test://hp")
    eq2 = proj.add_node(NodeType.EQUALIZER, x=520, y=140, eq_gains_db=[3.0, 0.0, -2.0, 1.5, 4.0])
    vmic1 = proj.add_node(NodeType.VIRTUAL_OUTPUT, x=780, y=140, device_id="test://vmic1")
    mic = proj.add_node(NodeType.MIC_INPUT, x=260, y=280, device_id="test://mic")
    mixer = proj.add_node(NodeType.MIXER, x=520, y=280)
    vmic2 = proj.add_node(NodeType.VIRTUAL_OUTPUT, x=780, y=280, device_id="test://vmic2")

    assert proj.add_connection(chrome.id, 0, splitter.id, 0)[0]
    assert proj.add_connection(splitter.id, 0, vol1.id, 0)[0]
    assert proj.add_connection(vol1.id, 0, hp.id, 0)[0]
    assert proj.add_connection(splitter.id, 1, eq2.id, 0)[0]
    assert proj.add_connection(eq2.id, 0, vmic1.id, 0)[0]
    assert proj.add_connection(splitter.id, 2, mixer.id, 0)[0]
    assert proj.add_connection(mic.id, 0, mixer.id, 1)[0]
    assert proj.add_connection(mixer.id, 0, vmic2.id, 0)[0]

    # ComfyUI-style 1-to-many output: any node's single output can drive multiple downstream inputs
    extra_hp = proj.add_node(NodeType.SPEAKER_OUTPUT, x=780, y=400, device_id="test://spk")
    ok, msg = proj.add_connection(vol1.id, 0, extra_hp.id, 0)
    assert ok, f"Expected non-splitter multi-output fan-out to succeed: {msg}"

    # Non-mixer cannot accept multiple inputs
    mic2 = proj.add_node(NodeType.MIC_INPUT, x=260, y=380, device_id="test://mic2")
    ok, msg = proj.add_connection(mic2.id, 0, vol1.id, 0)
    assert not ok and "Mixer" in msg

    # Cycle rejection
    ok, msg = proj.add_connection(splitter.id, 3, chrome.id, 0)
    assert not ok

    with AudioFlowEngine(48000, 128) as eng:
        assert eng.start()
        ok, err = eng.apply_graph(proj.to_engine_payload())
        assert ok, f"Engine rejected valid multi-branch graph: {err}"

        assert eng.set_float_param(vol1.id, "volume", 0.5)
        assert eng.set_int_param(vol1.id, "muted", 1)
        assert eng.set_float_param(eq2.id, "eq_band_2", -4.5)

        time.sleep(0.05)
        meters = eng.get_node_meters()
        assert splitter.id in meters
        assert mixer.id in meters

        # Native engine must also independently reject cyclic JSON
        bad_payload = {
            "nodes": [
                {"id": 1, "type": int(NodeType.VOLUME)},
                {"id": 2, "type": int(NodeType.GAIN)},
            ],
            "connections": [
                {"src_node": 1, "src_port": 0, "dst_node": 2, "dst_port": 0},
                {"src_node": 2, "src_port": 0, "dst_node": 1, "dst_port": 0},
            ],
        }
        ok_bad, err_bad = eng.apply_graph(bad_payload)
        assert not ok_bad
        assert "Cycle" in err_bad


def test_project_save_load_preserves_unavailable_device_without_fallback(tmp_path: Path):
    proj_path = tmp_path / "studio_route.aflow"
    missing_id = "{0.0.0.00000000}.{deadbeef-1234-5678-90ab-cdef12345678}"

    proj = ProjectModel()
    src = proj.add_node(
        NodeType.MIC_INPUT,
        x=40,
        y=60,
        device_id=missing_id,
        device_name="Unplugged USB Condenser",
    )
    out = proj.add_node(
        NodeType.SPEAKER_OUTPUT,
        x=320,
        y=60,
        device_id="{0.0.0.00000000}.{present-device}",
        device_name="Studio Monitors",
    )
    assert proj.add_connection(src.id, 0, out.id, 0)[0]
    proj.save_file(proj_path)

    reloaded = ProjectModel()
    missing = reloaded.load_file(
        proj_path,
        available_device_ids={"{0.0.0.00000000}.{present-device}", "{other-mic}"},
    )
    assert len(missing) == 1
    assert missing[0].id == src.id
    # Must preserve original device_id/device_name and mark unavailable
    assert reloaded.nodes[src.id].device_id == missing_id
    assert reloaded.nodes[src.id].device_name == "Unplugged USB Condenser"
    assert reloaded.nodes[src.id].device_available is False
    assert reloaded.nodes[out.id].device_available is True


def test_all_required_node_specs_present():
    # 4 source + 9 processing + 3 output = 16 required node types
    assert len(NODE_SPECS) == 16
    for nt in NodeType:
        assert nt in NODE_SPECS

    # Legacy IDs (4 Line In, 202 Headphone, 204 Virtual Mic) and removed meter (106) migrate cleanly
    migrated = ProjectModel()
    migrated.load_project_dict(
        {
            "nodes": [
                {"id": 1, "type": 202, "device_id": "d1"},
                {"id": 2, "type": 204, "device_id": "d2"},
                {"id": 3, "type": 4, "device_id": "m1"},
                {"id": 4, "type": 106},
            ],
            "connections": [
                {"src_node": 3, "src_port": 0, "dst_node": 4, "dst_port": 0},
                {"src_node": 4, "src_port": 0, "dst_node": 1, "dst_port": 0},
            ],
        }
    )
    assert migrated.nodes[1].node_type == NodeType.SPEAKER_OUTPUT
    assert migrated.nodes[2].node_type == NodeType.VIRTUAL_OUTPUT
    assert migrated.nodes[3].node_type == NodeType.MIC_INPUT
    assert 4 not in migrated.nodes
    assert len(migrated.connections) == 1
    assert (migrated.connections[0].src_node, migrated.connections[0].dst_node) == (3, 1)


def test_pyqt_main_window_canvas_undo_grouping_and_persistence(qapp, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)

    win = AudioFlowMainWindow(
        auto_start=True,
        cache_path=tmp_path / "main_window_test_cache.json",
        restore_cache=False,
    )
    win.show()
    qapp.processEvents()
    try:
        canvas = win.canvas
        n_sys = canvas.add_node_action(NodeType.SYSTEM_CAPTURE)
        n_eq = canvas.add_node_action(NodeType.EQUALIZER)
        n_rec = canvas.add_node_action(
            NodeType.FILE_RECORDING,
            file_path=str(tmp_path / "ui_test.wav"),
        )
        assert len(win.project.nodes) == 3

        assert canvas.connect_ports_action(n_sys.id, 0, n_eq.id, 0)
        assert canvas.connect_ports_action(n_eq.id, 0, n_rec.id, 0)
        assert len(win.project.connections) == 2

        # Select 2 nodes and group them
        canvas.node_items[n_sys.id].setSelected(True)
        canvas.node_items[n_eq.id].setSelected(True)
        grp = canvas.group_selected_action("Capture Chain")
        assert grp is not None
        assert len(win.project.groups) == 1

        # Undo group, then redo group
        canvas.undo_stack.undo()
        assert len(win.project.groups) == 0
        canvas.undo_stack.redo()
        assert len(win.project.groups) == 1

        # Delete node & undo restores node and its wires
        canvas.scene_obj.clearSelection()
        canvas.node_items[n_eq.id].setSelected(True)
        canvas.delete_selected_action()
        assert n_eq.id not in win.project.nodes
        assert len(win.project.connections) == 0

        canvas.undo_stack.undo()
        assert n_eq.id in win.project.nodes
        assert len(win.project.connections) == 2

        # ComfyUI inline widgets live directly inside canvas nodes (no right sidebar dock)
        assert not hasattr(win, "right_panel")
        eq_item = canvas.node_items[n_eq.id]
        assert len(eq_item.eq_sliders) == 5
        rec_item = canvas.node_items[n_rec.id]
        assert rec_item.edit_file is not None
        assert rec_item.chk_mute is not None

        # Inject a nonexistent device ID and save/load through MainWindow
        n_sys.device_id = "{0.0.0.00000000}.{offline-test-guid}"
        n_sys.device_name = "Missing DAC"
        save_file = tmp_path / "window_test.aflow"
        win.project.save_file(save_file)
        win.load_project_path(save_file)
        loaded_src = win.project.nodes[n_sys.id]
        assert loaded_src.device_id == "{0.0.0.00000000}.{offline-test-guid}"
        assert loaded_src.device_available is False

        # Trigger full paint pass across canvas items (nodes, ports, wires, groups)
        pixmap = canvas.grab()
        assert not pixmap.isNull()
        qapp.processEvents()
    finally:
        win.close()


def _drag_canvas(canvas, start_scene: QPointF, end_scene: QPointF) -> None:
    vp = canvas.viewport()
    p1 = canvas.mapFromScene(start_scene)
    p2 = canvas.mapFromScene(end_scene)
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p1)
    mid = QPoint((p1.x() + p2.x()) // 2, (p1.y() + p2.y()) // 2)
    QTest.mouseMove(vp, mid)
    QTest.mouseMove(vp, p2)
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p2)


def test_comfyui_wire_detach_move_replace_and_fanout(qapp, tmp_path: Path):
    win = AudioFlowMainWindow(
        auto_start=False,
        cache_path=tmp_path / "wire_cache.json",
        restore_cache=False,
    )
    win.show()
    qapp.processEvents()
    try:
        canvas = win.canvas
        src = canvas.add_node_action(NodeType.SYSTEM_CAPTURE, scene_pos=QPointF(60, 120))
        vol = canvas.add_node_action(NodeType.VOLUME, scene_pos=QPointF(380, 40))
        eq = canvas.add_node_action(NodeType.EQUALIZER, scene_pos=QPointF(380, 220))
        rec = canvas.add_node_action(
            NodeType.FILE_RECORDING,
            scene_pos=QPointF(700, 120),
            file_path=str(tmp_path / "fanout.wav"),
        )
        canvas.centerOn(380, 140)
        qapp.processEvents()

        src_out = canvas.node_items[src.id].output_ports[0].center_scene_pos()
        vol_in = canvas.node_items[vol.id].input_ports[0].center_scene_pos()
        vol_out = canvas.node_items[vol.id].output_ports[0].center_scene_pos()
        eq_in = canvas.node_items[eq.id].input_ports[0].center_scene_pos()
        eq_out = canvas.node_items[eq.id].output_ports[0].center_scene_pos()
        rec_in = canvas.node_items[rec.id].input_ports[0].center_scene_pos()

        # 1. Single node output connects cleanly to multiple downstream nodes (fan-out)
        _drag_canvas(canvas, src_out, vol_in)
        _drag_canvas(canvas, src_out, eq_in)
        assert len(win.project.connections) == 2

        # 2. Detach wire by dragging from connected input port to empty canvas space
        empty_spot = QPointF(200, 380)
        _drag_canvas(canvas, vol_in, empty_spot)
        assert len(win.project.connections) == 1
        assert all(c.dst_node != vol.id for c in win.project.connections)

        # Undo restores detached wire
        canvas.undo_stack.undo()
        assert len(win.project.connections) == 2

        # 3. Dragging wire off connected input port onto another node card moves the wire
        rec_card_center = QPointF(rec.x + NODE_WIDTH * 0.5, rec.y + HEADER_HEIGHT + 20)
        _drag_canvas(canvas, vol_in, rec_card_center)
        assert any(c.src_node == src.id and c.dst_node == rec.id for c in win.project.connections)
        assert all(c.dst_node != vol.id for c in win.project.connections)

        # 4. Dropping another wire onto an already-occupied single-input port replaces cleanly
        _drag_canvas(canvas, vol_out, eq_in)  # no-op check; eq already has src->eq
        _drag_canvas(canvas, eq_out, rec_in)
        rec_inputs = [c for c in win.project.connections if c.dst_node == rec.id]
        assert len(rec_inputs) == 1
        assert rec_inputs[0].src_node == eq.id
    finally:
        win.close()


def test_sidebar_drag_drop_creates_node_at_cursor(qapp, tmp_path: Path):
    win = AudioFlowMainWindow(
        auto_start=False,
        cache_path=tmp_path / "dnd_cache.json",
        restore_cache=False,
    )
    win.show()
    qapp.processEvents()
    try:
        assert isinstance(win.left_panel.palette_list, DraggablePaletteList)
        assert isinstance(win.left_panel.device_list, DraggablePaletteList)
        assert isinstance(win.left_panel.proc_list, DraggablePaletteList)

        # Verify palette item carries structured drag payload
        found_payload = None
        for idx in range(win.left_panel.palette_list.count()):
            data = win.left_panel.palette_list.item(idx).data(DraggablePaletteList.NODE_PAYLOAD_ROLE)
            if isinstance(data, dict) and data.get("node_type") == int(NodeType.GAIN):
                found_payload = data
                break
        assert found_payload is not None

        canvas = win.canvas
        drop_view_pt = QPoint(240, 180)
        expected_scene = canvas.mapToScene(drop_view_pt)

        mime = QMimeData()
        mime.setData(NODE_DRAG_MIME_TYPE, json.dumps(found_payload).encode("utf-8"))
        ev = QDropEvent(
            QPointF(drop_view_pt),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        canvas.dropEvent(ev)
        assert ev.isAccepted()
        assert len(win.project.nodes) == 1
        node = next(iter(win.project.nodes.values()))
        assert node.node_type == NodeType.GAIN
        assert abs(node.x - (expected_scene.x() - NODE_WIDTH / 2.0)) < 2.0
        assert abs(node.y - (expected_scene.y() - HEADER_HEIGHT / 2.0)) < 2.0
    finally:
        win.close()


def test_workspace_cache_persists_across_restarts(qapp, tmp_path: Path):
    cache_file = tmp_path / "nested" / "workspace_cache.json"

    win1 = AudioFlowMainWindow(auto_start=False, cache_path=cache_file, restore_cache=True)
    win1.show()
    qapp.processEvents()
    try:
        n1 = win1.canvas.add_node_action(NodeType.SYSTEM_CAPTURE, scene_pos=QPointF(40, 80))
        n2 = win1.canvas.add_node_action(NodeType.VOLUME, scene_pos=QPointF(340, 80), volume=0.42)
        assert win1.canvas.connect_ports_action(n1.id, 0, n2.id, 0)
        win1.canvas.node_items[n1.id].setSelected(True)
        win1.canvas.node_items[n2.id].setSelected(True)
        grp = win1.canvas.group_selected_action("Saved Bus")
        assert grp is not None
        win1.canvas.node_items[n2.id].setPos(410, 125)
        n2.x, n2.y = 410.0, 125.0
        n1_id, n2_id, grp_id = n1.id, n2.id, grp.id
    finally:
        win1.close()

    assert cache_file.is_file()

    win2 = AudioFlowMainWindow(auto_start=False, cache_path=cache_file, restore_cache=True)
    win2.show()
    qapp.processEvents()
    try:
        assert set(win2.project.nodes.keys()) == {n1_id, n2_id}
        assert win2.project.nodes[n2_id].x == pytest.approx(410.0)
        assert win2.project.nodes[n2_id].y == pytest.approx(125.0)
        assert win2.project.nodes[n2_id].volume == pytest.approx(0.42)
        assert len(win2.project.connections) == 1
        conn = win2.project.connections[0]
        assert (conn.src_node, conn.dst_node) == (n1_id, n2_id)
        assert grp_id in win2.project.groups
        assert win2.project.groups[grp_id].title == "Saved Bus"
        assert set(win2.canvas.node_items.keys()) == {n1_id, n2_id}
        assert len(win2.canvas.conn_items) == 1
        assert grp_id in win2.canvas.group_items
    finally:
        win2.close()

    # Opening with restore_cache=False must not wipe an existing cache file on close
    before_bytes = cache_file.read_text(encoding="utf-8")
    win3 = AudioFlowMainWindow(auto_start=False, cache_path=cache_file, restore_cache=False)
    win3.show()
    qapp.processEvents()
    win3.close()
    assert cache_file.read_text(encoding="utf-8") == before_bytes


def test_virtual_driver_option2_inspector_and_device_prioritization(qapp):
    panel = InspectorPanel()
    hw_spk = DeviceInfo(
        id="hw-spk",
        name="Realtek Speakers",
        flow=DataFlow.RENDER,
        role_hint=DeviceRoleHint.SPEAKERS,
        state=1,
        sample_rate=48000,
        channels=2,
        is_default=True,
        is_virtual=False,
    )
    hw_mic = DeviceInfo(
        id="hw-mic",
        name="Realtek Microphone",
        flow=DataFlow.CAPTURE,
        role_hint=DeviceRoleHint.MICROPHONE,
        state=1,
        sample_rate=48000,
        channels=2,
        is_default=True,
        is_virtual=False,
    )
    vad_spk = DeviceInfo(
        id="vad-spk",
        name="Speakers (Virtual Audio Driver)",
        flow=DataFlow.RENDER,
        role_hint=DeviceRoleHint.VIRTUAL,
        state=1,
        sample_rate=48000,
        channels=2,
        is_default=False,
        is_virtual=True,
    )
    vad_mic = DeviceInfo(
        id="vad-mic",
        name="Microphone (Virtual Audio Driver)",
        flow=DataFlow.CAPTURE,
        role_hint=DeviceRoleHint.VIRTUAL,
        state=1,
        sample_rate=48000,
        channels=2,
        is_default=False,
        is_virtual=True,
    )

    # 1. Virtual Device Output / Input show only virtual endpoints; Physical Device Output / Mic show only physical
    panel.update_catalog([hw_spk, hw_mic, vad_spk, vad_mic], [])
    vmic_out = NodeModel(id=1, node_type=NodeType.VIRTUAL_OUTPUT)
    panel.set_node(vmic_out)
    combo_ids = [panel.cmb_device.itemData(i)[0] for i in range(panel.cmb_device.count())]
    assert not any(cid.startswith("audioflow://") for cid in combo_ids)
    assert combo_ids == ["", "vad-spk"]
    assert panel.lbl_missing_warn.isHidden()

    phys_out = NodeModel(id=3, node_type=NodeType.SPEAKER_OUTPUT)
    panel.set_node(phys_out)
    phys_ids = [panel.cmb_device.itemData(i)[0] for i in range(panel.cmb_device.count())]
    assert phys_ids == ["", "hw-spk"]

    vin = NodeModel(id=2, node_type=NodeType.VIRTUAL_INPUT)
    panel.set_node(vin)
    in_ids = [panel.cmb_device.itemData(i)[0] for i in range(panel.cmb_device.count())]
    assert in_ids == ["", "vad-mic"]

    mic_in = NodeModel(id=4, node_type=NodeType.MIC_INPUT)
    panel.set_node(mic_in)
    mic_ids = [panel.cmb_device.itemData(i)[0] for i in range(panel.cmb_device.count())]
    assert mic_ids == ["", "hw-mic"]

    # 2. Without any Windows virtual driver installed: show setup hint for VirtualDrivers/Virtual-Audio-Driver
    panel.update_catalog([hw_spk, hw_mic], [])
    panel.set_node(vmic_out)
    assert [panel.cmb_device.itemData(i)[0] for i in range(panel.cmb_device.count())] == [""]
    assert not panel.lbl_missing_warn.isHidden()
    assert "VirtualDrivers/Virtual-Audio-Driver" in panel.lbl_missing_warn.text()


def test_comfyui_left_drag_pan_and_hidden_scrollbars(qapp, tmp_path: Path):
    win = AudioFlowMainWindow(
        auto_start=False,
        cache_path=tmp_path / "pan_cache.json",
        restore_cache=False,
    )
    win.show()
    qapp.processEvents()
    try:
        canvas = win.canvas
        assert canvas.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        assert canvas.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        assert not canvas.horizontalScrollBar().isVisible()
        assert not canvas.verticalScrollBar().isVisible()

        node = canvas.add_node_action(NodeType.VOLUME, scene_pos=QPointF(100, 100))
        canvas.node_items[node.id].setSelected(True)
        canvas.centerOn(0, 0)
        qapp.processEvents()

        before = canvas.mapToScene(canvas.viewport().rect().center())
        vp = canvas.viewport()
        start_pt = QPoint(40, 40)
        end_pt = QPoint(180, 130)
        QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start_pt)
        QTest.mouseMove(vp, end_pt)
        QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end_pt)
        qapp.processEvents()

        after = canvas.mapToScene(canvas.viewport().rect().center())
        # Dragging right & down shifts viewport scene center left & up
        assert after.x() < before.x() - 80
        assert after.y() < before.y() - 50
        # Clicking empty canvas also clears node selection without moving the node
        assert not canvas.node_items[node.id].isSelected()
        assert node.x == pytest.approx(100.0)
        assert node.y == pytest.approx(100.0)
    finally:
        win.close()


def test_comfyui_inline_node_widgets_selection_tabs_and_terminal(qapp, tmp_path: Path):
    win = AudioFlowMainWindow(
        auto_start=False,
        cache_path=tmp_path / "comfyui_cache.json",
        restore_cache=False,
    )
    win.show()
    qapp.processEvents()
    try:
        canvas = win.canvas
        n_vol = canvas.add_node_action(NodeType.VOLUME, scene_pos=QPointF(80, 100))
        n_eq = canvas.add_node_action(NodeType.EQUALIZER, scene_pos=QPointF(380, 100))
        n_vin = canvas.add_node_action(NodeType.VIRTUAL_INPUT, scene_pos=QPointF(80, 320))

        # 1. Inline widgets directly on canvas node cards sync to model
        vol_item = canvas.node_items[n_vol.id]
        assert vol_item.slider_vol is not None
        assert vol_item.chk_mute is not None
        vol_item.slider_vol.setValue(145)
        vol_item.chk_mute.setChecked(True)
        assert n_vol.volume == pytest.approx(1.45)
        assert n_vol.muted is True

        # Device combo inline filtering matches Physical vs Virtual rules
        hw_mic = DeviceInfo("hw-mic", "Realtek Mic", DataFlow.CAPTURE, DeviceRoleHint.MICROPHONE, 1, 48000, 2, True, False)
        vad_mic = DeviceInfo("vad-mic", "Virtual Mic", DataFlow.CAPTURE, DeviceRoleHint.VIRTUAL, 1, 48000, 2, False, True)
        canvas.update_catalog([hw_mic, vad_mic], [])
        vin_item = canvas.node_items[n_vin.id]
        assert vin_item.cmb_device is not None
        assert [vin_item.cmb_device.itemData(i)[0] for i in range(vin_item.cmb_device.count())] == ["", "vad-mic"]

        # 2. Dirty '*' indicator appears on active workflow tab when edited, cleared by Ctrl+S
        assert win.workflow_tabs.tabText(0).endswith("*")
        save_path = tmp_path / "studio_mix.aflow"
        win.project_path = save_path
        win._tabs[0].file_path = save_path
        QTest.keyClick(canvas, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
        qapp.processEvents()
        assert save_path.is_file()
        assert win.workflow_tabs.tabText(0) == "studio_mix"

        # 3. Ctrl+A selects all nodes cleanly
        canvas.scene_obj.clearSelection()
        assert len(canvas.scene_obj.selectedItems()) == 0
        QTest.keyClick(canvas, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        qapp.processEvents()
        assert all(canvas.node_items[nid].isSelected() for nid in (n_vol.id, n_eq.id, n_vin.id))

        # 4. Ctrl+Click selects/toggles nodes individually one by one
        canvas.scene_obj.clearSelection()
        canvas.centerOn(230, 200)
        qapp.processEvents()
        vp = canvas.viewport()
        pt_vol = canvas.mapFromScene(QPointF(n_vol.x + 120, n_vol.y + 12))
        pt_eq = canvas.mapFromScene(QPointF(n_eq.x + 120, n_eq.y + 12))
        QTest.mouseClick(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, pt_vol)
        QTest.mouseClick(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, pt_eq)
        assert canvas.node_items[n_vol.id].isSelected()
        assert canvas.node_items[n_eq.id].isSelected()
        assert not canvas.node_items[n_vin.id].isSelected()
        # Ctrl+Click again toggles off without clearing other node
        QTest.mouseClick(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, pt_vol)
        assert not canvas.node_items[n_vol.id].isSelected()
        assert canvas.node_items[n_eq.id].isSelected()

        # 5. '+' new workflow tab creates fresh clean canvas and keeps previous tab intact
        assert win.workflow_tabs.count() == 1
        win.btn_new_tab.click()
        qapp.processEvents()
        assert win.workflow_tabs.count() == 2
        assert win.workflow_tabs.currentIndex() == 1
        assert len(win.project.nodes) == 0
        win.workflow_tabs.setCurrentIndex(0)
        qapp.processEvents()
        assert len(win.project.nodes) == 3

        # 6. Bottom Terminal ('Meters, Status & Logs') closes with '×' and reopens cleanly
        assert win.bottom_dock.isVisible()
        win.bottom_panel.btn_close.click()
        qapp.processEvents()
        assert not win.bottom_dock.isVisible()
        assert not win.btn_status_terminal.isChecked()
        win.btn_status_terminal.click()
        qapp.processEvents()
        assert win.bottom_dock.isVisible()
        assert win.btn_status_terminal.isChecked()

        # 7. Left Node Palette Sidebar closes with '×' and reopens via Node Palette button or View menu
        assert not hasattr(win.left_panel, "btn_terminal")
        assert win.left_dock.isVisible()
        win.left_panel.btn_close.click()
        qapp.processEvents()
        assert not win.left_dock.isVisible()
        assert not win.btn_status_palette.isChecked()
        assert not win.act_toggle_palette.isChecked()
        win.btn_status_palette.click()
        qapp.processEvents()
        assert win.left_dock.isVisible()
        assert win.btn_status_palette.isChecked()
        assert win.act_toggle_palette.isChecked()
        # Reopen also works via View menu after close
        win.left_panel.btn_close.click()
        qapp.processEvents()
        win.act_toggle_palette.trigger()
        qapp.processEvents()
        assert win.left_dock.isVisible()

        # 8. Every single NodeType (including Channel Mapping and Equalizer) instantiates & edits cleanly
        created_ids = []
        for idx, nt in enumerate(NODE_SPECS.keys()):
            created_node = canvas.add_node_action(nt, scene_pos=QPointF(60 + (idx % 4) * 300, 500 + (idx // 4) * 180))
            assert created_node.id in canvas.node_items
            created_ids.append(created_node)

        ch_node = next(n for n in created_ids if n.node_type == NodeType.CHANNEL_MAP)
        ch_item = canvas.node_items[ch_node.id]
        assert ch_item.cmb_map_l is not None and ch_item.cmb_map_r is not None
        ch_item.cmb_map_l.setCurrentIndex(1)
        ch_item.cmb_map_r.setCurrentIndex(0)
        assert ch_node.channel_map == [1, 0]

        eq_node = next(n for n in created_ids if n.node_type == NodeType.EQUALIZER)
        eq_node_item = canvas.node_items[eq_node.id]
        eq_node_item.eq_sliders[0].setValue(35)
        assert eq_node.eq_gains_db[0] == pytest.approx(3.5)
        assert "dB" in eq_node_item.lbl_eq_vals[0].text()
    finally:
        win.close()




