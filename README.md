# strokespec

Author a **universal fallback font** as *strokes* instead of outlines or bitmaps, then compile
it to a real TTF using Google's CLI font tools. This is a **fallback** font, not a universal
one: no complex shaping, no ligatures, no kerning, no variable axes, no COLR. It is a
**dual-width monospace** font in the style of old Japanese fonts — every glyph is `8x16` or
`16x16` (width x height in grid units), and that is its advance width.

> ⚠️ **TTF/OTF policy.** The Python `fontTools` library is used **only** for the UFO authoring
> format. Its TTF/OTF read/write code paths are **never** used here. The actual binary is
> produced by Google's CLI compilers — **`gftools`** (preferred) or **`fontmake`** (fallback) —
> and reference-font previews are rendered with **FreeType (Pillow)** and **HarfBuzz
> (uharfbuzz)**, again without fontTools' TTF/OTF code.

## The model

* A glyph is defined on a **16×16 grid** of cells. Each grid index `g` (0..15) is the
  **centre** of cell `g`, so points are **cell-centre aligned** — think "zoomed in on a
  16×16 unifont glyph". The outermost vertices sit half a cell inside the em box, which
  leaves visible padding around them in the editor.
* A glyph is a set of **up to 32 strokes**.
* The only data a stroke carries is **exactly two grid points plus a line-vs-quarter-ellipse
  flag**:
  * `line` — a straight segment between the two points;
  * `arc` — a **scaled quarter-circle** (a quarter-**ellipse**) that is confined to the
    axis-aligned bounding box of the two points and is **tangent-aligned** to it (at each
    endpoint the tangent is horizontal/vertical). It never bulges past the chord. Its **bend
    direction is a pure function of the ordering of the two points** — there is no bend
    flag. Swapping the two points bends the arc the other way.
* Each stroke is "painted" with a pen of **one grid-cell diameter** that follows the
  centreline and expands it into an outline. The pen/cap shape is a **tool-level choice**
  (a round pen by default), not per-glyph data. The outline is filled with the **non-zero
  winding rule** (like TrueType/OpenType; *not* even-odd), so overlapping strokes **union**
  instead of punching holes.
* The **baseline is globally configurable**, in grid cells above the bottom edge of the em
  box (default 3.0). Descenders are drawn in the cells *below* the baseline and map to a
  negative font y. **x-height** and **cap-height** are also configurable and are written
  to the font's metrics. Set them via **Font → Metrics Options…** in the editor (or in the
  stroke set's metadata).
* A glyph may be flagged **combining** (e.g. a diacritic). Combining glyphs get **zero
  advance** (typographically correct); note the fallback ships **no** GPOS mark
  positioning, so fully correct mark attachment is out of scope (consistent with
  "no shaping").

## Design units

The grid is the em box. One grid cell = **64 font units**:

| thing                | value                                              |
|----------------------|----------------------------------------------------|
| units per em         | 1024                                               |
| full-width glyph     | 16 grid = 1024 units                               |
| half-width glyph     | 8 grid  = 512  units                               |
| grid cell            | 64 units                                           |
| pen radius           | 32 units (1 grid-cell diameter)                    |
| baseline (default)   | 2.0 cells above the em-box bottom (Unifont, → 128 units descent) |
| x-height (default)   | 10.0 cells above the em-box bottom (Unifont)            |
| cap-height (default) | 12.0 cells above the em-box bottom (Unifont)            |

The pen radius (32) is chosen so a minimum-size arc/semicircle stays well inside the box.
Coordinates are rounded to integers when written to the font.

## Directory layout

```
src/strokespec/
  model.py         Stroke / Glyph / StrokeFont + JSON persistence + Unicode blocks
  geometry.py      stroke -> outline (centreline offset, round/butt caps, quarter arcs)
  ufo.py           write a UFO (***authoring format only***) from a StrokeFont
  compiler.py      UFO -> TTF via Google CLI (gftools, else fontmake); gftools fix
  refbrowser.py    scan a folder of reference fonts; render glyphs via FreeType+HarfBuzz
  cli.py           `uffy` command-line entry point
  editor/          PySide6 graphical editor
    grid.py        interactive, FontForge-style glyph grid (clickable cells)
    glyph_canvas.py 16×16 stroke-authoring canvas (line/arc/bend, drag endpoints)
    main_window.py  main window (grid left, editor right, reference-font dock)
    uiutil.py       stroke -> QPainterPath painting + PIL -> QImage
  examples/sample.strokes.json
```

## The GUI (the main deliverable)

`uffy-editor` opens a window with:

* an **interactive glyph grid** on the left — a scrollable `QListView` of individually
  clickable cells, grouped by Unicode block, with a search box and a "show unassigned"
  toggle. Each cell shows
  * the authored stroke glyph, **or**
  * a **reference-font preview** for codepoints with no stroke glyph yet, **or**
  * an empty slot.
  Clicking a cell opens that glyph in the editor.
* a **glyph editor** on the right:
  * a 16×16 grid canvas where you **click two points** to add a stroke; **drag an endpoint**
    to move it; a **Line / Arc** tool toggle; an arc's bend is flipped by **reversing the
    stroke's points**;
  * a **stroke list** (select / delete / reverse to flip an arc), a **combining** checkbox
    and a **width** selector (8 / 16);
  * a faint **reference ghost** behind your strokes, scaled by the reference font's
    cap-height and aligned to its baseline so it lines up with your grid's metric guides
    (toggleable);
  * **Font → Metrics Options…** to set the baseline, x-height and cap-height.
* a **Reference Fonts** dock where you point the app at a folder of fonts to preview while
  authoring;
* **File → Compile TTF…** to build the font with Google's tools.

## Quick start

```sh
uv sync                                # create .venv + install (fontmake, PySide6, …)
uv run uffy-editor                     # open the graphical editor
uv run uffy build examples/sample.strokes.json -o fallback.ttf
uv run uffy preview examples/sample.strokes.json -c 0x4E2D -o out.png
uv run uffy refs C:\SomeFontFolder -s "ABC" -o refs.png
```

**Install gftools** (preferred compiler) with:

```sh
uv sync --extra validate
```

If gftools is not available the compiler automatically falls back to **fontmake** (both are
Google CLI tools), and if gftools is present it is also used for `fix`/`validate`.

## Notes / known limitations

* If `uv` can't initialise its cache in `%LOCALAPPDATA%` (e.g. under a restricted sandbox),
  point it somewhere writable:
  `$env:UV_CACHE_DIR="$PWD\.uv-cache"; uv sync`.
* The grid candidate computation queries reference fonts; for very large folders / the
  CJK block the first pass can take a moment (it is cached afterwards).
* On a headless box, run GUI code with `QT_QPA_PLATFORM=offscreen`.

## Packaging (standalone executable)

```sh
uv run pyinstaller strokespec.spec
```

This produces `dist/uffy-editor` (windowed). The compiler commands (`gftools`/`fontmake`)
must be present on `PATH` at runtime; the packaged GUI simply shells out to them.
