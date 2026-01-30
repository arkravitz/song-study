"""Frame-level spectral / temporal feature extraction.

Design notes
------------
* Features are computed on a **shared frame grid** so that every downstream
  consumer (activity detection, energy, spatial, LLM bundles) can index into
  them interchangeably.  ``STFT.frame_hz`` is the single source of truth.
* Memory: a 5-minute track at 86 Hz is only ~26k frames, so we happily keep the
  full spectrogram resident.  That lets later stages re-slice without recompute.
* Band energies use a fixed *named* filterbank rather than mel bands, because
  the UI needs to say "low-mid energy rose", not "band 23 rose".
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Named frequency bands.  Boundaries are the -3 dB crossover points an
# engineer would actually name.
BANDS: list[tuple[str, float, float]] = [
    ("sub", 20, 60),
    ("bass", 60, 160),
    ("low_bass", 160, 320),
    ("low_mid", 320, 800),
    ("mid", 800, 2000),
    ("upper_mid", 2000, 4000),
    ("presence", 4000, 7000),
    ("brilliance", 7000, 11000),
    ("air", 11000, 20000),
]

BAND_ORDER = [b[0] for b in BANDS]
BAND_LOW = np.array([b[1] for b in BANDS])
BAND_HIGH = np.array([b[2] for b in BANDS])


def band_index_for_hz(hz: float) -> int:
    for i, (_, lo, hi) in enumerate(BANDS):
        if lo <= hz < hi:
            return i
    return len(BANDS) - 1 if hz >= BANDS[-1][2] else 0


def _hz_to_mel(f: np.ndarray | float) -> np.ndarray | float:
    return 2595.0 * np.log10(1.0 + np.asarray(f) / 700.0)


def _mel_to_hz(m: np.ndarray | float) -> np.ndarray | float:
    return 700.0 * (10.0 ** (np.asarray(m) / 2595.0) - 1.0)


def _hz_to_semitone(f: np.ndarray | float) -> np.ndarray | float:
    return 12.0 * np.log2(np.maximum(np.asarray(f), 1e-6) / 16.3516)  # C0 = 16.35 Hz


def hz_to_note(hz: float) -> str:
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    if hz <= 0:
        return "?"
    midi = int(round(69 + 12 * np.log2(hz / 440.0)))
    return f"{names[midi % 12]}{midi // 12 - 1}"


@dataclass
class STFT:
    """A computed short-time Fourier transform plus the grid it lives on."""

    stft: np.ndarray  # (frames, bins) complex128
    freqs: np.ndarray
    times: np.ndarray
    sr: int
    n_fft: int
    hop: int
    n_frames: int
    window: np.ndarray = field(repr=False, default=None)

    @property
    def frame_hz(self) -> float:
        return self.sr / self.hop

    @property
    def duration(self) -> float:
        return float(self.times[-1]) if len(self.times) else 0.0

    def mag(self) -> np.ndarray:
        return np.abs(self.stft).astype(np.float32)

    def power(self) -> np.ndarray:
        return (np.abs(self.stft) ** 2).astype(np.float32)


def stft(
    y: np.ndarray,
    sr: int,
    n_fft: int = 2048,
    hop: int = 512,
    center: bool = True,
) -> STFT:
    """Centred STFT with a Hann window.

    `center=True` mirrors-pads so frame i is centred on sample i*hop — this
    matters a lot for onsets, where a half-hop offset is very audible.
    """
    y = np.asarray(y, dtype=np.float32)
    if y.ndim == 1:
        y = y[:, None]
    y = y.mean(axis=1) if y.shape[1] > 1 else y[:, 0]

    win = np.hanning(n_fft + 1)[:-1].astype(np.float32)
    n_frames = 1 + int(np.ceil(len(y) / hop)) if center else 1 + max(0, len(y) - n_fft) // hop
    n_frames = max(1, n_frames)

    from scipy.signal import get_window  # noqa: F401  (kept for clarity of intent)
    import scipy.fftpack as fftpack

    if center:
        pad = n_fft // 2
        ypad = np.pad(y, (pad, pad), mode="reflect" if len(y) > pad else "constant")
    else:
        ypad = y
    n_frames = 1 + max(0, len(ypad) - n_fft) // hop

    # Strided framing without a Python loop over frames.
    strides = (ypad.strides[0] * hop, ypad.strides[0])
    frames = np.lib.stride_tricks.as_strided(
        ypad, shape=(n_frames, n_fft), strides=strides, writeable=False
    )
    frames = frames * win
    spec = fftpack.fft(frames, n=n_fft, axis=1)
    spec = spec[:, : n_fft // 2 + 1]

    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    times = np.arange(n_frames) * hop / float(sr)
    return STFT(stft=spec, freqs=freqs, times=times, sr=sr, n_fft=n_fft, hop=hop,
                n_frames=n_frames, window=win)


def band_filterbank(
    freqs: np.ndarray, bands: list[tuple[str, float, float]] | None = None
) -> np.ndarray:
    """(n_bands, n_bins) triangular filterbank, power-normalised."""
    bands = bands or BANDS
    fb = np.zeros((len(bands), len(freqs)), dtype=np.float32)
    for i, (_, lo, hi) in enumerate(bands):
        if hi <= lo:
            continue
        if lo <= 0:
            rise = np.ones_like(freqs)
        else:
            rise = np.clip((freqs - (lo - (hi - lo) * 0.5)) / max(1e-6, (hi - lo) * 0.5), 0, 1)
        fall = np.clip(((hi + (hi - lo) * 0.5) - freqs) / max(1e-6, (hi - lo) * 0.5), 0, 1)
        fb[i] = rise * fall
        s = fb[i].sum()
        if s > 0:
            fb[i] /= s  # unity gain per band
    return fb


def mel_filterbank(freqs: np.ndarray, n_mels: int = 96, fmin: float = 20, fmax: float | None = None,
                   sr: int = 44100, slaney: bool = True) -> np.ndarray:
    fmax = fmax or sr / 2
    mels = np.linspace(_hz_to_mel(fmin), _hz_to_mel(fmax), n_mels + 2)
    hz = _mel_to_hz(mels)
    fb = np.zeros((n_mels, len(freqs)), dtype=np.float32)
    fdiff = np.diff(hz)
    ramps = hz[:, None] - freqs[None, :]
    for i in range(n_mels):
        lower = -ramps[i] / max(fdiff[i], 1e-6)
        upper = ramps[i + 2] / max(fdiff[i + 1], 1e-6)
        fb[i] = np.maximum(0, np.minimum(lower, upper))
        if slaney:
            enorm = 2.0 / (hz[i + 2] - hz[i])
            fb[i] *= enorm
        s = fb[i].sum()
        if s > 0:
            fb[i] /= s
    return fb


# ---------------------------------------------------------------------------
# Feature bundle
# ---------------------------------------------------------------------------


@dataclass
class FrameFeatures:
    """All per-frame features on a shared grid."""

    frame_hz: float
    duration: float
    times: np.ndarray
    db: np.ndarray                  # (n, C) level per band
    band_db: np.ndarray             # (n, n_bands)
    band_names: list[str]
    centroid: np.ndarray            # Hz
    bandwidth: np.ndarray           # Hz
    rolloff85: np.ndarray           # Hz
    flatness: np.ndarray            # 0..1
    contrast: np.ndarray            # (n, n_contrast) spectral contrast
    flux: np.ndarray                # positive spectral flux
    flux_harm: np.ndarray
    flux_perc: np.ndarray
    hp_ratio: np.ndarray            # H/P energy ratio 0..1
    zcr: np.ndarray
    rms_db: np.ndarray
    peak_db: np.ndarray
    spectral_tilt: np.ndarray       # dB/octave, negative = dark
    mfcc: np.ndarray | None = None
    n_fft: int = 2048
    sr: int = 44100

    def n(self) -> int:
        return len(self.times)

    def as_dict(self) -> dict[str, np.ndarray]:
        d = {
            "db": self.db, "band_db": self.band_db, "centroid": self.centroid,
            "bandwidth": self.bandwidth, "rolloff85": self.rolloff85,
            "flatness": self.flatness, "flux": self.flux, "flux_harm": self.flux_harm,
            "flux_perc": self.flux_perc, "hp_ratio": self.hp_ratio, "zcr": self.zcr,
            "rms_db": self.rms_db, "peak_db": self.peak_db,
            "spectral_tilt": self.spectral_tilt,
        }
        if self.mfcc is not None:
            d["mfcc"] = self.mfcc
        if self.contrast is not None and self.contrast.size:
            d["contrast"] = self.contrast
        return d


_EPS = 1e-10


def _db(x: np.ndarray | float, floor: float = -120.0) -> np.ndarray:
    return np.maximum(20.0 * np.log10(np.maximum(np.abs(x), _EPS)), floor)


def compute_frame_features(
    y: np.ndarray,
    sr: int,
    n_fft: int = 2048,
    hop: int = 512,
    n_mfcc: int = 24,
    compute_mfcc: bool = True,
) -> tuple[FrameFeatures, STFT]:
    """Compute the full frame-feature bundle for a mono signal."""
    S = stft(y, sr, n_fft=n_fft, hop=hop)
    mag = np.abs(S.stft).astype(np.float32)
    power = mag ** 2
    n, nb = mag.shape
    freqs = S.freqs
    times = S.times

    # --- band energies ---------------------------------------------------
    fb = band_filterbank(freqs)
    band_power = power @ fb.T                       # (n, n_bands)
    band_db = _db(np.sqrt(band_power), -100.0)
    total_power = power.sum(axis=1, keepdims=True) + _EPS
    band_rel = band_power / total_power

    # --- spectral shape --------------------------------------------------
    centroid = (power * freqs[None, :]).sum(axis=1) / total_power[:, 0]
    spread = np.sqrt((power * (freqs[None, :] - centroid[:, None]) ** 2).sum(axis=1) / total_power[:, 0])
    csum = np.cumsum(power, axis=1)
    roll_idx = np.argmax(csum >= 0.85 * total_power, axis=1)
    rolloff = freqs[roll_idx]
    gmean = np.exp(np.log(power + _EPS).mean(axis=1))
    amean = power.mean(axis=1) + _EPS
    flatness = np.clip(gmean / amean, 0, 1)

    # spectral contrast: difference of mean energy in log-f bands (14 bands)
    contrast = _spectral_contrast(power, freqs, sr)

    # --- flux ------------------------------------------------------------
    diff = np.diff(power, axis=0, prepend=power[:1])
    flux = np.maximum(diff, 0).sum(axis=1) / (total_power[:, 0] + _EPS)

    # --- harmonic/percussive split (HPSS, median filtering) --------------
    harm, perc = _hpss(power, n_fft)
    hp_ratio = perc.sum(axis=1) / (harm.sum(axis=1) + perc.sum(axis=1) + _EPS)
    diffh = np.diff(harm, axis=0, prepend=harm[:1])
    diffp = np.diff(perc, axis=0, prepend=perc[:1])
    flux_harm = np.maximum(diffh, 0).sum(axis=1) / (harm.sum(axis=1) + _EPS)
    flux_perc = np.maximum(diffp, 0).sum(axis=1) / (perc.sum(axis=1) + _EPS)

    # --- time-domain -----------------------------------------------------
    win = np.hanning(n_fft + 1)[:-1].astype(np.float32)
    frames = _frame_signal(np.asarray(y, np.float32), n_fft, hop, S.n_frames)
    rms = np.sqrt((frames ** 2).mean(axis=1) + _EPS)
    peak = np.abs(frames).max(axis=1)
    zcr = (np.diff(np.signbit(frames), axis=1).sum(axis=1) / n_fft).astype(np.float32)

    # --- spectral tilt: linear fit of dB vs log2(f), dB per octave --------
    valid = freqs > 40
    lf = np.log2(frefs := freqs[valid])
    pdb = 10.0 * np.log10(power[:, valid] + _EPS)
    lf_c = lf - lf.mean()
    denom = float((lf_c ** 2).sum()) + _EPS
    tilt = (pdb - pdb.mean(axis=1, keepdims=True)) @ lf_c / denom

    mfcc = None
    if compute_mfcc:
        mfcc = _mfcc(mag, freqs, sr, n_mfcc)

    feats = FrameFeatures(
        frame_hz=S.frame_hz, duration=float(times[-1]) if len(times) else 0.0,
        times=times, db=_db(rms), band_db=band_db, band_names=list(BAND_ORDER),
        centroid=centroid.astype(np.float32), bandwidth=spread.astype(np.float32),
        rolloff85=rolloff.astype(np.float32), flatness=flatness.astype(np.float32),
        contrast=contrast, flux=flux.astype(np.float32),
        flux_harm=flux_harm.astype(np.float32), flux_perc=flux_perc.astype(np.float32),
        hp_ratio=hp_ratio.astype(np.float32), zcr=zcr,
        rms_db=_db(rms, -100).astype(np.float32), peak_db=_db(peak, -100).astype(np.float32),
        spectral_tilt=tilt.astype(np.float32), mfcc=mfcc, n_fft=n_fft, sr=sr,
    )
    return feats, S


def _frame_signal(y: np.ndarray, n_fft: int, hop: int, n_frames: int) -> np.ndarray:
    pad = n_fft // 2
    ypad = np.pad(y, (pad, pad), mode="reflect" if len(y) > pad else "constant")
    strides = (ypad.strides[0] * hop, ypad.strides[0])
    out = np.lib.stride_tricks.as_strided(ypad, shape=(n_frames, n_fft),
                                          strides=strides, writeable=False)
    return out.astype(np.float32, copy=True)


def _spectral_contrast(power: np.ndarray, freqs: np.ndarray, sr: int,
                       n_bands: int = 14) -> np.ndarray:
    """Buch-esque spectral contrast: octave band peaks minus valley means."""
    edges = np.geomspace(40, sr / 2, n_bands + 1)
    out = np.zeros((power.shape[0], n_bands), dtype=np.float32)
    for i in range(n_bands):
        m = (freqs >= edges[i]) & (freqs < edges[i + 1])
        if not m.any():
            continue
        band = power[:, m]
        db = 10.0 * np.log10(band + _EPS)
        out[:, i] = np.percentile(db, 92, axis=1) - np.percentile(db, 12, axis=1)
    return out


def _hpss(power: np.ndarray, n_fft: int, margin: float = 2.0) -> tuple[np.ndarray, np.ndarray]:
    """Median-filter H/P separation (Fitzgerald 2010) on the power spectrogram."""
    from scipy.ndimage import median_filter

    n_bins = power.shape[1]
    win_freq = int(round(31 * 2048 / n_fft)) | 1
    win_time = int(round(31 * 2048 / n_fft)) | 1
    h = median_filter(power, size=(1, win_freq), mode="nearest")
    p = median_filter(power, size=(win_time, 1), mode="nearest")
    alpha = power ** 2
    harm = alpha * h ** 2 / (h ** 2 + p ** 2 + _EPS)
    perc = alpha * p ** 2 / (h ** 2 + p ** 2 + _EPS)
    return harm, perc


def _mfcc(mag: np.ndarray, freqs: np.ndarray, sr: int, n_mfcc: int) -> np.ndarray:
    from scipy.fftpack import dct

    melfb = mel_filterbank(freqs, n_mels=64, sr=sr)
    mel = mag @ melfb.T
    logmel = np.log(mel + 1e-6)
    return dct(logmel, type=2, axis=1, norm="ortho")[:, :n_mfcc].astype(np.float32)


def normalize_minmax(x: np.ndarray, lo_pct: float = 1.0, hi_pct: float = 99.0) -> tuple[np.ndarray, float, float]:
    lo = float(np.percentile(x, lo_pct))
    hi = float(np.percentile(x, hi_pct))
    if hi - lo < 1e-9:
        hi = lo + 1.0
    return np.clip((x - lo) / (hi - lo), 0, 1), lo, hi


def smooth(x: np.ndarray, win: int, mode: str = "hann") -> np.ndarray:
    """Edge-preserving-ish smoothing with a proper window (no wraparound)."""
    if win <= 1 or len(x) < 3:
        return x
    if mode == "hann":
        k = np.hanning(win + 2)[1:-1]
    elif mode == "hamming":
        k = np.hamming(win)
    else:
        k = np.ones(win)
    k = k / k.sum()
    pad = win // 2
    xp = np.pad(x, (pad, pad), mode="edge")
    return np.convolve(xp, k, mode="valid")[: len(x)]
