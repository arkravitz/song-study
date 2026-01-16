"""Listener corrections to section boundaries, without re-running the models."""

from __future__ import annotations

from bisect import bisect_right
import math
import re
from uuid import uuid4

import numpy as np

from arrangement.schema import Section, SectionLabel, SongAnalysis

MIN_SECTION_SECONDS = .1


def _mean(values, frame_hz: float, start: float, end: float,
          previous: float) -> float:
    if not values or frame_hz <= 0:
        return previous
    a = min(len(values) - 1, max(0, int(start * frame_hz)))
    b = min(len(values), max(a + 1, int(end * frame_hz)))
    return float(np.mean(values[a:b]))


def _label(name: str) -> SectionLabel:
    base = re.sub(r"\s+\d+$", "", name.strip().lower())
    try:
        return SectionLabel(base.replace(" ", "-"))
    except ValueError:
        return SectionLabel.UNKNOWN


def _refresh_metrics(song: SongAnalysis, section: Section) -> None:
    a, b = section.start, section.end
    if song.energy.raw:
        section.energy_raw = _mean(song.energy.raw.values, song.energy.raw.frame_hz,
                                   a, b, section.energy_raw)
    if song.energy.perceived:
        section.energy_perceived = _mean(
            song.energy.perceived.values, song.energy.perceived.frame_hz,
            a, b, section.energy_perceived)
    if song.spatial.width:
        section.stereo_width = _mean(
            song.spatial.width, song.spatial.frame_hz,
            a, b, section.stereo_width)
    if song.spatial.correlation:
        section.stereo_correlation = _mean(
            song.spatial.correlation, song.spatial.frame_hz,
            a, b, section.stereo_correlation)
    beats = song.beats.beats
    if beats:
        section.bar_start = bisect_right(beats, a) // 4 + 1
        section.bar_end = bisect_right(beats, b) // 4 + 1
    duration = max(.01, b - a)
    active = [source for source in song.sources if not source.is_silent and
              sum(max(0, min(seg.end, b) - max(seg.start, a))
                  for seg in source.segments) / duration >= .45]
    section.source_ids = [source.id for source in active]
    section.source_count = len(active)
    section.density = len(active) / max(1, len(song.sources))
    section.dominant_source = (max(active, key=lambda s: s.mean_level_db).id
                               if active else None)


def split_macro_section(song: SongAnalysis, macro_id: str, at: float,
                        before_name: str | None = None,
                        after_name: str | None = None) -> Section:
    """Split a displayed passage and keep its existing identity on the right.

    Keeping the later macro id means a previously saved user name for that
    passage stays attached to the later section. The new earlier passage gets a
    fresh id. Both sides receive fresh window measurements; no energy, width or
    source summary is copied across the new boundary.
    """
    for name in (before_name, after_name):
        if name is not None and not 1 <= len(name.strip()) <= 36:
            raise ValueError("section names must be 1–36 characters")
    original = [s for s in song.sections if (s.macro_id or s.id) == macro_id]
    if not original:
        raise ValueError("section no longer exists")
    start, end = min(s.start for s in original), max(s.end for s in original)
    if not math.isfinite(at) or not start + MIN_SECTION_SECONDS <= at <= end - MIN_SECTION_SECONDS:
        raise ValueError("place the playhead inside the section, away from its edges")

    new_id = f"manual-{uuid4().hex[:10]}"
    result: list[Section] = []
    for section in song.sections:
        if (section.macro_id or section.id) != macro_id:
            result.append(section)
        elif section.start < at < section.end:
            left = section.model_copy(deep=True)
            right = section.model_copy(deep=True)
            left.id = f"{section.id}-before-{int(at * 1000)}"
            left.end = at
            right.start = at
            right.novelty = 0.0  # A human boundary is not a measured novelty.
            _refresh_metrics(song, left)
            _refresh_metrics(song, right)
            result.extend([left, right])
        else:
            result.append(section)

    earlier = [s for s in result if (s.macro_id or s.id) == macro_id and s.end <= at]
    later = [s for s in result if (s.macro_id or s.id) == macro_id and s.start >= at]
    if not earlier or not later:
        raise ValueError("place the boundary inside the passage")
    for group, mid, name in ((earlier, new_id, before_name),
                             (later, macro_id, after_name)):
        for position, section in enumerate(group, 1):
            section.macro_id = mid
            if name is not None:
                name = name.strip()
                section.macro_label = name
                section.label = _label(name)
                section.label_source = "user"
                section.label_confidence = 1.0
            section.macro_part = position
            section.macro_parts = len(group)
            section.description = None

    song.sections = sorted(result, key=lambda s: s.start)
    _finish_edit(song)
    return later[0]


def move_macro_boundary(song: SongAnalysis, right_macro_id: str, at: float) -> None:
    """Move a shared section edge, preserving the internal detected segments.

    The section on either side keeps its name and macro identity. Material that
    crosses the moved edge becomes part of its new section; its window metrics
    are recalculated rather than copied from the former section.
    """
    macros: dict[str, list[Section]] = {}
    for section in sorted(song.sections, key=lambda s: s.start):
        macros.setdefault(section.macro_id or section.id, []).append(section)
    keys = list(macros)
    if right_macro_id not in macros or keys.index(right_macro_id) == 0:
        raise ValueError("the beginning of the song cannot be moved")
    left_id = keys[keys.index(right_macro_id) - 1]
    left, right = macros[left_id], macros[right_macro_id]
    if (not math.isfinite(at) or
            not left[0].start + MIN_SECTION_SECONDS <= at <= right[-1].end - MIN_SECTION_SECONDS):
        raise ValueError("keep the boundary between the neighboring section edges")
    if abs(left[-1].end - right[0].start) > .05:
        raise ValueError("these sections do not share a boundary")

    groups: tuple[list[Section], list[Section]] = ([], [])
    for original in left + right:
        section = original.model_copy(deep=True)
        if section.start < at < section.end:
            before = section.model_copy(deep=True)
            before.id = f"edit-{uuid4().hex[:12]}"
            before.end = at
            section.start = at
            section.novelty = 0.0
            groups[0].append(before)
            groups[1].append(section)
        else:
            groups[0 if section.end <= at else 1].append(section)
    if not all(groups):
        raise ValueError("each section must contain some of the song")
    # Remove tiny detector rounding gaps at the original join as well.
    groups[0][-1].end = groups[1][0].start = at
    groups[1][0].novelty = 0.0
    for sections, template, mid in ((groups[0], left[0], left_id),
                                    (groups[1], right[0], right_macro_id)):
        for section in sections:
            section.macro_id = mid
            section.macro_label = template.macro_label
            section.label = template.label
            section.label_source = template.label_source
            section.label_confidence = template.label_confidence
            section.description = None
            _refresh_metrics(song, section)
    affected = {left_id, right_macro_id}
    song.sections = sorted(
        [s for s in song.sections if (s.macro_id or s.id) not in affected]
        + groups[0] + groups[1], key=lambda s: s.start)
    _finish_edit(song)


def merge_macro_sections(song: SongAnalysis, right_macro_id: str) -> str:
    """Join adjacent displayed sections, retaining their internal analysis."""
    macros: dict[str, list[Section]] = {}
    for section in sorted(song.sections, key=lambda s: s.start):
        macros.setdefault(section.macro_id or section.id, []).append(section)
    keys = list(macros)
    if right_macro_id not in macros or keys.index(right_macro_id) == 0:
        raise ValueError("choose two neighboring sections to merge")
    left_id = keys[keys.index(right_macro_id) - 1]
    left, right = macros[left_id], macros[right_macro_id]
    if abs(left[-1].end - right[0].start) > .05:
        raise ValueError("these sections do not share a boundary")
    template = left[0]
    for section in right:
        section.macro_id = left_id
        section.macro_label = template.macro_label
        section.label = template.label
        section.label_source = template.label_source
        section.label_confidence = template.label_confidence
    for section in left + right:
        section.description = None
    # Undo cuts introduced by Split, while retaining genuine detected changes.
    result: list[Section] = []
    for section in song.sections:
        if (result and result[-1].macro_id == left_id and section.macro_id == left_id
                and abs(result[-1].end - section.start) < 1e-6
                and re.fullmatch(re.escape(section.id) + r"-before-\d+", result[-1].id)):
            section.start = result.pop().start
            _refresh_metrics(song, section)
        result.append(section)
    song.sections = result
    _finish_edit(song)
    return left_id


def _finish_edit(song: SongAnalysis) -> None:
    groups: dict[str, list[Section]] = {}
    for section in song.sections:
        groups.setdefault(section.macro_id or section.id, []).append(section)
    for sections in groups.values():
        for index, section in enumerate(sections, 1):
            section.macro_part = index
            section.macro_parts = len(sections)
    seen: dict[str, int] = {}
    for section in song.sections:
        key = section.macro_id or section.id
        seen.setdefault(key, len(seen) + 1)
        section.macro_index = seen[key]

    from arrangement.analysis.pipeline import _build_transitions, _section_delta

    for i, section in enumerate(song.sections):
        section.deltas_from_previous = (
            _section_delta(song.sections[i - 1], section) if i else [])
    song.transitions.clear()
    _build_transitions(song, None)
    warning = (
        "A section boundary was corrected by a listener. Earlier generated "
        "commentary may still use the previous section map.")
    if warning not in song.warnings:
        song.warnings.append(warning)
