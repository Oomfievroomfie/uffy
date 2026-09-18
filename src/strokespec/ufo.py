"""Build a UFO from a :class:`StrokeFont`.

This writes the UFO *authoring* format only (via ``ufoLib2``). It never reads or writes a
TTF/OTF itself — that is left to Google's CLI compiler (``gftools``/``fontmake``) so the
binary font tables are produced by the sanctioned tools rather than by hand.
"""

from __future__ import annotations

import os
from typing import Callable, List, Optional

from ufoLib2 import Font
from ufoLib2.objects import Component
from ufoLib2.objects import Glyph as UFOGlyph

from .model import GRID_H, SCALE, UPEM, StrokeFont, Glyph, PEN_RADIUS, PEN_CAP
from .geometry import glyph_contours, subcomponent_contours
from .geometry_merge import merge_stroke_edges

# Op is a tuple ("M"/"L"/"Q"/"C"/"Z", ...) — reused from geometry.
Op = tuple

# How a glyph's subcomponents reach the compiled font. Internal (build-time) policy, not
# authored data:
#   reuse   — every distinct converted outline becomes one shared helper glyph, and every glyph
#             producing it references that one glyph as a composite component (the default).
#   flatten — no composites: each instance is expanded into the glyph's own contours instead.
SUBCOMPONENT_REUSE = "reuse"
SUBCOMPONENT_FLATTEN = "flatten"
SUBCOMPONENT_MODES = (SUBCOMPONENT_REUSE, SUBCOMPONENT_FLATTEN)

# ufo2ft's per-glyph library key for the TrueType overlap hints: it becomes `OVERLAP_SIMPLE` on a
# simple glyph and `OVERLAP_COMPOUND` on a composite's first component.
TRUETYPE_OVERLAP_KEY = "public.truetype.overlap"

# Devanagari vowel signs the shaper reorders to sit BEFORE their consonant (verified against
# HarfBuzz: U+093F VOWEL SIGN I and U+094E VOWEL SIGN PRISHTHAMATRA E are the only two).
DEVANAGARI_PREBASE_MATRAS = {0x093F, 0x094E}


def _translate_x(ops: list, dx: float) -> list:
    """Shift x by ``dx`` in every M/L/Q/C op."""
    out = []
    for op in ops:
        k = op[0]
        if k in ("M", "L"):
            out.append((k, (op[1][0] + dx, op[1][1])))
        elif k == "Q":
            out.append((k, (op[1][0] + dx, op[1][1]), (op[2][0] + dx, op[2][1])))
        elif k == "C":
            out.append((k, (op[1][0] + dx, op[1][1]), (op[2][0] + dx, op[2][1]),
                        (op[3][0] + dx, op[3][1])))
        else:
            out.append(op)
    return out


def glyph_name(codepoint: int) -> str:
    """AGLFN-compatible Unicode glyph name."""
    if codepoint <= 0xFFFF:
        return "uni%04X" % codepoint
    return "u%X" % codepoint


def _outline_key(contours) -> tuple:
    """A hashable identity for an outline (list of contour op lists).

    Ops are tuples of hashable primitives (``('M', (x, y))`` etc.), so the whole sequence is
    hashable — glyphs with identical outlines share a key and can be merged.
    """
    parts = []
    for contour in contours:
        parts.extend(op for op in contour)
        parts.append(("@contour",))
    return tuple(parts)


def op_to_pen(ops: List[Op], pen) -> None:
    """Feed a list of M/L/Q/C/Z ops into a segment pen (supports quadratic+curves)."""
    for op in ops:
        kind = op[0]
        if kind == "M":
            pen.moveTo(op[1])
        elif kind == "L":
            pen.lineTo(op[1])
        elif kind == "C":
            pen.curveTo(op[1], op[2], op[3])
        elif kind == "Q":
            # store as TrueType-style quadratic qcurve points so they round-trip to glyf
            # quadratics (the arc is one quadratic per quarter, so keep it exactly).
            pen.qCurveTo(op[1], op[2])
        elif kind == "Z":
            pen.closePath()


def glyph_outline_from_strokes(glyph: Glyph, pen_radius: float, baseline: float) -> List[List[Op]]:
    """Expand a glyph's **own** strokes to fill outlines from our own stroke geometry.

    This is the canonical stroke expansion (each stroke swept by the pen into a closed
    contour, one contour per stroke, no boolean merge — overlapping strokes stay as
    redundant subpaths and union visually under the non-zero winding rule). The editor preview
    uses the same path, so preview and compiled font can never differ.

    Subcomponents are deliberately *not* included: a glyph that instances another codepoint is
    emitted as a composite (each part pooled separately), never with its instances' outlines
    merged into its own contour list.
    """
    return glyph_contours(glyph, r=pen_radius, baseline=baseline)


def _notdef_ops(width_units: int, cap: str, r: float, descent: float, ascent: float) -> List[List[Op]]:
    """A single solid rectangle tofu glyph so .notdef is clearly visible.

    A single contour (no reversed/inner contour) keeps the fill unambiguous under the
    non-zero winding rule — no even-odd or hand-managed winding tricks.
    """
    pad = r * 2
    x0, x1 = pad, width_units - pad
    y0 = -descent + r * 3
    y1 = ascent - r * 3
    if y1 - y0 < r * 4 or x1 - x0 < r * 4:
        return [[
            ("M", (pad, -descent + pad)),
            ("L", (width_units - pad, -descent + pad)),
            ("L", (width_units - pad, ascent - pad)),
            ("L", (pad, ascent - pad)),
            ("Z",),
        ]]
    return [[
        ("M", (x0, y0)),
        ("L", (x1, y0)),
        ("L", (x1, y1)),
        ("L", (x0, y1)),
        ("Z",),
    ]]


def make_ufo(
    strokefont: StrokeFont,
    *,
    family_name: Optional[str] = None,
    style_name: Optional[str] = "Regular",
    cap: str = PEN_CAP,
    pen_radius: int = PEN_RADIUS,
    notdef_width_units: Optional[int] = None,
    progress: Optional[Callable[[int, int], None]] = None,
    merge_edges: bool = True,
    subcomponents: str = SUBCOMPONENT_REUSE,
) -> Font:
    """Build the UFO **in memory** and return the ``ufoLib2.Font``.

    Keeping the font object lets the compiler hand it straight to ufo2ft, avoiding writing and
    re-reading a UFO package with one ``.glif`` file per glyph (which dominates build time for a
    font this size). Use :func:`build_ufo` when a UFO on disk is actually wanted.

    ``subcomponents`` selects how instances reach the binary (see the ``SUBCOMPONENT_*``
    constants): shared helper glyphs + composites (``"reuse"``, the default), or no composites at
    all with each instance flattened into its host glyph (``"flatten"``).
    """
    if subcomponents not in SUBCOMPONENT_MODES:
        raise ValueError(
            f"subcomponents must be one of {SUBCOMPONENT_MODES}, got {subcomponents!r}")
    if family_name is None:
        family_name = strokefont.metadata.get("name", "Uffy Fallback")

    baseline = strokefont.baseline
    x_height = strokefont.x_height
    cap_height = strokefont.cap_height
    # baseline sits ``baseline`` cells above the em-box bottom; font y-up has baseline at 0.
    ascent = (GRID_H - baseline) * SCALE
    descent = baseline * SCALE

    font = Font()
    info = font.info
    info.unitsPerEm = UPEM
    info.ascender = round(ascent)
    info.descender = round(-descent)
    info.capHeight = round(cap_height * SCALE)
    info.xHeight = round(x_height * SCALE)
    info.familyName = family_name
    info.styleName = style_name
    info.versionMajor = 1
    info.versionMinor = 0
    info.copyright = (
        "Copyright (C) 2024 strokespec. "
        "Designed as a stroke-defined fallback font."
    )
    info.openTypeHeadCreated = "2024/01/01 00:00:00"
    info.postscriptUnderlinePosition = -(UPEM // 8)
    info.postscriptUnderlineThickness = SCALE // 2
    # Two ufo2ft defaults differ from what an ordinary horizontal font carries, and both are
    # wrong for us:
    #  * openTypeHheaCaretSlopeRise defaults to UPEM (256 here). The spec wants 1 with slopeRun 0
    #    for a font with a horizontal baseline (a vertical caret); every ordinary font, Unifont
    #    included, has 1.
    #  * openTypeOS2Type defaults to [2] -> fsType 4 = "Preview & Print embedding" (embedding
    #    restricted). Unifont ships fsType 0 (installable), which is right for a libre font.
    info.openTypeHheaCaretSlopeRise = 1
    info.openTypeHheaCaretSlopeRun = 0
    info.openTypeOS2Type = []

    font.lib["com.strokespec.type"] = "stroke-fallback"
    font.lib["com.strokespec.penRadius"] = str(PEN_RADIUS)
    font.lib["com.strokespec.cap"] = cap
    font.lib["com.strokespec.subcomponents"] = subcomponents
    # Drop per-glyph PostScript names from the compiled font. The 'post' table is otherwise
    # written in format 2.0 with a glyph name for every glyph (one per cmap entry), which grows
    # enormously for a large fallback font (it was ~1/10th of the compiled file). Format 3.0
    # keeps a valid but near-empty post table; name/post are still present, just minimal. The
    # glyphs stay addressable via the cmap, which is all a fallback font needs.
    font.lib["com.github.googlei18n.ufo2ft.keepGlyphNames"] = False

    def new_glyph(name: str) -> UFOGlyph:
        """Create a glyph, marked as built from possibly-overlapping contours.

        Every glyph here is a set of independently expanded per-stroke contours that **overlap by
        design** — that is how strokes union, under the non-zero winding rule — so no rasterizer
        may assume otherwise. ``public.truetype.overlap`` makes ufo2ft set ``OVERLAP_SIMPLE`` on a
        simple glyph and ``OVERLAP_COMPOUND`` on a composite, which is what tells a rasterizer to
        use its overlap-aware scheme instead of the fast non-overlapping one; without it a
        rasterizer is free to produce seams or double-counted coverage where two strokes' edges
        meet. The flags are hints: rasterizers that ignore them are unaffected.
        """
        glyph = font.newGlyph(name)
        glyph.lib[TRUETYPE_OVERLAP_KEY] = True
        return glyph

    def expand(glyph: Glyph) -> List[List[Op]]:
        conts = glyph_outline_from_strokes(glyph, pen_radius, baseline)
        if merge_edges:
            conts = merge_stroke_edges(conts)
        # Combining glyphs: place the ink one cell to the LEFT, as Unifont does (its combining
        # marks have zero advance with negative-x outlines, e.g. U+093F ink at x -1024..-320).
        # A zero-advance mark is drawn at the pen, which is already past its base, so shifting the
        # ink left by the glyph's own cell width lands it back on the base's cell — no GPOS and no
        # mark/abvm/mkmk needed. Exception: the Devanagari pre-base matras, which the shaper
        # reorders to sit *before* the base: there the pen is already at the base's cell, so they
        # are left unshifted (their ink then overlays the base's cell, as authored).
        if glyph.combining and glyph.codepoint not in DEVANAGARI_PREBASE_MATRAS:
            dx = -glyph.width * SCALE
            conts = [_translate_x(c, dx) for c in conts]
        return conts

    def expand_full(glyph: Glyph) -> List[List[Op]]:
        """The glyph's outline with its instances **flattened in** (``flatten`` mode).

        The parts are exactly the ones ``reuse`` mode would emit — its own strokes, then each
        instance's converted contours — just concatenated into this glyph's own contour list
        instead of living in helper glyphs, so both policies carry the same contours.
        """
        conts = expand(glyph)
        for sub in glyph.subcomponents:
            conts.extend(instance_contours(glyph, sub))
        return conts

    def rendered(glyph: Glyph) -> List[List[Op]]:
        """A glyph's full outline: instances flattened in only in ``flatten`` mode."""
        if glyph.subcomponents and subcomponents == SUBCOMPONENT_FLATTEN:
            return expand_full(glyph)
        return expand(glyph)

    def instance_contours(glyph: Glyph, sub) -> List[List[Op]]:
        """The contours one subcomponent of ``glyph`` converts to, in ``glyph``'s own space.

        The referenced glyph's strokes are transformed by the instance's box *before* being
        expanded (``geometry.subcomponent_contours``), so the pen keeps its own width. The
        combining-mark shift is the host glyph's, exactly as for its own strokes.
        """
        conts = subcomponent_contours(
            sub, strokefont.get, r=pen_radius, cap=cap, baseline=baseline
        )
        if merge_edges:
            conts = merge_stroke_edges(conts)
        if glyph.combining and glyph.codepoint not in DEVANAGARI_PREBASE_MATRAS:
            conts = [_translate_x(c, -glyph.width * SCALE) for c in conts]
        return conts

    # Pool of already-emitted outlines, keyed on the OUTLINE DATA alone (never on the codepoint
    # or the transform). Every distinct outline an instance converts to becomes one shared helper
    # glyph, and every glyph that produces those same outlines references that one glyph as a
    # TrueType composite component — so a shape instanced in twenty glyphs is stored once.
    pool: "dict[tuple, str]" = {}
    pool_seq = [0]

    def pooled(contours: List[List[Op]]) -> Optional[str]:
        """Emit ``contours`` once and return the helper glyph name (``None`` if empty)."""
        if not contours:
            return None
        key = _outline_key(contours)
        name = pool.get(key)
        if name is None:
            pool_seq[0] += 1
            name = ".sub%05d" % pool_seq[0]
            helper = new_glyph(name)
            helper.unicodes = []
            helper.width = 0
            pen = helper.getPen()
            for contour in contours:
                op_to_pen(contour, pen)
            order.append(name)
            pool[key] = name
        return name

    # .notdef always first.
    nd_cmds = _notdef_ops(UPEM, cap, pen_radius, descent, ascent)
    notdef = new_glyph(".notdef")
    notdef.unicode = None
    notdef.width = notdef_width_units or UPEM
    pen = notdef.getPen()
    for contour in nd_cmds:
        op_to_pen(contour, pen)

    order: List[str] = [".notdef"]

    # Reserve the classic Macintosh glyph-order slots: glyph 1 must be `.null` and glyph 2
    # `nonmarkingreturn` — both zero-advance and outline-less. Renderers and converters still
    # assume glyph 1 is `.null` (zero advance), so leaving a real glyph there silently zeroes its
    # advance. Without these our lowest codepoint — U+0020 SPACE — lands in slot 1, and the ASCII
    # space stops advancing (invisible at the end of a string, caret does not move) even though its
    # hmtx entry is correct. Unifont ships these two slots for exactly this reason.
    for _slot in (".null", "nonmarkingreturn"):
        _dummy = new_glyph(_slot)
        _dummy.unicodes = []
        _dummy.width = 0
        order.append(_slot)

    cps = strokefont.codepoints()
    total = len(cps)
    # Merge glyphs with identical outlines (same contour ops AND same advance width) into a
    # single glyph that all those codepoints map to, so the compiled font doesn't store a
    # duplicate outline for each duplicated codepoint. Group key = (outline ops, advance units).
    # **Empty glyphs are never merged**: each codepoint keeps its own glyph. There is nothing to
    # save (an empty glyph is just a loca entry and an hmtx row), and sharing one glyph across
    # many codepoints is a needless departure from ordinary fonts — e.g. it made U+0020 share a
    # glyph with U+061C/U+2000–U+200A/U+202F/U+205F/U+FFA0.
    #
    # Glyphs carrying subcomponents do NOT take part in this whole-glyph merge in ``reuse`` mode:
    # they are emitted as composites below, where each of their parts is pooled on its own. In
    # ``flatten`` mode they are ordinary simple glyphs like every other.
    groups: "dict[Any, List[int]]" = {}
    composite_cps: List[int] = []
    for i, cp in enumerate(cps):
        if progress is not None:
            progress(i, total)
        glyph = strokefont.get(cp)
        if glyph is None:
            continue
        if glyph.subcomponents and subcomponents == SUBCOMPONENT_REUSE:
            composite_cps.append(cp)
            continue
        contours = rendered(glyph)
        key = ("empty", cp) if not contours else (_outline_key(contours), glyph.advance_units)
        groups.setdefault(key, []).append(cp)

    for key, group in groups.items():
        rep_cp = group[0]
        rep_glyph = strokefont.get(rep_cp)
        name = glyph_name(rep_cp)
        ufo_glyph = new_glyph(name)
        ufo_glyph.unicodes = group          # map every codepoint in the group to this glyph
        ufo_glyph.width = rep_glyph.advance_units
        pen = ufo_glyph.getPen()
        contours = rendered(rep_glyph)
        for contour in contours:
            op_to_pen(contour, pen)
        order.append(name)

    # Subcomponent-bearing glyphs: a glyph is either simple (its own contours) or a composite
    # (components), so each glyph that instances another codepoint is written as a composite with
    # one component per part — its own strokes first (if any), then one per subcomponent, in
    # order. Each part's converted outlines are pooled on their outline data, so identical parts
    # (from any glyph, any codepoint, any transform) are stored once and shared.
    for cp in composite_cps:
        glyph = strokefont.get(cp)
        if glyph is None:
            continue
        parts: List[str] = []
        own = pooled(expand(glyph))
        if own is not None:
            parts.append(own)
        for sub in glyph.subcomponents:
            name = pooled(instance_contours(glyph, sub))
            if name is not None:
                parts.append(name)
        if not parts:
            # Every part was empty (e.g. instances of blank glyphs): nothing to draw.
            name = glyph_name(cp)
            ufo_glyph = new_glyph(name)
            ufo_glyph.unicodes = [cp]
            ufo_glyph.width = glyph.advance_units
            order.append(name)
            continue
        name = glyph_name(cp)
        ufo_glyph = new_glyph(name)
        ufo_glyph.unicodes = [cp]
        ufo_glyph.width = glyph.advance_units
        for part in parts:
            ufo_glyph.components.append(
                Component(baseGlyph=part, transformation=(1, 0, 0, 1, 0, 0))
            )
        order.append(name)

    # Give the font a de-facto-empty GPOS table. HarfBuzz only runs its extents-based fallback
    # mark positioning when the face has NO GPOS positioning at all
    # (`plan.apply_gpos = hb_ot_layout_has_positioning(face)`; `fallback_mark_positioning =
    # !apply_gpos`), and when it runs it overrides the ink placement we control. (Unifont ships a
    # bare GPOS table for this reason.)
    #
    # It is emitted as a STANDALONE lookup that no feature references: feaLib then writes a GPOS
    # with an empty FeatureList and an empty ScriptList, so no software reports any feature as
    # existing, while the table itself is still present and non-empty. The rule covers an
    # unreachable phantom glyph (no cmap entry, no outline), so no reachable glyph carries any
    # positioning data either.
    phantom = new_glyph(".gposphantom")
    phantom.unicodes = []
    phantom.width = 0
    order.append(".gposphantom")
    font.features.text = "lookup gpos_noop {\n    pos .gposphantom <1 0 0 0>;\n} gpos_noop;\n"

    # Set the glyph layout order explicitly so the font is deterministic.
    font.glyphOrder = order
    return font


def build_ufo(strokefont: StrokeFont, output_dir: str, **kwargs) -> str:
    """Build the UFO (see :func:`make_ufo`) and save it at ``output_dir``; return that path."""
    font = make_ufo(strokefont, **kwargs)
    font.save(output_dir, overwrite=True)
    return output_dir


