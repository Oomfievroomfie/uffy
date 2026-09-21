"""Data model for stroke-defined glyphs.

Design space
------------
Strokes live on a 16x16 grid of integer points. A glyph is either 8x16 or 16x16 (that
is its advance width, in grid units). One grid unit maps to ``SCALE`` font units, so the
grid is the em box. See ``strokespec.geometry`` for the coordinate conventions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

# --- design constants ---------------------------------------------------------
GRID_W = 16          # number of grid cells per side (a full-width glyph fills this)
GRID_H = 16
GRID_N = 16          # cell indices are 0..15; each point sits at a cell CENTRE
SCALE = 16           # font units per grid cell
UPEM = SCALE * GRID_H  # 256 units per em
PEN_RADIUS = SCALE // 2  # 8 units -> 1 grid-cell-diameter pen
# Pen butt style is a tool-level choice (not per-glyph data). "square" = a square butt: the
# stroke extends by the pen radius (a half cell) so its flat end lands on a cell boundary.
PEN_CAP = "square"
FULL_WIDTH_UNITS = GRID_W * SCALE   # 256
HALF_WIDTH_UNITS = (GRID_W // 2) * SCALE  # 128

# Metrics, in grid cells, measured from the BOTTOM edge of the em box (0) upward.
# The bottom edge is NOT the baseline: descenders live in the cells below the baseline,
# so the baseline sits above the bottom edge. Both are globally configurable per stroke set.
DEFAULT_BASELINE = 2.0    # grid cells from the em-box bottom to the baseline (Unifont)
DEFAULT_X_HEIGHT = 10.0   # grid cells (x-height metric; stored in the font info) (Unifont)
DEFAULT_CAP_HEIGHT = 12.0 # grid cells (cap-height metric) (Unifont)

SHAPE_LINE = "line"
SHAPE_ARC = "arc"

_SUPPORTED_SHAPES = frozenset({SHAPE_LINE, SHAPE_ARC})


def unicode_fullwidth(codepoint: int) -> bool:
    """True if the codepoint is full-width per Unicode East Asian Width (W or F)."""
    import unicodedata2 as unicodedata
    try:
        return unicodedata.east_asian_width(chr(codepoint)) in ("W", "F")
    except Exception:
        return False


def unicode_combining(codepoint: int) -> bool:
    """True if the codepoint is a combining mark (general category Mn/Mc/Me)."""
    import unicodedata2 as unicodedata
    try:
        return unicodedata.category(chr(codepoint)) in ("Mn", "Mc", "Me")
    except Exception:
        return False


def unicode_allocated(codepoint: int) -> bool:
    """True if the codepoint is an assigned (named) Unicode character.

    Unallocated codepoints are usually scratch/dummy slots the user parks temporary glyph data
    in, so provenance pointing at one is not meaningful.
    """
    import unicodedata2 as unicodedata
    if codepoint < 0 or codepoint > 0x10FFFF or 0xD800 <= codepoint <= 0xDFFF:
        return False
    try:
        return bool(unicodedata.name(chr(codepoint), ""))
    except Exception:
        return False


def copied_origin(
    source_cp: int,
    target_cp: int,
    source_origin: Optional["StrokeOrigin"],
    direction: Optional[str] = None,
    fraction: Optional[float] = None,
) -> Optional["StrokeOrigin"]:
    """Provenance for a stroke copied from ``source_cp`` into ``target_cp``.

    The direct source is stamped (with the squish direction/amount, if any) **only** when it is
    an *allocated* codepoint and differs from the target. Otherwise the stroke's existing
    provenance is carried through unchanged — this covers both pasting into the codepoint the
    strokes came from, and copying out of an *unallocated* scratch/dummy glyph (whose strokes keep
    whatever provenance they already had, rather than being overwritten or deleted).
    """
    if unicode_allocated(source_cp) and source_cp != target_cp:
        return StrokeOrigin(source_cp, direction, fraction)
    return source_origin


def _clamp_grid(v: int) -> int:
    # cell indices are 0..GRID_N-1 (16 cells -> indices 0..15)
    return max(0, min(GRID_N - 1, int(v)))


def point_to_font(gx: float, gy: float, baseline: float = DEFAULT_BASELINE) -> Tuple[float, float]:
    """Convert a grid point to font-unit coordinates.

    Points are **cell-centre aligned**: grid index ``g`` maps to the centre of cell ``g``,
    i.e. ``(g + 0.5) * SCALE`` horizontally. Vertically the grid is the em box
    ``[0, UPEM]`` and the baseline sits ``baseline`` cells above the bottom edge, so
    grid ``gy`` maps to ``(gy + 0.5 - baseline) * SCALE`` (y-up, descenders negative).
    """
    return ((gx + 0.5) * SCALE, (gy + 0.5 - baseline) * SCALE)


@dataclass(frozen=True)
class Point:
    """An integer grid point on the 16x16 grid."""
    x: int
    y: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", _clamp_grid(self.x))
        object.__setattr__(self, "y", _clamp_grid(self.y))

    def as_font_units(self, baseline: float = DEFAULT_BASELINE) -> Tuple[float, float]:
        """Grid point -> font-unit coordinates (cell-centre aligned, y-up, baseline at 0)."""
        return point_to_font(self.x, self.y, baseline)

    @staticmethod
    def from_font_units(x: Union[int, float], y: Union[int, float]) -> "Point":
        # invert the cell-centre mapping; baseline cancels out for the x/y index
        return Point(round(x / SCALE - 0.5), round(y / SCALE - 0.5))

    def __iter__(self):
        yield self.x
        yield self.y


@dataclass(frozen=True)
class StrokeOrigin:
    """Optional provenance for a stroke: where it was copied from.

    Records that the stroke came from the glyph associated with ``codepoint``, squished (if at
    all) toward ``direction`` by ``fraction``. ``direction``/``fraction`` are ``None`` for a plain
    (unsquished) copy; ``direction`` uses the same names as :func:`squish_strokes`
    (``"up"``/``"down"``/``"left"``/``"right"`` or a corner like ``"upleft"``).
    """
    codepoint: int
    direction: Optional[str] = None
    fraction: Optional[float] = None

    def to_dict(self) -> dict:
        d: dict = {"codepoint": self.codepoint}
        if self.direction is not None:
            d["direction"] = self.direction
        if self.fraction is not None:
            d["fraction"] = self.fraction
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "StrokeOrigin":
        return cls(int(d["codepoint"]), d.get("direction"), d.get("fraction"))


@dataclass(frozen=True)
class Stroke:
    """A single stroke: exactly two grid points plus a line-vs-single-quadratic flag.

    That is the *only* shape data a stroke carries. An arc's bend direction is a pure function of
    the ordering of ``p1`` and ``p2`` (no separate bend flag), and the pen/cap shape is a
    tool-level choice, not per-glyph data. ``origin`` is optional, non-shape provenance (see
    :class:`StrokeOrigin`) and never affects geometry or compilation.
    """
    p1: Point
    p2: Point
    shape: str = SHAPE_LINE
    origin: Optional[StrokeOrigin] = None

    def __post_init__(self) -> None:
        if self.shape not in _SUPPORTED_SHAPES:
            raise ValueError(f"unknown stroke shape: {self.shape!r}")

    @property
    def is_arc(self) -> bool:
        return self.shape == SHAPE_ARC

    def reversed(self) -> "Stroke":
        """The same stroke with the two points swapped (the arc bends the other way)."""
        return Stroke(self.p2, self.p1, self.shape, self.origin)

    def with_origin(self, origin: Optional[StrokeOrigin]) -> "Stroke":
        """A copy of this stroke carrying ``origin`` (geometry unchanged)."""
        return Stroke(self.p1, self.p2, self.shape, origin)

    def to_dict(self) -> dict:
        d = {
            "p1": [self.p1.x, self.p1.y],
            "p2": [self.p2.x, self.p2.y],
            "shape": self.shape,
        }
        if self.origin is not None:
            d["origin"] = self.origin.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Stroke":
        p1 = Point(*d["p1"])
        p2 = Point(*d["p2"])
        o = d.get("origin")
        return cls(p1, p2, d.get("shape", SHAPE_LINE),
                   StrokeOrigin.from_dict(o) if o else None)


def _clamp_box(x, y) -> Tuple[int, int]:
    """Clamp a bounding-box corner into the cell lattice (``0..GRID_W`` x ``0..GRID_H``).

    Corner coordinates are cell corners, so the maximum is the number of cells, not one less.
    """
    return (max(0, min(GRID_W, int(x))), max(0, min(GRID_H, int(y))))


@dataclass(frozen=True)
class Subcomponent:
    """A reference to another codepoint, placed in this glyph by a bounding box.

    This is a drawing feature, **not a diacritics system**: it draws the referenced glyph's
    strokes again inside a box, and carries no attachment, anchor or positioning. Combining marks
    are handled without it — a mark is a zero-advance glyph whose ink is shifted one cell left.

    ``start``/``end`` are the two opposite corners of the instance's bounding box, in **cell**
    coordinates of the *containing* glyph's grid (``0..GRID_W`` across, ``0..GRID_H`` up — cell
    coordinates, not cell-centre point indices, so the full cell box is ``(0,0)-(16,16)``).

    The box may be **negatively sized**: if ``end.x < start.x`` the instance is mirrored
    horizontally, and likewise vertically for ``y``. It is the *destination* box: the referenced
    glyph's own cell box — ``(0,0)`` to ``(its width, GRID_H)`` — is mapped onto it, so a
    full-cell box of the same width is the identity placement.

    That transform is applied to the referenced glyph's **strokes**, and only then are they
    expanded into outlines (``geometry``); the pen therefore keeps its own width instead of being
    scaled with the instance.

    ``origin`` is optional, non-shape provenance exactly like :attr:`Stroke.origin`: it records
    the glyph this instance was *copied out of* (which is not necessarily the codepoint it
    references — e.g. instances that came along with a copy-paste of a glyph's contents). Like a
    stroke's, it never affects geometry or compilation.
    """
    codepoint: int
    start: Tuple[int, int] = (0, 0)
    end: Tuple[int, int] = (GRID_W, GRID_H)
    origin: Optional[StrokeOrigin] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "codepoint", int(self.codepoint))
        object.__setattr__(self, "start", _clamp_box(*self.start))
        object.__setattr__(self, "end", _clamp_box(*self.end))

    @property
    def flipped_x(self) -> bool:
        return self.end[0] < self.start[0]

    @property
    def flipped_y(self) -> bool:
        return self.end[1] < self.start[1]

    @property
    def box(self) -> Tuple[int, int, int, int]:
        """``(x0, y0, x1, y1)`` as stored (sizes may be negative)."""
        return (self.start[0], self.start[1], self.end[0], self.end[1])

    def with_box(self, start, end) -> "Subcomponent":
        """The same instance moved to another box (provenance carried through)."""
        return Subcomponent(self.codepoint, (int(start[0]), int(start[1])),
                            (int(end[0]), int(end[1])), self.origin)

    def with_origin(self, origin: Optional[StrokeOrigin]) -> "Subcomponent":
        """A copy of this instance carrying ``origin`` (geometry unchanged)."""
        return Subcomponent(self.codepoint, self.start, self.end, origin)

    def to_dict(self) -> dict:
        d = {
            "codepoint": self.codepoint,
            "start": [self.start[0], self.start[1]],
            "end": [self.end[0], self.end[1]],
        }
        if self.origin is not None:
            d["origin"] = self.origin.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Subcomponent":
        start = d.get("start", (0, 0))
        end = d.get("end", (GRID_W, GRID_H))
        o = d.get("origin")
        return cls(int(d["codepoint"]), (int(start[0]), int(start[1])),
                   (int(end[0]), int(end[1])),
                   StrokeOrigin.from_dict(o) if o else None)


@dataclass
class Glyph:
    """A glyph, implicitly mapped to a Unicode codepoint.

    ``width`` is the advance width in grid units (8 or 16). ``combining`` glyphs get a
    zero advance (correct behaviour for diacritics) while keeping their nominal cell.

    When ``width``/``combining`` are not given explicitly they are **derived from
    Unicode**: full-width (East Asian Width ``W``/``F``) characters get a 16-cell width,
    and combining marks (general category ``Mn``/``Mc``/``Me``) start as combining.

    ``subcomponents`` are instances of other codepoints' glyphs (see :class:`Subcomponent`).
    They are kept in a separate list from ``strokes``; their *content* is not editable, only
    their box transform.
    """
    codepoint: int
    strokes: List[Stroke] = field(default_factory=list)
    subcomponents: List[Subcomponent] = field(default_factory=list)
    width: Optional[int] = None        # None -> derive from Unicode East Asian Width
    combining: Optional[bool] = None   # None -> derive from Unicode mark category
    name: Optional[str] = None
    empty: bool = False                # intentionally assigned but blank (e.g. space)

    def __post_init__(self) -> None:
        if self.width is None:
            self.width = GRID_W if unicode_fullwidth(self.codepoint) else GRID_W // 2
        if self.combining is None:
            self.combining = unicode_combining(self.codepoint)
        if self.width not in (GRID_W, GRID_W // 2):
            raise ValueError(f"glyph width must be 8 or 16, got {self.width}")

    # --- helpers ---------------------------------------------------------------
    @property
    def advance_units(self) -> int:
        """Advance width in font units (0 for combining marks)."""
        if self.combining:
            return 0
        return self.width * SCALE

    @property
    def cell_width_grid(self) -> int:
        """Nominal cell width in grid units, independent of the combining flag."""
        return self.width

    @property
    def cell_width_units(self) -> int:
        return self.width * SCALE

    def add_stroke(self, stroke: Stroke) -> None:
        self.strokes.append(stroke)

    def add_subcomponent(self, sub: Subcomponent) -> None:
        self.subcomponents.append(sub)

    @property
    def is_empty(self) -> bool:
        """True when the glyph carries no ink of its own and no instances."""
        return not self.strokes and not self.subcomponents

    def to_dict(self) -> dict:
        d: dict = {
            "codepoint": self.codepoint,
            "width": self.width,
            "combining": self.combining,
            "strokes": [s.to_dict() for s in self.strokes],
        }
        if self.subcomponents:
            d["subcomponents"] = [s.to_dict() for s in self.subcomponents]
        if self.name:
            d["name"] = self.name
        if self.empty:
            d["empty"] = True
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Glyph":
        # width/combining are only taken from JSON when present, otherwise derived from
        # the codepoint's Unicode properties in __post_init__.
        width = d.get("width")
        combining = d.get("combining")
        return cls(
            codepoint=int(d["codepoint"]),
            strokes=[Stroke.from_dict(s) for s in d.get("strokes", [])],
            subcomponents=[Subcomponent.from_dict(s)
                           for s in d.get("subcomponents", [])],
            width=(int(width) if width is not None else None),
            combining=(bool(combining) if combining is not None else None),
            name=d.get("name"),
            empty=bool(d.get("empty", False)),
        )


def squish_strokes(
    strokes: Iterable[Stroke], direction: str, width: int, fraction: float = 0.5,
    origin: Optional["StrokeOrigin"] = None,
) -> List[Stroke]:
    """Copy + squish ``strokes`` into a fraction of the grid, as new strokes.

    A linear (affine) transform maps the source grid space into a block of ``fraction`` of the
    glyph's ``width`` x ``GRID_H`` cell space, aligned as far as possible toward the given edge
    (or corner): ``"up"/"down"/"left"/"right"`` squeeze toward the single edge; a diagonal like
    ``"up-left"`` squeezes toward that corner along BOTH axes. ``fraction`` is 1/2 normally, or
    2/3 with Shift held. Coordinates are rounded to integers. This acts on the strokes being
    *added* only (existing strokes of the target glyph are untouched).
    """
    W, H = width, GRID_H
    xmax = max(1, W - 1)
    ymax = max(1, H - 1)
    f = fraction

    # Horizontal squeeze (None -> leave x untouched).
    if "left" in direction:
        x0, x1 = 0, max(0, round(W * f) - 1)
    elif "right" in direction:
        x0, x1 = min(max(0, W - 1), round(W * (1.0 - f))), W - 1
    else:
        x0 = x1 = None
    # Vertical squeeze (None -> leave y untouched).
    if "down" in direction:
        y0, y1 = 0, max(0, round(H * f) - 1)
    elif "up" in direction:
        y0, y1 = min(max(0, H - 1), round(H * (1.0 - f))), H - 1
    else:
        y0 = y1 = None

    def map_v(v, rmax, t0, t1):
        return round(t0 + v * (t1 - t0) / rmax)

    def map_pt(p: Point) -> Point:
        gx = _clamp_grid(map_v(p.x, xmax, x0, x1)) if x0 is not None else p.x
        gy = _clamp_grid(map_v(p.y, ymax, y0, y1)) if y0 is not None else p.y
        return Point(gx, gy)

    return [Stroke(map_pt(s.p1), map_pt(s.p2), s.shape, origin) for s in strokes]


def squish_subcomponent(
    sub: Subcomponent, direction: str, width: int, fraction: float = 0.5,
    origin: Optional[StrokeOrigin] = None,
) -> Subcomponent:
    """Copy + squish a subcomponent's box by the same linear map :func:`squish_strokes` uses.

    A box corner at cell coordinate ``c`` is the point index ``c - 0.5``, so it maps with the same
    ``map_v`` and comes back to the corner lattice. The map is monotonic, so a negatively sized
    box stays negatively sized.
    """
    W, H = width, GRID_H
    xmax = max(1, W - 1)
    ymax = max(1, H - 1)
    f = fraction

    if "left" in direction:
        x0, x1 = 0, max(0, round(W * f) - 1)
    elif "right" in direction:
        x0, x1 = min(max(0, W - 1), round(W * (1.0 - f))), W - 1
    else:
        x0 = x1 = None
    if "down" in direction:
        y0, y1 = 0, max(0, round(H * f) - 1)
    elif "up" in direction:
        y0, y1 = min(max(0, H - 1), round(H * (1.0 - f))), H - 1
    else:
        y0 = y1 = None

    def map_cell(c, rmax, t0, t1):
        if t0 is None:
            return c
        return round(t0 + (c - 0.5) * (t1 - t0) / rmax + 0.5)

    sx, sy = sub.start
    ex, ey = sub.end
    start = (_clamp_box(map_cell(sx, xmax, x0, x1), map_cell(sy, ymax, y0, y1)))
    end = (_clamp_box(map_cell(ex, xmax, x0, x1), map_cell(ey, ymax, y0, y1)))
    return Subcomponent(sub.codepoint, start, end, origin if origin is not None else sub.origin)


class StrokeFont:
    """An ordered collection of glyphs keyed by codepoint.

    Provides persistence (JSON) and helpers for grouping glyphs by Unicode block/script
    for the grid view.
    """

    def __init__(self) -> None:
        self.glyphs: Dict[int, Glyph] = {}
        self.metadata: dict = {
            "name": "Uffy Fallback",
            "height": GRID_H,
            "baseline": DEFAULT_BASELINE,
            "x_height": DEFAULT_X_HEIGHT,
            "cap_height": DEFAULT_CAP_HEIGHT,
        }
        # Subcomponents that a load had to drop because they closed a cycle (invalid data).
        # Never persisted; the editor reports it after loading.
        self.broken_subcomponent_cycles: List[Tuple[int, int]] = []

    @property
    def baseline(self) -> float:
        """Baseline position in grid cells above the em-box bottom."""
        try:
            return float(self.metadata.get("baseline", DEFAULT_BASELINE))
        except (TypeError, ValueError):
            return DEFAULT_BASELINE

    @property
    def x_height(self) -> float:
        try:
            return float(self.metadata.get("x_height", DEFAULT_X_HEIGHT))
        except (TypeError, ValueError):
            return DEFAULT_X_HEIGHT

    @property
    def cap_height(self) -> float:
        try:
            return float(self.metadata.get("cap_height", DEFAULT_CAP_HEIGHT))
        except (TypeError, ValueError):
            return DEFAULT_CAP_HEIGHT

    # --- editing ---------------------------------------------------------------
    def get(self, codepoint: int) -> Optional[Glyph]:
        return self.glyphs.get(codepoint)

    def ensure(self, codepoint: int) -> Glyph:
        g = self.glyphs.get(codepoint)
        if g is None:
            g = Glyph(codepoint=int(codepoint))
            self.glyphs[int(codepoint)] = g
        return g

    def add(self, glyph: Glyph) -> Glyph:
        self.glyphs[int(glyph.codepoint)] = glyph
        return glyph

    def remove(self, codepoint: int) -> bool:
        return self.glyphs.pop(int(codepoint), None) is not None

    def has(self, codepoint: int) -> bool:
        return int(codepoint) in self.glyphs

    def __len__(self) -> int:
        return len(self.glyphs)

    def __iter__(self) -> Iterable[int]:
        return iter(sorted(self.glyphs))

    def codepoints(self) -> List[int]:
        return sorted(self.glyphs)

    # --- subcomponent graph -----------------------------------------------------
    def references(self, codepoint: int) -> List[int]:
        """The codepoints ``codepoint``'s glyph references directly, in instance order."""
        g = self.glyphs.get(int(codepoint))
        return [s.codepoint for s in g.subcomponents] if g is not None else []

    def reaches(self, start: int, target: int) -> bool:
        """True if ``target`` is reachable from ``start`` by following subcomponents.

        Safe on cyclic data: every codepoint is visited once.
        """
        start, target = int(start), int(target)
        seen = {start}
        stack = [start]
        while stack:
            cp = stack.pop()
            for nxt in self.references(cp):
                if nxt == target:
                    return True
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return False

    def would_create_cycle(self, owner: int, target: int) -> bool:
        """True if adding ``owner -> target`` would make a subcomponent cycle.

        A self-reference counts, as does an indirect path back from ``target`` to ``owner``.
        """
        owner, target = int(owner), int(target)
        if owner == target:
            return True
        return self.reaches(target, owner)

    def break_subcomponent_cycles(self) -> List[Tuple[int, int]]:
        """Drop the subcomponents that close a cycle (invalid data). Returns what was dropped.

        Codepoints are walked in sorted order and each glyph's subcomponents in order, keeping an
        instance only when it does not reach back to its own glyph through instances that were
        already kept. Where a cycle is broken is therefore arbitrary but deterministic — any
        member of the cycle is a valid place to break it.
        """
        broken: List[Tuple[int, int]] = []
        # Edges kept so far, as a plain adjacency map (the glyphs' own lists are rewritten).
        kept: Dict[int, List[int]] = {cp: [] for cp in self.glyphs}

        def reaches(cp: int, target: int) -> bool:
            seen = {cp}
            stack = [cp]
            while stack:
                cur = stack.pop()
                for nxt in kept.get(cur, ()):
                    if nxt == target:
                        return True
                    if nxt not in seen:
                        seen.add(nxt)
                        stack.append(nxt)
            return False

        for cp in sorted(self.glyphs):
            g = self.glyphs[cp]
            survivors: List[Subcomponent] = []
            for sub in g.subcomponents:
                if sub.codepoint == cp or reaches(sub.codepoint, cp):
                    broken.append((cp, sub.codepoint))
                    continue
                survivors.append(sub)
                kept.setdefault(cp, []).append(sub.codepoint)
            g.subcomponents = survivors
        return broken

    # --- Unicode block grouping ------------------------------------------------
    def by_block(self) -> Dict[str, List[int]]:
        """Group codepoints by Unicode block name (sorted blocks, sorted codepoints)."""
        from .unicode_blocks import block_of, block_name
        out: Dict[str, List[int]] = {}
        for cp in self.codepoints():
            out.setdefault(block_name(block_of(cp)), []).append(cp)
        for vals in out.values():
            vals.sort()
        return out

    # --- persistence -----------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "metadata": self.metadata,
            "glyphs": [g.to_dict() for g in (self.glyphs[c] for c in self.codepoints())],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StrokeFont":
        sf = cls()
        sf.metadata.update(d.get("metadata", {}))
        for gd in d.get("glyphs", []):
            g = Glyph.from_dict(gd)
            sf.glyphs[g.codepoint] = g
        # A file must never carry a subcomponent cycle (the editor cannot author one): if it
        # does, the data is invalid, so break the cycles and record what was dropped.
        sf.broken_subcomponent_cycles = sf.break_subcomponent_cycles()
        return sf

    def to_json_text(self) -> str:
        """The stroke set as line-oriented JSON: one glyph per line, ascending codepoint.

        A glyph is the unit of editing, so a single line per glyph makes an edit rewrite exactly
        that line, an added glyph insert one line and a deleted glyph remove one line — which is
        what keeps the authored file diffable and mergeable. Strokes, subcomponents and points
        stay compact inside their glyph's line; the metadata block is one line too.
        """
        meta = json.dumps(self.metadata, ensure_ascii=False, sort_keys=True)
        cps = self.codepoints()
        lines = ["{", f'  "metadata": {meta},', '  "glyphs": [']
        for i, cp in enumerate(cps):
            body = json.dumps(self.glyphs[cp].to_dict(), ensure_ascii=False,
                              separators=(",", ":"))
            lines.append(f"    {body}{',' if i + 1 < len(cps) else ''}")
        lines.append("  ]")
        lines.append("}")
        return "\n".join(lines) + "\n"

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.to_json_text())

    @classmethod
    def load(cls, path: str) -> "StrokeFont":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    # --- codepoint helpers ------------------------------------------------------
    @staticmethod
    def iter_codepoints_in_range(
        start: int, end: int, existing: Optional[Sequence[int]] = None
    ) -> Iterable[int]:
        existing_set = set(existing or ())
        for cp in range(start, end + 1):
            if cp in existing_set:
                continue
            yield cp
