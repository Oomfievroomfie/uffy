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

The block table can legitimately run ahead of the character database: assigned codepoints
(names/categories) come from `unicodedata2`, which lags a Unicode release by however long the
package takes to ship. Blocks the new release adds therefore read as **0 allocated** in
`COVERAGE.md` — they exist in the editor's block list, but nothing in them counts as allocated yet,
so they are excluded from the block-status buckets. That is expected, not a coverage regression.

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

## Subcomponents (instancing other codepoints)

A glyph may **instance** another codepoint instead of repeating its strokes. An instance is a
codepoint reference plus a **destination bounding box**, given as its **start and end corners** in
the *host* glyph's **cell** coordinates (integer coordinates are cell corners, so a full cell is
`(0,0)→(16,16)`; a `Point` index `g` sits at cell coordinate `g + 0.5`).

* **The box may be negatively sized.** `end.x < start.x` mirrors the instance horizontally,
  `end.y < start.y` vertically — both at once is a 180° turn. That is the only way an instance is
  flipped; there is no separate flip flag.
* **What the box maps is the referenced glyph's own cell box** — `(0,0)→(its width, 16)` — onto
  the destination box, so a full-cell box of the same width is the identity placement. The source
  box is the glyph's *cell*, never its ink: an ink-box fit would make every instance jump whenever
  the source is edited.
* **The transform is applied to the strokes, before they are expanded into outlines.** That is
  the whole point: the pen keeps its own (unscaled) one-cell width, and cell-quantised decisions
  — the butt snap onto the cell's own edge/diagonal, the arc's axis-aligned control corner — are
  taken in the *host* glyph's grid. Transforming the outlines afterwards would scale the pen and
  break both.
* **Each transformed endpoint is snapped to the nearest cell centre.** The stroke geometry only
  exists on the integer point lattice, and a box is rarely a whole number of cells tall/wide
  (U+51A4's second instance is 13 cells of a 16-cell-tall source), so without the snap every
  instanced stroke would sit between cells: an off-grid centreline, a butt snapped against a
  rounded-to-the-wrong-cell `_cell_min`, and an arc bulging to a fractional box corner. Snapping
  here is what makes an instance expand *exactly* like authored strokes — and it is the same
  rounding `pull contents` does to the strokes it inlines, so the strokes of a pulled instance
  land exactly where they were drawn.
* **Instances nest, and every level is rounded before the next transform.** A glyph renders
  itself in its own cell space: its own strokes, plus each instance resolved by rendering *its
  source in the source's own space* (which rounds there), then transporting that already-rounded
  stroke list into this glyph with the instance's box and rounding again
  (`geometry.flatten_cell_strokes` + `geometry.transport_cell_strokes`). The chain is therefore
  quantised once per level. Composing a whole chain of transforms and rounding once at the end
  gives *different* strokes and is not what a glyph renders: an instance of B must reproduce the
  strokes **B presents**, never a fresh derivation from B's raw subcomponent or stroke data. The
  practical statement of the rule: a subcomponent always shows exactly what its source glyph
  shows, transported — its appearance cannot depend on how deep the reference chain is.
* **A mirror is handled by swapping the two points of each stroke** when the composed transform
  has a negative determinant. An arc's bend is a pure function of point order, and its control
  point is recomputed from the transformed endpoints as the box corner on the right of the chord;
  swapping is exactly what makes that recomputation land on the *mirrored* corner. (For a
  diagonal transform with `sx·sy < 0` the argmin corner flips sign, and `cross(-d, c-p2) =
  cross(d, c-p1)`, so the swap reproduces the affine image of the original arc, including its
  nudged flat butt ends.)
* **Instances are not editable through their contents.** In the editor an instance is drawn with a
  dashed box and four corner handles, and it is picked up by those handles (or its stroke-list
  row) — never by the nodes inside it, which are not strokes of this glyph. Its *contents* can be
  taken over, though: **pull contents** inlines what the referenced codepoint *actually holds*,
  appropriately transformed. That is its own strokes (the box transform applied to their
  endpoints, snapped to the cell lattice, points swapped where the box mirrors) **and its own
  subcomponent references** (boxes mapped by the same transform) — not a flattened copy of one
  stroke list, so an instance of a glyph that is itself built from instances stays a mixture of
  strokes and instances, and each pulled stroke records its provenance (the referenced codepoint)
  exactly as the Copy button does. Since the pulled glyph's references cannot reach back to the
  host (that would already be a cycle), inlining never creates one.
* **Pulling re-quantises a nested instance's box, and that is expected to shift its rounding.**
  A box corner is an integer cell corner, so a nested box mapped by a scaled transform (it lands
  on something like `5.375`) has to be rounded back onto that lattice. The nested content is then
  rendered through the rounded box rather than the exact transformed one, which can flip the
  cell-centre snap of a nested endpoint by up to a cell. The instance's *own* strokes are
  unaffected — those are inlined through the exact transform — and the alternative (collapsing
  nested instances into strokes) would lose the structure the pull exists to preserve.
* **Instances carry optional provenance too.** A subcomponent's ``origin`` is exactly a stroke's:
  the glyph the instance was *copied out of*, which need not be the codepoint it references (an
  instance that arrived along with a copy-paste of some glyph's contents names that glyph). It is
  never shape and never compiled. Copy-paste carries a glyph's strokes **and** its instances, by
  the same rules: origins are stamped with the direct source only when it is allocated and differs
  from the target, squish-pasting squishes the instance's box by the same map as the points, and
  with the related list's **Copy as subcomponent** mode on, Ctrl+V instances the copied glyph
  instead of pasting its contents — *except* when the clipboard came from the glyph being pasted
  into, which always pastes the contents: an instance of yourself is a cycle, and Ctrl+C/Ctrl+V
  inside one glyph is the ordinary "duplicate the contents" gesture. Both paste paths keep cycles
  impossible: a paste that would close one is refused, and any instance in a batch that would close
  one is dropped with a warning (pulling contents needs no such check — a reference that reached
  back here would already be a cycle).
* **Cycles are structurally impossible.** Creating an instance whose target already leads back to
  the host glyph is refused up front (`StrokeFont.would_create_cycle`). A file that contains one
  anyway is invalid: loading breaks the cycles greedily in sorted codepoint order — dropping the
  instance that would close the cycle — and the editor reports how many references were removed.
  (A malformed file may also reference a codepoint that has no glyph; that instance simply
  converts to nothing.)

### In the compiled font

A glyph that instances something is written as a TrueType **composite**, because a `glyf` entry
is either simple contours or components and can never mix the two. Its parts are:

1. its own strokes (if any), and
2. one component per subcomponent, in order.

Every part is **pooled on its converted outline data alone** — never on the codepoint or the
transform — so one helper glyph (`.subNNNNN`, no cmap entry, zero advance) serves every glyph
that converts to those same outlines, no matter which codepoint or box produced them, and the
component offset is always `(0,0)` because the outlines are already in the host's absolute
position. Components nest, so `maxp.maxComponentDepth` is computed by fontTools as usual. A glyph
with no subcomponents is still written as a plain simple glyph with the pre-existing whole-glyph
merge (identical outlines *and* advance share one glyph).

**Every glyph carries the TrueType overlap hints.** The design is a set of independently expanded
per-stroke contours that **overlap**, so each glyph is written with ufo2ft's
`public.truetype.overlap` key, which becomes **`OVERLAP_SIMPLE`** (bit 6 of the first point flag) on
a simple glyph and **`OVERLAP_COMPOUND`** (bit 10 of the first component) on a composite. The spec
only says a rasterizer *may* use them — they *"activate additional logic required when contours
overlap to obtain correct rasterization"* — but without them a rasterizer is free to take its fast
non-overlapping path, which double-counts coverage where two strokes' edges meet (darkened
junctions / seams). Empty glyphs (`.null`, `nonmarkingreturn`, blank glyphs) are skipped by the
compiler, as they have no contours to overlap. The bits cost nothing: both exports are the same
size with and without them.

**The export policy is switchable** (`make_ufo(..., subcomponents=)`, `uffy build
--subcomponents`): `"reuse"` is the above, `"flatten"` inlines each instance's converted contours
into its host glyph instead and emits no composites and no helpers at all. Both policies emit the
*same parts* — the flatten path concatenates exactly the contour lists the reuse path would put in
helper glyphs, rather than merging the whole glyph's flattened contours in one pass — so the two
differ only in where the bytes live. Verified over every instance-bearing glyph: identical contour
coordinates (same multiset, same contour order; a contour may merely start at a different vertex,
which cannot affect rendering) and identical rendering at 256 ppem, as well as identical advances.
Measured on the 11888-glyph set at the time (126 glyphs instancing, 238 instances, 176 distinct
converted outlines): reuse 1,050,760 bytes vs flatten 1,046,820 — reuse is **3,940 bytes (0.38%)
larger**. That is because a 1.35× reuse factor barely pays for what a composite costs: reuse saves
~1 KB of outline bytes but spends ~2.8 KB on composite records, ~1.4 KB on the extra `loca`/`hmtx`
rows for the 176 helper glyphs, and shifts the `cmap` by ~1.8 KB. Sharing wins once shapes repeat
across many glyphs (a shape used twice already roughly breaks even; CJK components reused across
dozens of hanzi win clearly).

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
