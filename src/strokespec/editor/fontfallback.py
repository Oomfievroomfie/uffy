"""Per-block native-text font fallback for the editor previews.

The big "native reference" panel and the small per-cell character badge render a codepoint as
text. Qt's default glyph fallback (qwindowsfontdatabasebase.cpp) is a hardcoded CJK-centric try
font list that covers ~90% of scripts; our intentional/bundled fonts must run AFTER that system
chain (so they never steal, e.g., Japanese from MS UI Gothic) and Unifont must be the absolute
last resort.

There is no Qt API to append glyph fallbacks after the platform chain (application fallbacks are
always prepended), so we build the family list ourselves, ON A PER-BLOCK BASIS: for a codepoint
in block X we attach only the fallback fonts that actually cover X's rare scripts -- not every
bundled font.
"""
from __future__ import annotations

import glob
import os
from pathlib import Path

from fontTools.ttLib import TTFont
from PySide6.QtWidgets import QApplication

_FONTS_DIR = Path(__file__).resolve().parent.parent / "data" / "fonts"

# System try-fonts Qt itself falls back to for the default font; tried FIRST.
SYSTEM_FALLBACKS = [
    "Arial", "MS UI Gothic", "Gulim", "SimSun", "PMingLiU", "Arial Unicode MS",
    "Yu Gothic UI", "Malgun Gothic", "Segoe UI Emoji", "Segoe UI Symbol",
]
# First-party Windows script fonts (not in the system try list) covering specific scripts.
WINDOWS_SCRIPT = [
    "Microsoft Yi Baiti", "Segoe UI Historic", "Ebrima", "Gadugi", "Nirmala UI",
]

_family_cps: dict[str, set] | None = None
_bundled_families: list[str] | None = None
_primary_cps: set = set()


def _family_name(tt: TTFont) -> str | None:
    name = tt["name"]
    for mid in (16, 1):
        rec = name.getName(mid, 3, 1) or name.getName(mid, 3, 0) or name.getName(mid, 1, 0)
        if rec:
            return rec.toUnicode()
    return None


def _cps(font: TTFont) -> set:
    return {cp for tb in font["cmap"].tables if tb.isUnicode() for cp in tb.cmap}


def _load_family_cps() -> dict[str, set]:
    """family -> set(codepoints) for every fallback candidate (bundled + Windows script fonts)."""
    global _family_cps, _bundled_families, _primary_cps
    if _family_cps is not None:
        return _family_cps
    cps: dict[str, set] = {}
    bundled: list[str] = []
    # Bundled fonts (Noto + Unifont), loaded from disk.
    for path in glob.glob(str(_FONTS_DIR / "*")):
        if os.path.splitext(path)[1].lower() not in (".ttf", ".otf"):
            continue
        try:
            tt = TTFont(path, lazy=True)
            fam = _family_name(tt)
            if fam:
                cps.setdefault(fam, set()).update(_cps(tt))
                if fam not in ("Unifont", "Unifont Upper"):
                    bundled.append(fam)
            tt.close()
        except Exception:
            continue
    # First-party Windows script fonts (system + per-user dirs), plus the system try-fonts
    # (so we can tell which codepoints the default chain already renders).
    app = QApplication.instance()
    primary = app.font().family() if app is not None else "Segoe UI"
    wanted = set(WINDOWS_SCRIPT) | set(SYSTEM_FALLBACKS)
    for d in (r"C:\Windows\Fonts",
              os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts")):
        if not os.path.isdir(d):
            continue
        for path in glob.glob(os.path.join(d, "*")):
            if os.path.splitext(path)[1].lower() not in (".ttf", ".otf", ".ttc"):
                continue
            try:
                tt = TTFont(path, fontNumber=0, lazy=True) if path.lower().endswith(".ttc") else TTFont(path, lazy=True)
                fam = _family_name(tt)
                if fam in wanted:
                    cps.setdefault(fam, set()).update(_cps(tt))
                if fam == primary:
                    _primary_cps |= _cps(tt)
                tt.close()
            except Exception:
                continue
    _family_cps = cps
    _bundled_families = bundled
    return _family_cps


def native_text_families(cp: int) -> list[str]:
    """Native-text family list for a codepoint — NO override unless the block needs one.

    If the primary font or Qt's own system try-font chain already renders ``cp`` (Latin, CJK,
    Japanese, Greek, …), return just the primary family: Qt's platform fallback handles it and
    we do NOT attach Unifont or any rare-script font. Only when nothing in the system chain can
    render ``cp`` (a radically-unrepresented block) do we attach the minimal rare-script
    font(s) that cover it, with Unifont as the absolute last resort.
    """
    cps = _load_family_cps()
    app = QApplication.instance()
    primary = app.font().family() if app is not None else "Segoe UI"
    # Default chain renders it? -> no override.
    if cp in cps.get(primary, ()) or any(cp in cps.get(f, ()) for f in SYSTEM_FALLBACKS):
        return [primary]
    # Radically unrepresented: attach only the rare-script fonts that actually cover this
    # codepoint, then Unifont last.
    relevant: list[str] = []
    for fam in WINDOWS_SCRIPT + (_bundled_families or []):
        if fam in ("Unifont", "Unifont Upper"):
            continue
        if cp in cps.get(fam, ()):
            relevant.append(fam)
    return [primary] + relevant + ["Unifont", "Unifont Upper"]
