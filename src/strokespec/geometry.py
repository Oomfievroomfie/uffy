"""Geometric expansion of strokes into closed outlines (font-unit space).

Every stroke is a "pen" of diameter one grid unit that follows a centreline (a straight
segment or a **single quadratic** arc, one quadratic per quarter) and is expanded into a
closed outline in TrueType/font-unit coordinates (y-up). The two cap styles mirror the user's
"1x1 circle or square" stylus:

* ``"round"`` — a circle of radius ``PEN_RADIUS``. The outline is a capsule / annular
  band with round (semicircular) end caps. Best for a natural "pen stroke" look and for
  consistent joins between overlapping strokes under the non-zero winding rule.
* ``"butt"`` — a square of side one grid unit. The ends are cut square (perpendicular to
  the centreline), which is closer to a pixel-font / brush look.

The model is stroke-based (no holes), so each stroke produces a single simple closed
contour. All contours are normalised to counter-clockwise (positive area) so that
overlapping strokes *union* under the TrueType non-zero winding rule instead of punching
holes.

A glyph is a set of up to 32 such strokes; each is expanded independently (no boolean merge)
and the glyph outline is the per-stroke contours, which union visually when rendered.

Coordinate conventions
----------------------
* Font units, y-up, baseline at ``y = 0``. The grid row 0 maps to ``y = 0`` and grid row
  16 maps to ``y = UPEM``. One grid unit = ``SCALE`` units.
* ``Point`` grid coords are multiplied by ``SCALE`` on entry.
"""

from __future__ import annotations

import math
from typing import Callable, List, Sequence, Tuple

from .model import (
    DEFAULT_BASELINE,
    PEN_CAP,
    PEN_RADIUS,
    SCALE,
    SHAPE_ARC,
    SHAPE_LINE,
    Stroke,
    Glyph,
)

# Path operators: ("M", pt) ("L", pt) ("C", c1, c2, pt) ("Z",)
# where each pt is an (x, y) tuple in font units.
Op = Tuple
Pt = Tuple[float, float]

_TWO_PI = 2.0 * math.pi

# An arc whose chord is axis-aligned (or degenerate/zero-length) is NOT a single quadratic
# at all: it is exactly the straight segment between its two points. Such a stroke is
# therefore a LINE in every expansion pipeline, and must never reach the arc/butt code path.
# This is the single source of truth for that decision, shared by the geometry preview and
# the font compile so the two can never disagree.
_AXIS_EPS = 1e-6


def arc_degenerates_to_line(p1: Pt, p2: Pt) -> bool:
    """True if an arc stroke between ``p1`` and ``p2`` is really a line (axis-aligned or
    zero length). Such an arc has no ellipse: its "quarter circle" collapses to the straight
    segment, so every pipeline must expand it exactly as a ``SHAPE_LINE`` stroke."""
    dx = abs(p2[0] - p1[0])
    dy = abs(p2[1] - p1[1])
    return dx < _AXIS_EPS or dy < _AXIS_EPS


# --------------------------------------------------------------------------- #
# small math helpers
# --------------------------------------------------------------------------- #
def _norm_angle(a: float) -> float:
    """Normalise to (-pi, pi]."""
    a = math.fmod(a, _TWO_PI)
    if a <= -math.pi:
        a += _TWO_PI
    elif a > math.pi:
        a -= _TWO_PI
    return a


def _unit(v: Tuple[float, float]) -> Tuple[float, float]:
    x, y = v
    m = math.hypot(x, y)
    if m == 0:
        return (0.0, 0.0)
    return (x / m, y / m)


def _perp(u: Tuple[float, float]) -> Tuple[float, float]:
    """90-degree CCW rotation of a unit vector (y-up): (-uy, ux)."""
    return (-u[1], u[0])


def _add(a: Pt, b: Pt) -> Pt:
    return (a[0] + b[0], a[1] + b[1])


def _sub(a: Pt, b: Pt) -> Pt:
    return (a[0] - b[0], a[1] - b[1])


def _mul(a: Pt, s: float) -> Pt:
    return (a[0] * s, a[1] * s)


def _mid(a: Pt, b: Pt) -> Pt:
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)


def _arc_seg(c: Pt, r: float, a0: float, a1: float) -> Tuple[Pt, Pt, Pt, Pt]:
    """Approximate the circular arc centred at ``c``, radius ``r`` from angle a0 to a1
    (may be negative for clockwise) as a single cubic bezier. Returns (p0, c1, c2, p1)."""
    x0 = c[0] + r * math.cos(a0)
    y0 = c[1] + r * math.sin(a0)
    x1 = c[0] + r * math.cos(a1)
    y1 = c[1] + r * math.sin(a1)
    d = a1 - a0
    alpha = (4.0 / 3.0) * math.tan(d / 4.0)
    c1 = (x0 - alpha * r * math.sin(a0), y0 + alpha * r * math.cos(a0))
    c2 = (x1 + alpha * r * math.sin(a1), y1 - alpha * r * math.cos(a1))
    return ((x0, y0), c1, c2, (x1, y1))


def _append_arc(c: Pt, r: float, a0: float, a1: float, ops: List[Op]) -> None:
    """Append cubic segments walking the arc from a0 to a1 (in the sign of a1-a0)."""
    delta = a1 - a0
    n = max(1, min(8, int(abs(delta) / (math.pi / 2.0)) + 1))
    step = delta / n
    for i in range(n):
        s0 = a0 + i * step
        s1 = a0 + (i + 1) * step
        _p0, c1, c2, p1 = _arc_seg(c, r, s0, s1)
        ops.append(("C", c1, c2, p1))


def _append_endcap(
    endpoint: Pt, r: float, radial_angle: float, bulge_angle: float, ops: List[Op]
) -> None:
    """Append a 180-degree round cap centred at ``endpoint``.

    The cap connects the outer point (``endpoint + r*radial``) to the inner point
    (``endpoint - r*radial``), bulging through ``bulge_angle`` (the direction the pen
    travels beyond the end of the stroke). It is emitted as two 90-degree cubic arcs.
    """
    a_out = radial_angle
    # choose the sweep sign (+/-) so the arc's midpoint lands on the bulge direction
    mid_p = math.pi / 2.0
    chosen = None
    for sign in (1.0, -1.0):
        mid = a_out + sign * mid_p
        if abs(_norm_angle(mid - bulge_angle)) < math.pi / 2.0 + 1e-6:
            chosen = sign
            break
    if chosen is None:
        chosen = -1.0
    # sweep from a_out, through the bulge, to the opposite radial, all in one direction
    _append_arc(endpoint, r, a_out, a_out + chosen * mid_p, ops)
    _append_arc(endpoint, r, a_out + chosen * mid_p, a_out + chosen * math.pi, ops)


def _signed_area(ops: Sequence[Op]) -> float:
    """Signed area of a closed contour (sampled beziers) used for orientation."""
    area = 0.0
    pts = _flatten(ops, samples=12)
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        area += x0 * y1 - x1 * y0
    return area / 2.0


def _flatten(ops: Sequence[Op], samples: int = 16) -> List[Pt]:
    """Flatten a contour of M/L/Q/C/Z ops into a closed polygon (first point repeated)."""
    pts: List[Pt] = []
    cur: Pt = (0.0, 0.0)
    for op in ops:
        kind = op[0]
        if kind == "M":
            cur = op[1]
            pts.append(cur)
        elif kind == "L":
            cur = op[1]
            pts.append(cur)
        elif kind == "Q":
            p0, c, p1 = cur, op[1], op[2]
            for t in range(1, samples + 1):
                u = t / samples
                v = 1.0 - u
                x = v * v * p0[0] + 2 * v * u * c[0] + u * u * p1[0]
                y = v * v * p0[1] + 2 * v * u * c[1] + u * u * p1[1]
                pts.append((x, y))
            cur = p1
        elif kind == "C":
            p0, c1, c2, p1 = cur, op[1], op[2], op[3]
            for t in range(1, samples + 1):
                u = t / samples
                v = 1.0 - u
                x = (
                    v * v * v * p0[0]
                    + 3 * v * v * u * c1[0]
                    + 3 * v * u * u * c2[0]
                    + u * u * u * p1[0]
                )
                y = (
                    v * v * v * p0[1]
                    + 3 * v * v * u * c1[1]
                    + 3 * v * u * u * c2[1]
                    + u * u * u * p1[1]
                )
                pts.append((x, y))
            cur = p1
        elif kind == "Z":
            break
    return pts


def _reverse(ops: Sequence[Op]) -> List[Op]:
    """Reverse a closed M/L/Q/C/Z contour."""
    if len(ops) < 2:
        return list(ops)
    # Build (kind, start, c1, c2, end) segments, walking the on-curve path.
    segments: List[tuple] = []
    cur = ops[0][1]  # the "M" point
    for op in ops[1:]:
        kind = op[0]
        if kind == "L":
            end = op[1]
            segments.append(("L", cur, None, None, end))
            cur = end
        elif kind == "Q":
            segments.append(("Q", cur, op[1], None, op[2]))
            cur = op[2]
        elif kind == "C":
            segments.append(("C", cur, op[1], op[2], op[3]))
            cur = op[3]
        elif kind == "Z":
            break
    # New contour begins at the final on-curve point and walks the segments backwards.
    new: List[Op] = [("M", cur)]
    for seg in reversed(segments):
        kind, start, c1, c2, end = seg
        if kind == "L":
            new.append(("L", start))
        elif kind == "Q":
            new.append(("Q", c1, start))
        else:
            new.append(("C", c2, c1, start))
    new.append(("Z",))
    return new


def _orient_ccw(ops: Sequence[Op]) -> List[Op]:
    """Return the contour with positive (counter-clockwise) signed area."""
    if _signed_area(ops) < 0:
        return _reverse(ops)
    return list(ops)


# --------------------------------------------------------------------------- #
# stroke expansion
# --------------------------------------------------------------------------- #
def line_outline(p1: Pt, p2: Pt, r: float, cap: str) -> List[Op]:
    """Outline (closed contour) for a straight-line stroke with given butt.

    Each end is extended by the pen radius (``r``) along the centreline, so the flat butt
    lands on the cell boundary — this is the correct stroke length for every line.
    """
    d = _sub(p2, p1)
    L = math.hypot(*d)
    if L == 0:
        return _degenerate(p1, r, cap)
    u = _mul(d, 1.0 / L)
    n = _perp(u)

    def off(p: Pt, s: float) -> Pt:
        return (p[0] + s * n[0] * r, p[1] + s * n[1] * r)

    if cap == "round":
        upper_a = off(p1, 1.0)
        upper_b = off(p2, 1.0)
        lower_b = off(p2, -1.0)
        lower_a = off(p1, -1.0)
        ang_u = math.atan2(u[1], u[0])
        ang_n = math.atan2(n[1], n[0])
        ang_mn = math.atan2(-n[1], -n[0])
        ops: List[Op] = [("M", upper_a), ("L", upper_b)]
        _append_endcap(p2, r, ang_n, ang_u, ops)
        ops.append(("L", lower_a))
        _append_endcap(p1, r, ang_mn, ang_u + math.pi, ops)
        ops.append(("Z",))
        return _orient_ccw(ops)

    # extend each end by r so the flat butt keeps the correct stroke length
    e1 = (p1[0] - u[0] * r, p1[1] - u[1] * r)
    e2 = (p2[0] + u[0] * r, p2[1] + u[1] * r)
    corners = [
        (e1[0] + n[0] * r, e1[1] + n[1] * r),
        (e2[0] + n[0] * r, e2[1] + n[1] * r),
        (e2[0] - n[0] * r, e2[1] - n[1] * r),
        (e1[0] - n[0] * r, e1[1] - n[1] * r),
    ]
    ops = [("M", corners[0]), ("L", corners[1]), ("L", corners[2]), ("L", corners[3]), ("Z",)]
    return _orient_ccw(ops)


def _degenerate(center: Pt, r: float, cap: str) -> List[Op]:
    """A zero-length stroke -> a dot (disc for round, square for butt)."""
    if cap == "round":
        ops: List[Op] = [("M", (center[0] + r, center[1]))]
        _append_arc(center, r, 0.0, math.pi / 2.0, ops)
        _append_arc(center, r, math.pi / 2.0, math.pi, ops)
        _append_arc(center, r, math.pi, 1.5 * math.pi, ops)
        _append_arc(center, r, 1.5 * math.pi, 2.0 * math.pi, ops)
        ops.append(("Z",))
        return ops
    else:
        return [
            ("M", (center[0] - r, center[1] - r)),
            ("L", (center[0] + r, center[1] - r)),
            ("L", (center[0] + r, center[1] + r)),
            ("L", (center[0] - r, center[1] + r)),
            ("Z",),
        ]


def arc_control(p1: Pt, p2: Pt) -> Pt:
    """The single quadratic control point: the box corner the arc bulges toward.

    The arc is ONE quadratic per quarter, ``p1 -> C -> p2``, whose control point ``C`` is the
    axis-aligned bounding-box corner on the RIGHT of the directed ``p1 -> p2`` chord (negative
    cross product in y-up coordinates). That makes the endpoint tangents axial and
    full-strength (the control point sits at the box corner, at full extent). Swapping the
    points bows the arc the other way.
    """
    minx, maxx = min(p1[0], p2[0]), max(p1[0], p2[0])
    miny, maxy = min(p1[1], p2[1]), max(p1[1], p2[1])
    corners = [(minx, miny), (maxx, miny), (minx, maxy), (maxx, maxy)]
    candidates = [
        c for c in corners
        if (abs(c[0] - p1[0]) > 1e-9 or abs(c[1] - p1[1]) > 1e-9)
        and (abs(c[0] - p2[0]) > 1e-9 or abs(c[1] - p2[1]) > 1e-9)
    ]
    dxd = p2[0] - p1[0]
    dyd = p2[1] - p1[1]
    # RIGHT of the directed chord (negative cross product) -> the arc bows the other way.
    return min(candidates, key=lambda c: dxd * (c[1] - p1[1]) - dyd * (c[0] - p1[0]))


def arc_end_tangents(p1: Pt, p2: Pt) -> Tuple[Pt, Pt]:
    """Unit tangents (in the ``p1 -> p2`` travel sense) at each arc endpoint.

    The single-quadratic arc is axial at both ends: the tangent at ``p1`` points at the
    control (box) corner and the tangent at ``p2`` points away from it, so both are
    axis-aligned and full-strength. Returns ``(t1, t2)`` (zero vectors for degenerate arcs).
    """
    dx = abs(p2[0] - p1[0])
    dy = abs(p2[1] - p1[1])
    if dx < 1e-9 and dy < 1e-9:
        return ((0.0, 0.0), (0.0, 0.0))
    if dx < 1e-9 or dy < 1e-9:
        # axis-aligned "arc" (degenerates to a straight line): nudge along the segment.
        L = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        ux, uy = (p2[0] - p1[0]) / L, (p2[1] - p1[1]) / L
        return ((ux, uy), (ux, uy))
    C = arc_control(p1, p2)
    return (_unit(_sub(C, p1)), _unit(_sub(p2, C)))


def arc_outline(p1: Pt, p2: Pt, r: float, cap: str) -> List[Op]:
    """Outline for a single-quadratic-per-quarter arc stroke.

    The centreline is ONE quadratic Bezier ``p1 -> C -> p2`` whose control point ``C`` is the
    box bulge corner, so the endpoint tangents are **axial and full-strength** (the control
    point sits at the box corner, at full extent). The outline is that centreline swept by a
    pen of diameter ``2r``: two offset sides, each a **single quadratic** (the offset control
    polygon), joined by the end caps.

    The bend direction is a pure function of the ordering of ``p1``/``p2``: the arc bows
    toward the box corner on the RIGHT of the directed ``p1 -> p2`` chord.
    """
    dx = abs(p2[0] - p1[0])
    dy = abs(p2[1] - p1[1])
    if dx < 1e-9 and dy < 1e-9:
        return _degenerate(p1, r, cap)
    if dx < 1e-9 or dy < 1e-9:
        # axis-aligned chord: degenerates to the straight segment
        return line_outline(p1, p2, r, cap)

    C = arc_control(p1, p2)
    # centreline tangents (axial, full-strength) and their CCW normals
    u1 = _unit(_sub(C, p1))   # tangent at p1 (points at the control corner)
    u2 = _unit(_sub(p2, C))   # tangent at p2 (points away from the control corner)
    n1 = _perp(u1)
    n2 = _perp(u2)

    # offset side endpoints: outer = centreline + r*n, inner = centreline - r*n
    O1 = (p1[0] + r * n1[0], p1[1] + r * n1[1])
    O2 = (p2[0] + r * n2[0], p2[1] + r * n2[1])
    I1 = (p1[0] - r * n1[0], p1[1] - r * n1[1])
    I2 = (p2[0] - r * n2[0], p2[1] - r * n2[1])

    # Each offset side keeps the axial end tangents (u1 at the start, u2 at the end), so its
    # control point is the perpendicular projection of the end point onto the start's axial
    # tangent line — NOT the centreline control point offset normally (which tips the side's
    # tangent off-axis). This makes each single-quadratic side's end tangents precisely axial.
    def _ctrl(start: Pt, end: Pt) -> Pt:
        a = (end[0] - start[0]) * u1[0] + (end[1] - start[1]) * u1[1]
        return (start[0] + a * u1[0], start[1] + a * u1[1])

    OC = _ctrl(O1, O2)
    IC = _ctrl(I1, I2)

    ops: List[Op] = [("M", O1), ("Q", OC, O2)]  # outer side, one quadratic
    if cap == "round":
        _append_endcap(p2, r, math.atan2(n2[1], n2[0]), math.atan2(u2[1], u2[0]), ops)
        ops.append(("Q", IC, I1))               # inner side (back)
        _append_endcap(p1, r, math.atan2(-n1[1], -n1[0]), math.atan2(-u1[1], -u1[0]), ops)
    else:
        # flat (butt) cut across the pen. The forward nudge in stroke_outline already places
        # the butt exactly where the half-pen square extension would have reached.
        ops.append(("L", I2))                   # flat end cut at p2
        ops.append(("Q", IC, I1))               # inner side (back)
        ops.append(("L", O1))                   # flat start cut at p1
    ops.append(("Z",))
    return _orient_ccw(ops)


def stroke_outline(
    stroke: Stroke,
    r: float = PEN_RADIUS,
    cap: str = PEN_CAP,
    baseline: float = DEFAULT_BASELINE,
) -> List[Op]:
    """Expand a single stroke to a closed outline (list of M/L/C/Z ops).

    Arcs use a **flat (butt)** end — no half-pen extension along the centreline — so each arc
    end is half a pen-width shorter than a square butt, giving a visibly different (shorter)
    shape. Lines keep the given butt style.
    """
    p1 = stroke.p1.as_font_units(baseline)
    p2 = stroke.p2.as_font_units(baseline)
    if stroke.shape == SHAPE_ARC:
        if arc_degenerates_to_line(p1, p2):
            # Axis-aligned (or zero-length) arc: it has no ellipse, so it is exactly the
            # straight segment between its two points. Expand it EXACTLY as a SHAPE_LINE with
            # the line's cap — never the arc/butt/nudged path.
            return line_outline(p1, p2, r, cap)
        # FLAT (butt) end at a FORWARD-NUDGED end: move each endpoint by the pen radius along
        # the tangent (p1 back, p2 forward) in stroke space, then build the arc outline —
        # the flat end lands where a square butt's half-pen extension would have reached.
        t1, t2 = arc_end_tangents(p1, p2)
        p1n = (p1[0] - r * t1[0], p1[1] - r * t1[1])
        p2n = (p2[0] + r * t2[0], p2[1] + r * t2[1])
        return arc_outline(p1n, p2n, r, "butt")
    return line_outline(p1, p2, r, cap)


def glyph_outline(
    glyph: Glyph, r: float = PEN_RADIUS, cap: str = PEN_CAP, baseline: float = DEFAULT_BASELINE
) -> List[Op]:
    """Expand every stroke in ``glyph`` into one flat op stream (multiple closed
    subpaths, one per stroke)."""
    ops: List[Op] = []
    for stroke in glyph.strokes:
        ops.extend(stroke_outline(stroke, r, cap, baseline))
    return ops


def glyph_contours(
    glyph: Glyph,
    r: float = PEN_RADIUS,
    cap: str = PEN_CAP,
    baseline: float = DEFAULT_BASELINE,
) -> List[List[Op]]:
    """Expand every stroke to its own closed contour (list of M/L/C/Z op lists)."""
    return [stroke_outline(s, r, cap, baseline) for s in glyph.strokes]


def outline_to_paths(
    glyph: Glyph,
    cell_width_units: int,
    r: float = PEN_RADIUS,
    cap: str = PEN_CAP,
    baseline: float = DEFAULT_BASELINE,
) -> List[Tuple[float, List[Op]]]:
    """Convenience: a list of ``(advance, contours)`` ready to be written as glyphs.

    Provided mainly as a stable API boundary between the model and the builders.
    """
    advance = 0 if glyph.combining else cell_width_units
    return [(advance, glyph_contours(glyph, r, cap, baseline))]

