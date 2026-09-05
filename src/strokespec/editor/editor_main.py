"""Entry point for the graphical editor (``uffy-editor``)."""

from __future__ import annotations

import sys
from typing import List, Optional


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    from PySide6.QtWidgets import QApplication

    from .main_window import MainWindow

    app = QApplication(sys.argv[:1])
    app.setApplicationName("strokespec")
    app.setOrganizationName("strokespec")
    # With ScrollPerPixel scroll views, a discrete wheel notch otherwise scrolls a whole
    # page (Qt caps at pageStep, cutting glyphs off). One wheel line = one rubber-band
    # line/cell, so a notch scrolls a controlled amount instead of a full page.
    app.setWheelScrollLines(1)

    # Qt's default glyph fallback chain (qwindowsfontdatabasebase.cpp) is a hardcoded,
    # CJK-centric try-font list that covers ~90% of scripts (the platform chain is kept).
    # It has NO entry for several scripts even though Windows ships first-party fonts for
    # them (Segoe UI Historic, Microsoft Yi Baiti, Ebrima, Gadugi, Nirmala UI). Prepend
    # those fonts, then the bundled Noto fallback fonts, to the app font's family list.
    # Setting families only ADDS to the fallback chain (loadEngine still appends the
    # platform try-fonts after them), never replaces it.
    from PySide6.QtGui import QFontDatabase
    from pathlib import Path
    default_font = app.font()
    fallback = [default_font.family()]
    for fam in ["Microsoft Yi Baiti", "Segoe UI Historic", "Ebrima", "Gadugi", "Nirmala UI"]:
        fallback.append(fam)
    # Bundled Noto + Unifont fonts: cover every remaining script no Windows font provides.
    # Tried after the Windows fonts but before the Unifont catch-all.
    fonts_dir = Path(__file__).resolve().parent.parent / "data" / "fonts"
    fdb = QFontDatabase()
    for f in sorted(list(fonts_dir.glob("*.ttf")) + list(fonts_dir.glob("*.otf"))):
        fid = fdb.addApplicationFont(str(f))
        if fid >= 0:
            for fam in fdb.applicationFontFamilies(fid):
                if fam not in fallback:
                    fallback.append(fam)
    # GNU Unifont (BMP) + Unifont Upper (supplementary planes): last-resort catch-all
    # for anything still missing.
    for fam in ("Unifont", "Unifont Upper"):
        if fam not in fallback:
            fallback.append(fam)
    default_font.setFamilies(fallback)
    app.setFont(default_font)

    path = argv[0] if argv and not argv[0].startswith("-") else None
    window = MainWindow(path)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
