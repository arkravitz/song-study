#!/usr/bin/env python3
"""Build a native .app with a private, relocatable analysis runtime. No web server."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
DESKTOP=ROOT/'desktop'
APP=DESKTOP/'build'/'Song Study.app'
RES=APP/'Contents'/'Resources'


def bundle_icon():
    """Create every standard macOS icon size from the checked-in artwork."""
    source=DESKTOP/'Assets'/'SongStudy.png'
    iconset=DESKTOP/'build'/'SongStudy.iconset'
    iconset.mkdir(parents=True,exist_ok=True)
    for size in (16,32,128,256,512):
        for scale in (1,2):
            pixels=size*scale
            suffix='@2x' if scale==2 else ''
            run(['sips','-z',pixels,pixels,source,'--out',iconset/f'icon_{size}x{size}{suffix}.png'],stdout=subprocess.DEVNULL)
    run(['iconutil','-c','icns',iconset,'-o',RES/'SongStudy.icns'])


def run(args,**kwargs):
    return subprocess.run([str(a) for a in args],check=True,**kwargs)


def clone(source,destination):
    destination.parent.mkdir(parents=True,exist_ok=True)
    # APFS copy-on-write avoids duplicating the 1.4 GB runtime on local builds.
    run(['/bin/cp','-cR',source,destination])


def bundle_tools():
    tools=RES/'tools';libs=RES/'lib';tools.mkdir(exist_ok=True);libs.mkdir(exist_ok=True)
    pending=[]
    for name in ('ffmpeg','ffprobe'):
        source=Path(shutil.which(name) or '')
        if not source.is_file(): raise RuntimeError(f'{name} is required by the existing analysis runtime.')
        dest=tools/name;clone(source.resolve(),dest);pending.append((source.resolve(),dest))
    seen=set()
    while pending:
        source,dest=pending.pop()
        if str(source) in seen:continue
        seen.add(str(source))
        listing=subprocess.check_output(['otool','-L',str(source)],text=True)
        for line in listing.splitlines()[1:]:
            dependency=line.strip().split(' (')[0]
            if not dependency.startswith('/opt/') and not dependency.startswith('/usr/local/'):continue
            dep=Path(dependency).resolve();target=libs/dep.name
            if not target.exists():clone(dep,target);pending.append((dep,target))
            relative='@loader_path/../lib/'+dep.name if dest.parent==tools else '@loader_path/'+dep.name
            run(['install_name_tool','-change',dependency,relative,dest],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        run(['codesign','--force','--sign','-',dest],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--debug',action='store_true');args=parser.parse_args()
    env=os.environ.copy();env['CLANG_MODULE_CACHE_PATH']=str(DESKTOP/'.cache'/'clang')
    configuration='debug' if args.debug else 'release'
    run(['swift','build','--package-path',DESKTOP,'--disable-sandbox','--cache-path',DESKTOP/'.cache','-c',configuration],env=env)
    binary_dir=subprocess.check_output(['swift','build','--package-path',str(DESKTOP),'--show-bin-path','-c',configuration],env=env,text=True).strip()
    (APP/'Contents'/'MacOS').mkdir(parents=True,exist_ok=True);RES.mkdir(parents=True,exist_ok=True)
    shutil.copy2(Path(binary_dir)/'SongStudy',APP/'Contents'/'MacOS'/'SongStudy')
    bundle_icon()
    info={'CFBundleExecutable':'SongStudy','CFBundleIdentifier':'com.arrangement.songstudy','CFBundleName':'Song Study',
          'CFBundleIconFile':'SongStudy.icns',
          'CFBundleDisplayName':'Song Study','CFBundlePackageType':'APPL','CFBundleShortVersionString':'0.1.0','CFBundleVersion':'1',
          'LSMinimumSystemVersion':'14.0','NSHighResolutionCapable':True,'NSPrincipalClass':'NSApplication',
          'NSHumanReadableCopyright':'Song Study. Includes third-party components; see Resources notices.'}
    (APP/'Contents'/'Info.plist').write_bytes(plistlib.dumps(info))
    shutil.copytree(DESKTOP/'worker',RES/'worker',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','tests'))
    shutil.copytree(ROOT/'backend'/'arrangement',RES/'backend'/'arrangement',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
    if not (RES/'python'/'bin'/'python3.12').exists():
        python=Path(subprocess.check_output([str(ROOT/'.venv'/'bin'/'python'),'-c','import sys; print(sys.base_prefix)'],text=True).strip()).resolve()
        clone(python,RES/'python')
        packages=ROOT/'.venv'/'lib'/'python3.12'/'site-packages'
        destination=RES/'python'/'lib'/'python3.12'/'site-packages'
        if destination.exists():shutil.rmtree(destination)
        clone(packages,destination)
        for p in destination.glob('_editable*'):p.unlink() if p.is_file() else None
        for name in ('_virtualenv.pth','_virtualenv.py'):(destination/name).unlink(missing_ok=True)
    launcher=RES/'python'/'bin'/'allin1-mlx'
    launcher.write_text("#!/bin/sh\nexec \"$(dirname \"$0\")/python3.12\" -c 'from allin1_mlx.cli import main; main()' \"$@\"\n")
    launcher.chmod(0o755)
    model_source=ROOT/'data'/'models'/'allinone_mlx'
    if model_source.exists():
        shutil.copytree(model_source,RES/'models'/'allinone_mlx',dirs_exist_ok=True)
    if not (RES/'tools'/'ffmpeg').exists():bundle_tools()
    config={'root':str(ROOT),'legacy':str(ROOT/'data'),'store':'$APPLICATION_SUPPORT/Song Study',
            'python':'$RESOURCES/python/bin/python3.12','backend':'$RESOURCES/backend',
            'worker':'$RESOURCES/worker/worker.py','tools':'$RESOURCES/tools'}
    (RES/'configuration.json').write_text(json.dumps(config,indent=2))
    (RES/'NOTICES.txt').write_text('Local development build. The bundled Python site-packages retain their distribution license files.\nRuntime includes CPython, NumPy, SciPy, PyTorch, Demucs, All-In-One MLX, and ffmpeg plus codec dependencies.\nCommercial redistribution requires a complete license and model-checkpoint manifest. This build is ad-hoc signed, not notarized.\n')
    run(['codesign','--force','--sign','-',APP],stdout=subprocess.DEVNULL)
    print(APP)

if __name__=='__main__':main()
