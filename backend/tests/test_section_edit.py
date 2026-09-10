"""A listener's boundary correction must change the map and its measurements."""

import pytest

from arrangement.schema import Curve, EnergyModel, Section, SectionLabel, SongAnalysis


def _song():
    return SongAnalysis(
        id="edit-fixture", title="Edit fixture", duration=40,
        sections=[Section(id="sec0", start=0, end=40,
                          label=SectionLabel.CHORUS, label_source="model",
                          macro_id="mac001", macro_label="chorus 1")],
        energy=EnergyModel(
            raw=Curve(name="raw", frame_hz=1, values=[.2] * 20 + [.8] * 20),
            perceived=Curve(name="perceived", frame_hz=1,
                            values=[.2] * 20 + [.8] * 20),
        ),
    )


def test_split_preserves_later_section_identity_and_recomputes_energy():
    from arrangement.analysis.section_edit import split_macro_section

    song = _song()
    later = split_macro_section(song, "mac001", 20, "Passage", "Chorus 3")
    assert [(s.start, s.end, s.macro_label) for s in song.sections] == [
        (0, 20, "Passage"), (20, 40, "Chorus 3")]
    assert song.sections[0].macro_id != "mac001"
    assert later.id == "sec0" and later.macro_id == "mac001"
    assert song.sections[0].energy_perceived == pytest.approx(.2)
    assert later.energy_perceived == pytest.approx(.8)
    assert song.sections[0].label_source == later.label_source == "user"
    assert len(song.transitions) == 1 and song.transitions[0].t == 20


def test_split_rejects_tiny_passages_without_mutating_the_analysis():
    from arrangement.analysis.section_edit import split_macro_section

    song = _song()
    with pytest.raises(ValueError):
        split_macro_section(song, "mac001", .05, "Passage", "Chorus")
    assert len(song.sections) == 1


def test_split_at_existing_internal_boundary_preserves_both_segments():
    from arrangement.analysis.section_edit import split_macro_section

    song = _song()
    song.sections[0].end = 20
    second = song.sections[0].model_copy(deep=True)
    second.id = "sec1"
    second.start, second.end = 20, 40
    song.sections.append(second)

    split_macro_section(song, "mac001", 20, "Turnaround", "Chorus")

    assert len(song.sections) == 2
    assert [(s.macro_label, s.start, s.end) for s in song.sections] == [
        ("Turnaround", 0, 20), ("Chorus", 20, 40)]
    assert song.sections[0].macro_id != song.sections[1].macro_id


def test_split_survives_reload_without_losing_the_energy_curve(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from arrangement.analysis.section_edit import split_macro_section
    from arrangement.server import store

    monkeypatch.setattr(store, "get_settings",
                        lambda: SimpleNamespace(analyses_dir=tmp_path))
    song = _song()
    store.save_analysis(song)
    split_macro_section(song, "mac001", 20, "Passage", "Chorus 3")
    store.save_analysis(song)
    loaded = store.load_analysis(song.id)
    assert len(loaded.sections) == 2
    assert loaded.sections[1].macro_label == "Chorus 3"
    assert store.read_curve(song.id, "energy/perceived", 0, 40)["count"] == 40


def test_playhead_split_keeps_fractional_time_and_inherits_names():
    from arrangement.analysis.section_edit import split_macro_section

    song = _song()
    split_macro_section(song, "mac001", 1.375)
    assert [(s.start, s.end) for s in song.sections] == [(0, 1.375), (1.375, 40)]
    assert all(s.label == SectionLabel.CHORUS for s in song.sections)
    assert all(s.label_source == "model" for s in song.sections)


def _two_macros():
    song = _song()
    song.sections[0].end = 20
    song.sections[0].energy_perceived = .2
    right = song.sections[0].model_copy(deep=True)
    right.id = "sec1"
    right.start, right.end = 20, 40
    right.macro_id = "mac002"
    right.macro_label = "verse 1"
    right.label = SectionLabel.VERSE
    right.energy_perceived = .8
    song.sections.append(right)
    return song


@pytest.mark.parametrize("at", [12.375, 27.625])
def test_boundary_move_keeps_a_contiguous_map_and_remeasures_both_sides(at):
    from arrangement.analysis.section_edit import move_macro_boundary

    song = _two_macros()
    move_macro_boundary(song, "mac002", at)
    left = [s for s in song.sections if s.macro_id == "mac001"]
    right = [s for s in song.sections if s.macro_id == "mac002"]
    assert left[0].start == 0 and left[-1].end == at
    assert right[0].start == at and right[-1].end == 40
    assert all(a.end == b.start for a, b in zip(song.sections, song.sections[1:]))
    assert all(s.end > s.start for s in song.sections)
    assert all(s.label == SectionLabel.CHORUS for s in left)
    assert all(s.label == SectionLabel.VERSE for s in right)
    # The crossed material is assigned the new side's role but keeps its sound.
    crossed = next(s for s in song.sections if s.start < 20 < s.end or s.end == 20)
    assert crossed.energy_perceived == pytest.approx(.2)
    assert any(t.t == at for t in song.transitions)


def test_boundary_move_across_internal_segments_preserves_their_landmarks():
    from arrangement.analysis.section_edit import move_macro_boundary

    song = _two_macros()
    tail = song.sections[1].model_copy(deep=True)
    song.sections[1].end = 30
    tail.id, tail.start = "sec2", 30
    song.sections.append(tail)
    move_macro_boundary(song, "mac002", 32)
    assert [(s.start, s.end, s.macro_id) for s in song.sections] == [
        (0, 20, "mac001"), (20, 30, "mac001"),
        (30, 32, "mac001"), (32, 40, "mac002")]
    assert song.sections[-1].id == "sec2"
    assert [s.macro_part for s in song.sections] == [1, 2, 3, 1]


@pytest.mark.parametrize("at", [0, 40, float("nan"), float("inf")])
def test_invalid_boundary_move_does_not_modify_analysis(at):
    from arrangement.analysis.section_edit import move_macro_boundary

    song = _two_macros()
    original = song.model_dump_json()
    with pytest.raises(ValueError):
        move_macro_boundary(song, "mac002", at)
    assert song.model_dump_json() == original


def test_section_edit_routes_persist_fractional_playhead_and_boundary(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from arrangement.server import store
    from arrangement.server.app import app

    monkeypatch.setattr(store, 'get_settings', lambda: SimpleNamespace(analyses_dir=tmp_path))
    store.save_analysis(_song())
    client = TestClient(app)
    response = client.post('/api/songs/edit-fixture/sections/split',
                           json={'macro_id': 'mac001', 'at': 12.375})
    assert response.status_code == 200, response.text
    response = client.post('/api/songs/edit-fixture/sections/boundary',
                           json={'right_macro_id': 'mac001', 'at': 18.125})
    assert response.status_code == 200, response.text
    loaded = store.load_analysis('edit-fixture')
    assert loaded.sections[0].start == 0 and loaded.sections[-1].end == 40
    assert loaded.sections[-1].start == 18.125
    assert all(a.end == b.start for a, b in zip(loaded.sections, loaded.sections[1:]))
    assert len({s.macro_id for s in loaded.sections}) == 2
    assert loaded.sections[1].label_source == 'model'
    assert len(loaded.energy.perceived.values) == 40
    response = client.post('/api/songs/edit-fixture/sections/boundary',
                           json={'right_macro_id': 'mac001', 'at': 41})
    assert response.status_code == 400
    assert store.load_analysis('edit-fixture').sections[-1].start == 18.125
    response = client.post('/api/songs/edit-fixture/sections/merge', json={'right_macro_id': 'mac001'})
    assert response.status_code == 200, response.text
    loaded = store.load_analysis('edit-fixture')
    assert len({s.macro_id for s in loaded.sections}) == 1
    assert len(loaded.energy.perceived.values) == 40


def test_merge_rejoins_split_sections_and_keeps_earlier_identity():
    from arrangement.analysis.section_edit import split_macro_section, merge_macro_sections
    song = _song()
    split_macro_section(song, 'mac001', 12.375)
    earlier_id = song.sections[0].macro_id
    merge_macro_sections(song, 'mac001')
    assert {s.macro_id for s in song.sections} == {earlier_id}
    assert song.sections[0].start == 0 and song.sections[-1].end == 40
    assert len(song.sections) == 1
    assert song.sections[0].macro_parts == 1
    assert len(song.energy.perceived.values) == 40
    with pytest.raises(ValueError):
        merge_macro_sections(song, earlier_id)


def test_merge_preserves_original_internal_changes():
    from arrangement.analysis.section_edit import split_macro_section, merge_macro_sections
    song = _song()
    song.sections[0].end = 20
    second = song.sections[0].model_copy(deep=True)
    second.id = 'sec1'
    second.start, second.end = 20, 40
    song.sections.append(second)
    split_macro_section(song, 'mac001', 20)
    merge_macro_sections(song, 'mac001')
    assert [(s.start, s.end) for s in song.sections] == [(0, 20), (20, 40)]
    assert len({s.macro_id for s in song.sections}) == 1


def test_section_history_restores_exact_state_and_rejects_stale_restore(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from arrangement.server import store
    from arrangement.server.app import app

    monkeypatch.setattr(store, 'get_settings', lambda: SimpleNamespace(analyses_dir=tmp_path))
    store.save_analysis(_song())
    client = TestClient(app)
    url = '/api/songs/edit-fixture/sections/state'
    before = client.get(url)
    assert before.status_code == 200
    before = before.json()
    assert client.post('/api/songs/edit-fixture/sections/split', json={'macro_id': 'mac001', 'at': 12.375}).status_code == 200
    after = client.get(url).json()
    assert after['revision'] != before['revision']
    restore = client.put(url, json={'state': before, 'expected_revision': after['revision']})
    assert restore.status_code == 200, restore.text
    assert client.get(url).json() == before
    assert len(store.load_analysis('edit-fixture').energy.perceived.values) == 40
    restore = client.put(url, json={'state': after, 'expected_revision': before['revision']})
    assert restore.status_code == 200, restore.text
    assert client.get(url).json() == after
    stale = client.put(url, json={'state': before, 'expected_revision': before['revision']})
    assert stale.status_code == 409
    assert client.get(url).json() == after
