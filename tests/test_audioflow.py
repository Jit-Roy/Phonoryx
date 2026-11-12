"""Comprehensive integration tests for AudioFlow Python bridge, models, persistence, and PySide6 studio."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDropEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QToolButton

from audioflow.engine_bridge import AudioFlowEngine, DataFlow, DeviceInfo, DeviceRoleHint, NodeType, ProcessInfo
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
        win1.canvas.centerOn(580, 390)
        win1.canvas.zoom_in()
        win1.canvas.zoom_in()
        qapp.processEvents()
        saved_view = win1.canvas.get_view_state()
        assert saved_view["zoom"] == pytest.approx(1.15 * 1.15, rel=1e-3)
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
        # Viewport zoom and center location must be faithfully restored
        restored_view = win2.canvas.get_view_state()
        assert restored_view["zoom"] == pytest.approx(saved_view["zoom"], rel=1e-3)
        assert restored_view["center_x"] == pytest.approx(saved_view["center_x"], abs=3.0)
        assert restored_view["center_y"] == pytest.approx(saved_view["center_y"], abs=3.0)
        assert win2.canvas.btn_float_zoom.text() == "132%"
    finally:
        win2.close()

    # Opening with restore_cache=False must not wipe an existing cache file on close
    before_bytes = cache_file.read_text(encoding="utf-8")
    win3 = AudioFlowMainWindow(auto_start=False, cache_path=cache_file, restore_cache=False)
    win3.show()
    qapp.processEvents()
    win3.close()
    assert cache_file.read_text(encoding="utf-8") == before_bytes

    # Panning & zooming alone on reopen persists new camera without marking clean workflow dirty
    win4 = AudioFlowMainWindow(auto_start=False, cache_path=cache_file, restore_cache=True)
    win4.show()
    qapp.processEvents()
    try:
        vp4 = win4.canvas.viewport()
        start_pt = vp4.rect().center()
        QTest.mousePress(vp4, Qt.MouseButton.MiddleButton, Qt.KeyboardModifier.NoModifier, start_pt)
        QTest.mouseMove(vp4, start_pt + QPoint(-110, -70))
        QTest.mouseRelease(vp4, Qt.MouseButton.MiddleButton, Qt.KeyboardModifier.NoModifier, start_pt + QPoint(-110, -70))
        win4.canvas.zoom_in()
        qapp.processEvents()
        assert not win4._tabs[0].dirty
        moved_view = win4.canvas.get_view_state()
        assert moved_view["zoom"] > saved_view["zoom"]
    finally:
        win4.close()

    win5 = AudioFlowMainWindow(auto_start=False, cache_path=cache_file, restore_cache=True)
    win5.show()
    qapp.processEvents()
    try:
        reloaded_view = win5.canvas.get_view_state()
        assert reloaded_view["zoom"] == pytest.approx(moved_view["zoom"], rel=1e-3)
        assert reloaded_view["center_x"] == pytest.approx(moved_view["center_x"], abs=3.0)
        assert reloaded_view["center_y"] == pytest.approx(moved_view["center_y"], abs=3.0)
    finally:
        win5.close()


def test_ui_layout_window_docks_tabs_and_terminal_cache(qapp, tmp_path: Path):
    cache_file = tmp_path / "ui_layout_cache.json"

    # Session 1: resize & move window, resize docks, pick Devices + Logs tabs, close terminal
    avail = qapp.primaryScreen().availableGeometry()
    target_w = max(780, min(920, avail.width() - 10))
    target_h = max(520, min(620, avail.height() - 30))
    target_x = avail.left() + 5
    target_y = avail.top() + 15

    win1 = AudioFlowMainWindow(auto_start=False, cache_path=cache_file, restore_cache=True)
    win1.show()
    qapp.processEvents()
    try:
        win1.setGeometry(target_x, target_y, target_w, target_h)
        win1.resizeDocks([win1.left_dock], [440], Qt.Orientation.Horizontal)
        win1.resizeDocks([win1.bottom_dock], [275], Qt.Orientation.Vertical)
        win1.left_panel.tabs.setCurrentIndex(1)   # Devices
        win1.bottom_panel.tabs.setCurrentIndex(3) # Logs
        qapp.processEvents()
        saved_left_w = win1.left_dock.width()
        saved_term_h = win1.bottom_dock.height()
        # Close bottom terminal via '×' button after setting its height
        win1.bottom_panel.btn_close.click()
        qapp.processEvents()
        assert not win1.bottom_dock.isVisible()
    finally:
        win1.close()

    # Session 2: reopened app restores geometry, palette tab=Devices, logs tab, closed terminal + height
    win2 = AudioFlowMainWindow(auto_start=False, cache_path=cache_file, restore_cache=True)
    win2.show()
    qapp.processEvents()
    try:
        assert win2.left_panel.tabs.currentIndex() == 1
        assert win2.bottom_panel.tabs.currentIndex() == 3
        assert not win2.bottom_dock.isVisible()
        assert not win2.btn_status_terminal.isChecked()
        assert not win2.act_toggle_terminal.isChecked()
        assert win2.left_dock.isVisible()
        assert abs(win2.left_dock.width() - saved_left_w) <= 6
        assert abs(win2.width() - target_w) <= 6
        assert abs(win2.height() - target_h) <= 6
        assert abs(win2.x() - target_x) <= 6
        assert abs(win2.y() - target_y) <= 6

        # Reopening terminal restores saved terminal height
        win2.btn_status_terminal.click()
        qapp.processEvents()
        assert win2.bottom_dock.isVisible()
        assert abs(win2.bottom_dock.height() - saved_term_h) <= 6

        # Maximized state also persists across restarts
        win2.showMaximized()
        qapp.processEvents()
    finally:
        win2.close()

    win3 = AudioFlowMainWindow(auto_start=False, cache_path=cache_file, restore_cache=True)
    win3.show()
    qapp.processEvents()
    try:
        assert win3.isMaximized()
    finally:
        win3.close()


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

        # 5. '+' new workflow tab button is compact ComfyUI square; creates fresh canvas & preserves per-tab zoom/location
        assert win.btn_new_tab.width() <= 32
        assert win.btn_new_tab.height() <= 32
        assert not hasattr(canvas, "btn_float_group")
        assert not hasattr(canvas, "btn_float_del")
        float_texts = [b.text() for b in canvas.floating_bar.findChildren(QToolButton)]
        assert "Group" not in float_texts and "Delete" not in float_texts
        assert float_texts[0] == "Fit"

        canvas.zoom_in()
        canvas.centerOn(650, 420)
        qapp.processEvents()
        tab0_view = canvas.get_view_state()
        assert win.workflow_tabs.count() == 1
        win.btn_new_tab.click()
        qapp.processEvents()
        assert win.workflow_tabs.count() == 2
        assert win.workflow_tabs.currentIndex() == 1
        assert len(win.project.nodes) == 0
        assert canvas.transform().m11() == pytest.approx(1.0)
        win.workflow_tabs.setCurrentIndex(0)
        qapp.processEvents()
        assert len(win.project.nodes) == 3
        assert canvas.transform().m11() == pytest.approx(tab0_view["zoom"], rel=1e-3)
        tab0_restored = canvas.get_view_state()
        assert tab0_restored["center_x"] == pytest.approx(tab0_view["center_x"], abs=3.0)
        assert tab0_restored["center_y"] == pytest.approx(tab0_view["center_y"], abs=3.0)

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

        # 9. Wheel over dropdown button or open dropdown list scrolls list & NEVER zooms canvas
        app_node = next(n for n in created_ids if n.node_type == NodeType.APP_CAPTURE)
        app_item = canvas.node_items[app_node.id]
        cmb = app_item.cmb_process
        assert cmb is not None
        for p_idx in range(35):
            cmb.addItem(f"app_{p_idx:02d}.exe (PID {100 + p_idx})", (100 + p_idx, f"app_{p_idx:02d}.exe"))
        canvas.centerOn(app_node.x + NODE_WIDTH / 2.0, app_node.y + 70.0)
        qapp.processEvents()
        zoom_before = canvas.transform().m11()

        # Hovering closed dropdown box: wheel must not change selection or zoom canvas
        cmb_scene_center = app_item._proxy.mapToScene(QPointF(cmb.geometry().center()))
        cmb_vp_pos = canvas.mapFromScene(cmb_scene_center)
        closed_wheel = QWheelEvent(
            QPointF(cmb_vp_pos),
            QPointF(canvas.viewport().mapToGlobal(cmb_vp_pos)),
            QPoint(0, 0),
            QPoint(0, -120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        QApplication.sendEvent(canvas.viewport(), closed_wheel)
        qapp.processEvents()
        assert canvas.transform().m11() == pytest.approx(zoom_before)
        assert app_node.pid == 0

        # Open dropdown list: wheel must scroll the list (NOT zoom canvas)
        cmb.showPopup()
        qapp.processEvents()
        popup_view = cmb.view()
        popup_scroll_before = popup_view.verticalScrollBar().value()
        assert popup_view.verticalScrollBar().maximum() > 0
        popup_global = popup_view.viewport().mapToGlobal(popup_view.viewport().rect().center())
        popup_vp_pos = canvas.viewport().mapFromGlobal(popup_global)
        open_wheel = QWheelEvent(
            QPointF(popup_vp_pos),
            QPointF(popup_global),
            QPoint(0, 0),
            QPoint(0, -240),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        QApplication.sendEvent(canvas.viewport(), open_wheel)
        qapp.processEvents()
        assert canvas.transform().m11() == pytest.approx(zoom_before)
        assert popup_view.verticalScrollBar().value() > popup_scroll_before
        # Clicking on empty canvas while dropdown is open dismisses the dropdown list
        assert popup_view.isVisible()
        QTest.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(18, 18))
        qapp.processEvents()
        assert not popup_view.isVisible()
        qapp.processEvents()

        # 10. Double-clicking workflow tab header renames workflow (inline editor + file rename)
        win.workflow_tabs.setCurrentIndex(1)
        qapp.processEvents()
        win.workflow_tabs.tabBarDoubleClicked.emit(1)
        qapp.processEvents()
        ed = win._tab_rename_editor
        assert ed is not None
        ed.setText("Live Stream Routing")
        QTest.keyClick(ed, Qt.Key.Key_Return)
        qapp.processEvents()
        assert win.workflow_tabs.tabText(1) == "Live Stream Routing"

        # Escape cancels inline rename
        win.workflow_tabs.tabBarDoubleClicked.emit(1)
        qapp.processEvents()
        ed2 = win._tab_rename_editor
        assert ed2 is not None
        ed2.setText("Should Cancel")
        QTest.keyClick(ed2, Qt.Key.Key_Escape)
        qapp.processEvents()
        assert win.workflow_tabs.tabText(1) == "Live Stream Routing"

        # Clicking empty canvas without changing name exits inline rename mode immediately
        win.workflow_tabs.tabBarDoubleClicked.emit(1)
        qapp.processEvents()
        ed3 = win._tab_rename_editor
        assert ed3 is not None and ed3.isVisible()
        QTest.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(18, 18))
        qapp.processEvents()
        assert win._tab_rename_editor is None
        assert win.workflow_tabs.tabText(1) == "Live Stream Routing"

        # Renaming saved tab 0 renames saved .aflow file on disk and tab title
        assert win.rename_workflow_tab(0, "master_bus")
        assert (tmp_path / "master_bus.aflow").is_file()
        assert not save_path.exists()
        assert win.workflow_tabs.tabText(0) == "master_bus *"
    finally:
        win.close()


def test_refresh_button_and_comfyui_node_resize(tmp_path):
    qapp = QApplication.instance() or QApplication([])
    cache_path = tmp_path / "workspace_cache.json"

    win = AudioFlowMainWindow(cache_path=cache_path, restore_cache=False, auto_start=False)
    try:
        win.resize(1200, 760)
        win.show()
        qapp.processEvents()
        canvas = win.canvas

        n_mic = canvas.add_node_action(NodeType.MIC_INPUT, scene_pos=QPointF(100.0, 100.0))
        n_vol = canvas.add_node_action(NodeType.VOLUME, scene_pos=QPointF(460.0, 100.0))
        assert canvas.connect_ports_action(n_mic.id, 0, n_vol.id, 0)
        qapp.processEvents()

        # 1. Verify Refresh button enumerates live devices/processes & updates UI + logs
        before_devs = win.left_panel.device_list.count()
        before_procs = win.left_panel.proc_list.count()
        before_cmb = canvas.node_items[n_mic.id].cmb_device.count()
        fake_devs = list(win._devices) + [
            DeviceInfo("dev-usb-test", "USB Test Mic", DataFlow.CAPTURE, DeviceRoleHint.MICROPHONE, 1, 48000, 2, False, False)
        ]
        fake_procs = list(win._processes) + [
            ProcessInfo(98765, 1, "SpotifyTest.exe", r"C:\SpotifyTest.exe", "SpotifyTest", True, 0.4)
        ]
        win.engine.enumerate_devices = lambda: fake_devs
        win.engine.enumerate_processes = lambda: fake_procs

        win.btn_refresh.click()
        qapp.processEvents()
        assert win.left_panel.device_list.count() == before_devs + 1
        assert win.left_panel.proc_list.count() == before_procs + 1
        assert canvas.node_items[n_mic.id].cmb_device.count() == before_cmb + 1
        assert win.bottom_panel.device_status_table.rowCount() == before_devs + 1
        assert "Refreshed" in win.bottom_panel.log_console.toPlainText()

        # 2. ComfyUI-style SE corner drag resizes node width & height, moves output ports & wire, and is undoable
        canvas.fit_view()
        qapp.processEvents()
        mic_item = canvas.node_items[n_mic.id]
        orig_w = mic_item._node_width()
        orig_h = mic_item._compute_height()
        conn = canvas.project.connections[0]
        orig_wire_start_x = canvas.conn_items[conn].path().elementAt(0).x

        se_local = QPointF(orig_w - 4.0, orig_h - 4.0)
        se_vp = canvas.mapFromScene(mic_item.mapToScene(se_local))
        target_vp = canvas.mapFromScene(mic_item.mapToScene(QPointF(orig_w + 90.0, orig_h + 60.0)))

        QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, se_vp)
        QTest.mouseMove(canvas.viewport(), target_vp)
        QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, target_vp)
        qapp.processEvents()

        assert mic_item._node_width() > orig_w + 60.0
        assert mic_item._compute_height() > orig_h + 40.0
        assert mic_item.output_ports[0].x() == pytest.approx(mic_item._node_width())
        assert canvas.conn_items[conn].path().elementAt(0).x > orig_wire_start_x + 60.0

        # Shrinking past minimum size clamps cleanly to compute_min_size()
        cur_w = mic_item._node_width()
        cur_h = mic_item._compute_height()
        min_w, min_h = mic_item.compute_min_size()
        se_vp2 = canvas.mapFromScene(mic_item.mapToScene(QPointF(cur_w - 4.0, cur_h - 4.0)))
        shrink_vp = canvas.mapFromScene(mic_item.mapToScene(QPointF(60.0, 40.0)))
        QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, se_vp2)
        QTest.mouseMove(canvas.viewport(), shrink_vp)
        QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, shrink_vp)
        qapp.processEvents()
        assert mic_item._node_width() == pytest.approx(min_w)
        assert mic_item._compute_height() == pytest.approx(min_h)

        # Undo restores previous resized state cleanly in a single step
        canvas.undo_stack.undo()
        qapp.processEvents()
        assert mic_item._node_width() > orig_w + 60.0

        # Dragging NW corner expands top-left while keeping bottom-right edge anchored
        old_x = mic_item.pos().x()
        old_y = mic_item.pos().y()
        old_right = old_x + mic_item._node_width()
        old_bottom = old_y + mic_item._compute_height()
        nw_vp = canvas.mapFromScene(mic_item.mapToScene(QPointF(3.0, 3.0)))
        nw_target_vp = canvas.mapFromScene(mic_item.mapToScene(QPointF(-27.0, -17.0)))
        QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, nw_vp)
        QTest.mouseMove(canvas.viewport(), nw_target_vp)
        QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, nw_target_vp)
        qapp.processEvents()
        assert mic_item.pos().x() < old_x - 20.0
        assert mic_item.pos().y() < old_y - 10.0
        assert (mic_item.pos().x() + mic_item._node_width()) == pytest.approx(old_right, abs=2.0)
        assert (mic_item.pos().y() + mic_item._compute_height()) == pytest.approx(old_bottom, abs=2.0)

        # Persistence check: save cache and reopen restores custom node size
        win.save_workspace_cache()
        expected_w = mic_item._node_width()
        expected_h = mic_item._compute_height()
    finally:
        win.close()

    win2 = AudioFlowMainWindow(cache_path=cache_path, restore_cache=True, auto_start=False)
    try:
        win2.show()
        qapp.processEvents()
        restored_item = win2.canvas.node_items[n_mic.id]
        assert restored_item._node_width() == pytest.approx(expected_w)
        assert restored_item._compute_height() == pytest.approx(expected_h)
        # Resize-to-Fit restores default computed size
        restored_item.setSelected(True)
        win2.canvas.resize_selected_to_fit_action()
        qapp.processEvents()
        assert restored_item.model.width is None
        assert restored_item._node_width() == pytest.approx(NODE_WIDTH)
    finally:
        win2.close()


def test_save_location_and_node_resize_persist_across_restarts(qapp, monkeypatch, tmp_path: Path):
    cache_path = tmp_path / "restart_save_cache.json"
    project_stem = tmp_path / "remembered_mix"
    project_file = tmp_path / "remembered_mix.json"
    dialog_calls = []

    def _fake_save_dialog(*args, **kwargs):
        dialog_calls.append(args)
        # Return extensionless name to verify ComfyUI-style automatic .json extension
        return str(project_stem), "AudioFlow Workflow (*.json)"

    monkeypatch.setattr(QFileDialog, "getSaveFileName", _fake_save_dialog)

    # Session 1: resize a node via mouse drag, Ctrl+S once (prompts), edit & Ctrl+S again (no prompt), close
    win1 = AudioFlowMainWindow(cache_path=cache_path, restore_cache=True, auto_start=False)
    try:
        win1.resize(1200, 760)
        win1.show()
        qapp.processEvents()
        n_eq = win1.canvas.add_node_action(NodeType.EQUALIZER, scene_pos=QPointF(120.0, 90.0))
        qapp.processEvents()
        win1.canvas.fit_view()
        qapp.processEvents()

        eq_item = win1.canvas.node_items[n_eq.id]
        orig_w = eq_item._node_width()
        orig_h = eq_item._compute_height()
        se_vp = win1.canvas.mapFromScene(eq_item.mapToScene(QPointF(orig_w - 4.0, orig_h - 4.0)))
        drag_vp = win1.canvas.mapFromScene(eq_item.mapToScene(QPointF(orig_w + 110.0, orig_h + 70.0)))
        QTest.mousePress(win1.canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, se_vp)
        QTest.mouseMove(win1.canvas.viewport(), drag_vp)
        QTest.mouseRelease(win1.canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, drag_vp)
        qapp.processEvents()

        resized_w = eq_item._node_width()
        resized_h = eq_item._compute_height()
        assert resized_w > orig_w + 80.0
        assert resized_h > orig_h + 50.0

        # First Ctrl+S asks once for location and saves cleanly
        QTest.keyClick(win1.canvas, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
        qapp.processEvents()
        assert len(dialog_calls) == 1
        assert project_file.is_file()
        assert win1.project_path == project_file
        assert win1.workflow_tabs.tabText(0) == "remembered_mix"

        # Further edit + second Ctrl+S saves directly without prompting again
        n_vol = win1.canvas.add_node_action(NodeType.VOLUME, scene_pos=QPointF(520.0, 110.0))
        assert win1.workflow_tabs.tabText(0) == "remembered_mix *"
        QTest.keyClick(win1.canvas, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
        qapp.processEvents()
        assert len(dialog_calls) == 1
        assert win1.workflow_tabs.tabText(0) == "remembered_mix"

        # Make another node resize in a 2nd workflow tab, switch back & close WITHOUT manual save_workspace_cache()
        win1.btn_new_tab.click()
        qapp.processEvents()
        n_tab2 = win1.canvas.add_node_action(NodeType.GAIN, scene_pos=QPointF(140.0, 140.0))
        win1.canvas.node_items[n_tab2.id].apply_geometry(140.0, 140.0, 350.0, 210.0)
        win1.workflow_tabs.setCurrentIndex(0)
        qapp.processEvents()
    finally:
        win1.close()

    # Session 2: reopen app -> remembered file path, clean title, resized node size, and tab 2 resize all persist!
    win2 = AudioFlowMainWindow(cache_path=cache_path, restore_cache=True, auto_start=False)
    try:
        win2.show()
        qapp.processEvents()
        assert win2.workflow_tabs.count() == 2
        assert win2.workflow_tabs.currentIndex() == 0
        assert win2.project_path == project_file
        assert win2._tabs[0].file_path == project_file
        assert win2.workflow_tabs.tabText(0) == "remembered_mix"

        restored_eq = win2.canvas.node_items[n_eq.id]
        assert restored_eq._node_width() == pytest.approx(resized_w)
        assert restored_eq._compute_height() == pytest.approx(resized_h)

        # Make an edit after reopening and hit Ctrl+S -> MUST NOT prompt for save location!
        n_gain = win2.canvas.add_node_action(NodeType.GAIN, scene_pos=QPointF(520.0, 280.0), gain_db=4.5)
        qapp.processEvents()
        assert win2.workflow_tabs.tabText(0) == "remembered_mix *"
        QTest.keyClick(win2.canvas, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
        qapp.processEvents()
        assert len(dialog_calls) == 1
        assert win2.workflow_tabs.tabText(0) == "remembered_mix"

        saved_disk = json.loads(project_file.read_text(encoding="utf-8"))
        saved_ids = {n["id"]: n for n in saved_disk["nodes"]}
        assert n_gain.id in saved_ids
        assert saved_ids[n_eq.id]["width"] == pytest.approx(resized_w)
        assert saved_ids[n_eq.id]["height"] == pytest.approx(resized_h)
        assert "ds" in saved_disk.get("extra", {})
        assert "viewport" in saved_disk.get("extra", {})
        assert len(dialog_calls[0]) >= 4
        assert dialog_calls[0][2].endswith(".json")
        assert "*.json" in dialog_calls[0][3]

        # Tab 2's resized node was preserved across restart too
        win2.workflow_tabs.setCurrentIndex(1)
        qapp.processEvents()
        restored_tab2 = win2.canvas.node_items[n_tab2.id]
        assert restored_tab2._node_width() == pytest.approx(350.0)
        assert restored_tab2._compute_height() == pytest.approx(210.0)

        # Opening saved .json workflow restores nodes and saved viewport
        win2.workflow_tabs.setCurrentIndex(1)
        win2.canvas.zoom_in()
        win2.load_project_path(project_file)
        qapp.processEvents()
        assert win2.canvas.transform().m11() == pytest.approx(
            saved_disk["extra"]["ds"]["scale"], rel=1e-3
        )
    finally:
        win2.close()


def test_per_tab_run_stop_runs_multiple_tabs_simultaneously(qapp, tmp_path: Path):
    """Switching tabs must not stop/overwrite other tabs, and btn_engine toggles only the active tab."""
    created_engines = []

    class SpyEngine:
        def __init__(self) -> None:
            self.running = False
            self.closed = False
            self.applied_graphs = []
            created_engines.append(self)

        @property
        def is_running(self) -> bool:
            return self.running

        def start(self) -> bool:
            self.running = True
            return True

        def stop(self) -> None:
            self.running = False

        def close(self) -> None:
            self.running = False
            self.closed = True

        def enumerate_devices(self):
            return []

        def enumerate_processes(self):
            return []

        def device_change_seq(self) -> int:
            return 1

        def apply_graph(self, payload: dict):
            self.applied_graphs.append(payload)
            return True, ""

        def set_float_param(self, node_id: int, param: str, val: float) -> bool:
            return True

        def set_int_param(self, node_id: int, param: str, val: int) -> bool:
            return True

        def get_node_meters(self):
            return {}

        def get_engine_stats(self):
            from audioflow.engine_bridge import EngineStats
            return EngineStats(
                running=self.running,
                sample_rate=48000,
                block_frames=256,
                active_nodes=len(self.applied_graphs[-1]["nodes"]) if self.applied_graphs else 0,
                active_connections=0,
                virtual_endpoints=0,
                total_xruns=0,
                cpu_load_percent=0.0,
                processed_blocks=1 if self.running else 0,
                device_change_seq=1,
            )

        def poll_logs(self):
            return []

    win = AudioFlowMainWindow(
        auto_start=False,
        cache_path=tmp_path / "multi_run_cache.json",
        restore_cache=False,
        engine_factory=SpyEngine,
    )
    try:
        win.show()
        qapp.processEvents()
        assert len(created_engines) == 1
        eng0 = win._tabs[0].engine
        assert win.btn_engine.text() == " Run"
        assert not bool(win.btn_engine.property("running"))

        # Add node to Tab 0 and click Run -> Tab 0 runs (button turns red ' Stop')
        win.canvas.add_node_action(NodeType.VOLUME, scene_pos=QPointF(100, 100))
        win.btn_engine.click()
        qapp.processEvents()
        assert eng0.is_running is True
        assert win.btn_engine.text() == " Stop"
        assert bool(win.btn_engine.property("running")) is True
        tab0_applies_before_switch = len(eng0.applied_graphs)

        # Open Tab 1 -> Tab 0 keeps running; Tab 1 starts stopped (' Run')
        win.btn_new_tab.click()
        qapp.processEvents()
        assert win.workflow_tabs.count() == 2
        assert win.workflow_tabs.currentIndex() == 1
        eng1 = win._tabs[1].engine
        assert eng1 is not eng0
        assert eng0.is_running is True
        assert eng1.is_running is False
        assert win.btn_engine.text() == " Run"
        assert not bool(win.btn_engine.property("running"))
        assert len(eng0.applied_graphs) == tab0_applies_before_switch

        # Editing & starting Tab 1 runs BOTH tabs simultaneously without touching Tab 0
        win.canvas.add_node_action(NodeType.GAIN, scene_pos=QPointF(150, 150))
        win.btn_engine.click()
        qapp.processEvents()
        assert eng0.is_running is True
        assert eng1.is_running is True
        assert win.btn_engine.text() == " Stop"
        assert len(eng0.applied_graphs) == tab0_applies_before_switch

        # Switching back to Tab 0 just to inspect leaves BOTH running and avoids re-applying graphs
        win.workflow_tabs.setCurrentIndex(0)
        qapp.processEvents()
        assert win.btn_engine.text() == " Stop"
        assert eng0.is_running is True
        assert eng1.is_running is True
        assert len(eng0.applied_graphs) == tab0_applies_before_switch

        # Stopping Tab 0 via red ' Stop' button stops ONLY Tab 0; Tab 1 keeps running
        win.btn_engine.click()
        qapp.processEvents()
        assert eng0.is_running is False
        assert eng1.is_running is True
        assert win.btn_engine.text() == " Run"
        assert "1 bg tab running" in win.lbl_status_engine.text()

        # Closing Tab 0 closes eng0 and leaves eng1 running
        win.close_workflow_tab(0)
        qapp.processEvents()
        assert eng0.closed is True
        assert eng1.is_running is True
        assert win.btn_engine.text() == " Stop"
    finally:
        win.close()

    assert eng1.closed is True


def test_real_native_engines_run_concurrently_across_tabs(qapp, tmp_path: Path):
    """Two tabs backed by real C++ AudioFlowEngine instances run and stop independently."""
    win = AudioFlowMainWindow(
        auto_start=True,
        cache_path=tmp_path / "native_multi_tab_cache.json",
        restore_cache=False,
    )
    try:
        win.show()
        qapp.processEvents()
        eng0 = win._tabs[0].engine
        assert isinstance(eng0, AudioFlowEngine)
        assert eng0.is_running is True

        win.btn_new_tab.click()
        qapp.processEvents()
        eng1 = win._tabs[1].engine
        assert isinstance(eng1, AudioFlowEngine)
        assert eng1 is not eng0
        assert eng0.is_running is True
        assert eng1.is_running is False
        assert win.btn_engine.text() == " Run"

        # Start Tab 1 as well -> both real C++ engines live concurrently
        win.btn_engine.click()
        qapp.processEvents()
        assert eng0.is_running is True
        assert eng1.is_running is True
        assert win.btn_engine.text() == " Stop"

        # Stop only Tab 1; Tab 0 remains running
        win.btn_engine.click()
        qapp.processEvents()
        assert eng0.is_running is True
        assert eng1.is_running is False
    finally:
        win.close()





