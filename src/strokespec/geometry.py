"""Geometric expansion of strokes into closed outlines (font-unit space).

Every stroke is a "pen" of diameter one grid unit that follows a centreline (a straight
segment or a quarter-circle) and is expanded into a closed outline in TrueType/font-unit
coordinates (y-up). The two cap styles mirror the user's "1x1 circle or square" stylus:

* ``"round"`` — a circle of radius ``PEN_RADIUS``. The outline is a capsule / annular
  band with round (semicircular) end caps. Best for a natural "pen stroke" look and for
  consistent joins between overlapping strokes under the non-zero winding rule.
* ``"butt"`` — a square of side one grid unit. The ends are cut square (perpendicular to
  the centreline), which is closer to a pixel-font / brush look.

The model is stroke-based (no holes), so each stroke produces a single simple closed
contour. All contours are normalised to counter-clockwise (positive area) so that
overlapping strokes *union* under the TrueType non-zero winding rule instead of punching
holes.

A glyph is a set of up to 32 such strokes; each is expanded independently and the glyph
outline is the union of the resulting contours.

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
    """Flatten a contour of M/L/C/Z ops into a closed polygon (first point repeated)."""
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
    """Reverse a closed M/L/C/Z contour."""
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
    """Outline (closed contour) for a straight-line stroke with given cap."""
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
        # end cap at p2: from outer(+n) through travel(+u) to inner(-n)
        _append_endcap(p2, r, ang_n, ang_u, ops)
        ops.append(("L", lower_a))
        # start cap at p1: from inner(-n) through travel(-u) to outer(+n)
        _append_endcap(p1, r, ang_mn, ang_u + math.pi, ops)
        ops.append(("Z",))
        return _orient_ccw(ops)
    if cap == "square":
        # square cap: extend each end by half the pen width (= r) so the flat end lands on a
        # cell boundary, and close with a straight (square) end.
        e1 = (p1[0] - u[0] * r, p1[1] - u[1] * r)
        e2 = (p2[0] + u[0] * r, p2[1] + u[1] * r)
        ops = [
            ("M", (e1[0] + n[0] * r, e1[1] + n[1] * r)),
            ("L", (e2[0] + n[0] * r, e2[1] + n[1] * r)),
            ("L", (e2[0] - n[0] * r, e2[1] - n[1] * r)),
            ("L", (e1[0] - n[0] * r, e1[1] - n[1] * r)),
            ("Z",),
        ]
        return _orient_ccw(ops)
    # butt (no extension)
    ops = [
        ("M", off(p1, 1.0)),
        ("L", off(p2, 1.0)),
        ("L", off(p2, -1.0)),
        ("L", off(p1, -1.0)),
        ("Z",),
    ]
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


def arc_end_tangents(p1: Pt, p2: Pt) -> Tuple[Pt, Pt]:
    """Unit tangents (in the ``p1 -> p2`` travel sense) at each arc endpoint.

    A quarter-ellipse is tangent-aligned to its bounding box, so at an ``x``-neighbour
    endpoint the tangent is vertical and at a ``y``-neighbour endpoint horizontal. Returns
    a pair for ``(p1, p2)`` (zero vectors for degenerate arcs).
    """
    dx = abs(p2[0] - p1[0])
    dy = abs(p2[1] - p1[1])
    if dx < 1e-9 and dy < 1e-9:
        return ((0.0, 0.0), (0.0, 0.0))
    if dx < 1e-9 or dy < 1e-9:
        # axis-aligned "arc" (degenerates to a straight line): there is no valid ellipse
        # tangent, so nudge along the direction the line goes (p1 -> p2).
        L = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        ux, uy = (p2[0] - p1[0]) / L, (p2[1] - p1[1]) / L
        return ((ux, uy), (ux, uy))
    minx, maxx = min(p1[0], p2[0]), max(p1[0], p2[0])
    miny, maxy = min(p1[1], p2[1]), max(p1[1], p2[1])
    cands = [
        c for c in ((minx, miny), (maxx, miny), (minx, maxy), (maxx, maxy))
        if (abs(c[0] - p1[0]) > 1e-9 or abs(c[1] - p1[1]) > 1e-9)
        and (abs(c[0] - p2[0]) > 1e-9 or abs(c[1] - p2[1]) > 1e-9)
    ]
    dxd = p2[0] - p1[0]
    dyd = p2[1] - p1[1]
    Bc = max(cands, key=lambda c: dxd * (c[1] - p1[1]) - dyd * (c[0] - p1[0]))
    if abs(p1[1] - Bc[1]) < 1e-9:
        xend, yend = p1, p2
    else:
        xend, yend = p2, p1
    sa = 1.0 if xend[0] > Bc[0] else -1.0
    sb = 1.0 if yend[1] > Bc[1] else -1.0
    t_xend = (0.0, sb)
    t_yend = (-sa, 0.0)
    if xend[0] == p1[0] and xend[1] == p1[1]:
        return (t_xend, t_yend)
    return ((-t_yend[0], -t_yend[1]), (-t_xend[0], -t_xend[1]))


def _ellipse_point(c: Pt, a: float, b: float, sa: float, sb: float, t: float) -> Pt:
    return (c[0] + sa * a * math.cos(t), c[1] + sb * b * math.sin(t))


def _ellipse_normal(c: Pt, a: float, b: float, sa: float, sb: float, t: float) -> Pt:
    """Outward unit normal of the ellipse at parameter ``t`` (gradient-normalised)."""
    nx = sa * math.cos(t) / a
    ny = sb * math.sin(t) / b
    m = math.hypot(nx, ny)
    if m == 0:
        return (0.0, 0.0)
    return (nx / m, ny / m)


def _cap_pts(endpoint: Pt, radial: Pt, bulge: Pt, r: float, n: int = 10) -> List[Pt]:
    """Sample a round cap: a semicircle radius ``r`` centred on ``endpoint`` from the outer
    point (``endpoint + r*radial``) through the bulge direction to the inner point
    (``endpoint - r*radial``). Returns ``n`` points (excluding the outer point)."""
    a_out = math.atan2(radial[1], radial[0])
    a_bulge = math.atan2(bulge[1], bulge[0])
    chosen = None
    for sign in (1.0, -1.0):
        mid = a_out + sign * (math.pi / 2.0)
        if abs(_norm_angle(mid - a_bulge)) < math.pi / 2.0 + 1e-6:
            chosen = sign
            break
    if chosen is None:
        chosen = -1.0
    pts: List[Pt] = []
    for i in range(1, n + 1):
        a = a_out + chosen * math.pi * (i / n)
        pts.append((endpoint[0] + r * math.cos(a), endpoint[1] + r * math.sin(a)))
    return pts


def _smooth_open(pts: List[Pt], ops: List[Op]) -> None:
    """Emit an open Catmull-Rom spline (as cubic beziers) through ``pts``."""
    n = len(pts)
    if n == 0:
        return
    ops.append(("M", pts[0]))
    if n == 1:
        return
    if n == 2:
        ops.append(("L", pts[1]))
        return
    for i in range(n - 1):
        p0 = pts[max(i - 1, 0)]
        p1 = pts[i]
        p2 = pts[i + 1]
        p3 = pts[min(i + 2, n - 1)]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6.0, p1[1] + (p2[1] - p0[1]) / 6.0)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6.0, p2[1] - (p3[1] - p1[1]) / 6.0)
        ops.append(("C", c1, c2, p2))


def _offset_ellipse_pts(c, a, b, sa, sb, r, side, n=20) -> List[Pt]:
    """Offset the quarter-ellipse centreline by ``side*r`` along its normal, sampled."""
    pts: List[Pt] = []
    for i in range(n + 1):
        t = (math.pi / 2.0) * i / n
        p = _ellipse_point(c, a, b, sa, sb, t)
        N = _ellipse_normal(c, a, b, sa, sb, t)
        pts.append((p[0] + side * r * N[0], p[1] + side * r * N[1]))
    return pts


def arc_outline(p1: Pt, p2: Pt, r: float, cap: str) -> List[Op]:
    """Outline for a scaled quarter-circle (quarter-*ellipse*) stroke.

    The arc is confined to the axis-aligned bounding box of the two points (its chord is a
    diagonal of that box) and stays inside it — it never bulges past the chord. It is
    tangent-aligned to the box: at each endpoint the tangent is axis-aligned.

    The bend direction is a pure function of the ordering of the two points: the arc bows
    toward the box corner that lies on the LEFT of the directed chord ``p1 -> p2`` (positive
    cross product in y-up coordinates), so swapping the points bends it the other way.
    """
    dx = abs(p2[0] - p1[0])
    dy = abs(p2[1] - p1[1])
    if dx < 1e-9 and dy < 1e-9:
        return _degenerate(p1, r, cap)
    if dx < 1e-9 or dy < 1e-9:
        # axis-aligned chord: the quarter-ellipse degenerates to the straight segment
        return line_outline(p1, p2, r, cap)

    minx, maxx = min(p1[0], p2[0]), max(p1[0], p2[0])
    miny, maxy = min(p1[1], p2[1]), max(p1[1], p2[1])
    corners = [(minx, miny), (maxx, miny), (minx, maxy), (maxx, maxy)]
    candidates = [
        c for c in corners
        if (abs(c[0] - p1[0]) > 1e-9 or abs(c[1] - p1[1]) > 1e-9)
        and (abs(c[0] - p2[0]) > 1e-9 or abs(c[1] - p2[1]) > 1e-9)
    ]
    # bulge toward the box corner on the left of p1 -> p2 (ordering is the only input)
    dxd = p2[0] - p1[0]
    dyd = p2[1] - p1[1]
    Bc = max(candidates, key=lambda c: dxd * (c[1] - p1[1]) - dyd * (c[0] - p1[0]))
    a, b = dx, dy

    # x-neighbour (same y as Bc) and y-neighbour (same x as Bc) are exactly p1/p2.
    if abs(p1[1] - Bc[1]) < 1e-9:
        xend, yend = p1, p2
    else:
        xend, yend = p2, p1
    sa = 1.0 if xend[0] > Bc[0] else -1.0
    sb = 1.0 if yend[1] > Bc[1] else -1.0

    # Sample the centreline and build the outline as a single sampled *polygon*:
    # side_a = centreline+r*normal, cap at the end, side_b = centreline-r*normal, cap at the
    # start. The two offset sides are always 2r apart and the caps join their endpoints, so
    # the polygon is simple by construction (no self-intersection / notches).
    N = 40
    a_pts: List[Pt] = []
    b_pts: List[Pt] = []
    ncap_pts: List[Pt] = []
    for i in range(N + 1):
        t = (math.pi / 2.0) * i / N
        c = _ellipse_point(Bc, a, b, sa, sb, t)
        nm = _ellipse_normal(Bc, a, b, sa, sb, t)
        a_pts.append((c[0] + r * nm[0], c[1] + r * nm[1]))
        b_pts.append((c[0] - r * nm[0], c[1] - r * nm[1]))
        ncap_pts.append(nm)

    # travel tangent directions at the two endpoints
    t_end = (-sa, 0.0)   # t=pi/2 (yend)
    t_start = (0.0, sb)  # t=0 (xend)

    if cap == "square":
        # square (half-pen) cap: extend each end by r (half the pen width) along the tangent
        # so the flat end lands on a cell boundary, then close with a straight square end.
        a_start_ext = (a_pts[0][0] - t_start[0] * r, a_pts[0][1] - t_start[1] * r)
        a_end_ext = (a_pts[N][0] + t_end[0] * r, a_pts[N][1] + t_end[1] * r)
        b_start_ext = (b_pts[0][0] - t_start[0] * r, b_pts[0][1] - t_start[1] * r)
        b_end_ext = (b_pts[N][0] + t_end[0] * r, b_pts[N][1] + t_end[1] * r)
        ops: List[Op] = [("M", a_start_ext)]
        for i in range(N + 1):
            ops.append(("L", a_pts[i]))                   # outer   A -> B
        ops.append(("L", a_end_ext))                      # square end at B
        ops.append(("L", b_end_ext))
        for i in range(N - 1, -1, -1):
            ops.append(("L", b_pts[i]))                   # inner   B -> A
        ops.append(("L", b_start_ext))                    # square end at A
        ops.append(("L", a_start_ext))
        ops.append(("Z",))
        return _orient_ccw(ops)

    ops: List[Op] = [("M", a_pts[0])]
    for i in range(1, N + 1):
        ops.append(("L", a_pts[i]))                       # side_a (outer)   A -> B
    if cap == "round":
        cap_end = _cap_pts(yend, ncap_pts[N], t_end, r)
        for pt in cap_end[1:]:
            ops.append(("L", pt))                          # round cap at B
        for i in range(N - 1, -1, -1):
            ops.append(("L", b_pts[i]))                    # side_b (inner)   B -> A
        cap_start = _cap_pts(xend, ncap_pts[0], (-t_start[0], -t_start[1]), r)[::-1]
        for pt in cap_start[1:]:
            ops.append(("L", pt))                          # round cap at A
    else:
        ops.append(("L", b_pts[N]))                        # butt cut at B
        for i in range(N - 1, -1, -1):
            ops.append(("L", b_pts[i]))
        ops.append(("L", a_pts[0]))                        # butt cut at A
    ops.append(("Z",))
    return _orient_ccw(ops)


def stroke_outline(
    stroke: Stroke,
    r: float = PEN_RADIUS,
    cap: str = PEN_CAP,
    baseline: float = DEFAULT_BASELINE,
) -> List[Op]:
    """Expand a single stroke to a closed outline (list of M/L/C/Z ops).

    Arcs use a **flat (butt)** endcap — no half-square extension — so each arc end is half a
    pen-width shorter, giving a visibly different (shorter) shape. Lines keep the given cap.
    """
    p1 = stroke.p1.as_font_units(baseline)
    p2 = stroke.p2.as_font_units(baseline)
    if stroke.shape == SHAPE_ARC:
        # FLAT (butt) cap at a FORWARD-NUDGED end: move each endpoint by the pen radius along
        # the tangent (p1 backward, p2 forward), re-fit the arc to the nudged endpoints, and
        # butt-cap. This cuts the end perpendicular to the curve there — a different shape from
        # the half-square (straight tangent) cap.
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

