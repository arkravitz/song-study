"""Refresh cached activity and its dependents without replacing listener edits."""
from __future__ import annotations

import numpy as np

from arrangement.analysis import events as EV
from arrangement.analysis.pipeline import (
    _bar_beat, _bars_for_segments, _build_transitions, _section_delta, _to_evidence,
)
from arrangement.analysis.section_edit import _refresh_metrics
from arrangement.dsp.activity import ActivityResult
from arrangement.dsp.energy import (
    EnergyComponent, EnergyResult, _to_grid, _znorm, attribute_change, find_energy_changes,
)
from arrangement.dsp.features import normalize_minmax, smooth
from arrangement.schema import ActivitySegment, ArrangementEvent, EnergyChange


def refresh_source_activity(song, source_id: str, activity: ActivityResult) -> None:
    source = next(s for s in song.sources if s.id == source_id)
    source.segments = [ActivitySegment(
        start=s.start, end=s.end, level_db=s.level_db, peak_db=s.peak_db,
        confidence=s.conf, intensity=s.mean_intensity,
        onset_slope_db_s=s.attack_db_s, offset_slope_db_s=s.release_db_s,
        pan=source.mean_pan, width=source.mean_width,
    ) for s in activity.spans]
    source.total_active_seconds = sum(s.end - s.start for s in source.segments)
    source.duty_cycle = source.total_active_seconds / max(.01, song.duration)
    source.mean_level_db = float(np.mean([s.level_db for s in source.segments])) if source.segments else -70.
    source.max_level_db = max((s.peak_db for s in source.segments), default=-70.)
    source.n_entries = len(source.segments)
    source.n_exits = max(0, source.n_entries - 1)
    source.active_bars = _bars_for_segments(source.segments, song.beats)

    hz = 10.
    counts = np.zeros(max(2, int(song.duration * hz) + 1))
    for part in song.sources:
        for segment in part.segments:
            counts[max(0, int(segment.start * hz)):min(len(counts), int(segment.end * hz) + 1)] += 1

    model = song.energy
    if 'active_layers' in model.components and model.perceived and model.perceived.values:
        n = len(model.perceived.values)
        layer = model.components['active_layers']
        layer.values = _znorm(_to_grid(counts, hz, model.frame_hz, n)).tolist()
        layer.frame_hz = model.frame_hz
        components = [EnergyComponent(key, np.asarray(curve.values), model.weights[key])
                      for key, curve in model.components.items()
                      if key in model.weights and len(curve.values) == n]
        weight = sum(c.weight for c in components)
        if weight > 0:
            combined = sum((c.curve * c.weight for c in components), np.zeros(n)) / weight
            combined = smooth(combined, max(1, int(round(.35 * model.frame_hz))))
            perceived, lo, hi = normalize_minmax(combined, 2., 98.)
            model.perceived.values = perceived.tolist()
            model.norm_lo, model.norm_hi = lo, hi
            energy = EnergyResult(model.frame_hz, song.duration,
                                  np.asarray(model.raw.values) if model.raw else np.zeros(n),
                                  perceived, components, norm_lo=lo, norm_hi=hi)
            song.energy_changes = []
            for t, delta, magnitude in find_energy_changes(energy):
                _, pos, neg = attribute_change(energy, t, t + .35)
                song.energy_changes.append(EnergyChange(
                    t=t, end=t + .4, direction='rise' if delta > 0 else 'fall',
                    delta=delta, magnitude=magnitude, bar=_bar_beat(t, song.beats)[0],
                    contributors=pos if delta > 0 else neg))

    # Replace only events that depended on the corrected part or layer count.
    song.events = [e for e in song.events if source_id not in e.source_ids
                   and e.kind.value != 'density_change']
    candidates = EV.events_from_activity({source_id: activity}, {
        source_id: {'label': source.label, 'separator_model': source.separator_model}})
    candidates += EV.events_from_density(counts, hz, song.duration)
    for i, candidate in enumerate(EV.dedupe_events(candidates)):
        bar, beat = _bar_beat(candidate.t, song.beats)
        song.events.append(ArrangementEvent(
            id=f'activity-refresh-{source_id}-{i}', t=candidate.t, end=candidate.end,
            kind=candidate.kind, label=candidate.label, source_ids=candidate.source_ids,
            strength=candidate.strength, confidence=candidate.confidence,
            bar=bar, beat_in_bar=beat, is_continuous=candidate.kind in EV.CONTINUOUS_KINDS,
            evidence=[_to_evidence(e) for e in candidate.evidence]))
    song.events.sort(key=lambda e: e.t)
    for i, section in enumerate(song.sections):
        _refresh_metrics(song, section)
        section.deltas_from_previous = _section_delta(song.sections[i - 1], section) if i else []
    song.transitions.clear()
    _build_transitions(song, None)
    from arrangement.analysis.patterns import find_patterns
    song.patterns = find_patterns(song)
    if song.insights or song.comparisons:
        warning = 'Instrument activity was corrected. Earlier generated commentary may use the previous activity map.'
        if warning not in song.warnings:
            song.warnings.append(warning)
