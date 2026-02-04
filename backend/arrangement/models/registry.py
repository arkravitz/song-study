"""Model registry.

Every pretrained model the pipeline uses is declared here with a *role* rather
than being imported directly by the analysis code.  Swapping a model is then a
one-line change in config plus an entry here, and a missing model degrades that
one role instead of killing the run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

log = logging.getLogger(__name__)

ModelRole = Literal[
    "separation",      # stems
    "instrument_sep",  # extra instrument stems
    "beat",            # beat / downbeat / tempo
    "tagger",          # closed-set audio tagging
    "zero_shot",       # open-vocabulary audio-text retrieval
    "structure",       # music structure segmentation
    "pitch",           # f0 / multi-pitch
    "vad",             # voice activity
]


@dataclass
class ModelSpec:
    key: str
    role: ModelRole
    backend: str                 # python module that loads it
    name: str                    # checkpoint / repo id
    description: str = ""
    license: str = "unknown"
    requires: list[str] = field(default_factory=list)
    optional: bool = True        # if True, failure is non-fatal
    device_capable: bool = True
    notes: str = ""


#: Declared but resolved lazily.  `backend` is an import path or a known key.
REGISTRY: dict[str, ModelSpec] = {
    # ---------------- separation ----------------
    "demucs_htdemucs": ModelSpec(
        "demucs_htdemucs", "separation", "demucs", "htdemucs",
        "Demucs v4 hybrid transformer, 4 stems", "MIT", ["torch", "demucs"]),
    "demucs_htdemucs_ft": ModelSpec(
        "demucs_htdemucs_ft", "separation", "demucs", "htdemucs_ft",
        "Demucs v4 4-stem bag of 4 fine-tuned models — best 4-stem quality",
        "MIT", ["torch", "demucs"]),
    "demucs_htdemucs_6s": ModelSpec(
        "demucs_htdemucs_6s", "separation", "demucs", "htdemucs_6s",
        "Demucs v4 6-stem (adds guitar, piano), single model = fast",
        "MIT", ["torch", "demucs"]),
    "demucs_mdx": ModelSpec(
        "demucs_mdx", "separation", "demucs", "mdx_extra",
        "Demucs v3 MDX-trained 4 stems", "MIT", ["torch", "demucs"]),
    "vr_bs_roformer": ModelSpec(
        "vr_bs_roformer", "separation", "audio_separator",
        "model_bs_roformer_ep_317_sdr_12.9755.ckpt",
        "BS-RoFormer via UVR — strongest 4-stem vocals/bass/drums/other",
        "MIT (code) / model-specific", ["audio-separator", "onnxruntime", "torch"]),
    "vr_mdx_inst": ModelSpec(
        "vr_mdx_inst", "instrument_sep", "audio_separator",
        "UVR-MDX-NET-Inst_HQ_3.onnx",
        "Multi-instrument separation (vocals/drums/bass/other + more)",
        "MIT", ["audio-separator", "onnxruntime"], optional=True),

    # ---------------- rhythm ----------------
    "beat_this": ModelSpec(
        "beat_this", "beat", "beat_this", "beat_this",
        "CPFN Beat Transformer: joint beat / downbeat / meter / tempo",
        "MIT", ["torch"], notes="best-accuracy open beat tracker; ~50 MB"),

    # ---------------- tagging ----------------
    "panns_cnn14": ModelSpec(
        "panns_cnn14", "tagger", "panns", "Cnn14",
        "PANNs CNN14 AudioSet tagger (527 classes)", "CC-BY-4.0", ["torch"]),
    "ast": ModelSpec(
        "ast", "tagger", "transformers", "MIT/ast-finetuned-audioset-10-10-0.4593",
        "Audio Spectrogram Transformer tagger", "Apache-2.0", ["torch", "transformers"]),
    "clap": ModelSpec(
        "clap", "zero_shot", "transformers", "laion/clap-htsat-fused",
        "CLAP audio-text joint embedding — open-vocabulary instrument/timbre queries",
        "CC-BY-4.0", ["torch", "transformers"],
        notes="48 kHz input; ideal for descriptive labels like 'muted rhythm guitar'"),

    # ---------------- structure ----------------
    "msaf": ModelSpec(
        "msaf", "structure", "msaf", "msaf",
        "Music Structure Analysis Framework (Foote novelty + recurrence graph)",
        "AGPL-3.0", ["msaf"], optional=True,
        notes="needs a compiled C++/Python extension"),
    "librosa_structure": ModelSpec(
        "librosa_structure", "structure", "librosa", "recurrence+agglomerative",
        "librosa self-similarity + agglomerative clustering", "ISC", ["librosa"]),

    # ---------------- pitch ----------------
    "crepe": ModelSpec(
        "crepe", "pitch", "torchcrepe", "full",
        "CREPE monophonic f0", "MIT", ["torchcrepe"], optional=True),
    "rmvpe": ModelSpec(
        "rmvpe", "pitch", "rmvpe", "rmvpe",
        "RMVPE robust f0 for vocals and bass", "MIT", ["rmvpe"], optional=True),

    # ---------------- vad ----------------
    "silero_vad": ModelSpec(
        "silero_vad", "vad", "silero", "silero_vad",
        "Silero VAD (bundled weights)", "MIT", ["torchaudio", "onnxruntime"],
        optional=True, notes="used to confirm vocal activity on the vocals stem"),
}


def available(key: str) -> bool:
    """True if the backend package for this model is importable."""
    spec = REGISTRY.get(key)
    if spec is None:
        return False
    if spec.backend in ("demucs", "librosa"):
        return _importable(spec.backend)
    if spec.backend == "audio_separator":
        return _importable("audio_separator")
    if spec.backend == "transformers":
        return _importable("transformers")
    return _importable(spec.backend)


def _importable(name: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def describe() -> list[dict[str, Any]]:
    out = []
    for spec in REGISTRY.values():
        out.append({
            "key": spec.key, "role": spec.role, "name": spec.name,
            "description": spec.description, "license": spec.license,
            "requires": spec.requires, "optional": spec.optional,
            "available": available(spec.key), "notes": spec.notes,
        })
    return out


def get(key: str) -> ModelSpec:
    spec = REGISTRY.get(key)
    if spec is None:
        raise KeyError(f"unknown model '{key}'. Known: {sorted(REGISTRY)}")
    return spec
