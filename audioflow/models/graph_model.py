"""AudioFlow project, graph, and validation models."""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from audioflow.engine_bridge import NodeType


class NodeCategory(str, Enum):
    SOURCE = "source"
    PROCESSING = "processing"
    OUTPUT = "output"


@dataclass(frozen=True, slots=True)
class NodeSpec:
    node_type: NodeType
    title: str
    category: NodeCategory
    input_ports: Tuple[str, ...]
    output_ports: Tuple[str, ...]
    description: str


NODE_SPECS: Dict[NodeType, NodeSpec] = {
    # Audio Source Nodes
    NodeType.APP_CAPTURE: NodeSpec(
        NodeType.APP_CAPTURE,
        "Application Audio Capture",
        NodeCategory.SOURCE,
        (),
        ("Out",),
        "Captures audio from a specific running Windows process (PID).",
    ),
    NodeType.SYSTEM_CAPTURE: NodeSpec(
        NodeType.SYSTEM_CAPTURE,
        "System Audio Capture",
        NodeCategory.SOURCE,
        (),
        ("Out",),
        "Captures system-wide Windows loopback audio from a render endpoint.",
    ),
    NodeType.MIC_INPUT: NodeSpec(
        NodeType.MIC_INPUT,
        "Microphone Input",
        NodeCategory.SOURCE,
        (),
        ("Out",),
        "Captures audio from a Windows microphone input device.",
    ),
    NodeType.LINE_INPUT: NodeSpec(
        NodeType.LINE_INPUT,
        "Physical Line Input",
        NodeCategory.SOURCE,
        (),
        ("Out",),
        "Captures audio from a physical Line-In / interface input.",
    ),
    NodeType.VIRTUAL_INPUT: NodeSpec(
        NodeType.VIRTUAL_INPUT,
        "Virtual Device Input",
        NodeCategory.SOURCE,
        (),
        ("Out",),
        "Captures audio from a Windows virtual audio endpoint (e.g. Virtual Audio Driver).",
    ),
    # Audio Processing Nodes
    NodeType.VOLUME: NodeSpec(
        NodeType.VOLUME,
        "Volume Control",
        NodeCategory.PROCESSING,
        ("In",),
        ("Out",),
        "Smooth linear volume control (0% - 200%).",
    ),
    NodeType.GAIN: NodeSpec(
        NodeType.GAIN,
        "Gain",
        NodeCategory.PROCESSING,
        ("In",),
        ("Out",),
        "Preamp / trim gain in decibels (-60 dB to +24 dB).",
    ),
    NodeType.MIXER: NodeSpec(
        NodeType.MIXER,
        "Audio Mixer",
        NodeCategory.PROCESSING,
        ("In 1", "In 2", "In 3", "In 4"),
        ("Out",),
        "Sums multiple incoming audio streams with soft limiting.",
    ),
    NodeType.SPLITTER: NodeSpec(
        NodeType.SPLITTER,
        "Audio Splitter",
        NodeCategory.PROCESSING,
        ("In",),
        ("Out 1", "Out 2", "Out 3", "Out 4"),
        "Duplicates an incoming stream to multiple independent branches.",
    ),
    NodeType.MUTE: NodeSpec(
        NodeType.MUTE,
        "Mute",
        NodeCategory.PROCESSING,
        ("In",),
        ("Out",),
        "Click-free ramped mute toggle.",
    ),
    NodeType.STEREO_TO_MONO: NodeSpec(
        NodeType.STEREO_TO_MONO,
        "Stereo-to-Mono",
        NodeCategory.PROCESSING,
        ("In",),
        ("Out",),
        "Sums Left + Right into balanced dual-mono.",
    ),
    NodeType.MONO_TO_STEREO: NodeSpec(
        NodeType.MONO_TO_STEREO,
        "Mono-to-Stereo",
        NodeCategory.PROCESSING,
        ("In",),
        ("Out",),
        "Duplicates Left channel onto both Left and Right.",
    ),
    NodeType.CHANNEL_MAP: NodeSpec(
        NodeType.CHANNEL_MAP,
        "Channel Mapping",
        NodeCategory.PROCESSING,
        ("In",),
        ("Out",),
        "Remaps L/R channels (Direct, Swap, Mono Sum, Mute).",
    ),
    NodeType.EQUALIZER: NodeSpec(
        NodeType.EQUALIZER,
        "Equalizer",
        NodeCategory.PROCESSING,
        ("In",),
        ("Out",),
        "5-band stereo parametric/shelving equalizer.",
    ),
    # Audio Output Nodes
    NodeType.SPEAKER_OUTPUT: NodeSpec(
        NodeType.SPEAKER_OUTPUT,
        "Audio Device Output",
        NodeCategory.OUTPUT,
        ("In",),
        (),
        "Outputs audio to any physical playback device (speakers, headphones, monitors, USB DAC).",
    ),
    NodeType.VIRTUAL_OUTPUT: NodeSpec(
        NodeType.VIRTUAL_OUTPUT,
        "Virtual Cable / Mic Output",
        NodeCategory.OUTPUT,
        ("In",),
        (),
        "Routes audio to a Windows virtual playback endpoint paired with a virtual mic for Chrome/Discord/OBS.",
    ),
    NodeType.FILE_RECORDING: NodeSpec(
        NodeType.FILE_RECORDING,
        "File Recording Output",
        NodeCategory.OUTPUT,
        ("In",),
        (),
        "Records real-time stereo 32-bit float audio to a WAV file.",
    ),
}


@dataclass
class NodeModel:
    id: int
    node_type: NodeType
    name: str = ""
    x: float = 0.0
    y: float = 0.0
    device_id: str = ""
    device_name: str = ""
    device_available: bool = True
    pid: int = 0
    process_name: str = ""
    file_path: str = ""
    volume: float = 1.0
    gain_db: float = 0.0
    muted: bool = False
    channel_map: List[int] = field(default_factory=lambda: [0, 1])
    eq_gains_db: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0, 0.0])
    sample_rate: int = 48000
    channels: int = 2
    group_id: Optional[int] = None

    def spec(self) -> NodeSpec:
        return NODE_SPECS[self.node_type]

    def display_title(self) -> str:
        return self.name.strip() or self.spec().title

    def to_engine_dict(self) -> dict:
        return {
            "id": int(self.id),
            "type": int(self.node_type),
            "device_id": self.device_id,
            "pid": int(self.pid),
            "file_path": self.file_path,
            "volume": float(self.volume),
            "gain_db": float(self.gain_db),
            "muted": bool(self.muted),
            "channel_map": [int(self.channel_map[0]), int(self.channel_map[1])],
            "eq_gains_db": [float(v) for v in self.eq_gains_db[:5]],
        }

    def to_project_dict(self) -> dict:
        return {
            "id": int(self.id),
            "type": int(self.node_type),
            "type_name": self.node_type.name,
            "name": self.name,
            "x": float(self.x),
            "y": float(self.y),
            "device_id": self.device_id,
            "device_name": self.device_name,
            "pid": int(self.pid),
            "process_name": self.process_name,
            "file_path": self.file_path,
            "volume": float(self.volume),
            "gain_db": float(self.gain_db),
            "muted": bool(self.muted),
            "channel_map": [int(self.channel_map[0]), int(self.channel_map[1])],
            "eq_gains_db": [float(v) for v in self.eq_gains_db[:5]],
            "sample_rate": int(self.sample_rate),
            "channels": int(self.channels),
            "group_id": self.group_id,
        }

    @classmethod
    def from_project_dict(cls, raw: dict) -> "NodeModel":
        ch_map = list(raw.get("channel_map", [0, 1]))
        if len(ch_map) < 2:
            ch_map = [0, 1]
        eq = list(raw.get("eq_gains_db", [0.0, 0.0, 0.0, 0.0, 0.0]))
        while len(eq) < 5:
            eq.append(0.0)
        raw_type = int(raw["type"])
        legacy_map = {202: int(NodeType.SPEAKER_OUTPUT), 204: int(NodeType.VIRTUAL_OUTPUT)}
        node_type = NodeType(legacy_map.get(raw_type, raw_type))
        return cls(
            id=int(raw["id"]),
            node_type=node_type,
            name=str(raw.get("name", "")),
            x=float(raw.get("x", 0.0)),
            y=float(raw.get("y", 0.0)),
            device_id=str(raw.get("device_id", "")),
            device_name=str(raw.get("device_name", "")),
            device_available=True,
            pid=int(raw.get("pid", 0)),
            process_name=str(raw.get("process_name", "")),
            file_path=str(raw.get("file_path", "")),
            volume=float(raw.get("volume", 1.0)),
            gain_db=float(raw.get("gain_db", 0.0)),
            muted=bool(raw.get("muted", False)),
            channel_map=[int(ch_map[0]), int(ch_map[1])],
            eq_gains_db=[float(x) for x in eq[:5]],
            sample_rate=int(raw.get("sample_rate", 48000)),
            channels=int(raw.get("channels", 2)),
            group_id=raw.get("group_id"),
        )


@dataclass(frozen=True, slots=True)
class ConnectionModel:
    src_node: int
    src_port: int
    dst_node: int
    dst_port: int

    def to_dict(self) -> dict:
        return {
            "src_node": int(self.src_node),
            "src_port": int(self.src_port),
            "dst_node": int(self.dst_node),
            "dst_port": int(self.dst_port),
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "ConnectionModel":
        return cls(
            src_node=int(raw["src_node"]),
            src_port=int(raw.get("src_port", 0)),
            dst_node=int(raw["dst_node"]),
            dst_port=int(raw.get("dst_port", 0)),
        )


@dataclass
class GroupModel:
    id: int
    title: str = "Node Group"
    color: str = "#2A3950"
    node_ids: List[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": int(self.id),
            "title": self.title,
            "color": self.color,
            "node_ids": [int(nid) for nid in self.node_ids],
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "GroupModel":
        return cls(
            id=int(raw["id"]),
            title=str(raw.get("title", "Node Group")),
            color=str(raw.get("color", "#2A3950")),
            node_ids=[int(nid) for nid in raw.get("node_ids", [])],
        )


class ProjectModel:
    """In-memory audio routing graph + JSON project persistence."""

    def __init__(self) -> None:
        self.nodes: Dict[int, NodeModel] = {}
        self.connections: List[ConnectionModel] = []
        self.groups: Dict[int, GroupModel] = {}
        self._next_node_id: int = 1
        self._next_group_id: int = 1

    def clear(self) -> None:
        self.nodes.clear()
        self.connections.clear()
        self.groups.clear()
        self._next_node_id = 1
        self._next_group_id = 1

    def allocate_node_id(self) -> int:
        nid = self._next_node_id
        self._next_node_id += 1
        return nid

    def allocate_group_id(self) -> int:
        gid = self._next_group_id
        self._next_group_id += 1
        return gid

    def add_node(
        self,
        node_type: NodeType,
        x: float = 0.0,
        y: float = 0.0,
        name: str = "",
        node_id: Optional[int] = None,
        **kwargs,
    ) -> NodeModel:
        if node_id is None:
            nid = self.allocate_node_id()
        else:
            nid = int(node_id)
            if nid >= self._next_node_id:
                self._next_node_id = nid + 1
        node = NodeModel(id=nid, node_type=node_type, name=name, x=x, y=y, **kwargs)
        self.nodes[nid] = node
        return node

    def remove_node(self, node_id: int) -> None:
        self.nodes.pop(node_id, None)
        self.connections = [
            c for c in self.connections if c.src_node != node_id and c.dst_node != node_id
        ]
        empty_groups: List[int] = []
        for gid, grp in self.groups.items():
            if node_id in grp.node_ids:
                grp.node_ids = [nid for nid in grp.node_ids if nid != node_id]
                if not grp.node_ids:
                    empty_groups.append(gid)
        for gid in empty_groups:
            self.groups.pop(gid, None)

    def would_create_cycle(
        self, src_node: int, dst_node: int, ignore_connections: Optional[Iterable[ConnectionModel]] = None
    ) -> bool:
        if src_node == dst_node:
            return True
        ignored = set(ignore_connections) if ignore_connections else set()
        adj: Dict[int, List[int]] = {}
        for c in self.connections:
            if c in ignored:
                continue
            adj.setdefault(c.src_node, []).append(c.dst_node)
        # Check whether dst_node can already reach src_node
        visited: Set[int] = {dst_node}
        q: deque[int] = deque([dst_node])
        while q:
            cur = q.popleft()
            if cur == src_node:
                return True
            for nxt in adj.get(cur, ()):
                if nxt not in visited:
                    visited.add(nxt)
                    q.append(nxt)
        return False

    def validate_connection(
        self,
        src_node: int,
        src_port: int,
        dst_node: int,
        dst_port: int,
        ignore_connections: Optional[Iterable[ConnectionModel]] = None,
        allow_replace_input: bool = False,
    ) -> Tuple[bool, str]:
        if src_node not in self.nodes or dst_node not in self.nodes:
            return False, "Source or destination node does not exist."
        if src_node == dst_node:
            return False, "Cannot connect a node to itself."

        sn = self.nodes[src_node]
        dn = self.nodes[dst_node]
        s_spec = sn.spec()
        d_spec = dn.spec()

        if s_spec.category == NodeCategory.OUTPUT or not s_spec.output_ports:
            return False, "Output nodes cannot originate outgoing audio connections."
        if d_spec.category == NodeCategory.SOURCE or not d_spec.input_ports:
            return False, "Source nodes cannot receive incoming audio connections."

        if src_port < 0 or src_port >= len(s_spec.output_ports):
            return False, "Invalid source output port index."
        if dst_port < 0 or dst_port >= len(d_spec.input_ports):
            return False, "Invalid destination input port index."

        ignored = set(ignore_connections) if ignore_connections else set()
        candidate = ConnectionModel(src_node, src_port, dst_node, dst_port)
        if candidate in self.connections and candidate not in ignored:
            return False, "Connection already exists."

        # Input port occupancy & single-input rule for non-mixers (unless replacing existing wire)
        if not allow_replace_input:
            if dn.node_type != NodeType.MIXER:
                existing_in = [
                    c for c in self.connections if c.dst_node == dst_node and c not in ignored
                ]
                if existing_in:
                    return False, "Only Audio Mixer nodes can accept multiple inputs."
            else:
                existing_in_port = [
                    c
                    for c in self.connections
                    if c.dst_node == dst_node and c.dst_port == dst_port and c not in ignored
                ]
                if existing_in_port:
                    return False, f"Mixer input port {dst_port + 1} is already connected."

        if self.would_create_cycle(src_node, dst_node, ignore_connections=ignored):
            return False, "Feedback loops (cycles) are not allowed."

        return True, ""

    def add_connection(
        self, src_node: int, src_port: int, dst_node: int, dst_port: int
    ) -> Tuple[bool, str]:
        ok, err = self.validate_connection(src_node, src_port, dst_node, dst_port)
        if not ok:
            return False, err
        self.connections.append(ConnectionModel(src_node, src_port, dst_node, dst_port))
        return True, ""

    def remove_connection(self, conn: ConnectionModel) -> bool:
        if conn in self.connections:
            self.connections.remove(conn)
            return True
        return False

    def create_group(self, node_ids: Iterable[int], title: str = "Group") -> Optional[GroupModel]:
        valid = [nid for nid in dict.fromkeys(node_ids) if nid in self.nodes]
        if not valid:
            return None
        # Remove from any prior groups first
        for nid in valid:
            old_gid = self.nodes[nid].group_id
            if old_gid is not None and old_gid in self.groups:
                g = self.groups[old_gid]
                if nid in g.node_ids:
                    g.node_ids.remove(nid)
                if not g.node_ids:
                    self.groups.pop(old_gid, None)
        gid = self.allocate_group_id()
        grp = GroupModel(id=gid, title=title, node_ids=valid)
        self.groups[gid] = grp
        for nid in valid:
            self.nodes[nid].group_id = gid
        return grp

    def remove_group(self, group_id: int) -> None:
        grp = self.groups.pop(group_id, None)
        if not grp:
            return
        for nid in grp.node_ids:
            if nid in self.nodes and self.nodes[nid].group_id == group_id:
                self.nodes[nid].group_id = None

    def refresh_device_availability(self, available_device_ids: Set[str]) -> List[NodeModel]:
        """Mark device_available without ever silently replacing missing device IDs."""
        missing: List[NodeModel] = []
        for node in self.nodes.values():
            if not node.device_id:
                node.device_available = True
                continue
            if node.device_id.startswith("test://"):
                node.device_available = True
                continue
            node.device_available = node.device_id in available_device_ids
            if not node.device_available:
                missing.append(node)
        return missing

    def to_engine_payload(self) -> dict:
        return {
            "nodes": [n.to_engine_dict() for n in self.nodes.values()],
            "connections": [c.to_dict() for c in self.connections],
        }

    def to_project_dict(self) -> dict:
        return {
            "version": 1,
            "app": "AudioFlow",
            "nodes": [n.to_project_dict() for n in self.nodes.values()],
            "connections": [c.to_dict() for c in self.connections],
            "groups": [g.to_dict() for g in self.groups.values()],
        }

    def load_project_dict(
        self, data: dict, available_device_ids: Optional[Set[str]] = None
    ) -> List[NodeModel]:
        self.clear()
        for raw_n in data.get("nodes", []):
            node = NodeModel.from_project_dict(raw_n)
            self.nodes[node.id] = node
            if node.id >= self._next_node_id:
                self._next_node_id = node.id + 1

        for raw_c in data.get("connections", []):
            c = ConnectionModel.from_dict(raw_c)
            if c.src_node in self.nodes and c.dst_node in self.nodes:
                self.connections.append(c)

        for raw_g in data.get("groups", []):
            grp = GroupModel.from_dict(raw_g)
            grp.node_ids = [nid for nid in grp.node_ids if nid in self.nodes]
            if grp.node_ids:
                self.groups[grp.id] = grp
                if grp.id >= self._next_group_id:
                    self._next_group_id = grp.id + 1
                for nid in grp.node_ids:
                    self.nodes[nid].group_id = grp.id

        if available_device_ids is not None:
            return self.refresh_device_availability(available_device_ids)
        return []

    def save_file(self, path: Path | str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_project_dict(), indent=2), encoding="utf-8"
        )

    def load_file(
        self, path: Path | str, available_device_ids: Optional[Set[str]] = None
    ) -> List[NodeModel]:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return self.load_project_dict(data, available_device_ids=available_device_ids)

