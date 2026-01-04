"""Arrangement events: the abstraction the whole product is built around.

An event is a *discrete, named change in the arrangement* at a time, with the
measurements that justify it.  Every event here is derived from a measurement —
nothing is asserted that a number does not support.

The event taxonomy distinguishes **discrete** changes (something entered or
left) from **continuous** ones (something got wider, brighter, denser while
staying the same source).  The UI draws them differently for a reason: a tick
means "this happened", a bar means "this was ramping".
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from arrangement.dsp.activity import ActivityResult, ActivitySpan
from arrangement.dsp.features import smooth
from arrangement.dsp.spatial import SpatialResult

_EPS = 1e-10


@dataclass
class EventCandidate:
    t: float
    end: float | None
    kind: str
    label: str
    source_ids: list[str] = field(default_factory=list)
    strength: float = 0.5
    confidence: float = 0.5
    is_continuous: bool = False
    evidence: list[dict] = field(default_factory=list)
    metric: str | None = None
    value_before: float | None = None
    value_after: float | None = None


#: Continuous kinds drawn as bars rather than ticks.
CONTINUOUS_KINDS = {
    "width_change", "filter_change", "density_change", "texture_change",
    "level_change", "rhythm_change", "space_change",
}


def events_from_activity(
    activities: dict[str, ActivityResult],
    source_meta: dict[str, dict],
    bar_of: callable | None = None,
    first_return: dict[str, bool] | None = None,
) -> list[EventCandidate]:
    """Turn per-source playing spans into enter/exit events.

    A source's *first* entry is an ``enter``; a later entry after a gap is a
    ``restart`` (producers care about the difference: a bass that never left is
    not a bass that came back).
    """
    out: list[EventCandidate] = []
    for sid, act in activities.items():
        meta = source_meta.get(sid, {})
        label = meta.get("label", sid)
        seen = 0
        for sp in act.spans:
            dur = sp.end - sp.start
            if dur < 0.15:
                continue
            kind = "enter" if seen == 0 else "restart"
            seen += 1
            # A span that ramps in over a long time is a swell, not an entrance.
            if sp.kind == "swell" and sp.attack_db_s < 6.0 and dur > 3.0:
                kind = "level_change"
                out.append(EventCandidate(
                    t=sp.start, end=sp.end, kind=kind,
                    label=f"{label} swells in",
                    source_ids=[sid],
                    strength=float(np.clip(dur / 8.0, 0.2, 1.0)),
                    confidence=sp.conf * 0.85, is_continuous=True,
                    evidence=[_span_evidence(sid, meta, sp, act, "swell in")],
                    metric="rms_db", value_before=act.noise_floor_db,
                    value_after=sp.level_db,
                ))
                continue
            out.append(EventCandidate(
                t=sp.start, end=None, kind=kind,
                label=f"{label} {'enters' if kind == 'enter' else 'returns'}",
                source_ids=[sid],
                strength=float(np.clip((sp.peak_db - act.threshold_db) / 18.0, 0.15, 1.0)),
                confidence=sp.conf,
                evidence=[_span_evidence(sid, meta, sp, act,
                                         "enters" if kind == "enter" else "returns")],
                metric="rms_db",
                value_before=float(act.noise_floor_db), value_after=float(sp.level_db),
            ))
        # exits: one per span end
        for sp in act.spans[:-1]:
            out.append(EventCandidate(
                t=sp.end, end=None, kind="exit",
                label=f"{label} drops out",
                source_ids=[sid],
                strength=float(np.clip((sp.level_db - act.threshold_db) / 18.0, 0.15, 1.0)),
                confidence=float(sp.conf * 0.9),
                evidence=[_span_evidence(sid, meta, sp, act, "drops out at")],
                metric="rms_db", value_before=float(sp.level_db),
                value_after=float(act.noise_floor_db),
            ))
    out.sort(key=lambda e: e.t)
    return out


def _span_evidence(sid: str, meta: dict, sp: ActivitySpan, act: ActivityResult,
                   verb: str) -> dict:
    model = meta.get("separator_model", "separator")
    return {
        "kind": "dsp",
        "method": f"{model}:activity",
        "metric": "stem_rms_db",
        "t0": round(sp.start, 3),
        "t1": round(sp.end, 3),
        "value": round(float(sp.level_db), 2),
        "unit": "dBFS",
        "strength": "direct",
        "note": (
            f"{meta.get('label', sid)} stem level reaches {sp.level_db:.1f} dBFS "
            f"at {sp.start:.2f}s (threshold {act.threshold_db:.1f} dBFS, "
            f"noise floor {act.noise_floor_db:.1f} dBFS); span classified "
            f"'{sp.kind}' with attack {sp.attack_db_s:+.0f} dB/s"
        ),
    }


def events_from_spatial(spatial: SpatialResult, frame_hz: float = 20.0,
                        min_delta: float = 0.09, min_gap: float = 6.0) -> list[EventCandidate]:
    """Detect stereo width and balance changes from the spatial curves."""
    if not spatial.frames or spatial.is_mono_source:
        return []
    n = len(spatial.frames)
    w = np.array([f.width for f in spatial.frames])
    pan = np.array([f.pan for f in spatial.frames])
    sm = max(1, int(round(2.0 * frame_hz)))
    ws = smooth(w, sm)
    ds = np.gradient(ws) * frame_hz
    out: list[EventCandidate] = []
    min_gap_f = max(1, int(min_gap * frame_hz))
    idx = np.argsort(-np.abs(ds))
    chosen: list[int] = []
    for i in idx:
        if abs(ds[i]) < min_delta / 4.0:
            break
        if any(abs(int(i) - j) < min_gap_f for j in chosen):
            continue
        chosen.append(int(i))
        t = float(i / frame_hz)
        a0, a1 = max(0, i - int(2 * frame_hz)), i
        b0, b1 = i, min(n, i + int(2 * frame_hz))
        before = float(np.mean(ws[a0:a1])) if a1 > a0 else float(ws[i])
        after = float(np.mean(ws[b0:b1])) if b1 > b0 else float(ws[i])
        change = after - before
        widens = change > 0
        band = ("high" if np.mean([f.high_width for f in spatial.frames[max(0, i - 10): i + 10]])
                > np.mean([f.low_width for f in spatial.frames[max(0, i - 10): i + 10]])
                else "broadband")
        out.append(EventCandidate(
            t=t, end=None, kind="width_change",
            label=f"Stereo image {'widens' if widens else 'narrows'}",
            strength=float(np.clip(abs(change) * 3.0, 0.1, 1.0)),
            confidence=float(np.clip(abs(change) * 4.0, 0.2, 0.95)),
            is_continuous=True,
            evidence=[{
                "kind": "dsp", "method": "spatial:side_mid", "metric": "width",
                "t0": round(t - 2.0, 2), "t1": round(t + 2.0, 2),
                "value": round(change, 3), "unit": "side/mid",
                "strength": "direct",
                "note": (f"side-to-mid energy ratio moves {before:.2f} -> {after:.2f} "
                         f"({band}); inter-channel correlation "
                         f"{np.mean([f.correlation for f in spatial.frames[a0:b1]]):.2f}"),
            }],
            metric="stereo_width", value_before=before, value_after=after,
        ))
    return sorted(out, key=lambda e: e.t)


def events_from_density(
    layer_counts: np.ndarray, layer_hz: float, duration: float,
    threshold: float = 0.55, min_gap: float = 8.0,
) -> list[EventCandidate]:
    """Detect step changes in how many layers are playing (arrangement density)."""
    if len(layer_counts) < 8:
        return []
    c = smooth(np.asarray(layer_counts, dtype=np.float64), max(1, int(1.0 * layer_hz)))
    d = np.gradient(c) * layer_hz
    out: list[EventCandidate] = []
    min_gap_f = max(1, int(min_gap * layer_hz))
    idx = np.argsort(-np.abs(d))
    chosen: list[int] = []
    for i in idx:
        if abs(d[i]) < threshold:
            break
        if any(abs(int(i) - j) < min_gap_f for j in chosen):
            continue
        chosen.append(int(i))
        t = float(i / layer_hz)
        a0, a1 = max(0, i - int(3 * layer_hz)), i
        b0, b1 = i, min(len(c), i + int(3 * layer_hz))
        before = float(np.mean(c[a0:a1])) if a1 > a0 else float(c[i])
        after = float(np.mean(c[b0:b1])) if b1 > b0 else float(c[i])
        out.append(EventCandidate(
            t=t, end=None, kind="density_change",
            label=f"Arrangement {'thickens' if after > before else 'thins'}",
            strength=float(np.clip(abs(after - before) / 3.0, 0.1, 1.0)),
            confidence=float(np.clip(abs(after - before) / 2.0, 0.2, 0.95)),
            is_continuous=True,
            evidence=[{
                "kind": "dsp", "method": "arrangement:active_source_count",
                "metric": "active_layers", "t0": round(t - 3, 2), "t1": round(t + 3, 2),
                "value": round(after - before, 2), "unit": "layers",
                "strength": "direct",
                "note": (f"number of separately-tracked sources playing moves "
                         f"{before:.1f} -> {after:.1f}"),
            }],
            metric="active_layers", value_before=before, value_after=after,
        ))
    return sorted(out, key=lambda e: e.t)


def events_from_silence(
    rms_db: np.ndarray, frame_hz: float, floor_db: float = -60.0,
    min_dur: float = 0.25, min_gap: float = 10.0,
) -> list[EventCandidate]:
    """Detect genuine near-silence and the return from it."""
    if len(rms_db) < 8:
        return []
    sm = smooth(np.asarray(rms_db, dtype=np.float64), max(1, int(round(0.05 * frame_hz))))
    quiet = sm < floor_db
    if not quiet.any():
        return []
    # runs of quiet
    d = np.diff(quiet.astype(np.int8))
    starts = list(np.where(d == 1)[0] + 1)
    ends = list(np.where(d == -1)[0] + 1)
    if quiet[0]:
        starts.insert(0, 0)
    if quiet[-1]:
        ends.append(len(quiet))
    out: list[EventCandidate] = []
    last = -1e9
    for a, b in zip(starts, ends):
        t0, t1 = a / frame_hz, b / frame_hz
        if t1 - t0 < min_dur or t0 - last < min_gap:
            continue
        last = t0
        out.append(EventCandidate(
            t=t0, end=t1, kind="silence",
            label=f"Silence ({t1 - t0:.1f}s)",
            strength=float(np.clip((t1 - t0) / 2.0, 0.1, 1.0)),
            confidence=0.8, is_continuous=True,
            evidence=[{
                "kind": "dsp", "method": "dsp:rms", "metric": "rms_db",
                "t0": round(t0, 2), "t1": round(t1, 2),
                "value": round(float(np.min(sm[a:b])), 1), "unit": "dBFS",
                "strength": "direct",
                "note": f"master falls below {floor_db:.0f} dBFS for {t1 - t0:.2f}s",
            }],
            metric="rms_db", value_before=float(sm[max(0, a - 5)]),
            value_after=float(sm[min(len(sm) - 1, b + 5)]),
        ))
    return out


def events_from_transients(
    onset_env: np.ndarray, env_hz: float, rms_db: np.ndarray, frame_hz: float,
    peak_threshold: float = 0.82, min_gap: float = 1.5,
) -> list[EventCandidate]:
    """Detect impacts: a very large flux spike coinciding with a level jump.

    Distinguishing an *impact* from an ordinary entrance matters — an impact is
    a production event (a crash, a sub drop, a drum fill) rather than a layer
    being added, and the two read completely differently in a session.
    """
    if len(onset_env) < 16 or len(rms_db) < 16:
        return []
    env = np.asarray(onset_env, dtype=np.float64)
    hi = float(np.percentile(env, 99.5))
    if hi <= 0:
        return []
    env = env / hi
    from scipy.signal import find_peaks

    rms = smooth(np.asarray(rms_db, dtype=np.float64), max(1, int(round(0.08 * frame_hz))))
    peaks, props = find_peaks(env, height=peak_threshold, prominence=0.25,
                              distance=max(1, int(min_gap * env_hz)))
    out: list[EventCandidate] = []
    for i, p in enumerate(peaks):
        t = float(p / env_hz)
        a = max(0, int((t - 0.25) * frame_hz))
        b = min(len(rms), int((t + 0.5) * frame_hz))
        if a >= b:
            continue
        jump = float(np.max(rms[a:b]) - rms[a])
        if jump < 5.0:
            continue
        out.append(EventCandidate(
            t=t, end=None, kind="impact", label="Impact / hit",
            strength=float(np.clip(props["peak_heights"][i], 0.2, 1.0)),
            confidence=0.6,
            evidence=[{
                "kind": "dsp", "method": "dsp:onset_flux", "metric": "spectral_flux",
                "t0": round(t - 0.05, 3), "t1": round(t + 0.5, 3),
                "value": round(jump, 1), "unit": "dB rise",
                "strength": "direct",
                "note": (f"onset flux in the top {100 * (1 - peak_threshold):.0f}% of the "
                         f"song coincides with a {jump:.1f} dB level jump"),
            }],
            metric="flux", value_before=float(rms[a]), value_after=float(np.max(rms[a:b])),
        ))
    return out


def events_from_rhythm_change(
    onsets: np.ndarray, beat_times: np.ndarray, bpm: float,
    window: float = 4.0, min_ratio: float = 1.9, min_gap: float = 12.0,
) -> list[EventCandidate]:
    """Detect changes in rhythmic density (fills, drops to half-time, etc.).

    Measured on a *grid-relative* basis: what fraction of 16th-note slots carry
    an onset.  A fill and a dropped backbeat read very differently in absolute
    onsets-per-second but similarly here, which is the musically meaningful view.
    """
    if len(beats := np.asarray(beat_times, dtype=np.float64)) < 16 or bpm <= 0 or len(onsets) < 8:
        return []
    onsets = np.asarray(onsets, dtype=np.float64)
    grid = window
    step = 60.0 / bpm / 2.0  # eighth notes
    out: list[EventCandidate] = []
    t = float(beats[0])
    last = -1e9
    series: list[tuple[float, float]] = []
    while t < float(beats[-1]):
        n = np.sum((onsets >= t) & (onsets < t + grid))
        series.append((t, n / (grid / step)))
        t += grid
    if len(series) < 6:
        return []
    times = np.array([s[0] for s in series])
    dens = np.array([s[1] for s in series])
    sm = smooth(dens, 3)
    med = float(np.median(sm))
    if med <= 0.2:
        return []
    for i in range(2, len(sm) - 2):
        before = float(np.mean(sm[i - 2:i]))
        after = float(np.mean(sm[i:i + 2]))
        if before <= 0.2 or after <= 0.2:
            continue
        ratio = after / before
        if not (ratio >= min_ratio or ratio <= 1.0 / min_ratio):
            continue
        if times[i] - last < min_gap:
            continue
        last = float(times[i])
        denser = ratio > 1
        out.append(EventCandidate(
            t=float(times[i]), end=None, kind="rhythm_change",
            label=("Percussion density rises" if denser else
                   "Rhythm thins out (drums drop back)"),
            strength=float(np.clip(abs(ratio - 1.0), 0.1, 1.0)),
            confidence=0.55, is_continuous=True,
            evidence=[{
                "kind": "dsp", "method": "dsp:onset_density_grid", "metric": "onsets_per_8th",
                "t0": round(float(times[i]) - grid, 2), "t1": round(float(times[i]) + grid, 2),
                "value": round(ratio, 2), "unit": "x",
                "strength": "direct",
                "note": (f"onsets per eighth-note slot change {before:.2f} -> "
                         f"{after:.2f} over {grid:.0f}s"),
            }],
            metric="onset_density", value_before=before, value_after=after,
        ))
    return out


def dedupe_events(events: list[EventCandidate], min_gap: float = 0.35
                  ) -> list[EventCandidate]:
    """Collapse near-simultaneous events, keeping the strongest per (kind, source)."""
    events = sorted(events, key=lambda e: (e.t, -e.strength))
    out: list[EventCandidate] = []
    for e in events:
        clash = False
        for o in out[-6:]:
            if abs(o.t - e.t) < min_gap and o.kind == e.kind and \
                    set(o.source_ids) & set(e.source_ids):
                clash = True
                break
        if not clash:
            out.append(e)
    return out
