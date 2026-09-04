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

from ..geometry import stroke_outline, glyph_contours
from ..model import (
    PEN_CAP,
    PEN_RADIUS,
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
        self._oob_count: int = 0
        self._clip: list = []
        self.tool: str = SHAPE_LINE
        self.cap: str = PEN_CAP  # pen shape is a tool-level choice; not exposed
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

    @staticmethod
    def _path_from(contours: list) -> "QPainterPath":
        """One non-zero-winding path from all contours (so inner counters punch holes)."""
        from PySide6.QtGui import QPainterPath
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.WindingFill)
        for contour in contours:
            path.addPath(ops_to_painterpath(contour))
        return path

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

    def toggle_selected_shape(self) -> None:
        """Switch the selected stroke between a straight line and a single-quadratic arc."""
        if 0 <= self._selected_index < len(self._glyph.strokes):
            s = self._glyph.strokes[self._selected_index]
            new_shape = SHAPE_LINE if s.shape == SHAPE_ARC else SHAPE_ARC
            self._glyph.strokes[self._selected_index] = Stroke(s.p1, s.p2, new_shape)
            self.update()
            self.glyphChanged.emit()

    def nudge(self, dx: int, dy: int) -> None:
        """Shift every point of every stroke by ``(dx, dy)`` grid cells (a 1-pixel nudge)."""
        strokes = self._glyph.strokes
        for i, s in enumerate(strokes):
            strokes[i] = Stroke(Point(s.p1.x + dx, s.p1.y + dy),
                                Point(s.p2.x + dx, s.p2.y + dy), s.shape)
        self.update()
        if strokes:
            self.glyphChanged.emit()

    def copy_strokes(self) -> None:
        """Copy the current glyph's strokes to an internal clipboard."""
        self._clip = [Stroke(s.p1, s.p2, s.shape) for s in self._glyph.strokes]

    def paste_strokes(self) -> None:
        """Append the clipboard strokes to the current glyph (up to the 32-stroke cap).

        Pasting *adds* rather than replaces; to replace, the user clears the glyph first.
        """
        if self._clip:
            room = 32 - len(self._glyph.strokes)
            if room > 0:
                self._glyph.strokes.extend(
                    [Stroke(s.p1, s.p2, s.shape) for s in self._clip[:room]])
                self.update()
                self.glyphChanged.emit()

    def flip_horizontal(self) -> None:
        """Mirror every stroke left-right across the glyph's cell centre.

        The point order is also swapped: the mirror flips the handedness of the geometry, and
        an arc's bulge comes from the point ordering, so without the swap arcs would re-bow in
        the *opposite* direction. (Swapping a line's endpoints is harmless.)
        """
        m = self._cols() - 1
        strokes = self._glyph.strokes
        for i, s in enumerate(strokes):
            p1 = Point(m - s.p1.x, s.p1.y)
            p2 = Point(m - s.p2.x, s.p2.y)
            strokes[i] = Stroke(p2, p1, s.shape)
        self.update()
        if strokes:
            self.glyphChanged.emit()

    # --- geometry ------------------------------------------------------------
    def _cols(self) -> int:
        """The glyph's cell width (8 for half-width, 16 for full-width)."""
        return self._glyph.cell_width_grid

    def _in_bounds(self, p) -> bool:
        """True if a point lies inside the glyph's designated cell AABB."""
        cols = self._cols()
        return 0 <= p.x < cols and 0 <= p.y < GRID_H

    def _grid_rect(self) -> QRectF:
        import math as _m
        cols = self._cols()
        rows = GRID_H
        margin = 20.0
        avail_w = max(1.0, self.width() - margin)
        avail_h = max(1.0, self.height() - margin)
        # upright square cells; the box takes the glyph's aspect ratio (cols:rows)
        scale = min(avail_w / cols, avail_h / rows)
        w, h = scale * cols, scale * rows
        return QRectF((self.width() - w) / 2.0, (self.height() - h) / 2.0, w, h)

    def _cell(self) -> float:
        r = self._grid_rect()
        return r.width() / self._cols()

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
        c = rect.width() / self._cols()
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

        # cell boundary lines (cols wide x GRID_H tall)
        cols = self._cols()
        p.setPen(QPen(QColor(206, 206, 214), 1))
        for i in range(cols + 1):
            x = rect.left() + i * c
            p.drawLine(QPoint(x, rect.top()), QPoint(x, rect.bottom()))
        for j in range(GRID_H + 1):
            y = rect.top() + j * c
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
                    qpix, baseline_px, _cap_px = ghost
                except (TypeError, ValueError):
                    qpix, baseline_px, _cap_px = ghost, rect.height(), rect.height()
                if not qpix.isNull():
                    cell = self._cell()
                    # Scale by the reference font's *full cell* (its em = ascender+descender),
                    # not its cap-height. Full-cell fonts (e.g. 16x16 bitmap fonts) have glyphs
                    # that fill the cell, and cap-based scaling shrinks them; ascender-based
                    # "to full cell" scaling shows the real proportions.
                    scale = rect.height() / qpix.height() if qpix.height() > 0 else 1.0
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
                        left += 0.5 * cell
                    p.setOpacity(0.20)
                    p.drawPixmap(left, top, scaled)
                    p.setOpacity(1.0)

        # stroke fills — real-time outline from our own fast geometry (pure Python),
        # drawn as ONE non-zero-winding path so inner counters punch their holes. We do NOT clip
        # to the grid rect: out-of-bounds strokes stay visible (in red).
        p.save()
        self._apply_grid_transform(p, rect)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(30, 30, 30)))
        p.drawPath(self._path_from(glyph_contours(self._glyph, cap=PEN_CAP, baseline=self.baseline)))
        oob_strokes = [s for s in self._glyph.strokes
                       if not (self._in_bounds(s.p1) and self._in_bounds(s.p2))]
        sel = self.selected_stroke()
        for sub_strokes, color in ((oob_strokes, QColor(214, 45, 45)),
                                   ([sel] if sel is not None else [], QColor(0, 110, 220))):
            if not sub_strokes:
                continue
            p.setBrush(QBrush(color))
            contours = [stroke_outline(st, cap=PEN_CAP, baseline=self.baseline) for st in sub_strokes]
            p.drawPath(self._path_from(contours))
        p.restore()

        # if any stroke is out of bounds, ring the working AABB in red so it is unmistakable
        self._oob_count = len(oob_strokes)
        if oob_strokes:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(214, 45, 45), 2, Qt.PenStyle.DashLine))
            p.drawRect(rect.adjusted(-3, -3, 3, 3))

        # grid point dots at cell centres
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(155, 155, 168))
        dot = max(1.5, c * 0.07)
        for gx in range(cols):
            for gy in range(GRID_H):
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
                self._apply_grid_transform(p, rect)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(0, 150, 90, 130))
                p.drawPath(self._path_from(
                    [stroke_outline(preview, cap=PEN_CAP, baseline=self.baseline)]))
                p.restore()

        # hover ring
        if self._hover is not None and self._pending is not None:
            p.setPen(QPen(QColor(0, 150, 90), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(self._grid_to_scene(self._hover.x, self._hover.y), c * 0.13, c * 0.13)

        # info line
        p.setPen(QColor(120, 120, 130))
        mode = "ARC" if self.tool == SHAPE_ARC else "LINE"
        text = (f"{mode}   baseline={self.baseline:.1f}   x-height={self.x_height:.1f}   "
                f"cap-height={self.cap_height:.1f}")
        if self._oob_count:
            text += f"    \u26a0 {self._oob_count} stroke(s) outside glyph area"
            p.setPen(QColor(214, 45, 45))
        p.drawText(
            QRectF(8, self.height() - 22, self.width() - 16, 18),
            Qt.AlignmentFlag.AlignLeft,
            text,
        )

    # --- interaction ---------------------------------------------------------
    # _drag is None, ("endpoint", idx, ep) to move an endpoint, or ("new",) to draw a stroke.
    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        if self._drag is not None:
            kind = self._drag[0]
            if kind == "endpoint":
                idx, ep = self._drag[1], self._drag[2]
                gp = self._scene_to_grid(pos)
                strokes = self._glyph.strokes
                if 0 <= idx < len(strokes):
                    s = strokes[idx]
                    strokes[idx] = Stroke(gp, s.p2, s.shape) if ep == 0 else Stroke(s.p1, gp, s.shape)
                    self.update()  # repaint the canvas only; the grid refresh is
                    # deferred to mouseRelease so it doesn't run on every drag step.
            else:  # "new": preview the stroke while dragging
                self._hover = self._scene_to_grid(pos)
                self.update()
        else:
            self._hover = self._scene_to_grid(pos)
            self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        gp = self._scene_to_grid(pos)
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)

        if not shift:
            endpoint = self._find_endpoint(pos)
            if endpoint is not None:
                idx, ep = endpoint
                self._drag = ("endpoint", idx, ep)
                self._selected_index = idx
                self.update()
                return

        # Blank space (or Shift): begin a new stroke. The pending start point is set or,
        # if both clicks are used, left intact so a second click completes the stroke.
        if shift:
            self._pending = gp  # Shift forces the stroke to start HERE, ignoring endpoints
        elif self._pending is None:
            self._pending = gp
        self._hover = gp  # separated strokes: each click starts fresh — never draw a line
        # back to a stale hover from the previous stroke (that was the phantom)
        self._drag = ("new",)
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._drag is None:
            return
        kind = self._drag[0]
        if kind == "endpoint":
            self._drag = None
            self.update()
            self.glyphChanged.emit()  # commit the drag once, on release
        else:  # "new"
            gp = self._scene_to_grid(event.position())
            if self._pending is not None and gp != self._pending:
                try:
                    self._glyph.add_stroke(Stroke(self._pending, gp, self.tool))
                except ValueError:
                    pass
                self._selected_index = len(self._glyph.strokes) - 1
                self._pending = None
                self.glyphChanged.emit()
            # else: a click with no drag keeps the pending point for two-click completion
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


