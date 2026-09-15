# Design notes

These are the *reasons* behind the major design choices. Everything here is an implementation
or model rationale — the *what* (what a stroke is, what the app does) lives in `README.md`.

## Vendored Unicode data needs periodic updating

`src/strokespec/data/Blocks.txt` is a snapshot of the official UCD `Blocks.txt` (parsed at
runtime by `unicode_blocks.py`; nothing is hardcoded and it is never re-downloaded). It must be
**re-fetched from time to time** whenever Unicode adds, renames, or splits blocks, so the editor's
block list and the coverage report stay accurate. Re-download from
`https://www.unicode.org/Public/UCD/latest/ucd/Blocks.txt` and replace the vendored copy. Private-
use blocks are deliberately excluded from the editor's block list.

## Strokes are the source of truth

A glyph is authored as **strokes**, never as outlines or bitmaps. One stroke is exactly two grid
points plus a line-vs-arc flag; that is all the data it carries. The outlines are produced from
the strokes by our own geometry (`geometry.py`) — it is **not** a preview convenience. The
editor canvas, the grid previews and the compiled font all expand strokes through the *same*
code path, so preview and output can never disagree about a shape.

### Why not `.glyphs`/`glyphsLib`

`fontmake`'s `glyphsLib` pipeline has **no stroke support** (no stroke attributes/classes on
paths), so authored strokes would not survive it. We expand the strokes to outlines ourselves
instead, and hand the outlines to the compiler.

> ⚠️ **TTF/OTF policy.** The Python `fontTools` library is used **only** for the UFO authoring
> format; its TTF/OTF read/write code paths are never used to *build* the font. The binary is
> produced by Google's tooling — by default **`ufo2ft.compileTTF`** in-process, on the in-memory
> font from `make_ufo()` (the same engine the `fontmake` CLI drives, minus the UFO package
> round-trip, which is ~2.4x faster); the **`fontmake`** / **`gftools`** CLIs remain selectable
> via `tool=`. Reference-font previews are rendered with **FreeType (Pillow)** and **HarfBuzz
> (uharfbuzz)**.

## The stroke model

* Points are **cell-centre aligned**: grid index `g` is the centre of cell `g`, so a coordinate
  `x` maps to `(x + 0.5) * SCALE` font units. The outermost vertices sit half a cell inside the
  em box, leaving visible padding in the editor.
* A glyph holds any number of strokes. (A **design guideline** of ~32 was formerly enforced as a
  hard limit; it is not a technical limitation and is no longer enforced.)
* An `arc` is a **single quadratic per quarter**. Its control point is the corner of the
  axis-aligned bounding box of the two endpoints that the arc bows toward, so at each endpoint
  the tangent is **axial** (horizontal/vertical) **and full-strength**. It never bulges past the
  chord. The **bend direction is a pure function of the ordering of the two points** — there is
  no bend flag; swapping the endpoints bends the arc the other way (this is what "reverse"
  flips). Axis-aligned or zero-length "arcs" are really lines.
* Each stroke is swept by a **one-grid-cell-diameter pen** that follows the centreline and
  expands it into a closed contour. The pen/cap shape is a tool-level choice (round by default),
  not per-glyph data.
* **One contour per stroke, no boolean merge.** Overlapping strokes stay as redundant subpaths
  and union visually under the **non-zero winding rule** (like TrueType/OpenType; not even-odd),
  so overlaps fill instead of punching holes. Consequently the pen radius is 32 (a one-cell
  diameter) so a minimum-size arc/semicircle stays well inside the box.
* A glyph may be flagged **combining** (zero advance). Combining glyphs are exported with their
  ink shifted **one cell to the left** (negative x), exactly as Unifont does — its combining marks
  are zero-advance with negative-x outlines (e.g. U+093F ink at x −1024…−320, U+0940 at −704…0).
  A zero-advance mark is drawn at the pen, which is already past its base, so the shifted ink
  lands back on the base's cell: an authored 16x16 combining glyph overlays its base's 16x16 cell.
  The font carries a **de-facto-empty GPOS table** purely so HarfBuzz does not run its
  extents-based *fallback* mark positioning on top: HarfBuzz applies that fallback only when the
  face has no GPOS at all (`plan.apply_gpos = hb_ot_layout_has_positioning(face)`), and it would
  otherwise override the ink. It is emitted as a **standalone lookup that no feature references**,
  covering only an unreachable phantom glyph (no cmap entry, no outline), so the compiled font has
  an empty FeatureList and ScriptList — no software reports any feature — and no positioning data
  on any reachable glyph. That is why Unifont ships a bare GPOS table too. No
  `mark`/`abvm`/`mkmk`, no anchors. The
  one exception to the shift is the **Devanagari pre-base matras** (U+093F, U+094E), which the
  shaper reorders to sit *before* their consonant: there the pen is already at the base's cell, so
  they are left unshifted. Complex shaping is otherwise out of scope (consistent with "no
  shaping").

## Fallback philosophy

This is a **fallback** font, not a full one: no complex shaping, no ligatures, no kerning, no
variable axes, no COLR. It is a **dual-width monospace** font in the style of old Japanese
fonts — every glyph is 8×16 or 16×16 (width × height in grid units), and that is its advance
width.

## Native-text font fallback (per codepoint)

The editor renders a codepoint's character as native text (the big "native reference" panel and
the small per-cell badge). Qt's own glyph-fallback chain — a hardcoded, CJK-centric try-font
list in `qwindowsfontdatabasebase.cpp` — covers most scripts. We want our bundled rare-script
fonts and Unifont to run **after** that chain, and only for blocks the system genuinely can't
render.

There is no Qt API to append glyph fallbacks *after* the platform chain: the platform chain is
always appended last, and `addApplicationFallbackFontFamily` (the official API) prepends into
the application bucket, before the platform chain. `QFont::insertSubstitution` feeds a separate
substitution table that the glyph-fallback path does not consult. So the editor builds the
family list itself, per codepoint:

* Decide "system-covered" using **Qt's own oracle** (`QFontMetrics(app.font()).inFontUcs4`),
  which reflects the real platform fallback chain — not a hand-maintained font list (a prior
  hand-rolled `SYSTEM_FALLBACKS` + fontTools cmap read was incomplete and lossy, causing false
  overrides for e.g. CJK Compatibility).
* If the system covers the codepoint → return just the primary family (no override, no
  Unifont).
* Otherwise → attach only the rare-script fonts that actually cover it, then Unifont as the
  absolute last resort.

The consultation pool is the OS-default Windows fonts (the complete `DEFAULT_WINDOWS_FONTS`
set) plus, for the **hanzi/kanji blocks only**, the Hanazono Mincho faces **HanaMinA** and
**HanaMinB** (`HANZI_FALLBACK_FONTS`). HanaMin is not an OS default, but it covers the CJK
ideograph blocks far more completely than any OS font; it is attached *after* the OS defaults
(so an OS font that covers the character still wins) and *before* Unifont, and only when the
face is actually present and its cmap covers the codepoint.

The bundled fallback fonts live in `src/strokespec/data/fonts/` (Noto scripts for every block
Windows doesn't cover + GNU Unifont BMP and Unifont Upper), with their licenses alongside. They
are registered at startup and attached per-block, so a block that the system already renders
gets no override at all.

## Notes / known limitations

* If `uv` can't initialise its cache in `%LOCALAPPDATA%` (e.g. under a restricted sandbox),
  point it somewhere writable:
  `$env:UV_CACHE_DIR="$PWD\.uv-cache"; uv sync`.
* The grid candidate computation queries reference fonts; for very large folders / the
  CJK block the first pass can take a moment (it is cached afterwards).
* On a headless box, run GUI code with `QT_QPA_PLATFORM=offscreen`.
