#!/usr/bin/env python3
"""Create invented audio and a clearly labeled UI fixture; no inference or downloads."""
import argparse
import os
from pathlib import Path
import sys

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".demo-data"))
    args = parser.parse_args()
    output = args.output.resolve()
    os.environ["ARRANGEMENT_DATA_DIR"] = str(output)
    from arrangement.config import get_settings
    from arrangement.schema import (
        ActivitySegment, BeatGrid, Curve, EnergyModel, Section,
        SectionLabel, SongAnalysis, Source, SpatialCurve,
    )
    from arrangement.server.store import save_analysis

    settings = get_settings()
    settings.ensure_dirs()
    sr, duration = 22050, 48
    t = np.arange(sr * duration) / sr
    envelope = np.select([t < 8, t < 24, t < 40], [.25, .5, .9], default=.35)
    envelope[t >= 40] *= (48 - t[t >= 40]) / 8
    # A repeating tone and pulse have known origins, not guessed instruments.
    notes = np.array([220., 261.6256, 329.6276, 261.6256])
    frequency = notes[(t.astype(int) // 2) % len(notes)]
    phase = np.cumsum(2 * np.pi * frequency / sr)
    tonal = .18 * envelope * np.sin(phase)
    pulse = .12 * envelope * np.sin(2 * np.pi * 80 * t) * np.exp(-24 * (t % .5))
    stem_dir = settings.stems_dir / "synthetic-demo" / "demo"
    stem_dir.mkdir(parents=True, exist_ok=True)
    stereo = lambda x: np.column_stack([x, x])
    sf.write(stem_dir / "tone.wav", stereo(tonal), sr)
    sf.write(stem_dir / "pulse.wav", stereo(pulse), sr)
    audio = settings.uploads_dir / "synthetic-demo.wav"
    sf.write(audio, stereo(tonal + pulse), sr)

    hz = 20
    curve_t = np.arange(duration * hz + 1) / hz
    energy = np.interp(curve_t, t, envelope).tolist()
    sections = []
    for i, (start, end, label, level) in enumerate([
        (0, 8, "intro", .25), (8, 24, "verse", .5),
        (24, 40, "chorus", .9), (40, 48, "outro", .175),
    ]):
        sections.append(Section(
            id=f"demo-{i}", start=start, end=end,
            label=SectionLabel(label), label_source="user",
            macro_id=f"demo-{i}", macro_label=label.title(), macro_index=i+1,
            macro_part=1, macro_parts=1, source_ids=["tone", "pulse"],
            source_count=2, energy_raw=level, energy_perceived=level,
            bar_start=int(start/2)+1, bar_end=int(end/2)+1,
        ))
    sources = [Source(
        id=key, label=label, family="unknown", color=color,
        stem_key=key, separator_model="demo", detection_method="synthetic generator",
        confidence=1, segments=[ActivitySegment(start=0, end=48, level_db=-20)],
        mean_level_db=-20, total_active_seconds=48, duty_cycle=1, n_entries=1,
    ) for key, label, color in [
        ("tone", "Synth tone", "#bb8df2"), ("pulse", "Synth pulse", "#73c6bd")]]
    song = SongAnalysis(
        id="synthetic-demo", title="Synthetic arrangement", artist="Generated demo",
        source_path=str(audio), duration=duration, sample_rate=sr, channels=2,
        beats=BeatGrid(bpm=120, bpm_confidence=1, method="synthetic generator",
                       beats=np.arange(0, 48, .5).tolist(), downbeats=list(range(0, 96, 4))),
        sections=sections, sources=sources,
        energy=EnergyModel(raw=Curve(name="raw", frame_hz=hz, values=energy),
                           perceived=Curve(name="perceived", frame_hz=hz, values=energy)),
        spatial=SpatialCurve(frame_hz=hz, width=[0.] * len(energy),
                             correlation=[1.] * len(energy)),
        warnings=["Synthetic UI fixture. Sections, activity, and energy follow the generator; "
                  "they are not model predictions or evidence of analysis accuracy."],
    )
    save_analysis(song)
    print(f"Demo saved to {output}")
    print(f"Run with ARRANGEMENT_DATA_DIR={output} arrangement-server")


if __name__ == "__main__":
    main()
