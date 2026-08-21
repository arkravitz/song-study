"""The structural model must not silently poison the song timeline."""

import json
from pathlib import Path

import numpy as np
import pytest

from arrangement.models.allinone_mlx import load_result, normalize_segments


def test_normalize_segments_removes_markers_and_covers_the_song():
    spans = normalize_segments([
        {"start": 0, "end": 0.3, "label": "start"},
        {"start": 0.3, "end": 18, "label": "intro"},
        {"start": 18, "end": 40, "label": "inst"},
        {"start": 40, "end": 56, "label": "chorus"},
        {"start": 56, "end": 70, "label": "chorus"},
        {"start": 70, "end": 74, "label": "end"},
    ], 74)
    assert [(s.start, s.end, s.label) for s in spans] == [
        (0, 18, "intro"), (18, 40, "instrumental"),
        (40, 56, "chorus"), (56, 74, "chorus")]


def test_load_result_validates_beat_grid_and_section_coverage(tmp_path: Path):
    path = tmp_path / "analysis.json"
    path.write_text(json.dumps({
        "bpm": 120,
        "beats": [0.5 * i for i in range(20)],
        "downbeats": [0, 2, 4, 6, 8],
        "segments": [{"start": 0, "end": 10, "label": "verse"}],
    }))
    result = load_result(path, 10)
    assert result.bpm == 120
    assert result.beats[3] == 1.5
    assert result.sections[0].label == "verse"

    data = json.loads(path.read_text())
    data["beats"][4] = data["beats"][3]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="beat"):
        load_result(path, 10)


def test_unknown_section_label_is_not_presented_as_a_named_section():
    spans = normalize_segments([
        {"start": 0, "end": 12, "label": "alien_label"},
        {"start": 12, "end": 30, "label": "chorus"},
    ], 30)
    assert [s.label for s in spans] == ["unknown", "chorus"]


def test_adjacent_outro_fragments_form_one_ending():
    spans = normalize_segments([
        {"start": 0, "end": 20, "label": "verse"},
        {"start": 20, "end": 34, "label": "outro"},
        {"start": 34, "end": 42, "label": "outro"},
    ], 42)
    assert [(s.start, s.end, s.label) for s in spans] == [
        (0, 20, "verse"), (20, 42, "outro")]


def test_model_section_runs_keep_internal_seams_and_count_choruses_in_order():
    from types import SimpleNamespace

    from arrangement.analysis.pipeline import merge_macro_sections
    from arrangement.schema import Section, SectionLabel

    labels = [SectionLabel.VERSE, SectionLabel.CHORUS, SectionLabel.VERSE,
              SectionLabel.CHORUS, SectionLabel.CHORUS, SectionLabel.CHORUS]
    ends = [26, 47, 73, 98, 136, 157]
    sections = [
        Section(id=f"s{i}", start=0 if i == 0 else ends[i - 1], end=end,
                label=label, label_source="model", repeat_group=1)
        for i, (end, label) in enumerate(zip(ends, labels))
    ]
    song = SimpleNamespace(sections=sections, duration=157)
    merge_macro_sections(song)
    assert [s.macro_label for s in sections] == [
        "verse 1", "chorus 1", "verse 2", "chorus 2", "chorus 3", "chorus 3"]
    assert sections[-2].macro_id == sections[-1].macro_id


def test_external_grid_skips_beat_this_and_uses_model_tempo(monkeypatch):
    from arrangement.dsp import rhythm

    monkeypatch.setattr(rhythm, "onset_envelope", lambda *_args: (
        np.array([]), np.array([]), np.zeros(100), 10.0))
    monkeypatch.setattr(rhythm, "track_beats", lambda *_args: (_ for _ in ()).throw(
        AssertionError("Beat This should not run when MLX supplied a grid")))
    beats = np.arange(0, 20, 0.5)
    result = rhythm.analyse_rhythm(
        np.zeros(2000), 100, external_beats=beats,
        external_downbeats=beats[::4], external_bpm=120.0)
    assert result.method == "all-in-one-mlx"
    assert result.bpm == 120.0
    np.testing.assert_array_equal(result.beats, beats)


def test_repeat_conflict_and_independent_change_recover_missed_chorus_boundary():
    from types import SimpleNamespace

    from arrangement.analysis.section_repair import propose_repairs

    model = [SimpleNamespace(start=a, end=b, label=label, repeat_group=group)
             for a, b, label, group in [
                 (72.5, 98.0, "chorus", 1),
                 (98.0, 136.2, "chorus", 3),
                 (136.2, 157.3, "chorus", 1),
             ]]
    fallback = [SimpleNamespace(start=t, novelty=nov) for t, nov in [
        (109.1, .69), (118.8, .78), (130.5, .75),
    ]]
    changes = [SimpleNamespace(t=t, direction=direction, magnitude=mag)
               for t, direction, mag in [
                   (115.3, "fall", .46), (119.3, "rise", .18),
                   (127.3, "rise", .24),
               ]]
    assert propose_repairs(model, fallback, changes) == [(1, 118.8)]


def test_section_repair_needs_repetition_and_acoustic_corroboration():
    from types import SimpleNamespace

    from arrangement.analysis.section_repair import propose_repairs

    model = [SimpleNamespace(start=a, end=b, label="chorus", repeat_group=g)
             for a, b, g in [(0, 30, 1), (30, 68, 3), (68, 90, 1)]]
    fallback = [SimpleNamespace(start=49, novelty=.9)]
    fall = SimpleNamespace(t=47, direction="fall", magnitude=.4)
    rise = SimpleNamespace(t=50, direction="rise", magnitude=.2)
    assert propose_repairs(model, fallback, [fall, rise]) == [(1, 49)]
    assert propose_repairs(model, fallback, [fall]) == []
    assert propose_repairs(model, [SimpleNamespace(start=49, novelty=.5)], [fall, rise]) == []
    model[2].repeat_group = 4
    assert propose_repairs(model, fallback, [fall, rise]) == []
