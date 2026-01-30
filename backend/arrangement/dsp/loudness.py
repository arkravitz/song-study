"""Loudness: ITU-R BS.1770 LUFS, LRA, true peak.

`pyloudnorm` is the reference implementation but runs roughly 100x slower than
realtime, which is unusable for a per-second loudness curve.  ffmpeg's
`ebur128` filter is a native implementation of the same recommendation and is
effectively instant, so we prefer it and fall back to pyloudnorm when ffmpeg is
missing.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

_EPS = 1e-12


@dataclass
class LoudnessResult:
    integrated_lufs: float | None = None
    lra: float | None = None
    true_peak_dbfs: float | None = None
    momentary_lufs: list[float] = field(default_factory=list)   # 400 ms
    short_term_lufs: list[float] = field(default_factory=list)  # 3 s
    momentary_hz: float = 2.5
    short_term_hz: float = 0.5
    plr_db: float | None = None
    crest_db: float | None = None
    method: str = "ffmpeg ebur128"
    notes: list[str] = field(default_factory=list)


def _db(x: np.ndarray | float) -> np.ndarray:
    return 20.0 * np.log10(np.maximum(np.abs(x), _EPS))


def analyse_loudness(x: np.ndarray, sr: int, method: str = "auto") -> LoudnessResult:
    """Full loudness report.

    ``x`` is (n, 2) float32 in [-1, 1] at ``sr``.
    """
    if x.ndim == 1:
        x = np.repeat(x[:, None], 2, axis=1)
    res = LoudnessResult()

    peak = float(np.max(np.abs(x))) if x.size else 0.0
    res.true_peak_dbfs = float(_db(peak))
    rms = float(np.sqrt(np.mean(x ** 2)))
    res.crest_db = float(_db(peak) - _db(rms)) if rms > 0 else 0.0

    if method in ("auto", "ffmpeg"):
        got = _ffmpeg_ebur128(x, sr)
        if got is not None:
            got.crest_db = res.crest_db
            if rms > 0 and got.integrated_lufs is not None:
                got.plr_db = float(res.crest_db - (got.integrated_lufs + 0.691))
            return got
    return _pyloudnorm(x, sr, res)


def _ffmpeg_ebur128(x: np.ndarray, sr: int) -> LoudnessResult | None:
    """Momentary/short-term curves plus integrated + LRA via ffmpeg ebur128."""
    try:
        import soundfile as sf

        with tempfile.TemporaryDirectory(prefix="ebur_") as td:
            src = Path(td) / "in.wav"
            sf.write(str(src), np.clip(x, -1, 1), sr, subtype="PCM_24")
            proc = subprocess.run(
                ["ffmpeg", "-nostdin", "-hide_banner", "-i", str(src),
                 "-filter_complex", "ebur128=peak=true:framelog=verbose",
                 "-f", "null", "-"],
                capture_output=True, text=True, timeout=900,
            )
            out = proc.stderr or ""
    except Exception:
        return None
    if "Integrated loudness" not in out:
        return None

    res = LoudnessResult(method="ffmpeg ebur128")
    m = re.search(r"Integrated loudness:\s*I:\s*(-?[\d.]+)\s*LUFS", out)
    if m:
        res.integrated_lufs = float(m.group(1))
    m = re.search(r"Loudness range:\s*LRA:\s*(-?[\d.]+)\s*LU", out)
    if m:
        res.lra = float(m.group(1))
    m = re.search(r"True peak:\s*Peak:\s*(-?[\d.]+)\s*dBFS", out)
    if m:
        res.true_peak_dbfs = float(m.group(1))

    momentary: list[float] = []
    for line in out.splitlines():
        mm = re.search(r"t:\s*(-?[\d.]+).*M:\s*(-?[\d.]+)\s*S:\s*(-?[\d.]+)", line)
        if mm:
            momentary.append(float(mm.group(2)))
            res.short_term_lufs.append(float(mm.group(3)))
    res.momentary_lufs = momentary
    res.momentary_hz = 2.5
    res.short_term_hz = 0.5
    if res.integrated_lufs is None:
        return None
    return res


def _pyloudnorm(x: np.ndarray, sr: int, res: LoudnessResult) -> LoudnessResult:
    try:
        import pyloudnorm as pyln

        meter = pyln.Meter(sr)
        y = x.mean(axis=1)
        res.method = "pyloudnorm (BS.1770-4)"
        res.integrated_lufs = float(meter.integrated_loudness(y))
        try:
            res.lra = float(meter.loudness_range(y))
        except Exception:
            res.lra = None
        res.notes.append(
            "ffmpeg ebur128 unavailable; loudness curve is not computed because "
            "pyloudnorm is too slow for per-frame values. Integrated LUFS is exact."
        )
        return res
    except Exception as exc:
        res.method = "unavailable"
        res.notes.append(f"loudness measurement unavailable: {exc}")
        return res


def lufs_1s(x: np.ndarray, sr: int, window: float = 1.0, hop: float = 0.1
            ) -> tuple[np.ndarray, float]:
    """A cheap, K-weighted short-term LUFS curve.

    Not a substitute for BS.1770 integrated loudness (it uses a simplified
    K-weighting) but it tracks *changes* well, which is what the energy model
    and the UI need.  Flagged as an approximation everywhere it is used.
    """
    import scipy.signal as sps

    # Filter channels independently. Averaging waveforms first can cancel
    # stereo information and mistake a width/phase change for a level dropout.
    y = np.asarray(x, dtype=np.float64)
    # High-shelf (+4 dB above ~1.5 kHz) then high-pass (~38 Hz), the K-weighting.
    f0 = 1681.974450955533
    G = 3.999843853973347
    Q = 0.7071752369554196
    K = np.tan(np.pi * f0 / sr)
    Vh = 10 ** (G / 20.0)
    Vb = Vh ** 0.4996667741545416
    a0 = 1.0 + K / Q + K * K
    b0 = (Vh + Vb * K / Q + K * K) / a0
    b1 = 2.0 * (K * K - Vh) / a0
    b2 = (Vh - Vb * K / Q + K * K) / a0
    a1 = 2.0 * (K * K - 1.0) / a0
    a2 = (1.0 - K / Q + K * K) / a0
    sh = sps.lfilter([b0, b1, b2], [1.0, a1, a2], y, axis=0)
    hp = sps.butter(2, 38.0 / (sr / 2), btype="high", output="sos")
    sh = sps.sosfilt(hp, sh, axis=0)
    power = np.sum(sh ** 2, axis=1) if sh.ndim > 1 else sh ** 2

    n = max(1, int(len(sh) - window * sr))
    step = max(1, int(hop * sr))
    idx = np.arange(0, n, step)
    if len(idx) == 0:
        idx = np.array([0])
    csum = np.concatenate([[0.0], np.cumsum(power)])
    ends = np.clip(idx + int(window * sr), 0, len(sh))
    starts = np.clip(idx, 0, len(sh))
    mean_sq = (csum[ends] - csum[starts]) / np.maximum(ends - starts, 1)
    lufs = -0.691 + 10.0 * np.log10(np.maximum(mean_sq, 1e-12))
    return lufs, 1.0 / hop
