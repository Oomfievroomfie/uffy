"""Qt helpers shared across the editor: painting stroke outlines and converting images."""

from __future__ import annotations

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap

from ..geometry import Op, glyph_contours
from ..model import (
    GRID_H,
    GRID_N,
    DEFAULT_BASELINE,
    PEN_CAP,
    SCALE,
    UPEM,
    Glyph,
)


def ops_to_painterpath(ops: list) -> QPainterPath:
    """Convert a list of M/L/C/Z font-unit ops into a QPainterPath (y-up units)."""
    path = QPainterPath()
    for op in ops:
        kind = op[0]
        if kind == "M":
            path.moveTo(op[1][0], op[1][1])
        elif kind == "L":
            path.lineTo(op[1][0], op[1][1])
        elif kind == "C":
            path.cubicTo(op[1][0], op[1][1], op[2][0], op[2][1], op[3][0], op[3][1])
        elif kind == "Z":
            path.closeSubpath()
    return path


def glyph_qpainterpath(glyph: Glyph, cap: str = PEN_CAP, baseline: float = DEFAULT_BASELINE) -> QPainterPath:
    """Build one QPainterPath (in font units, baseline at y=0) for all strokes.

    The fill rule is set to non-zero winding (**not** even-odd) so overlapping strokes
    *union* rather than punch holes — TrueType/OpenType also fills with non-zero winding.
    """
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.WindingFill)
    for contour in glyph_contours(glyph, cap=cap, baseline=baseline):
        path.addPath(ops_to_painterpath(contour))
    return path


def _font_to_box(painter: QPainter, box: QRectF, width_units: float, ascent: float, descent: float) -> None:
    """Fit the design box (x:0..width_units, y:-descent..+ascent) into ``box``, centred."""
    scale = min(box.width() / width_units, box.height() / UPEM)
    ox, oy = box.center().x(), box.center().y()
    painter.translate(ox, oy)
    painter.scale(scale, -scale)
    painter.translate(-width_units / 2.0, -((ascent - descent) / 2.0))


def paint_stroke_glyph(
    painter: QPainter,
    glyph: Glyph,
    rect: QRectF,
    *,
    color: QColor = QColor(20, 20, 20),
    cap: str = PEN_CAP,
    baseline: float = DEFAULT_BASELINE,
    offset_y: float = 0.0,
) -> None:
    """Paint a glyph's stroke outline aspect-fitted into ``rect`` (baseline-aware)."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    path = glyph_qpainterpath(glyph, cap=cap, baseline=baseline)
    if offset_y:
        rect = rect.translated(0, offset_y)
    width_units = glyph.cell_width_units
    ascent = (GRID_H - baseline) * SCALE
    descent = baseline * SCALE
    _font_to_box(painter, rect, width_units, ascent, descent)
    painter.drawPath(path)
    painter.restore()


def pil_to_qimage(img: "Image.Image") -> QImage:
    """Convert a PIL RGBA image to a QImage (RGBA8888 byte order matches PIL)."""
    img = img.convert("RGBA")
    w, h = img.size
    raw = img.tobytes("raw", "RGBA")
    qimg = QImage(raw, w, h, 4 * w, QImage.Format.Format_RGBA8888)
    return qimg.copy()


def pil_to_qpixmap(img: "Image.Image") -> QPixmap:
    return QPixmap.fromImage(pil_to_qimage(img))


def grid_geometry(widget_size: QRectF, cols: int = GRID_N) -> QRectF:
    """Return the square grid rect (for the glyph canvas) fitted in ``widget_size``."""
    side = min(widget_size.width(), widget_size.height()) - 24.0
    side = max(24.0, side)
    x = widget_size.center().x() - side / 2.0
    y = widget_size.center().y() - side / 2.0
    return QRectF(x, y, side, side)

