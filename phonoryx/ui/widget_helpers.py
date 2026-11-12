"""Shared catalog filtering and formatting helpers for inline node widgets and panels."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

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


def _build_process_tree_index(
    processes: List[ProcessInfo],
) -> Tuple[Dict[int, ProcessInfo], Dict[int, List[ProcessInfo]]]:
    """Index processes by PID and parent PID for descendant-tree traversal."""
    by_pid: Dict[int, ProcessInfo] = {}
    children_by_ppid: Dict[int, List[ProcessInfo]] = {}
    for proc in processes:
        by_pid[proc.pid] = proc
        if proc.parent_pid > 0 and proc.parent_pid != proc.pid:
            children_by_ppid.setdefault(proc.parent_pid, []).append(proc)
    return by_pid, children_by_ppid


def _collect_subtree(
    root_pid: int,
    by_pid: Dict[int, ProcessInfo],
    children_by_ppid: Dict[int, List[ProcessInfo]],
) -> List[Tuple[int, ProcessInfo]]:
    """Return ``(depth, ProcessInfo)`` pairs for ``root_pid`` and all descendants."""
    root = by_pid.get(root_pid)
    if root is None:
        return []
    visited = {root_pid}
    out: List[Tuple[int, ProcessInfo]] = [(0, root)]
    queue: List[Tuple[int, int]] = [(0, root_pid)]
    head = 0
    while head < len(queue):
        depth, pid = queue[head]
        head += 1
        for child in children_by_ppid.get(pid, ()):
            if child.pid in visited:
                continue
            visited.add(child.pid)
            out.append((depth + 1, child))
            queue.append((depth + 1, child.pid))
    return out


def resolve_capture_pid_for_process(
    selected_pid: int,
    processes: List[ProcessInfo],
    _index: Optional[Tuple[Dict[int, ProcessInfo], Dict[int, List[ProcessInfo]]]] = None,
) -> int:
    """Resolve the WASAPI capture PID for ``selected_pid`` across its descendant tree.

    Walks all descendant processes (regardless of executable name). If a descendant
    (such as a sandboxed Chromium/WebView2 ``AudioService`` or child runtime) holds
    an active WASAPI audio session while the parent itself has no separate non-zero
    render stream, binds capture to the best active descendant PID. Otherwise keeps
    ``selected_pid`` for standard ``INCLUDE_TARGET_PROCESS_TREE`` capture.
    """
    if selected_pid <= 0:
        return selected_pid
    by_pid, children_by_ppid = _index if _index is not None else _build_process_tree_index(processes)
    if selected_pid not in by_pid:
        return selected_pid

    subtree = _collect_subtree(selected_pid, by_pid, children_by_ppid)
    root_proc = by_pid[selected_pid]
    root_name = root_proc.exe_name.strip().lower()
    audio_descendants = [
        (depth, proc) for depth, proc in subtree if depth > 0 and proc.has_audio_session
    ]
    if not audio_descendants:
        return selected_pid

    descendant_has_peak = any(proc.session_peak > 0.0 for _, proc in audio_descendants)
    if root_proc.has_audio_session and root_proc.session_peak > 0.0 and not descendant_has_peak:
        return selected_pid

    _, best_proc = max(
        audio_descendants,
        key=lambda item: (
            item[1].session_peak > 0.0,
            item[1].session_peak,
            item[1].exe_name.strip().lower() != root_name,
            item[0],
            -item[1].pid,
        ),
    )
    return best_proc.pid


def select_best_process_for_name(
    process_name: str, processes: List[ProcessInfo]
) -> Optional[ProcessInfo]:
    """Pick the best live process tree matching ``process_name``, preferring active audio trees."""
    target = process_name.strip().lower()
    if not target:
        return None
    matches = [p for p in processes if p.exe_name.strip().lower() == target]
    if not matches:
        return None

    by_pid, children_by_ppid = _build_process_tree_index(processes)
    match_pids = {p.pid for p in matches}
    child_counts = {p.pid: 0 for p in matches}
    for proc in matches:
        if proc.parent_pid in child_counts:
            child_counts[proc.parent_pid] += 1

    def _score(proc: ProcessInfo) -> Tuple[bool, float, bool, bool, bool, bool, int]:
        subtree = _collect_subtree(proc.pid, by_pid, children_by_ppid)
        tree_has_peak = any(child.session_peak > 0.0 for _, child in subtree)
        tree_max_peak = max((child.session_peak for _, child in subtree), default=0.0)
        tree_has_audio = any(child.has_audio_session for _, child in subtree)
        return (
            tree_has_peak,
            tree_max_peak,
            tree_has_audio,
            proc.parent_pid not in match_pids,
            child_counts.get(proc.pid, 0) > 0,
            bool(proc.window_title.strip()),
            -proc.pid,
        )

    return max(matches, key=_score)


def resolve_project_process_bindings(
    project: ProjectModel, processes: List[ProcessInfo]
) -> List[Tuple[NodeModel, int, int]]:
    """Resolve APP_CAPTURE nodes by ``process_name`` and descendant audio sessions.

    Preserves the selected host app identity (``node.pid`` and ``node.process_name``)
    in the project while updating ``node.capture_pid`` to an active descendant
    WASAPI audio child when present. Returns ``(node, old_pid, new_pid)`` tuples
    whenever host or capture PID bindings change.
    """
    proc_index = _build_process_tree_index(processes)
    by_pid = proc_index[0]
    changes: List[Tuple[NodeModel, int, int]] = []
    for node in project.nodes.values():
        if node.node_type != NodeType.APP_CAPTURE:
            continue
        old_host_pid = node.pid
        old_capture_pid = node.capture_pid or node.pid
        target_name = node.process_name.strip()

        host_alive = False
        if old_host_pid > 0 and old_host_pid in by_pid:
            live = by_pid[old_host_pid]
            if not target_name:
                node.process_name = live.exe_name
                host_alive = True
            elif live.exe_name.strip().lower() == target_name.lower():
                host_alive = True

        if not host_alive and target_name:
            best = select_best_process_for_name(target_name, processes)
            new_host_pid = best.pid if best is not None else 0
            if best is not None:
                node.process_name = best.exe_name
            node.pid = new_host_pid

        if node.pid > 0 and node.pid in by_pid:
            new_capture_pid = resolve_capture_pid_for_process(
                node.pid, processes, _index=proc_index
            )
        else:
            new_capture_pid = node.pid

        node.capture_pid = new_capture_pid
        if node.pid != old_host_pid:
            changes.append((node, old_host_pid, node.pid))
        elif node.pid > 0 and new_capture_pid != old_capture_pid:
            changes.append((node, old_capture_pid, new_capture_pid))
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
