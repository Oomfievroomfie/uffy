"""Emit glyphs as stroked SVG (strokes -> stroked ``<path>`` elements).

The *source of truth* for the font is the stroke model, and the stroke->outline
expansion is our own (see :mod:`strokespec.geometry`). This module is the SVG face of the
same model: it emits the strokes as a stroked SVG for debugging/inspection, where a straight
segment is ``M/L`` (butt) and an arc is a **single quadratic** per quarter (``Q``,
butt cap) with its control point at the box bulge corner. It feeds nothing — the compiled
font and the editor preview both use the geometry expansion directly.
"""

from __future__ import annotations

from typing import List, Tuple, Union

from .geometry import (
    arc_end_tangents,
    arc_control,
    arc_degenerates_to_line,
    flatten_cell_strokes,
)
from .model import (
    SHAPE_ARC,
    DEFAULT_BASELINE,
    GRID_H,
    SCALE,
    UPEM,
    Glyph,
    PEN_RADIUS,
)

Pt = Tuple[float, float]


# --- SVG emission ------------------------------------------------------------
def _stroke_svg_d_pts(
    p1f: Pt, p2f: Pt, shape: str, r: float, to_svg
) -> Tuple[str, str]:
    """Return ``(path_d, line_butt)`` for one stroke given in font units.

    ``to_svg`` maps a font point to an SVG point (y-down). A line (or a degenerate/axis-aligned
    arc, which *is* a line) is an ``M/L``; a genuine arc is a butt *``Q``* (control at the box
    corner) with its ends nudged forward by the pen radius. No square-butt SVG linecap is used:
    the geometry bakes the correct butt length directly (see ``strokespec.geometry``).
    """
    if shape == SHAPE_ARC:
        if arc_degenerates_to_line(p1f, p2f):
            x1, y1 = to_svg(p1f)
            x2, y2 = to_svg(p2f)
            return f"M {x1:.3f} {y1:.3f} L {x2:.3f} {y2:.3f}", "butt"
        # flat butt, ends nudged forward by the pen radius along the axial tangents
        t1, t2 = arc_end_tangents(p1f, p2f)
        p1n = (p1f[0] - r * t1[0], p1f[1] - r * t1[1])
        p2n = (p2f[0] + r * t2[0], p2f[1] + r * t2[1])
        C = arc_control(p1n, p2n)
        x1, y1 = to_svg(p1n)
        cx, cy = to_svg(C)
        x2, y2 = to_svg(p2n)
        return f"M {x1:.3f} {y1:.3f} Q {cx:.3f} {cy:.3f} {x2:.3f} {y2:.3f}", "butt"
    x1, y1 = to_svg(p1f)
    x2, y2 = to_svg(p2f)
    return f"M {x1:.3f} {y1:.3f} L {x2:.3f} {y2:.3f}", "butt"


def glyph_svg(
    glyph: Glyph,
    pen_radius: float = PEN_RADIUS,
    baseline: float = DEFAULT_BASELINE,
    ascent: float | None = None,
    resolve=None,
) -> str:
    """Return an SVG whose path carries all of ``glyph``'s strokes.

    Subcomponents are emitted as the strokes they instance (transformed into this glyph's cell
    space, see ``geometry.flatten_cell_strokes``), which is exactly what the real expansion
    consumes.

    Coordinates are emitted in *SVG* space (y-down): ``svg_y = ascent - font_y`` so the
    glyph's baseline sits at ``y = ascent``. picosvg-free SVG for inspection only (the font
    and the preview use the geometry directly). No square-butt SVG linecap is used.
    """
    if ascent is None:
        ascent = (GRID_H - baseline) * SCALE
    width = glyph.cell_width_units

    def to_svg(p: Pt) -> Pt:
        return (p[0], ascent - p[1])

    r = pen_radius

    def cell_to_font(p) -> Pt:
        return (p[0] * SCALE, (p[1] - baseline) * SCALE)

    ds = [
        _stroke_svg_d_pts(cell_to_font(p1), cell_to_font(p2), shape, r, to_svg)[0]
        for p1, p2, shape in flatten_cell_strokes(glyph, resolve)
    ]
    if not ds:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{UPEM:.0f}" '
            f'viewBox="0 0 {width:.0f} {UPEM:.0f}"></svg>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{UPEM:.0f}" '
        f'viewBox="0 0 {width:.0f} {UPEM:.0f}">'
        f'<path d="{" ".join(ds)}" fill="none" stroke="black" stroke-width="{2*r:.3f}" '
        f'stroke-linecap="butt" stroke-linejoin="round"/></svg>'
    )
