"""Energy migrations must not erase human edits or unrelated measurements."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import numpy as np
import pytest
from arrangement.analysis.energy_refresh import refresh
from arrangement.dsp.energy import DEFAULT_WEIGHTS, HISTORICAL_WEIGHTS


def fixture():
    curves = {name:np.sin(np.arange(200)/13+i) for i,name in enumerate(HISTORICAL_WEIGHTS)}
    arrays = {'energy/'+k:v for k,v in curves.items()}
    arrays.update({'energy/raw':np.zeros(200),'energy/perceived':np.zeros(200),
                   'spatial/width':np.arange(20), 'activity/bass':np.array([[2,8,.9]])})
    analysis = {'duration':10,'title':'Custom title','energy':{'frame_hz':20,
        'components':{k:{'values':v.tolist(),'description':k} for k,v in curves.items()},
        'raw':{'values':[0]*200},'perceived':{'values':[0]*200}},
        'sections':[{'id':'a','start':0,'end':5,'label':'My verse'},
                    {'id':'b','start':5,'end':10,'label':'My chorus','deltas_from_previous':['bass enters','energy rises 90 points']}],
        'beats':{'beats':[0,1,2,3,4,5,6,7,8,9],'downbeats':[0,4,8]},
        'loudness':{'lra_curve':[-20]*10},'fingerprint':{'custom':True},
        'user_metadata':{'do not touch':1}}
    return analysis, arrays


def test_refresh_changes_energy_preserves_everything_else():
    analysis,arrays=fixture();before=copy.deepcopy(analysis)
    new,output,variants=refresh(analysis,arrays,np.arange(10,dtype=float)-25,1)
    assert analysis==before
    for old,updated in zip(analysis['sections'],new['sections']):
        assert {k:updated[k] for k in ('id','start','end','label')}=={k:old[k] for k in ('id','start','end','label')}
    assert new['user_metadata']==before['user_metadata']
    assert new['beats']==before['beats']
    assert 'bass enters' in new['sections'][1]['deltas_from_previous']
    assert 'energy rises 90 points' not in new['sections'][1]['deltas_from_previous']
    assert new['fingerprint']['custom'] is True
    for name in arrays:
        if not name.startswith('energy/') or name not in ('energy/raw','energy/perceived','energy/loudness'):
            np.testing.assert_array_equal(output[name], arrays[name])
    for energy in variants.values():
        assert sum(c.weight for c in energy.components)==pytest.approx(1)
        assert np.isfinite(energy.perceived).all()
        assert np.min(energy.perceived)>=0 and np.max(energy.perceived)<=1
    np.testing.assert_allclose(new['energy']['perceived']['values'],output['energy/perceived'],atol=1e-7)


def cli():
    path=Path(__file__).resolve().parents[2]/'desktop/tools/recompute_energy.py'
    spec=importlib.util.spec_from_file_location('refresh_cli',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def staged(tmp_path):
    tool=cli(); db_path=tmp_path/'library.sqlite'; original=tmp_path/'song.json'; original.write_text('original')
    backup=tmp_path/'backup';backup.write_text('original');after=tmp_path/'after';after.write_text('refreshed')
    snapshot=tmp_path/'analysis.json';snapshot.write_text('{"energy":"new"}')
    with sqlite3.connect(db_path) as db:
        db.executescript('CREATE TABLE songs(id TEXT,analysis TEXT,cursor INT); CREATE TABLE edits(song TEXT,revision INT,sections TEXT); CREATE TABLE assets(song TEXT,track TEXT,payload BLOB);')
        db.execute('INSERT INTO songs VALUES(?,?,?)',('song','{"energy":"old"}',2))
        db.executemany('INSERT INTO edits VALUES(?,?,?)',[('song',1,'My verse'),('song',2,'My chorus'),('song',3,'Redo')])
        db.execute('INSERT INTO assets VALUES(?,?,?)',('song','bass',b'audio'))
    manifest={'database':str(db_path),'songs':[{'id':'song'}], 'files':[{'path':str(original),'hash':tool.digest(original),'staged':str(after),'backup':str(backup)}],
              'rows':[{'id':'song','hash':hashlib.sha256(b'{"energy":"old"}').hexdigest(),'staged':str(snapshot)}]}
    tool.write(tmp_path/'manifest.json',manifest)
    return tool, db_path,original


def test_apply_preserves_native_edits_redo_and_assets(tmp_path):
    tool,db_path,original=staged(tmp_path)
    with sqlite3.connect(db_path) as db:
        before=[list(db.execute(sql)) for sql in ['SELECT * FROM edits','SELECT id,cursor FROM songs','SELECT * FROM assets']]
    tool.apply(tmp_path)
    assert original.read_text()=='refreshed'
    with sqlite3.connect(db_path) as db:
        after=[list(db.execute(sql)) for sql in ['SELECT * FROM edits','SELECT id,cursor FROM songs','SELECT * FROM assets']]
        assert db.execute('SELECT analysis FROM songs').fetchone()[0]=='{"energy":"new"}'
    assert before==after


def test_apply_rolls_back_on_file_failure(tmp_path,monkeypatch):
    tool,db_path,original=staged(tmp_path)
    def fail(*args): raise OSError('disk error')
    monkeypatch.setattr(tool,'atomic_copy',fail)
    with pytest.raises(OSError):tool.apply(tmp_path)
    assert original.read_text()=='original'
    with sqlite3.connect(db_path) as db: assert db.execute('SELECT analysis FROM songs').fetchone()[0]=='{"energy":"old"}'


def test_apply_refuses_stale_staged_data(tmp_path):
    tool,db_path,original=staged(tmp_path);original.write_text('Changed by user')
    with pytest.raises(ValueError,match='changed since staging'):tool.apply(tmp_path)
    assert original.read_text()=='Changed by user'


def test_subframe_sections_get_finite_sample_without_moving_boundaries():
    analysis,arrays=fixture()
    analysis['sections'].insert(1,{'id':'tiny','start':5.001,'end':5.002,'label':'Tiny'})
    updated,_,_=refresh(analysis,arrays,np.arange(10,dtype=float)-25,1)
    assert np.isfinite(updated['sections'][1]['energy_perceived'])
    assert updated['sections'][1]['start']==5.001


def test_boundary_events_have_finite_deltas():
    from arrangement.dsp.energy import EnergyResult, find_energy_changes
    e=EnergyResult(20,10,np.zeros(200),np.r_[np.ones(5),np.zeros(190),np.ones(5)])
    assert all(np.isfinite(d) for _,d,_ in find_energy_changes(e,min_delta=.001))


def test_refresh_applies_exact_balanced_candidate_and_matches_new_analysis():
    from arrangement.dsp.energy import EnergyComponent, combine_components
    analysis,arrays=fixture()
    updated,output,variants=refresh(analysis,arrays,np.arange(10,dtype=float)-25,1)
    expected=combine_components(20,10,[EnergyComponent(k,output['energy/'+k].astype(float),v)
        for k,v in DEFAULT_WEIGHTS.items()],np.zeros(200))
    np.testing.assert_allclose(output['energy/perceived'],variants['Balanced'].perceived,atol=1e-7)
    np.testing.assert_allclose(output['energy/perceived'],expected.perceived,atol=1e-7)
    assert updated['energy']['weights']==pytest.approx(DEFAULT_WEIGHTS)
    assert set(updated['energy']['weights'])=={'loudness','onset_density','percussive_energy','active_layers','bass_activity','band_spread'}
    assert not np.allclose(variants['Balanced'].perceived,variants['Current corrected'].perceived)
