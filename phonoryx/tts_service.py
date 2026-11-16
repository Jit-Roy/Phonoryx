"""Text-to-Speech (TTS) inference service, worker threads, and voice profile discovery for Phonoryx."""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PySide6.QtCore import QObject, QThread, Signal

logger = logging.getLogger("phonoryx.tts")

DEFAULT_MODEL_DIR = r"D:\Personal Projects\Edge Audio\model"
DEFAULT_VOICE_DIR = r"D:\Personal Projects\Edge Audio\voice_cache"

_MODEL_LOCK = threading.Lock()
_CACHED_PROCESSOR: Any = None
_CACHED_MODEL: Any = None
_CACHED_DEVICE: Optional[str] = None


def resolve_model_dir(custom_path: Optional[str] = None) -> Path:
    if custom_path and os.path.isdir(custom_path):
        return Path(custom_path)
    env_dir = os.environ.get("PHONORYX_TTS_MODEL_DIR")
    if env_dir and os.path.isdir(env_dir):
        return Path(env_dir)
    return Path(DEFAULT_MODEL_DIR)


def resolve_voice_dir(custom_path: Optional[str] = None) -> Path:
    if custom_path and os.path.isdir(custom_path):
        return Path(custom_path)
    env_dir = os.environ.get("PHONORYX_TTS_VOICE_DIR")
    if env_dir and os.path.isdir(env_dir):
        return Path(env_dir)
    return Path(DEFAULT_VOICE_DIR)


def list_voice_profiles(voice_dir: Optional[str] = None) -> List[Dict[str, str]]:
    """Scan voice cache directory for cloned voice profiles (.pt files)."""
    p = resolve_voice_dir(voice_dir)
    profiles: List[Dict[str, str]] = []
    if not p.is_dir():
        return profiles

    for item in sorted(p.iterdir()):
        if item.is_file() and item.suffix.lower() == ".pt":
            name = item.stem.replace("_", " ").title()
            profiles.append({
                "name": name,
                "filename": item.name,
                "path": str(item.resolve()),
            })
    return profiles


def is_tts_enabled() -> bool:
    """Return whether the TTS node and inference services are active in the current build/environment."""
    import sys
    if getattr(sys, "_PHONORYX_LITE", False):
        return False
    if os.environ.get("PHONORYX_LITE", "0") in ("1", "true", "True"):
        return False
    return True


def ensure_torch_environment() -> bool:
    """Ensure PyTorch and Transformers can be imported even when running in a standalone PyInstaller bundle."""
    try:
        import torch
        return True
    except ImportError:
        pass

    import sys
    candidates = [
        r"D:\Anaconda\Lib\site-packages",
        os.path.join(os.environ.get("CONDA_PREFIX", ""), "Lib", "site-packages") if os.environ.get("CONDA_PREFIX") else "",
    ]
    for cp in candidates:
        if cp and os.path.isdir(cp):
            t_lib = os.path.join(cp, "torch", "lib")
            if os.path.isdir(t_lib):
                try:
                    os.add_dll_directory(t_lib)
                except Exception:
                    pass
            if cp not in sys.path:
                sys.path.append(cp)
            try:
                import torch
                logger.info("Successfully hooked host PyTorch from: %s", cp)
                return True
            except Exception:
                pass
    return False


def extract_reference_text_from_profile(profile_path: str) -> str:
    """Read and return cached reference transcription text from a .pt voice profile if present."""
    if not profile_path or not os.path.isfile(profile_path):
        return ""
    if profile_path.lower().endswith(".pt"):
        try:
            ensure_torch_environment()
            import torch
            data = torch.load(profile_path, weights_only=False)
            if isinstance(data, dict):
                return str(data.get("reference_text", "")).strip()
        except Exception:
            pass
    return ""


def sanitize_filename(text: str, max_chars: int = 80) -> str:
    """Sanitize prompt transcript text into a safe, valid filesystem filename."""
    import re
    cleaned = re.sub(r'[\\/*?:"<>|\r\n\t]+', " ", text).strip()
    cleaned = re.sub(r'\s+', ' ', cleaned)
    if not cleaned:
        cleaned = "tts_output"
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip(" ._")
    return cleaned


def get_tts_model(model_dir: Optional[str] = None) -> Tuple[Any, Any, str]:
    """Lazy thread-safe loader for the TTS model and processor."""
    global _CACHED_PROCESSOR, _CACHED_MODEL, _CACHED_DEVICE

    with _MODEL_LOCK:
        if _CACHED_MODEL is not None and _CACHED_PROCESSOR is not None:
            return _CACHED_PROCESSOR, _CACHED_MODEL, _CACHED_DEVICE or "cpu"

        ensure_torch_environment()
        import torch
        from transformers import AutoModel, AutoProcessor

        m_path = resolve_model_dir(model_dir)
        if not m_path.exists():
            raise FileNotFoundError(f"TTS model directory not found at: {m_path}")

        device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.bfloat16 if device == "cuda" else torch.float32

        logger.info("Loading TTS processor from %s (device: %s, dtype: %s)...", m_path, device, dtype)
        processor = AutoProcessor.from_pretrained(str(m_path), trust_remote_code=True)
        model = AutoModel.from_pretrained(
            str(m_path),
            trust_remote_code=True,
            dtype=dtype,
        ).eval().to(device)

        _CACHED_PROCESSOR = processor
        _CACHED_MODEL = model
        _CACHED_DEVICE = device
        logger.info("TTS model loaded successfully.")
        return processor, model, device


def prepare_reference_audio(source_path: str) -> str:
    """Converts any reference audio (.mp3, .wav, .flac, .ogg, etc.) to a standardized 44.1 kHz mono WAV on the go.

    Returns the path to the converted .wav file to be passed into the TTS processor pipeline.
    """
    import tempfile
    import soundfile as sf
    from phonoryx.player_service import load_audio_file

    source = Path(source_path)
    if not source.is_file():
        raise FileNotFoundError(f"Reference audio file not found: {source_path}")

    # Load and resample to 44.1 kHz float32
    audio_data, _ = load_audio_file(str(source), target_sr=44100)
    # Downmix to mono if multi-channel
    if audio_data.ndim == 2 and audio_data.shape[1] > 1:
        mono_data = np.mean(audio_data, axis=1)
    else:
        mono_data = audio_data.squeeze()

    # Peak-normalize if clipping
    peak = float(np.max(np.abs(mono_data))) if mono_data.size > 0 else 0.0
    if peak > 1.0:
        mono_data = mono_data / peak

    # Write to a deterministic temporary WAV in temp dir
    temp_dir = Path(tempfile.gettempdir()) / "phonoryx_ref_audio"
    temp_dir.mkdir(parents=True, exist_ok=True)
    target_wav = temp_dir / f"{source.stem}_ref_44100.wav"
    sf.write(str(target_wav), mono_data.astype(np.float32), 44100, subtype="PCM_16")
    logger.info("Prepared reference audio on the fly: %s -> %s (44.1 kHz mono WAV)", source_path, target_wav)
    return str(target_wav)


def synthesize_speech(
    text: str,
    voice_profile_path: Optional[str] = None,
    reference_text: Optional[str] = None,
    temperature: float = 0.8,
    top_p: float = 0.95,
    top_k: int = 50,
    max_new_tokens: int = 1024,
    target_sample_rate: int = 48000,
    model_dir: Optional[str] = None,
) -> Tuple[np.ndarray, int]:
    """Synthesizes speech from prompt text and returns interleaved stereo float32 array at target_sample_rate."""
    ensure_torch_environment()
    import torch
    import torchaudio

    prompt = (text or "").strip()
    if not prompt:
        raise ValueError("Synthesis text prompt cannot be empty.")

    processor, model, device = get_tts_model(model_dir)

    # Prepare inputs with optional voice cloning profile or reference audio file
    if voice_profile_path and os.path.exists(voice_profile_path):
        v_lower = voice_profile_path.lower()
        if v_lower.endswith(".pt"):
            logger.info("Synthesizing with .pt voice profile: %s", voice_profile_path)
            profile = torch.load(voice_profile_path, weights_only=False)
            ref_txt = (reference_text or "").strip() or str(profile.get("reference_text", "")).strip()
            if not ref_txt:
                raise ValueError("reference_text (transcription of reference voice) is required for voice cloning.")
            if "reference_codes" in profile:
                inputs = processor(
                    text=[prompt],
                    reference_text=[ref_txt],
                    reference_codes=[profile["reference_codes"]],
                    return_tensors="pt",
                )
            elif "reference_audio" in profile:
                inputs = processor(
                    text=[prompt],
                    reference_text=[ref_txt],
                    reference_audio=[profile["reference_audio"]],
                    sampling_rate=44100,
                    return_tensors="pt",
                )
            else:
                raise ValueError(f"Unrecognized keys in voice profile {voice_profile_path}: {list(profile.keys())}")
        else:
            # Custom reference audio file (.wav, .mp3, .flac, .ogg, etc.) converted to 44.1 kHz mono WAV on the fly
            logger.info("Synthesizing with reference audio file: %s", voice_profile_path)
            ref_txt = (reference_text or "").strip()
            if not ref_txt:
                raise ValueError("Ref Text (transcription of reference audio) is required when using an audio file.")
            ref_wav_path = prepare_reference_audio(voice_profile_path)
            inputs = processor(
                text=[prompt],
                reference_text=[ref_txt],
                reference_audio=[ref_wav_path],
                sampling_rate=44100,
                return_tensors="pt",
            )
    else:
        logger.info("Synthesizing with base/default voice profile")
        inputs = processor(text=[prompt], return_tensors="pt")

    inputs = {k: v.to(device) for k, v in inputs.items()}

    with torch.inference_mode():
        output = model.generate(
            **inputs,
            max_new_tokens=int(max_new_tokens),
            temperature=float(temperature),
            top_p=float(top_p),
            top_k=int(top_k),
            do_sample=True,
            return_dict_in_generate=True,
        )
        waveforms, waveform_lengths = model.decode_audio(output.codes)

    # 1D audio waveform tensor on CPU
    audio = waveforms[0, : int(waveform_lengths[0])].float().cpu()
    codec_sr = getattr(model.config, "codec_sample_rate", 44100)

    # Resample to engine rate (e.g. 44.1 kHz -> 48.0 kHz)
    if codec_sr != target_sample_rate:
        audio = torchaudio.functional.resample(audio.unsqueeze(0), codec_sr, target_sample_rate).squeeze(0)

    mono_np = audio.numpy().astype(np.float32)
    # Convert mono (N,) to interleaved stereo (N, 2)
    stereo_np = np.column_stack((mono_np, mono_np)).astype(np.float32)
    return np.ascontiguousarray(stereo_np), target_sample_rate


class TTSWorker(QThread):
    """Background worker for non-blocking speech generation and real-time audio injection."""

    generation_started = Signal(int)              # node_id
    generation_progress = Signal(int, str)         # node_id, message
    generation_finished = Signal(int, int, float)  # node_id, frames_pushed, duration_s
    generation_error = Signal(int, str)            # node_id, error_message
    generation_stopped = Signal(int)              # node_id

    def __init__(
        self,
        engine: Any,
        node_id: int,
        text: str,
        voice_profile_path: Optional[str] = None,
        reference_text: Optional[str] = None,
        temperature: float = 0.8,
        top_p: float = 0.95,
        top_k: int = 50,
        max_new_tokens: int = 1024,
        output_dir: Optional[str] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.engine = engine
        self.node_id = node_id
        self.text = text
        self.voice_profile_path = voice_profile_path
        self.reference_text = reference_text
        self.temperature = temperature
        self.top_p = top_p
        self.top_k = top_k
        self.max_new_tokens = max_new_tokens
        self.output_dir = output_dir
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            self.generation_started.emit(self.node_id)
            self.generation_progress.emit(self.node_id, "Synthesizing neural audio...")

            stereo_audio, sample_rate = synthesize_speech(
                text=self.text,
                voice_profile_path=self.voice_profile_path,
                reference_text=self.reference_text,
                temperature=self.temperature,
                top_p=self.top_p,
                top_k=self.top_k,
                max_new_tokens=self.max_new_tokens,
                target_sample_rate=48000,
            )

            if self._cancelled:
                self.generation_stopped.emit(self.node_id)
                return

            frames = len(stereo_audio)
            duration_s = frames / float(sample_rate)

            # Auto-save synthesized WAV file if output_dir is configured
            if self.output_dir and os.path.isdir(self.output_dir):
                safe_name = sanitize_filename(self.text)
                save_path = os.path.join(self.output_dir, f"{safe_name}.wav")
                try:
                    import soundfile as sf
                    sf.write(save_path, stereo_audio, sample_rate)
                    logger.info("Saved TTS synthesized WAV to %s", save_path)
                except Exception as save_err:
                    logger.warning("Failed saving audio to %s: %s", save_path, save_err)

            # Cache audio on manager for instant zero-overhead replay
            p = self.parent()
            if p is not None and hasattr(p, "cache_audio"):
                p.cache_audio(self.node_id, stereo_audio, sample_rate)

            # Stream / push directly into Phonoryx engine ring buffer
            if self.engine:
                self.generation_progress.emit(self.node_id, f"Streaming {frames} frames to engine...")
                pushed = self.engine.push_source_audio(self.node_id, stereo_audio)
                logger.info("Pushed %d / %d frames to TTS node %d", pushed, frames, self.node_id)

            self.generation_finished.emit(self.node_id, frames, duration_s)

        except Exception as exc:
            logger.exception("TTS synthesis failed for node %d: %s", self.node_id, exc)
            self.generation_error.emit(self.node_id, str(exc))


class TTSServiceManager(QObject):
    """Central singleton manager orchestrating active TTS workers."""

    worker_started = Signal(int)
    worker_progress = Signal(int, str)
    worker_finished = Signal(int, int, float)
    worker_error = Signal(int, str)
    worker_stopped = Signal(int)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._active_workers: Dict[int, TTSWorker] = {}
        self._cached_audio: Dict[int, Tuple[np.ndarray, int]] = {}

    def is_generating(self, node_id: int) -> bool:
        w = self._active_workers.get(node_id)
        return w is not None and w.isRunning()

    def cache_audio(self, node_id: int, audio: np.ndarray, sample_rate: int) -> None:
        self._cached_audio[node_id] = (audio, sample_rate)

    def has_cached_audio(self, node_id: int) -> bool:
        return node_id in self._cached_audio

    def replay(self, engine: Any, node_id: int) -> bool:
        """Immediately replay the last synthesized audio buffer without re-running neural generation."""
        cached = self._cached_audio.get(node_id)
        if cached is None or engine is None:
            return False
        audio, sample_rate = cached
        engine.clear_source_audio(node_id)
        pushed = engine.push_source_audio(node_id, audio)
        logger.info("Replayed %d frames to TTS node %d", pushed, node_id)
        self.worker_started.emit(node_id)
        self.worker_finished.emit(node_id, len(audio), len(audio) / float(sample_rate))
        return True

    def speak(
        self,
        engine: Any,
        node_id: int,
        text: str,
        voice_profile_path: Optional[str] = None,
        reference_text: Optional[str] = None,
        temperature: float = 0.8,
        top_p: float = 0.95,
        top_k: int = 50,
        max_new_tokens: int = 1024,
        output_dir: Optional[str] = None,
    ) -> bool:
        # Stop existing worker on this node if any
        self.stop(engine, node_id)

        worker = TTSWorker(
            engine=engine,
            node_id=node_id,
            text=text,
            voice_profile_path=voice_profile_path,
            reference_text=reference_text,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            max_new_tokens=max_new_tokens,
            output_dir=output_dir,
            parent=self,
        )
        worker.generation_started.connect(self._on_started)
        worker.generation_progress.connect(self._on_progress)
        worker.generation_finished.connect(self._on_finished)
        worker.generation_error.connect(self._on_error)
        worker.generation_stopped.connect(self._on_stopped)
        worker.finished.connect(lambda: self._cleanup(node_id))

        self._active_workers[node_id] = worker
        worker.start()
        return True

    def stop(self, engine: Any, node_id: int) -> None:
        worker = self._active_workers.pop(node_id, None)
        if worker and worker.isRunning():
            worker.cancel()
            worker.wait(100)
        if engine:
            engine.clear_source_audio(node_id)
        self.worker_stopped.emit(node_id)

    def _on_started(self, node_id: int) -> None:
        self.worker_started.emit(node_id)

    def _on_progress(self, node_id: int, msg: str) -> None:
        self.worker_progress.emit(node_id, msg)

    def _on_finished(self, node_id: int, frames: int, duration_s: float) -> None:
        self.worker_finished.emit(node_id, frames, duration_s)

    def _on_error(self, node_id: int, err: str) -> None:
        self.worker_error.emit(node_id, err)

    def _on_stopped(self, node_id: int) -> None:
        self.worker_stopped.emit(node_id)

    def _cleanup(self, node_id: int) -> None:
        self._active_workers.pop(node_id, None)


# Module-level shared singleton instance
tts_manager = TTSServiceManager()
