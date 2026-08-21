import numpy as np
from arrangement.dsp.activity import ActivityConfig


def test_sparse_loud_phrase_does_not_turn_quiet_residue_into_a_long_part():
    from arrangement.dsp.activity import detect_activity
    sr = 8000
    t = np.arange(sr * 40) / sr
    # Like Taste's piano stem: fluctuating residue and one brief loud passage.
    db = -78 + 10 * np.sin(t * 2 * np.pi / 4)
    db[(t > 35) & (t < 36)] = -30
    x = 10 ** (db / 20) * np.sin(2 * np.pi * 220 * t)
    result = detect_activity(x, sr, ActivityConfig.for_family('harmony', 'piano'))
    assert not any(s.start <= 20 < s.end for s in result.spans)
    assert any(s.start <= 35.5 < s.end for s in result.spans)
    assert sum(s.end - s.start for s in result.spans) < 2


def test_quiet_playing_is_kept_relative_to_the_stems_own_peak():
    from arrangement.dsp.activity import detect_activity
    sr = 8000
    t = np.arange(sr * 20) / sr
    db = np.full(len(t), -90.)
    db[(t > 2) & (t < 6)] = -50
    db[(t > 10) & (t < 14)] = -30
    x = 10 ** (db / 20) * np.sin(2 * np.pi * 220 * t)
    result = detect_activity(x, sr, ActivityConfig.for_family('harmony', 'piano'))
    assert any(s.start <= 4 < s.end for s in result.spans)
    assert any(s.start <= 12 < s.end for s in result.spans)
    assert not any(s.start <= 8 < s.end for s in result.spans)


def test_refresh_keeps_listener_section_edits_and_updates_instrument_presence():
    from arrangement.analysis.activity_refresh import refresh_source_activity
    from arrangement.dsp.activity import ActivityResult, ActivitySpan
    from arrangement.schema import SongAnalysis, Source, Section, ActivitySegment, EnergyModel, Curve
    song = SongAnalysis(id='refresh', title='Fixture', duration=20,
        sources=[Source(id='piano', label='Piano', segments=[ActivitySegment(start=0,end=20)],mean_pan=0.,mean_width=.4)],
        sections=[Section(id='custom',start=0,end=20,macro_id='listener',macro_label='My verse',label_source='user',source_ids=['piano'])],
        energy=EnergyModel(frame_hz=10,raw=Curve(name='raw',frame_hz=10,values=[.4]*200),
            perceived=Curve(name='perceived',frame_hz=10,values=[.5]*200),
            components={'active_layers':Curve(name='active_layers',frame_hz=10,values=[0.]*200),
                        'loudness':Curve(name='loudness',frame_hz=10,values=[.2]*200)},
            weights={'active_layers':.17,'loudness':.83}))
    activity = ActivityResult(spans=[ActivitySpan(start=10,end=12,level_db=-30,peak_db=-20,attack_db_s=10,release_db_s=-10)],
        level_db=np.zeros(200),smoothed_db=np.zeros(200),threshold_db=-50,noise_floor_db=-80,peak_level_db=-20,
        frame_hz=10,times=np.arange(200)/10,otsu_contrast=.5)
    refresh_source_activity(song,'piano',activity)
    assert song.sources[0].total_active_seconds == 2
    assert song.sources[0].duty_cycle == .1
    assert song.sections[0].source_ids == []
    assert (song.sections[0].id,song.sections[0].start,song.sections[0].end,song.sections[0].macro_label,song.sections[0].label_source) == ('custom',0,20,'My verse','user')
    assert song.energy.raw.values == [.4]*200
    assert song.energy.perceived.values[105] > song.energy.perceived.values[50]
    assert any(e.source_ids == ['piano'] and e.t == 10 for e in song.events)
