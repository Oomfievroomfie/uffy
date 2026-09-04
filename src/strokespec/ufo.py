"""Build a UFO from a :class:`StrokeFont`.

This writes the UFO *authoring* format only (via ``ufoLib2``). It never reads or writes a
TTF/OTF itself — that is left to Google's CLI compiler (``gftools``/``fontmake``) so the
binary font tables are produced by the sanctioned tools rather than by hand.
"""

from __future__ import annotations

import os
from typing import List, Optional

from ufoLib2 import Font
from ufoLib2.objects import Glyph as UFOGlyph

import re

from .model import GRID_W, GRID_H, SCALE, UPEM, StrokeFont, Glyph, PEN_RADIUS, PEN_CAP
from .svgout import glyph_svg, parse_svg_d
from picosvg.svg import SVG

# Op is a tuple ("M"/"L"/"C"/"Q"/"Z", ...) — reused from svgout.
Op = tuple


def glyph_name(codepoint: int) -> str:
    """AGLFN-compatible Unicode glyph name."""
    if codepoint <= 0xFFFF:
        return "uni%04X" % codepoint
    return "u%X" % codepoint


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
            # picosvg emits quadratic beziers; store as TrueType-style qcurve points so they
            # round-trip to glyf quadratics. qCurveTo(control, oncurve).
            pen.qCurveTo(op[1], op[2])
        elif kind == "Z":
            pen.closePath()


def glyph_outline_from_strokes(
    glyph: Glyph, pen_radius: float, baseline: float, *, per_stroke: bool = True
) -> List[List[Op]]:
    """Expand a glyph's strokes to outlines using Google's **picosvg** (shared with the preview).

    With ``per_stroke=True`` (default) picosvg runs on each stroke independently and the
    outlines are concatenated with **no boolean merge**, keeping overlapping strokes as
    redundant subpaths (produces a smaller TTF — see
    :func:`strokespec.svgout.glyph_contours_svg_per_stroke`). With ``per_stroke=False`` the
    whole glyph is fed to picosvg at once and overlapping strokes are boolean-unioned.
    """
    if per_stroke:
        from .svgout import glyph_contours_svg_per_stroke
        return glyph_contours_svg_per_stroke(glyph, pen_radius, baseline)
    from .svgout import glyph_contours_svg
    return glyph_contours_svg(glyph, pen_radius, baseline)


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


def build_ufo(
    strokefont: StrokeFont,
    output_dir: str,
    *,
    family_name: Optional[str] = None,
    style_name: Optional[str] = "Regular",
    cap: str = PEN_CAP,
    pen_radius: int = PEN_RADIUS,
    notdef_width_units: Optional[int] = None,
    per_stroke: bool = True,
) -> str:
    """Build a UFO at ``output_dir`` and return its path.

    ``output_dir`` must not already exist (or will be overwritten if it is a UFO).
    ``per_stroke`` selects the expansion: ``True`` (default) is the no-boolean-merge
    per-stroke picosvg expansion; ``False`` boolean-unions the whole glyph.
    """
    if family_name is None:
        family_name = strokefont.metadata.get("name", "strokespec")

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

    font.lib["com.strokespec.type"] = "stroke-fallback"
    font.lib["com.strokespec.penRadius"] = str(PEN_RADIUS)
    font.lib["com.strokespec.cap"] = cap

    # .notdef always first.
    nd_cmds = _notdef_ops(UPEM, cap, pen_radius, descent, ascent)
    notdef = font.newGlyph(".notdef")
    notdef.unicode = None
    notdef.width = notdef_width_units or UPEM
    pen = notdef.getPen()
    for contour in nd_cmds:
        op_to_pen(contour, pen)

    order: List[str] = [".notdef"]
    for cp in strokefont.codepoints():
        glyph = strokefont.get(cp)
        if glyph is None:
            continue
        name = glyph_name(cp)
        ufo_glyph = font.newGlyph(name)
        ufo_glyph.unicode = cp
        ufo_glyph.width = glyph.advance_units
        pen = ufo_glyph.getPen()
        try:
            contours = glyph_outline_from_strokes(glyph, pen_radius, baseline, per_stroke=per_stroke)
        except Exception:
            contours = []  # picosvg couldn't expand it (e.g. degenerate) -> leave empty
        for contour in contours:
            op_to_pen(contour, pen)
        order.append(name)

    # Set the glyph layout order explicitly so the font is deterministic.
    font.glyphOrder = order

    if os.path.exists(output_dir):
        # ufoLib2 will overwrite a UFO at path only when it is already a UFO; be safe.
        pass
    font.save(output_dir, overwrite=True)
    return output_dir


