"""Turning a song analysis into a compact, token-efficient prompt.

The constraint that shapes this whole file: the model must be able to cite
measurements, but we must not send it hundreds of thousands of curve samples.
So we send:

* a **song card** — a few hundred numbers of global facts
* a **per-section table** — aggregated statistics, one row per section
* an **event table** — only events, each with its own evidence already
  summarised to one line
* **windowed detail** only for the region a question is about

Numbers are quantised to 2-3 significant figures, which costs nothing in
usefulness and roughly halves the token count.
"""

from __future__ import annotations

import json
from typing import Any

from arrangement.schema import SongAnalysis


def n(v: float, sig: int = 2) -> float | None:
    """Quantise to `sig` significant figures. Keeps prompts small, loses nothing."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or abs(f) == float("inf"):
        return None
    if f == 0:
        return 0.0
    from math import floor, log10

    return round(f, -int(floor(log10(abs(f)))) + (sig - 1))


def fmt_time(t: float, bpm: float | None = None, meter: str | None = None) -> str:
    m, s = divmod(max(0.0, t), 60)
    out = f"{int(m)}:{s:04.1f}"
    if bpm and bpm > 0:
        try:
            n_, d = (meter or "4/4").split("/")
            bpb = max(1.0, float(n_) * 4.0 / float(d))
        except Exception:
            bpb = 4.0
        beats = (t / (60.0 / bpm))
        bar = int(beats // bpb) + 1
        beat = int(beats % bpb) + 1
        out += f" (bar {bar}.{beat})"
    return out


# ---------------------------------------------------------------------------


def song_card(a: SongAnalysis) -> dict[str, Any]:
    b = a.beats
    return {
        "title": a.title,
        "artist": a.artist,
        "album": a.album,
        "duration_seconds": n(a.duration, 4),
        "sample_rate_hz": a.source_quality.get("native_sample_rate"),
        "channels": a.source_quality.get("native_channels"),
        "is_mono_source": a.is_mono_source,
        "lossy_source": a.source_quality.get("lossy"),
        "tempo": {
            "bpm": n(b.bpm, 4),
            "confidence": n(b.bpm_confidence, 2),
            "meter": b.meter,
            "meter_confidence": n(b.meter_confidence, 2),
            "first_downbeat_seconds": n(b.offset_seconds, 3),
            "tempo_drift_bpm": n(b.tempo_drift, 2),
            "irregular_pulse": b.is_tangent,
            "grid_confidence": n(b.beat_grid_confidence, 2),
        },
        "loudness": {
            "integrated_lufs": n(a.loudness.integrated_lufs, 4),
            "loudness_range_lu": n(a.loudness.lra, 3),
            "true_peak_dbfs": n(a.loudness.true_peak_dbfs, 3),
            "peak_to_loudness_db": n(a.loudness.plr_db, 3),
            "crest_db": n(a.loudness.crest_db, 3),
        },
        "stereo_image": {
            "mean_pan": n(_curve_mean(a, "stereo_pan"), 3),
            "mean_width_side_mid": n(_curve_mean(a, "stereo_width"), 3),
            "mean_correlation": n(_curve_mean(a, "stereo_correlation"), 3),
            "is_mono": a.is_mono_source,
        },
        "energy_model": {
            "description": a.energy.model_notes,
            "mean": n(_curve_mean(a, "perceived"), 3),
            "p10": n(_percentile(a, "perceived", 10), 3),
            "p90": n(_percentile(a, "perceived", 90), 3),
            "dynamic_range": n(
                (_percentile(a, "perceived", 95) - _percentile(a, "perceived", 10)), 3),
        },
        "counts": {
            "sources": len(a.sources),
            "sections": len(a.sections),
            "events": len(a.events),
        },
        "sections": [_section_row(a, s) for s in a.sections],
        "sources": [_source_row(a, s) for s in a.sources],
        "arrangement_events": [_event_row(a, e) for e in a.events],
        "energy_changes": [
            {
                "at": fmt_time(c.t, b.bpm, b.meter),
                "at_seconds": n(c.t, 4),
                "direction": c.direction,
                "delta": n(c.delta, 2),
                "driven_by": [[name, n(v, 2)] for name, v in c.contributors[:4]],
            }
            for c in a.energy_changes[:40]
        ],
        "patterns": [
            {"kind": p.kind, "title": p.title, "detail": p.detail,
             "strength": n(p.strength, 2)}
            for p in a.patterns
        ],
        "spatial_notes": a.warnings[:6],
    }


def _curve_mean(a: SongAnalysis, name: str) -> float | None:
    c = a.curves.get(name) or a.energy.components.get(name) or (
        a.energy.raw if name == "raw" else a.energy.perceived)
    if c is None or not c.values:
        return None
    vals = list(c.values)
    return sum(vals) / len(vals)


def _percentile(a: SongAnalysis, name: str, p: float) -> float | None:
    c = a.curves.get(name) or a.energy.components.get(name) or (
        a.energy.raw if name == "raw" else a.energy.perceived)
    if c is None or not c.values:
        return None
    s = sorted(c.values)
    k = (len(s) - 1) * p / 100.0
    f0, f1 = int(k), min(int(k) + 1, len(s) - 1)
    return s[f0] + (s[f1] - s[f0]) * (k - f0)


def _section_row(a: SongAnalysis, s) -> dict[str, Any]:
    return {
        "id": s.id,
        "at": fmt_time(s.start, a.beats.bpm, a.beats.meter),
        "start": n(s.start, 4),
        "end": n(s.end, 4),
        "detected_label": s.label.value,
        "label_confidence": n(s.label_confidence, 2),
        "energy": n(s.energy_perceived, 3),
        "sources_playing": s.source_count,
        "source_ids": s.source_ids,
        "stereo_width": n(s.stereo_width, 3),
        "correlation": n(s.stereo_correlation, 3),
        "repeat_group": s.repeat_group,
        "repeat_index": s.repeat_rank,
        "novelty": n(s.novelty, 2),
        "changes_from_previous": s.deltas_from_previous,
    }


def _source_row(a: SongAnalysis, s) -> dict[str, Any]:
    return {
        "id": s.id,
        "label": s.label,
        "family": s.family.value,
        "detected_from": s.separator_model,
        "confidence": n(s.confidence, 2),
        "plays_for_seconds": n(s.total_active_seconds, 3),
        "duty_cycle": n(s.duty_cycle, 2),
        "entries": s.n_entries,
        "exits": s.n_exits,
        "mean_level_dbfs": n(s.mean_level_db, 3),
        "pan": n(s.mean_pan, 2),
        "width": n(s.mean_width, 2),
        "pan_stability": n(s.pan_stability, 2),
        "descriptors": s.descriptor.descriptors,
        "role": s.descriptor.role,
        "spectral_centroid_hz": n(s.descriptor.spectral_centroid_hz, 3),
        "spectral_flatness": n(s.descriptor.spectral_flatness, 2),
        "active_spans": [
            {"start": n(g.start, 4), "end": n(g.end, 4),
             "level_dbfs": n(g.level_db, 3), "confidence": n(g.confidence, 2)}
            for g in s.segments[:24]
        ],
        "active_span_count": len(s.segments),
    }


def _event_row(a: SongAnalysis, e) -> dict[str, Any]:
    ev = e.evidence[0] if e.evidence else None
    return {
        "id": e.id,
        "at": fmt_time(e.t, a.beats.bpm, a.beats.meter),
        "start_seconds": n(e.t, 4),
        "end_seconds": n(e.end, 4) if e.end is not None else None,
        "kind": e.kind.value,
        "label": e.label,
        "source_ids": e.source_ids,
        "strength": n(e.strength, 2),
        "confidence": n(e.confidence, 2),
        "evidence": (ev.note if ev else None),
        "evidence_metric": (f"{ev.method} / {ev.metric}" if ev else None),
    }


# ---------------------------------------------------------------------------
# Windowed detail
# ---------------------------------------------------------------------------


def window_detail(a: SongAnalysis, t0: float, t1: float, max_events: int = 30,
                  resolution: float = 0.5) -> dict[str, Any]:
    """Fine-grained detail for a time range, for "what changed here?".

    Curve values are resampled to `resolution` seconds and quantised, so a
    10-second window costs a few hundred numbers rather than tens of thousands.
    """
    b = a.beats
    t0 = max(0.0, t0)
    t1 = min(a.duration, max(t1, t0 + 0.5))
    n_pts = max(2, int((t1 - t0) / resolution) + 1)
    ts = [t0 + i * resolution for i in range(n_pts)]

    def series(name: str) -> list[float | None]:
        c = a.curves.get(name) or a.energy.components.get(name)
        if c is None or not c.values:
            return [None] * n_pts
        out = []
        for t in ts:
            i = max(0, min(len(c.values) - 1, int(t * c.frame_hz)))
            out.append(n(c.values[i], 3))
        return out

    evs = [e for e in a.events if t0 - 0.75 <= e.t <= t1 + 0.75]
    evs.sort(key=lambda e: e.t)
    sources = []
    for s in a.sources:
        spans = [g for g in s.segments if g.end >= t0 and g.start <= t1]
        if not spans:
            continue
        sources.append({
            "id": s.id, "label": s.label,
            "spans": [{"start": n(max(g.start, t0), 4), "end": n(min(g.end, t1), 4),
                       "level_dbfs": n(g.level_db, 3),
                       "onset_slope_db_per_s": n(g.onset_slope_db_s, 2)}
                      for g in spans[:16]],
        })

    sections = [s for s in a.sections if s.end >= t0 and s.start <= t1]
    return {
        "window": {"start": fmt_time(t0, b.bpm, b.meter), "end": fmt_time(t1, b.bpm, b.meter),
                   "start_seconds": n(t0, 4), "end_seconds": n(t1, 4),
                   "resolution_seconds": resolution},
        "energy": {
            "perceived": series("perceived"),
            "raw": series("raw"),
            "loudness": series("loudness"),
            "stereo_width": series("stereo_width"),
            "bass_activity": series("bass_activity"),
            "high_activity": series("high_activity"),
            "onset_density": series("onset_density"),
            "active_layers": series("active_layers"),
        },
        "events": [_event_row(a, e) for e in evs[:max_events]],
        "sources": sources,
        "sections": [{"id": s.id, "start": n(s.start, 4), "end": n(s.end, 4),
                      "label": s.label.value, "energy": n(s.energy_perceived, 3),
                      "source_ids": s.source_ids} for s in sections],
    }


def compare_windows(a: SongAnalysis, a0: float, a1: float, b0: float, b1: float
                    ) -> dict[str, Any]:
    """A-vs-B comparison, with the differences computed, not guessed."""
    b = a.beats

    def summarise(t0: float, t1: float) -> dict[str, Any]:
        t0, t1 = max(0.0, t0), min(a.duration, max(a.duration if t1 <= 0 else t1, t0 + 0.5))
        active: dict[str, float] = {}
        for s in a.sources:
            tot = sum(max(0.0, min(g.end, t1) - max(g.start, t0)) for g in s.segments)
            if tot > 0.25:
                active[s.id] = tot / (t1 - t0)
        def win(name: str) -> float | None:
            c = a.curves.get(name) or a.energy.components.get(name)
            if c is None or not c.values:
                return None
            i0 = max(0, int(t0 * c.frame_hz))
            i1 = min(len(c.values), int(t1 * c.frame_hz))
            if i1 <= i0:
                return None
            return n(sum(c.values[i0:i1]) / (i1 - i0), 3)
        secs = [s for s in a.sections if s.end > t0 and s.start < t1]
        return {
            "label": _window_label(a, t0, t1, secs),
            "start_seconds": n(t0, 4), "end_seconds": n(t1, 4),
            "duration_seconds": n(t1 - t0, 3),
            "sections": [s.id for s in secs],
            "source_presence": {k: n(v, 2) for k, v in active.items()},
            "mean_energy": win("perceived"),
            "mean_loudness_lufs": win("loudness"),
            "mean_stereo_width": win("stereo_width"),
            "mean_bass": win("bass_activity"),
            "mean_high": win("high_activity"),
            "mean_onset_density": win("onset_density"),
            "mean_active_layers": win("active_layers"),
            "events": [_event_row(a, e) for e in a.events if t0 <= e.t < t1][:24],
        }

    A, B = summarise(a0, a1), summarise(b0, b1)
    pa, pb = set(A["source_presence"]), set(B["source_presence"])
    metric_changes = []
    for k in ("mean_energy", "mean_loudness_lufs", "mean_stereo_width",
              "mean_bass", "mean_high", "mean_onset_density", "mean_active_layers"):
        va, vb = A.get(k), B.get(k)
        if va is None or vb is None:
            continue
        delta = round(vb - va, 3)
        if abs(delta) < (0.05 if "width" in k or "energy" in k else 0.2):
            continue
        metric_changes.append({"metric": k, "a": va, "b": vb, "delta": delta})
    return {
        "a": A, "b": B,
        "only_in_a": sorted(pa - pb), "only_in_b": sorted(pb - pa),
        "metric_changes": metric_changes,
    }


def _window_label(a: SongAnalysis, t0: float, t1: float, secs) -> str:
    if secs:
        if len(secs) == 1:
            return f"{secs[0].label.value} at {fmt_time(secs[0].start, a.beats.bpm, a.beats.meter)}"
        return f"{fmt_time(t0, a.beats.bpm, a.beats.meter)} – {fmt_time(t1, a.beats.bpm, a.beats.meter)}"
    return f"{fmt_time(t0, a.beats.bpm, a.beats.meter)} – {fmt_time(t1, a.beats.bpm, a.beats.meter)}"


# ---------------------------------------------------------------------------


SYSTEM_PROMPT = """You are a senior mix engineer and record producer annotating a \
finished piece of music for another producer who wants to understand how it was \
built. You are precise, concrete and allergic to filler.

ABSOLUTE RULES — these are not stylistic preferences:

1. You may ONLY state things supported by the measurement bundle you are given. \
If the bundle does not contain an instrument, a time, or a number, you may not \
mention it. Never infer a sound that was not measured.
2. Every time you mention, cite it using the exact time/bar format from the bundle \
(e.g. "1:14.0 (bar 12.2)"). Do not invent times.
3. Only use source labels from the `sources` list. Do not rename or re-infer them.
4. If something you would like to say is not supported, write it as an explicit \
limitation: "Not determinable from this analysis: <what you would need>". A \
stated limitation is more useful than a plausible guess.
5. Distinguish what was MEASURED from what you INFER. Mark inferences as such \
("likely", "consistent with", "suggests").
6. Do not flatter the record and do not manufacture criticism. If the analysis \
shows an unremarkable arrangement, say so plainly.
7. Prefer the specific over the general. "The second chorus adds a distorted \
guitar layer 6 dB louder than the verse" beats "the second chorus is fuller".

Your voice: a good mix engineer talking to another engineer. Short sentences. \
Concrete numbers. No adjectives that do not describe something measurable."""

USER_GUIDANCE = """Write for a producer who wants to learn from this record: not a \
summary of what happens, but WHY the record is built the way it is, and what \
techniques are doing the work. Aim for {n_claims} distinct observations, each \
grounded in a specific measurement."""


def build_prompt(a: SongAnalysis, task: str, payload: dict[str, Any],
                 n_claims: int = 6) -> list[dict[str, str]]:
    """Assemble the message list for one reasoning task."""
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"{task}\n\n{USER_GUIDANCE.format(n_claims=n_claims)}\n\n"
                                    f"MEASUREMENT BUNDLE (JSON):\n{body}"},
    ]
