"""Energy: measured intensity, and a proxy for felt arrangement energy.

Two curves, deliberately kept separate:

RAW ENERGY
    What the signal is doing: short-term loudness, onset density, transient
    density, band energy. A hand-weighted acoustic-intensity summary.

PERCEIVED ARRANGEMENT ENERGY
    A higher-level estimate of how intense the *arrangement* feels.  This is
    a deterministic, hand-weighted proxy, not a calibrated perceptual model.
    It combines song-relative, z-scored measurements and adds the things a
    producer would count when asking "does this feel bigger?": how many layers
    are playing, whether the low end and the top end are both active, how much
    rhythmic activity there is, how wide the image is.

The weights are documented and exposed so the UI can show *why* a point on the
curve is where it is, and the reasoning layer can only ever describe the
contribution of a named component, never invent one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from arrangement.dsp.features import normalize_minmax, smooth

_EPS = 1e-10


@dataclass
class EnergyComponent:
    name: str
    curve: np.ndarray
    weight: float
    unit: str = ""
    description: str = ""
    polarity: int = 1  # +1: more = more energy; -1: more = less (e.g. width can cut)


@dataclass
class EnergyResult:
    frame_hz: float
    duration: float
    raw: np.ndarray
    perceived: np.ndarray
    components: list[EnergyComponent] = field(default_factory=list)
    raw_parts: dict[str, np.ndarray] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    norm_lo: float = 0.0
    norm_hi: float = 1.0

    def at(self, t: float, perceived: bool = True) -> float:
        arr = self.perceived if perceived else self.raw
        if not len(arr):
            return float("nan")
        i = int(round(t * self.frame_hz))
        i = max(0, min(len(arr) - 1, i))
        return float(arr[i])

    def window(self, t0: float, t1: float, perceived: bool = True) -> float:
        arr = self.perceived if perceived else self.raw
        i0 = max(0, int(round(t0 * self.frame_hz)))
        i1 = min(len(arr), int(round(t1 * self.frame_hz)))
        if i1 <= i0:
            return float("nan")
        return float(np.mean(arr[i0:i1]))


#: Retained for the listening comparison and reproducible historical curves.
HISTORICAL_WEIGHTS: dict[str, float] = {
    "loudness": 0.27225130890052357,
    "active_layers": 0.17801047120418848,
    "onset_density": 0.11518324607329843,
    "bass_activity": 0.10471204188481675,
    "high_activity": 0.09424083769633507,
    "percussive_energy": 0.07329842931937174,
    "hp_ratio": 0.03664921465968587,
    "band_spread": 0.06282722513089005,
    "stereo_width": 0.06282722513089005,
}

#: Balanced arrangement intensity: 40% loudness, 30% rhythm, 30% fullness.
#: Chosen by listening comparison, not fitted perceptual coefficients.
DEFAULT_WEIGHTS: dict[str, float] = {
    "loudness": .40,
    "onset_density": .18,
    "percussive_energy": .12,
    "active_layers": .15,
    "bass_activity": .075,
    "band_spread": .075,
}


def _znorm(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    if len(x) == 0:
        return x
    mu, sd = float(np.mean(x)), float(np.std(x))
    if sd < 1e-9:
        return np.zeros_like(x)
    z = (x - mu) / sd
    return np.clip(z, -2.5, 3.0)


def _to_grid(x: np.ndarray, src_hz: float, dst_hz: float, n: int) -> np.ndarray:
    if len(x) == 0:
        return np.zeros(n)
    t_src = np.arange(len(x)) / src_hz
    t_dst = np.arange(n) / dst_hz
    return np.interp(t_dst, t_src, x)


def build_energy(
    frame_hz: float,
    duration: float,
    loudness_curve: np.ndarray,
    loudness_hz: float,
    onset_density: np.ndarray,
    flux: np.ndarray,
    flux_hz: float,
    band_db: np.ndarray,
    band_names: list[str],
    hp_ratio: np.ndarray,
    band_hz: float,
    onset_density_hz: float = 20.0,
    active_layers: np.ndarray | None = None,
    layers_hz: float = 20.0,
    width: np.ndarray | None = None,
    width_hz: float = 20.0,
    weights: dict[str, float] | None = None,
) -> EnergyResult:
    """Assemble the two energy curves from measured components."""
    n = max(2, int(round(duration * frame_hz)) + 1)
    W = dict(DEFAULT_WEIGHTS)
    if weights:
        W.update(weights)

    loud = _to_grid(loudness_curve, loudness_hz, frame_hz, n)
    onsets = _to_grid(onset_density, onset_density_hz, frame_hz, n) if len(onset_density) else np.zeros(n)
    fl = _to_grid(flux, flux_hz, frame_hz, n)
    bands = np.stack([_to_grid(band_db[:, i], band_hz, frame_hz, n)
                      for i in range(band_db.shape[1])], axis=1) \
        if band_db.size else np.zeros((n, len(band_names)))
    hp = _to_grid(hp_ratio, band_hz, frame_hz, n)

    # --- raw energy ------------------------------------------------------
    # Short-term loudness is the backbone; the rest modulates it so the raw
    # curve is not just a volume fader in disguise.
    raw = 0.62 * _znorm(loud) + 0.20 * _znorm(onsets) + 0.18 * _znorm(fl)

    # --- components ------------------------------------------------------
    idx = {b: i for i, b in enumerate(band_names)}
    bass = bands[:, idx["bass"]] if "bass" in idx else bands.mean(axis=1)
    high = np.mean([bands[:, idx[b]] for b in ("presence", "brilliance", "air")
                    if b in idx], axis=0) if any(b in idx for b in
                    ("presence", "brilliance", "air")) else bands.mean(axis=1)
    # occupied spectrum: how many bands are meaningfully above this song's floor
    floor = np.percentile(bands, 20, axis=0) if bands.size else 0
    occupied = ((bands - floor[None, :] > 9.0).sum(axis=1) / max(1, bands.shape[1]))

    comps: list[EnergyComponent] = [
        EnergyComponent("loudness", _znorm(loud), W["loudness"], "z",
                        "song-relative short-term loudness (measured in LUFS)"),
        EnergyComponent("onset_density", _znorm(onsets), W["onset_density"], "z",
                        "song-relative rhythmic activity (onset density)"),
        EnergyComponent("percussive_energy", _znorm(fl), W["percussive_energy"],
                        "z", "song-relative spectral flux: transients and attacks"),
        EnergyComponent("bass_activity", _znorm(bass), W["bass_activity"], "z",
                        "song-relative energy in the 60-160 Hz band"),
        EnergyComponent("high_activity", _znorm(high), W.get("high_activity", 0.0), "z",
                        "song-relative energy above 4 kHz"),
        EnergyComponent("band_spread", _znorm(occupied), W["band_spread"], "z",
                        "song-relative fraction of the spectrum in active use"),
        EnergyComponent("hp_ratio", _znorm(hp), W.get("hp_ratio", 0.0), "z",
                        "song-relative percussive share of energy"),
    ]
    if active_layers is not None and len(active_layers):
        comps.append(EnergyComponent(
            "active_layers", _znorm(_to_grid(active_layers, layers_hz, frame_hz, n)),
            W["active_layers"], "z", "song-relative count of active separated-part categories"))
    if width is not None and len(width):
        comps.append(EnergyComponent(
            "stereo_width", _znorm(_to_grid(width, width_hz, frame_hz, n)),
            W.get("stereo_width", 0.0), "z", "song-relative size of the stereo image"))

    return combine_components(frame_hz, duration, [c for c in comps if c.weight != 0], raw,
                              {"loudness": loud, "onsets": onsets, "flux": fl,
                               "bass": bass, "high": high, "occupied": occupied})


def combine_components(frame_hz: float, duration: float,
                       comps: list[EnergyComponent], raw: np.ndarray,
                       raw_parts: dict[str, np.ndarray] | None = None) -> EnergyResult:
    """Apply the common equation to measured or cached standardized inputs.

    The caller owns components; their weights become effective shares.
    Experiment equations use the same smoothing and display normalization.
    """
    total_w = sum(c.weight for c in comps)
    if total_w <= 0 or any(not np.isfinite(c.weight) or c.weight < 0 for c in comps):
        raise ValueError("Energy weights must be finite, nonnegative, and have a positive sum.")
    perceived = np.zeros_like(raw, dtype=np.float64)
    for c in comps:
        if len(c.curve) != len(raw):
            raise ValueError("Energy components must share the same time grid.")
        c.weight /= total_w
        perceived += c.curve * c.weight
    w = max(1, int(round(0.35 * frame_hz)))
    raw = smooth(raw, w)
    perceived = smooth(perceived, w)
    rn, lo, hi = normalize_minmax(perceived, 2.0, 98.0)
    rawn, _, _ = normalize_minmax(raw, 2.0, 98.0)
    return EnergyResult(frame_hz=frame_hz, duration=duration, raw=rawn, perceived=rn,
                        components=comps, norm_lo=lo, norm_hi=hi,
                        raw_parts=raw_parts or {})


def attribute_change(
    energy: EnergyResult, t0: float, t1: float, before: float = 1.2,
    after: float = 1.2,
) -> tuple[float, list[tuple[str, float]], list[tuple[str, float]]]:
    """Explain a change in perceived energy by component.

    Compares the component curves in a window before ``t0`` against a window
    after ``t1`` and returns ``(delta, positive contributors, negative
    contributors)``. Component changes are weighted and scaled into the model's
    normalised units before ranking. They are approximate contributions because
    the final curve is smoothed and clipped at its percentile limits.
    """
    hz = energy.frame_hz
    i_b0 = max(0, int(round((t0 - before) * hz)))
    i_b1 = min(len(energy.perceived), int(round(t0 * hz)))
    i_a0 = max(0, int(round(t1 * hz)))
    i_a1 = min(len(energy.perceived), int(round((t1 + after) * hz)))
    if i_b1 <= i_b0 or i_a1 <= i_a0:
        return 0.0, [], []

    delta = float(np.mean(energy.perceived[i_a0:i_a1]) -
                  np.mean(energy.perceived[i_b0:i_b1]))
    total_weight = sum(c.weight for c in energy.components)
    scale = max(1e-9, total_weight * (energy.norm_hi - energy.norm_lo))
    contribs: list[tuple[str, float]] = []
    for c in energy.components:
        if len(c.curve) < i_a1:
            continue
        b = float(np.mean(c.curve[i_b0:i_b1]))
        a = float(np.mean(c.curve[i_a0:i_a1]))
        contribs.append((c.name, (a - b) * c.weight / scale))
    pos = sorted([c for c in contribs if c[1] > 0.01], key=lambda x: -x[1])
    neg = sorted([c for c in contribs if c[1] < -0.01], key=lambda x: x[1])
    return delta, pos[:6], neg[:6]


def find_energy_changes(
    energy: EnergyResult,
    window: float = 2.0,
    min_delta: float = 0.045,
    min_gap: float = 4.0,
    max_events: int = 80,
) -> list[tuple[float, float, float]]:
    """Locate the biggest steps in the perceived energy curve.

    Returns ``(t, delta, magnitude)``.  ``min_delta`` is in *normalised energy
    units per second of the smoothed derivative*, so the default catches steps
    of roughly 0.09 across the averaging window — big enough to be a real
    arrangement move rather than texture.

    Boundaries are placed where the curve crosses its local mean rather than
    where the derivative is steepest, so the reported time is when the change
    *lands*, which is what a producer would quote.
    """
    hz = energy.frame_hz
    w = max(1, int(round(window * hz)))
    curve = np.asarray(energy.perceived, dtype=np.float64)
    if len(curve) < 2 * w + 4:
        return []
    kern = np.hanning(2 * w + 1)[1:-1]
    kern = kern / kern.sum()
    ma = np.convolve(np.pad(curve, (w, w), mode="edge"), kern, mode="valid")[: len(curve)]
    d = np.gradient(ma) * hz  # units per second

    order = np.argsort(-np.abs(d))
    min_gap_f = max(1, int(min_gap * hz))
    out: list[tuple[float, float, float]] = []
    chosen: list[int] = []
    for i in order:
        if abs(d[i]) < min_delta:
            break
        i = int(i)
        if any(abs(i - j) < min_gap_f for j in chosen):
            continue
        chosen.append(i)
        # place the event at the local extremum the step moved toward
        j = i
        step = 1
        while 0 < j - i < w and ma[j + step] <= ma[j]:
            j += step
        while 0 < i - j < w and ma[j - step] <= ma[j]:
            j -= step
        j = max(0, min(len(curve) - 1, j))
        a0, a1 = max(0, j - w), j
        b0, b1 = j, min(len(curve), j + w)
        if a1 <= a0 or b1 <= b0:
            continue
        delta = float(np.mean(ma[b0:b1]) - np.mean(ma[a0:a1]))
        out.append((float(j / hz), delta, float(abs(delta))))
        if len(out) >= max_events:
            break
    return sorted(out, key=lambda e: e[0])


def energy_percentile_table(energy: EnergyResult, n: int = 12) -> list[dict]:
    """A coarse bar-by-bar energy ladder, for the 'every N bars' pattern view."""
    if not len(energy.perceived):
        return []
    per_bar = energy.frame_hz * (60.0 / 120.0) * 4.0  # 4 beats at 120 BPM
    rows = []
    total_bars = int(len(energy.perceived) / max(per_bar, 1e-6))
    step = max(1, total_bars // n)
    for b in range(0, total_bars, step):
        i0 = int(b * per_bar)
        i1 = min(len(energy.perceived), int((b + 1) * per_bar))
        if i1 <= i0:
            continue
        rows.append({"bar": b, "t": float(i0 / energy.frame_hz),
                     "energy": float(np.mean(energy.perceived[i0:i1])),
                     "raw": float(np.mean(energy.raw[i0:i1]))})
    return rows
