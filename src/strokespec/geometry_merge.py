"""Merge stroke outlines that share an exactly-matching edge (straight or curved).

Given a glyph's per-stroke contours (one closed contour per stroke, M/L/Q/C/Z), when two
contours share an edge whose path is identical — same two endpoints in opposite direction, and
the same curve (same quadratic off-curve control point, or the same cubic control points
swapped for the reversed traversal) — remove the two coincident edges and splice the two loops
into one along that seam. Straight edges that meet collinearly at a 180-degree no-op vertex are
then collapsed into a single edge.

This is used by the TTF export path (default in ``build_ufo``): the editor previews keep one
contour per stroke, so preview and export can't disagree on the authored shape — export just
merges redundant seams for size. It is NOT used for authoring.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

Op = tuple


def _parse(ops: List[Op]) -> Tuple[List, List]:
    """Contour op list -> (verts, edges) where edges[i] draws verts[i] -> verts[i+1 mod n]."""
    verts = [ops[0][1]]
    edges = []
    for op in ops[1:]:
        k = op[0]
        if k == "L":
            edges.append(("L", op[1]))
            verts.append(op[1])
        elif k == "Q":
            edges.append(("Q", op[1], op[2]))
            verts.append(op[2])
        elif k == "C":
            edges.append(("C", op[1], op[2], op[3]))
            verts.append(op[3])
        elif k == "Z":
            break
    n = len(verts)
    if len(edges) == n - 1:
        edges.append(("L", verts[0]))
    return verts, edges


def _to_ops(loop: Tuple[List, List]) -> List[Op]:
    verts, edges = loop
    ops = [("M", verts[0])]
    for e in edges:
        if e[0] == "L":
            ops.append(("L", e[1]))
        elif e[0] == "Q":
            ops.append(("Q", e[1], e[2]))
        elif e[0] == "C":
            ops.append(("C", e[1], e[2], e[3]))
    ops.append(("Z",))
    return ops


def _vec(a, b):
    return (b[0] - a[0], b[1] - a[1])


def _cross(u, v):
    return u[0] * v[1] - u[1] * v[0]


def _dot(u, v):
    return u[0] * v[0] + u[1] * v[1]


def _edges_match(ea, va, ia, eb, vb, ib) -> bool:
    """True iff edge ia of A (va[ia]->va[ia+1]) and edge ib of B are the SAME edge reversed.

    Both edges must go between the same two points in opposite directions and carry the same
    curve: for a quadratic the off-curve control point is unchanged by reversal; for a cubic the
    two control points swap.
    """
    n, m = len(va), len(vb)
    pa, qa = va[ia], va[(ia + 1) % n]
    pb, qb = vb[ib], vb[(ib + 1) % m]
    if pa != qb or qa != pb:
        return False
    ka, kb = ea[ia][0], eb[ib][0]
    if ka != kb:
        return False
    if ka == "L":
        return True
    if ka == "Q":
        return ea[ia][1] == eb[ib][1]
    if ka == "C":
        return ea[ia][1] == eb[ib][2] and ea[ia][2] == eb[ib][1]
    return False


def _merge_two(A, B):
    """Merge two loops that share an exactly-matching (straight or curved) edge; else None."""
    va, ea = A
    vb, eb = B
    for ia in range(len(va)):
        for ib in range(len(vb)):
            if _edges_match(ea, va, ia, eb, vb, ib):
                return _build(va, ea, vb, eb, ia, ib)
    return None


def _build(va, ea, vb, eb, ia, ib):
    n, m = len(va), len(vb)
    verts = []
    edges = []
    for t in range(n):
        verts.append(va[(ia + 1 + t) % n])
        if t < n - 1:
            edges.append(ea[(ia + 1 + t) % n])
    for s in range(m - 1):
        verts.append(vb[(ib + 2 + s) % m])
        edges.append(eb[(ib + 1 + s) % m])
    if verts and verts[0] == verts[-1]:
        verts.pop()
    return (verts, edges)


def _simplify(loop: Tuple[List, List]) -> Tuple[List, List]:
    """Remove 180-degree no-op vertices: keep collinear straight runs as a single edge."""
    verts, edges = loop
    n = len(verts)
    if n < 3:
        return loop

    def redundant(i):
        ep, en = edges[i - 1], edges[i]
        return (
            ep[0] == "L"
            and en[0] == "L"
            and abs(_cross(_vec(verts[i - 1], verts[i]), _vec(verts[i], verts[(i + 1) % n]))) < 1e-9
            and _dot(_vec(verts[i - 1], verts[i]), _vec(verts[i], verts[(i + 1) % n])) > 0
        )

    nv = [verts[i] for i in range(n) if not redundant(i)]
    if len(nv) < 3:
        return (verts, edges)
    ne = []
    for k in range(len(nv)):
        a = nv[k]
        b = nv[(k + 1) % len(nv)]
        placed = False
        for i in range(n):
            if verts[i] == a and verts[(i + 1) % n] == b:
                ne.append(edges[i])
                placed = True
                break
        if not placed:
            ne.append(("L", b))
    return (nv, ne)


def merge_stroke_edges(contours: List[List[Op]]) -> List[List[Op]]:
    """Merge any contours sharing an exact straight edge, then simplify, in-place result."""
    if len(contours) < 2:
        return [c for c in contours]
    loops = [_parse(c) for c in contours]
    while True:
        best = None
        for i in range(len(loops)):
            for j in range(i + 1, len(loops)):
                r = _merge_two(loops[i], loops[j])
                if r is not None:
                    best = (i, j, r)
                    break
            if best is not None:
                break
        if best is None:
            return [_to_ops(_simplify(loop)) for loop in loops]
        i, j, r = best
        loops = [loop for k, loop in enumerate(loops) if k not in (i, j)] + [r]
