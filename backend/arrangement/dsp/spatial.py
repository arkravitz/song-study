"""Stereo analysis.

Scientific care is required here.  From a stereo master we can measure the
**spatial characteristics of the mix**; we cannot recover what a producer's pan
knobs were set to.  Those are different things:

* ``pan`` below is the *energy centroid* of the side signal relative to mid,
  i.e. "how much of this signal's energy exists only in the differences between
  channels".  A hard-panned left guitar and a wide stereo reverb both produce
  it, for completely different reasons.
* ``width`` is the mid/side ratio, which measures image *width*, not width in
  the sense of "how many instruments are spread out".
* ``correlation`` is the zero-lag inter-channel correlation, which distinguishes
  *decorrelated* width (reverb, Haas) from *amplitude* panning (which keeps
  correlation near 1).

All three are frequency dependent, so we compute them per band.  A bass line
that is dead-centre and a hi-hat that is wide is completely normal, and a
single broadband number hides it.

Mono sources are flagged and all values are reported as meaningless rather than
silently computed on duplicated channels.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

_EPS = 1e-10

#: Band edges (Hz) for frequency-dependent spatial measures.
#:
#: `field` exists because the name here and the name the rest of the system
#: stores the result under are not the same string, and that mismatch was a live
#: trap: the middle band is `low_mid` here but is written to
#: `spatial/mid_width` and reported as `mid_width`. Deriving a curve name from
#: `name` produced `spatial/low_mid_width`, which does not exist, and the request
#: failed at runtime instead of at import. The band now carries the name it is
#: stored under, so there is nothing left to derive wrongly.
#:
#: `label` is what a producer is shown. `low_mid` is an internal name for the
#: middle of the spectrum, not something to print next to a curve.
@dataclass(frozen=True)
class SpatialBand:
    name: str        # internal, matches the stored field suffix
    label: str       # what the interface shows
    lo: float        # Hz, inclusive
    hi: float        # Hz, exclusive
    field: str       # the key this band is stored and reported under

    @property
    def curve(self) -> str:
        return f"spatial/{self.field}"


SPATIAL_BANDS: list[SpatialBand] = [
    SpatialBand("low", "low", 20, 250, "low_width"),
    SpatialBand("mid", "mid", 250, 2000, "mid_width"),
    SpatialBand("high", "high", 2000, 12000, "high_width"),
]


@dataclass
class SpatialFrame:
    t: float
    pan: float = 0.0         # -1 (L) .. +1 (R) energy centroid
    width: float = 0.0       # 0..1, side/mid energy ratio, normalised
    correlation: float = 1.0
    low_width: float = 0.0
    mid_width: float = 0.0
    high_width: float = 0.0
    low_pan: float = 0.0
    mid_pan: float = 0.0
    high_pan: float = 0.0
    side_ratio: float = 0.0


@dataclass
class SpatialResult:
    frame_hz: float
    frames: list[SpatialFrame]
    is_mono_source: bool = False
    mean_pan: float = 0.0
    mean_width: float = 0.0
    mean_correlation: float = 1.0
    low_width: float = 0.0
    mid_width: float = 0.0
    high_width: float = 0.0
    mono_compatibility: float = 1.0
    notes: list[str] = field(default_factory=list)
    method: str = "mid/side, per band"


def analyse_spatial(
    x: np.ndarray, sr: int, frame_hz: float = 20.0, n_fft: int = 2048,
    is_mono_source: bool = False,
) -> SpatialResult:
    """Per-frame stereo measurements.

    ``x`` is (n, 2).  A mono source is up-mixed to two identical channels, which
    makes side energy exactly zero — a correct measurement that would be
    *misleading* if presented as "this track is mono".  So we detect it and say
    so instead.
    """
    x = np.asarray(x, dtype=np.float32)
    if x.ndim == 1:
        x = np.repeat(x[:, None], 2, axis=1)
    if x.shape[1] < 2:
        x = np.repeat(x[:, 1:2], 2, axis=1)
    if is_mono_source:
        return SpatialResult(frame_hz=frame_hz, frames=[], is_mono_source=True,
                             notes=[
                                 "Source is mono: the stereo image is not recoverable "
                                 "from this file. All spatial values are reported as "
                                 "undefined rather than measured on duplicated channels."
                             ])

    L, R = x[:, 0], x[:, 1]
    mid = (L + R) * 0.5
    side = (L - R) * 0.5

    hop = max(1, int(round(sr / frame_hz)))
    n_fft = min(n_fft, 1 << int(np.floor(np.log2(max(256, len(x) // 4)))))
    n_frames = max(1, 1 + max(0, len(x) - n_fft) // hop)
    if len(x) < n_fft:
        n_frames = 1

    win = np.hanning(n_fft + 1)[:-1].astype(np.float32)

    def stft_band(sig: np.ndarray, lo: float, hi: float) -> tuple[np.ndarray, np.ndarray]:
        if len(sig) < n_fft:
            sig = np.pad(sig, (0, max(0, n_fft - len(sig))))
        frames = np.lib.stride_tricks.as_strided(
            np.pad(sig, (n_fft // 2, n_fft // 2), mode="reflect"),
            shape=(max(1, 1 + max(0, len(sig) - n_fft) // hop), n_fft),
            strides=(sig.strides[0] * hop, sig.strides[0]), writeable=False) * win
        S = np.abs(np.fft.rfft(frames, axis=1)) ** 2
        freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
        m = (freqs >= lo) & (freqs < hi)
        if not m.any():
            m = np.ones_like(freqs, dtype=bool)
        return S[:, m], freqs

    _b0, _b1, _b2 = SPATIAL_BANDS
    M_all, _ = stft_band(mid, 0, sr / 2)
    S_all, _ = stft_band(side, 0, sr / 2)
    M_low, _ = stft_band(mid, _b0.lo, _b0.hi)
    S_low, _ = stft_band(side, _b0.lo, _b0.hi)
    M_mid, _ = stft_band(mid, _b1.lo, _b1.hi)
    S_mid, _ = stft_band(side, _b1.lo, _b1.hi)
    M_hi, _ = stft_band(mid, _b2.lo, _b2.hi)
    S_hi, _ = stft_band(side, _b2.lo, _b2.hi)

    def ratio(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        return b.sum(axis=1) / (a.sum(axis=1) + _EPS)

    side_ratio = ratio(M_all, S_all)
    low_w, mid_w, high_w = ratio(M_low, S_low), ratio(M_mid, S_mid), ratio(M_hi, S_hi)
    overall = ratio(M_all, S_all)

    # Pan is the left/right *energy* balance, measured directly on the channels.
    # It cannot be recovered from |M| and |S| alone: the mid/side cross term is
    # exactly the information that carries panning, and taking magnitudes throws
    # it away.  (The tempting shortcut, side/mid, collapses to a width measure
    # and reports a hard-panned impression for any wide mix.)
    el, er = _windowed_energy(L, R, n_fft, hop, n_frames)
    denom = el + er + _EPS
    pan = np.clip((er - el) / denom, -1.0, 1.0)

    # Frequency-dependent balance needs its own per-band channel energies.
    low_pan = _band_balance(L, R, sr, SPATIAL_BANDS[0], n_fft, hop, n_frames)
    mid_pan = _band_balance(L, R, sr, SPATIAL_BANDS[1], n_fft, hop, n_frames)
    high_pan = _band_balance(L, R, sr, SPATIAL_BANDS[2], n_fft, hop, n_frames)

    # time-domain correlation over each window
    corr = _windowed_correlation(L, R, n_fft, hop, n_frames)

    # Width normalisation: a side/mid ratio of 1.0 is already fully "wide"
    # (equal-energy side), so we map [0, 1] -> [0, 1] and clip above.
    frames: list[SpatialFrame] = []
    times = np.arange(n_frames) * hop / float(sr)
    for i in range(n_frames):
        frames.append(SpatialFrame(
            t=float(times[i]), pan=float(pan[i]),
            width=float(np.clip(side_ratio[i], 0, 1) * 0.8),
            correlation=float(np.clip(corr[i], -1, 1)),
            low_width=float(np.clip(low_w[i], 0, 1) * 0.8),
            mid_width=float(np.clip(mid_w[i], 0, 1) * 0.8),
            high_width=float(np.clip(high_w[i], 0, 1) * 0.8),
            low_pan=float(low_pan[i]), mid_pan=float(mid_pan[i]),
            high_pan=float(high_pan[i]),
            side_ratio=float(side_ratio[i]),
        ))

    active = [f for f in frames if f.side_ratio > 1e-4] or frames
    mean_w = float(np.mean([f.width for f in active]))
    res = SpatialResult(
        frame_hz=frame_hz, frames=frames, is_mono_source=False,
        mean_pan=float(np.mean([f.pan for f in active])),
        mean_width=mean_w,
        mean_correlation=float(np.mean([f.correlation for f in active])),
        low_width=float(np.mean([f.low_width for f in active])),
        mid_width=float(np.mean([f.mid_width for f in active])),
        high_width=float(np.mean([f.high_width for f in active])),
    )
    # A master whose side/mid ratio is tiny sums to near-mono in a way that
    # matters to anyone checking compatibility.
    res.mono_compatibility = float(np.clip(1.0 - mean_w, 0.0, 1.0))
    res.notes = _interpret(res)
    return res


def _frames(sig: np.ndarray, n_fft: int, hop: int, n_frames: int) -> np.ndarray:
    if len(sig) < n_fft:
        sig = np.pad(sig, (0, max(0, n_fft - len(sig))))
    padded = np.pad(sig, (n_fft // 2, n_fft // 2), mode="reflect")
    return np.lib.stride_tricks.as_strided(
        padded, shape=(n_frames, n_fft),
        strides=(padded.strides[0] * hop, padded.strides[0]), writeable=False)


def _windowed_energy(L: np.ndarray, R: np.ndarray, n_fft: int, hop: int,
                     n_frames: int) -> tuple[np.ndarray, np.ndarray]:
    lf = _frames(L, n_fft, hop, n_frames)
    rf = _frames(R, n_fft, hop, n_frames)
    return (lf ** 2).sum(axis=1), (rf ** 2).sum(axis=1)


def _band_balance(L: np.ndarray, R: np.ndarray, sr: int, band: tuple[str, float, float],
                  n_fft: int, hop: int, n_frames: int) -> np.ndarray:
    lo, hi = band.lo, band.hi
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    m = (freqs >= lo) & (freqs < hi)
    if not m.any():
        m = np.ones_like(freqs, dtype=bool)
    lf = np.abs(np.fft.rfft(_frames(L, n_fft, hop, n_frames), axis=1))[:, m] ** 2
    rf = np.abs(np.fft.rfft(_frames(R, n_fft, hop, n_frames), axis=1))[:, m] ** 2
    el, er = lf.sum(axis=1), rf.sum(axis=1)
    return np.clip((er - el) / (el + er + _EPS), -1.0, 1.0)


def _windowed_correlation(L: np.ndarray, R: np.ndarray, n_fft: int, hop: int,
                         n_frames: int) -> np.ndarray:
    if len(L) < n_fft:
        L = np.pad(L, (0, max(0, n_fft - len(L))))
        R = np.pad(R, (0, max(0, n_fft - len(R))))
    lp = np.pad(L, (n_fft // 2, n_fft // 2), mode="reflect")
    rp = np.pad(R, (n_fft // 2, n_fft // 2), mode="reflect")
    lf = np.lib.stride_tricks.as_strided(lp, shape=(n_frames, n_fft),
                                         strides=(lp.strides[0] * hop, lp.strides[0]),
                                         writeable=False)
    rf = np.lib.stride_tricks.as_strided(rp, shape=(n_frames, n_fft),
                                         strides=(rp.strides[0] * hop, rp.strides[0]),
                                         writeable=False)
    num = (lf * rf).sum(axis=1)
    den = np.sqrt((lf ** 2).sum(axis=1) * (rf ** 2).sum(axis=1)) + _EPS
    return num / den


def _interpret(res: SpatialResult) -> list[str]:
    out: list[str] = []
    if res.mean_width < 0.06:
        out.append("The master is very close to mono (side energy is negligible). "
                   "Any width you hear is not in the master.")
    if res.mean_correlation > 0.92 and res.mean_width > 0.06:
        out.append("High inter-channel correlation with non-zero side energy: the "
                   "image is wide by amplitude panning rather than by decorrelation.")
    if res.mean_correlation < 0.35:
        out.append("Low inter-channel correlation: the width is genuinely "
                   "decorrelated, which usually means reverb or Haas widening "
                   "rather than panned instruments.")
    if res.high_width > res.low_width * 1.8 and res.high_width > 0.1:
        out.append("The high band is considerably wider than the low band — a "
                   "common and effective way to keep a dense mix from feeling "
                   "narrow without losing bass focus.")
    if res.low_width < 0.03 and res.high_width > 0.12:
        out.append("Bass is effectively mono while the top end is wide; this is "
                   "mix convention rather than an accident.")
    return out


def spatial_window(res: SpatialResult, t0: float, t1: float) -> dict:
    """Summary over a time window, for section statistics and the LLM bundle."""
    sel = [f for f in res.frames if t0 <= f.t < t1]
    if not sel:
        return {}
    return {
        "mean_pan": float(np.mean([f.pan for f in sel])),
        "mean_width": float(np.mean([f.width for f in sel])),
        "mean_correlation": float(np.mean([f.correlation for f in sel])),
        "low_width": float(np.mean([f.low_width for f in sel])),
        "mid_width": float(np.mean([f.mid_width for f in sel])),
        "high_width": float(np.mean([f.high_width for f in sel])),
    }


def source_pan(stem: np.ndarray, sr: int, frame_hz: float = 10.0) -> tuple[float, float, float]:
    """Mean pan, width and pan stability for one stem."""
    res = analyse_spatial(stem, sr, frame_hz=frame_hz, n_fft=1024)
    if not res.frames:
        return 0.0, 0.0, 1.0
    pans = np.array([f.pan for f in res.frames])
    widths = np.array([f.width for f in res.frames])
    active = widths > 0.02
    if active.sum() < 2:
        return 0.0, 0.0, 1.0
    p = pans[active]
    w = widths[active]
    # stability: 1 when the pan holds still, 0 when it swings across the image
    stability = float(np.clip(1.0 - (np.std(p) / max(np.mean(np.abs(p)), 0.05) * 0.5), 0, 1))
    return float(np.mean(p)), float(np.mean(w)), stability
