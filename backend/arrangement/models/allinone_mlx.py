"""Isolated All-In-One MLX inference for tempo, meter and song form.

The optional Apple Silicon package runs in a subprocess: its Metal allocations
are released when inference ends, and a failure cannot break the rest of the
arrangement analysis. Existing Demucs stems are reused whenever available.
"""

from __future__ import annotations

import json
import math
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from arrangement.config import Settings
from arrangement.models.separation import SeparationResult, cache_dir
from arrangement.schema import SectionLabel

MODEL_VERSION = "1.0.14"
_MARKERS = {"start", "end"}
_ALIASES = {"inst": "instrumental"}
_KNOWN_LABELS = {label.value for label in SectionLabel}


@dataclass(frozen=True)
class ModelSection:
    start: float
    end: float
    label: str


@dataclass(frozen=True)
class ModelResult:
    bpm: float
    beats: list[float]
    downbeats: list[float]
    sections: list[ModelSection]


def normalize_segments(raw: list[dict], duration: float) -> list[ModelSection]:
    """Turn the model's markers and fragments into complete musical sections."""
    candidates = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", "unknown")).strip().lower()
        if label in _MARKERS:
            continue
        label = _ALIASES.get(label, label)
        if label not in _KNOWN_LABELS:
            label = "unknown"
        try:
            start = max(0.0, min(duration, float(item["start"])))
            end = max(0.0, min(duration, float(item["end"])))
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(start) and math.isfinite(end)) or end - start < 0.4:
            continue
        candidates.append(ModelSection(start, end, label))

    candidates.sort(key=lambda s: (s.start, s.end))
    if not candidates:
        return []
    out: list[ModelSection] = []
    for item in candidates:
        start = out[-1].end if out else 0.0
        end = max(start, item.end)
        if end - start < 0.4:
            continue
        if out and out[-1].label == item.label and item.label in {"intro", "outro", "fade"}:
            # These names describe a position in the record, not a repeatable
            # role. Two adjacent outro fragments are one ending.
            prior = out[-1]
            out[-1] = ModelSection(prior.start, end, prior.label)
        else:
            # Adjacent choruses can be distinct repetitions. Preserve their
            # internal boundary for closer inspection.
            out.append(ModelSection(start, end, item.label))
    if out:
        last = out[-1]
        out[-1] = ModelSection(last.start, duration, last.label)
    return out


def load_result(path: Path, duration: float) -> ModelResult:
    data = json.loads(path.read_text())
    bpm = float(data["bpm"])
    beats = [float(value) for value in data["beats"]]
    downbeats = [float(value) for value in data["downbeats"]]
    sections = normalize_segments(data["segments"], duration)
    if not math.isfinite(bpm) or not 40 <= bpm <= 240:
        raise ValueError("invalid model BPM")
    if (len(beats) < 8 or any(not math.isfinite(t) or t < 0 or t > duration + 1
                              for t in beats)
            or any(b <= a for a, b in zip(beats, beats[1:]))):
        raise ValueError("invalid model beat grid")
    if any(not math.isfinite(t) or t < 0 or t > duration + 1 for t in downbeats):
        raise ValueError("invalid model downbeats")
    if not sections:
        raise ValueError("model returned no usable sections")
    return ModelResult(bpm=bpm, beats=beats, downbeats=downbeats, sections=sections)


def run_model(path: Path, song_id: str, duration: float, settings: Settings,
              separations: list[SeparationResult], force: bool = False) -> ModelResult | None:
    """Return model output, or None when this platform lacks the optional CLI."""
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        return None
    bundled = Path(sys.executable).with_name("allin1-mlx")
    executable = str(bundled) if bundled.is_file() else shutil.which("allin1-mlx")
    if not executable:
        return None

    out_dir = settings.cache_dir / song_id / f"allinone_mlx_{MODEL_VERSION}"
    result_path = out_dir / f"{path.stem}.json"
    if result_path.is_file() and not force:
        try:
            return load_result(result_path, duration)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            pass  # a corrupt cache is rebuilt below

    out_dir.mkdir(parents=True, exist_ok=True)
    command = [executable, str(path), "--out-dir", str(out_dir), "--no-multiprocess"]
    for result in separations:
        if {"bass", "drums", "other", "vocals"}.issubset(result.stems):
            stem_path = cache_dir(settings.stems_dir, song_id, result.model)
            if all((stem_path / f"{name}.wav").is_file()
                   for name in ("bass", "drums", "other", "vocals")):
                command.extend(["--stems-dir", str(stem_path)])
                break
    weights = settings.models_dir / "allinone_mlx"
    if (weights / "harmonix-fold0_mlx.npz").is_file():
        command.extend(["--mlx-weights-dir", str(weights)])
    if force:
        command.extend(["--overwrite", "json"])
    proc = subprocess.run(command, capture_output=True, text=True, timeout=900,
                          check=False)
    if proc.returncode:
        raise RuntimeError((proc.stderr or proc.stdout).strip()[-700:])
    return load_result(result_path, duration)
