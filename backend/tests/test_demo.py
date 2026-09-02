"""A fresh checkout can seed a playable demo without models or provider keys."""
import json
import os
from pathlib import Path
import subprocess
import sys

import soundfile as sf


def test_demo_generates_playable_audio_and_portable_analysis(tmp_path):
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "ARRANGEMENT_ALLOW_DOWNLOAD": "0", "DEEPSEEK_API_KEY": ""}
    result = subprocess.run(
        [sys.executable, str(root / "examples/create_demo.py"), "--output", str(tmp_path)],
        env=env, text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    analysis = json.loads((tmp_path / "analyses/synthetic-demo.json").read_text())
    assert analysis["title"] == "Synthetic arrangement"
    assert analysis["duration"] == 48
    assert [s["macro_label"] for s in analysis["sections"]] == ["Intro", "Verse", "Chorus", "Outro"]
    assert "synthetic" in " ".join(analysis["warnings"]).lower()
    audio = sf.info(analysis["source_path"])
    assert audio.frames == 48 * 22050
    assert audio.channels == 2
    assert (tmp_path / "analyses/synthetic-demo.npz").is_file()
    assert len(list((tmp_path / "stems/synthetic-demo/demo").glob("*.wav"))) == 2
