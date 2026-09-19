"""A subset of the Unicode bidirectional algorithm (UAX #9): base direction and directional runs.

Enough to place a paragraph that mixes directionalities: P2/P3 for the base level, the classes
from ``unicodedata``, N1/N2 for the neutrals that sit between runs, numbers kept left-to-right,
then the runs come back in visual order.

Not implemented: the explicit embedding/override/isolate controls (LRE, RLE, PDF, LRI, RLI, FSI,
PDI), bracket pairs (N0), the weak-type rules beyond numbers (W1-W7), and the line-level reset
(L1). Text using those codes is treated as if they were not there.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import unicodedata2 as unicodedata

RTL = 1
LTR = 0

_STRONG_R = ("R", "AL")


def bidi_class(char: str) -> str:
    """The Unicode bidirectional class of a character (``""`` for unassigned)."""
    return unicodedata.bidirectional(char) or ""


def base_level(text: str, base_direction: Optional[str] = None) -> int:
    """The paragraph level: 0 (LTR) or 1 (RTL).

    ``base_direction`` ("ltr"/"rtl") overrides P2/P3. Otherwise the first strong character
    decides (P2) and a paragraph with no strong character is left-to-right (P3).
    """
    if base_direction:
        return RTL if base_direction.lower().startswith("r") else LTR
    for char in text:
        cls = bidi_class(char)
        if cls == "L":
            return LTR
        if cls in _STRONG_R:
            return RTL
    return LTR


def _char_levels(text: str, base: int) -> List[int]:
    """A resolved level per character: even is left-to-right, odd is right-to-left."""
    levels: List[int] = []
    for char in text:
        cls = bidi_class(char)
        if cls == "L":
            levels.append(0)
        elif cls in _STRONG_R:
            levels.append(1)
        elif cls in ("EN", "AN"):
            levels.append(0)          # numbers stay left-to-right
        else:
            levels.append(-1)         # neutral until N1/N2 resolve it
    # N1/N2: a neutral run takes the direction of the characters on both sides when they agree,
    # and the paragraph direction otherwise. For this test EN/AN count as R, per UAX #9 N1.
    sides: List[Optional[int]] = []
    for char in text:
        cls = bidi_class(char)
        if cls == "L":
            sides.append(LTR)
        elif cls in _STRONG_R or cls in ("EN", "AN"):
            sides.append(RTL)
        else:
            sides.append(None)

    i = 0
    while i < len(levels):
        if levels[i] >= 0:
            i += 1
            continue
        j = i
        while j < len(levels) and levels[j] < 0:
            j += 1
        before = next((sides[k] for k in range(i - 1, -1, -1) if sides[k] is not None), None)
        after = next((sides[k] for k in range(j, len(sides)) if sides[k] is not None), None)
        resolved = before if (before is not None and before == after) else base
        for k in range(i, j):
            levels[k] = resolved
        i = j
    return levels


def visual_runs(text: str, base_direction: Optional[str] = None) -> List[Tuple[int, int, bool]]:
    """Directional runs of ``text`` as ``(start, end, rtl)``, in visual (left-to-right) order.

    ``start``/``end`` index the original string, so a run's text is ``text[start:end]`` in
    logical order — what a shaper wants; the list order is what a renderer lays out.
    """
    if not text:
        return []
    base = base_level(text, base_direction)
    levels = _char_levels(text, base)
    runs: List[Tuple[int, int, bool]] = []
    start = 0
    for i in range(1, len(text) + 1):
        if i == len(text) or (levels[i] % 2) != (levels[start] % 2):
            runs.append((start, i, bool(levels[start] % 2)))
            start = i
    if base % 2:
        runs.reverse()
    return runs
