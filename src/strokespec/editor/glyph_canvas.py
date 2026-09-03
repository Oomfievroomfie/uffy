"""The 16x16 stroke-authoring canvas: draw, adjust and inspect a single glyph.

Points are **cell-centre aligned**: grid index ``g`` (0..15) is the *centre* of cell ``g``,
so the outermost vertices sit half a cell inside the em box (the canvas shows the em box,
which gives that half-cell of padding). The baseline is a configurable line inside the em
box; descenders are drawn in the cells below it.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QTransform
from PySide6.QtWidgets import QWidget

from ..geometry import stroke_outline
from ..model import (
    DEFAULT_BASELINE,
    DEFAULT_CAP_HEIGHT,
    DEFAULT_X_HEIGHT,
    GRID_H,
    GRID_N,
    SCALE,
    SHAPE_ARC,
    SHAPE_LINE,
    UPEM,
    Glyph,
    Point,
    Stroke,
)
from .uiutil import ops_to_painterpath


class GlyphCanvas(QWidget):
    """Edits one glyph: click two grid points to add a stroke; drag an endpoint to move it.

    The tool mode (line / arc) and the arc bend are owned by the widget so the main window
    can drive them from its toolbar.
    """

    glyphChanged = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(320, 320)
        self._glyph: Glyph = Glyph(codepoint=0x20)
        self._pending: Point | None = None
        self._drag = None
        self._hover: Point | None = None
        self._selected_index: int = -1
        self.tool: str = SHAPE_LINE
        self.cap: str = "round"  # pen shape is a tool-level choice; not exposed
        self.baseline: float = DEFAULT_BASELINE
        self.x_height: float = DEFAULT_X_HEIGHT
        self.cap_height: float = DEFAULT_CAP_HEIGHT
        self.reference_provider = None
        self.show_reference = True

    # --- public API ----------------------------------------------------------
    def set_glyph(self, glyph: Glyph) -> None:
        self._glyph = glyph
        self._pending = None
        self._drag = None
        self._selected_index = -1
        self.update()

    def glyph(self) -> Glyph:
        return self._glyph

    def set_baseline(self, baseline: float) -> None:
        self.baseline = baseline
        self.update()

    def set_metrics(self, baseline: float, x_height: float, cap_height: float) -> None:
        self.baseline = float(baseline)
        self.x_height = float(x_height)
        self.cap_height = float(cap_height)
        self.update()

    def set_tool(self, tool: str) -> None:
        if tool in (SHAPE_LINE, SHAPE_ARC):
            self.tool = tool
            self.update()

    def select_stroke(self, index: int) -> None:
        self._selected_index = index
        self.update()

    def selected_stroke(self) -> Stroke | None:
        if 0 <= self._selected_index < len(self._glyph.strokes):
            return self._glyph.strokes[self._selected_index]
        return None

    def delete_selected(self) -> bool:
        if 0 <= self._selected_index < len(self._glyph.strokes):
            del self._glyph.strokes[self._selected_index]
            self._selected_index = -1
            self.update()
            self.glyphChanged.emit()
            return True
        return False

    def reverse_selected(self) -> None:
        """Reverse the selected stroke's points — this is what flips an arc's bend."""
        if 0 <= self._selected_index < len(self._glyph.strokes):
            s = self._glyph.strokes[self._selected_index]
            self._glyph.strokes[self._selected_index] = s.reversed()
            self.update()
            self.glyphChanged.emit()

    # --- geometry ------------------------------------------------------------
    def _grid_rect(self) -> QRectF:
        side = min(self.width(), self.height()) - 24.0
        side = max(24.0, side)
        x = (self.width() - side) / 2.0
        y = (self.height() - side) / 2.0
        return QRectF(x, y, side, side)

    def _cell(self) -> float:
        return self._grid_rect().width() / GRID_N

    def _grid_to_scene(self, gx: int, gy: int) -> QPointF:
        r = self._grid_rect()
        c = self._cell()
        return QPointF(r.left() + (gx + 0.5) * c, r.top() + (GRID_H - (gy + 0.5)) * c)

    def _scene_to_grid(self, pos: QPointF) -> Point:
        r = self._grid_rect()
        c = self._cell()
        gx = round((pos.x() - r.left()) / c - 0.5)
        gy = round(GRID_H - (pos.y() - r.top()) / c - 0.5)
        return Point(gx, gy)

    def _apply_grid_transform(self, p: QPainter, rect: QRectF) -> None:
        c = rect.width() / GRID_N
        # font (fx,fy) -> scene: sx = rect.left + fx*c/SCALE;
        #                        sy = rect.top + (16 - baseline - fy/SCALE)*c
        tf = QTransform(c / SCALE, 0.0, 0.0, -(c / SCALE),
                        rect.left(), rect.top() + (GRID_H - self.baseline) * c)
        p.setTransform(tf)

    # --- painting ------------------------------------------------------------
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), QColor(250, 250, 250))
        rect = self._grid_rect()
        c = self._cell()

        # cell boundary lines (0..16)
        p.setPen(QPen(QColor(206, 206, 214), 1))
        for i in range(GRID_N + 1):
            x = rect.left() + i * c
            p.drawLine(QPoint(x, rect.top()), QPoint(x, rect.bottom()))
            y = rect.top() + i * c
            p.drawLine(QPoint(rect.left(), y), QPoint(rect.right(), y))

        # guide lines: cap-height (top), x-height (middle), baseline (bottom)
        def grid2y(u: float) -> float:
            return rect.top() + (GRID_H - u) * c

        cap_y = grid2y(self.cap_height)
        xh_y = grid2y(self.x_height)
        base_y = grid2y(self.baseline)
        p.setPen(QPen(QColor(205, 120, 120), 1))
        p.drawLine(QPoint(rect.left(), cap_y), QPoint(rect.right(), cap_y))
        p.setPen(QPen(QColor(120, 190, 130), 1))
        p.drawLine(QPoint(rect.left(), xh_y), QPoint(rect.right(), xh_y))
        p.setPen(QPen(QColor(120, 150, 210), 1.5))
        p.drawLine(QPoint(rect.left(), base_y), QPoint(rect.right(), base_y))

        # faint reference glyph ghost, aligned to this font's baseline/cap-height guide
        if self.show_reference and self.reference_provider is not None:
            try:
                ghost = self.reference_provider(self._glyph.codepoint)
            except Exception:
                ghost = None
            if ghost is not None:
                try:
                    qpix, baseline_px, cap_px = ghost
                except (TypeError, ValueError):
                    qpix, baseline_px, cap_px = ghost, rect.height(), rect.height()
                if not qpix.isNull():
                    cell = self._cell()
                    grid_cap_span = (self.cap_height - self.baseline) * cell
                    scale = (grid_cap_span / cap_px) if (cap_px > 0 and grid_cap_span > 0) else 1.0
                    scene_base_y = rect.top() + (GRID_H - self.baseline) * cell
                    scaled = qpix.scaled(
                        max(1, int(qpix.width() * scale)),
                        max(1, int(qpix.height() * scale)),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    top = scene_base_y - baseline_px * (scaled.height() / qpix.height())
                    left = rect.center().x() - scaled.width() / 2.0
                    # an odd-cell-width reference glyph lands on a cell boundary when centred;
                    # nudge it by half a cell so it lines up with the cell-centre lattice
                    w_cells = scaled.width() / cell
                    if round(w_cells) % 2 == 1:
                        left -= 0.5 * cell
                    p.setOpacity(0.20)
                    p.drawPixmap(left, top, scaled)
                    p.setOpacity(1.0)

        # stroke fills (font units -> scene via the baseline transform)
        p.save()
        p.setClipRect(rect)
        self._apply_grid_transform(p, rect)
        for index, stroke in enumerate(self._glyph.strokes):
            color = QColor(30, 30, 30)
            if index == self._selected_index:
                color = QColor(0, 110, 220)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(color))
            p.drawPath(ops_to_painterpath(
                stroke_outline(stroke, cap=self.cap, baseline=self.baseline)))
        p.restore()

        # grid point dots at cell centres
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(155, 155, 168))
        dot = max(1.5, c * 0.07)
        for gx in range(GRID_N):
            for gy in range(GRID_N):
                q = self._grid_to_scene(gx, gy)
                p.drawEllipse(q, dot, dot)

        # pending first point + preview stroke
        if self._pending is not None:
            p.setBrush(QColor(0, 150, 90))
            p.setPen(QPen(QColor(0, 110, 60), 1.5))
            q = self._grid_to_scene(self._pending.x, self._pending.y)
            p.drawEllipse(q, c * 0.13, c * 0.13)
            if self._hover is not None and self._hover != self._pending:
                preview = Stroke(self._pending, self._hover, self.tool)
                p.save()
                p.setClipRect(rect)
                self._apply_grid_transform(p, rect)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(0, 150, 90, 130))
                p.drawPath(ops_to_painterpath(
                    stroke_outline(preview, cap=self.cap, baseline=self.baseline)))
                p.restore()

        # hover ring
        if self._hover is not None and self._pending is not None:
            p.setPen(QPen(QColor(0, 150, 90), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(self._grid_to_scene(self._hover.x, self._hover.y), c * 0.13, c * 0.13)

        # info line
        p.setPen(QColor(120, 120, 130))
        mode = "ARC" if self.tool == SHAPE_ARC else "LINE"
        p.drawText(
            QRectF(8, self.height() - 22, self.width() - 16, 18),
            Qt.AlignmentFlag.AlignLeft,
            f"{mode}   baseline={self.baseline:.1f}   x-height={self.x_height:.1f}   cap-height={self.cap_height:.1f}",
        )

    # --- interaction ---------------------------------------------------------
    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        if self._drag is not None:
            idx, ep = self._drag
            gp = self._scene_to_grid(pos)
            strokes = self._glyph.strokes
            if 0 <= idx < len(strokes):
                s = strokes[idx]
                if ep == 0:
                    strokes[idx] = Stroke(gp, s.p2, s.shape)
                else:
                    strokes[idx] = Stroke(s.p1, gp, s.shape)
                self.update()
                self.glyphChanged.emit()
        else:
            self._hover = self._scene_to_grid(pos)
            self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position()
            gp = self._scene_to_grid(pos)
            endpoint = self._find_endpoint(pos)
            if endpoint is not None:
                idx, ep = endpoint
                self._drag = (idx, ep)
                self._selected_index = idx
                self.update()
                return
            if self._pending is None:
                self._pending = gp
            else:
                if gp != self._pending:
                    try:
                        self._glyph.add_stroke(Stroke(self._pending, gp, self.tool))
                    except ValueError:
                        pass
                    self._pending = None
                    self._selected_index = len(self._glyph.strokes) - 1
                    self.update()
                    self.glyphChanged.emit()

    def mouseReleaseEvent(self, event) -> None:
        if self._drag is not None:
            self._drag = None
            self.update()

    def mouseDoubleClickEvent(self, event) -> None:
        self._pending = None
        self.update()

    def _find_endpoint(self, pos: QPointF):
        tol = self._cell() * 0.35
        best = None
        best_d = tol
        for i, s in enumerate(self._glyph.strokes):
            for ep, pt in enumerate((s.p1, s.p2)):
                q = self._grid_to_scene(pt.x, pt.y)
                d = (q.x() - pos.x()) ** 2 + (q.y() - pos.y()) ** 2
                if d <= best_d * best_d:
                    best_d = d ** 0.5
                    best = (i, ep)
        return best
