"""Shared catalog filtering and formatting helpers for inline node widgets and panels."""

from __future__ import annotations

from typing import List, Optional, Tuple

from audioflow.engine_bridge import DataFlow, DeviceInfo, NodeType, ProcessInfo
from audioflow.models.graph_model import NodeModel

VIRTUAL_DRIVER_MISSING_TEXT = (
    "No Windows virtual audio driver detected. Install the open-source MIT "
    "VirtualDrivers/Virtual-Audio-Driver (or another virtual cable) to expose "
    "virtual endpoints to Chrome, Discord, and OBS."
)

EQ_BAND_LABELS = ("60Hz", "250Hz", "1kHz", "4kHz", "12kHz")


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
    return entries
