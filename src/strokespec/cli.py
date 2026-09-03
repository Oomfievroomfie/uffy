"""``uffy`` command line: build a TTF, render previews, list reference fonts.

Entry point registered in ``pyproject.toml`` as the ``uffy`` console script.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional


def _build(args) -> int:
    import os

    from .model import StrokeFont
    from .compiler import compile_strokefont, available_compile_tools

    if args.input is None:
        print("error: an input stroke set is required", file=sys.stderr)
        return 2
    if not os.path.exists(args.input):
        print(f"error: no such file: {args.input}", file=sys.stderr)
        return 2
    tools = available_compile_tools()
    if not tools:
        print(
            "error: no Google font CLI compiler found (need `gftools` or `fontmake` on PATH)",
            file=sys.stderr,
        )
        return 2
    sf = StrokeFont.load(args.input)
    out = compile_strokefont(
        sf,
        args.output,
        family_name=args.family or sf.metadata.get("name", "strokespec"),
        style_name=args.style,
        cap=args.cap,
        tool=args.tool,
        run_fix=not args.no_fix,
    )
    print(f"wrote {out}")
    return 0


def _preview(args) -> int:
    import os

    from .model import StrokeFont

    if args.input is None:
        print("error: an input stroke set is required", file=sys.stderr)
        return 2
    sf = StrokeFont.load(args.input)
    glyph = sf.get(args.cp)
    if glyph is None:
        print(f"error: glyph U+{args.cp:04X} not in {args.input}", file=sys.stderr)
        return 2
    from .editor.uiutil import paint_stroke_glyph

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter

    size = args.size
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    paint_stroke_glyph(
        painter, glyph, QRectF(4, 4, size - 8, size - 8),
        color=QColor(20, 20, 20), cap=args.cap, baseline=sf.baseline,
    )
    painter.end()
    img.save(args.output)
    print(f"wrote {args.output}")
    return 0


def _refs(args) -> int:
    from .refbrowser import ReferenceLibrary
    from PIL import Image, ImageDraw, ImageFont

    lib = ReferenceLibrary()
    n = lib.add_folders([args.folder], recursive=not args.no_recursive)
    print(f"found {len(lib)} font(s) in {args.folder} ({n} newly added)")
    for f in lib:
        print(f"  - {f.family}  ({f.path.name})")

    sample = args.sample or "ABC"
    if args.output and len(lib) > 0:
        cell = 72
        font = None
        rows = []
        for f in lib:
            row = []
            for ch in sample:
                img = f.render_in_box(ord(ch), box_px=cell, pixel_size=128)
                row.append(img if img else Image.new("RGBA", (cell, cell), (240, 240, 240, 255)))
            rows.append(row)
        w = cell * len(sample) + 8
        h = cell * len(rows) + 8
        sheet = Image.new("RGBA", (w, h), (255, 255, 255, 255))
        for y, row in enumerate(rows):
            for x, img in enumerate(row):
                sheet.paste(img, (4 + x * cell, 4 + y * cell), img)
        sheet.convert("RGB").save(args.output)
        print(f"wrote reference sheet: {args.output}")
    return 0


def _validate(args) -> int:
    import shutil, subprocess, sys

    if not shutil.which("gftools") and not shutil.which("fontmake"):
        print("error: no gftools/fontmake on PATH", file=sys.stderr)
        return 2
    cmd = [args.tool, "validate", args.path]
    if args.tool == "fontmake":
        cmd = [args.tool, "validate"]  # fontmake has no validate; use gftools
    rc = subprocess.run(cmd).returncode
    return rc


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="uffy", description="strokespec stroke fallback font tools")
    sub = parser.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="compile a stroke set to a TTF with Google font tools")
    b.add_argument("input", help="path to a .strokes.json stroke set")
    b.add_argument("-o", "--output", default="fallback.ttf", help="output TTF path")
    b.add_argument("--family", default=None, help="font family name")
    b.add_argument("--style", default="Regular", help="font style name")
    b.add_argument("--cap", default="round", choices=["round", "butt"], help="pen cap style")
    b.add_argument("--tool", default="auto", choices=["auto", "gftools", "fontmake"], help="compile tool")
    b.add_argument("--no-fix", action="store_true", help="skip gftools fix")
    b.set_defaults(func=_build)

    pr = sub.add_parser("preview", help="render a single stroke glyph to a PNG")
    pr.add_argument("input", help="path to a .strokes.json stroke set")
    pr.add_argument("-c", "--cp", type=lambda s: int(s, 0), help="codepoint (int or 0x..)")
    pr.add_argument("-o", "--output", default="glyph_preview.png", help="output PNG path")
    pr.add_argument("--size", type=int, default=512, help="output pixel size")
    pr.add_argument("--cap", default="round", choices=["round", "butt"])
    pr.set_defaults(func=_preview)

    rf = sub.add_parser("refs", help="scan a folder of reference fonts")
    rf.add_argument("folder", help="folder to scan")
    rf.add_argument("-s", "--sample", default="ABC", help="sample string to render a reference sheet of")
    rf.add_argument("-o", "--output", default=None, help="write a reference preview sheet PNG")
    rf.add_argument("--no-recursive", action="store_true", help="do not scan subfolders")
    rf.set_defaults(func=_refs)

    # a v-style validate subcommand (best-effort)
    va = sub.add_parser("validate", help="validate a TTF with gftools")
    va.add_argument("path", help="TTF path")
    va.add_argument("--tool", default="gftools", choices=["gftools"])
    va.set_defaults(func=_validate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
