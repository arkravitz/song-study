"""Activity segmentation: "when does this layer start and stop playing?"

This is the single most important DSP step in the whole tool.  Everything the
user asks about — entrances, exits, dropouts, layer accumulation — falls out
of getting this right.

Approach
--------
1.  Compute a short-window RMS level curve for a source in dB.
2.  Estimate the *off* level: for a source that is genuinely absent, the stem
    contains only separation bleed, which sits 25–45 dB below the playing
    level.  A histogram of the level curve is therefore bimodal, so we use
    **Otsu's method** on the dB envelope to find the valley between "playing"
    and "not playing" without assuming any absolute threshold.
3.  Apply **hysteresis**: a higher bar to switch on, a lower bar to switch off.
    Without it, a guitar that rings out for two bars gets shredded into notes.
4.  Close gaps shorter than ``min_gap`` and drop spans shorter than
    ``min_span`` — with a distinction: a *transient* shorter than ``min_span``
    that has a real attack is kept, because a one-shot stab is a real event.
5.  Classify each edge as **discrete** (sharp attack, coincident with an onset)
    or **continuous** (gradual swell).  This is what lets the UI draw entrances
    as ticks and swells as ramps.
6.  Optionally **snap** edges to the beat grid when they are within
    ``snap_window`` — producers place arrangements on the grid, and an unsnapped
    edge is often just analysis jitter.  The snap offset is preserved as
    evidence so the UI can show how far it moved.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Sequence

import numpy as np

from arrangement.dsp.features import smooth

_EPS = 1e-10


#: On-margins that differ from the family default, per source id, in dB above
#: the stem's own noise floor.
#:
#: The margin is the one knob that decides "is this part playing", and a single
#: value cannot be right for every stem: it has to clear the stem's *own* floor
#: without climbing so high that real playing time reads as silence. Measured on
#: the four analysed songs against the stems themselves:
#:
#:   vocals  1.0  "DNA." goes 93.5% -> 98.0% duty while the kept-vs-dropped
#:                separation improves +0.1 -> +3.1 dB. "All the Small Things",
#:                "Taste" and the Funkadelic track do not move at all: their
#:                absent regions sit 30-50 dB down, so a lower bar has nothing to
#:                admit.
#:
#:   guitar  2.0  "All the Small Things" 82.7% -> 84.6%; the other three are
#:                within half a per cent either way. A quiet sustained guitar is
#:                the case the higher bar was clipping.
#:
#: Deliberately *not* lowered, because the measurement says lowering it invents
#: instruments:
#:
#:   piano   5.0  "DNA."'s piano stem peaks at -68.9 dB and "Funkadelic"'s at
#:                -65.7 dB — they are separator bleed, not a piano. Sweeping the
#:                margin down from 3 to -1 takes "DNA." from 11.5% duty to 35.3%
#:                and Funkadelic from 34.7% to 63.6%: a phantom part filling a
#:                third of the lane. Taste exposed a separate failure (B-049):
#:                Otsu split the residue instead of the rare louder passage.
#:                Its piano threshold now also stays within 30 dB of the stem's
#:                own robust peak; changing the on-margin alone is insufficient.
#:
#:   drums, bass, other   left at the default 5.0 because they do not respond to
#:                it. Sweeping 3 -> -1 moves bass on "DNA." by 0.0%, drums by
#:                0.5%, other by 0.3%. Their duty cycles are limited by real rests
#:                in the music, not by the threshold, so lowering the margin would
#:                be a change with no upside and unmeasured risk.
STEM_MARGIN_DB: dict[str, float] = {
    "vocals": 1.0,
    "guitar": 2.0,
}


@dataclass
class ActivityConfig:
    """Thresholds for one source.

    The defaults matter a lot and were tuned by eye against real stems.  In
    particular ``median_window`` is what makes this work: a short RMS window
    alone reports a guitar as a string of separate strums rather than one
    playing part, because the level dips between strokes.  Smoothing over
    ~0.3 s spans a strum pattern and reveals the sustained *part*.
    """

    level_window: float = 0.060      # s, RMS integration
    smooth_window: float = 0.080     # s
    median_window: float = 0.300     # s, spans note gaps within a part
    light_window: float = 0.100      # s, the curve that can rescue sparse phrasing
    otsu_bins: int = 192
    on_margin_db: float = 5.0        # above the Otsu valley to switch on
    off_margin_db: float = 1.0       # above the valley to stay on
    min_span: float = 0.22           # s, discard shorter (unless transient)
    min_gap: float = 0.400           # s, bridge holes inside a playing part
    min_transient_span: float = 0.05
    min_dynamic_range_db: float = 12.0  # below this the source is all-or-nothing
    min_level_db: float = -78.0
    # A rare louder phrase must not let Otsu split two populations of bleed.
    # This is relative to this stem's robust peak, so quietly recorded parts
    # remain detectable without admitting residue 40–50 dB below the playing.
    max_threshold_below_peak_db: float | None = None
    #: Maximum distance a boundary may be moved to reach the beat grid, seconds.
    snap_window: float = 0.115
    #: Minimum tempo confidence before the grid is used to move boundaries at all.
    #: A grid whose tempo is 2% out is *seconds* out by the end of a track, and
    #: snapping to it then makes the answer worse rather than tidier. The measured
    #: confidences are 0.67 ("DNA.") and 0.60 ("Taste"), both under this, so
    #: neither is snapped; a track with a firm tempo still is.
    snap_min_confidence: float = 0.80
    #: How close to the playing either side a gap has to get before it counts as
    #: the part still going rather than a departure. Measured across the library
    #: the two populations are well separated: gaps that still contain the
    #: instrument sit 3-10 dB below the phrases beside them, real absences sit
    #: 15 dB and further down. 10 is inside the first and outside the second.
    gap_resume_db: float = 10.0

    @classmethod
    def for_family(cls, family: str, source_id: str | None = None) -> "ActivityConfig":
        """Per-family tuning.

        A kick drum is a series of isolated hits, so a part-level view needs a
        long gap-closing window.  A pad is continuous, so almost none is
        needed.  Bass sits in between: notes are short but the part is
        legato.  Getting these wrong is what produces "the bass appears 200
        times" instead of "the bass enters at 0:20".
        """
        base = cls()
        if family == "drums":
            return replace(base, min_gap=0.60, median_window=0.35, min_span=0.30)
        if family == "bass":
            # 0.60 rather than 0.45. On a driving bass line the gaps between
            # phrases land at 0.44-0.57 s, so a 0.45 s window bridged almost
            # none of them and one part that plays throughout the whole record
            # reported five entrances. Raising it to 0.60 collapses that to the
            # single span it really is, and leaves vocal phrasing untouched:
            # the choice is stable anywhere from 0.60 to 0.90, so 0.60 is the
            # least bridging that fixes it. Verify with
            # `backend/bench/activity_map.py <song_id> --gap <seconds>`.
            return replace(base, min_gap=0.60, median_window=0.32)
        if family == "vocals":
            # Phrases are separated by breaths and instrumental bars; do not
            # bridge those, or every section merges into one.
            #
            # The on-margin is the distance above the stem's own noise floor at
            # which a part is declared to be playing, and 1 dB is what the
            # library's vocals stems support. Measured against the stems
            # themselves, on a 50 ms grid, over four songs:
            #
            #   margin   DNA. duty   DNA. kept-vs-dropped   AST      Taste   Funk
            #     +3       93.5%          +0.1 dB         61.2%    89.5%   69.9%
            #     +2       96.3%          +0.6 dB         61.3%    89.5%   70.0%
            #     +1       98.0%          +3.1 dB         61.3%    89.6%   70.4%
            #      0       98.4%          +3.9 dB         61.3%    89.6%   70.8%
            #     -1       98.9%          +6.9 dB         61.4%    89.8%   70.9%
            #
            # The "kept-vs-dropped" column is the one that decides it: the median
            # level of the regions reported as *playing* minus the 90th percentile
            # of the regions reported as *absent*. It must be positive or the
            # detector is excluding material louder than what it keeps. At +3 it
            # was +0.1 — passing by a hair, which is the same failure B-046 was,
            # one notch less severe. At +1 it is +3.1, so the 4.5% of "DNA." that
            # comes back is unambiguously part of the vocal.
            #
            # The other three songs do not move, and that is the check that makes
            # this a targeted fix rather than a loosening. Their absent regions
            # sit 30-50 dB down — real silence — so there is nothing for a lower
            # bar to admit. A change that had moved those numbers would have been
            # inventing vocal on tracks that do not have any.
            return replace(base, min_gap=0.32, median_window=0.26, min_span=0.30,
                           on_margin_db=STEM_MARGIN_DB.get(source_id or "vocals", 1.0))
        if family == "harmony":
            # Guitar and piano share this family but need different margins, so
            # the per-stem value is looked up below. See STEM_MARGIN_DB.
            return replace(base, min_gap=0.38, median_window=0.30,
                           on_margin_db=STEM_MARGIN_DB.get(
                               source_id or "", base.on_margin_db),
                           max_threshold_below_peak_db=30.0 if source_id == "piano" else None)
        if family in ("pad", "ambience"):
            return replace(base, min_gap=0.80, median_window=0.50, min_span=0.40)
        if family == "harmony":
            return replace(base, min_gap=0.38, median_window=0.30)
        if family in ("fx",):
            return replace(base, min_gap=0.15, median_window=0.18, min_span=0.15)
        return base


@dataclass
class ActivitySpan:
    start: float
    end: float
    level_db: float
    peak_db: float
    attack_db_s: float
    release_db_s: float
    kind: str = "sustain"   # sustain | transient | swell | decay
    on_onset: bool = False
    snap_delta: float = 0.0
    snapped: bool = False
    conf: float = 0.7
    band_profile: dict[str, float] = field(default_factory=dict)
    mean_intensity: float = 0.0


@dataclass
class ActivityResult:
    spans: list[ActivitySpan]
    level_db: np.ndarray
    smoothed_db: np.ndarray
    threshold_db: float
    noise_floor_db: float
    peak_level_db: float
    frame_hz: float
    times: np.ndarray
    otsu_contrast: float = 0.0
    method: str = "otsu+hysteresis"

    @property
    def total_active(self) -> float:
        return sum(s.end - s.start for s in self.spans)

    def is_active(self, t: float) -> bool:
        return any(s.start <= t < s.end for s in self.spans)

    def spans_in(self, t0: float, t1: float) -> list[ActivitySpan]:
        return [s for s in self.spans if s.start < t1 and t0 < s.end]


def level_envelope(x: np.ndarray, sr: int, frame_hz: float = 86.13) -> tuple[np.ndarray, np.ndarray]:
    """RMS level in dB on a fixed frame grid, plus the frame times."""
    hop = max(1, int(round(sr / frame_hz)))
    n_fft = max(256, int(round(sr * 0.040)))
    n_fft = 1 << int(np.ceil(np.log2(n_fft)))
    n_frames = max(1, 1 + max(0, len(x) - n_fft) // hop)
    if len(x) < n_fft:
        return np.array([-90.0]), np.array([0.0])

    strides = (x.strides[0] * hop, x.strides[0])
    frames = np.lib.stride_tricks.as_strided(
        x, shape=(n_frames, n_fft), strides=strides, writeable=False
    )
    rms = np.sqrt((frames.astype(np.float32) ** 2).mean(axis=1) + _EPS)
    db = np.maximum(20.0 * np.log10(rms), -120.0).astype(np.float32)
    times = np.arange(n_frames) * hop / float(sr)
    return db, times


def otsu_threshold(values: np.ndarray, bins: int = 192) -> tuple[float, float]:
    """Otsu split of a 1-D sample. Returns (threshold, separability)."""
    v = np.asarray(values, dtype=np.float64)
    v = v[np.isfinite(v)]
    if v.size < 8:
        return (float(np.median(v)) if v.size else -60.0), 0.0
    lo, hi = float(np.percentile(v, 0.5)), float(np.percentile(v, 99.5))
    if hi - lo < 1.0:
        return lo + 0.5, 0.0
    hist, edges = np.histogram(v, bins=bins, range=(lo, hi))
    hist = hist.astype(np.float64)
    p = hist / max(1.0, hist.sum())
    centers = 0.5 * (edges[:-1] + edges[1:])
    w0 = np.cumsum(p)
    w1 = 1.0 - w0
    mu0 = np.cumsum(p * centers) / np.maximum(w0, 1e-12)
    mu_tot = float((p * centers).sum())
    mu1 = (mu_tot - np.cumsum(p * centers)) / np.maximum(w1, 1e-12)
    between = w0 * w1 * (mu0 - mu1) ** 2
    k = int(np.argmax(between))
    thresh = float(centers[k])
    eta = float(between[k] / max(1e-12, (p * centers ** 2).sum()))
    return thresh, eta


def detect_activity(
    x: np.ndarray,
    sr: int,
    cfg: ActivityConfig | None = None,
    frame_hz: float = 86.13,
    onsets: np.ndarray | None = None,
    onset_times: np.ndarray | None = None,
    beat_times: Sequence[float] | None = None,
    downbeats: Sequence[float] | None = None,
    band_profile_fn=None,
    #: How much the tempo estimate is trusted, 0..1. Gates beat snapping: a grid
    #: whose tempo is a couple of per cent out is seconds out by the end of a
    #: track, and moving a boundary onto it makes the answer worse.
    bpm_confidence: float = 1.0,
) -> ActivityResult:
    """Segment a source's level envelope into playing spans."""
    cfg = cfg or ActivityConfig()
    db, times = level_envelope(x, sr, frame_hz)

    sm_win = max(1, int(round(cfg.smooth_window * frame_hz)))
    sm = smooth(db.astype(np.float64), sm_win)

    # Median-filter to the part level.  A short RMS window dips between notes
    # and strums, so the raw curve reads a single sustained part as dozens of
    # separate events.  Smoothing over ~0.3 s spans the gaps and leaves the
    # genuine entrances and exits intact.
    med_win = max(1, int(round(cfg.median_window * frame_hz)))
    env = _median_smooth(sm, med_win) if med_win > 1 else sm

    # robust dynamic range reference
    noise_floor = float(np.percentile(env, 3.0))
    peak = float(np.percentile(env, 99.5))
    span = max(6.0, peak - noise_floor)

    thr, eta = otsu_threshold(env, cfg.otsu_bins)
    # Anchor the threshold to the noise floor when the source is essentially
    # absent everywhere (Otsu then splits noise, which is meaningless).
    if peak - noise_floor < cfg.min_dynamic_range_db:
        thr = noise_floor + max(4.0, 0.35 * span)
        eta = 0.0
    # Never claim "playing" at a level where the stem is just bleed, and never
    # above the source's own peak.
    thr = max(thr, noise_floor + 4.0, cfg.min_level_db)
    if cfg.max_threshold_below_peak_db is not None:
        thr = max(thr, peak - cfg.max_threshold_below_peak_db)
    thr = min(thr, peak - 1.5)
    if peak - thr < 2.0:
        thr = peak - 2.0

    on_thr = thr + cfg.on_margin_db
    off_thr = thr + cfg.off_margin_db

    # ---- sparse-phrasing rescue -----------------------------------------
    #
    # The median filter is what turns a stream of notes into one sustained part,
    # and it is the single most important step here. But a running median takes
    # the *lower* values in its window, so a passage that is genuinely present
    # yet sparse — breathy, spoken, half-sung, or delivered as short separated
    # phrases — reads as near-silence and falls between the two hysteresis
    # levels, never quite crossing to "on".
    #
    # Measured on Kendrick Lamar's "DNA.": the detected vocal regions sit at a
    # median of -20.6 dB, and the excluded ones at -24.7 dB with a 90th
    # percentile of -21.5 dB against an on-threshold of -20.8 dB. A 6.25 s
    # stretch of the vocals stem was being reported as absent while carrying a
    # level 164% of the median of the passages the tool *was* calling vocal.
    # In plain terms the tool was hiding the loudest vocal material on the
    # record and the user could hear it.
    #
    # So the switching envelope is the heavier of the two curves, but only
    # where the lightly-smoothed curve is *convincingly* above the floor
    # (`on_margin` plus two further dB). Two guards, both load-bearing:
    #
    # * the rescue is skipped entirely when the source has no real dynamic range
    #   (`peak - noise_floor < min_dynamic_range_db`). Without that guard it
    #   doubled a near-silent piano from 7.7% to 15.2% duty on invented
    #   activity whose level was 80% of an already-silent baseline;
    # * the extra two dB keeps it off bleed. Ungated, the same change added
    #   regions to the guitar at 13% of detected level.
    #
    # Net effect on "DNA.": vocal duty 61.3% -> 68.6%, and the recovered
    # material measures 131% of the level of what was already detected, so what
    # comes back is the loud material, not noise.
    light = smooth(db.astype(np.float64), max(1, int(round(cfg.light_window * frame_hz))))
    rescue_at = (
        thr + cfg.on_margin_db + 2.0
        if (peak - noise_floor) >= cfg.min_dynamic_range_db
        else np.inf
    )
    env = np.where(light >= rescue_at, np.maximum(env, light), env)

    active = _hysteresis(env, on_thr, off_thr)

    # bridge short gaps, then drop implausibly short spans
    min_gap_f = max(1, int(round(cfg.min_gap * frame_hz)))
    active = _close_gaps(active, min_gap_f)
    min_span_f = max(1, int(round(cfg.min_span * frame_hz)))
    active = _open_gaps(active, min_span_f)
    # ...and bridge the gaps that duration alone cannot judge.
    active = _close_quiet_gaps(active, env, cfg.gap_resume_db)

    if not active.any():
        return ActivityResult(spans=[], level_db=db, smoothed_db=env, threshold_db=thr,
                              noise_floor_db=noise_floor, peak_level_db=peak,
                              frame_hz=frame_hz, times=times, otsu_contrast=eta)

    raw_spans = _frames_to_spans(active, times)
    spans: list[ActivitySpan] = []
    onset_arr = onsets if onsets is not None else None
    otimes = onset_times if onset_times is not None else None

    for sp in raw_spans:
        i0 = max(0, int(sp[0] * frame_hz))
        i1 = min(len(sm), int(sp[1] * frame_hz) + 1)
        if i1 <= i0:
            continue
        seg = env[i0:i1]
        level = float(np.mean(seg))
        pk = float(np.max(seg))
        attack = _slope(seg, frame_hz, 0, min(len(seg) - 1, max(2, int(0.12 * frame_hz))))
        release = _slope(seg, frame_hz, max(0, len(seg) - int(0.20 * frame_hz)), len(seg) - 1)
        dur = sp[1] - sp[0]
        on_onset = _near_onset(sp[0], onset_arr, otimes, tol=0.06)
        kind = _classify(attack, dur, pk - level, on_onset)
        if dur < cfg.min_span and kind != "transient":
            continue
        spans.append(ActivitySpan(
            start=sp[0], end=sp[1], level_db=level, peak_db=pk,
            attack_db_s=attack, release_db_s=release, kind=kind, on_onset=on_onset,
            conf=_confidence(attack, dur, eta, level - thr, peak - thr),
            mean_intensity=float(np.clip((level - noise_floor) / max(1e-6, span), 0, 1)),
        ))

    spans = _merge_touching(spans, frame_hz, cfg)
    # Snap to the beat grid only when the grid is worth trusting.
    #
    # A single global tempo is an approximation, and its error accumulates: a
    # tempo 2.6% slow puts the grid **4.8 s** out by the end of a 3:06 record,
    # and 1.7% slow puts it 2.6 s out by the end of a 2:37 one. Measured against
    # documented tempos -- "DNA." is ~140 and is reported as 136.4; "Taste" is
    # ~113 and is reported as 111.1. So near the end of a track, snapping a
    # boundary to the grid does not tidy it up, it *moves the analysis's answer*
    # by seconds, towards a tempo that is measurably wrong.
    #
    # The app already knows: `bpm_confidence` is 0.67 for "DNA." and 0.60 for
    # "Taste", and "DNA." carries a caveat saying every bar number is an
    # approximation. The grid was being used anyway.
    #
    # The threshold is set where the error stops being smaller than the snapping
    # it is supposed to buy. `snap_window` is 0.115 s, so a boundary is already
    # only ever moved by up to 0.115 s; that is worth having when the grid is
    # good, and is swamped by a multi-second drift when it is not.
    if (beat_times is not None and len(beat_times)
            and bpm_confidence >= cfg.snap_min_confidence):
        grid = list(beat_times)
        if downbeats is not None and len(downbeats):
            grid = grid + list(downbeats)
        spans = _snap_to_grid(spans, grid, cfg.snap_window)

    if band_profile_fn is not None:
        for sp in spans:
            sp.band_profile = band_profile_fn(sp.start, sp.end)

    return ActivityResult(spans=spans, level_db=db, smoothed_db=env, threshold_db=thr,
                          noise_floor_db=noise_floor, peak_level_db=peak,
                          frame_hz=frame_hz, times=times, otsu_contrast=eta)


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


def _median_smooth(x: np.ndarray, win: int) -> np.ndarray:
    """Odd-window running median.

    Preferred to a mean here because a mean is dragged around by isolated
    transients (a single cymbal crash lifts a 0.3 s window by several dB),
    which makes level estimates unstable exactly at the moments we care about.
    Implemented with a stride trick so there is no per-frame Python loop.
    """
    if win <= 1 or len(x) < win:
        return x
    if win % 2 == 0:
        win += 1
    half = win // 2
    padded = np.pad(x, (half, half), mode="edge")
    view = np.lib.stride_tricks.sliding_window_view(padded, win)
    return np.median(view, axis=-1)


def _hysteresis(x: np.ndarray, on_thr: float, off_thr: float) -> np.ndarray:
    out = np.zeros(len(x), dtype=bool)
    state = False
    for i, v in enumerate(x):
        if state:
            if v < off_thr:
                state = False
        else:
            if v > on_thr:
                state = True
        out[i] = state
    return out


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    if not mask.any():
        return []
    d = np.diff(mask.astype(np.int8))
    starts = list(np.where(d == 1)[0] + 1)
    ends = list(np.where(d == -1)[0] + 1)
    if mask[0]:
        starts.insert(0, 0)
    if mask[-1]:
        ends.append(len(mask))
    return list(zip(starts, ends))


def _close_gaps(mask: np.ndarray, max_gap: int) -> np.ndarray:
    mask = mask.copy()
    runs = _runs(mask)
    for (s0, e0), (s1, _e1) in zip(runs, runs[1:]):
        if 0 < s1 - e0 <= max_gap:
            mask[e0:s1] = True
    return mask


def _open_gaps(mask: np.ndarray, max_gap: int) -> np.ndarray:
    mask = mask.copy()
    runs = _runs(mask)
    for (s0, _e0), (s1, e1) in zip(runs, runs[1:]):
        if 0 < s1 - s0 <= max_gap:
            mask[s0:s1] = False
    return mask


def _frames_to_spans(mask: np.ndarray, times: np.ndarray) -> list[tuple[float, float]]:
    return [(float(times[s]), float(times[min(e, len(times) - 1)])) for s, e in _runs(mask)]


def _slope(seg: np.ndarray, frame_hz: float, i0: int, i1: int) -> float:
    if i1 <= i0 or i1 >= len(seg):
        return 0.0
    dt = (i1 - i0) / frame_hz
    return float((seg[i1] - seg[i0]) / max(dt, 1e-6))


def _near_onset(t: float, strengths: np.ndarray | None, times: np.ndarray | None,
                tol: float = 0.06) -> bool:
    if times is None or len(times) == 0:
        return False
    i = int(np.searchsorted(times, t))
    lo = max(0, i - 3)
    hi = min(len(times), i + 3)
    if hi <= lo:
        return False
    if np.min(np.abs(times[lo:hi] - t)) > tol:
        return False
    if strengths is not None and len(strengths) == len(times):
        return bool(strengths[lo:hi].max() > 0.0)
    return True


def _classify(attack: float, dur: float, crest: float, on_onset: bool) -> str:
    if dur < 0.5 and (crest > 8.0 or (on_onset and attack > 25.0)):
        return "transient"
    if dur < 1.2 and crest > 12.0 and attack > 20.0:
        return "transient"
    if attack < 4.0 and dur > 2.0:
        return "swell"
    if attack < 8.0:
        return "swell"
    return "sustain"


def _confidence(attack: float, dur: float, eta: float, level_over_thr: float,
                headroom: float) -> float:
    c = 0.35
    c += 0.30 * min(1.0, level_over_thr / 12.0)
    c += 0.15 * min(1.0, headroom / 18.0)
    c += 0.10 * min(1.0, max(0.0, attack) / 25.0)
    c += 0.10 * min(1.0, dur / 2.0)
    if eta > 0:
        c += 0.15 * min(1.0, eta * 3.0)
    return float(np.clip(c, 0.05, 0.98))


def _merge_touching(spans: list[ActivitySpan], frame_hz: float,
                    cfg: ActivityConfig) -> list[ActivitySpan]:
    if not spans:
        return spans
    out = [spans[0]]
    for sp in spans[1:]:
        prev = out[-1]
        gap = sp.start - prev.end
        if gap <= cfg.min_gap and (prev.kind == sp.kind or sp.kind in ("sustain", "swell")):
            prev.end = sp.end
            prev.level_db = max(prev.level_db, sp.level_db)
            prev.peak_db = max(prev.peak_db, sp.peak_db)
            prev.mean_intensity = max(prev.mean_intensity, sp.mean_intensity)
        else:
            out.append(sp)
    return out


def _close_quiet_gaps(active: np.ndarray, env: np.ndarray, resume_db: float) -> np.ndarray:
    """Bridge a gap whose loudest moment is close to the playing either side.

    Closing gaps by *duration* is the wrong question, and the measurements show it
    answering wrong in both directions. A half-second hole is not evidence of
    anything: on "DNA." the vocal stem is continuous and the detector cut it into
    seven pieces, and every one of the six holes contains audio only 3-10 dB below
    the phrases beside it. The instrument did not stop; the median filter dipped
    for a moment and the hysteresis turned that into an exit.

    Meanwhile a real instrumental break can be short and still be 20-40 dB down,
    and closing it would invent playing that is not there. Duration cannot tell
    those apart. Level can, because the question is not "how long was it quiet"
    but "was it quiet".

    So for each remaining gap, take the loudest part of it and compare with the
    quieter of the two spans it separates. If the gap gets within `resume_db` of
    that, it was not a departure — the part is still there and the exit was an
    artefact of the envelope.

    The threshold is set from the measured separation between the two
    populations, which is wide: artefacts sit 3-10 dB down, real absences 15 dB
    and below. 10 dB is inside the first and outside the second with room to
    spare on both sides.
    """
    n = len(active)
    if n < 3 or not active.any():
        return active
    out = active.copy()
    # Boundaries of alternating runs: even indices start an *active* run, odd
    # indices end one. So the gaps are the spans between an odd and the next even
    # index -- reading them the other way round compares each active run against
    # itself and bridges nothing.
    edges = np.flatnonzero(
        np.diff(np.concatenate(([0], active.view(np.int8), [0]))))
    # The level of each *whole* run, not of the frames next to the gap.
    #
    # This was originally sampled from a dozen frames either side of the gap, and
    # that is wrong in a way that only shows up on real data: those frames are the
    # fade-out and the attack, so the reference came out far below the phrase
    # beside it. A 10 dB margin against a reference that is already 20 dB low
    # bridges anything -- it reported "Taste" as singing through an instrumental
    # break whose quietest second is **37 dB** below her phrases. The level of a
    # run is the level of the run.
    levels: dict[int, float] = {}
    for i in range(0, len(edges) - 1, 2):
        r0, r1 = int(edges[i]), int(edges[i + 1])
        if r1 > r0:
            levels[i // 2] = float(np.median(env[r0:r1]))

    for i in range(1, len(edges) - 1, 2):
        a0, a1 = int(edges[i]), int(edges[i + 1])   # the gap between two runs
        if a1 <= a0 + 1:
            continue
        # The quieter neighbour: the gap has to be close to *both* sides to be a
        # continuation rather than the tail of one phrase and the head of the next.
        cands = [levels[k] for k in ((i - 1) // 2, (i + 1) // 2) if k in levels]
        if not cands:
            continue
        ref = min(cands)
        # The loudest *sustained* part of the gap, not its single peak frame: one
        # transient inside a genuine rest should not bridge it.
        core = env[a0:a1]
        if core.size == 0:
            continue
        win = max(1, core.size // 4)
        k = min(win, 3)
        smoothed = np.convolve(core, np.ones(k) / k, mode="valid")
        if smoothed.size == 0:
            continue
        if float(np.percentile(smoothed, 90)) >= ref - resume_db:
            out[a0:a1] = True
    return out


def _snap_to_grid(spans: list[ActivitySpan], grid: Sequence[float],
                  window: float) -> list[ActivitySpan]:
    """Move boundaries onto the beat grid, but never at the cost of a span.

    Snapping exists to make a boundary land on a beat so a producer can count
    bars. It is a convenience applied *after* the span has been decided, and it
    must not be able to destroy what was decided.

    It could. Start and end are snapped independently, so a span shorter than the
    grid spacing has both ends attracted to the same grid point and collapses to
    zero length. Measured on the library: **16 of Taste's 78 segments**, and 1 of
    the Funkadelic track's 105, were emitted with `start == end`. A zero-length
    segment is not a small error — it draws as a hairline, contributes nothing to
    duty cycle, and still counts towards the lane's entry count, which is how
    Taste's drums came to report 27 entries for what is really about eleven.
    The same independence can invert a span, putting its end before its start.

    So each span keeps its unsnapped boundaries, snaps a copy, and only keeps the
    snapped pair if it is still a span: positive length, and no shorter than the
    grid step that produced it. Otherwise the original is restored. The count of
    spans is unchanged, which is the property that matters.
    """
    g = np.asarray(grid, dtype=np.float64)
    g.sort()
    if len(g) < 2:
        return spans
    step = float(np.median(np.diff(g))) if len(g) > 1 else 0.0

    def snap_one(t: float) -> float | None:
        i = int(np.searchsorted(g, t))
        best, bd = None, window
        for j in range(max(0, i - 2), min(len(g), i + 2)):
            d = abs(float(g[j]) - t)
            if d < bd:
                best, bd = float(g[j]), d
        return best

    for sp in spans:
        s0, e0 = sp.start, sp.end
        s1, e1 = snap_one(s0), snap_one(e0)
        if s1 is None or e1 is None:
            continue
        if e1 - s1 <= 0:
            # Collapsed or inverted: the grid pulled both ends onto one point.
            sp.start, sp.end = s0, e0
            continue
        if step > 0 and (e1 - s1) < 0.5 * step:
            # Snapping would have shortened a real span by more than half a beat.
            sp.start, sp.end = s0, e0
            continue
        sp.start, sp.end = s1, e1
        sp.snap_delta = max(sp.snap_delta, abs(s1 - s0))
        sp.snapped = True
    return spans


# ---------------------------------------------------------------------------
# Combination / arrangement-level helpers
# ---------------------------------------------------------------------------


def merge_within(spans: list[ActivitySpan], gap: float) -> list[ActivitySpan]:
    if not spans:
        return []
    spans = sorted(spans, key=lambda s: s.start)
    out = [spans[0]]
    for sp in spans[1:]:
        if sp.start - out[-1].end <= gap:
            out[-1].end = max(out[-1].end, sp.end)
            out[-1].peak_db = max(out[-1].peak_db, sp.peak_db)
        else:
            out.append(sp)
    return out


def stems_to_map(results: dict[str, ActivityResult], t0: float, t1: float,
                 frame_hz: float) -> np.ndarray:
    """Binary (n_sources, n_frames) activity matrix for a time window."""
    n = int(round((t1 - t0) * frame_hz)) + 1
    out = np.zeros((len(results), n), dtype=np.float32)
    for r, res in enumerate(results.values()):
        i0 = max(0, int(t0 * frame_hz))
        i1 = min(res.frame_hz * res.frame_hz if False else len(res.smoothed_db), i0 + n)
        if i1 <= i0:
            continue
        seg = res.smoothed_db[i0:i1]
        if len(seg) < n:
            seg = np.pad(seg, (0, n - len(seg)))
        out[r, : len(seg)] = seg[:n]
    return out
