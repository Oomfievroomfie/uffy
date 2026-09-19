"""The reference dock's second mode: a shaping preview of the font being authored.

The shaping is ``strokespec.shaping`` — uharfbuzz driven by custom font funcs, with no font file
and no compilation: the stroke set answers HarfBuzz's font callbacks directly. This module is the
view. It draws the run at the positions HarfBuzz returned, using the editor's own stroke geometry
for the outlines (``glyph_qpainterpath``), so what is on screen is the authored font shaped.
"""

from __future__ import annotations

from typing import List, Optional

import uharfbuzz as hb

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..model import SCALE, StrokeFont
from ..shaping import ShapedRun, Shaper, StrokeFontSource
from .uiutil import glyph_qpainterpath

BACKGROUND = "#eceef2"
CELL_LINE = QColor(180, 185, 195)
INK = QColor(28, 30, 36)
NOTDEF = QColor(150, 60, 60)
BASELINE = QColor(150, 110, 110)
LABEL = QColor(70, 76, 86)
CLUSTER_LABEL = QColor(120, 128, 142)


class ShapedStripView(QWidget):
    """Draws one shaped run: the authored glyphs at HarfBuzz's advances and offsets."""

    MARGIN = 8.0

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._source: Optional[StrokeFontSource] = None
        self._run: Optional[ShapedRun] = None
        self._paths: List[QPainterPath] = []
        self._scale = 1.0
        self._base = 0.0
        self._ascender = 0.0
        self._em = 1.0
        self.setFixedHeight(104)
        self.setFixedWidth(1)
        self.setStyleSheet(f"background:{BACKGROUND};")

    def set_run(self, source: Optional[StrokeFontSource], run: Optional[ShapedRun]) -> None:
        self._source = source
        self._run = run
        # The outlines come from the editor's own stroke geometry (the same call the canvas and
        # the grid use) and are built once per run, not per repaint: HarfBuzz supplies positions.
        self._paths: List[QPainterPath] = []
        width = 2 * self.MARGIN
        if source is not None and run is not None:
            ascender, descender, _gap = source.font_extents()
            self._ascender = float(ascender)
            self._em = float(ascender - descender)
            # One fixed scale from the height: the run keeps its own width and the view scrolls,
            # instead of the glyphs shrinking to fit (which would resize no nothing but the run).
            self._scale = (self.height() - 2 * self.MARGIN) / self._em
            self._base = self.MARGIN + self._ascender * self._scale
            for g in run.glyphs:
                glyph = source.glyph(g.gid)
                self._paths.append(
                    glyph_qpainterpath(glyph, baseline=source.baseline,
                                       resolve=source.strokefont.get)
                    if glyph is not None else QPainterPath())
            width += run.advance * self._scale
        self.setFixedWidth(max(1, int(round(width))))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().paintEvent(event)
        run, source = self._run, self._source
        if run is None or source is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        small = QFont(p.font())
        small.setPixelSize(9)
        p.setFont(small)

        p.setPen(QPen(BASELINE, 1, Qt.PenStyle.DashLine))
        p.drawLine(0, int(self._base), self.width(), int(self._base))

        # HarfBuzz returns the run in visual order (leftmost glyph first, RTL included), so the
        # pen only ever moves right, by each glyph's own advance.
        pen = self.MARGIN
        top = self._base - self._ascender * self._scale
        for g, path in zip(run.glyphs, self._paths):
            w = g.x_advance * self._scale
            if g.x_advance > 0:
                p.setPen(QPen(CELL_LINE, 1, Qt.PenStyle.DotLine))
                p.drawRect(QRectF(pen, top, w, self._em * self._scale))
                p.setPen(CELL_LINE)
                p.drawLine(int(pen), int(self._base + 2), int(pen), int(self._base + 5))

            p.save()
            p.translate(pen, self._base)
            p.scale(self._scale, -self._scale)     # font units, y-up, as HarfBuzz reports them
            p.translate(g.x_offset, g.y_offset)    # HarfBuzz's offset, in font units
            p.setPen(Qt.PenStyle.NoPen)
            glyph = source.glyph(g.gid)
            if glyph is None:
                p.setBrush(NOTDEF)                 # .notdef: no authored glyph for the codepoint
                p.drawRect(QRectF(2 * SCALE, -self._ascender,
                                  max(4 * SCALE, g.x_advance - 4 * SCALE), self._em * 0.6))
            else:
                p.setBrush(INK)
                p.drawPath(path)
            p.restore()

            p.setPen(LABEL)
            p.drawText(QRectF(pen - 12, top - 15, max(w, 24.0) + 24, 14),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, g.text)
            p.setPen(CLUSTER_LABEL)
            p.drawText(QRectF(pen - 12, self._base + 6, max(w, 24.0) + 24, 13),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                       f"{g.gid} @{g.cluster}")
            pen += w
        p.end()


class ShapingExample(QWidget):
    """Shapes a sample string with HarfBuzz against the live stroke set, and shows the result."""

    SAMPLE = "AV e\u0301 \u4e2d \u05d0\u05d1\u05d2"

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._strokefont: Optional[StrokeFont] = None
        self._shaper: Optional[Shaper] = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)

        row = QHBoxLayout()
        row.addWidget(QLabel("Sample"))
        self._sample = QLineEdit(self.SAMPLE)
        self._sample.setToolTip("Text shaped with HarfBuzz against the font you are drawing.")
        row.addWidget(self._sample, 1)
        row.addWidget(QLabel("Direction"))
        self._direction = QComboBox()
        self._direction.addItems(["Auto", "LTR", "RTL"])
        row.addWidget(self._direction)
        lay.addLayout(row)

        # The strip is as wide as the run and is the ONLY thing that scrolls: without the scroll
        # area a long sample would widen the whole dock, and the dock is a sidebar.
        self._strip = ShapedStripView()
        self._scroll = QScrollArea()
        self._scroll.setWidget(self._strip)
        self._scroll.setWidgetResizable(False)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._scroll.setFixedHeight(
            self._strip.height() + self._scroll.horizontalScrollBar().sizeHint().height() + 4)
        lay.addWidget(self._scroll)

        # The status line wraps, so its minimum width is its longest word: an unwrapped label's
        # minimum is the whole string, which would pin the whole dock to that width forever.
        self._info = QLabel()
        self._info.setWordWrap(True)
        self._info.setStyleSheet("color:#5a6070;")
        lay.addWidget(self._info)

        self._sample.textChanged.connect(self._reshape)
        self._direction.currentIndexChanged.connect(self._reshape)
        self._reshape()

    # --- the live font ---------------------------------------------------------
    def set_font(self, strokefont: Optional[StrokeFont]) -> None:
        """Point the preview at a stroke set (or none). Rebuilds the mocked font funcs."""
        self._strokefont = strokefont
        self._shaper = Shaper(StrokeFontSource(strokefont)) if strokefont is not None else None
        self._reshape()

    def refresh(self) -> None:
        """Re-read the stroke set (an edit changed what the shape callbacks must answer)."""
        self.set_font(self._strokefont)

    def _reshape(self) -> None:
        text = self._sample.text()
        if self._shaper is None:
            self._strip.set_run(None, None)
            self._info.setText("no stroke set loaded")
            return
        direction = {0: None, 1: "ltr", 2: "rtl"}[self._direction.currentIndex()]
        try:
            run = self._shaper.shape(text, direction=direction)
        except Exception as exc:                      # never let a shaping error kill the dock
            self._strip.set_run(None, None)
            self._info.setText(f"shaping failed: {exc}")
            return
        self._strip.set_run(self._shaper.source, run)
        self._info.setText(
            f"uharfbuzz {hb.version_string()} · {run.direction} · {run.script or '—'} · "
            f"{run.language} · {len(run.glyphs)} glyphs · "
            f"advance {run.advance}u ({run.advance / SCALE:g} cells)"
        )
