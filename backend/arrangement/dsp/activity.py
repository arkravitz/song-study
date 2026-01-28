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


