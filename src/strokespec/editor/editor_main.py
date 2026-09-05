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

    path = argv[0] if argv and not argv[0].startswith("-") else None
    window = MainWindow(path)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
