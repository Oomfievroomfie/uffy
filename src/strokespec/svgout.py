"""Emit glyphs as stroked SVG (strokes -> stroked ``<path>`` elements).

The *source of truth* for the font is the stroke model, and the stroke->outline
expansion is our own (see :mod:`strokespec.geometry`). This module is the SVG face of the
same model: it emits the strokes as a stroked SVG for debugging/inspection, where a straight
segment is ``M/L`` (square cap) and an arc is a **single quadratic** per quarter (``Q``,
butt cap) with its control point at the box bulge corner. It feeds nothing — the compiled
font and the editor preview both use the geometry expansion directly.
"""

from __future__ import annotations

from typing import List, Tuple, Union

from .geometry import arc_end_tangents, arc_control, arc_degenerates_to_line
from .model import SHAPE_ARC, DEFAULT_BASELINE, UPEM, Glyph, Stroke, PEN_RADIUS

Pt = Tuple[float, float]


# --- SVG emission ------------------------------------------------------------
def _stroke_svg_d(
    s: Stroke, baseline: float, r: float, to_svg
) -> Tuple[str, str]:
    """Return ``(path_d, linecap)`` for a single stroke, in SVG space.

    ``to_svg`` maps a font point to an SVG point (y-down). A line (or a degenerate/axis-aligned
    arc, which *is* a line) is a square-capped ``M/L``; a genuine arc is a butt-capped
    single-quadratic ``Q`` (control at the box corner) with its ends nudged forward by the pen
    radius.
    """
    p1f = s.p1.as_font_units(baseline)
    p2f = s.p2.as_font_units(baseline)
    if s.shape == SHAPE_ARC:
        if arc_degenerates_to_line(p1f, p2f):
            x1, y1 = to_svg(p1f)
            x2, y2 = to_svg(p2f)
            return f"M {x1:.3f} {y1:.3f} L {x2:.3f} {y2:.3f}", "square"
        # butt cap, ends nudged forward by the pen radius along the axial tangents
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
    return f"M {x1:.3f} {y1:.3f} L {x2:.3f} {y2:.3f}", "square"


def glyph_svg(
    glyph: Glyph,
    pen_radius: float = PEN_RADIUS,
    baseline: float = DEFAULT_BASELINE,
    ascent: float | None = None,
) -> str:
    """Return an SVG whose path(s) carry all of ``glyph``'s strokes.

    Coordinates are emitted in *SVG* space (y-down): ``svg_y = ascent - font_y`` so the
    glyph's baseline sits at ``y = ascent``. Lines and arcs are grouped by linecap; stale
    picosvg-free SVG for inspection.
    """
    if ascent is None:
        ascent = (16 - baseline) * 64
    width = glyph.cell_width_units

    def to_svg(p: Pt) -> Pt:
        return (p[0], ascent - p[1])

    r = pen_radius
    lines: List[str] = []
    arcs: List[str] = []
    for s in glyph.strokes:
        d, cap = _stroke_svg_d(s, baseline, r, to_svg)
        if cap == "square":
            lines.append(d)
        else:
            arcs.append(d)

    paths: List[str] = []
    if lines:
        paths.append(
            f'<path d="{" ".join(lines)}" fill="none" stroke="black" stroke-width="{2*r:.3f}" '
            f'stroke-linecap="square" stroke-linejoin="round"/>'
        )
    if arcs:
        paths.append(
            f'<path d="{" ".join(arcs)}" fill="none" stroke="black" stroke-width="{2*r:.3f}" '
            f'stroke-linecap="butt" stroke-linejoin="round"/>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{UPEM:.0f}" '
        f'viewBox="0 0 {width:.0f} {UPEM:.0f}">' + "".join(paths) + "</svg>"
    )
