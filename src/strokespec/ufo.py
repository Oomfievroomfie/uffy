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

from .geometry import Op, glyph_contours
from .model import (
    GRID_W,
    GRID_H,
    SCALE,
    UPEM,
    StrokeFont,
    Glyph,
    PEN_RADIUS,
)


def glyph_name(codepoint: int) -> str:
    """AGLFN-compatible Unicode glyph name."""
    if codepoint <= 0xFFFF:
        return "uni%04X" % codepoint
    return "u%X" % codepoint


def op_to_pen(ops: List[Op], pen) -> None:
    """Feed a list of M/L/C/Z ops into a segment pen."""
    for op in ops:
        kind = op[0]
        if kind == "M":
            pen.moveTo(op[1])
        elif kind == "L":
            pen.lineTo(op[1])
        elif kind == "C":
            pen.curveTo(op[1], op[2], op[3])
        elif kind == "Z":
            pen.closePath()


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
    cap: str = "round",
    pen_radius: int = PEN_RADIUS,
    notdef_width_units: Optional[int] = None,
) -> str:
    """Build a UFO at ``output_dir`` and return its path.

    ``output_dir`` must not already exist (or will be overwritten if it is a UFO).
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
    font.lib["com.strokespec.penRadius"] = str(pen_radius)
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
        for contour in glyph_contours(glyph, pen_radius, cap, baseline):
            op_to_pen(contour, pen)
        order.append(name)

    # Set the glyph layout order explicitly so the font is deterministic.
    font.glyphOrder = order

    if os.path.exists(output_dir):
        # ufoLib2 will overwrite a UFO at path only when it is already a UFO; be safe.
        pass
    font.save(output_dir, overwrite=True)
    return output_dir
