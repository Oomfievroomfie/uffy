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
    # those fonts to the app font's family list; setting families only ADDS to the fallback
    # chain (loadEngine still appends the platform try-fonts after them), never replaces it.
    default_font = app.font()
    default_font.setFamilies([default_font.family()] + [
        "Microsoft Yi Baiti",      # Yi Syllables, Yi Radicals
        "Segoe UI Historic",       # Linear B, Runic, Old Italic, Gothic, Cuneiform, Egyptian
                                   # Hieroglyphs, Phoenician, Glagolitic, Old Turkic, Brahmi, ...
        "Ebrima",                  # Osmanya, Tifinagh, Vai, NKo
        "Gadugi",                  # Canadian Aboriginal syllabics, Cherokee
        "Nirmala UI",              # Meetei Mayek (and Indic)
        # GNU Unifont is a per-user all-Unicode fallback covering the remaining scripts that
        # no Windows font provides (Tagalog, Avestan, Bamum, Miao/Pollard, Anatolian
        # Hieroglyphs, Tangut, ...). Last in the list so it only picks up what nothing else can.
        "Unifont",
    ])
    app.setFont(default_font)

    path = argv[0] if argv and not argv[0].startswith("-") else None
    window = MainWindow(path)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
