"""Entry point for the graphical editor (``uffy-editor``)."""

from __future__ import annotations

import sys
from typing import List, Optional


def apply_fallback_fonts(app) -> None:
    """Register the bundled rare-script fallback fonts so QFont can use them.

    The app default font is left alone (Segoe UI + Qt's own platform fallback chain). The
    native-text previews (big "native reference" panel + per-cell character badge) build a
    PER-BLOCK family list in fontfallback.native_text_families(), attaching only the fonts
    relevant to the codepoint's block, so the intentional fonts never run before the system
    chain and Unifont stays the last resort.
    """
    from PySide6.QtGui import QFontDatabase
    from pathlib import Path
    fonts_dir = Path(__file__).resolve().parent.parent / "data" / "fonts"
    fdb = QFontDatabase()
    for f in sorted(list(fonts_dir.glob("*.ttf")) + list(fonts_dir.glob("*.otf"))):
        fdb.addApplicationFont(str(f))


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
    apply_fallback_fonts(app)

    path = argv[0] if argv and not argv[0].startswith("-") else None
    window = MainWindow(path)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
