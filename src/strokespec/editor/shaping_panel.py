"""The reference dock's shaping tab: draws a shaped sample of the font being authored."""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
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


class ShapedStripView(QWidget):
    """Draws one shaped run: the authored glyphs at HarfBuzz's advances and offsets."""

    MARGIN = 8.0
    PAD_TOP = 15.0          # room for the cluster text above the em box
    PAD_BOTTOM = 16.0

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._source: Optional[StrokeFontSource] = None
        self._runs: List[ShapedRun] = []
        self._paths: List[List[QPainterPath]] = []
        self._scale = 1.0
        self._em = 1.0
        self._ascender = 0.0
        self._base = 0.0
        self._px = 32.0                       # em size in pixels
        self.setFixedWidth(1)
        self._apply_height()
        self.setStyleSheet(f"background:{BACKGROUND};")

    def _apply_height(self) -> None:
        self.setMinimumHeight(int(round(self._px + self.PAD_TOP + self.PAD_BOTTOM)))

    def pixel_size(self) -> float:
        return self._px

    def set_pixel_size(self, px: float) -> None:
        """Em size in pixels; re-lays out the runs at the new size."""
        self._px = float(px)
        self._apply_height()
        self.set_runs(self._source, self._runs)

    def set_runs(self, source: Optional[StrokeFontSource], runs: List[ShapedRun]) -> None:
        """Draw these runs in the given (visual) order; each run's glyphs are already visual."""
        self._source = source
        self._runs = list(runs)
        self._paths = []
        width = 2 * self.MARGIN
        if source is not None and self._runs:
            ascender, descender, _gap = source.font_extents()
            self._ascender = float(ascender)
            self._em = float(ascender - descender)
            # The size sets the scale; the strip is as wide as the text and the scroll area moves
            # it, rather than the text shrinking to fit the visible width.
            self._scale = self._px / self._em
            self._base = self.PAD_TOP + self._ascender * self._scale
            for run in self._runs:
                paths = []
                for g in run.glyphs:
                    glyph = source.glyph(g.gid)
                    paths.append(
                        glyph_qpainterpath(glyph, baseline=source.baseline,
                                           resolve=source.strokefont.get)
                        if glyph is not None else QPainterPath())
                self._paths.append(paths)
                width += run.advance * self._scale
        self.setFixedWidth(max(1, int(round(width))))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().paintEvent(event)
        source = self._source
        if source is None or not self._runs:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        small = QFont(p.font())
        small.setPixelSize(9)
        p.setFont(small)

        p.setPen(QPen(BASELINE, 1, Qt.PenStyle.DashLine))
        p.drawLine(0, int(self._base), self.width(), int(self._base))

        # Runs arrive in visual order and HarfBuzz returns each run's glyphs in visual order too
        # (RTL included), so the pen only moves right.
        pen = self.MARGIN
        top = self.PAD_TOP
        for run, paths in zip(self._runs, self._paths):
            for g, path in zip(run.glyphs, paths):
                w = g.x_advance * self._scale
                if g.x_advance > 0:
                    p.setPen(QPen(CELL_LINE, 1, Qt.PenStyle.DotLine))
                    p.drawRect(QRectF(pen, top, w, self._em * self._scale))
                    p.setPen(CELL_LINE)
                    p.drawLine(int(pen), int(self._base + 2), int(pen), int(self._base + 5))

                p.save()
                p.translate(pen, self._base)
                p.scale(self._scale, -self._scale)  # font units, y-up, as HarfBuzz reports them
                p.translate(g.x_offset, g.y_offset)  # HarfBuzz's offset, in font units
                p.setPen(Qt.PenStyle.NoPen)
                glyph = source.glyph(g.gid)
                if glyph is None:
                    p.setBrush(NOTDEF)             # .notdef: no authored glyph for the codepoint
                    p.drawRect(QRectF(2 * SCALE, -self._ascender,
                                      max(4 * SCALE, g.x_advance - 4 * SCALE), self._em * 0.6))
                else:
                    p.setBrush(INK)
                    p.drawPath(path)
                p.restore()

                p.setPen(LABEL)
                p.drawText(QRectF(pen - 12, top - 14, max(w, 24.0) + 24, 13),
                           Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, g.text)
                pen += w
        p.end()


class ShapingExample(QWidget):
    """Shapes a sample string with HarfBuzz against the loaded stroke set."""

    SAMPLE = "AV e\u0301 \u4e2d \u05d0\u05d1\u05d2"
    SIZE = 32                # default em size in pixels

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
        row.addWidget(QLabel("Size"))
        self._size = QSpinBox()
        self._size.setRange(8, 256)
        self._size.setSingleStep(4)
        self._size.setValue(self.SIZE)
        self._size.setSuffix(" px")
        self._size.setToolTip("Em size of the shaped sample, in pixels.")
        row.addWidget(self._size)
        lay.addLayout(row)

        # The strip carries the whole run and the scroll area moves it, so a long sample cannot
        # widen the dock.
        self._strip = ShapedStripView()
        self._strip.set_pixel_size(self.SIZE)
        self._scroll = QScrollArea()
        self._scroll.setWidget(self._strip)
        self._scroll.setWidgetResizable(False)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        # The viewport is the display area: same background as the strip, so the run sits on one
        # continuous surface instead of on a strip inside a frame.
        self._scroll.viewport().setAutoFillBackground(True)
        pal = self._scroll.viewport().palette()
        pal.setColor(self._scroll.viewport().backgroundRole(), QColor(BACKGROUND))
        self._scroll.viewport().setPalette(pal)
        self._scroll.horizontalScrollBar().rangeChanged.connect(self._fit_scroll_height)
        self._fit_scroll_height()
        lay.addWidget(self._scroll)

        self._sample.textChanged.connect(self._reshape)
        self._direction.currentIndexChanged.connect(self._reshape)
        self._size.valueChanged.connect(self._on_size)
        self._reshape()

    def _fit_scroll_height(self, *_args) -> None:
        """Minimum height is the strip plus a scrollbar while one is there; it fills the rest."""
        bar = self._scroll.horizontalScrollBar()
        extra = bar.sizeHint().height() if bar.maximum() > bar.minimum() else 0
        self._scroll.setMinimumHeight(self._strip.minimumHeight() + extra)

    def _on_size(self, px: int) -> None:
        self._strip.set_pixel_size(px)
        self._fit_scroll_height()

    # --- the loaded font -------------------------------------------------------
    def set_font(self, strokefont: Optional[StrokeFont]) -> None:
        """Point the tab at a stroke set, or at none."""
        self._strokefont = strokefont
        self._shaper = Shaper(StrokeFontSource(strokefont)) if strokefont is not None else None
        self._reshape()

    def refresh(self) -> None:
        """Re-read the stroke set after an edit."""
        self.set_font(self._strokefont)

    def _reshape(self) -> None:
        text = self._sample.text()
        if self._shaper is None:
            self._strip.set_runs(None, [])
            return
        # Auto = bidi runs (HarfBuzz resolves one direction per buffer); LTR/RTL override it and
        # shape the whole sample in encoding order.
        base = {0: None, 1: "ltr", 2: "rtl"}[self._direction.currentIndex()]
        try:
            runs = (self._shaper.shape_visual(text) if base is None
                    else [self._shaper.shape(text, direction=base)])
        except Exception:
            self._strip.set_runs(None, [])
            return
        self._strip.set_runs(self._shaper.source, runs)
