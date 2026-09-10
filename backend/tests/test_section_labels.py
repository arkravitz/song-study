"""Portable regressions for positional labels and edited passage hierarchy.

Fixtures contain invented spans and measurements, never a private song library.
A displayed macro passage may have several detector/edit segments. Position is
checked for that contiguous passage, not for every internal fragment.
"""

import pytest

from arrangement.analysis.pipeline import label_sections_by_group
from arrangement.analysis.section_edit import move_macro_boundary
from arrangement.schema import Section, SectionLabel, SongAnalysis, Source


def positional_violations(song):
    """Audit generated labels on contiguous macro occurrences.

    A reused macro ID separated by other material is a separate occurrence.
    Listener names are judgments and are not treated as model assertions.
    """
    passages = []
    for section in sorted(song.sections, key=lambda s: s.start):
        key = section.macro_id or section.id
        if (passages and passages[-1][0] == key
                and abs(passages[-1][1][-1].end - section.start) < .05):
            passages[-1][1].append(section)
        else:
            passages.append((key, [section]))
    bad = []
    for key, members in passages:
        start, end = members[0].start, members[-1].end
        for section in members:
            if section.label_source == "user":
                continue
            if section.label == SectionLabel.INTRO and start >= .75:
                bad.append(key)
            if section.label in (SectionLabel.OUTRO, SectionLabel.FADE) and end < song.duration - .75:
                bad.append(key)
    return bad


def song_from_spans(spans, duration=200):
    song = SongAnalysis(id="synthetic-form", title="Synthetic form", duration=duration)
    song.sources = [
        Source(id="drums", label="Drums", family="drums"),
        Source(id="bass", label="Bass", family="bass"),
        Source(id="vocals", label="Vocals", family="vocals"),
    ]
    for i, (start, end, group, ids, energy) in enumerate(spans):
        song.sections.append(Section(
            id=f"sec{i}", start=start, end=end, repeat_group=group,
            source_ids=ids, source_count=len(ids), energy_perceived=energy,
            energy_raw=energy, stereo_width=.3,
        ))
    return song


def test_editing_intro_boundary_keeps_internal_intro_fragments_in_opening_passage():
    song = SongAnalysis(id="edited-intro", title="Synthetic edit", duration=40,
                        sections=[
        Section(id="opening", start=0, end=13.58, label=SectionLabel.INTRO,
                label_source="model", macro_id="intro", macro_label="intro"),
        Section(id="verse", start=13.58, end=40, label=SectionLabel.VERSE,
                label_source="model", macro_id="verse", macro_label="verse 1"),
    ])
    move_macro_boundary(song, "verse", 13.7607)
    assert len(song.sections) == 3
    assert song.sections[1].label == SectionLabel.INTRO
    assert song.sections[1].start == 13.58
    assert song.sections[1].macro_id == song.sections[0].macro_id
    assert positional_violations(song) == []


@pytest.mark.parametrize("label,start,end", [
    (SectionLabel.INTRO, 40, 60),
    (SectionLabel.OUTRO, 40, 60),
    (SectionLabel.FADE, 40, 60),
])
def test_audit_still_rejects_genuinely_misplaced_generated_passages(label,start,end):
    song = SongAnalysis(id="bad-form", title="Bad form", duration=200,
                        sections=[Section(id="bad", start=start, end=end,
                                          label=label, label_source="model")])
    assert positional_violations(song) == ["bad"]


def test_reused_macro_id_does_not_hide_a_mid_song_intro():
    song = SongAnalysis(id="reused-id", title="Reused ID", duration=60, sections=[
        Section(id="a", start=0, end=10, macro_id="same", label=SectionLabel.INTRO),
        Section(id="b", start=10, end=40, macro_id="verse", label=SectionLabel.VERSE),
        Section(id="c", start=40, end=60, macro_id="same", label=SectionLabel.INTRO),
    ])
    assert positional_violations(song) == ["same"]


def test_shared_repeat_group_cannot_paint_mid_song_material_as_outro():
    song = song_from_spans([
        (0, 10, 0, ["drums", "bass"], .5),
        (10, 40, 1, ["drums", "bass", "vocals"], .8),
        (40, 57, 2, ["drums", "bass"], .25),
        (57, 100, 1, ["drums", "bass", "vocals"], .8),
        (100, 182, 3, ["drums", "bass"], .5),
        (182, 200, 2, ["drums", "bass"], .25),
    ])
    label_sections_by_group(song)
    assert song.sections[2].label not in (SectionLabel.OUTRO, SectionLabel.FADE, SectionLabel.UNKNOWN)
    assert song.sections[-1].label in (SectionLabel.OUTRO, SectionLabel.FADE)
    assert positional_violations(song) == []


def test_recurring_opening_group_does_not_name_a_mid_song_intro():
    song = song_from_spans([
        (0, 10, 0, ["drums", "bass"], .5),
        (10, 40, 1, ["drums", "bass", "vocals"], .8),
        (40, 60, 0, ["drums", "bass"], .5),
    ], duration=60)
    label_sections_by_group(song)
    assert song.sections[2].label != SectionLabel.INTRO
    assert positional_violations(song) == []


def test_opening_and_ending_vocabulary_remains_available():
    song = song_from_spans([
        (0, 10, 0, ["drums", "bass"], .5),
        (10, 40, 1, ["drums", "bass", "vocals"], .8),
        (40, 60, 2, ["drums", "bass"], .25),
    ], duration=60)
    label_sections_by_group(song)
    assert song.sections[0].label == SectionLabel.INTRO
    assert song.sections[-1].label in (SectionLabel.OUTRO, SectionLabel.FADE)


def test_a_recurring_chorus_that_reaches_the_end_keeps_its_name():
    song = song_from_spans([
        (0, 10, 0, ["drums", "bass"], .5),
        (10, 40, 1, ["drums", "bass", "vocals"], .8),
        (40, 60, 2, ["drums", "bass"], .4),
        (60, 100, 1, ["drums", "bass", "vocals"], .8),
    ], duration=100)
    label_sections_by_group(song)
    assert song.sections[1].label == song.sections[-1].label == SectionLabel.CHORUS
