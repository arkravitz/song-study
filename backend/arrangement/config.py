"""Runtime configuration, device detection and filesystem layout.

Everything that could reasonably change between machines lives here so the rest
of the pipeline never reads environment variables directly.
"""

from __future__ import annotations

import functools
import os
import platform
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Quality = Literal["fast", "standard", "deep"]
Device = Literal["auto", "cpu", "mps", "cuda"]


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader — avoids a dependency and never clobbers real env vars."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class QualityProfile:
    """Knobs that trade cost for fidelity."""

    name: Quality
    sr: int  # analysis sample rate
    n_fft: int
    hop: int
    # feature frame rate in Hz
    frame_hz: float = 20.0
    separation_sr: int = 44100
    separation_overlap: float = 0.25
    separation_segment: int = 0
    enable_clap: bool = False
    enable_instrument_separation: bool = False
    enable_tagger: bool = True
    onset_fps: int = 86.13  # hop for onset-strength envelope
    tag_window: float = 2.0  # seconds per audio-tagging window
    tag_hop: float = 1.0


PROFILES: dict[str, QualityProfile] = {
    "fast": QualityProfile(
        name="fast", sr=22050, n_fft=1024, hop=256, separation_sr=44100,
        separation_overlap=0.1, enable_clap=False, enable_instrument_separation=False,
    ),
    "standard": QualityProfile(
        name="standard", sr=44100, n_fft=2048, hop=512, separation_sr=44100,
        separation_overlap=0.25, enable_clap=False, enable_instrument_separation=True,
    ),
    "deep": QualityProfile(
        name="deep", sr=44100, n_fft=4096, hop=512, separation_sr=44100,
        separation_overlap=0.35, enable_clap=True, enable_instrument_separation=True,
    ),
}


@dataclass
class Settings:
    data_dir: Path
    quality: QualityProfile
    separator: str
    allow_download: bool
    device: Device
    torch_threads: int | None
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    deepseek_model_deep: str
    deepseek_effort: str
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"

    # ---- derived paths -------------------------------------------------
    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def analyses_dir(self) -> Path:
        return self.data_dir / "analyses"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def stems_dir(self) -> Path:
        return self.data_dir / "stems"

    @property
    def tmp_dir(self) -> Path:
        """Scratch space: logs, job state, and the model response cache.

        Nothing here is analysis. Deleting the whole directory costs a rebuild of
        transient state and nothing else, which is the property that makes it the
        right home for a cache you may want to throw away to force a fresh
        answer.
        """
        return self.data_dir / "tmp"

    def ensure_dirs(self) -> None:
        for d in (
            self.data_dir, self.uploads_dir, self.cache_dir,
            self.analyses_dir, self.models_dir, self.stems_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.deepseek_api_key)


def resolve_device(requested: Device = "auto") -> str:
    """Pick a torch device, degrading gracefully."""
    try:
        import torch
    except Exception:  # pragma: no cover
        return "cpu"

    if requested != "auto":
        if requested == "mps" and not torch.backends.mps.is_available():
            return "cpu"
        if requested == "cuda" and not torch.cuda.is_available():
            return "cpu"
        return requested

    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


@functools.lru_cache(maxsize=1)
def hardware_report() -> dict:
    """Cached description of the machine — surfaced in the UI."""
    info: dict = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or platform.machine(),
        "logical_cores": os.cpu_count(),
        "has_ffmpeg": False,
        "ffmpeg_version": None,
        "torch": None,
        "torch_device": "cpu",
        "mps": False,
        "cuda": False,
    }
    try:
        import torch

        info["torch"] = torch.__version__
        info["torch_device"] = resolve_device("auto")
        info["mps"] = bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
        info["cuda"] = bool(torch.cuda.is_available())
    except Exception:
        pass
    if shutil.which("ffmpeg"):
        info["has_ffmpeg"] = True
        try:
            import subprocess

            out = subprocess.run(
                ["ffmpeg", "-version"], capture_output=True, text=True, timeout=10
            ).stdout.splitlines()
            info["ffmpeg_version"] = out[0] if out else None
        except Exception:
            pass
    return info


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    # backend/arrangement/config.py -> repo root is parents[2], not parents[3]
    repo_root = Path(__file__).resolve().parents[2]
    _load_dotenv(repo_root / ".env")
    _load_dotenv(repo_root / "backend" / ".env")

    data_dir = Path(os.environ.get("ARRANGEMENT_DATA_DIR", repo_root / "data")).resolve()
    quality_name = os.environ.get("ARRANGEMENT_QUALITY", "standard").lower()
    quality = PROFILES.get(quality_name, PROFILES["standard"])

    threads_raw = os.environ.get("ARRANGEMENT_TORCH_THREADS", "").strip()
    threads = int(threads_raw) if threads_raw.isdigit() else None

    s = Settings(
        data_dir=data_dir,
        quality=quality,
        separator=os.environ.get("ARRANGEMENT_SEPARATOR", "multi"),
        allow_download=os.environ.get("ARRANGEMENT_ALLOW_DOWNLOAD", "1") != "0",
        device=os.environ.get("ARRANGEMENT_DEVICE", "auto"),  # type: ignore[arg-type]
        torch_threads=threads,
        deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
        deepseek_base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        deepseek_model=os.environ.get("DEEPSEEK_MODEL", "deepseek-flash"),
        deepseek_model_deep=os.environ.get("DEEPSEEK_MODEL_DEEP", "deepseek-v4-pro"),
        deepseek_effort=os.environ.get("DEEPSEEK_EFFORT", "high"),
    )
    s.ensure_dirs()
    if s.torch_threads:
        try:
            import torch

            torch.set_num_threads(s.torch_threads)
        except Exception:
            pass
    return s
