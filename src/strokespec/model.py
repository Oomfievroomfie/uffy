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
SCALE = 64           # font units per grid cell
UPEM = SCALE * GRID_H  # 1024 units per em
PEN_RADIUS = SCALE // 2  # 32 units -> 1 grid-cell-diameter pen
# Pen butt style is a tool-level choice (not per-glyph data). "square" = a square butt: the
# stroke extends by the pen radius (a half cell) so its flat end lands on a cell boundary.
PEN_CAP = "square"
FULL_WIDTH_UNITS = GRID_W * SCALE   # 1024
HALF_WIDTH_UNITS = (GRID_W // 2) * SCALE  # 512

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
    import unicodedata
    try:
        return unicodedata.east_asian_width(chr(codepoint)) in ("W", "F")
    except Exception:
        return False


def unicode_combining(codepoint: int) -> bool:
    """True if the codepoint is a combining mark (general category Mn/Mc/Me)."""
    import unicodedata
    try:
        return unicodedata.category(chr(codepoint)) in ("Mn", "Mc", "Me")
    except Exception:
        return False


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
class Stroke:
    """A single stroke: exactly two grid points plus a line-vs-single-quadratic flag.

    That is the *only* data a stroke carries. An arc's bend direction is a pure function of
    the ordering of ``p1`` and ``p2`` (no separate bend flag), and the pen/cap shape is a
    tool-level choice, not per-glyph data.
    """
    p1: Point
    p2: Point
    shape: str = SHAPE_LINE

    def __post_init__(self) -> None:
        if self.shape not in _SUPPORTED_SHAPES:
            raise ValueError(f"unknown stroke shape: {self.shape!r}")

    @property
    def is_arc(self) -> bool:
        return self.shape == SHAPE_ARC

    def reversed(self) -> "Stroke":
        """The same stroke with the two points swapped (the arc bends the other way)."""
        return Stroke(self.p2, self.p1, self.shape)

    def to_dict(self) -> dict:
        return {
            "p1": [self.p1.x, self.p1.y],
            "p2": [self.p2.x, self.p2.y],
            "shape": self.shape,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Stroke":
        p1 = Point(*d["p1"])
        p2 = Point(*d["p2"])
        return cls(p1, p2, d.get("shape", SHAPE_LINE))


@dataclass
class Glyph:
    """A glyph, implicitly mapped to a Unicode codepoint.

    ``width`` is the advance width in grid units (8 or 16). ``combining`` glyphs get a
    zero advance (correct behaviour for diacritics) while keeping their nominal cell.

    When ``width``/``combining`` are not given explicitly they are **derived from
    Unicode**: full-width (East Asian Width ``W``/``F``) characters get a 16-cell width,
    and combining marks (general category ``Mn``/``Mc``/``Me``) start as combining.
    """
    codepoint: int
    strokes: List[Stroke] = field(default_factory=list)
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
        if len(self.strokes) > 32:
            raise ValueError("a glyph may have at most 32 strokes")

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
        if len(self.strokes) >= 32:
            raise ValueError("a glyph may have at most 32 strokes")
        self.strokes.append(stroke)

    def to_dict(self) -> dict:
        d: dict = {
            "codepoint": self.codepoint,
            "width": self.width,
            "combining": self.combining,
            "strokes": [s.to_dict() for s in self.strokes],
        }
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
            width=(int(width) if width is not None else None),
            combining=(bool(combining) if combining is not None else None),
            name=d.get("name"),
            empty=bool(d.get("empty", False)),
        )


def squish_strokes(
    strokes: Iterable[Stroke], direction: str, width: int, fraction: float = 0.5
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

    return [Stroke(map_pt(s.p1), map_pt(s.p2), s.shape) for s in strokes]


class StrokeFont:
    """An ordered collection of glyphs keyed by codepoint.

    Provides persistence (JSON) and helpers for grouping glyphs by Unicode block/script
    for the grid view.
    """

    def __init__(self) -> None:
        self.glyphs: Dict[int, Glyph] = {}
        self.metadata: dict = {
            "name": "strokespec fallback",
            "height": GRID_H,
            "baseline": DEFAULT_BASELINE,
            "x_height": DEFAULT_X_HEIGHT,
            "cap_height": DEFAULT_CAP_HEIGHT,
        }

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
        return sf

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, ensure_ascii=False, indent=2)

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
