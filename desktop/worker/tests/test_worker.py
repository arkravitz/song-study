import importlib.util
import json
from pathlib import Path
import pytest

SPEC = importlib.util.spec_from_file_location('song_study_worker', Path(__file__).parents[1] / 'worker.py')

def module():
    worker = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(worker)
    return worker


def seed(tmp_path):
    legacy = tmp_path / 'legacy'; (legacy / 'analyses').mkdir(parents=True)
    original = {'id': 'song', 'title': 'Song', 'duration': 10, 'source_path': 'missing.wav',
                'sections': [{'id':'a','start':0,'end':5,'macro_label':'Verse','macro_id':'a'},
                             {'id':'b','start':5,'end':10,'macro_label':'Chorus','macro_id':'b'}]}
    path = legacy / 'analyses/song.json'; path.write_text(json.dumps(original))
    return legacy, path


def test_edits_do_not_mutate_legacy_and_survive_restart(tmp_path):
    worker = module(); legacy, original = seed(tmp_path)
    before = original.read_bytes()
    store = worker.Library(tmp_path / 'desktop', legacy, tmp_path)
    rows = store.library(); assert rows[0]['title'] == 'Song'
    sections = store.sections('song'); sections[0]['name'] = 'Opening'
    store.save_sections('song', sections)
    assert original.read_bytes() == before
    reopened = worker.Library(tmp_path / 'desktop', legacy, tmp_path)
    assert reopened.sections('song')[0]['name'] == 'Opening'
    assert reopened.history('song', -1)[0]['name'] == 'Verse'
    assert reopened.history('song', 1)[0]['name'] == 'Opening'


def test_rejects_gaps_overlaps_and_out_of_range_edits(tmp_path):
    worker = module(); legacy, _ = seed(tmp_path)
    store = worker.Library(tmp_path / 'desktop', legacy, tmp_path); store.library()
    sections = store.sections('song'); sections[1]['startFrame'] += 1
    with pytest.raises(ValueError): store.save_sections('song', sections)
    sections = store.sections('song'); sections[-1]['endFrame'] += 44100
    with pytest.raises(ValueError): store.save_sections('song', sections)


def test_new_edit_after_undo_discards_redo(tmp_path):
    worker = module(); legacy, _ = seed(tmp_path)
    store = worker.Library(tmp_path / 'desktop', legacy, tmp_path); store.library()
    sections = store.sections('song'); sections[0]['name'] = 'A'; store.save_sections('song', sections)
    store.history('song', -1)
    sections = store.sections('song'); sections[0]['name'] = 'B'; store.save_sections('song', sections)
    assert store.history('song', 1)[0]['name'] == 'B'


def test_waveform_uses_binary_artifact_and_same_timebase_as_audio(tmp_path):
    import numpy as np
    import soundfile as sf
    worker = module(); legacy, _ = seed(tmp_path)
    store = worker.Library(tmp_path / 'desktop', legacy, tmp_path)
    samples = np.zeros((22050,2), dtype=np.float32); samples[11025] = .9
    path = tmp_path / 'audio with spaces.wav'; sf.write(path,samples,22050,subtype='FLOAT')
    media = store.media(str(path))
    assert isinstance(media['waveform'],str), 'Large waveform data belongs in an artifact, not the message pipe.'
    values = np.fromfile(media['waveform'],dtype='<f4')
    assert abs(np.argmax(values)/media['waveformHz'] - .5) < .004
    assert sf.info(media['audioPath']).samplerate == 44100


def test_prepare_allows_playback_without_running_analysis(tmp_path):
    import numpy as np
    import soundfile as sf
    worker = module(); legacy, _ = seed(tmp_path)
    store = worker.Library(tmp_path / 'desktop', legacy, tmp_path)
    path = tmp_path / 'A new song.wav'; sf.write(path,np.zeros((44100,2)),44100)
    result = store.prepare(str(path))
    assert not result['existing']
    loaded = store.load(result['song'])
    assert Path(loaded['audioPath']).exists()
    assert loaded['duration'] == 1
    assert loaded['tracks'] == []
    assert loaded['sections'][0]['name'] == 'Song'
    assert store.prepare(str(path))['song'] == result['song']


def test_analysis_completion_preserves_human_corrections(tmp_path):
    worker = module(); legacy, _ = seed(tmp_path)
    store = worker.Library(tmp_path / 'desktop', legacy, tmp_path); store.library()
    sections = store.sections('song'); sections[0]['name']='My opening'; store.save_sections('song',sections)
    new_analysis=store.analysis('song'); new_analysis['sections'][0]['macro_label']='New model guess'
    store.complete_analysis('song',new_analysis,'new-analysis.json')
    assert store.sections('song')[0]['name']=='My opening'
    assert store.history('song',-1)[0]['name']=='Verse'


def test_imported_audio_remains_usable_after_original_disappears(tmp_path):
    import numpy as np
    import soundfile as sf
    worker=module();legacy,_=seed(tmp_path)
    store=worker.Library(tmp_path/'desktop',legacy,tmp_path)
    source=tmp_path/'original.wav';sf.write(source,np.zeros((44100,2)),44100)
    prepared=store.prepare(str(source));song=prepared['song']
    first=store.load(song)
    Path(store.analysis(song)['source_path']).unlink()
    reopened=worker.Library(tmp_path/'desktop',legacy,tmp_path)
    assert reopened.load(song)['audioPath']==first['audioPath']


def test_load_converts_downbeat_indices_and_preserves_energy_attribution(tmp_path):
    worker = module(); legacy, path = seed(tmp_path)
    a = json.loads(path.read_text())
    a['beats'] = {'bpm':120, 'beats':[.25,.75,1.25,1.75,2.25], 'downbeats':[0,4,999]}
    a['energy_changes'] = [{'t':2.25,'direction':'rise','delta':.2,'contributors':[['bass_activity',.1]]}]
    path.write_text(json.dumps(a))
    store = worker.Library(tmp_path/'desktop',legacy,tmp_path)
    store.library()
    store.asset = lambda *args: {'audioPath':'test.wav','waveform':'test.f32','waveformHz':400,'waveformScale':1}
    loaded = store.load('song')
    assert loaded['downbeats'] == [.25,2.25]
    assert loaded['energyChanges'][0]['contributors'] == [{'key':'bass_activity','delta':.1}]
