"""Emit glyphs as stroked SVG and convert stroke-expanded results to outlines.

The *source of truth* for the font is the stroke model. For the binary we hand each glyph to
**picosvg** (Google Fonts) as a stroked SVG path — a straight segment is ``M/L``, a
quarter-ellipse is an SVG elliptical-arc ``A`` — and picosvg expands the strokes (including
the round caps and the arc) into filled outlines. We then parse that outline back into
font-unit contours for a UFO, which ``fontmake``/``ufo2ft`` compiles to a TTF.

This keeps my own geometry as the *preview* only; the compiled font is produced by Google's
tools from strokes.
"""

from __future__ import annotations

import math
import re
from typing import List, Tuple, Union, Iterable

from .geometry import arc_end_tangents
from .model import SHAPE_ARC, SHAPE_LINE, DEFAULT_BASELINE, UPEM, Glyph, Stroke, PEN_RADIUS
from picosvg.svg import SVG

Pt = Tuple[float, float]
# An op is ("M"/"L"/"C"/"Q"/"Z", ...) in font units (y-up, baseline 0).
Op = Union[Tuple, tuple]


# --- SVG emission ------------------------------------------------------------
def glyph_svg(
    glyph: Glyph,
    pen_radius: float = PEN_RADIUS,
    baseline: float = DEFAULT_BASELINE,
    ascent: float | None = None,
) -> str:
    """Return an SVG whose single path carries all of ``glyph``'s strokes.

    Coordinates are emitted in *SVG* space (y-down): ``svg_y = ascent - font_y`` so the
    glyph's baseline sits at ``y = ascent``. picosvg expands the strokes to fills.
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
        p1f = s.p1.as_font_units(baseline)
        p2f = s.p2.as_font_units(baseline)
        if s.shape == SHAPE_ARC:
            # arcs: FLAT (butt) cap, with the ends nudged forward by the pen radius along the
            # tangent. The nudge is intentional: the arc re-fits to the nudged endpoint, so the
            # end cuts perpendicular to the *curve* there — a genuinely different shape from a
            # half-square (straight tangent) cap.
            t1, t2 = arc_end_tangents(p1f, p2f)
            p1n = (p1f[0] - r * t1[0], p1f[1] - r * t1[1])
            p2n = (p2f[0] + r * t2[0], p2f[1] + r * t2[1])
            x1, y1 = to_svg(p1n)
            x2, y2 = to_svg(p2n)
            dx = abs(x2 - x1)
            dy = abs(y2 - y1)
            arcs.append(f"M {x1:.3f} {y1:.3f} A {max(dx, 1e-6):.3f} {max(dy, 1e-6):.3f} 0 0 0 {x2:.3f} {y2:.3f}")
        else:
            x1, y1 = to_svg(p1f)
            x2, y2 = to_svg(p2f)
            lines.append(f"M {x1:.3f} {y1:.3f} L {x2:.3f} {y2:.3f}")

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


# --- SVG path -> font-unit ops ----------------------------------------------
def _number(s: str, i: int) -> Tuple[float, int]:
    """Read one number from ``s`` starting at ``i``; returns (value, new_index)."""
    n = len(s)
    while i < n and (s[i].isspace() or s[i] == ','):
        i += 1
    start = i
    while i < n and not (s[i].isspace() or s[i] == ',' or s[i] in "MLCQZASHV"):
        i += 1
    if start == i:
        return 0.0, i
    return float(s[start:i]), i


def parse_svg_d(d: str, ascent: float) -> List[Op]:
    """Parse a picosvg ``d`` (M/L/Q/C/Z/H/V absolute) into font-unit M/L/C/Q/Z ops.

    ``ascent`` is the baseline's SVG y; a font point is ``(x, ascent - svg_y)``.
    """
    ops: List[Op] = []
    cur: Pt = (0.0, 0.0)
    start: Pt = (0.0, 0.0)
    cmd: str | None = None
    params: List[float] = []
    i = 0
    n = len(d)

    def emit(c: str, args: List[float]):
        nonlocal cur, start
        if c == "M":
            if len(args) >= 2:
                cur = (args[0], args[1])
                start = cur
                ops.append(("M", (cur[0], ascent - cur[1])))
        elif c == "L":
            if len(args) >= 2:
                cur = (args[0], args[1])
                ops.append(("L", (cur[0], ascent - cur[1])))
        elif c == "Q":
            if len(args) >= 4:
                s = cur
                c1 = (args[0], args[1])
                cur = (args[2], args[3])
                # quadratic -> cubic (ufo2ft's filters only handle cubic 'curve' segments)
                c1a = (s[0] + (2.0 / 3.0) * (c1[0] - s[0]), s[1] + (2.0 / 3.0) * (c1[1] - s[1]))
                c1b = (cur[0] + (2.0 / 3.0) * (c1[0] - cur[0]), cur[1] + (2.0 / 3.0) * (c1[1] - cur[1]))
                ops.append(("C", (c1a[0], ascent - c1a[1]), (c1b[0], ascent - c1b[1]),
                            (cur[0], ascent - cur[1])))
        elif c == "C":
            if len(args) >= 6:
                c1 = (args[0], args[1]); c2 = (args[2], args[3]); cur = (args[4], args[5])
                ops.append(("C", (c1[0], ascent - c1[1]), (c2[0], ascent - c2[1]), (cur[0], ascent - cur[1])))
        elif c == "Z":
            ops.append(("Z",))
            cur = start

    while i < n:
        ch = d[i]
        if ch.isspace() or ch == ',':
            i += 1
            continue
        if ch in "MLQCZ":
            cmd = ch
            params = []
            i += 1
            if cmd == "Z":
                emit("Z", [])
            continue
        if ch in "HVA":
            # picosvg should not emit these after topicosvg, but be safe
            cmd = ch
            params = []
            i += 1
            continue
        if cmd is None:
            i += 1
            continue
        v, i = _number(d, i)
        params.append(v)
        need = {"M": 2, "L": 2, "Q": 4, "C": 6}.get(cmd, 2)
        if len(params) >= need:
            emit(cmd, params)
            params = []
    return ops


def _split_ops_to_contours(ops: List[Op]) -> List[List[Op]]:
    contours: List[List[Op]] = []
    cur: List[Op] = []
    for op in ops:
        if op[0] == "M":
            if cur:
                contours.append(cur)
            cur = [op]
        else:
            cur.append(op)
    if cur:
        contours.append(cur)
    return contours


def glyph_contours_svg(glyph: Glyph, pen_radius: float, baseline: float) -> List[List[Op]]:
    """Expand a glyph's strokes to fill outlines using Google's **picosvg**.

    This is the *canonical* stroke-expansion used both for the compiled font and for the
    editor preview, so the two can never disagree. Returns a list of contours, each a list
    of ``M/L/C/Z`` ops in font units (y-up, baseline at 0). A glyph with no strokes returns
    an empty list.

    picosvg processes one ``<path>`` element per call, so when the glyph uses different
    stroke-linecaps for lines vs. arcs we feed each sub-path through picosvg independently
    and combine the result (otherwise the second path is silently dropped).
    """
    if not glyph.strokes:
        return []
    ascent = (16 - baseline) * 64
    svg = glyph_svg(glyph, pen_radius, baseline)
    style = re.findall(r'<path d="([^"]+)"[^>]*stroke-linecap="([^"]+)"[^>]*/>', svg)
    out: List[List[Op]] = []
    for d, cap in style:
        only = (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{glyph.cell_width_units:.0f}" height="{UPEM:.0f}" '
            f'viewBox="0 0 {glyph.cell_width_units:.0f} {UPEM:.0f}">'
            f'<path d="{d}" fill="none" stroke="black" stroke-width="{2 * pen_radius:.3f}" '
            f'stroke-linecap="{cap}" stroke-linejoin="round"/></svg>'
        )
        try:
            expanded = SVG.fromstring(only).topicosvg()
        except Exception:
            continue
        m = re.search(r'd="([^"]+)"', expanded.tostring())
        ops = parse_svg_d(m.group(1) if m else "", ascent)
        out.extend(_split_ops_to_contours(ops))
    return out


