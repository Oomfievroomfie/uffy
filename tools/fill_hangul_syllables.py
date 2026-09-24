"""Fill the Hangul Syllables block from its first rank.

U+AC00..U+AE4B is the first rank: the 588 (V, T) combinations with the leading jamo
U+1100. Every other rank is the same 588 glyphs with a different leading jamo, so each
glyph is copied and its U+1100 subcomponent is repointed at the rank's leading jamo.
Trailing jamo (U+11A8..) are left alone: they are separate codepoints that happen to
share a shape with a leading jamo of the same sound.

Usage: uv run --no-sync python tools/fill_hangul_syllables.py [path]
"""

from __future__ import annotations

import sys

from strokespec.model import Glyph, StrokeFont, Subcomponent

SBASE = 0xAC00
SEND = 0xD7A3
LCOUNT = 19
VCOUNT = 21
TCOUNT = 28
NCOUNT = VCOUNT * TCOUNT            # 588 syllables per rank
L_BASE = 0x1100                     # leading jamo
L_GEOMETRY = 0x1100                 # the leading jamo the first rank is authored with


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "glyphs.strokes.json"
    sf = StrokeFont.load(path)

    first_rank = {}
    for k in range(NCOUNT):
        g = sf.get(SBASE + k)
        if g is not None and g.subcomponents:
            first_rank[k] = g
    if len(first_rank) != NCOUNT:
        missing = [hex(SBASE + k) for k in range(NCOUNT) if k not in first_rank]
        print(f"first rank is incomplete: {len(first_rank)}/{NCOUNT}, missing {missing[:10]}")
        return 1

    written = 0
    for li in range(1, LCOUNT):
        lead = L_BASE + li
        for k, template in first_rank.items():
            subs = [
                Subcomponent(lead, s.start, s.end, s.origin)
                if s.codepoint == L_GEOMETRY else
                Subcomponent(s.codepoint, s.start, s.end, s.origin)
                for s in template.subcomponents
            ]
            if not any(s.codepoint == lead for s in subs):
                print(f"{hex(SBASE + k)} has no leading jamo to repoint")
                return 1
            cp = SBASE + li * NCOUNT + k
            sf.add(Glyph(codepoint=cp, strokes=[], subcomponents=subs,
                         width=template.width, combining=template.combining))
            written += 1

    sf.save(path)
    have = sum(1 for cp in range(SBASE, SEND + 1) if sf.has(cp))
    print(f"wrote {written} glyphs; {have}/{SEND - SBASE + 1} syllables present in {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
