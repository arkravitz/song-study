"""Source separation.

The pipeline is deliberately *model-agnostic*: each backend is a small adapter
that returns ``{stem_name: (n, 2) float32}``.  Results are cached on disk keyed
by content hash + model name so re-running is free.

Why not just call `audio-separator`?  It pins an old numpy/librosa which
collides with the rest of the toolchain, so it is used only when it is the only
thing that can do the job (multi-instrument models) and through a subprocess
where its version constraints cannot leak.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
import soundfile as sf

from arrangement.audio.io import write_wav

log = logging.getLogger(__name__)

ProgressFn = Callable[[float, str], None]


@dataclass
class SeparationResult:
    model: str
    stems: dict[str, np.ndarray]  # name -> (n, 2) float32
    sample_rate: int
    duration: float
    device: str
    from_cache: bool = False
    notes: list[str] | None = None

    @property
    def stem_names(self) -> list[str]:
        return sorted(self.stems)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def cache_dir(root: Path, song_id: str, model: str) -> Path:
    return root / song_id / model.replace("/", "_")


def load_cached(root: Path, song_id: str, model: str) -> SeparationResult | None:
    d = cache_dir(root, song_id, model)
    if not d.exists() or not (d / "_meta.json").exists():
        return None
    import json

    try:
        meta = json.loads((d / "_meta.json").read_text())
        stems: dict[str, np.ndarray] = {}
        for name, info in meta["stems"].items():
            f = d / info["file"]
            if not f.exists():
                return None
            data, sr = sf.read(str(f), dtype="float32", always_2d=True)
            stems[name] = data
        if meta.get("sample_rate") != 44100 and stems:
            # Stems are always stored at 44.1 kHz by our writers.
            pass
        return SeparationResult(
            model=meta.get("model", model), stems=stems,
            sample_rate=meta.get("sample_rate", 44100),
            duration=meta.get("duration", 0.0), device=meta.get("device", "?"),
            from_cache=True, notes=meta.get("notes"),
        )
    except Exception as exc:  # pragma: no cover
        log.warning("separation cache read failed: %s", exc)
        return None


def store_cached(root: Path, song_id: str, result: SeparationResult,
                 also_wav: bool = True, keep_float: bool = False) -> Path:
    """Persist stems.

    16-bit PCM is ~96 dB below full scale — far under the level of separation
    bleed we care about — so stems are stored as WAV only unless ``keep_float``
    is set.  That halves the disk footprint of a 6-stem separation.
    """
    import json

    d = cache_dir(root, song_id, result.model)
    d.mkdir(parents=True, exist_ok=True)
    meta = {"model": result.model, "sample_rate": result.sample_rate,
            "duration": result.duration, "device": result.device, "stems": {}}
    for name, data in result.stems.items():
        safe = name.replace("/", "_").replace(" ", "_")
        entry: dict = {}
        if keep_float:
            np.save(d / f"{safe}.npy", data.astype(np.float32))
            entry["npy"] = f"{safe}.npy"
        write_wav(d / f"{safe}.wav", data, result.sample_rate, "PCM_16")
        entry["file"] = f"{safe}.wav"
        entry["n_samples"] = int(data.shape[0])
        meta["stems"][name] = entry
    (d / "_meta.json").write_text(json.dumps(meta, indent=2))
    return d


def load_stem_file(root: Path, song_id: str, model: str, stem: str) -> np.ndarray | None:
    d = cache_dir(root, song_id, model)
    safe = stem.replace("/", "_").replace(" ", "_")
    npy = d / f"{safe}.npy"
    if npy.exists():
        return np.load(npy)
    wav = d / f"{safe}.wav"
    if wav.exists():
        data, _ = sf.read(str(wav), dtype="float32", always_2d=True)
        return data
    return None


# ---------------------------------------------------------------------------
# Demucs backend
# ---------------------------------------------------------------------------

DEMUCS_STEMS: dict[str, list[str]] = {
    "htdemucs": ["drums", "bass", "other", "vocals"],
    "htdemucs_ft": ["drums", "bass", "other", "vocals"],
    "mdx_extra": ["drums", "bass", "other", "vocals"],
    "htdemucs_6s": ["drums", "bass", "other", "vocals", "guitar", "piano"],
}


def separate_demucs(
    audio: np.ndarray,
    sr: int,
    model_name: str = "htdemucs_6s",
    device: str = "mps",
    segment: int | None = None,
    overlap: float = 0.25,
    shifts: int = 0,
    progress: ProgressFn | None = None,
    max_seconds: float | None = None,
) -> SeparationResult:
    """Run Demucs.  `audio` is (n, 2) float32 at `sr`."""
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    t0 = time.time()
    if progress:
        progress(0.02, f"loading demucs '{model_name}'")
    model = get_model(model_name)
    model_device = device
    try:
        model.to(model_device)
    except Exception as exc:
        log.warning("demucs: device %s unavailable (%s), falling back to cpu", model_device, exc)
        model_device = "cpu"
        model.cpu()

    model_sr = int(getattr(model, "samplerate", 44100))
    x = _to_stereo(audio)
    n_in = x.shape[0]
    if max_seconds:
        x = x[: int(max_seconds * sr)]
        n_in = x.shape[0]

    if sr != model_sr:
        x = _resample(x, sr, model_sr)
    x = x.T.astype(np.float32)  # (2, N)
    src = torch.from_numpy(x)
    if len(src.shape) == 1:
        src = src.unsqueeze(0)

    kwargs = dict(split=True, overlap=overlap, progress=False, num_workers=0)
    if segment is not None and segment > 6:
        kwargs["segment"] = segment
    if device != "mps" and device != "cpu":
        kwargs["device"] = device

    # demucs does not accept device kwarg for mps in some versions; set it via
    # the apply_model default and rely on model.to().
    if device in ("mps", "cpu"):
        kwargs.pop("device", None)
    if shifts:
        kwargs["shifts"] = shifts

    seg = getattr(getattr(model, "bag", None), "segment", None) or getattr(model, "segment", None) or 7.8
    n_chunks = max(1, int(np.ceil(x.shape[1] / (seg * model_sr))))
    if progress:
        progress(0.05, f"separating {n_in / sr:.0f}s audio "
                       f"(~{n_chunks} chunk{'s' if n_chunks != 1 else ''})")

    try:
        with torch.no_grad():
            out = apply_model(model, src[None], **kwargs)
    except TypeError:
        kwargs.pop("num_workers", None)
        with torch.no_grad():
            out = apply_model(model, src[None], device=device, **kwargs)

    out = out[0].detach().to("cpu").numpy()  # (S, 2, N)
    stems = {}
    names = DEMUCS_STEMS.get(model_name, [f"stem{i}" for i in range(out.shape[0])])
    for i, name in enumerate(names[: out.shape[0]]):
        s = out[i]
        if s.shape[0] == 1:
            s = np.repeat(s, 2, axis=0)
        stems[name] = np.ascontiguousarray(s.T, dtype=np.float32)

    if model_sr != sr:
        stems = {k: _resample(v, model_sr, sr) for k, v in stems.items()}

    dur = stems[next(iter(stems))].shape[0] / sr if stems else 0.0
    if progress:
        progress(1.0, f"demucs done in {time.time() - t0:.0f}s")
    return SeparationResult(model=model_name, stems=stems, sample_rate=sr,
                            duration=dur, device=model_device,
                            notes=[f"{time.time() - t0:.1f}s on {model_device}"])


def _to_stereo(audio: np.ndarray) -> np.ndarray:
    a = np.asarray(audio, dtype=np.float32)
    if a.ndim == 1:
        return np.repeat(a[:, None], 2, axis=1)
    if a.shape[1] == 1:
        return np.repeat(a, 2, axis=1)
    return a[:, :2]


def _resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out:
        return x
    import librosa

    return librosa.resample(x.T, orig_sr=sr_in, target_sr=sr_out, axis=0).T.astype(np.float32)


# ---------------------------------------------------------------------------
# audio-separator (UVR) backend — isolated via subprocess
# ---------------------------------------------------------------------------

#: Models known to give more than 4 stems.  ``instrument set`` models are the
#: only realistic route to per-instrument stems from a stereo master.
#: BS-RoFormer checkpoints that are verified to download and run on this setup.
#: NOTE: despite the widely-repeated "12-stem" name, this checkpoint is a
#: 2-stem (Vocals / Instrumental) separator. It is included because its vocals
#: are markedly cleaner than Demucs', which matters for vocal activity and
#: vocal-band analysis. There is no open 12-stem checkpoint in existence.
UVR_MODELS: dict[str, dict] = {
    "bs_roformer_viperx_1297": {
        "filename": "model_bs_roformer_ep_317_sdr_12.9755.ckpt",
        "stems": ["Vocals", "Instrumental"],
        "note": "2-stem; highest-quality open vocals separation",
    },
    "mel_band_roformer_1143": {
        "filename": "model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt",
        "stems": ["Vocals", "Drums", "Bass", "Other"],
        "note": "4-stem, the audio-separator default",
    },
}


#: The UVR environment is a *separate* virtualenv: audio-separator pins
#: numpy<2 and an old librosa, which would break the main toolchain. It lives
#: beside the main one and is addressed by path.
UVR_VENV = Path(__file__).resolve().parents[3] / ".venv-uvr"
UVR_PYTHON = UVR_VENV / "bin" / "python"


def uvr_python() -> Path | None:
    if UVR_PYTHON.exists():
        return UVR_PYTHON
    from arrangement.config import get_settings

    for cand in (get_settings().data_dir.parent / ".venv-uvr" / "bin" / "python",
                 Path.cwd() / ".venv-uvr" / "bin" / "python"):
        if cand.exists():
            return cand
    return None


def uvr_available() -> bool:
    py = uvr_python()
    if py is None:
        return False
    try:
        return subprocess.run(
            [str(py), "-c", "import audio_separator"],
            capture_output=True, timeout=120,
        ).returncode == 0
    except Exception:
        return False


def separate_uvr(
    path: str | Path,
    model_filename: str,
    model_dir: Path,
    output_dir: Path,
    sr: int = 44100,
    overlap: int = 4,
    progress: ProgressFn | None = None,
    timeout: int = 14400,
) -> tuple[dict[str, np.ndarray], str]:
    """Run audio-separator in a throwaway subprocess with its own interpreter.

    Keeping it out-of-process means its numpy<2 pin cannot affect the rest of
    the pipeline.  If the subprocess cannot be built, the caller degrades.
    """
    import json

    python = uvr_python()
    if python is None:
        raise RuntimeError("audio-separator environment not found (.venv-uvr)")

    # audio-separator 0.4x: the model is chosen via load_model(), and separate()
    # takes only the path.  `overlap` is the main speed/quality dial; 8 is the
    # library default and 4 is nearly as good for our purpose.
    script = f"""
import json, sys, os
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
from audio_separator.separator import Separator
sep = Separator(output_dir={str(output_dir)!r}, model_file_dir={str(model_dir)!r},
                output_format="WAV", sample_rate={sr}, use_soundfile=True,
                mdxc_params={{"segment_size": 256, "overlap": {overlap}, "batch_size": 1}})
sep.load_model({model_filename!r})
files = sep.separate({str(path)!r})
print("__RESULT__" + json.dumps({{"files": list(files)}}))
"""
    with tempfile.TemporaryDirectory(prefix="uvr_") as td:
        script_path = Path(td) / "run_uvr.py"
        script_path.write_text(script)
        if progress:
            progress(0.05, f"audio-separator subprocess ({model_filename})")
        try:
            proc = subprocess.run(
                [str(python), str(script_path)], capture_output=True, text=True,
                timeout=timeout,
            )
        except Exception as exc:
            raise RuntimeError(f"audio-separator subprocess failed: {exc}") from exc
        if proc.returncode != 0:
            raise RuntimeError(
                f"audio-separator failed: {(proc.stderr or proc.stdout)[-800:]}"
            )
        marker = proc.stdout.find("__RESULT__")
        if marker < 0:
            raise RuntimeError("audio-separator produced no result marker")

        stems: dict[str, np.ndarray] = {}
        for f in Path(output_dir).rglob("*.wav"):
            name = f.stem
            try:
                data, file_sr = sf.read(str(f), dtype="float32", always_2d=True)
            except Exception:
                continue
            if file_sr != sr:
                data = _resample(data, file_sr, sr)
            stems[name] = data
        if not stems:
            raise RuntimeError("audio-separator produced no readable stems")
        return stems, model_filename


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_separation(
    audio: np.ndarray,
    sr: int,
    song_id: str,
    preset: str,
    device: str,
    cache_root: Path,
    allow_download: bool = True,
    progress: ProgressFn | None = None,
    max_seconds: float | None = None,
    quality: str = "standard",
) -> tuple[list[SeparationResult], list[str]]:
    """Run the configured separation plan.  Returns (results, warnings)."""
    warnings: list[str] = []
    results: list[SeparationResult] = []
    plans = _plan(preset, allow_uvr=(quality == "deep"))

    for plan in plans:
        backend, name = plan["backend"], plan["model"]
        try:
            cached = load_cached(cache_root, song_id, name) if plan.get("cache", True) else None
            if cached is not None:
                if progress:
                    progress(plan.get("weight", 0.3), f"{name} (cached)")
                results.append(cached)
                continue

            if backend == "demucs":
                res = separate_demucs(
                    audio, sr, name, device=device,
                    overlap=plan.get("overlap", 0.25),
                    shifts=plan.get("shifts", 0),
                    progress=(lambda p, m: progress(plan.get("weight", 0.3) * p, m)) if progress else None,
                    max_seconds=max_seconds,
                )
            elif backend == "audio_separator":
                res = _run_instrument_sep(plan, audio, sr, song_id, cache_root, progress)
            else:
                warnings.append(f"unknown separation backend '{backend}'")
                continue

            if res is None:
                continue
            if plan.get("cache", True):
                store_cached(cache_root, song_id, res)
            results.append(res)
        except Exception as exc:
            msg = f"separation '{name}' failed: {exc}"
            log.warning(msg)
            warnings.append(msg)
    return results, warnings


def _plan(preset: str, allow_uvr: bool = True) -> list[dict]:
    """Translate a preset name into a list of separation jobs.

    Timings measured on an M1 (MPS): htdemucs_6s runs at ~3.4x realtime; the
    BS-RoFormer vocals pass runs at ~0.22x realtime, i.e. ~13 minutes for a
    3-minute song. That is a real cost, so it is opt-in rather than default.
    """
    if preset == "htdemucs":
        return [dict(backend="demucs", model="htdemucs", weight=1.0)]
    if preset == "htdemucs_ft":
        return [dict(backend="demucs", model="htdemucs_ft", weight=1.0, shifts=1)]
    if preset == "htdemucs_6s":
        return [dict(backend="demucs", model="htdemucs_6s", weight=1.0)]
    if preset == "roformer4":
        return [dict(backend="audio_separator", model="mel_band_roformer_1143", weight=1.0)]
    if preset == "roformer_vocals":
        return [dict(backend="audio_separator", model="bs_roformer_viperx_1297", weight=1.0)]
    if preset == "bs_roformer_vocals_plus":
        return [
            dict(backend="demucs", model="htdemucs_6s", weight=0.45),
            dict(backend="audio_separator", model="bs_roformer_viperx_1297", weight=0.55),
        ]
    # default: fast 6-stem Demucs, which gives guitar and piano that the
    # 4-stem RoFormer models cannot.
    plan = [dict(backend="demucs", model="htdemucs_6s", weight=1.0)]
    if allow_uvr and uvr_available():
        plan[0]["weight"] = 0.6
        plan.append(dict(backend="audio_separator", model="bs_roformer_viperx_1297",
                         weight=0.4))
    return plan


def _run_instrument_sep(plan: dict, audio: np.ndarray, sr: int, song_id: str,
                        cache_root: Path, progress: ProgressFn | None) -> SeparationResult | None:
    """Multi-instrument separation.  Requires a real file on disk."""
    cfg = UVR_MODELS.get(plan["model"])
    if cfg is None:
        return None
    import tempfile

    with tempfile.TemporaryDirectory(prefix="src_") as td:
        src = Path(td) / "input.wav"
        write_wav(src, _to_stereo(audio), sr, "PCM_16")
        out = Path(td) / "out"
        out.mkdir(parents=True, exist_ok=True)
        try:
            stems, name = separate_uvr(src, cfg["filename"], cache_root, out, sr,
                                       overlap=plan.get("overlap", 4),
                                       progress=progress)
        except Exception as exc:
            log.warning("instrument separation unavailable: %s", exc)
            return None
    n = min(v.shape[0] for v in stems.values())
    stems = {k: v[:n] for k, v in stems.items()}
    return SeparationResult(model=name, stems=stems, sample_rate=sr,
                            duration=n / sr, device="onnxruntime")
