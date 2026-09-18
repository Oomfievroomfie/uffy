"""Per-block native-text font fallback for the editor previews.

The big "native reference" panel and the small per-cell character badge render a codepoint as
text. Qt's default glyph fallback (qwindowsfontdatabasebase.cpp) is a hardcoded CJK-centric try
font list that covers ~90% of scripts; Unifont must be the absolute last resort after the system.

There is no Qt API to append glyph fallbacks after the platform chain (application fallbacks are
always prepended), so we build the family list ourselves, and it must always include the actual
font(s) that support the codepoint's script. If a script's proper font is *omitted* (e.g. an
Ethiopic stack without Ebrima or Nyala), Qt renders it through a font that doesn't really support
it, and its measuring-vs-layout font-stack mismatch makes that visible as a clipped/broken glyph.
So the rule is simple: attach every OS-default font whose cmap covers the codepoint.

The consultation pool is strictly the fonts that ship with a clean Windows install
(``DEFAULT_WINDOWS_FONTS``) plus the primary app font. Fonts a user has added themselves (Noto,
Source Han, HanaMinA, …) are deliberately NOT consulted: they may be absent on another machine,
so treating them as system coverage would be wrong.
"""
from __future__ import annotations

import glob
import os
from pathlib import Path

from fontTools.ttLib import TTFont
from PySide6.QtWidgets import QApplication

# The families that ship with a clean Windows 10 install (Microsoft Typography, "Fonts included
# in Windows 10"). Fallback detection may consult ONLY these plus the primary app font; a user's
# own third-party fonts are excluded. This is the COMPLETE default set — we use all of it and
# only it.
DEFAULT_WINDOWS_FONTS = [
    # ---- Latin / western core ----
    "Arial", "Arial Black", "Arial Narrow", "Arial Rounded MT Bold",
    "Bahnschrift", "Baskerville Old Face", "Bell MT", "Berlin Sans FB",
    "Bernard MT Condensed", "Blackadder ITC", "Bodoni MT", "Book Antiqua",
    "Bookman Old Style", "Bradley Hand ITC", "Britannic Bold", "Broadway",
    "Brush Script MT", "Calibri", "Calibri Light", "Californian FB",
    "Calisto MT", "Cambria", "Cambria Math", "Candara", "Century",
    "Century Gothic", "Century Schoolbook", "Chiller", "Colonna MT",
    "Comic Sans MS", "Consolas", "Constantia", "Cooper Black",
    "Copperplate Gothic Bold", "Copperplate Gothic Light", "Corbel",
    "Courier New", "Curlz MT", "Edwardian Script ITC", "Elephant",
    "Engravers MT", "Eras", "Felix Titling", "Footlight MT Light", "Forte",
    "Franklin Gothic Book", "Franklin Gothic Demi", "Franklin Gothic Heavy",
    "Franklin Gothic Medium", "Freestyle Script", "French Script MT",
    "Gabriola", "Garamond", "Georgia", "Gigi", "Gill Sans MT",
    "Gloucester MT Extra Condensed", "Goudy Old Style", "Goudy Stout",
    "Harlow Solid Italic", "Haettenschweiler", "Harrington",
    "High Tower Text", "Impact", "Imprint MT Shadow", "Informal Roman",
    "Ink Free", "Kristen ITC", "Kunstler Script", "Lucida Bright",
    "Lucida Calligraphy", "Lucida Console", "Lucida Fax",
    "Lucida Handwriting", "Lucida Sans", "Lucida Sans Unicode",
    "Lucida Sans Typewriter", "Magneto", "Maiandra GD", "Marlett",
    "Matura MT Script Capitals", "Microsoft Sans Serif",
    "Modern No. 20", "MT Extra", "Niagara Engraved", "Niagara Solid",
    "OCR A Extended", "Old English Text MT", "Onyx", "Palatino Linotype",
    "Papyrus", "Parchment", "Perpetua", "Perpetua Titling MT",
    "Playbill", "Poor Richard", "Pristina", "Rage Italic", "Ravie",
    "Rockwell", "Rockwell Condensed", "Rockwell Extra Bold",
    "Script MT Bold", "Segoe Print", "Segoe Script", "Segoe UI",
    "Segoe UI Black", "Segoe UI Emoji", "Segoe UI Historic", "Segoe UI Light",
    "Segoe UI Semibold", "Segoe UI Semilight", "Segoe UI Symbol",
    "Showcard Gothic", "Sitka Banner", "Sitka Display", "Sitka Heading",
    "Sitka Small", "Sitka Subheading", "Sitka Text", "Snap ITC", "Stencil",
    "Sylfaen", "Symbol", "Tahoma", "Tempus Sans ITC", "Times New Roman",
    "Trebuchet MS", "Tw Cen MT", "Tw Cen MT Condensed", "Verdana",
    "Viner Hand ITC", "Vivaldi", "Vladimir Script", "Webdings",
    "Wide Latin", "Wingdings", "Wingdings 2", "Wingdings 3",
    "Yu Gothic", "Yu Gothic UI", "Yu Mincho",
    # ---- Non-Latin / language fonts ----
    "Ebrima", "Gadugi", "Javanese Text", "Malgun Gothic",
    "Microsoft Himalaya", "Microsoft JhengHei", "Microsoft JhengHei UI",
    "Microsoft New Tai Lue", "Microsoft PhagsPa", "Microsoft Tai Le",
    "Microsoft Uighur", "Microsoft YaHei", "Microsoft YaHei UI",
    "Microsoft Yi Baiti", "MingLiU", "MingLiU-ExtB", "MingLiU_HKSCS",
    "MingLiU_HKSCS-ExtB", "Mongolian Baiti", "MS Gothic", "MS PGothic",
    "MS UI Gothic", "MV Boli", "Nirmala UI", "NSimSun", "PMingLiU",
    "PMingLiU-ExtB", "SimSun", "SimSun-ExtB", "Leelawadee UI",
    "Myanmar Text", "Arabic Typesetting", "Aldhabi", "Arial Unicode MS",
    "Kartika", "Khmer UI", "Lao UI", "Leelawadee", "Narkisim", "Nyala",
    "Sakkal Majalla", "Traditional Arabic", "Urdu Typesetting",
    "Meiryo", "Meiryo UI", "MS Mincho", "Thai Sans", "Thai Sans NE",
    "HoloLens MDL2 Assets", "Segoe MDL2 Assets", "Segoe Fluent Icons",
]

_family_cps: dict[str, set] | None = None
_primary_cps: set = set()

# Extra intentional fallback fonts for the hanzi/kanji (CJK ideograph) blocks. These are NOT
# OS-default fonts, but the Hanazono Mincho ("HanaMin") faces cover the ideograph blocks far more
# completely than any OS font, so they are attached for those blocks after the OS defaults and
# before Unifont. Attached only when the font is actually present and its cmap covers the
# codepoint (so nothing is assumed about what is installed).
HANZI_FALLBACK_FONTS = ["HanaMinA", "HanaMinB"]
_HAN_BLOCK_PREFIXES = ("CJK Unified Ideographs", "CJK Compatibility Ideographs")

# Families whose cmaps the disk scan reads: OS defaults plus the hanzi/kanji extras.
_ACCEPTED_FAMILIES = set(DEFAULT_WINDOWS_FONTS) | set(HANZI_FALLBACK_FONTS)


def _is_han_block(cp: int) -> bool:
    """True for the hanzi/kanji (CJK ideograph) blocks."""
    from ..unicode_blocks import block_name
    try:
        return block_name(cp).startswith(_HAN_BLOCK_PREFIXES)
    except Exception:
        return False


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
    """family -> set(codepoints) for the OS-default fallback candidates + the primary font."""
    global _family_cps, _primary_cps
    if _family_cps is not None:
        return _family_cps
    cps: dict[str, set] = {}
    app = QApplication.instance()
    primary = app.font().family() if app is not None else "Segoe UI"
    # Read the cmaps of every font installed in the OS font dirs, but only keep families that are
    # DEFAULT Windows fonts (plus the primary app font's own cmap). User-installed third-party
    # fonts are dropped.
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
                if fam in _ACCEPTED_FAMILIES:
                    cps.setdefault(fam, set()).update(_cps(tt))
                if fam == primary:
                    _primary_cps |= _cps(tt)
                tt.close()
            except Exception:
                continue
    _family_cps = cps
    return _family_cps


def native_text_families(cp: int) -> list[str]:
    """Native-text family list for a codepoint.

    Consults only the OS-default Windows fonts (and the primary app font) for real cmap coverage.
    Returns just the primary family when the system (primary + its own platform chain) already
    renders the codepoint, prepends the relevant OS-default font(s) for scripts the primary can't
    render, and appends Unifont as the absolute last resort.
    """
    from .perflog import count, span
    count("fonts.native_families")
    with span("fonts.native_families"):
        return _native_text_families(cp)


# Codepoint -> the family Qt resolves it to, or None when nothing on the system can draw it.
# Session-global: resolving is the expensive part and the answer does not depend on the pixel size.
_resolved: dict[int, Optional[str]] = {}


def native_text_resolution(cp: int) -> Optional[str]:
    """The family Qt draws ``cp`` with, through the codepoint's chain and its platform fallback.

    ``None`` when nothing on this system can draw it. Resolving is the expensive part — the
    platform searches the installed font collection, measured at 15 ms to 3.5 s, and does not keep
    the answer — and it is the SAME resolution at every pixel size. So it is asked once per
    codepoint and remembered, because the cell badge (13 px), the native reference panel (110 px)
    and the native ghost (160 px) all draw the same character and would otherwise each ask Qt the
    same question again, back to back.
    """
    cp = int(cp)
    if cp in _resolved:
        return _resolved[cp]
    from PySide6.QtGui import QFont, QTextLayout
    f = QFont()
    f.setFamilies(native_text_families(cp))
    f.setPixelSize(13)
    layout = QTextLayout(chr(cp), f)
    layout.beginLayout()
    line = layout.createLine()
    line.setLineWidth(64.0)
    layout.endLayout()
    family: Optional[str] = None
    runs = line.glyphRuns()
    if runs:
        gids = runs[0].glyphIndexes()
        if gids and int(gids[0]) != 0:
            family = runs[0].rawFont().familyName() or None
    _resolved[cp] = family
    return family


def native_text_unmapped(cp: int, chain: list[str] | None = None) -> bool:
    """True when nothing Qt could draw this codepoint with: not the chain, not its fallback."""
    return native_text_resolution(cp) is None


def _native_text_families(cp: int) -> list[str]:
    cps = _load_family_cps()
    app = QApplication.instance()
    primary = app.font().family() if app is not None else "Segoe UI"
    # The primary font (Segoe UI) genuinely covers it -> its own stack handles it; no rare font.
    if cp in _primary_cps:
        return [primary]
    # Attach every OS-default font whose cmap covers this codepoint so the script is rendered
    # through a font that truly supports it (e.g. Ebrima/Nyala for Ethiopic, JhengHei/YaHei for
    # CJK). If none is needed, fall through to Qt's own platform chain.
    relevant: list[str] = []
    for fam in DEFAULT_WINDOWS_FONTS:
        if fam in ("Unifont", "Unifont Upper"):
            continue
        if cp in cps.get(fam, ()):
            relevant.append(fam)
    # hanzi/kanji blocks: also attach the HanaMin faces (when present and covering the
    # codepoint), after the OS defaults so an OS font that covers the character still wins, and
    # before Unifont.
    han = ([f for f in HANZI_FALLBACK_FONTS if cp in cps.get(f, ())]
           if _is_han_block(cp) else [])
    if not relevant and not han:
        return [primary]
    return [primary] + relevant + han + ["Unifont", "Unifont Upper"]
