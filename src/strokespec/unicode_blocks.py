"""Unicode block table, parsed at runtime from the vendored ``Blocks.txt``.

``Blocks.txt`` is a snapshot of the official Unicode Character Database (in
``src/strokespec/data/Blocks.txt``). It is parsed at load time — nothing is hardcoded and it is
never re-downloaded. Private-use blocks are excluded: the editor never navigates into them.

The file is a snapshot and must be updated occasionally when Unicode adds/changes blocks; see
DESIGN.md.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple

_BLOCKS_PATH = Path(__file__).resolve().parent / "data" / "Blocks.txt"

_cache: List[Tuple[int, int, str]] | None = None


def _load_blocks() -> List[Tuple[int, int, str]]:
    """Parse the vendored Blocks.txt into ``(start, end, name)`` triples (cached)."""
    global _cache
    if _cache is not None:
        return _cache
    blocks: List[Tuple[int, int, str]] = []
    with open(_BLOCKS_PATH, encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            m = re.match(r"([0-9A-Fa-f]+)\.\.([0-9A-Fa-f]+);\s*(.+)$", line)
            if not m:
                continue
            name = m.group(3).strip()
            if "Private Use" in name:
                continue  # blacklist private-use blocks in the editor
            blocks.append((int(m.group(1), 16), int(m.group(2), 16), name))
    _cache = blocks
    return blocks


def block_of(codepoint: int) -> int:
    """Return the index of the block containing ``codepoint``, or -1."""
    for i, (start, end, _name) in enumerate(_load_blocks()):
        if start <= codepoint <= end:
            return i
    return -1


def block_name(codepoint: int) -> str:
    """Human-readable block name for a codepoint (fallback: 'Other')."""
    idx = block_of(codepoint)
    return _load_blocks()[idx][2] if idx >= 0 else "Other"


def block_ranges() -> List[Tuple[str, int, int]]:
    """The full table as ``(name, start, end)`` triples (private-use excluded)."""
    return [(name, start, end) for start, end, name in _load_blocks()]
