"""Which Unicode blocks do the bundled fonts not cover at all?

Reads the cmaps of every font in ``src/strokespec/data/fonts`` and the vendored block table, and
reports, per block, how many *allocated* codepoints the bundle covers. Blocks with allocated
codepoints and no coverage are the ones that need another face bundled.

Workspace and stdlib only: no font-directory scan, no network.
"""
from __future__ import annotations

import glob
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import unicodedata2 as unicodedata
from fontTools.ttLib import TTFont

from strokespec.unicode_blocks import block_ranges

FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src",
                         "strokespec", "data", "fonts")


def load_bundled() -> dict[str, set]:
    out: dict[str, set] = {}
    for path in sorted(glob.glob(os.path.join(FONTS_DIR, "*"))):
        if os.path.splitext(path)[1].lower() not in (".ttf", ".otf", ".ttc"):
            continue
        try:
            tt = TTFont(path, lazy=True)
            name = tt["name"]
            fam = None
            for mid in (16, 1):
                rec = name.getName(mid, 3, 1) or name.getName(mid, 1, 0)
                if rec:
                    fam = rec.toUnicode()
                    break
            cps = {c for tb in tt["cmap"].tables if tb.isUnicode() for c in tb.cmap}
            tt.close()
            if fam:
                out.setdefault(fam, set()).update(cps)
        except Exception:
            continue
    return out


def main() -> int:
    bundled = load_bundled()
    # Unifont is not a native reference, so it does not count as coverage here.
    native = {fam: cps for fam, cps in bundled.items() if not fam.lower().startswith("unifont")}
    all_cps: set = set()
    for cps in native.values():
        all_cps |= cps
    print(f"bundled faces: {len(bundled)}   native-reference faces: {len(native)}"
          f"   codepoints covered: {len(all_cps)}")
    rows = []
    for name, start, end in block_ranges():
        allocated = [cp for cp in range(start, end + 1)
                     if unicodedata.category(chr(cp)) not in ("Cn", "Cs")]
        if not allocated:
            continue
        covered = sum(1 for cp in allocated if cp in all_cps)
        rows.append((covered, len(allocated), name, start, end))
    empty = [r for r in rows if r[0] == 0]
    partial = [r for r in rows if 0 < r[0] < r[1]]
    print(f"\nblocks with allocated codepoints: {len(rows)}")
    print(f"  fully covered : {len(rows) - len(empty) - len(partial)}")
    print(f"  partially     : {len(partial)}")
    print(f"  NOT covered   : {len(empty)}")

    print(f"\nblocks with NO bundled coverage ({len(empty)}):")
    for covered, allocated, name, start, end in sorted(empty, key=lambda r: -r[1]):
        print(f"  U+{start:04X}-U+{end:04X}  {allocated:6d} allocated   {name}")
    print(f"\npartially covered blocks (allocated, missing):")
    for covered, allocated, name, start, end in sorted(partial, key=lambda r: -(r[1] - r[0]))[:25]:
        print(f"  U+{start:04X}-U+{end:04X}  {allocated:6d} allocated, {allocated - covered:6d} missing"
              f"   {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
