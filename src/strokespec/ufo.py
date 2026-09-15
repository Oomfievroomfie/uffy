"""Build a UFO from a :class:`StrokeFont`.

This writes the UFO *authoring* format only (via ``ufoLib2``). It never reads or writes a
TTF/OTF itself — that is left to Google's CLI compiler (``gftools``/``fontmake``) so the
binary font tables are produced by the sanctioned tools rather than by hand.
"""

from __future__ import annotations

import os
from typing import Callable, List, Optional

from ufoLib2 import Font
from ufoLib2.objects import Glyph as UFOGlyph

from .model import GRID_H, SCALE, UPEM, StrokeFont, Glyph, PEN_RADIUS, PEN_CAP
from .geometry import glyph_contours
from .geometry_merge import merge_stroke_edges

# Op is a tuple ("M"/"L"/"Q"/"C"/"Z", ...) — reused from geometry.
Op = tuple

# Devanagari vowel signs the shaper reorders to sit BEFORE their consonant (verified against
# HarfBuzz: U+093F VOWEL SIGN I and U+094E VOWEL SIGN PRISHTHAMATRA E are the only two).
DEVANAGARI_PREBASE_MATRAS = {0x093F, 0x094E}


def _translate_x(ops: list, dx: float) -> list:
    """Shift x by ``dx`` in every M/L/Q/C op."""
    out = []
    for op in ops:
        k = op[0]
        if k in ("M", "L"):
            out.append((k, (op[1][0] + dx, op[1][1])))
        elif k == "Q":
            out.append((k, (op[1][0] + dx, op[1][1]), (op[2][0] + dx, op[2][1])))
        elif k == "C":
            out.append((k, (op[1][0] + dx, op[1][1]), (op[2][0] + dx, op[2][1]),
                        (op[3][0] + dx, op[3][1])))
        else:
            out.append(op)
    return out


def glyph_name(codepoint: int) -> str:
    """AGLFN-compatible Unicode glyph name."""
    if codepoint <= 0xFFFF:
        return "uni%04X" % codepoint
    return "u%X" % codepoint


def _outline_key(contours) -> tuple:
    """A hashable identity for an outline (list of contour op lists).

    Ops are tuples of hashable primitives (``('M', (x, y))`` etc.), so the whole sequence is
    hashable — glyphs with identical outlines share a key and can be merged.
    """
    parts = []
    for contour in contours:
        parts.extend(op for op in contour)
        parts.append(("@contour",))
    return tuple(parts)


def op_to_pen(ops: List[Op], pen) -> None:
    """Feed a list of M/L/Q/C/Z ops into a segment pen (supports quadratic+curves)."""
    for op in ops:
        kind = op[0]
        if kind == "M":
            pen.moveTo(op[1])
        elif kind == "L":
            pen.lineTo(op[1])
        elif kind == "C":
            pen.curveTo(op[1], op[2], op[3])
        elif kind == "Q":
            # store as TrueType-style quadratic qcurve points so they round-trip to glyf
            # quadratics (the arc is one quadratic per quarter, so keep it exactly).
            pen.qCurveTo(op[1], op[2])
        elif kind == "Z":
            pen.closePath()


def glyph_outline_from_strokes(glyph: Glyph, pen_radius: float, baseline: float) -> List[List[Op]]:
    """Expand a glyph's strokes to fill outlines from our own stroke geometry.

    This is the canonical stroke expansion (each stroke swept by the pen into a closed
    contour, one contour per stroke, no boolean merge — overlapping strokes stay as
    redundant subpaths and union visually under the non-zero winding rule). The editor preview
    uses the same path, so preview and compiled font can never differ.
    """
    return glyph_contours(glyph, r=pen_radius, baseline=baseline)


def _notdef_ops(width_units: int, cap: str, r: float, descent: float, ascent: float) -> List[List[Op]]:
    """A single solid rectangle tofu glyph so .notdef is clearly visible.

    A single contour (no reversed/inner contour) keeps the fill unambiguous under the
    non-zero winding rule — no even-odd or hand-managed winding tricks.
    """
    pad = r * 2
    x0, x1 = pad, width_units - pad
    y0 = -descent + r * 3
    y1 = ascent - r * 3
    if y1 - y0 < r * 4 or x1 - x0 < r * 4:
        return [[
            ("M", (pad, -descent + pad)),
            ("L", (width_units - pad, -descent + pad)),
            ("L", (width_units - pad, ascent - pad)),
            ("L", (pad, ascent - pad)),
            ("Z",),
        ]]
    return [[
        ("M", (x0, y0)),
        ("L", (x1, y0)),
        ("L", (x1, y1)),
        ("L", (x0, y1)),
        ("Z",),
    ]]


def make_ufo(
    strokefont: StrokeFont,
    *,
    family_name: Optional[str] = None,
    style_name: Optional[str] = "Regular",
    cap: str = PEN_CAP,
    pen_radius: int = PEN_RADIUS,
    notdef_width_units: Optional[int] = None,
    progress: Optional[Callable[[int, int], None]] = None,
    merge_edges: bool = True,
) -> Font:
    """Build the UFO **in memory** and return the ``ufoLib2.Font``.

    Keeping the font object lets the compiler hand it straight to ufo2ft, avoiding writing and
    re-reading a UFO package with one ``.glif`` file per glyph (which dominates build time for a
    font this size). Use :func:`build_ufo` when a UFO on disk is actually wanted.
    """
    if family_name is None:
        family_name = strokefont.metadata.get("name", "Uffy Fallback")

    baseline = strokefont.baseline
    x_height = strokefont.x_height
    cap_height = strokefont.cap_height
    # baseline sits ``baseline`` cells above the em-box bottom; font y-up has baseline at 0.
    ascent = (GRID_H - baseline) * SCALE
    descent = baseline * SCALE

    font = Font()
    info = font.info
    info.unitsPerEm = UPEM
    info.ascender = round(ascent)
    info.descender = round(-descent)
    info.capHeight = round(cap_height * SCALE)
    info.xHeight = round(x_height * SCALE)
    info.familyName = family_name
    info.styleName = style_name
    info.versionMajor = 1
    info.versionMinor = 0
    info.copyright = (
        "Copyright (C) 2024 strokespec. "
        "Designed as a stroke-defined fallback font."
    )
    info.openTypeHeadCreated = "2024/01/01 00:00:00"
    info.postscriptUnderlinePosition = -(UPEM // 8)
    info.postscriptUnderlineThickness = SCALE // 2
    # Two ufo2ft defaults differ from what an ordinary horizontal font carries, and both are
    # wrong for us:
    #  * openTypeHheaCaretSlopeRise defaults to UPEM (256 here). The spec wants 1 with slopeRun 0
    #    for a font with a horizontal baseline (a vertical caret); every ordinary font, Unifont
    #    included, has 1.
    #  * openTypeOS2Type defaults to [2] -> fsType 4 = "Preview & Print embedding" (embedding
    #    restricted). Unifont ships fsType 0 (installable), which is right for a libre font.
    info.openTypeHheaCaretSlopeRise = 1
    info.openTypeHheaCaretSlopeRun = 0
    info.openTypeOS2Type = []

    font.lib["com.strokespec.type"] = "stroke-fallback"
    font.lib["com.strokespec.penRadius"] = str(PEN_RADIUS)
    font.lib["com.strokespec.cap"] = cap
    # Drop per-glyph PostScript names from the compiled font. The 'post' table is otherwise
    # written in format 2.0 with a glyph name for every glyph (one per cmap entry), which grows
    # enormously for a large fallback font (it was ~1/10th of the compiled file). Format 3.0
    # keeps a valid but near-empty post table; name/post are still present, just minimal. The
    # glyphs stay addressable via the cmap, which is all a fallback font needs.
    font.lib["com.github.googlei18n.ufo2ft.keepGlyphNames"] = False

    def expand(glyph: Glyph) -> List[List[Op]]:
        conts = glyph_outline_from_strokes(glyph, pen_radius, baseline)
        if merge_edges:
            conts = merge_stroke_edges(conts)
        # Combining glyphs: place the ink one cell to the LEFT, as Unifont does (its combining
        # marks have zero advance with negative-x outlines, e.g. U+093F ink at x -1024..-320).
        # A zero-advance mark is drawn at the pen, which is already past its base, so shifting the
        # ink left by the glyph's own cell width lands it back on the base's cell — no GPOS and no
        # mark/abvm/mkmk needed. Exception: the Devanagari pre-base matras, which the shaper
        # reorders to sit *before* the base: there the pen is already at the base's cell, so they
        # are left unshifted (their ink then overlays the base's cell, as authored).
        if glyph.combining and glyph.codepoint not in DEVANAGARI_PREBASE_MATRAS:
            dx = -glyph.width * SCALE
            conts = [_translate_x(c, dx) for c in conts]
        return conts

    # .notdef always first.
    nd_cmds = _notdef_ops(UPEM, cap, pen_radius, descent, ascent)
    notdef = font.newGlyph(".notdef")
    notdef.unicode = None
    notdef.width = notdef_width_units or UPEM
    pen = notdef.getPen()
    for contour in nd_cmds:
        op_to_pen(contour, pen)

    order: List[str] = [".notdef"]

    # Reserve the classic Macintosh glyph-order slots: glyph 1 must be `.null` and glyph 2
    # `nonmarkingreturn` — both zero-advance and outline-less. Renderers and converters still
    # assume glyph 1 is `.null` (zero advance), so leaving a real glyph there silently zeroes its
    # advance. Without these our lowest codepoint — U+0020 SPACE — lands in slot 1, and the ASCII
    # space stops advancing (invisible at the end of a string, caret does not move) even though its
    # hmtx entry is correct. Unifont ships these two slots for exactly this reason.
    for _slot in (".null", "nonmarkingreturn"):
        _dummy = font.newGlyph(_slot)
        _dummy.unicodes = []
        _dummy.width = 0
        order.append(_slot)

    cps = strokefont.codepoints()
    total = len(cps)
    # Merge glyphs with identical outlines (same contour ops AND same advance width) into a
    # single glyph that all those codepoints map to, so the compiled font doesn't store a
    # duplicate outline for each duplicated codepoint. Group key = (outline ops, advance units).
    # **Empty glyphs are never merged**: each codepoint keeps its own glyph. There is nothing to
    # save (an empty glyph is just a loca entry and an hmtx row), and sharing one glyph across
    # many codepoints is a needless departure from ordinary fonts — e.g. it made U+0020 share a
    # glyph with U+061C/U+2000–U+200A/U+202F/U+205F/U+FFA0.
    groups: "dict[Any, List[int]]" = {}
    for i, cp in enumerate(cps):
        if progress is not None:
            progress(i, total)
        glyph = strokefont.get(cp)
        if glyph is None:
            continue
        contours = expand(glyph)
        key = ("empty", cp) if not contours else (_outline_key(contours), glyph.advance_units)
        groups.setdefault(key, []).append(cp)

    for key, group in groups.items():
        rep_cp = group[0]
        rep_glyph = strokefont.get(rep_cp)
        name = glyph_name(rep_cp)
        ufo_glyph = font.newGlyph(name)
        ufo_glyph.unicodes = group          # map every codepoint in the group to this glyph
        ufo_glyph.width = rep_glyph.advance_units
        pen = ufo_glyph.getPen()
        contours = expand(rep_glyph)
        for contour in contours:
            op_to_pen(contour, pen)
        order.append(name)

    # Give the font a de-facto-empty GPOS table. HarfBuzz only runs its extents-based fallback
    # mark positioning when the face has NO GPOS positioning at all
    # (`plan.apply_gpos = hb_ot_layout_has_positioning(face)`; `fallback_mark_positioning =
    # !apply_gpos`), and when it runs it overrides the ink placement we control. (Unifont ships a
    # bare GPOS table for this reason.)
    #
    # It is emitted as a STANDALONE lookup that no feature references: feaLib then writes a GPOS
    # with an empty FeatureList and an empty ScriptList, so no software reports any feature as
    # existing, while the table itself is still present and non-empty. The rule covers an
    # unreachable phantom glyph (no cmap entry, no outline), so no reachable glyph carries any
    # positioning data either.
    phantom = font.newGlyph(".gposphantom")
    phantom.unicodes = []
    phantom.width = 0
    order.append(".gposphantom")
    font.features.text = "lookup gpos_noop {\n    pos .gposphantom <1 0 0 0>;\n} gpos_noop;\n"

    # Set the glyph layout order explicitly so the font is deterministic.
    font.glyphOrder = order
    return font


def build_ufo(strokefont: StrokeFont, output_dir: str, **kwargs) -> str:
    """Build the UFO (see :func:`make_ufo`) and save it at ``output_dir``; return that path."""
    font = make_ufo(strokefont, **kwargs)
    font.save(output_dir, overwrite=True)
    return output_dir


