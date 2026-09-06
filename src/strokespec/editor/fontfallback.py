"""Per-block native-text font fallback for the editor previews.

The big "native reference" panel and the small per-cell character badge render a codepoint as
text. Qt's default glyph fallback (qwindowsfontdatabasebase.cpp) is a hardcoded CJK-centric try
font list that covers ~90% of scripts; our intentional/bundled fonts must run after that system
chain and Unifont must be the absolute last resort.

There is no Qt API to append glyph fallbacks after the platform chain (application fallbacks are
always prepended), so we build the family list ourselves, and it must always include the actual
font(s) that support the codepoint's script. If a script's proper font is *omitted* (e.g. an
Ethiopic stack without Ebrima or Nyala), Qt renders it through a font that doesn't really support
it, and its measuring-vs-layout font-stack mismatch makes that visible as a clipped/broken glyph.
So the rule is simple: attach every rare-script fallback font whose cmap covers the codepoint.
"""
from __future__ import annotations

import glob
import os
from pathlib import Path

from fontTools.ttLib import TTFont
from PySide6.QtWidgets import QApplication

_FONTS_DIR = Path(__file__).resolve().parent.parent / "data" / "fonts"

# First-party Windows script fonts (not in Qt's system try list) covering specific scripts that
# Segoe UI does not render properly. Ebrima/Nyala cover Ethiopic (and other African scripts),
# Historic covers the ancient scripts, Gadugi the Canadian/Cherokee syllabics, Nirmala the Indic
# scripts, Yi Baiti Yi. A script's stack MUST include its supporting font from here.
WINDOWS_SCRIPT = [
    "Microsoft Yi Baiti", "Segoe UI Historic", "Ebrima", "Nyala", "Gadugi", "Nirmala UI",
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
    """family -> set(codepoints) for the fallback candidates (bundled + Windows script fonts)."""
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
    # First-party Windows script fonts + the primary (Segoe UI) font's own cmap, from disk.
    app = QApplication.instance()
    primary = app.font().family() if app is not None else "Segoe UI"
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
                if fam in WINDOWS_SCRIPT:
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
    """Native-text family list for a codepoint.

    Always includes every rare-script fallback font (Windows script font or bundled Noto) whose
    cmap covers ``cp``, so a script is never rendered through a font that doesn't support it — Qt
    draws such a codepoint via the wrong stack and clips it. When no rare-script font is needed
    (Latin, CJK, … — handled by Qt's own platform chain) we return just the primary family.
    """
    cps = _load_family_cps()
    app = QApplication.instance()
    primary = app.font().family() if app is not None else "Segoe UI"
    # The primary font (Segoe UI) genuinely covers it -> its own stack handles it; no rare font.
    if cp in _primary_cps:
        return [primary]
    # Attach every rare-script fallback font whose cmap covers this codepoint so the script is
    # rendered through a font that truly supports it (e.g. Ebrima/Nyala for Ethiopic, Historic
    # for ancient scripts, a bundled Noto for a rare block). If none is needed, fall through to
    # Qt's own platform chain.
    relevant: list[str] = []
    for fam in WINDOWS_SCRIPT + (_bundled_families or []):
        if fam in ("Unifont", "Unifont Upper"):
            continue
        if cp in cps.get(fam, ()):
            relevant.append(fam)
    if not relevant:
        return [primary]
    return [primary] + relevant + ["Unifont", "Unifont Upper"]
