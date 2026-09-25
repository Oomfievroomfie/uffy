# strokespec workspace — environment notes

Hard-won learnings about working efficiently in this sandboxed Windows workspace.
These are the things that repeatedly cost time when forgotten.

## Python / uv (the only toolchain)

- **uv is the ONLY Python/dependency toolchain — mandatory.** All other Python project tooling
  and dependency tooling is forbidden (**no pip, no `uv pip install`, no poetry/conda**, etc.).
  Adding a dependency goes in `pyproject.toml`; it is installed by `uv sync`/`uv add`.

- **uv must be given a LOCAL workspace directory, because we're inside a sandbox.** Set the
  cache dirs to paths INSIDE the **current project folder** (`$PWD`); the default
  `%LOCALAPPDATA%` cache is sandbox-denied with a cache-initialize permission error. The venv is
  also project-local (`$PWD\.venv`).

  ```pwsh
  $env:UV_CACHE_DIR="$PWD\.uv-cache"; $env:UV_PYTHON_INSTALL_DIR="$PWD\.uv-python"; uv ... 
  ```

  Without these, uv fails immediately with "failed to initialize cache … Access is denied".

- A dependency with only an sdist **cannot build here**: uv builds into
  `.uv-cache/builds-v0`, which the workspace-write sandbox denies. `gftools` depends on
  `ufomerge`, which has no wheel for this venv's Python 3.14, so `uv sync --extra validate`
  fails building it. **Workaround:** `gftools` is effectively unavailable; use **fontmake**
  (installed, prebuilt) as the compiler. `available_compile_tools()` will report only
  fontmake; the compiler falls back to it automatically.

- Aborted uv builds leave stale `.tmp*` dirs in `.uv-cache/builds-v0`; `Remove-Item` cannot
  delete them under the sandbox. They are gitignored, harmless residue — leave them alone.

- **`uv run` / `uv sync` / `uv add` that re-install the `strokespec` project fail here:**
  uv reinstalls the project package, which rewrites the entry-point console script
  `.venv\Scripts\uffy-editor.exe`, and the workspace-write sandbox denies that write
  ("failed to remove file … uffy-editor.exe: Access is denied (os error 5)" / `[sandbox: file
  access denied under workspace-write mode]`). This is a **sandbox write denial, NOT a locked
  process** — do NOT go hunting for or killing processes on the user's machine. Proceed:

  - To run code that avoids the rebuild: `uv run --no-sync python …` (skips reinstalling the
    project, so it never touches the console script).
  - To add a dependency: edit `pyproject.toml` directly (a plain workspace write), then let
    `uv sync` install it when the sandbox permits the console-script write; otherwise test with
    `uv run --no-sync` (works as long as the package is already present in the venv).

## Shell / git

- Use the Git-for-Windows binary explicitly — msys64 git fails:
  `& 'C:\Program Files\Git\cmd\git.exe' <cmd>` from `C:\Users\wareya\dev\uffy`.

- Never `git add -A`. It pulls in sensitive authored data. Always stage specific paths.
  `glyphs.strokes.json` (the live authored glyph file) and untracked `todo.txt` /
  `* - Copy.json` must NOT be committed unless the user explicitly asks; leave them unstaged.

- Capturing a child process's stdout over a pipe is denied on Windows (EPERM). Redirect to a
  log file and read it back (the compiler already does this for fontmake/gftools logs).

- **Never pipe Python through PowerShell.** No `python -c "…"`, no here-strings, no `<<`. Write a
  throwaway script **file** (fresh name each time) and run `uv run --no-sync python <file>`.
  PowerShell mangles quoting/escaping and it has repeatedly wasted time.

- `fallback.ttf` in the repo root is the user's **live, always-in-progress export** — never treat
  it as stale, never delete it, never commit it.

## Sandbox discipline

- **The filesystem rule: never read outside `C:\Users\wareya\dev\uffy`.** No reading, listing,
  globbing, grepping, or stat-ing `C:\Windows`, `C:\Program Files`, `%USERPROFILE%`, `%TEMP%`, the
  registry, or any other drive — for any reason. If a task seems to need a file out there, the
  approach is wrong: get the data some other way, or say so.

- **The network is not outside the workspace.** `web_fetch`, `web_search`, `git clone`, `uv`, and
  downloads into the workspace are ordinary tools with no restriction. The rule above is about the
  filesystem and nothing else.

- File writes run under a **workspace-write** sandbox. NO privilege escalation, NO sandbox
  escapes, work only inside `C:\Users\wareya\dev\uffy`. Do not attempt `sandbox_permissions`.

- A `[sandbox: file access denied]` message may simply **be the user pressing "no"** on an
  escalation prompt. Treat it as a direct user instruction, not a harness bug to reason around.

- **Harness bug (temp test files):** once you `Remove-Item` a temp file (e.g. `_t.py`), the
  `write` tool later rejects re-creating that exact path ("file no longer exists — re-read the
  file, then retry") but `read` also fails because the path is gone, so you cannot satisfy the
  re-read. Workaround: **use a fresh filename** for each throwaway test script (`_t.py`,
  `_t2.py`, …) rather than deleting and re-creating the same one.

## Hard rules about the authored data (non-negotiable)

- **Data bugs are DATA bugs.** When a defect lives in the authored data (`glyphs.strokes.json` — a
  glyph's `combining`/`width` flag, its strokes, its codepoint), it gets fixed **in the data**. Do
  NOT patch around it in code, do not add a code path that compensates for it, and do not even
  *consider* a code workaround — unless the user explicitly tells you to. A code change that makes
  a wrong data value behave correctly is forbidden: it hides the bad data and makes the tool lie
  about what the user actually authored.
- **Do NOT fix data bugs yourself.** Diagnose, report the exact glyph and field and what is wrong
  with it, and stop there. Correcting authored data is the user's job. Do not hand-edit
  `glyphs.strokes.json` unless the user explicitly asks for that specific edit.
- **"It was a data bug" is a complete answer.** Report it and stop. Do not follow it with a fix,
  a workaround, or a suggestion of one.

## Method (the most important lessons)

- **Run the code, do not reason in a vacuum.** Verify the ACTUAL output against the user's
  concrete constraints by executing it — never against a predicate you invented that happens
  to pass. Several bugs persisted because I checked a self-written sanity test instead of the
  real requirement (e.g. the line-butt snapping: a floating `2r` segment vs. the cell's own
  edge/diagonal differs wildly even though both "touched a grid line").

- **Never write self-referential or self-congratulatory text into deliverables.** No "look how
  careful I am", no noting that a requirement was met, no justifying a design choice, no explaining
  what the user already knows. That applies to comments, docstrings, README/DESIGN text and commit
  messages. A comment states a fact the code needs; anything else is noise.

- **Read the requirement literally; the user means exactly what they say.** Terminology and
  precision matter and are corrected harshly: flat ends are **butts**, not caps; snap to the
  **cell's own edge/diagonal** (real corner coords), not a floating segment; diagonal case
  only for **exactly 45°**, etc. Do not "improve" or guess — implement the stated constraint.

- After any geometry change, verify per-stroke: signed area > 0, valid contours, and (for
  snapping) bring the concrete grid-unit cases to check. Then build the TTF with fontmake and
  render a sample string via PIL/FreeType to confirm it looks right before committing.

## Project conventions that matter

- **ALWAYS commit your work.** After every finished feature or bug-fix, stage the specific changed
  files and commit (the user can amend later). Do not leave finished work uncommitted. Never
  `git add -A`; stage exact paths only.

- **Experiments are NOT committed until the user explicitly says to commit them.** If the user
  calls something an experiment — or you are just trying something out / comparing options to
  show them a result — leave it **uncommitted** in the working tree, report what you found, and
  wait for an explicit "commit" instruction before staging any of it. An experiment becoming the
  default (e.g. "make it the default") is an explicit instruction to commit it; simply having
  finished or validated it is not.

- **`COVERAGE.md` must be committed whenever you update it.** Any time you re-run
  `tools/glyph_coverage.py` (or otherwise touch COVERAGE.md), regenerate it and commit the
  result in the same commit as the work that caused the change. It is a real deliverable, not a
  scratch artifact.

- **Docs/maintenance are always in scope.** Keeping `README.md` / `DESIGN.md` / `COVERAGE.md` and
  the code in sync is basic maintenance: if a change invalidates something the docs claim, fix the
  docs as part of that work. This is independent of how narrow or broad the request was — do not
  ask whether to update them, and do not treat "the request didn't mention docs" as a reason to
  leave them wrong.

- **"progress update and commit" — a named routine, and NOT the "always commit your work" rule.**
  These are two different things and must not be merged: *always commit your work* is the standing
  rule above about **when to commit**; *progress update and commit* is a specific procedure the
  user asks for **by that phrase**, and it is about **refreshing the coverage stats**. The routine
  itself is only the "progress update" part — the "and commit" is simply the standing rule above
  being applied afterwards, not a step of its own.

  When the user asks for a **progress update and commit**:
  1. Validate the authored data loads:
     `uv run --no-sync python -c "from strokespec.model import StrokeFont; print(len(StrokeFont.load('glyphs.strokes.json').codepoints()))"`.
  2. Regenerate the coverage report: `uv run --no-sync python tools/glyph_coverage.py`
     (rewrites `COVERAGE.md`).
  3. Copy the README **Coverage** summary from the regenerated `COVERAGE.md`: its Summary table
     states every number the README needs — codepoints authored / with strokes / intentionally
     empty, coverage of allocated, and the five block-bucket counts (filled, mostly-filled,
     slightly-filled, untouched, no allocated codepoints). **Read them off that table; do not
     recount and do not re-derive them from the per-block table.** The buckets are exact-count
     based and total: filled = every allocated codepoint covered, untouched = none covered,
     mostly-filled = at least half, slightly-filled = some but under half.
  4. The numbers are never suspicious. Do not second-guess them: do not flag a figure as
     anomalous, do not investigate it, and do not "correct" it. Report them as they come out and
     move on.

  Then commit that refreshed result per the standing "always commit your work" rule above — and
  that commit **includes `glyphs.strokes.json`**, the authored data the report was computed from
  (the routine is exactly the case where committing the live glyph file is wanted).
- Clean up test artifacts (`*.ttf`, `*.build.log`, `*.png`) before committing; they are
  gitignored.

- **Glyph order slots 1 and 2 are reserved.** `build_ufo` emits `.null` (glyph 1) and
  `nonmarkingreturn` (glyph 2) — zero advance, no outline — before any real glyph. Renderers and
  webfont converters still assume glyph 1 is `.null` (zero advance): without the reservation our
  lowest codepoint (U+0020 SPACE) lands in slot 1 and the ASCII space stops advancing in some
  tools even though its hmtx advance is correct (invisible trailing space, caret does not move).
  Unifont ships the same two slots. Do not remove them to save 16 bytes.

- Grid geometry: points are cell-centre aligned `(g+0.5)*SCALE`; `SCALE=16`, `UPEM=256`,
  `PEN_RADIUS=8`, `PEN_CAP="square"`. Grid lines are at `k*SCALE`; cell diagonals are
  `x±y = k*SCALE`. (The scale was lowered from 64/1024/32: TrueType stores coordinates as deltas
  with a 1-byte unsigned form, and at 16 units/cell almost no delta exceeds 255, so the 2-byte
  form disappears — 10.3% smaller with pixel-identical rendering and exactly proportional
  geometry.)

- `geometry.py` is now the single stroke→outline expansion used by the editor preview, the
  grid previews AND the compiled font (no picosvg). The editor canvas and `uiutil` both call
  it directly, so preview and compile can never disagree.

- Large-font compilation is slow: it runs on a background `QThread` with a `QProgressDialog`
  (determinate `done/total` while outlines build, indeterminate while the CLI compiler runs).
  `compile_strokefont(..., on_progress=...)` and `build_ufo(..., progress=...)` accept the
  callback.

## Codebase architecture intuition

The **stroke set is the single source of truth** (the authored `.strokes.json`). Everything —
editor, previews, compiled font — derives from the stroke model alone.

### Data flow (one direction)
```
StrokeFont (.strokes.json)         model.py     Stroke/Glyph/StrokeFont + JSON + Unicode defaults
    │
    ├─ geometry.py   strokes → outlines (M/L/Q/C/Z ops, font units, y-up, baseline 0)
    │                    the CANONICAL expansion — used by everyone
    ├─ ufo.py        contours → UFO authoring format (op_to_pen Q → qCurveTo)
    │                    build_ufo(..., progress=...)
    └─ compiler.py   UFO(in-memory) → TTF via ufo2ft   compile_strokefont(..., on_progress=...)
                         ufo2ft.compileTTF(removeOverlaps=False); gftools/fontmake CLI optional
```
The editor is a *front end* for the same model, not a parallel definition:
- `glyph_canvas.py` paints in real time from `geometry.stroke_outline`.
- `editor/uiutil.py` `glyph_qpainterpath` fills from `geometry.glyph_contours` (grid icons,
  `uffy preview`, reference sheets).
- `svgout.py` is a **debug-only** stroked-SVG emitter (no picosvg; the font/preview do not use
  it — don't let it drift or re-introduce it as the compile source).

### Intuitions / rules of thumb
- **Never park a widget on a grid index to draw per-cell decoration.** The codepoint grid draws
  its per-cell character badge in `GlyphGridDelegate.paint` — as a **cached bitmap**, never as a
  live `QFont` + `drawText`. `setIndexWidget` invalidates the item view's layout, and a wrapping
  `QListView` in IconMode is **not lazy** — invalidating it re-lays out every row, i.e. ~87,000
  Python model callbacks per scroll step on CJK Ext B's 42,720 rows (measured: 114 ms per scroll
  step, 80 ms per arrow-key step, scaling with the block size). Painting instead costs one
  `drawText` on the cells that were actually exposed: 39 ms scrolling, 16 ms navigating. Anything
  else that wants to appear on a cell gets painted there too (and a blocked badge update measured
  6 ms/step, so the layout — not the cell paint — is the cost).
- **A static per-cell decoration is a bitmap, not a per-frame font.** A `QFont` carrying a family
  *chain* makes the platform re-resolve that chain on every `drawText`, and for a codepoint no
  installed font covers it re-runs its **fallback search** — measured at 16–19 ms per cell, every
  frame, for the same codepoints (U+1D800, U+30000, U+3002F, U+1682F). Live badge draw: 0.094 ms
  mean, 19 ms worst. `grid.render_badge_pixmap()` + `GlyphGridModel.badge_pixmap()` rasterise it
  once per codepoint (2.77 ms) and then `drawPixmap` at 0.0012–0.0043 ms per cell. Same class of
  bug, still live: `glyph_canvas` calls its `reference_provider` on **every canvas repaint**, and
  the native ghost (`main_window.native_reference_bitmap`) builds a `QTextLayout` + a 160 px chain
  font + a pixmap + `drawText` inside that call.
- **A cache keyed by codepoint must not be cleared on a block switch.** `set_codepoints()` used to
  wipe the preview and badge caches, so every block change re-paid every badge render. Revisiting
  that block after three other blocks went from ~658 ms to 13.7 ms. Stroke-dependent caches are
  still dropped by `invalidate_previews()`; badges are not stroke-dependent, so they survive it.
- **Never re-derive the outline a second way.** Preview and compiled font must use the same
  `geometry.py` output; if a change only lands in one path, they drift. This is the whole point
  of having `geometry.py` be canonical.
- **Subcomponents transform strokes, not outlines.** A `Subcomponent` is a codepoint reference
  plus a destination box in **cell** coordinates (integer = cell corner; a `Point` index `g` is at
  cell `g + 0.5`), whose negative size means a flip. `geometry.flatten_cell_strokes` renders a
  glyph in its **own** cell space: its own strokes, plus each instance resolved by rendering the
  *source's own rendering* (already rounded on the source's lattice) and transporting that with
  `geometry.transport_cell_strokes`, which snaps every endpoint to the nearest cell centre.
  **One rounding per level, never a composed chain routed once** — an instance of B must show the
  strokes B shows, and composing `T_C∘T_B` and rounding at the end gives different results. Only
  after that does `cell_stroke_outline` expand the strokes, so the pen keeps its width and the
  butt snap happens in the host grid. A negative-determinant transform **swaps each stroke's two
  points** (`arc` bends come from point order) — do not "fix" that by transforming the control
  point. Anything that expands a glyph must be handed a `resolve` callable (`StrokeFont.get`) or
  the instances silently vanish (`expand()` in `ufo.py` deliberately omits it: it is only the
  glyph's OWN strokes), and any per-instance expansion (the compiler's) must go through
  `subcomponent_contours` so it stays identical to that glyph's own rendering.
- **`ufo.py` is the only place that decides simple vs composite.** A glyph with subcomponents
  becomes a ufoLib2 composite; each part (own strokes, then one per instance) is pooled in
  `.subNNNNN` helper glyphs keyed on **outline data alone** — never codepoint or transform — so
  identical converted outlines are stored once. **Every glyph is created through the local
  `new_glyph()` so it carries `public.truetype.overlap`**: our glyphs are overlapping per-stroke
  contours by design, and that key is what makes ufo2ft set `OVERLAP_SIMPLE` / `OVERLAP_COMPOUND`
  — without it a rasterizer may use its fast non-overlapping scheme and double-count coverage at
  stroke junctions. Check with `_meta_a.py <font.ttf>` (it prints the flag counts; both were 0
  before the fix). `make_ufo(..., subcomponents=)` switches the export policy between
  `SUBCOMPONENT_REUSE` (that) and `SUBCOMPONENT_FLATTEN` (inline each instance into its host
  glyph, no composites — `uffy build --subcomponents flatten`); flatten
  concatenates the SAME parts reuse would pool, never one merged blob, so both policies emit the
  same contour coordinates and render identically (verified over every instance-bearing glyph —
  check that way, not with a clipped render canvas). Glyphs without subcomponents keep the old
  whole-glyph merge and must stay byte-identical: verify with
  `glyph_contours(g, resolve=sf.get) == [stroke_outline(s) for s in g.strokes]` over the real set.
- **Cycles:** `StrokeFont.would_create_cycle` blocks one at creation (editor), and
  `break_subcomponent_cycles` (run by `from_dict`, i.e. every load) drops the instance that closes
  one and records it in `broken_subcomponent_cycles` (never persisted) for the editor's warning.
- **Ops shape:** `("M",pt)  ("L",pt)  ("Q",ctrl,pt)  ("C",c1,c2,pt)  ("Z",)`, y-up font units.
  Contour helpers in geometry (`_flatten`, `_reverse`, `_orient_ccw`, `_signed_area`) all
  understand M/L/Q/C/Z. Keep that set closed when adding ops.
- **Per-stroke, no boolean merge.** `glyph_contours` returns one contour per stroke; overlapping
  strokes stay as redundant subpaths and union visually under non-zero winding. Use
  `fontmake --keep-overlaps` — do NOT enable ufo2ft's RemoveOverlapsFilter (it only handles
  cubics and errors on the TrueType quadratics, and would undo the design).
- **One contour per stroke, oriented CCW** (positive area) so overlap unions instead of punching
  holes. Verify signed area > 0 after touching `stroke_outline`/`arc_outline`.
- **Arcs are a single quadratic per quarter** (`p1 -> C -> p2`, C = box bulge corner), giving
  axial, full-strength end tangents; each offset side is itself one quadratic. Axis-aligned /
  zero-length "arcs" are really lines (`arc_degenerates_to_line`). Don't reintroduce the old
  sampled quarter-ellipse or picosvg.
- **Line butts:** square butt extends each end by `r` (correct stroke length, baked — no SVG
  linecap). Non-axial butts snap onto the cell's own edge/diagonal in outline space; there's a
  committed series of requirements here (edge vs exactly-45°-only diagonal, then a
  user-controlled 45° vertical/horizontal split via p2 above/below p1). Read them in the code
  + comments before changing; some diagonal code is intentionally dummied out.
- **Where to add a new module:** pure model/geometry/compile logic in `strokespec/` (no Qt);
  any Qt/editor widget in `strokespec/editor/`. CLI entry points registered in `pyproject.toml`
  [project.scripts] (`uffy`, `uffy-editor`).
- **`cli.py`** exposes `build` / `preview` / `refs` / `validate` and is the fastest way to
  exercise the compile path without the GUI (`uv run uffy build … --tool fontmake --no-fix`).
- **Compile policy:** the binary is produced by Google's tooling. The default is
  **`ufo2ft.compileTTF` called in-process** on the in-memory `make_ufo()` font — the same engine
  the `fontmake` CLI drives, but with no UFO package written or re-read (that round-trip dominated
  build time: ~17.7s → ~7.4s for the current font). The `fontmake` / `gftools` CLIs remain
  selectable with `tool=`. Never hand-build TTF/OTF tables with fontTools' TTF/OTF read/write.

