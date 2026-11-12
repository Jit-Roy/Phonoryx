"""Shared catalog filtering and formatting helpers for inline node widgets and panels."""

from __future__ import annotations

from typing import List, Optional, Tuple

from phonoryx.engine_bridge import DataFlow, DeviceInfo, NodeType, ProcessInfo
from phonoryx.models.graph_model import NodeModel, ProjectModel

VIRTUAL_DRIVER_MISSING_TEXT = (
    "No Windows virtual audio driver detected. Install the open-source MIT "
    "VirtualDrivers/Virtual-Audio-Driver (or another virtual cable) to expose "
    "virtual endpoints to Chrome, Discord, and OBS."
)

EQ_BAND_LABELS = ("Sub 60Hz", "Low 250Hz", "Mid 1kHz", "High 4kHz", "Air 12kHz")


def node_needs_device_selector(node_type: NodeType) -> bool:
    return node_type in (
        NodeType.MIC_INPUT,
        NodeType.VIRTUAL_INPUT,
        NodeType.SPEAKER_OUTPUT,
        NodeType.VIRTUAL_OUTPUT,
    )


def node_supports_mute(node_type: NodeType) -> bool:
    return node_type in (
        NodeType.APP_CAPTURE,
        NodeType.SYSTEM_CAPTURE,
        NodeType.MIC_INPUT,
        NodeType.VIRTUAL_INPUT,
        NodeType.VOLUME,
        NodeType.GAIN,
        NodeType.MUTE,
        NodeType.MIXER,
        NodeType.SPEAKER_OUTPUT,
        NodeType.VIRTUAL_OUTPUT,
        NodeType.FILE_RECORDING,
    )


def node_supports_volume(node_type: NodeType) -> bool:
    return node_type in (
        NodeType.VOLUME,
        NodeType.MIXER,
        NodeType.SPEAKER_OUTPUT,
        NodeType.VIRTUAL_OUTPUT,
    )


def virtual_driver_missing_text(
    node_type: NodeType, devices: List[DeviceInfo]
) -> Optional[str]:
    """Return warning text when a Virtual node has no matching Windows virtual endpoints."""
    if node_type not in (NodeType.VIRTUAL_INPUT, NodeType.VIRTUAL_OUTPUT):
        return None
    want_flow = DataFlow.CAPTURE if node_type == NodeType.VIRTUAL_INPUT else DataFlow.RENDER
    has_virtual = any(d.is_virtual and d.flow == want_flow for d in devices)
    return None if has_virtual else VIRTUAL_DRIVER_MISSING_TEXT


def build_device_combo_entries(
    node: NodeModel, devices: List[DeviceInfo]
) -> List[Tuple[str, Tuple[str, str]]]:
    """Return filtered (label, (device_id, device_name)) entries for Physical vs Virtual nodes."""
    entries: List[Tuple[str, Tuple[str, str]]] = [("<Unassigned>", ("", ""))]
    want_capture = node.node_type in (NodeType.MIC_INPUT, NodeType.VIRTUAL_INPUT)
    is_virtual_node = node.node_type in (
        NodeType.VIRTUAL_INPUT,
        NodeType.VIRTUAL_OUTPUT,
    )
    is_physical_only = node.node_type in (
        NodeType.MIC_INPUT,
        NodeType.SPEAKER_OUTPUT,
    )
    candidates = [
        d
        for d in devices
        if (d.flow == DataFlow.CAPTURE if want_capture else d.flow == DataFlow.RENDER)
        and (not is_virtual_node or d.is_virtual)
        and (not is_physical_only or not d.is_virtual)
    ]
    candidates.sort(key=lambda d: d.name.lower())

    seen_ids = {""}
    dev_by_id = {d.id: d for d in devices}
    for d in candidates:
        label = f"[Virtual] {d.name}" if d.is_virtual else d.name
        entries.append((label, (d.id, d.name)))
        seen_ids.add(d.id)

    if node.device_id and node.device_id not in seen_ids:
        existing = dev_by_id.get(node.device_id)
        if existing is not None:
            lbl = f"[Virtual] {existing.name}" if existing.is_virtual else existing.name
            entries.append((lbl, (existing.id, existing.name)))
        else:
            entries.append(
                (
                    f"[OFFLINE] {node.device_name or node.device_id}",
                    (node.device_id, node.device_name),
                )
            )
    return entries


def build_process_combo_entries(
    node: NodeModel, processes: List[ProcessInfo]
) -> List[Tuple[str, Tuple[int, str]]]:
    """Return (label, (pid, exe_name)) entries for Application Audio Capture."""
    entries: List[Tuple[str, Tuple[int, str]]] = [("<Select running process...>", (0, ""))]
    seen_pids = {0}
    sorted_procs = sorted(processes, key=lambda p: (not p.has_audio_session, p.exe_name.lower()))
    for p in sorted_procs:
        tag = "♪ " if p.has_audio_session else ""
        entries.append((f"{tag}{p.exe_name} (PID {p.pid})", (p.pid, p.exe_name)))
        seen_pids.add(p.pid)
    if node.pid and node.pid not in seen_pids:
        entries.append((f"[Not Running] {node.process_name} (PID {node.pid})", (node.pid, node.process_name)))
    elif not node.pid and node.process_name.strip():
        entries.append((f"[Waiting] {node.process_name}", (0, node.process_name)))
    return entries


def select_best_process_for_name(
    process_name: str, processes: List[ProcessInfo]
) -> Optional[ProcessInfo]:
    """Pick the best live process matching ``process_name`` (preferring tree roots)."""
    target = process_name.strip().lower()
    if not target:
        return None
    matches = [p for p in processes if p.exe_name.strip().lower() == target]
    if not matches:
        return None

    match_pids = {p.pid for p in matches}
    child_counts = {p.pid: 0 for p in matches}
    for proc in matches:
        if proc.parent_pid in child_counts:
            child_counts[proc.parent_pid] += 1

    def _score(proc: ProcessInfo) -> Tuple[bool, bool, bool, bool, float, int]:
        return (
            proc.parent_pid not in match_pids,
            child_counts.get(proc.pid, 0) > 0,
            bool(proc.window_title.strip()),
            proc.has_audio_session,
            proc.session_peak,
            -proc.pid,
        )

    return max(matches, key=_score)


def resolve_project_process_bindings(
    project: ProjectModel, processes: List[ProcessInfo]
) -> List[Tuple[NodeModel, int, int]]:
    """Resolve APP_CAPTURE nodes by ``process_name`` against live ``processes``.

    Returns ``(node, old_pid, new_pid)`` tuples for nodes whose PID changed.
    Preserves ``node.process_name`` when the target application is not running so
    it can reconnect automatically once launched.
    """
    by_pid = {p.pid: p for p in processes}
    changes: List[Tuple[NodeModel, int, int]] = []
    for node in project.nodes.values():
        if node.node_type != NodeType.APP_CAPTURE:
            continue
        old_pid = node.pid
        target_name = node.process_name.strip()

        if old_pid > 0 and old_pid in by_pid:
            live = by_pid[old_pid]
            if not target_name:
                node.process_name = live.exe_name
                continue
            if live.exe_name.strip().lower() == target_name.lower():
                continue

        if not target_name:
            continue

        best = select_best_process_for_name(target_name, processes)
        new_pid = best.pid if best is not None else 0
        if best is not None:
            node.process_name = best.exe_name
        if new_pid != old_pid:
            node.pid = new_pid
            changes.append((node, old_pid, new_pid))
    return changes


def matching_process_combo_index(combo, node: NodeModel) -> int:
    """Return the combo index matching ``node.pid`` or its waiting ``process_name``."""
    target_name = node.process_name.strip().lower()
    for i in range(combo.count()):
        data = combo.itemData(i)
        if not data:
            continue
        pid_val, exe_val = data[0], str(data[1]).strip().lower()
        if node.pid and pid_val == node.pid:
            return i
        if not node.pid and target_name and pid_val == 0 and exe_val == target_name:
            return i
    return 0
