"""Conservative boundary proposals when a trained section label conflicts with the song.

A novelty peak by itself is often an internal arrangement change. A dip in the
energy curve by itself can be a fill. Only their conjunction, inside an unusually
long model span whose repeat identity breaks an A-B-A run, is enough to propose
a top-level seam. The role on the earlier side remains unknown.
"""

from __future__ import annotations

from typing import Sequence


def _role(section) -> str:
    label = section.label
    return str(getattr(label, "value", label))


def propose_repairs(model_sections: Sequence, fallback_sections: Sequence,
                    energy_changes: Sequence) -> list[tuple[int, float]]:
    """Return ``(model index, time)`` seams supported by three independent clues.

    The repeat pattern says the middle passage is unlike its same-named
    neighbours. The local detector says *where* its internal boundary is. A
    substantial fall followed by a rise says that boundary has an audible
    arrangement consequence. None of the three sources is sufficient alone.
    """
    repairs: list[tuple[int, float]] = []
    for i in range(1, len(model_sections) - 1):
        before, middle, after = model_sections[i - 1:i + 2]
        if not (_role(before) == _role(middle) == _role(after)
                and before.repeat_group == after.repeat_group
                and middle.repeat_group != before.repeat_group
                and middle.end - middle.start >= 28):
            continue

        candidates: list[tuple[float, float]] = []
        for seam in fallback_sections:
            t = seam.start
            if not (middle.start + 8 < t < middle.end - 8
                    and seam.novelty >= .7):
                continue
            falls = [c for c in energy_changes
                     if c.direction == "fall" and c.magnitude >= .2
                     and t - 10 <= c.t <= t + 2]
            if not falls:
                continue
            rises = [c for c in energy_changes
                     if c.direction == "rise" and c.magnitude >= .15
                     and t - 2 <= c.t <= t + 10
                     and any(2 <= c.t - f.t <= 20 for f in falls)]
            if rises:
                candidates.append((seam.novelty, t))
        if candidates:
            _, t = max(candidates)
            repairs.append((i, float(t)))
    return repairs
