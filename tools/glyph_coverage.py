"""Generate a markdown report on how much ``glyphs.strokes.json`` covers Unicode.

Usage (from the repo root, with the venv active):

    uv run --no-sync python tools/glyph_coverage.py          # writes COVERAGE.md
    uv run --no-sync python tools/glyph_coverage.py -o COVERAGE.md
"""

from __future__ import annotations

import argparse
import datetime
from pathlib import Path

import unicodedata2 as unicodedata

from strokespec.model import StrokeFont
from strokespec.unicode_blocks import block_ranges


def _allocated(start: int, end: int) -> int:
    """Count allocated (Unicode-assigned) codepoints in [start, end]."""
    n = 0
    for cp in range(start, end + 1):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        if unicodedata.name(chr(cp), ""):
            n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("strokes", default="glyphs.strokes.json", nargs="?",
                    help="path to the stroke set (default glyphs.strokes.json)")
    ap.add_argument("-o", "--output", default="COVERAGE.md", help="markdown output path")
    args = ap.parse_args(argv)

    sf = StrokeFont.load(args.strokes)
    authored = dict(sf.codepoints()) if isinstance(sf.codepoints(), dict) else {
        cp: sf.get(cp) for cp in sf.codepoints()
    }
    # Keep only glyphs that actually exist (codepoints() may include empty entries).
    authored = {cp: g for cp, g in authored.items() if g is not None}
    authored_set = set(authored)
    with_strokes = sum(1 for g in authored.values() if g.strokes)

    blocks = block_ranges()
    tot_alloc = tot_cov = 0
    rows = []
    for name, start, end in blocks:
        alloc = _allocated(start, end)
        cov = sum(1 for cp in range(start, end + 1) if cp in authored_set)
        tot_alloc += alloc
        tot_cov += cov
        pct = (100.0 * cov / alloc) if alloc else 0.0
        rows.append((name, start, end, alloc, cov, pct))
    rows.sort(key=lambda r: -r[5])

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    # Codepoints authored that fall outside the scanned block table (unlikely; flag them).
    scanned = set()
    for _n, _s, _e in blocks:
        scanned |= set(range(_s, _e + 1))
    outside = sorted(authored_set - scanned)

    lines = []
    lines.append("# Glyph coverage report\n")
    lines.append(f"Source: `{args.strokes}`  \nGenerated: {now}  \n")
    lines.append("## Summary\n")
    lines.append("| metric | value |")
    lines.append("|---|---|")
    lines.append(f"| codepoints authored | {len(authored_set)} |")
    lines.append(f"| - with strokes | {with_strokes} |")
    lines.append(f"| - intentionally empty | {len(authored_set) - with_strokes} |")
    lines.append(f"| allocated codepoints (in scanned blocks) | {tot_alloc} |")
    lines.append(f"| coverage of allocated | {tot_cov} ({100.0 * tot_cov / tot_alloc:.1f}%) |")
    lines.append(f"| codepoints authored outside the block table | {len(outside)} |")
    if outside:
        lines.append("\nOutside blocks: " + " ".join(f"U+{cp:04X}" for cp in outside[:20]))
    lines.append("")
    lines.append("## Coverage by block\n")
    lines.append("| block | range | allocated | covered | % |")
    lines.append("|---|---:|---:|---:|---:|")
    for name, start, end, alloc, cov, pct in rows:
        lines.append(f"| {name} | U+{start:04X}-U+{end:04X} | {alloc} | {cov} | {pct:.1f}% |")

    Path(args.output).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {args.output} (authored {len(authored_set)}, coverage {tot_cov}/{tot_alloc})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
