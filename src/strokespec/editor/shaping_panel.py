"""The reference dock's second mode: a shaping preview of the font being authored.

The shaping is ``strokespec.shaping`` — uharfbuzz driven by custom font funcs, with no font file
and no compilation: the stroke set answers HarfBuzz's font callbacks directly. This module is the
view. It draws the run at the positions HarfBuzz returned, using the editor's own stroke geometry
for the outlines (``glyph_qpainterpath``), so what is on screen is the authored font shaped.
"""

from __future__ import annotations

from typing import List, Optional

import uharfbuzz as hb

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
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

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._source: Optional[StrokeFontSource] = None
        self._run: Optional[ShapedRun] = None
        self._paths: List[QPainterPath] = []
        self.setMinimumHeight(104)
        self.setStyleSheet(f"background:{BACKGROUND}; border:1px solid #b8bcc4;")

    def set_run(self, source: Optional[StrokeFontSource], run: Optional[ShapedRun]) -> None:
        self._source = source
        self._run = run
        # The outlines come from the editor's own stroke geometry (the same call the canvas and
        # the grid use) and are built once per run, not per repaint: HarfBuzz supplies positions.
        self._paths: List[QPainterPath] = []
        if source is not None and run is not None:
            for g in run.glyphs:
                glyph = source.glyph(g.gid)
                self._paths.append(
                    glyph_qpainterpath(glyph, baseline=source.baseline,
                                       resolve=source.strokefont.get)
                    if glyph is not None else QPainterPath())
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(280, 104)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().paintEvent(event)
        run, source = self._run, self._source
        if run is None or source is None:
            return
        ascender, descender, _gap = source.font_extents()
        em = float(ascender - descender)
        margin = 8.0
        # Fit the em box to the height AND the whole run to the width, so a long sample is never
        # silently clipped. Units are the font's own (256/em), so this is a plain scale.
        height_scale = (self.height() - 2 * margin) / em
        width_scale = (self.width() - 2 * margin) / max(1.0, float(run.advance))
        scale = max(0.02, min(height_scale, width_scale, 0.5))
        base = margin + ascender * scale + max(0.0, (self.height() - 2 * margin - em * scale) / 2)

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        small = QFont(p.font())
        small.setPixelSize(9)
        p.setFont(small)

        p.setPen(QPen(BASELINE, 1, Qt.PenStyle.DashLine))
        p.drawLine(0, int(base), self.width(), int(base))

        # HarfBuzz returns the run in visual order (leftmost glyph first, RTL included), so the
        # pen only ever moves right, by each glyph's own advance.
        pen = margin
        for g, path in zip(run.glyphs, self._paths):
            w = g.x_advance * scale
            top = base - ascender * scale
            if g.x_advance > 0:
                p.setPen(QPen(CELL_LINE, 1, Qt.PenStyle.DotLine))
                p.drawRect(QRectF(pen, top, w, em * scale))
                p.setPen(CELL_LINE)
                p.drawLine(int(pen), int(base + 2), int(pen), int(base + 5))

            p.save()
            p.translate(pen, base)
            p.scale(scale, -scale)                 # font units, y-up, as HarfBuzz reports them
            p.translate(g.x_offset, g.y_offset)    # HarfBuzz's offset, in font units
            p.setPen(Qt.PenStyle.NoPen)
            glyph = source.glyph(g.gid)
            if glyph is None:
                p.setBrush(NOTDEF)                 # .notdef: no authored glyph for the codepoint
                p.drawRect(QRectF(2 * SCALE, -ascender, max(4 * SCALE, w / scale - 4 * SCALE),
                                  em * 0.6))
            else:
                p.setBrush(INK)
                p.drawPath(path)
            p.restore()

            p.setPen(LABEL)
            p.drawText(QRectF(pen - 12, top - 15, max(w, 24.0) + 24, 14),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, g.text)
            p.setPen(CLUSTER_LABEL)
            p.drawText(QRectF(pen - 12, base + 6, max(w, 24.0) + 24, 13),
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

        caption = QLabel(
            "HarfBuzz shapes this text against the font you are drawing, through its font "
            "callback API: no font file is compiled and nothing is written to disk."
        )
        caption.setWordWrap(True)
        caption.setStyleSheet("color:#5a6070;")
        lay.addWidget(caption)

        self._strip = ShapedStripView()
        lay.addWidget(self._strip)

        self._info = QLabel()
        self._info.setStyleSheet("color:#5a6070;")
        lay.addWidget(self._info)

        self._dump = QPlainTextEdit()
        self._dump.setReadOnly(True)
        f = QFont("Consolas")
        f.setStyleHint(QFont.StyleHint.Monospace)
        f.setPixelSize(10)
        self._dump.setFont(f)
        self._dump.setFixedHeight(118)
        lay.addWidget(self._dump)

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
            self._dump.setPlainText("")
            return
        direction = {0: None, 1: "ltr", 2: "rtl"}[self._direction.currentIndex()]
        try:
            run = self._shaper.shape(text, direction=direction)
        except Exception as exc:                      # never let a shaping error kill the dock
            self._strip.set_run(None, None)
            self._info.setText(f"shaping failed: {exc}")
            self._dump.setPlainText(str(exc))
            return
        self._strip.set_run(self._shaper.source, run)
        self._info.setText(
            f"uharfbuzz {hb.version_string()} · font funcs, no font file · {run.direction} · "
            f"{run.script or '—'} · {run.language} · {len(run.glyphs)} glyphs · "
            f"advance {run.advance}u ({run.advance / SCALE:g} cells)"
        )
        self._dump.setPlainText(run.dump())
