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

A glyph is a set of such strokes; each is expanded independently (no boolean merge)
and the glyph outline is the per-stroke contours, which union visually when rendered.

Subcomponents
-------------
A glyph may also *instance* another codepoint (:class:`strokespec.model.Subcomponent`): a
reference plus a destination **bounding box** in the containing glyph's cell coordinates. The
referenced glyph's own cell box maps onto that box, and — importantly — the transform is applied
to the referenced glyph's **strokes**, which are only then expanded into outlines here. The pen
therefore keeps its own (unscaled) width, and cell-quantised decisions such as the butt snap are
taken in the *host* glyph's grid. A negatively sized box mirrors the instance.

Instances nest, so an instance is flattened into a flat list of cell-space strokes
(:func:`flatten_cell_strokes`) with the transforms composed; a mirror (negative determinant)
swaps a stroke's two points, which is what keeps an arc bending the mirrored way after its
control point is recomputed from the transformed endpoints.

Coordinate conventions
----------------------
* Font units, y-up, baseline at ``y = 0``. The grid row 0 maps to ``y = 0`` and grid row
  16 maps to ``y = UPEM``. One grid unit = ``SCALE`` units.
* ``Point`` grid coords are multiplied by ``SCALE`` on entry. **Cell** coordinates (used by
  subcomponent boxes, and by the flattened stroke list) are instead the continuous coordinates
  whose integer values are cell *corners*: cell coordinate ``g`` is where ``Point`` index
  ``g - 0.5`` sits, so a point's cell coordinate is ``(p.x + 0.5, p.y + 0.5)``.
"""

from __future__ import annotations

import math
from typing import Callable, List, Sequence, Tuple

from .model import (
    DEFAULT_BASELINE,
    GRID_H,
    PEN_CAP,
    PEN_RADIUS,
    SCALE,
    SHAPE_ARC,
    SHAPE_LINE,
    Stroke,
    Subcomponent,
    Glyph,
)

# Path operators: ("M", pt) ("L", pt) ("C", c1, c2, pt) ("Z",)
# where each pt is an (x, y) tuple in font units.
Op = Tuple
Pt = Tuple[float, float]

# A cell-space affine transform, restricted to what a subcomponent box can express: an
# axis-aligned scale plus a translation. Applied as ``p -> (p.x*sx + tx, p.y*sy + ty)``.
XForm = Tuple[float, float, float, float]   # (sx, sy, tx, ty)
IDENTITY_XFORM: XForm = (1.0, 1.0, 0.0, 0.0)

# A stroke after flattening/transforming into the host glyph's CELL space.
CellStroke = Tuple[Pt, Pt, str]   # (p1, p2, shape)

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
def _snap_dir(v: Pt) -> Pt:
    """Snap a direction to the nearest cell-edge or cell-diagonal direction (multiple of 45°).

    NOTE: intentionally DUMMIED OUT. The diagonal butt is disabled (requirement change: 45°
    uses the horizontal edge). This function is not called by the current pipeline and must be
    LEFT IN PLACE for a future re-enable.
    """
    a = math.atan2(v[1], v[0])
    k = round(a / (math.pi / 4.0)) % 8
    a_s = k * (math.pi / 4.0)
    return (math.cos(a_s), math.sin(a_s))


def _snap_axis(v: Pt) -> Pt:
    """Snap a direction to the nearest axis-aligned (vertical/horizontal) direction (multiple
    of 90°) — the butt-face direction used for every non-axial line."""
    a = math.atan2(v[1], v[0])
    k = round(a / (math.pi / 2.0)) % 4
    a_s = k * (math.pi / 2.0)
    return (math.cos(a_s), math.sin(a_s))


def _is_exactly_45(p1: Pt, p2: Pt) -> bool:
    """True if the centreline is exactly at ±45° (equal |dx| and |dy|).

    Used to give 45° lines a user-controlled vertical/horizontal butt (see line_outline).
    NOTE: the DIAGONAL butt remains DUMMIED OUT — it is not used here; only the axis-aligned
    edge versions are.
    """
    return abs(abs(p2[0] - p1[0]) - abs(p2[1] - p1[1])) < 1e-9


def _cell_min(P: Pt) -> Pt:
    """The lower-left corner (font units) of the grid cell whose centre is ``P``."""
    gx = round(P[0] / SCALE - 0.5)
    gy = round(P[1] / SCALE - 0.5)
    return (gx * SCALE, gy * SCALE)


def _butt_on_cell(P: Pt, Q: Pt, ns: Pt) -> Tuple[Pt, Pt]:
    """The butt (two corners) at end ``P``, snapped onto the *cell's own* edge or diagonal.

    ``ns`` is the snapped butt-face direction (nearest 45°). The butt is one of:
      * the cell's vertical/horizontal EDGE on the outward side of the stroke, or
      * the cell's DIAGONAL matching ``ns``,
    in both cases using the cell's actual corner coordinates (so a 45° stroke's butt is the
    full cell diagonal ``(1,0)-(0,1)`` and a shallow stroke's butt is the full cell edge).
    """
    cx0, cy0 = _cell_min(P)
    dx, dy = P[0] - Q[0], P[1] - Q[1]            # outward: away from the other end
    L = math.hypot(dx, dy) or 1.0
    wx, wy = dx / L, dy / L
    if abs(ns[0]) < 1e-6:                          # vertical butt -> a vertical cell edge
        x = cx0 if wx < 0 else cx0 + SCALE
        return ((x, cy0), (x, cy0 + SCALE))
    if abs(ns[1]) < 1e-6:                          # horizontal butt -> a horizontal cell edge
        y = cy0 if wy < 0 else cy0 + SCALE
        return ((cx0, y), (cx0 + SCALE, y))
    # NOTE: the two diagonal branches below are intentionally DUMMIED OUT (requirement change:
    # 45° uses the horizontal edge). They are only reached when `ns` is diagonal, which the
    # current pipeline never passes (it uses _snap_axis). Keep them in place for re-enabling.
    if (ns[0] > 0) == (ns[1] > 0):                 # main diagonal (0,0)-(1,1)
        return ((cx0, cy0), (cx0 + SCALE, cy0 + SCALE))
    return ((cx0 + SCALE, cy0), (cx0, cy0 + SCALE))  # anti-diagonal (1,0)-(0,1)


def _order_by_side(corners: List[Pt], P: Pt, u: Pt) -> List[Pt]:
    """Order a butt's two corners so the corner on the +u-left side comes first."""
    sc = []
    for c in corners:
        # cross(u, c - P): positive = one side, negative = the other
        sc.append((u[0] * (c[1] - P[1]) - u[1] * (c[0] - P[0]), c))
    sc.sort(key=lambda t: -t[0])
    return [c for _, c in sc]


def line_outline(p1: Pt, p2: Pt, r: float, cap: str) -> List[Op]:
    """Outline (closed contour) for a straight-line stroke with given butt.

    For an **axial** line the square ends already sit on cell edges (each end extended by the
    pen radius for the correct length). For a **non-axial** line each butt is snapped onto the
    cell's own edge or diagonal (see :func:`_butt_on_cell`), so the ends are not square-angled:
    a 45° stroke's butt is the cell diagonal ``(1,0)-(0,1)`` and a shallow stroke's butt is the
    cell edge.
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

    axial = abs(u[0]) < 1e-9 or abs(u[1]) < 1e-9
    if axial:
        # extend each end by r so the flat butt keeps the correct stroke length
        e1 = (p1[0] - u[0] * r, p1[1] - u[1] * r)
        e2 = (p2[0] + u[0] * r, p2[1] + u[1] * r)
        corners = [
            (e1[0] + n[0] * r, e1[1] + n[1] * r),
            (e2[0] + n[0] * r, e2[1] + n[1] * r),
            (e2[0] - n[0] * r, e2[1] - n[1] * r),
            (e1[0] - n[0] * r, e1[1] - n[1] * r),
        ]
    else:
        # non-axial: snap each butt onto the cell's own edge, then order the two butts by
        # side so the stroke sides do not cross.
        #
        # The diagonal butt is DUMMIED OUT. For an exactly-45° line the vertical/horizontal
        # choice is driven by whether the second point is BELOW or ABOVE the first, so the user
        # can pick the butt orientation by the way the line is drawn: p2 below p1 -> vertical
        # butt; p2 above p1 -> horizontal butt (like a vertical line). The diagonal code is kept
        # in place but not reached (see _snap_dir and the diagonal branch of _butt_on_cell).
        if _is_exactly_45(p1, p2):
            ns = (1.0, 0.0) if p2[1] > p1[1] else (0.0, 1.0)
        else:
            ns = _snap_axis(n)
        s_face = _order_by_side(_butt_on_cell(p1, p2, ns), p1, u)
        e_face = _order_by_side(_butt_on_cell(p2, p1, ns), p2, u)
        corners = [s_face[0], e_face[0], e_face[1], s_face[1]]

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


def outline_from_font_points(p1: Pt, p2: Pt, shape: str, r: float, cap: str) -> List[Op]:
    """Expand a single stroke whose two endpoints are already in font units.

    This is the whole expansion: everything else (grid points, subcomponent transforms) only
    decides *where* the two endpoints are.
    """
    if shape == SHAPE_ARC:
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
    return outline_from_font_points(p1, p2, stroke.shape, r, cap)


# --------------------------------------------------------------------------- #
# subcomponents
# --------------------------------------------------------------------------- #
def xform_apply(t: XForm, p: Pt) -> Pt:
    return (p[0] * t[0] + t[2], p[1] * t[1] + t[3])


def xform_compose(outer: XForm, inner: XForm) -> XForm:
    """``outer ∘ inner`` — apply ``inner`` first, then ``outer``."""
    return (
        outer[0] * inner[0],
        outer[1] * inner[1],
        outer[0] * inner[2] + outer[2],
        outer[1] * inner[3] + outer[3],
    )


def xform_mirrors(t: XForm) -> bool:
    """True if the transform reverses handedness (an odd number of flips)."""
    return t[0] * t[1] < 0.0


def subcomponent_xform(sub: Subcomponent, source: Glyph) -> XForm:
    """The cell-space transform placing ``source``'s glyph into ``sub``'s bounding box.

    The source's own cell box ``(0,0)-(source width, GRID_H)`` maps onto the subcomponent's box.
    """
    w = max(1, int(source.cell_width_grid))
    sx = (sub.end[0] - sub.start[0]) / float(w)
    sy = (sub.end[1] - sub.start[1]) / float(GRID_H)
    return (sx, sy, float(sub.start[0]), float(sub.start[1]))


def resolve_glyph(resolve, codepoint: int) -> "Glyph | None":
    """Look up a codepoint through a resolver (``None`` when there is nothing to instance)."""
    if resolve is None:
        return None
    g = resolve(int(codepoint))
    return g if isinstance(g, Glyph) else None


def snap_cell_point(p: Pt) -> Pt:
    """Snap a cell-space point to the nearest **cell centre** (the integer ``Point`` lattice).

    ``Point`` index ``g`` sits at cell coordinate ``g + 0.5``, so a transformed position is
    rounded to the nearest half-integer cell coordinate. An instance's box is generally a
    non-integer number of cells tall/wide, so without this every instanced stroke would land
    between cells — off-grid centre lines, and butt/arc decisions taken against a rounded-up
    cell instead of the cell the point is in.
    """
    return (round(p[0] - 0.5) + 0.5, round(p[1] - 0.5) + 0.5)


def flatten_cell_strokes(
    glyph: Glyph,
    resolve=None,
    xform: XForm = IDENTITY_XFORM,
    _seen=None,
) -> List[CellStroke]:
    """Every stroke of ``glyph`` (including its instances, recursively) in CELL space.

    Transforms composed from nested instances are applied to the *strokes*; outlines are built
    from the result by :func:`cell_stroke_outline`. A mirroring transform swaps a stroke's points
    so that an arc — whose bend is a pure function of point order — bows the mirrored way once
    its control point is recomputed from the transformed endpoints.

    Each transformed endpoint is then snapped to the nearest **cell centre**
    (:func:`snap_cell_point`), which is the only lattice the stroke geometry is defined on, so an
    instance's strokes are expanded exactly like authored ones.

    ``_seen`` guards against a cycle in malformed data (the model breaks cycles on load, so a
    well-formed font never needs it).
    """
    if _seen is None:
        _seen = {int(glyph.codepoint)}
    mirror = xform_mirrors(xform)
    out: List[CellStroke] = []
    for s in glyph.strokes:
        p1 = xform_apply(xform, (s.p1.x + 0.5, s.p1.y + 0.5))
        p2 = xform_apply(xform, (s.p2.x + 0.5, s.p2.y + 0.5))
        if mirror:
            p1, p2 = p2, p1
        out.append((snap_cell_point(p1), snap_cell_point(p2), s.shape))
    for sub in glyph.subcomponents:
        if sub.codepoint in _seen:
            continue
        src = resolve_glyph(resolve, sub.codepoint)
        if src is None:
            continue
        inner = xform_compose(xform, subcomponent_xform(sub, src))
        out.extend(flatten_cell_strokes(src, resolve, inner, _seen | {sub.codepoint}))
    return out


def cell_stroke_outline(
    cs: CellStroke,
    r: float = PEN_RADIUS,
    cap: str = PEN_CAP,
    baseline: float = DEFAULT_BASELINE,
) -> List[Op]:
    """Expand one cell-space stroke to its outline (cell coords -> font units, then expand)."""
    p1, p2, shape = cs
    f1 = (p1[0] * SCALE, (p1[1] - baseline) * SCALE)
    f2 = (p2[0] * SCALE, (p2[1] - baseline) * SCALE)
    return outline_from_font_points(f1, f2, shape, r, cap)


def subcomponent_contours(
    sub: Subcomponent,
    resolve,
    r: float = PEN_RADIUS,
    cap: str = PEN_CAP,
    baseline: float = DEFAULT_BASELINE,
    outer: XForm = IDENTITY_XFORM,
) -> List[List[Op]]:
    """The contours an instance converts to *inside its host glyph* (empty when unresolved).

    ``outer`` maps the instance's host glyph cell space into whatever space the caller wants the
    result in (the host's own space by default).
    """
    src = resolve_glyph(resolve, sub.codepoint)
    if src is None:
        return []
    x = xform_compose(outer, subcomponent_xform(sub, src))
    return [cell_stroke_outline(cs, r, cap, baseline)
            for cs in flatten_cell_strokes(src, resolve, x, {int(src.codepoint)})]


def glyph_outline(
    glyph: Glyph,
    r: float = PEN_RADIUS,
    cap: str = PEN_CAP,
    baseline: float = DEFAULT_BASELINE,
    resolve=None,
) -> List[Op]:
    """Expand every stroke in ``glyph`` (and its instances) into one flat op stream
    (multiple closed subpaths, one per stroke)."""
    ops: List[Op] = []
    for cs in flatten_cell_strokes(glyph, resolve):
        ops.extend(cell_stroke_outline(cs, r, cap, baseline))
    return ops


def glyph_contours(
    glyph: Glyph,
    r: float = PEN_RADIUS,
    cap: str = PEN_CAP,
    baseline: float = DEFAULT_BASELINE,
    resolve=None,
) -> List[List[Op]]:
    """Expand every stroke to its own closed contour (list of M/L/C/Z op lists).

    ``resolve`` is a ``codepoint -> Glyph`` callable used to instance subcomponents; without it
    subcomponents contribute nothing.
    """
    return [cell_stroke_outline(cs, r, cap, baseline)
            for cs in flatten_cell_strokes(glyph, resolve)]


def outline_to_paths(
    glyph: Glyph,
    cell_width_units: int,
    r: float = PEN_RADIUS,
    cap: str = PEN_CAP,
    baseline: float = DEFAULT_BASELINE,
    resolve=None,
) -> List[Tuple[float, List[Op]]]:
    """Convenience: a list of ``(advance, contours)`` ready to be written as glyphs.

    Provided mainly as a stable API boundary between the model and the builders.
    """
    advance = 0 if glyph.combining else cell_width_units
    return [(advance, glyph_contours(glyph, r, cap, baseline, resolve))]

