"""ctypes C ABI bridge to the native C++20 Phonoryx real-time audio engine."""

from __future__ import annotations

import ctypes
import json
import os
import sys
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Dict, List, Optional


class DataFlow(IntEnum):
    RENDER = 0
    CAPTURE = 1


class DeviceRoleHint(IntEnum):
    UNKNOWN = 0
    SPEAKERS = 1
    HEADPHONES = 2
    MICROPHONE = 3
    LINE_IN = 4
    VIRTUAL = 5


class NodeType(IntEnum):
    # Audio Source Nodes
    APP_CAPTURE = 1
    SYSTEM_CAPTURE = 2
    MIC_INPUT = 3
    VIRTUAL_INPUT = 5

    # Audio Processing Nodes
    VOLUME = 101
    GAIN = 102
    MIXER = 103
    SPLITTER = 104
    MUTE = 105
    STEREO_TO_MONO = 107
    MONO_TO_STEREO = 108
    CHANNEL_MAP = 109
    EQUALIZER = 110

    # Audio Output Nodes
    SPEAKER_OUTPUT = 201
    VIRTUAL_OUTPUT = 203
    FILE_RECORDING = 205


class _AfDeviceInfo(ctypes.Structure):
    _fields_ = [
        ("id", ctypes.c_char * 256),
        ("name", ctypes.c_char * 256),
        ("flow", ctypes.c_int32),
        ("role_hint", ctypes.c_int32),
        ("state", ctypes.c_uint32),
        ("sample_rate", ctypes.c_uint32),
        ("channels", ctypes.c_uint16),
        ("is_default", ctypes.c_uint16),
        ("is_virtual", ctypes.c_uint16),
        ("reserved", ctypes.c_uint16),
    ]


class _AfProcessInfo(ctypes.Structure):
    _fields_ = [
        ("pid", ctypes.c_uint32),
        ("parent_pid", ctypes.c_uint32),
        ("exe_name", ctypes.c_char * 128),
        ("exe_path", ctypes.c_char * 260),
        ("window_title", ctypes.c_char * 256),
        ("has_audio_session", ctypes.c_uint16),
        ("reserved", ctypes.c_uint16),
        ("session_peak", ctypes.c_float),
    ]


class _AfNodeMeter(ctypes.Structure):
    _fields_ = [
        ("node_id", ctypes.c_uint64),
        ("peak_left", ctypes.c_float),
        ("peak_right", ctypes.c_float),
        ("rms_left", ctypes.c_float),
        ("rms_right", ctypes.c_float),
        ("active", ctypes.c_uint32),
        ("xruns", ctypes.c_uint32),
    ]


class _AfEngineStats(ctypes.Structure):
    _fields_ = [
        ("running", ctypes.c_uint32),
        ("sample_rate", ctypes.c_uint32),
        ("block_frames", ctypes.c_uint32),
        ("active_nodes", ctypes.c_uint32),
        ("active_connections", ctypes.c_uint32),
        ("virtual_endpoints", ctypes.c_uint32),
        ("total_xruns", ctypes.c_uint32),
        ("cpu_load_percent", ctypes.c_float),
        ("processed_blocks", ctypes.c_uint64),
        ("device_change_seq", ctypes.c_uint64),
    ]


class _AfLogEntry(ctypes.Structure):
    _fields_ = [
        ("timestamp_ms", ctypes.c_uint64),
        ("level", ctypes.c_int32),
        ("node_id", ctypes.c_uint64),
        ("message", ctypes.c_char * 256),
    ]


@dataclass(slots=True)
class DeviceInfo:
    id: str
    name: str
    flow: DataFlow
    role_hint: DeviceRoleHint
    state: int
    sample_rate: int
    channels: int
    is_default: bool
    is_virtual: bool


@dataclass(slots=True)
class ProcessInfo:
    pid: int
    parent_pid: int
    exe_name: str
    exe_path: str
    window_title: str
    has_audio_session: bool
    session_peak: float


@dataclass(slots=True)
class NodeMeter:
    node_id: int
    peak_left: float
    peak_right: float
    rms_left: float
    rms_right: float
    active: bool
    xruns: int


@dataclass(slots=True)
class EngineStats:
    running: bool
    sample_rate: int
    block_frames: int
    active_nodes: int
    active_connections: int
    virtual_endpoints: int
    total_xruns: int
    cpu_load_percent: float
    processed_blocks: int
    device_change_seq: int


@dataclass(slots=True)
class LogEntry:
    timestamp_ms: int
    level: int
    node_id: int
    message: str


def find_engine_dll() -> Path:
    env_path = os.environ.get("PHONORYX_ENGINE_DLL")
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return p

    candidates: List[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        exe_dir = Path(sys.executable).resolve().parent
        if meipass:
            mp = Path(meipass)
            candidates.extend([
                mp / "phonoryx_engine.dll",
                mp / "assets" / "bin" / "phonoryx_engine.dll",
            ])
        candidates.extend([
            exe_dir / "phonoryx_engine.dll",
            exe_dir / "assets" / "bin" / "phonoryx_engine.dll",
        ])

    root = Path(__file__).resolve().parent.parent
    candidates.extend([
        root / "build" / "engine" / "bin" / "Release" / "phonoryx_engine.dll",
        root / "build" / "engine" / "bin" / "Debug" / "phonoryx_engine.dll",
        root / "build" / "engine" / "bin" / "phonoryx_engine.dll",
        root / "assets" / "bin" / "phonoryx_engine.dll",
        Path(__file__).resolve().parent / "phonoryx_engine.dll",
    ])
    for c in candidates:
        if c.is_file():
            return c
    raise FileNotFoundError(
        "Could not find phonoryx_engine.dll. Ensure assets/bin/phonoryx_engine.dll is present "
        "or build engine/ with CMake first "
        f"(checked: {', '.join(str(x) for x in candidates)})"
    )


def _decode_cstr(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace").rstrip("\x00")


class PhonoryxEngine:
    """Thread-safe high-level wrapper over the native C++ audio engine DLL."""

    def __init__(
        self,
        sample_rate: int = 48000,
        block_frames: int = 256,
        dll_path: Optional[Path] = None,
    ) -> None:
        resolved = dll_path if dll_path is not None else find_engine_dll()
        self._lib = ctypes.CDLL(str(resolved))
        self._bind_signatures()
        self._handle = self._lib.af_engine_create(
            ctypes.c_uint32(sample_rate), ctypes.c_uint32(block_frames)
        )
        if not self._handle:
            raise RuntimeError("af_engine_create returned NULL")

    def _bind_signatures(self) -> None:
        lib = self._lib
        c_void_p = ctypes.c_void_p

        lib.af_engine_create.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        lib.af_engine_create.restype = c_void_p

        lib.af_engine_destroy.argtypes = [c_void_p]
        lib.af_engine_destroy.restype = None

        lib.af_engine_start.argtypes = [c_void_p]
        lib.af_engine_start.restype = ctypes.c_int32

        lib.af_engine_stop.argtypes = [c_void_p]
        lib.af_engine_stop.restype = None

        lib.af_enumerate_devices.argtypes = [
            c_void_p,
            ctypes.POINTER(_AfDeviceInfo),
            ctypes.c_int32,
        ]
        lib.af_enumerate_devices.restype = ctypes.c_int32

        lib.af_get_device_change_seq.argtypes = [c_void_p]
        lib.af_get_device_change_seq.restype = ctypes.c_uint64

        lib.af_enumerate_processes.argtypes = [
            c_void_p,
            ctypes.POINTER(_AfProcessInfo),
            ctypes.c_int32,
        ]
        lib.af_enumerate_processes.restype = ctypes.c_int32

        lib.af_graph_apply_json.argtypes = [
            c_void_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_int32,
        ]
        lib.af_graph_apply_json.restype = ctypes.c_int32

        lib.af_node_set_float_param.argtypes = [
            c_void_p,
            ctypes.c_uint64,
            ctypes.c_char_p,
            ctypes.c_float,
        ]
        lib.af_node_set_float_param.restype = ctypes.c_int32

        lib.af_node_set_int_param.argtypes = [
            c_void_p,
            ctypes.c_uint64,
            ctypes.c_char_p,
            ctypes.c_int32,
        ]
        lib.af_node_set_int_param.restype = ctypes.c_int32

        lib.af_get_node_meters.argtypes = [
            c_void_p,
            ctypes.POINTER(_AfNodeMeter),
            ctypes.c_int32,
        ]
        lib.af_get_node_meters.restype = ctypes.c_int32

        lib.af_get_engine_stats.argtypes = [
            c_void_p,
            ctypes.POINTER(_AfEngineStats),
        ]
        lib.af_get_engine_stats.restype = ctypes.c_int32

        lib.af_poll_logs.argtypes = [
            c_void_p,
            ctypes.POINTER(_AfLogEntry),
            ctypes.c_int32,
        ]
        lib.af_poll_logs.restype = ctypes.c_int32

    def close(self) -> None:
        if self._handle:
            self._lib.af_engine_stop(self._handle)
            self._lib.af_engine_destroy(self._handle)
            self._handle = None

    def __enter__(self) -> "PhonoryxEngine":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def start(self) -> bool:
        if not self._handle:
            return False
        return self._lib.af_engine_start(self._handle) == 0

    def stop(self) -> None:
        if self._handle:
            self._lib.af_engine_stop(self._handle)

    @property
    def is_running(self) -> bool:
        return bool(self._handle and self.get_engine_stats().running)

    def enumerate_devices(self, max_devices: int = 128) -> List[DeviceInfo]:
        if not self._handle:
            return []
        buf = (_AfDeviceInfo * max_devices)()
        count = self._lib.af_enumerate_devices(self._handle, buf, max_devices)
        out: List[DeviceInfo] = []
        for i in range(max(0, count)):
            d = buf[i]
            out.append(
                DeviceInfo(
                    id=_decode_cstr(d.id),
                    name=_decode_cstr(d.name),
                    flow=DataFlow(d.flow),
                    role_hint=DeviceRoleHint(d.role_hint),
                    state=int(d.state),
                    sample_rate=int(d.sample_rate),
                    channels=int(d.channels),
                    is_default=bool(d.is_default),
                    is_virtual=bool(d.is_virtual),
                )
            )
        return out

    def device_change_seq(self) -> int:
        if not self._handle:
            return 0
        return int(self._lib.af_get_device_change_seq(self._handle))

    def enumerate_processes(self, max_processes: int = 512) -> List[ProcessInfo]:
        if not self._handle:
            return []
        buf = (_AfProcessInfo * max_processes)()
        count = self._lib.af_enumerate_processes(self._handle, buf, max_processes)
        out: List[ProcessInfo] = []
        for i in range(max(0, count)):
            p = buf[i]
            out.append(
                ProcessInfo(
                    pid=int(p.pid),
                    parent_pid=int(p.parent_pid),
                    exe_name=_decode_cstr(p.exe_name),
                    exe_path=_decode_cstr(p.exe_path),
                    window_title=_decode_cstr(p.window_title),
                    has_audio_session=bool(p.has_audio_session),
                    session_peak=float(p.session_peak),
                )
            )
        return out

    def apply_graph(self, graph_payload: dict) -> tuple[bool, str]:
        if not self._handle:
            return False, "Engine closed"
        payload_bytes = json.dumps(graph_payload).encode("utf-8")
        err_buf = ctypes.create_string_buffer(512)
        rc = self._lib.af_graph_apply_json(self._handle, payload_bytes, err_buf, 512)
        return rc == 0, err_buf.value.decode("utf-8", errors="replace")

    def set_float_param(self, node_id: int, param_name: str, value: float) -> bool:
        if not self._handle:
            return False
        rc = self._lib.af_node_set_float_param(
            self._handle,
            ctypes.c_uint64(node_id),
            param_name.encode("utf-8"),
            ctypes.c_float(value),
        )
        return rc == 0

    def set_int_param(self, node_id: int, param_name: str, value: int) -> bool:
        if not self._handle:
            return False
        rc = self._lib.af_node_set_int_param(
            self._handle,
            ctypes.c_uint64(node_id),
            param_name.encode("utf-8"),
            ctypes.c_int32(value),
        )
        return rc == 0

    def get_node_meters(self, max_meters: int = 256) -> Dict[int, NodeMeter]:
        if not self._handle:
            return {}
        buf = (_AfNodeMeter * max_meters)()
        count = self._lib.af_get_node_meters(self._handle, buf, max_meters)
        out: Dict[int, NodeMeter] = {}
        for i in range(max(0, count)):
            m = buf[i]
            out[int(m.node_id)] = NodeMeter(
                node_id=int(m.node_id),
                peak_left=float(m.peak_left),
                peak_right=float(m.peak_right),
                rms_left=float(m.rms_left),
                rms_right=float(m.rms_right),
                active=bool(m.active),
                xruns=int(m.xruns),
            )
        return out

    def get_engine_stats(self) -> EngineStats:
        stats = _AfEngineStats()
        if self._handle:
            self._lib.af_get_engine_stats(self._handle, ctypes.byref(stats))
        return EngineStats(
            running=bool(stats.running),
            sample_rate=int(stats.sample_rate),
            block_frames=int(stats.block_frames),
            active_nodes=int(stats.active_nodes),
            active_connections=int(stats.active_connections),
            virtual_endpoints=int(stats.virtual_endpoints),
            total_xruns=int(stats.total_xruns),
            cpu_load_percent=float(stats.cpu_load_percent),
            processed_blocks=int(stats.processed_blocks),
            device_change_seq=int(stats.device_change_seq),
        )

    def poll_logs(self, max_logs: int = 64) -> List[LogEntry]:
        if not self._handle:
            return []
        buf = (_AfLogEntry * max_logs)()
        count = self._lib.af_poll_logs(self._handle, buf, max_logs)
        return [
            LogEntry(
                timestamp_ms=int(buf[i].timestamp_ms),
                level=int(buf[i].level),
                node_id=int(buf[i].node_id),
                message=_decode_cstr(buf[i].message),
            )
            for i in range(max(0, count))
        ]

