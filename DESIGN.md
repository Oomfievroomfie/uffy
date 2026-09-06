# Design notes

These are the *reasons* behind the major design choices. Everything here is an implementation
or model rationale — the *what* (what a stroke is, what the app does) lives in `README.md`.

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

## The stroke model

* Points are **cell-centre aligned**: grid index `g` is the centre of cell `g`, so a coordinate
  `x` maps to `(x + 0.5) * SCALE` font units. The outermost vertices sit half a cell inside the
  em box, leaving visible padding in the editor.
* A glyph holds **up to 32 strokes**.
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
* A glyph may be flagged **combining** (zero advance). The fallback ships no GPOS mark
  positioning, so fully correct mark attachment is out of scope (consistent with "no shaping").

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

The bundled fallback fonts live in `src/strokespec/data/fonts/` (Noto scripts for every block
Windows doesn't cover + GNU Unifont BMP and Unifont Upper), with their licenses alongside. They
are registered at startup and attached per-block, so a block that the system already renders
gets no override at all.
