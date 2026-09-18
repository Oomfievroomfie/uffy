"""Unicode script table, parsed at runtime from the vendored ``Scripts.txt``.

``Scripts.txt`` is a snapshot of the official Unicode Character Database (in
``src/strokespec/data/Scripts.txt``). It is parsed at load time — nothing is hardcoded and it is
never re-downloaded.

This exists rather than ``fontTools.unicodedata.script`` because fontTools' generated script table
is frozen at whatever release its own build ran against; the newly assigned scripts of a Unicode
release therefore read as ``Unknown`` there for as long as it takes fontTools to ship them.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from pathlib import Path
from typing import List, Tuple

_SCRIPTS_PATH = Path(__file__).resolve().parent / "data" / "Scripts.txt"

UNKNOWN = "Unknown"

_cache: Tuple[List[int], List[int], List[str]] | None = None


def _load_scripts() -> Tuple[List[int], List[int], List[str]]:
    """Parse the vendored Scripts.txt into parallel sorted ``starts`` / ``ends`` / ``names``."""
    global _cache
    if _cache is not None:
        return _cache
    rows: List[Tuple[int, int, str]] = []
    with open(_SCRIPTS_PATH, encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            m = re.match(r"([0-9A-Fa-f]+)(?:\.\.([0-9A-Fa-f]+))?\s*;\s*(\w+)$", line)
            if not m:
                continue
            start = int(m.group(1), 16)
            end = int(m.group(2), 16) if m.group(2) else start
            rows.append((start, end, m.group(3)))
    rows.sort()
    starts = [start for start, _end, _name in rows]
    ends = [end for _start, end, _name in rows]
    names = [name for _start, _end, name in rows]
    _cache = (starts, ends, names)
    return _cache


def script_of(codepoint: int) -> str:
    """Long script name for ``codepoint`` (fallback: ``UNKNOWN``)."""
    starts, ends, names = _load_scripts()
    i = bisect_right(starts, codepoint) - 1
    if i < 0 or codepoint > ends[i]:
        return UNKNOWN
    return names[i]
