"""Refresh energy only, preserving structure and all unrelated analysis data."""
from __future__ import annotations

from copy import deepcopy
import re
import numpy as np

from arrangement.dsp.energy import (DEFAULT_WEIGHTS, HISTORICAL_WEIGHTS, EnergyComponent, _to_grid,
                                   _znorm, attribute_change, combine_components,
                                   find_energy_changes)

EXPERIMENTS = {
    "Current corrected": HISTORICAL_WEIGHTS,
    "Dynamics-led": {"loudness": .65, "onset_density": .21, "percussive_energy": .14},
    "Balanced": DEFAULT_WEIGHTS,
    "Arrangement-led": {"loudness": .25, "onset_density": .15, "percussive_energy": .1,
                        "active_layers": .25, "bass_activity": .125, "band_spread": .125},
}


def refresh(analysis: dict, arrays: dict, loudness: np.ndarray, loudness_hz: float):
    updated = deepcopy(analysis)
    output = {k: v.copy() for k, v in arrays.items()}
    model = updated["energy"]
    hz = model["frame_hz"]
    n = len(arrays["energy/perceived"])
    measured = _to_grid(loudness, loudness_hz, hz, n)
    curves = {}
    for name, meta in model["components"].items():
        key = "energy/" + name
        values = arrays.get(key, np.asarray(meta.get("values") or [], dtype=float))
        if name in HISTORICAL_WEIGHTS and len(values) == n:
            curves[name] = values.astype(float)
    for required in ("onset_density", "percussive_energy"):
        if required not in curves:
            raise ValueError(f"Missing cached energy input: {required}")
    curves["loudness"] = _znorm(measured)
    raw = .62 * curves["loudness"] + .20 * curves["onset_density"] + .18 * curves["percussive_energy"]
    variants = {}
    for title, weights in EXPERIMENTS.items():
        comps = [EnergyComponent(name, curve.copy(), weights.get(name, 0), "z",
                                 model["components"].get(name, {}).get("description", ""))
                 for name, curve in curves.items() if weights.get(name, 0) > 0]
        variants[title] = combine_components(hz, analysis["duration"], comps, raw.copy())
    energy = variants["Balanced"]
    model["weights"] = {c.name: c.weight for c in energy.components}
    model["norm_lo"], model["norm_hi"] = energy.norm_lo, energy.norm_hi
    model["model_notes"] = "Balanced energy combines how loud the recording feels, how rhythmically busy it is, and how full the arrangement is. Their shares are 40%, 30% and 30% when all inputs are available. The curve shows changes within this song."
    for name, values in [("raw", energy.raw), ("perceived", energy.perceived)]:
        model[name]["values"] = values.tolist()
        output["energy/" + name] = values.astype(np.float32)
    for c in energy.components:
        if c.name != "loudness":
            continue
        model["components"][c.name]["values"] = c.curve.tolist()
        output["energy/" + c.name] = c.curve.astype(np.float32)
    updated["loudness"]["lra_curve"] = loudness.tolist()
    beats = updated.get("beats", {})
    times = beats.get("beats") or []
    downbeats = [times[i] for i in beats.get("downbeats", []) if 0 <= i < len(times)]
    updated["energy_changes"] = []
    for t, delta, magnitude in find_energy_changes(energy):
        _, pos, neg = attribute_change(energy, t, t + .35)
        updated["energy_changes"].append({"t": t, "end": min(analysis["duration"], t + .4),
            "direction": "rise" if delta >= 0 else "drop", "delta": delta,
            "magnitude": magnitude, "bar": max(0, sum(b <= t for b in downbeats) - 1) if downbeats else None,
            "contributors": pos[:4] + neg[:3], "evidence": [], "explanation": None})
    previous = None
    for section in updated.get("sections", []):
        start, end = section["start"], section["end"]
        for key, perceived in (("energy_raw", False), ("energy_perceived", True)):
            value = energy.window(start, end, perceived)
            section[key] = value if np.isfinite(value) else energy.at((start + end) / 2, perceived)
        lo, hi = max(0, round(start * loudness_hz)), min(len(loudness), round(end * loudness_hz))
        if hi > lo:
            section["loudness_lufs"] = float(np.mean(loudness[lo:hi]))
        deltas = [s for s in section.get("deltas_from_previous", []) if not re.match(r"^energy (rises|falls) ", s)]
        if previous is not None:
            delta = section["energy_perceived"] - previous["energy_perceived"]
            if abs(delta) > .08:
                deltas.append(f"energy {'rises' if delta > 0 else 'falls'} {abs(delta) * 100:.0f} points on this song's scale")
        section["deltas_from_previous"] = deltas
        previous = section
    fingerprint = updated.get("fingerprint", {})
    fingerprint.update(energy_mean=round(float(np.mean(energy.perceived)), 3),
                       energy_p90=round(float(np.percentile(energy.perceived, 90)), 3),
                       energy_dynamic_range=round(float(np.percentile(energy.perceived, 95) - np.percentile(energy.perceived, 10)), 3))
    return updated, output, variants
