"""Per-script coverage of the bundled fonts, using the vendored Unicode script data.

Lists every script that has assigned codepoints and is not covered by the bundled (non-Unifont)
fonts, with how many codepoints are missing. That is the shopping list for bundling.

Workspace and libraries only: bundled cmaps + vendored UCD. No font-directory scan.
"""
from __future__ import annotations

import glob
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import unicodedata2 as unicodedata
from fontTools.ttLib import TTFont

from strokespec.unicode_scripts import script_of

FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src",
                         "strokespec", "data", "fonts")


def bundled_cps(include_unifont: bool = False) -> tuple[dict[str, set], set]:
    faces: dict[str, set] = {}
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
            if fam and (include_unifont or not fam.lower().startswith("unifont")):
                faces.setdefault(fam, set()).update(cps)
        except Exception:
            continue
    union: set = set()
    for cps in faces.values():
        union |= cps
    return faces, union


def main() -> int:
    faces, covered = bundled_cps()
    print(f"bundled native-reference faces: {len(faces)}, codepoints: {len(covered)}")

    by_script: dict[str, list[int]] = defaultdict(list)
    for cp in range(0x20, 0x110000):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        if unicodedata.category(chr(cp)) in ("Cn", "Cs"):
            continue
        by_script[script_of(cp)].append(cp)

    rows = []
    for sc, cps in by_script.items():
        missing = [cp for cp in cps if cp not in covered]
        if missing:
            rows.append((len(missing), len(cps), sc))
    rows.sort(reverse=True)
    print(f"\nscripts with assigned codepoints: {len(by_script)}; not covered by the bundle: "
          f"{len(rows)}")
    print(f"\n{'missing':>8} {'assigned':>9}  script")
    for missing, total, sc in rows:
        print(f"{missing:>8} {total:>9}  {sc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
