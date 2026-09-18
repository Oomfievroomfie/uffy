"""Plan and verify the Noto faces the bundle is missing.

    list   -> write .noto-cache/urls.txt  (member<TAB>url), the faces to fetch
    bundle -> verify what is in .noto-cache and copy the useful ones into data/fonts

Downloading itself is done by the shell (curl from inside Python hits an SSL error here).
"""
from __future__ import annotations

import os
import shutil
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import unicodedata2 as unicodedata
from fontTools import unicodedata as ftunicodedata
from fontTools.ttLib import TTFont

from script_font_gaps import bundled_cps

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FONTS_DIR = os.path.join(ROOT, "src", "strokespec", "data", "fonts")
CACHE = os.path.join(ROOT, ".noto-cache")
BASE = "https://raw.githubusercontent.com/google/fonts/main/ofl/"
SKIP_SCRIPTS = {"Zzzz", "Zyyy", "Zinh", "Hani", "Hang", "Hira", "Kana"}
EXTRA_MEMBERS = [
    "NotoSans-Regular.ttf",
    "NotoSansSymbols-Regular.ttf",
    "NotoSansSymbols2-Regular.ttf",
    "NotoSansEgyptianHieroglyphs-Regular.ttf",
    "NotoSansMeroitic-Regular.ttf",
]


def slugify(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def family_of(path: str) -> str | None:
    tt = TTFont(path, lazy=True)
    name = tt["name"]
    fam = None
    for mid in (16, 1):
        rec = name.getName(mid, 3, 1) or name.getName(mid, 1, 0)
        if rec:
            fam = rec.toUnicode()
            break
    tt.close()
    return fam


def cmap_of(path: str) -> set:
    tt = TTFont(path, lazy=True)
    cps = {c for tb in tt["cmap"].tables if tb.isUnicode() for c in tb.cmap}
    tt.close()
    return cps


def wanted() -> list[tuple[str, str, list[int]]]:
    """(member, url, codepoints wanted) for every missing script face."""
    faces, covered = bundled_cps()
    by_script: dict[str, list[int]] = defaultdict(list)
    for cp in range(0x20, 0x110000):
        if 0xD800 <= cp <= 0xDFFF or unicodedata.category(chr(cp)) in ("Cn", "Cs"):
            continue
        try:
            sc = ftunicodedata.script(chr(cp))
        except Exception:
            sc = "Zzzz"
        by_script[sc].append(cp)
    out = []
    for sc, cps in sorted(by_script.items(), key=lambda kv: -len(kv[1])):
        if sc in SKIP_SCRIPTS:
            continue
        missing = [cp for cp in cps if cp not in covered]
        if not missing:
            continue
        try:
            sname = ftunicodedata.script_name(sc).replace(" ", "")
        except Exception:
            sname = sc
        for member in (f"NotoSans{sname}-Regular.ttf", f"NotoSerif{sname}-Regular.ttf"):
            out.extend(urls_for(member, missing))
    for member in EXTRA_MEMBERS:
        out.extend(urls_for(member, []))
    return out


def urls_for(member: str, missing: list[int]) -> list[tuple[str, str, list[int]]]:
    """Every place the face might live: static, static/ subdir, or as a variable font."""
    family = member.split("-")[0]
    slug = slugify(family)
    static = f"{BASE}{slug}/{member}"
    instatic = f"{BASE}{slug}/static/{member}"
    variable = f"{BASE}{slug}/{family}[wght].ttf"
    return [(member, static, missing), (member, instatic, missing), (member, variable, missing)]


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "list"
    os.makedirs(CACHE, exist_ok=True)
    if mode == "list":
        entries = wanted()
        seen: set[str] = set()
        with open(os.path.join(CACHE, "urls.txt"), "w", encoding="utf-8") as fh:
            for member, url, _ in entries:
                if member in seen:
                    continue
                seen.add(member)
                fh.write(f"{member}\t{url}\n")
        print(f"wrote {len(seen)} candidate urls to .noto-cache/urls.txt")
        return 0

    # bundle
    entries = wanted()
    already = {os.path.basename(p) for p in os.listdir(FONTS_DIR)}
    copied, skipped = [], []
    for member, _url, missing in entries:
        path = os.path.join(CACHE, member)
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            continue
        try:
            cps = cmap_of(path)
            fam = family_of(path)
        except Exception:
            continue
        if not fam:
            continue
        hit = sum(1 for cp in missing if cp in cps) if missing else len(cps)
        if missing and hit < max(1, len(missing) // 2):
            skipped.append((member, fam, hit, len(missing)))
            continue
        if member in already:
            skipped.append((member, fam, "already bundled", 0))
            continue
        shutil.copyfile(path, os.path.join(FONTS_DIR, member))
        copied.append((member, fam, len(cps), hit, len(missing)))
        print(f"  ADDED {member:<40} {fam:<34} cps={len(cps):6d} wanted={len(missing)} hit={hit}")
    print(f"\ncopied {len(copied)} faces into data/fonts; skipped {len(skipped)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
