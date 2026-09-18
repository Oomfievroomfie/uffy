# Uffy

Rapid font editor (vibecoded) + readable universal fallback font (human-made CC0)

## Coverage

- **11770** codepoints authored — **11741** with strokes, **29** intentionally empty.
- **7.7%** of the **153626** allocated Unicode codepoints across the full block table.
- Block status (allocated codepoints per block): **81 filled** (100%), **6 mostly-filled**
  (50–99%), **17 slightly-filled** (1–49%), **234 untouched** (0%). (11 blocks have no
  allocated codepoints.)
- Per-block breakdown: [`COVERAGE.md`](COVERAGE.md).

## Goals

- Easy-to-use stroke-based font editor that produces very little information per glyph
- - 8x16 or 16x16 per character, single strokes, straight or semicircle
- **Scroll down to see the hotkeys. There are a lot and they're all important.**
- Pan-unicode fallback font made in that editor, or as close as possible
- Small filesize, making any concessions needed for that
- Text rendered with that fallback font is >readable<, not like the "Last Resort" font
- Support for common or general unicode blocks first, then niche ones later
- - If you run into a block that looks really easy to implement and you get an itch to do it,
    just do it, even if it's a niche script

## Non-goals

- Complex macro tools in the editor
- Finer-than-grid or non-semicircle or accurate semicircle or non-unit-width strokes
- Glyphs that look good
- Glyphs that are readable at 16px pixelated renderings (use Unifont for that)
- Strict semantic accuracy (e.g. rendering an "enclosed rectangular box inverted A" as a
    normal enclosed A with a double-thick box outline, to save strokes and time, is fine)
- Complex shaping (e.g. arabic is going to render non-ligatured/non-cursive)
- Kerning
- Correct spacing or sizing for scripts that normally require complex shaping
- Support for any private-use glyphs
- Any variation selectors or regional selectors at all (e.g. hz vs jp forms? pick one)

## Authoring guidelines

DO NOT TRACE. Accidental similarity is OK. See the "Important" section below for reasoning.

Any two straight strokes cost basically the same.

A straight stroke that's secretly made of two straight strokes but looks like one, is two
straight strokes, and costs as much as two disconnected straight strokes.

Straight strokes snap their ends to a cell edge. This is determined by their angle, or,
for 45 degree strokes, whether they're up-down or down-up from start point to end point.

A curved stroke probably costs about 1.5 straight strokes. Their direction is determined by
their start-point-to-end-point direction.

A curved stroke that is actually straight and renders as a straight stroke costs as much as
a straight stroke, not as much as a curved stroke.

Minimize the cost needed to make a glyph readable, especially for big or complex scripts.

Only prioritize symmetry over stroke count for very common characters or characters where
it's critically important to get the symmetry right for semantic reasons.

For plain text confusables, try to make them look different. For non-plain-text confusables,
if they're conceptually identical characters (e.g. "encircled capital A"), do an exact copy-
paste; do not just manually rebuild the same glyph.

Two glyphs with EXACTLY the same contents cost as much as a single copy of that glyph, not
as much as two copies. Glyphs with EXACTLY the same contents are deduplicated on export.
(Exception: **empty** glyphs are never merged — each keeps its own glyph, since an empty glyph
costs nothing to store and sharing one across many codepoints is a needless oddity.)

Two lines that exactly meet end-to-end, as in, edge-to-edge, not endpoint-to-startpoint,
cost silghtly less than the two lines would cost if they didn't meet at all. Such edges are
merged on export. This only applies to the ttf export; it does not apply to other formats.
(The way strokes are so limited here is meant to make it possible to quickly autogenerate
glyphs on demand from a very small amount of binary data, for programs that can do that.
But such programs don't benefit from outline-level operations like edge merging.)

For conceptual glyphs, try to think of the fewest-stroked way to express the concept. For
example, if there's a series of glyphs where the concept is "growing old in life", and the
canonical form is something like "baby, child, teen, adult", but those forms aren't the only
valid interpretation of the way the glyphs are defined, it's OK to rethink it as "seed,
sprout, flower, field". This came up on tarot cards, for example, but it applies to emoji
too. A glyph for "thunder clouds with rain" doesn't require you to draw big fat fluffy
clouds -- you can draw faint line clouds that imply the presence of bigger thicker clouds.

For highly repetitive operations, figure out how to express them via keyboard and make a
macro with https://github.com/LOUDO56/PyMacroRecord/ or some other macro tool. (Note: many
macro tools are trojan horse malware. This one isn't, though. *Do not* get it from anywhere
other than github.)

## Important

DO NOT trace the reference fonts. They are only present for metric alignment and glyph form
reference purposes. We are NOT doing font design laundering. All existing work has followed
this rule. (Tracing fonts, as opposed to digitally pirating them, is considered legally
okayish in the US and japan and most reasonable countries, unless the font is protected by
something like a design patent, but we still explicitly do not trace fonts.)

In cases where there is simply no reason not to end up with the same shape as another font,
like capital H or L, you do not need to avoid creating the same shape. That isn't how IP law
works; such things are considered too trivial, obvious, or unavoidable to be copyrightable.
This is not an assertion that all simple characters are uncopyrightable. Some are. Do not
trace or make 1:1 copies. If in the process of trying to make a given glyph look good within
the constraints of the weirdly limited font format this tool produces, it results in a shape
that is annoyingly similar to or overlaps almost perfectly against a reference font, accept
that it's almost certainly just the only obvious way to produce that glyph at this resolution.
But do not use this as an excuse to trace the reference fonts.

## About

Author a **universal fallback font** as *strokes* instead of outlines or bitmaps, then compile
it to a real TTF using Google's CLI font tools. This is a **fallback** font, not a universal
one: no complex shaping, no ligatures, no kerning, no variable axes, no COLR. It is a
**dual-width monospace** font in the style of old Japanese fonts — every glyph is `8x16` or
`16x16` (width x height in grid units), and that is its advance width.

<img width="1920" height="1027" alt="image" src="https://github.com/user-attachments/assets/99d5c838-1df9-4d75-a5c5-26719e4f45ed" />

## The model

* A glyph is defined on a **16x16 grid** of cells; points are **cell-centre aligned**.
* The only *shape* data a stroke carries is **two grid points plus a
  line-vs-single-quadratic flag**:
  * `line` — a straight segment between the two points.
  * `arc` — a single quadratic per quarter, confined to the axis-aligned bounding box of the
    two points. Its **bend direction is a pure function of the ordering of the two points** —
    there is no bend flag; swapping the two points bends the arc the other way.
* A stroke may also carry optional, non-shape **provenance** (`origin`): the **codepoint** of the
  glyph it was copied from, plus the **squish direction and amount** if it was squished (absent
  for an unsquished copy). The direct source is stamped **only** when it is an *allocated*
  codepoint **different from the glyph being pasted into** — so it always names the glyph actually
  copied from (never traced back through that glyph's own origins), forming a graph of relations.
  Otherwise the copied strokes' existing provenance is carried through unchanged: pasting into the
  same codepoint, or copying out of an **unallocated** scratch/dummy glyph, copies the provenance
  data rather than overwriting or deleting it. It is recorded when copying/squishing from a
  related glyph and when copy-pasting, survives flips/rotations/nudges and undo/redo, and never
  affects geometry or compilation.
* Each stroke is painted with a **one-grid-cell-diameter pen**, expanded into a closed outline,
  filled with the **non-zero winding rule** so overlapping strokes **union**.
* A glyph may also **instance** another codepoint as a **subcomponent**: a reference plus the
  **start and end corners of a bounding box**, stored in *cell* coordinates (integer values are
  cell corners, so the full cell box is `(0,0)→(16,16)`). The box may be **negatively sized** —
  `end.x < start.x` mirrors the instance horizontally, and likewise vertically. The referenced
  glyph's own cell box `(0,0)→(its width,16)` is mapped onto that box, so a full-cell box of the
  same width is the identity placement. The transform is applied to the referenced glyph's
  **strokes**, each transformed endpoint is snapped to the nearest **cell centre** (the stroke
  lattice — a box is rarely a whole number of cells, and off-lattice endpoints would put the
  pen, the butt snap and an arc's box on the wrong cell), and only then are they expanded into
  outlines, so the pen keeps its own width and the cell-quantised butt snap is taken in the
  *host* glyph's grid. Instances nest (transforms
  compose), and a subcomponent's own strokes are never editable — only its box. **Cycles are
  impossible**: a reference that would close one is refused, and a file that contains one has it
  broken on load (with a warning).
* The **baseline**, **x-height** and **cap-height** are globally configurable (grid cells above
  the bottom edge of the em box). Set them via **Font → Metrics Options…** or in the stroke
  set's metadata.
* A glyph may be flagged **combining** (zero advance). Combining glyphs are exported with their
  ink shifted one cell to the **left** (negative x), as Unifont does, so a zero-advance mark drawn
  after its base lands on the base's cell. The font ships a **de-facto-empty GPOS table**, emitted
  as a standalone lookup that no feature references (covering only an unreachable phantom glyph),
  so the font reports **no features at all** while HarfBuzz's extents-based *fallback* mark
  positioning stays switched off and cannot override that ink — no `mark`/`abvm`/`mkmk`.
  Exception: the Devanagari pre-base matras (U+093F, U+094E), which the shaper reorders before
  their consonant, are left unshifted.
* Defaults come from Unicode: East Asian Width **W/F** → **16** cells, otherwise **8**;
  general category **Mn/Mc/Me** → starts **combining**; both overridable per glyph.

The full design rationale (why arcs are a single quadratic, why no boolean merge, and so on) is
in `DESIGN.md`.

## Design units

The grid is the em box. One grid cell = **16 font units**:

| thing                | value                                              |
|----------------------|----------------------------------------------------|
| units per em         | 256                                                |
| full-width glyph     | 16 grid = 256 units                                |
| half-width glyph     | 8 grid  = 128 units                                |
| grid cell            | 16 units                                           |
| pen radius           | 8 units (1 grid-cell diameter)                     |
| baseline (default)   | 2.0 cells above the em-box bottom (Unifont, → 32 units descent) |
| x-height (default)   | 10.0 cells above the em-box bottom (Unifont)            |
| cap-height (default) | 12.0 cells above the em-box bottom (Unifont)            |

## Directory layout

```
src/strokespec/
  model.py         Stroke / Glyph / StrokeFont + JSON persistence + Unicode defaults
  geometry.py      stroke -> outline (the real stroke->outline expansion used everywhere)
  svgout.py        strokes -> stroked SVG (debug/inspection; arcs are a single quadratic)
  ufo.py           write a UFO (***authoring format only***) from the stroke geometry
  compiler.py      UFO (in memory) -> TTF via ufo2ft; gftools/fontmake CLI optional
  refbrowser.py    scan a folder of reference fonts; render glyphs via FreeType+HarfBuzz
  cli.py           `uffy` command-line entry point
  editor/          PySide6 graphical editor
    grid.py        interactive, FontForge-style glyph grid (clickable cells)
    glyph_canvas.py 16x16 stroke-authoring canvas (line/arc, drag endpoints, reverse to flip)
    main_window.py  main window (grid left, editor right, reference-font dock)
    fontfallback.py  per-codepoint native-text font fallback for the editor previews
    uiutil.py       stroke -> QPainterPath painting + PIL -> QImage
  data/fonts/      bundled fallback fonts (Noto scripts + GNU Unifont) + their licenses
  examples/sample.strokes.json
```

## The GUI

`uffy-editor` opens a window with:

* an **interactive glyph grid** on the left — a scrollable `QListView` of individually
  clickable cells, grouped by Unicode block, with a search box and a "show unassigned"
  toggle. Each cell shows the authored stroke glyph, or a **reference-font preview** for
  codepoints with no stroke glyph yet, or an empty slot. Clicking a cell opens that glyph in
  the editor. Each visible cell also carries a small **native-text character badge** (the
  codepoint's character rendered with the fallback chain).
* a **glyph editor** on the right (the 16x16 canvas + stroke list + tool row). Hovering a row in
  the stroke list shows that stroke's provenance (if any) as a tooltip; **subcomponents** are
  listed there too, naming the codepoint they reference.
* a **Reference Fonts** dock with a big **native reference** panel and a **related-glyphs**
  list.
* **File → Compile TTF…** to build the font with Google's tools.

The full set of keyboard and mouse controls is below.

## Controls

### Glyph editor (canvas — needs keyboard focus)

* **Click two grid points** to add a stroke; **drag an endpoint** to move it.
* **Subcomponents** (instances of other codepoints) are drawn with a **dashed bounding box** and a
  **handle on each corner**: drag a handle to move that corner, dragging one corner past its
  opposite flips the instance. Their contained strokes are not draggable here — only the box is
  (plus the whole-glyph moves below, which carry the boxes with them). **Right-click** a box (or
  its row in the stroke list) for its menu: **open the referenced codepoint**, or **pull its
  contents into this glyph** — which inlines the referenced glyph's *actual contents*, its strokes
  **and** its own subcomponent references, appropriately transformed, and kills the instance (so
  nested instances stay instances rather than collapsing into one flat stroke list).
* Tool toggle: **Line** / **Arc** (toolbar).
* **Ctrl+Z** undo · **Ctrl+Shift+Z** redo
* **Ctrl+C** copy all strokes · **Ctrl+V** paste (append) · **Ctrl+H** clear the glyph
* **Ctrl+V** while **holding an arrow key** squishes the paste toward that side/corner:
  one arrow = side (`Up`/`Down`/`Left`/`Right`), two arrows = corner (e.g. `Up`+`Left` =
  top-left). Holding **Shift** gives 2/3 size, otherwise 1/2.
* **Arrow keys** `Left`/`Right`/`Up`/`Down` — move to the neighbouring codepoint (and open it).
* **W/A/S/D** — nudge the whole glyph up/left/down/right by one cell.
* **PageUp / PageDown** — nudge the whole glyph up / down.
* **F** — flip horizontally · **Flip vertically** (toolbar) · **Rotate 90°** (toolbar) —
  rotate 90° clockwise (in 16x16 space).
* **B** — toggle the selected stroke between line and arc.
* **R** — reverse the selected stroke's points (flips an arc's bend).
* **Delete** — delete the selected stroke (or the selected subcomponent).
* **N** — toggle the glyph width between 8 and 16.
* **M** — toggle the combining flag.
* **Reference ghost** checkbox — overlay a reference-font ghost behind your strokes. Ghosts are
  centred by their **advance box** (pen origin to advance width), not by their ink, so a glyph with
  asymmetric side bearings lands where its metrics put it, and each is fitted to the line height by
  **height alone** — width never drives the fit, so a ghost may be wider than the glyph's cell. A
  reference-font ghost is placed by its baseline, its ascender+descender box fitted to the line
  height. **Ghost from native reference** in the Reference Fonts panel (off by default) takes the
  ghost from the codepoint's native OS font instead; that one starts where the **descender ends**
  rather than at the baseline, so its box runs from the grid bottom to the grid top and never
  spills out of the 16-cell column. Most of Unicode (CJK, emoji, symbols) has no baseline worth
  aligning to — that is what the native ghost is for — while the baseline scripts are served by
  the reference fonts, which do pin the baseline.

### Codepoint grid (left)

* **Block** selector, **search** box (substring over the character's name, the character
  itself and its hex form), **Show unassigned** toggle.
* The count label under the list shows how many of the block's **allocated** codepoints are
  actually covered, as a percentage.
* Each block remembers its own scroll position when you switch blocks and come back.
* Click a cell to open that glyph; the list only scrolls when *you* scroll it (opening/editing
  a codepoint does not move the view).

### Reference Fonts dock (right)

* **Add folder** to load reference fonts; **Clear** to drop them.
* **Native reference** panel — the current codepoint as a big character.
* **Related glyphs** — components/IDS of the current codepoint, each with **Copy** / **Open**,
  four **axial squish arrows** (↑↓←→), and four **diagonal squish buttons** (↖↗↙↘) below them.
  **Copy** copies the related glyph's strokes in (a proper component); a squish button copies it
  into a fraction of the current glyph's grid toward that edge/corner (same as pasting while
  holding the arrow key(s)); **Shift** gives 2/3 size instead of 1/2. Above the list, **Copy as
  subcomponent** is one mode toggle for the whole list: with it on, Copy (and the squish buttons)
  **instance** the related glyph as a subcomponent of the current glyph instead — Copy at the
  identity placement, a squish button with the instance's bounding box in that fraction of the
  grid. A reference that would close a subcomponent cycle is refused, with a warning.

### Menus

* **File → Compile TTF…** (with progress).
* **Font → Metrics Options…** — set baseline, x-height, cap-height.

## Quick start

```sh
uv sync                                # create .venv + install (fontmake, PySide6, …)
uv run uffy-editor                     # open the graphical editor
uv run uffy build examples/sample.strokes.json -o fallback.ttf
uv run uffy preview examples/sample.strokes.json -c 0x4E2D -o out.png
uv run uffy refs C:\SomeFontFolder -s "ABC" -o refs.png
```

Compiling uses **`ufo2ft` in-process** by default — the same engine the `fontmake` CLI drives, on
the in-memory UFO, so no UFO package is written or re-read (that round-trip dominated build time).
Pass `--tool fontmake` (or `--tool gftools`, if installed) to shell out to a Google CLI compiler
instead.

## Packaging (standalone executable)

```sh
uv run pyinstaller strokespec.spec
```

This produces `dist/uffy-editor` (windowed). The compiler commands (`gftools`/`fontmake`)
must be present on `PATH` at runtime; the packaged GUI simply shells out to them.
