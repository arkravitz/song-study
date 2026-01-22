"""Audio decoding, resampling and file handling.

All decoding goes through ffmpeg because the source library is arbitrary
(FLAC 96 kHz, MP3 320, M4A, Opus…).  Everything downstream assumes
`(n, 2) float32` at the analysis sample rate.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf


@dataclass
class AudioFile:
    """Decoded audio plus provenance."""

    data: np.ndarray  # (n, channels) float32, [-1, 1]
    sample_rate: int
    path: Path | None = None
    native_sr: int = 0
    native_channels: int = 0
    lossy: bool = False

    @property
    def channels(self) -> int:
        return self.data.shape[1] if self.data.ndim > 1 else 1

    @property
    def duration(self) -> float:
        if self.channels == 0 or self.sample_rate == 0:
            return 0.0
        return self.data.shape[0] / self.sample_rate

    def mono(self) -> np.ndarray:
        return self.data.mean(axis=1) if self.data.ndim > 1 else self.data

    def stereo_or_mono(self) -> np.ndarray:
        """(n, 2) — a mono source is duplicated, and flagged by `native_channels`."""
        if self.data.ndim == 1:
            return np.repeat(self.data[:, None], 2, axis=1)
        if self.data.shape[1] == 1:
            return np.repeat(self.data, 2, axis=1)
        return self.data


_LOSSIY_EXT = {".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".aif", ".aiff"}


def probe(path: str | Path) -> dict:
    """ffprobe metadata. Returns {} rather than raising when ffmpeg is absent."""
    path = Path(path)
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(path),
            ],
            capture_output=True, text=True, timeout=120,
        )
        data = json.loads(out.stdout or "{}")
    except Exception:
        return {}
    if not data:
        return {}
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    tags = {**fmt.get("tags", {}), **audio.get("tags", {})}
    duration = _f(fmt.get("duration")) or _f(audio.get("duration"))
    return {
        "path": str(path),
        "filename": path.name,
        "container": fmt.get("format_name"),
        "codec": audio.get("codec_name"),
        "sample_rate": _i(audio.get("sample_rate")),
        "channels": _i(audio.get("channels")),
        "bit_rate": _i(fmt.get("bit_rate")),
        "bits_per_sample": _i(audio.get("bits_per_raw_sample") or audio.get("bits_per_sample")),
        "duration": duration,
        "size_bytes": _i(fmt.get("size")),
        "lossy": path.suffix.lower() in _LOSSIY_EXT,
        "tags": {k.lower(): v for k, v in tags.items() if isinstance(v, str)},
    }


def _f(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _i(x) -> int | None:
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def decode(
    path: str | Path,
    sr: int = 44100,
    mono: bool = False,
    max_seconds: float | None = None,
) -> AudioFile:
    """Decode any audio file to float32 numpy at `sr`.

    Falls back to soundfile/libsndfile when ffmpeg cannot handle the file, so a
    broken ffmpeg degrades the product instead of breaking it.
    """
    path = Path(path)
    info = probe(path)
    native_sr = info.get("sample_rate") or 0
    native_ch = info.get("channels") or 0
    lossy = bool(info.get("lossy"))

    data: np.ndarray | None = None
    try:
        data = _ffmpeg_decode(path, sr, mono, max_seconds)
    except Exception:
        data = None

    if data is None:
        data, used_sr = _soundfile_decode(path, mono, max_seconds)
        native_sr = native_sr or used_sr
        native_ch = native_ch or (1 if data.ndim == 1 else data.shape[1])
    else:
        native_ch = native_ch or 1

    if data.ndim == 1:
        data = data[:, None]

    # Safety: kill NaNs/inf and hard clip.
    if not np.all(np.isfinite(data)):
        data = np.nan_to_num(data, nan=0.0, posinf=0.0, neginf=0.0)
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak > 1.0001:
        data = data / peak * 0.999

    return AudioFile(
        data=np.ascontiguousarray(data, dtype=np.float32),
        sample_rate=sr,
        path=path,
        native_sr=native_sr or sr,
        native_channels=native_ch or data.shape[1],
        lossy=lossy,
    )


def _ffmpeg_decode(path: Path, sr: int, mono: bool, max_seconds: float | None) -> np.ndarray | None:
    channels = 1 if mono else 2
    cmd = ["ffmpeg", "-nostdin", "-v", "error"]
    if max_seconds:
        cmd += ["-t", str(float(max_seconds))]
    cmd += ["-i", str(path), "-map", "a:0", "-f", "f32le", "-acodec", "pcm_f32le",
            "-ar", str(sr), "-ac", str(channels), "-"]
    proc = subprocess.run(cmd, capture_output=True, timeout=3600)
    if proc.returncode != 0 or not proc.stdout:
        return None
    raw = np.frombuffer(proc.stdout, dtype=np.float32)
    return raw.reshape(-1, channels).copy()


def _soundfile_decode(path: Path, mono: bool, max_seconds: float | None) -> tuple[np.ndarray, int]:
    with sf.SoundFile(path) as f:
        sr = f.samplerate
        frames = -1 if max_seconds is None else int(max_seconds * sr)
        data = f.read(frames=frames, dtype="float32", always_2d=True)
    if mono:
        data = data.mean(axis=1, keepdims=True)
    return data, sr


def write_wav(path: str | Path, data: np.ndarray, sr: int, subtype: str = "PCM_16") -> Path:
    """Write stems.  16-bit keeps the audio interface responsive and the disk small."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if data.ndim == 1:
        data = data[:, None]
    data = np.clip(data, -1.0, 1.0)
    sf.write(str(path), data, sr, subtype=subtype)
    return path


def write_wav_float32(path: str | Path, data: np.ndarray, sr: int) -> Path:
    return write_wav(path, data, sr, subtype="FLOAT")


def resample(data: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """Polyphase resampling.  Falls back to ffmpeg for exotic rate pairs."""
    if sr_in == sr_out or data.size == 0:
        return data
    try:
        import librosa

        axis = 0 if data.ndim == 1 else 0
        return librosa.resample(data.T, orig_sr=sr_in, target_sr=sr_out, axis=axis).T
    except Exception:
        return data


def file_digest(path: str | Path, chunk: int = 1 << 20) -> str:
    """Content hash — used as the analysis cache key."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def short_id(*parts: object, length: int = 12) -> str:
    """A short, stable id from anything identifying the audio.

    Parts are stringified rather than type-annotated as ``str`` because the
    natural call site passes a file size alongside a path, and the resulting
    ``TypeError`` only surfaced when running the pipeline outside the server.
    """
    h = hashlib.sha1("::".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return h[:length]


def wav_duration(path: str | Path) -> float:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return 0.0
