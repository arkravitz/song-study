#!/usr/bin/env python3
"""Stage, back up, and apply energy-only refreshes; serve listening comparisons."""
from __future__ import annotations
import argparse, hashlib, json, mimetypes, os, re, shutil, sqlite3, sys
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from arrangement.audio.io import decode
from arrangement.analysis.energy_refresh import refresh, EXPERIMENTS
from arrangement.dsp.loudness import lufs_1s
from arrangement.dsp.energy import find_energy_changes

DEFAULT_DB = Path.home() / 'Library/Application Support/Song Study/library.sqlite'

def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def encoded(data): return json.dumps(data, ensure_ascii=False, allow_nan=False)
def json_hash(data): return hashlib.sha256(encoded(data).encode()).hexdigest()
def write(path, data): Path(path).write_text(encoded(data))
def atomic_copy(source, target):
    target = Path(target)
    temp = target.with_name(target.name + '.energy-refresh.tmp')
    shutil.copy2(source, temp)
    os.replace(temp, target)

def section_mean(energy, start, end):
    value = energy.window(start, end)
    return value if np.isfinite(value) else energy.at((start+end)/2)

def internal_end(energy):
    values=energy.perceived
    threshold=np.median(values)-max(.025,(np.percentile(values,95)-np.percentile(values,5))*.6)
    tail=len(values)
    floor=max(0,len(values)-round(min(20,energy.duration*.15)*energy.frame_hz))
    while tail>floor and values[tail-1]<threshold:
        tail-=1
    # Exclude the transition into a terminal fade, not only its quiet floor.
    tail_time=tail/energy.frame_hz-2 if tail<len(values) else energy.duration
    return min(energy.duration-8,tail_time)

def prepare(out, db_path):
    out.mkdir(parents=True, exist_ok=False)
    (out / 'before').mkdir(); (out / 'after').mkdir()
    files = {p.resolve() for p in (ROOT / 'data/analyses').glob('*.json')}
    rows, edits = [], {}
    if db_path.exists():
        with sqlite3.connect(f'file:{db_path}?mode=ro', uri=True) as db:
            with sqlite3.connect(out / 'before/library.sqlite') as backup: db.backup(backup)
            rows = list(db.execute('SELECT id,analysis,origin FROM songs'))
            for song, sections in db.execute('SELECT songs.id,edits.sections FROM songs JOIN edits ON edits.song=songs.id AND edits.revision=songs.cursor'):
                edits[song] = json.loads(sections)
        files.update(Path(origin).resolve() for _,_,origin in rows if origin and Path(origin).exists())
    manifest = {'equation': 'Balanced', 'database': str(db_path), 'files': [], 'rows': [], 'songs': []}
    readings = {}
    reports = []
    for number, path in enumerate(sorted(files)):
        analysis = json.loads(path.read_text())
        print(f'Recomputing {analysis.get("title") or analysis["id"]}', flush=True)
        npz = path.with_suffix('.npz')
        with np.load(npz) as z: arrays = {k: z[k] for k in z.files}
        source = Path(analysis['source_path']); source = source if source.is_absolute() else ROOT / source
        audio = decode(source)
        loudness, hz = lufs_1s(audio.data, audio.sample_rate)
        del audio
        readings[analysis['id']] = (loudness, hz, arrays)
        updated, output, variants = refresh(analysis, arrays, loudness, hz)
        for original, data in [(path, updated), (npz, output)]:
            name = f'{number}-{original.name}'
            shutil.copy2(original, out / 'before' / name)
            staged = out / 'after' / name
            if original.suffix == '.json': write(staged, data)
            else: np.savez_compressed(staged, **data)
            manifest['files'].append({'path':str(original), 'hash':digest(original), 'staged':str(staged.resolve()), 'backup':str((out/'before'/name).resolve())})
        old = arrays['energy/perceived']
        sections = edits.get(analysis['id']) or [{'name':s.get('macro_label') or s['label'], 'startFrame':round(s['start']*44100), 'endFrame':round(s['end']*44100)} for s in analysis['sections']]
        stride = max(1, round(analysis['energy']['frame_hz']/5))
        series = {name:np.round(e.perceived[::stride],4).tolist() for name,e in variants.items()}
        series['Previous saved'] = np.round(old[::stride],4).tolist()
        scores = {}
        for name,e in variants.items():
            unscaled = sum(c.curve*c.weight for c in e.components)
            scores[name] = [{'name':s['name'], 'start':s['startFrame']/44100, 'end':s['endFrame']/44100,
                            'mean':round(section_mean(e,s['startFrame']/44100,s['endFrame']/44100),3),
                            'unscaled':float(np.mean(unscaled[round(s['startFrame']/44100*e.frame_hz):max(round(s['startFrame']/44100*e.frame_hz)+1,round(s['endFrame']/44100*e.frame_hz))]))} for s in sections]
        report = {'id':analysis['id'], 'title':re.sub(r'^\d{13}_','',analysis.get('title') or analysis['id']).replace('_',' '), 'duration':analysis['duration'],
                  'change_label':'Balanced update', 'hz':analysis['energy']['frame_hz']/stride, 'audio':str(source.resolve()), 'sections':scores['Current corrected'],
                  'series':series, 'scores':scores, 'mean_change':float(np.mean(np.abs(variants['Balanced'].perceived-old))),
                  'max_change':float(np.max(np.abs(variants['Balanced'].perceived-old))),
                  'events':{name:[{'t':c[0],'delta':c[1]} for c in find_energy_changes(e)
                           if 4<c[0]<internal_end(e)] for name,e in variants.items()}}
        reports.append(report)
        manifest['songs'].append({'id':analysis['id'],'title':report['title'],'mean_change':report['mean_change'],'max_change':report['max_change']})
    for number,(song,payload,origin) in enumerate(rows):
        analysis=json.loads(payload)
        if song not in readings:
            raise ValueError(f'Desktop-only song {song} needs an accessible origin/curve artifact.')
        loudness,hz,arrays=readings[song]
        updated,_,_=refresh(analysis,arrays,loudness,hz)
        staged=out/'after'/f'desktop-{number}.json';write(staged,updated)
        write(out/'before'/f'desktop-{number}.json',analysis)
        manifest['rows'].append({'id':song,'hash':hashlib.sha256(payload.encode()).hexdigest(),'staged':str(staged.resolve())})
    write(out/'manifest.json',manifest)
    write(out/'comparison.json',reports)
    make_report(out,reports)
    print(f'Staged {len(reports)} songs and {len(rows)} desktop records. Backups: {out / "before"}',flush=True)

def apply(out):
    manifest=json.loads((out/'manifest.json').read_text())
    for item in manifest['files']:
        if digest(item['path']) != item['hash']: raise ValueError(f'Analysis changed since staging: {item["path"]}')
    db_path=Path(manifest['database'])
    db=sqlite3.connect(db_path) if manifest['rows'] else None
    before_edits=None
    changed_files=[]
    try:
        if db:
            db.execute('BEGIN IMMEDIATE')
            before_edits=(list(db.execute('SELECT * FROM edits ORDER BY song,revision')),list(db.execute('SELECT id,cursor FROM songs ORDER BY id')))
            for row in manifest['rows']:
                payload=db.execute('SELECT analysis FROM songs WHERE id=?',(row['id'],)).fetchone()[0]
                if hashlib.sha256(payload.encode()).hexdigest()!=row['hash']: raise ValueError(f'Desktop analysis changed: {row["id"]}')
            for row in manifest['rows']: db.execute('UPDATE songs SET analysis=? WHERE id=?',(Path(row['staged']).read_text(),row['id']))
            after_edits=(list(db.execute('SELECT * FROM edits ORDER BY song,revision')),list(db.execute('SELECT id,cursor FROM songs ORDER BY id')))
            assert before_edits==after_edits,'Section history changed'
        for item in manifest['files']:
            atomic_copy(item['staged'],item['path'])
            changed_files.append(item)
        if db: db.commit()
    except BaseException:
        if db: db.rollback()
        for item in changed_files: atomic_copy(item['backup'],item['path'])
        raise
    finally:
        if db: db.close()
    write(out/'applied.json',{'songs':len(manifest['songs']),'desktop_records':len(manifest['rows']),'section_history_preserved':True})
    print(f'Applied {len(manifest["songs"])} songs; desktop section edits and undo/redo history unchanged.',flush=True)

def make_report(out,reports):
    template=(Path(__file__).with_name('energy_comparison.html')).read_text()
    (out/'index.html').write_text(template.replace('__DATA__',encoded(reports).replace('</','<\\/')).replace('__WEIGHTS__',encoded(EXPERIMENTS)))

def serve(out,port):
    reports=json.loads((out/'comparison.json').read_text())
    audio={r['id']:Path(r['audio']) for r in reports}
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(out),**kwargs)
        def do_GET(self):
            path=urlparse(self.path).path
            if not path.startswith('/audio/'):
                if path not in ('/','/index.html'): self.send_error(404);return
                return super().do_GET()
            source=audio.get(path.removeprefix('/audio/'))
            if not source: self.send_error(404);return
            size=source.stat().st_size;start,end=0,size-1
            match=re.fullmatch(r'bytes=(\d+)-(\d*)',self.headers.get('Range',''))
            if match:
                start=int(match[1]);end=min(size-1,int(match[2]) if match[2] else size-1)
                if start>end: self.send_error(416);return
            self.send_response(206 if match else 200)
            self.send_header('Content-Type',mimetypes.guess_type(str(source))[0] or 'application/octet-stream')
            self.send_header('Accept-Ranges','bytes');self.send_header('Content-Length',str(end-start+1))
            if match:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
            self.end_headers()
            with source.open('rb') as f:
                f.seek(start);remaining=end-start+1
                while remaining:
                    chunk=f.read(min(65536,remaining))
                    if not chunk:break
                    try:self.wfile.write(chunk)
                    except (BrokenPipeError,ConnectionResetError):break
                    remaining-=len(chunk)
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    print(f'Comparison ready at http://127.0.0.1:{port}/',flush=True)
    server.serve_forever()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['prepare','apply','serve']);p.add_argument('--out',type=Path,required=True);p.add_argument('--database',type=Path,default=DEFAULT_DB);p.add_argument('--port',type=int,default=8850);a=p.parse_args()
    if a.mode=='prepare':prepare(a.out.resolve(),a.database.resolve())
    elif a.mode=='apply':apply(a.out.resolve())
    else:serve(a.out.resolve(),a.port)
