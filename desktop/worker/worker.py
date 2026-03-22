"""Private desktop worker. Legacy analysis is read-only; corrections live in SQLite."""
from __future__ import annotations
import argparse
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import traceback
import uuid

RATE = 44100


class Library:
    def __init__(self, directory: Path, legacy: Path | None, root: Path):
        self.directory = directory
        self.legacy = legacy
        self.root = root
        directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(directory / 'library.sqlite')
        self.db.execute('CREATE TABLE IF NOT EXISTS songs (id TEXT PRIMARY KEY, analysis TEXT NOT NULL, origin TEXT, cursor INTEGER NOT NULL DEFAULT 0)')
        self.db.execute('CREATE TABLE IF NOT EXISTS edits (song TEXT NOT NULL, revision INTEGER NOT NULL, sections TEXT NOT NULL, PRIMARY KEY(song,revision))')
        self.db.execute('CREATE TABLE IF NOT EXISTS assets (song TEXT NOT NULL, track TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(song,track))')
        self.db.commit()

    def register(self, data: dict, origin: str):
        if self.db.execute('SELECT 1 FROM songs WHERE id=?', (data['id'],)).fetchone():
            return
        with self.db:
            self.db.execute('INSERT INTO songs(id,analysis,origin) VALUES(?,?,?)',
                            (data['id'], json.dumps(data), origin))
            self.db.execute('INSERT INTO edits VALUES(?,?,?)',
                            (data['id'], 0, json.dumps(self.initial_sections(data))))

    def library(self):
        if self.legacy:
            for p in sorted((self.legacy / 'analyses').glob('*.json')):
                try:
                    self.register(json.loads(p.read_text()), str(p))
                except (ValueError, KeyError, sqlite3.Error):
                    continue
        rows = []
        for row in self.db.execute('SELECT id,analysis FROM songs'):
            a = json.loads(row[1])
            rows.append({'id': row[0], 'title': a.get('title') or 'Untitled song',
                         'artist': a.get('artist') or '', 'duration': a.get('duration', 0)})
        return sorted(rows, key=lambda r: (r['title'].casefold(), r['id']))

    @staticmethod
    def initial_sections(data):
        groups = {}
        for s in sorted(data.get('sections') or [], key=lambda s:s['start']):
            key = s.get('macro_id') or s['id']
            if key not in groups:
                name = s.get('macro_label') or s.get('label') or 'Section'
                if name == 'unknown': name = 'Section'
                groups[key] = {'id': key, 'name': name[:1].upper() + name[1:],
                               'kind': s.get('label') or 'unknown',
                               'repeatGroup': s.get('repeat_group'),
                               'startFrame': round(s['start'] * RATE),
                               'endFrame': round(s['end'] * RATE)}
            else:
                groups[key]['endFrame'] = round(s['end'] * RATE)
        sections = list(groups.values())
        duration = round(data['duration'] * RATE)
        if not sections:
            sections = [{'id': uuid.uuid4().hex, 'name': 'Song', 'kind': 'unknown',
                         'repeatGroup': None, 'startFrame': 0, 'endFrame': duration}]
        sections[0]['startFrame'] = 0
        for left, right in zip(sections, sections[1:]):
            left['endFrame'] = right['startFrame']
        sections[-1]['endFrame'] = duration
        return sections

    def analysis(self, song):
        row = self.db.execute('SELECT analysis FROM songs WHERE id=?', (song,)).fetchone()
        if not row: raise ValueError('This song is no longer in the library.')
        return json.loads(row[0])

    def sections(self, song):
        row = self.db.execute('SELECT sections FROM edits JOIN songs ON songs.id=edits.song AND songs.cursor=edits.revision WHERE songs.id=?', (song,)).fetchone()
        if not row: raise ValueError('This song is no longer in the library.')
        return json.loads(row[0])

    def status(self, song):
        cursor = self.db.execute('SELECT cursor FROM songs WHERE id=?', (song,)).fetchone()[0]
        maximum = self.db.execute('SELECT MAX(revision) FROM edits WHERE song=?', (song,)).fetchone()[0]
        return {'canUndo': cursor > 0, 'canRedo': cursor < maximum}

    def save_sections(self, song, sections):
        end = round(self.analysis(song)['duration'] * RATE)
        if not sections or len({s['id'] for s in sections}) != len(sections):
            raise ValueError('Sections must have unique identities.')
        edge = 0
        for s in sections:
            if (type(s['startFrame']) is not int or type(s['endFrame']) is not int
                or s['startFrame'] != edge or s['endFrame'] - edge < 4410
                or not 1 <= len(s['name'].strip()) <= 80):
                raise ValueError('Sections must be consecutive, named, and at least a tenth of a second long.')
            edge = s['endFrame']
        if edge != end: raise ValueError('Sections must cover exactly the song duration.')
        if self.sections(song) == sections: return sections
        cursor = self.db.execute('SELECT cursor FROM songs WHERE id=?', (song,)).fetchone()[0]
        with self.db:
            self.db.execute('DELETE FROM edits WHERE song=? AND revision>?', (song, cursor))
            self.db.execute('INSERT INTO edits VALUES(?,?,?)', (song, cursor+1, json.dumps(sections)))
            self.db.execute('UPDATE songs SET cursor=? WHERE id=?', (cursor+1, song))
        return sections

    def history(self, song, direction):
        cursor = self.db.execute('SELECT cursor FROM songs WHERE id=?', (song,)).fetchone()[0]
        dest = max(0, cursor + (1 if direction > 0 else -1))
        if self.db.execute('SELECT 1 FROM edits WHERE song=? AND revision=?', (song, dest)).fetchone():
            with self.db: self.db.execute('UPDATE songs SET cursor=? WHERE id=?', (dest,song))
        return self.sections(song)

    def resolve(self, path):
        p = Path(path)
        if not p.is_absolute(): p = self.root / p
        if not p.exists(): raise ValueError(f'The audio file could not be found: {p.name}. Restore it to its original location.')
        return p

    def media(self, path):
        import numpy as np
        import soundfile as sf
        p = self.resolve(path)
        stat = p.stat()
        key = hashlib.sha256(f'{p.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:44100:v1'.encode()).hexdigest()
        cache = self.directory / 'media'; cache.mkdir(exist_ok=True)
        wav = cache / f'{key}.wav'; peaks = cache / f'{key}.npz'
        if not wav.exists():
            temp = wav.with_suffix('.partial.wav')
            try:
                subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-i',str(p),'-map','0:a:0',
                                '-ac','2','-ar',str(RATE),'-c:a','pcm_f32le',str(temp)],
                               check=True, capture_output=True, timeout=3600)
                temp.replace(wav)
            finally:
                temp.unlink(missing_ok=True)
        if not peaks.exists():
            data, sr = sf.read(wav, dtype='float32', always_2d=True)
            # Max across channels avoids phase cancellation in a display envelope.
            mono = np.max(np.abs(data), axis=1)
            hop = 110
            pad = np.pad(mono, (0,(-len(mono)) % hop)).reshape(-1,hop)
            values = np.sqrt(np.mean(pad ** 2, axis=1))
            scale = max(.02, float(np.percentile(values,99.5)))
            np.savez_compressed(peaks, values=values, rate=sr/hop, scale=scale)
        with np.load(peaks) as z:
            waveform = peaks.with_suffix('.f32')
            if not waveform.exists():
                z['values'].astype('<f4').tofile(waveform)
            return {'audioPath':str(wav), 'waveform':str(waveform),
                    'waveformHz':float(z['rate']), 'waveformScale':float(z['scale'])}

    def asset(self, song, track, path=None):
        row=self.db.execute('SELECT payload FROM assets WHERE song=? AND track=?',(song,track)).fetchone()
        if row:
            payload=json.loads(row[0])
            if Path(payload['audioPath']).exists() and Path(payload['waveform']).exists():return payload
        if path is None:return None
        payload=self.media(path)
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO assets VALUES(?,?,?)',(song,track,json.dumps(payload)))
        return payload

    def load(self, song, report=lambda *args:None):
        import numpy as np
        a = self.analysis(song)
        report('Opening song', .1)
        media = self.asset(song,'mix',a['source_path'])
        origin = Path(self.db.execute('SELECT origin FROM songs WHERE id=?',(song,)).fetchone()[0])
        base = origin.parent.parent
        tracks = []
        order = {'vocals':0,'drums':1,'bass':2,'guitar':3,'piano':4,'other':5}
        for src in sorted(a.get('sources') or [], key=lambda s: order.get(s.get('stem_key') or s['id'],10)):
            if src.get('origin') not in (None,'stem'): continue
            stem = src.get('stem_key') or src['id']
            model = src.get('separator_model')
            choices = sorted((base / 'stems' / song).glob(f'*/{stem}.wav'))
            exact = base / 'stems' / song / str(model) / f'{stem}.wav'
            path = exact if exact.exists() else (choices[0] if choices else None)
            if not path and not self.asset(song,src['id']): continue
            report('Preparing separated parts', .15 + .7 * len(tracks)/max(1,len(a.get('sources',[]))))
            track_media = self.asset(song,src['id'],str(path) if path else None)
            tracks.append({'id':src['id'], 'name':'Other parts' if stem=='other' else src.get('label',stem).capitalize(),
                           'color':src.get('color','#85929e'), 'quiet':bool(src.get('is_silent')) or src.get('duty_cycle',0)<.04,
                           'segments':[{'start':s['start'],'end':s['end']} for s in src.get('segments',[])], **track_media})
        values = (a.get('energy',{}).get('perceived') or {}).get('values') or []
        hz = (a.get('energy',{}).get('perceived') or {}).get('frame_hz') or 20
        curve_file = origin.with_suffix('.npz')
        if curve_file.exists():
            with np.load(curve_file) as z:
                if 'energy/perceived' in z: values=z['energy/perceived'].astype(float).tolist()
        beats = a.get('beats',{})
        return {'id':song, 'title':a.get('title') or 'Untitled song', 'artist':a.get('artist') or '',
                'duration':a['duration'], 'sampleRate':RATE, 'sections':self.sections(song),
                'tracks':tracks, 'energy':values, 'energyHz':hz,
                'bpm':beats.get('bpm') or 0, 'beats':beats.get('beats') or [],
                'downbeats':[beats['beats'][i] for i in beats.get('downbeats',[]) if isinstance(i,int) and 0<=i<len(beats.get('beats',[]))],
                'energyChanges':[{'t':c['t'],'direction':c['direction'],'delta':c.get('delta',0),
                                  'contributors':[{'key':k,'delta':v} for k,v in c.get('contributors',[])]}
                                 for c in a.get('energy_changes',[])],
                'warnings':a.get('warnings') or [], **media, **self.status(song)}

    def prepare(self, path):
        import soundfile as sf
        p = self.resolve(path)
        self.library()
        for row in self.db.execute('SELECT id,analysis FROM songs'):
            data=json.loads(row[1])
            try:
                if self.resolve(data['source_path']).resolve()==p.resolve():
                    return {'song':row[0], 'existing':not data.get('_desktop_pending',False)}
            except (ValueError,KeyError): pass
        h=hashlib.sha256()
        with p.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''): h.update(chunk)
        sid=h.hexdigest()[:16]
        if self.db.execute('SELECT 1 FROM songs WHERE id=?',(sid,)).fetchone():
            return {'song':sid,'existing':not self.analysis(sid).get('_desktop_pending',False)}
        upload=self.directory/'analysis'/'uploads';upload.mkdir(parents=True,exist_ok=True)
        destination=upload/f'{sid}_{p.name}'
        shutil.copy2(p,destination)
        media=self.media(str(destination));duration=sf.info(media['audioPath']).duration
        if duration < .1: raise ValueError('Choose a song longer than a tenth of a second.')
        data={'id':sid,'title':p.stem,'artist':'','duration':duration,'source_path':str(destination),
              'sections':[], 'sources':[], 'energy':{}, 'beats':{}, '_desktop_pending':True}
        origin=self.directory/'analysis'/'analyses'/f'{sid}.json';origin.parent.mkdir(parents=True,exist_ok=True)
        origin.write_text(json.dumps(data))
        self.register(data,str(origin))
        return {'song':sid,'existing':False}

    def complete_analysis(self, song, data, origin):
        with self.db:
            cursor=self.db.execute('SELECT cursor FROM songs WHERE id=?',(song,)).fetchone()[0]
            self.db.execute('UPDATE songs SET analysis=?,origin=? WHERE id=?',(json.dumps(data),str(origin),song))
            if cursor==0:
                self.db.execute('UPDATE edits SET sections=? WHERE song=? AND revision=0',
                                (json.dumps(self.initial_sections(data)),song))

    def import_song(self, path, report):
        prepared=self.prepare(path);sid=prepared['song']
        if prepared['existing']:return sid
        source=self.analysis(sid)
        from arrangement.analysis.pipeline import run_analysis
        from arrangement.server.store import save_analysis
        a=run_analysis(source['source_path'],song_id=sid,progress=lambda stage,frac,msg:report(msg or stage,frac))
        a.llm_enabled=False
        if not a.title or a.title==Path(source['source_path']).stem:a.title=source['title']
        output=save_analysis(a)
        self.complete_analysis(sid,json.loads(output.read_text()),output)
        return sid


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--store',type=Path,required=True)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--legacy',type=Path)
    args=parser.parse_args()
    try: os.setsid()
    except OSError: pass
    os.environ['ARRANGEMENT_DATA_DIR']=str(args.store / 'analysis')
    os.environ['DEEPSEEK_API_KEY']=''
    os.environ['ARRANGEMENT_SEPARATOR']='htdemucs_6s'
    os.environ['ARRANGEMENT_ENABLE_LLM']='0'
    bundled_models=Path(__file__).resolve().parent.parent / 'models' / 'allinone_mlx'
    destination=args.store / 'analysis' / 'models' / 'allinone_mlx'
    if bundled_models.exists() and not destination.exists():
        shutil.copytree(bundled_models,destination)
    protocol=sys.stdout
    store=Library(args.store,args.legacy,args.root)
    def send(value):
        protocol.write(json.dumps(value,ensure_ascii=False,allow_nan=False)+'\n'); protocol.flush()
    for line in sys.stdin:
        req={}
        try:
            req=json.loads(line); method=req['method']; params=req.get('params',{}); rid=req['id']
            def report(message,fraction): send({'id':rid,'progress':message,'fraction':fraction})
            with contextlib.redirect_stdout(sys.stderr):
                if method=='library': result=store.library()
                elif method=='load': result=store.load(params['song'],report)
                elif method=='save':
                    result={'sections':store.save_sections(params['song'],params['sections']),**store.status(params['song'])}
                elif method in ('undo','redo'):
                    result={'sections':store.history(params['song'],-1 if method=='undo' else 1),**store.status(params['song'])}
                elif method=='prepare': result=store.prepare(params['path'])
                elif method=='import': result={'song':store.import_song(params['path'],report)}
                else: raise ValueError('Unknown desktop request.')
            send({'id':rid,'result':result})
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            send({'id':req.get('id',''),'error':str(exc)})


if __name__=='__main__': main()
