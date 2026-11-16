from __future__ import annotations

import logging
import math
import os
from typing import Any, Dict, Optional, Tuple

import numpy as np
try:
    from PySide6.QtCore import QObject, QThread, Signal
except ImportError:
    from PyQt6.QtCore import QObject, QThread, Signal

logger = logging.getLogger("phonoryx.player_service")


def resample_audio(audio: np.ndarray, orig_sr: int, target_sr: int = 48000) -> np.ndarray:
    """Resample (N, channels) audio array from orig_sr to target_sr using polyphase filtering or interpolation."""
    if orig_sr == target_sr or len(audio) == 0:
        return audio

    try:
        from scipy.signal import resample_poly
        g = math.gcd(int(orig_sr), int(target_sr))
        up = target_sr // g
        down = orig_sr // g
        if audio.ndim == 1:
            return resample_poly(audio, up, down).astype(np.float32)
        elif audio.ndim == 2:
            channels = [resample_poly(audio[:, c], up, down).astype(np.float32) for c in range(audio.shape[1])]
            return np.column_stack(channels)
    except Exception:
        pass

    # High quality linear interpolation fallback
    num_target_samples = int(round(len(audio) * float(target_sr) / float(orig_sr)))
    orig_indices = np.linspace(0.0, float(len(audio) - 1), len(audio))
    target_indices = np.linspace(0.0, float(len(audio) - 1), num_target_samples)

    if audio.ndim == 1:
        return np.interp(target_indices, orig_indices, audio).astype(np.float32)

    channels = [
        np.interp(target_indices, orig_indices, audio[:, c]).astype(np.float32)
        for c in range(audio.shape[1])
    ]
    return np.column_stack(channels)


def load_audio_file(file_path: str, target_sr: int = 48000) -> Tuple[np.ndarray, int]:
    """Load an audio file (.wav, .mp3, .flac, .ogg, .aiff, etc.) and convert to interleaved stereo float32 at target_sr.

    Returns:
        (stereo_audio, sample_rate) where stereo_audio is shape (frames, 2) in range [-1.0, 1.0].
    """
    if not file_path or not os.path.isfile(file_path):
        raise FileNotFoundError(f"Audio file not found: {file_path}")

    # 1. Primary loader: soundfile
    try:
        import soundfile as sf
        data, sr = sf.read(file_path, dtype="float32", always_2d=True)
    except Exception as sf_err:
        # 2. Fallback loader: scipy.io.wavfile (for WAV files)
        try:
            import scipy.io.wavfile as wav
            sr, raw_data = wav.read(file_path)
            if raw_data.dtype == np.int16:
                data = (raw_data / 32768.0).astype(np.float32)
            elif raw_data.dtype == np.int32:
                data = (raw_data / 2147483648.0).astype(np.float32)
            elif raw_data.dtype == np.uint8:
                data = ((raw_data - 128) / 128.0).astype(np.float32)
            else:
                data = raw_data.astype(np.float32)
            if data.ndim == 1:
                data = np.column_stack((data, data))
        except Exception:
            raise RuntimeError(f"Unable to decode audio file {file_path}: {sf_err}") from sf_err

    # Standardize to 2 channels
    if data.ndim == 1:
        data = np.column_stack((data, data))
    elif data.shape[1] == 1:
        data = np.column_stack((data[:, 0], data[:, 0]))
    elif data.shape[1] > 2:
        data = data[:, :2]

    # Resample to engine rate (48000 Hz)
    if sr != target_sr:
        data = resample_audio(data, sr, target_sr)

    # Soft clamp safely to [-1.0, 1.0]
    data = np.clip(data, -1.0, 1.0).astype(np.float32)
    return data, target_sr


class AudioPlayerWorker(QThread):
    """Background streaming worker that pushes audio chunks into the engine ring buffer."""

    playback_started = Signal(int)             # node_id
    playback_progress = Signal(int, float)     # node_id, progress (0.0 to 1.0)
    playback_finished = Signal(int)            # node_id
    playback_stopped = Signal(int)             # node_id
    playback_error = Signal(int, str)          # node_id, error message

    def __init__(
        self,
        engine: Any,
        node_id: int,
        audio_data: np.ndarray,
        sample_rate: int = 48000,
        loop: bool = False,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.engine = engine
        self.node_id = node_id
        self.audio_data = audio_data
        self.sample_rate = sample_rate
        self.loop = loop
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def set_loop(self, loop: bool) -> None:
        self.loop = bool(loop)

    def run(self) -> None:
        try:
            total_frames = len(self.audio_data)
            if total_frames == 0 or self.engine is None:
                self.playback_finished.emit(self.node_id)
                return

            self.playback_started.emit(self.node_id)

            # Clear any previously queued audio in the engine
            self.engine.clear_source_audio(self.node_id)

            chunk_size = 4800  # 100 ms chunks @ 48 kHz
            idx = 0

            while not self._cancelled:
                end = min(idx + chunk_size, total_frames)
                chunk = self.audio_data[idx:end]
                if len(chunk) > 0:
                    self.engine.push_source_audio(self.node_id, chunk)

                progress = float(end) / float(total_frames) if total_frames > 0 else 1.0
                self.playback_progress.emit(self.node_id, progress)

                idx = end
                if idx >= total_frames:
                    if self.loop and not self._cancelled:
                        idx = 0
                    else:
                        break

                # Sleep 70 ms while 100 ms of audio plays to keep the ring buffer populated
                self.msleep(70)

            if self._cancelled:
                self.playback_stopped.emit(self.node_id)
            else:
                self.playback_finished.emit(self.node_id)

        except Exception as exc:
            logger.exception("Audio player error on node %d: %s", self.node_id, exc)
            self.playback_error.emit(self.node_id, str(exc))


class AudioPlayerManager(QObject):
    """Singleton manager orchestrating active audio file playback workers."""

    playback_started = Signal(int)
    playback_progress = Signal(int, float)
    playback_finished = Signal(int)
    playback_stopped = Signal(int)
    playback_error = Signal(int, str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._active_workers: Dict[int, AudioPlayerWorker] = {}
        self._cached_audio: Dict[str, Tuple[np.ndarray, int]] = {}

    def is_playing(self, node_id: int) -> bool:
        w = self._active_workers.get(node_id)
        return w is not None and w.isRunning()

    def get_audio_data(self, file_path: str) -> Tuple[np.ndarray, int]:
        """Get preloaded/cached audio data or load it from disk."""
        norm_path = os.path.abspath(file_path)
        if norm_path not in self._cached_audio:
            data, sr = load_audio_file(norm_path)
            self._cached_audio[norm_path] = (data, sr)
        return self._cached_audio[norm_path]

    def play(
        self,
        engine: Any,
        node_id: int,
        file_path: str,
        loop: bool = False,
    ) -> bool:
        """Trigger immediate audio playback.
        
        Every time play is called, any current stream is cleared and restarts from frame 0 instantly.
        """
        # Stop existing worker on this node if any
        self.stop(engine, node_id)

        if not file_path or not os.path.isfile(file_path):
            self.playback_error.emit(node_id, f"File not found: {file_path}")
            return False

        try:
            audio_data, sample_rate = self.get_audio_data(file_path)
        except Exception as exc:
            self.playback_error.emit(node_id, str(exc))
            return False

        worker = AudioPlayerWorker(
            engine=engine,
            node_id=node_id,
            audio_data=audio_data,
            sample_rate=sample_rate,
            loop=loop,
            parent=self,
        )
        worker.playback_started.connect(self._on_started)
        worker.playback_progress.connect(self._on_progress)
        worker.playback_finished.connect(self._on_finished)
        worker.playback_stopped.connect(self._on_stopped)
        worker.playback_error.connect(self._on_error)
        worker.finished.connect(lambda: self._cleanup(node_id))

        self._active_workers[node_id] = worker
        worker.start()
        return True

    def set_loop(self, node_id: int, loop: bool) -> None:
        """Dynamically toggle looping on a running worker."""
        worker = self._active_workers.get(node_id)
        if worker is not None:
            worker.set_loop(loop)

    def stop(self, engine: Any, node_id: int) -> None:
        """Stop playback and clear the engine buffer for this node."""
        worker = self._active_workers.pop(node_id, None)
        if worker and worker.isRunning():
            worker.cancel()
            worker.wait(100)
        if engine:
            try:
                engine.clear_source_audio(node_id)
            except Exception:
                pass
        self.playback_stopped.emit(node_id)

    def _on_started(self, node_id: int) -> None:
        self.playback_started.emit(node_id)

    def _on_progress(self, node_id: int, progress: float) -> None:
        self.playback_progress.emit(node_id, progress)

    def _on_finished(self, node_id: int) -> None:
        self.playback_finished.emit(node_id)

    def _on_stopped(self, node_id: int) -> None:
        self.playback_stopped.emit(node_id)

    def _on_error(self, node_id: int, err: str) -> None:
        self.playback_error.emit(node_id, err)

    def _cleanup(self, node_id: int) -> None:
        self._active_workers.pop(node_id, None)


_GLOBAL_PLAYER_MANAGER: Optional[AudioPlayerManager] = None


def get_player_manager() -> AudioPlayerManager:
    global _GLOBAL_PLAYER_MANAGER
    if _GLOBAL_PLAYER_MANAGER is None:
        _GLOBAL_PLAYER_MANAGER = AudioPlayerManager()
    return _GLOBAL_PLAYER_MANAGER
