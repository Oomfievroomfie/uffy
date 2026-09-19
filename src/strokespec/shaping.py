"""HarfBuzz shaping with the glyph data supplied by a ``FontSource``.

HarfBuzz asks a font for glyph ids, advances and extents through callbacks, so an empty face plus
custom font funcs is a usable font without a font file. ``StrokeFontSource`` answers those callbacks
from a ``StrokeFont``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import uharfbuzz as hb

from .bidi import visual_runs
from .model import DEFAULT_BASELINE, GRID_H, HALF_WIDTH_UNITS, SCALE, UPEM, Glyph, StrokeFont


@dataclass(frozen=True)
class ShapedGlyph:
    """One shaped glyph: font units, y-up, exactly as HarfBuzz positioned it."""

    gid: int
    cluster: int
    x_advance: int = 0
    y_advance: int = 0
    x_offset: int = 0
    y_offset: int = 0
    name: Optional[str] = None
    text: str = ""          # the source characters this glyph's cluster came from


@dataclass
class ShapedRun:
    """A shaped string: the glyphs plus the segment properties HarfBuzz resolved."""

    text: str
    direction: str
    script: str
    language: str
    glyphs: List[ShapedGlyph] = field(default_factory=list)

    @property
    def advance(self) -> int:
        """Total horizontal advance of the run, in font units."""
        return sum(g.x_advance for g in self.glyphs)

    @property
    def rtl(self) -> bool:
        return self.direction == "rtl"

    def dump(self, label: str = "cluster") -> str:
        """A plain-text dump of the shaped glyphs (ids, clusters, advances, offsets)."""
        head = (f"direction={self.direction}  script={self.script}  language={self.language}  "
                f"glyphs={len(self.glyphs)}  advance={self.advance}u ({self.advance / SCALE:g} cells)")
        rows = [f"{'gid':>6} {label:>7} {'x_adv':>6} {'y_adv':>6} {'x_off':>6} {'y_off':>6}  text"]
        for g in self.glyphs:
            rows.append(f"{g.gid:>6} {g.cluster:>7} {g.x_advance:>6} {g.y_advance:>6} "
                        f"{g.x_offset:>6} {g.y_offset:>6}  {g.text!r}")
        return "\n".join([head] + rows)


class FontSource:
    """The glyph data HarfBuzz asks a font for. Implement this to shape something else.

    All values are in font units with ``upem`` units per em. ``nominal_glyph`` must return an int:
    the uharfbuzz callback wrapper rejects ``None``, so an unmapped codepoint answers 0
    (``.notdef``), which is what HarfBuzz would substitute anyway.
    """

    upem: int = UPEM

    def nominal_glyph(self, codepoint: int) -> int:
        raise NotImplementedError

    def h_advance(self, gid: int) -> int:
        raise NotImplementedError

    def v_advance(self, gid: int) -> int:
        return self.upem

    def font_extents(self) -> Tuple[int, int, int]:
        """``(ascender, descender, line_gap)``; the descender is negative."""
        raise NotImplementedError

    def glyph_name(self, gid: int) -> Optional[str]:
        return None


class StrokeFontSource(FontSource):
    """A ``StrokeFont`` answering HarfBuzz's font callbacks.

    Glyph ids are the codepoints in order, with 0 left for ``.notdef``. Advances are
    ``Glyph.advance_units`` (0 for combining marks, else 8 or 16 cells).
    """

    def __init__(self, strokefont: StrokeFont) -> None:
        self.strokefont = strokefont
        self._gid: Dict[int, int] = {cp: i for i, cp in enumerate(strokefont.codepoints(), 1)}
        self._cp: Dict[int, int] = {i: cp for cp, i in self._gid.items()}
        meta = strokefont.metadata
        self.height = float(meta.get("height", GRID_H))
        self.baseline = float(meta.get("baseline", DEFAULT_BASELINE))

    # --- HarfBuzz callbacks ----------------------------------------------------
    def nominal_glyph(self, codepoint: int) -> int:
        """The glyph id for a codepoint, or 0 (``.notdef``) when the set has no glyph for it."""
        return self._gid.get(codepoint, 0)

    def h_advance(self, gid: int) -> int:
        glyph = self.glyph(gid)
        return glyph.advance_units if glyph is not None else HALF_WIDTH_UNITS

    def font_extents(self) -> Tuple[int, int, int]:
        ascender = int(round((self.height - self.baseline) * SCALE))
        descender = -int(round(self.baseline * SCALE))
        return (ascender, descender, 0)

    def glyph_name(self, gid: int) -> Optional[str]:
        cp = self._cp.get(gid)
        return f"uni{cp:04X}" if cp is not None else ".notdef"

    # --- what the view draws ---------------------------------------------------
    def glyph(self, gid: int) -> Optional[Glyph]:
        """The authored glyph a shaped glyph id stands for (``None`` for ``.notdef``)."""
        cp = self._cp.get(gid)
        return self.strokefont.get(cp) if cp is not None else None


def font_funcs(source: FontSource) -> hb.FontFuncs:
    """The custom font funcs that make ``source`` usable as a HarfBuzz font."""
    funcs = hb.FontFuncs()
    funcs.set_nominal_glyph_func(lambda _font, ucp, _d: source.nominal_glyph(ucp))
    funcs.set_glyph_h_advance_func(lambda _font, gid, _d: source.h_advance(gid))
    funcs.set_glyph_v_advance_func(lambda _font, gid, _d: source.v_advance(gid))
    funcs.set_font_h_extents_func(
        lambda _font, _d: hb.FontExtents(*source.font_extents()))
    funcs.set_glyph_name_func(lambda _font, gid, _d: source.glyph_name(gid))
    return funcs


def _cluster_text(text: str, clusters: List[int]) -> List[str]:
    """The source characters belonging to each glyph, keyed by its cluster index."""
    if not text:
        return [""] * len(clusters)
    bounds = sorted(set(clusters))
    end_of = {c: (bounds[i + 1] if i + 1 < len(bounds) else len(text))
              for i, c in enumerate(bounds)}
    out = []
    for c in clusters:
        out.append(text[max(0, c):max(0, end_of.get(c, len(text)))])
    return out


class Shaper:
    """Shapes text with HarfBuzz against a :class:`FontSource`."""

    def __init__(self, source: FontSource) -> None:
        self.source: FontSource = source
        # An empty face, not hb.Face(None): shaping against the empty-face singleton returns every
        # advance as 0, even though the funcs answer correctly when called directly.
        self.font = hb.Font(hb.Face(hb.Blob(b"")))
        self.font.funcs = font_funcs(self.source)
        self.font.scale = (self.source.upem, self.source.upem)

    def shape(self, text: str, direction: Optional[str] = None, script: Optional[str] = None,
              language: Optional[str] = None, features: Optional[dict] = None) -> ShapedRun:
        """Shape ``text``; ``direction``/``script``/``language`` override what HarfBuzz guesses."""
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        if direction:
            buf.direction = direction
        if script:
            buf.script = script
        if language:
            buf.language = language
        hb.shape(self.font, buf, features or {})

        infos = list(buf.glyph_infos or [])
        positions = list(buf.glyph_positions or [])
        texts = _cluster_text(text, [i.cluster for i in infos])
        glyphs = [
            ShapedGlyph(
                gid=info.codepoint,
                cluster=info.cluster,
                x_advance=pos.x_advance,
                y_advance=pos.y_advance,
                x_offset=pos.x_offset,
                y_offset=pos.y_offset,
                name=self.source.glyph_name(info.codepoint),
                text=txt,
            )
            for info, pos, txt in zip(infos, positions, texts)
        ]
        return ShapedRun(text=text, direction=str(buf.direction), script=str(buf.script),
                         language=str(buf.language), glyphs=glyphs)

    def shape_visual(self, text: str, script: Optional[str] = None,
                     language: Optional[str] = None,
                     features: Optional[dict] = None) -> List[ShapedRun]:
        """Shape a paragraph: bidi runs, each shaped on its own, returned in visual order.

        HarfBuzz resolves one direction per buffer, so a paragraph that mixes directionalities
        has to be split first — the bidi algorithm's job, not the shaper's. A caller that wants
        one direction for the whole string calls :meth:`shape` instead.
        """
        return [
            self.shape(text[start:end], direction="rtl" if rtl else "ltr", script=script,
                       language=language, features=features)
            for start, end, rtl in visual_runs(text)
        ]
