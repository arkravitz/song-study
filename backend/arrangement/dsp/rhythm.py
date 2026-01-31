"""Rhythm: beat tracking, tempo, meter, onsets, and density measures.

When All-In-One MLX is available on Apple Silicon, its beat/downbeat grid and
BPM enter here for onset-based quality checks and meter reasoning. Otherwise
CPFN's *Beat This* transformer tracks the grid, with a librosa fallback.

Three things matter more here than raw beat accuracy:

* **Tempo.**  A tracker reports *a* pulse, which by the MIR convention *is* the
  beat, so the median inter-beat interval is the tempo.  Recovering a "felt
  tempo" from a tempogram is a trap: a tempogram peaks equally at the beat,
  half-beat, double-beat and bar, because every sample taken at twice the beat
  period is still on a beat.  The tempogram is used here only for *confidence*.
* **Metrical level.**  The one real ambiguity is whether the reported pulse is
  the beat or every other beat of a double-time record.  We test that directly
  by comparing onset energy at the beats against the energy at the midpoints
  between them, and report an explicit note when it is genuinely ambiguous.
* **Meter.**  Downbeat intervals vote for 4/4, 3/4, 6/8 and friends; without
  this, bar numbering and "every 4 bars" reasoning are wrong.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger(__name__)

_EPS = 1e-10

#: Tempi a producer actually taps.  Outside this we fold the tracked pulse by an
#: exact metrical factor and lower the confidence.
TEMPO_RANGE = (50.0, 210.0)


@dataclass
class RhythmResult:
    beats: np.ndarray
    downbeats: np.ndarray
    bpm: float
    bpm_confidence: float
    meter: str
    meter_confidence: float
    offset: float
    method: str
    bpm_curve: list[tuple[float, float]]
    onsets: np.ndarray
    onset_strength: np.ndarray
    onset_hz: float
    metrical_confidence: float
    is_tangent: bool
    notes: list[str] = field(default_factory=list)
    onset_env: np.ndarray | None = None
    #: onset-energy fraction landing on the 0.5x / 1x / 2x beat grid
    grid_strength: dict[str, float] = field(default_factory=dict)
    metrical_level_note: str | None = None
    boundary_error: float = 0.0

    @property
    def tempo_drift(self) -> float:
        """Spread of the local-tempo curve in BPM, as a 5-95 percentile range.

        This was ``max - min``, which is the most outlier-sensitive statistic
        available and reported Kendrick Lamar's "DNA." at 119.6 BPM of drift
        while the track's own inter-beat-interval spread was 1.10 — the grid was
        stable. A single beat at half the period puts a 273 BPM sample on the
        curve and the range reports the difference as if the track were changing
        tempo. Read as "119 BPM of drift", that is alarming and meaningless, and
        the user is right to distrust every other number on the screen.

        The 5-95 range ignores the tails, which is what we want: the question is
        whether the *pulse* moves, not whether the tracker ever mis-placed a beat.
        Genuinely rubato material still shows up, and `tempo_instable` flags it.
        """
        if len(self.bpm_curve) < 2:
            return 0.0
        vals = sorted(b for _, b in self.bpm_curve if b > 0)
        if len(vals) < 8:
            return 0.0
        if len(vals) < 20:
            return float(vals[-1] - vals[0])
        import numpy as _np
        lo, hi = _np.percentile(vals, [5, 95])
        return float(hi - lo)

    @property
    def tempo_instable(self) -> bool:
        """True when even the robust spread says the pulse genuinely moves.

        Deliberately a much higher bar than "drift is non-zero". Most commercial
        records have a few BPM of drift from performance and mastering, and
        flagging that as instability trains the user to ignore the flag.
        """
        d = self.tempo_drift
        return d > 12.0 or self.is_tangent


# ---------------------------------------------------------------------------
# Onsets
# ---------------------------------------------------------------------------


def onset_envelope(y: np.ndarray, sr: int, hop: int = 256):
    """Percussive onset strength via mel-spectrogram flux.

    Returns ``(peak_times, peak_values, envelope, envelope_hz)``.  Peak picking
    is done here rather than via ``librosa.util.peak_pick`` so thresholds stay
    in musical units and behave identically across librosa versions.
    """
    import librosa

    S = librosa.feature.melspectrogram(
        y=y, sr=sr, n_fft=2048, hop_length=hop, n_mels=128,
        fmax=min(11025.0, sr / 2), power=2.0, htk=False,
    )
    logmel = np.log1p(S)
    flux = np.maximum(0.0, np.diff(logmel, axis=1, prepend=logmel[:, :1])).sum(axis=0)
    hi = float(np.percentile(flux, 99.5))
    flux = flux / max(hi, _EPS)
    onset_hz = sr / hop
    times = librosa.frames_to_time(np.arange(len(flux)), sr=sr, hop_length=hop)
    frames = peak_pick(flux, onset_hz, delta=0.09, wait_s=0.045)
    return (np.asarray(times[frames], dtype=np.float64),
            np.asarray(flux[frames], dtype=np.float64),
            flux, onset_hz)


def peak_pick(
    env: np.ndarray, env_hz: float, pre_max_s: float = 0.03, post_max_s: float = 0.03,
    pre_avg_s: float = 0.10, post_avg_s: float = 0.10, delta: float = 0.09,
    wait_s: float = 0.045,
) -> np.ndarray:
    """SuperFlux-style peak picking (Böck & Widmer 2013), version-stable."""
    env = np.asarray(env, dtype=np.float64)
    if env.size == 0:
        return np.array([], dtype=int)
    n = len(env)
    pre_max = max(1, int(round(pre_max_s * env_hz)))
    post_max = max(1, int(round(post_max_s * env_hz)))
    pre_avg = max(1, int(round(pre_avg_s * env_hz)))
    post_avg = max(1, int(round(post_avg_s * env_hz)))
    wait = max(0, int(round(wait_s * env_hz)))

    out: list[int] = []
    last = -(10 ** 9)
    for i in range(n):
        lo, hi = max(0, i - pre_max), min(n, i + post_max + 1)
        if env[i] < np.max(env[lo:hi]):
            continue
        alo, ahi = max(0, i - pre_avg), min(n, i + post_avg + 1)
        if env[i] < float(np.mean(env[alo:ahi])) + delta:
            continue
        if i + 1 < min(n, i + wait + 1) and env[i] < np.max(env[i + 1: min(n, i + wait + 1)]):
            continue
        if i - last < wait:
            if out and env[i] > env[out[-1]]:
                out[-1] = i
                last = i
            continue
        out.append(i)
        last = i
    return np.asarray(out, dtype=int)


def _write_wav_fast(path: str, y: np.ndarray, sr: int) -> None:
    import soundfile as sf

    d = np.asarray(y, dtype=np.float32)
    if d.ndim == 2:
        d = d.mean(axis=1)
    sf.write(path, d, sr, subtype="PCM_16")


# ---------------------------------------------------------------------------
# Beat tracking
# ---------------------------------------------------------------------------

_BT_CACHE: dict[tuple[str, str], object] = {}


def _get_beat_this(device: str):
    key = ("final0", device)
    if key in _BT_CACHE:
        return _BT_CACHE[key]
    from beat_this.inference import File2Beats

    model = File2Beats("final0", device=device, dbn=False)
    _BT_CACHE[key] = model
    return model


def _disambiguate_beats(a, b, tol: float = 0.03) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(beats, downbeats)`` regardless of the caller's return order.

    Beat This's documented return order disagrees with its actual behaviour
    across versions, so we identify the sets structurally: downbeats are the
    subset contained in the beat set.
    """
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    if a.size == 0 or b.size == 0:
        return (a, b) if a.size >= b.size else (b, a)

    def contained_in(small: np.ndarray, big: np.ndarray) -> float:
        if small.size == 0 or big.size == 0:
            return 0.0
        i = np.clip(np.searchsorted(big, small), 0, len(big) - 1)
        d = np.minimum(np.abs(big[i] - small), np.abs(big[np.clip(i - 1, 0, len(big) - 1)] - small))
        return float(np.mean(d <= tol))

    # Whichever set is the superset is the beat grid.
    return (a, b) if contained_in(b, a) > contained_in(a, b) + 0.05 else (b, a)


def track_beats(y: np.ndarray, sr: int, device: str = "cpu"):
    """Returns ``(beats, downbeats, method, confidence)``."""
    model = _get_beat_this(device)
    with tempfile.TemporaryDirectory(prefix="bt_") as td:
        tmp = os.path.join(td, "in.wav")
        _write_wav_fast(tmp, y, sr)
        out = model(tmp)
    beats, downbeats = _disambiguate_beats(out[0], out[1])
    return beats, downbeats, "beat_this", (0.85 if len(beats) >= 8 else 0.1)


def librosa_fallback(y: np.ndarray, sr: int):
    import librosa

    hop = 512
    env = np.asarray(librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop),
                     dtype=np.float64)
    tempo, beats = librosa.beat.beat_track(
        y=y, sr=sr, units="time", trim=False, hop_length=hop, sparse=True)
    frames = peak_pick(env, sr / hop)
    times = librosa.frames_to_time(frames, sr=sr, hop_length=hop)
    db_times = np.array([], dtype=np.float64)
    b = np.asarray(beats, dtype=np.float64)
    if len(b) >= 4 and float(tempo) > 0:
        step = max(1, int(round(60.0 / float(tempo) / hop * 4)))
        db_times = librosa.frames_to_time(
            np.arange(0, len(env), step, dtype=int), sr=sr, hop_length=hop)
    return np.asarray(beats, dtype=np.float64), np.asarray(db_times, dtype=np.float64), \
        "librosa", 0.45


# ---------------------------------------------------------------------------
# Tempo / meter reasoning
# ---------------------------------------------------------------------------


def tempo_tempogram(env: np.ndarray, env_hz: float, bpm_min: float = 40.0,
                    bpm_max: float = 220.0, n_periods: int = 420):
    """Tempogram of the onset envelope, scored as a z-score.

    For each candidate period we sample the envelope at grid positions over all
    phases and ask how far the sample stands above the envelope mean, in
    standard deviations.  Returns ``(periods, z, phases)``.  Used for
    confidence, not for choosing the tempo — see the module docstring.
    """
    env = np.asarray(env, dtype=np.float64)
    n = len(env)
    if n < 32:
        return np.array([]), np.array([]), np.array([])
    baseline, spread = float(env.mean()), float(env.std())
    if spread <= 1e-12:
        return np.array([]), np.array([]), np.array([])
    t = np.arange(n) / env_hz

    periods = 60.0 / np.geomspace(bpm_min, bpm_max, n_periods)
    z = np.zeros(len(periods))
    phase_out = np.zeros(len(periods))
    for i, p in enumerate(periods):
        if p * env_hz < 1.0 or p >= t[-1]:
            continue
        best, best_ph = -1e9, 0.0
        for k in range(16):
            ph = p * k / 16
            pos = np.arange(ph, t[-1] + 1e-9, p)
            if len(pos) < 4:
                continue
            zi = (float(np.interp(pos, t, env).mean()) - baseline) / spread
            if zi > best:
                best, best_ph = zi, ph
        if best > -1e8:
            z[i], phase_out[i] = best, best_ph
    return periods, z, phase_out


def grid_strength(onsets: np.ndarray, onset_strength: np.ndarray | None,
                  bpm: float, beat_times: np.ndarray | None = None) -> dict[str, float]:
    """Fraction of onset energy landing on the 0.5x / 1x / 2x beat grid.

    When real beat times are available they are used directly.  Reconstructing a
    grid as ``t mod (60/bpm)`` assumes the grid starts at t=0, which is wrong
    for any track with a pickup and quietly destroys the measurement.
    """
    out = {"0.5x": 0.0, "1x": 0.0, "2x": 0.0}
    if onsets is None or onset_strength is None or len(onsets) == 0 or bpm <= 0:
        return out
    w = np.clip(np.asarray(onset_strength, dtype=np.float64), 0, None)
    tot = float(w.sum())
    if tot <= 0:
        return out

    if beat_times is not None and len(beat_times) >= 4:
        bt = np.asarray(beat_times, dtype=np.float64)
        step = float(np.median(np.diff(bt)))
        for name, mult in (("0.5x", 0.5), ("1x", 1.0), ("2x", 2.0)):
            grid = _subdivide(bt, mult)
            d = np.abs(onsets[:, None] - grid[None, :]).min(axis=1)
            out[name] = float((np.exp(-((d / 0.06) ** 2)) * w).sum() / tot)
        return out

    for name, mult in (("0.5x", 0.5), ("1x", 1.0), ("2x", 2.0)):
        step = 60.0 / bpm * mult
        d = np.abs(np.mod(onsets, step))
        d = np.minimum(d, step - d)
        out[name] = float((np.exp(-((d / 0.06) ** 2)) * w).sum() / tot)
    return out


def _subdivide(beats: np.ndarray, mult: float) -> np.ndarray:
    """Insert ``mult - 1`` evenly spaced points between each pair of beats.

    ``mult`` may be fractional (< 1) to thin a grid out, in which case we
    subsample the beat list instead.
    """
    beats = np.asarray(beats, dtype=np.float64)
    if mult >= 1.0:
        k = int(round(mult))
        if k <= 1:
            return beats
        out = [beats]
        for j in range(1, k):
            out.append(beats[:-1] + (beats[1:] - beats[:-1]) * j / k)
        return np.sort(np.concatenate(out))
    keep = max(1, int(round(1.0 / mult)))
    return beats[::keep]


def metrical_level(beats: np.ndarray, onsets: np.ndarray | None) -> float | None:
    """Are the midpoints between beats *also* beats?

    For each beat and each beat-to-beat midpoint, ask whether a detected onset
    falls within 55 ms.  Returns ``(midpoint hit rate) / (beat hit rate)``.

    Using *onsets* rather than envelope energy is the important part: in dense
    material the midpoints carry as much energy as the beats (orchestral
    eighth-note beds, a waltz accompaniment) even when they are plainly not the
    pulse.  What distinguishes a genuine double-time pulse is that the
    midpoints are *attacks*, not just loud.
    """
    if onsets is None or len(onsets) < 8 or len(beats) < 12:
        return None
    b = np.asarray(beats, dtype=np.float64)
    onsets = np.asarray(onsets, dtype=np.float64)
    b = b[(b >= onsets[0]) & (b <= onsets[-1])]
    if len(b) < 12:
        return None
    tol = 0.055

    def hit_rate(grid: np.ndarray) -> float:
        d = np.abs(onsets[:, None] - grid[None, :]).min(axis=1)
        return float(np.mean(d < tol))

    mid = (b[:-1] + b[1:]) / 2.0
    rb, rm = hit_rate(b), hit_rate(mid)
    if rb < 0.35:  # we cannot measure the ratio against a weak beat grid
        return None
    return float(min(rm / rb, 1.5))


def tempo_curve(beats: np.ndarray, bpm: float, window: int = 16) -> list[tuple[float, float]]:
    """Local tempo, so rubato and live drift become visible."""
    if len(beats) < window * 2:
        return [(float(beats[0]) if len(beats) else 0.0, bpm)]
    out: list[tuple[float, float]] = []
    for i in range(0, len(beats) - window, max(1, window // 2)):
        # Beat timestamps are quantised to the tracker's frame grid. Measure
        # across several beats so 20 ms rounding does not masquerade as drift.
        span = max(1, window // 2)
        seg = (beats[i + span: i + window] - beats[i: i + window - span]) / span
        seg = seg[np.isfinite(seg) & (seg > 0.2)]
        if len(seg) < 3:
            continue
        local = 60.0 / float(np.median(seg))
        for mult in (0.5, 2.0):  # fold exact metrical relatives
            if abs(local / (bpm * mult) - 1.0) < 0.04:
                local = bpm * mult
                break
        out.append((float(beats[i]), float(local)))
    return out or [(float(beats[0]), bpm)]


def estimate_tempo(beats: np.ndarray, onsets: np.ndarray, onset_strength: np.ndarray,
                   onset_hz: float, duration: float, onset_env: np.ndarray | None = None):
    """Returns ``(bpm, confidence, bpm_curve, grid_strength)``."""
    if len(beats) < 4:
        return 0.0, 0.0, [], {"0.5x": 0.0, "1x": 0.0, "2x": 0.0}

    ioi = np.diff(beats)
    ioi = ioi[np.isfinite(ioi) & (ioi > 0.2)]
    if len(ioi) < 3:
        return 0.0, 0.0, [], {"0.5x": 0.0, "1x": 0.0, "2x": 0.0}
    # The tracker's 20 ms timestamps make an adjacent-beat median too coarse:
    # steady 113, 140, and 148 BPM grids read as 111, 136, and 150. A 16-beat
    # span averages the timestamp error down while the median across overlapping
    # spans remains robust to an occasional missed or doubled beat.
    span = min(16, max(1, (len(beats) - 1) // 2))
    long_ioi = (beats[span:] - beats[:-span]) / span
    long_ioi = long_ioi[np.isfinite(long_ioi) & (long_ioi > 0.2)]
    base_bpm = 60.0 / float(np.median(long_ioi if len(long_ioi) >= 3 else ioi))

    lo, hi = TEMPO_RANGE
    bpm, folded = base_bpm, False
    if base_bpm < lo:
        for mult in (2.0, 4.0):
            if lo <= base_bpm * mult <= hi:
                bpm, folded = base_bpm * mult, True
                break
    elif base_bpm > hi:
        for mult in (0.5, 0.25):
            if lo <= base_bpm * mult <= hi:
                bpm, folded = base_bpm * mult, True
                break

    env = onset_env
    if env is None and len(onsets) and onset_strength is not None:
        env = np.zeros(int(duration * onset_hz) + 1, dtype=np.float64)
        idx = np.clip((onsets * onset_hz).astype(int), 0, len(env) - 1)
        np.add.at(env, idx, np.clip(onset_strength, 0, None))

    z = 0.0
    if env is not None and len(env) > 32:
        periods, zsal, _ph = tempo_tempogram(env, onset_hz)
        if len(periods):
            p = 60.0 / bpm
            i = int(np.argmin(np.abs(periods - p)))
            if abs(periods[i] - p) <= p * 0.03:
                z = float(zsal[max(0, i - 8): i + 9].max())

    gstr = grid_strength(onsets, onset_strength, bpm, beat_times=beats)

    on_grid = gstr.get("1x", 0.0)
    conf = float(np.clip(0.45 * min(1.0, z / 1.6) + 0.55 * min(1.0, on_grid / 0.55), 0.0, 0.97))
    if folded:
        conf *= 0.75
    return float(bpm), conf, tempo_curve(beats, bpm), gstr


_METER_HINTS = {
    "4/4": [1, 2, 4, 8],
    "3/4": [1, 2, 3, 6],
    "6/8": [1, 2, 3, 6],
    "2/4": [1, 2, 4, 8],
    "5/4": [1, 2, 5, 10],
    "7/8": [1, 2, 3, 7, 14],
}


def estimate_meter(downbeats: np.ndarray, beats: np.ndarray, bpm: float):
    """Vote for a meter from the spacing of predicted downbeats."""
    if len(downbeats) < 2 or bpm <= 0:
        return "4/4", 0.2, downbeats
    gaps = np.diff(downbeats) / (60.0 / bpm)  # in beats
    gaps = gaps[np.isfinite(gaps) & (gaps > 0.4)]
    if len(gaps) == 0:
        return "4/4", 0.2, downbeats
    votes: dict[str, float] = {}
    for meter, options in _METER_HINTS.items():
        best = max(float(np.mean(np.exp(-(np.abs(gaps / o - 1.0) * 3.0) ** 2)))
                   for o in options if o > 0)
        votes[meter] = best
    meter = max(votes, key=votes.get)
    return meter, float(np.clip(votes[meter], 0, 1)), downbeats


def detect_metrical_position(beats: np.ndarray, downbeats: np.ndarray, meter: str):
    """Returns ``(offset_seconds, confidence, downbeat_beat_indices)``."""
    if len(beats) < 4:
        return 0.0, 0.0, []
    beats_per_bar = _beats_per_bar(meter)
    db = np.asarray(downbeats, dtype=np.float64)
    if len(db) == 0:
        period = float(np.median(np.diff(beats))) if len(beats) > 2 else 0.5
        return float(beats[0]), 0.3, list(range(0, len(beats), beats_per_bar))
    idx = []
    for t in db:
        i = int(np.argmin(np.abs(beats - t)))
        if abs(beats[i] - t) < 0.12:
            idx.append(i)
    if not idx:
        return float(db[0]), 0.2, []
    return float(beats[idx[0]]), 0.8, idx


def _beats_per_bar(meter: str) -> float:
    """Beats per bar in quarter-note equivalents.

    The tempo convention, so bar numbering lines up with how the tempo is
    reported: 4/4 -> 4, 3/4 -> 3, 6/8 -> 3 dotted quarters, 7/8 -> 3.5.
    """
    try:
        n, d = meter.split("/")
        return max(1.0, float(n) * 4.0 / float(d))
    except Exception:
        return 4.0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def analyse_rhythm(y: np.ndarray, sr: int, device: str = "cpu",
                   external_beats: np.ndarray | None = None,
                   external_downbeats: np.ndarray | None = None,
                   external_bpm: float | None = None) -> RhythmResult:
    """Full rhythm analysis with graceful degradation."""
    notes: list[str] = []
    o_times, o_vals, o_env, o_hz = onset_envelope(y, sr)
    duration = len(y) / sr if len(y) else 0.0

    beats = np.array([])
    downbeats = np.array([])
    method, conf = "none", 0.0
    if external_beats is not None and len(external_beats) >= 8:
        beats = np.asarray(external_beats, dtype=np.float64)
        downbeats = np.asarray(external_downbeats if external_downbeats is not None else [],
                               dtype=np.float64)
        method, conf = "all-in-one-mlx", 0.75
    else:
        try:
            beats, downbeats, method, conf = track_beats(y, sr, device)
        except Exception as exc:
            log.info("beat_this unavailable: %s", exc)
            notes.append(f"Beat This unavailable ({exc}); used librosa")
            try:
                beats, downbeats, method, conf = librosa_fallback(y, sr)
            except Exception as exc2:
                notes.append(f"beat tracking failed: {exc2}")

    if len(beats) < 4:
        return RhythmResult(
            beats=beats, downbeats=downbeats, bpm=0.0, bpm_confidence=0.0,
            meter="4/4", meter_confidence=0.0, offset=0.0, method="none",
            bpm_curve=[], onsets=o_times, onset_strength=o_vals, onset_hz=o_hz,
            metrical_confidence=0.0, is_tangent=True, notes=notes, onset_env=o_env,
        )

    bpm, bpm_conf, bcurve, gstr = estimate_tempo(
        beats, o_times, o_vals, o_hz, duration, onset_env=o_env)
    if method == "all-in-one-mlx" and external_bpm and external_bpm > 0:
        # Use the model's metrical level rather than re-deriving a different
        # half/double-time interpretation from the same timestamps.
        bpm = float(external_bpm)
        bcurve = tempo_curve(beats, bpm)
        bpm_conf = min(bpm_conf, conf)  # the port does not publish confidence
    meter, meter_conf, downbeats = estimate_meter(downbeats, beats, bpm)
    offset, metrical_conf, _idx = detect_metrical_position(beats, downbeats, meter)

    ioi = np.diff(beats)
    ioi = ioi[np.isfinite(ioi) & (ioi > 0.2)]
    iqr_ratio = (float(np.percentile(ioi, 75) / np.percentile(ioi, 25))
                 if len(ioi) > 4 else 1.0)
    # Only flag "tangential" when the tracker itself is inconsistent.  A low
    # confidence on its own is not evidence of free time: dense mixes simply
    # put less onset energy on the beat grid.
    is_tangent = iqr_ratio > 1.45
    if is_tangent:
        notes.append(
            f"irregular pulse (IOI IQR ratio {iqr_ratio:.2f}) \u2014 bar numbering is "
            f"approximate; bar-referenced claims are unreliable"
        )

    mid_ratio = metrical_level(beats, o_times)
    gstr = grid_strength(o_times, o_vals, bpm, beat_times=beats)
    level_note = None
    if mid_ratio is not None and mid_ratio > 0.72 and bpm * 2.0 <= TEMPO_RANGE[1]:
        level_note = (f"detected onsets land between the tracked beats almost as often "
                      f"as on them ({mid_ratio:.2f} of the beat rate), so this reads as "
                      f"double-time: the {bpm:.0f} BPM grid may be the {bpm * 2:.0f} BPM "
                      f"pulse")
        notes.append(level_note)
        bpm_conf = float(bpm_conf * 0.92)

    return RhythmResult(
        beats=beats, downbeats=downbeats, bpm=float(bpm),
        bpm_confidence=float(bpm_conf if not is_tangent else bpm_conf * 0.6),
        meter=meter, meter_confidence=float(meter_conf), offset=float(offset),
        method=method, bpm_curve=bcurve, onsets=o_times, onset_strength=o_vals,
        onset_hz=o_hz, metrical_confidence=float(metrical_conf), is_tangent=is_tangent,
        notes=notes, onset_env=o_env, grid_strength=gstr, metrical_level_note=level_note,
        boundary_error=add_boundary_error(beats, downbeats),
    )


def add_boundary_error(beats: np.ndarray, downbeats: np.ndarray) -> float:
    """Fraction of beats that coincide with a downbeat \u2014 metrical consistency."""
    if len(beats) < 4 or len(downbeats) < 2:
        return 0.0
    d = np.abs(beats[:, None] - downbeats[None, :]).min(axis=1)
    return float(np.mean(d < 0.08))


# ---------------------------------------------------------------------------
# Density measures
# ---------------------------------------------------------------------------


def density_curve(times: np.ndarray, values: np.ndarray, t0: float, t1: float,
                  window: float = 2.0) -> np.ndarray:
    """Smoothed events-per-second over a sliding window."""
    step = 0.05
    n = max(1, int((t1 - t0) / step))
    if len(times) == 0:
        return np.zeros(n)
    edges = np.linspace(t0, t1, n + 1)
    counts, _ = np.histogram(times, bins=edges)
    k = max(1, int(window / step))
    kern = np.hanning(k + 2)[1:-1]
    kern /= kern.sum()
    padded = np.pad(counts.astype(np.float64), (k // 2, k // 2), mode="edge")
    # The normalised kernel returns average onsets *per bin*. Convert by the
    # actual bin duration; dividing by the smoothing window understated the
    # advertised onsets/s by roughly 40x at this 50 ms grid.
    bin_seconds = (t1 - t0) / n
    return np.convolve(padded, kern, mode="valid")[:n] / max(bin_seconds, _EPS)


def bpm_at(bpm_curve: list[tuple[float, float]], t: float) -> float:
    if not bpm_curve:
        return 0.0
    if t <= bpm_curve[0][0]:
        return bpm_curve[0][1]
    for (t0, b), (t1, b1) in zip(bpm_curve, bpm_curve[1:]):
        if t0 <= t <= t1:
            w = (t - t0) / max(t1 - t0, _EPS)
            return b * (1 - w) + b1 * w
    return bpm_curve[-1][1]
