"""The 16x16 stroke-authoring canvas: draw, adjust and inspect a single glyph.

Points are **cell-centre aligned**: grid index ``g`` (0..15) is the *centre* of cell ``g``,
so the outermost vertices sit half a cell inside the em box (the canvas shows the em box,
which gives that half-cell of padding). The baseline is a configurable line inside the em
box; descenders are drawn in the cells below it.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF, QTransform
from PySide6.QtWidgets import QWidget

from ..geometry import (
    glyph_contours,
    own_cell_strokes,
    stroke_outline,
    subcomponent_contours,
    subcomponent_xform,
    transform_subcomponent,
)
from ..model import (
    PEN_CAP,
    PEN_RADIUS,
    DEFAULT_BASELINE,
    DEFAULT_CAP_HEIGHT,
    DEFAULT_X_HEIGHT,
    GRID_H,
    GRID_N,
    GRID_W,
    SCALE,
    SHAPE_ARC,
    SHAPE_LINE,
    UPEM,
    Glyph,
    Point,
    Stroke,
    Subcomponent,
    squish_strokes,
    squish_subcomponent,
)
from .uiutil import ops_to_painterpath


class GlyphCanvas(QWidget):
    """Edits one glyph: click two grid points to add a stroke; drag an endpoint to move it.

    The tool mode (line / arc) and the arc bend are owned by the widget so the main window
    can drive them from its toolbar.

    A glyph's subcomponents are drawn as their instanced outlines plus a dashed bounding box
    with a handle on each corner; they are driven by those handles (their instanced strokes are
    not editable here). The item order used for selection is the stroke list followed by the
    subcomponent list.
    """

    glyphChanged = Signal()
    clearRequested = Signal()
    selectionChanged = Signal()
    navRequested = Signal(str)  # 'up'/'down'/'left'/'right' in the codepoint list
    toggleWidthRequested = Signal()
    toggleCombiningRequested = Signal()
    subcomponentMenuRequested = Signal(int)  # index into glyph.subcomponents
    subcomponentCycleBlocked = Signal(int)   # n pasted instances refused (would close a cycle)

    # half-size (px) of a subcomponent's corner handles, and their click tolerance
    HANDLE_PX = 5.0
    HANDLE_TOL = 7.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(320, 320)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._glyph: Glyph = Glyph(codepoint=0x20)
        self._pending: Point | None = None
        self._drag = None
        self._hover: Point | None = None
        self._selected_index: int = -1
        self._oob_count: int = 0
        # currently-held arrow keys (for paste-into-side/corner squish via Ctrl+arrow+V)
        self._held_arrows: set = set()
        self._clip: list = []
        self._clip_subs: list = []   # subcomponents travel with the strokes (Ctrl+C / Ctrl+V)
        self._clip_src: int = 0  # codepoint the clipboard was copied from (provenance)
        # callbacks owned by the main window: is the related panel in 'copy as subcomponent'
        # mode, and if so, instance that glyph instead of pasting its contents
        self.subcomponent_mode_fn = None
        self.paste_subcomponent_fn = None
        self.subcomponent_cycle_fn = None   # callable(owner_cp, target_cp) -> bool
        self._undo: list = []   # list of prior item-list snapshots (strokes, subcomponents)
        self._redo: list = []   # list of undone item-list snapshots
        self.tool: str = SHAPE_LINE
        self.cap: str = PEN_CAP  # pen shape is a tool-level choice; not exposed
        self.baseline: float = DEFAULT_BASELINE
        self.x_height: float = DEFAULT_X_HEIGHT
        self.cap_height: float = DEFAULT_CAP_HEIGHT
        self.reference_provider = None
        self.glyph_provider = None   # callable(codepoint) -> Glyph, to instance subcomponents
        self.show_reference = True
        self.wireframe: bool = False

    # --- public API ----------------------------------------------------------
    def set_wireframe(self, on: bool) -> None:
        self.wireframe = bool(on)
        self.update()

    def set_glyph(self, glyph: Glyph) -> None:
        self._glyph = glyph
        self._pending = None
        self._drag = None
        self._selected_index = -1
        self._undo = []
        self._redo = []
        self.update()

    # --- item indexing (strokes first, then subcomponents) --------------------
    def stroke_count(self) -> int:
        return len(self._glyph.strokes)

    def subcomponent_count(self) -> int:
        return len(self._glyph.subcomponents)

    def selected_subcomponent_index(self) -> int:
        """Index into ``glyph.subcomponents`` of the selected instance, or -1."""
        i = self._selected_index - self.stroke_count()
        if 0 <= i < self.subcomponent_count():
            return i
        return -1

    # --- undo / redo (current glyph only) ------------------------------------
    @staticmethod
    def _copy_strokes(strokes) -> list:
        return [Stroke(s.p1, s.p2, s.shape, s.origin) for s in strokes]

    @staticmethod
    def _copy_subcomponents(subs) -> list:
        return [Subcomponent(s.codepoint, s.start, s.end, s.origin) for s in subs]

    def _items_snapshot(self) -> tuple:
        return (self._copy_strokes(self._glyph.strokes),
                self._copy_subcomponents(self._glyph.subcomponents))

    def _restore_items(self, snap: tuple) -> None:
        strokes, subs = snap
        self._glyph.strokes[:] = strokes
        self._glyph.subcomponents[:] = subs

    def _snapshot(self) -> None:
        """Record the current items before a mutation, so it can be undone."""
        self._undo.append(self._items_snapshot())
        self._redo.clear()

    def undo(self) -> None:
        if not self._undo:
            return
        self._redo.append(self._items_snapshot())
        self._restore_items(self._undo.pop())
        self._clamp_selection()
        self.update()
        self.glyphChanged.emit()

    def redo(self) -> None:
        if not self._redo:
            return
        self._undo.append(self._items_snapshot())
        self._restore_items(self._redo.pop())
        self._clamp_selection()
        self.update()
        self.glyphChanged.emit()

    def _clamp_selection(self) -> None:
        if self._selected_index >= self.stroke_count() + self.subcomponent_count():
            self._selected_index = -1

    def event(self, ev) -> bool:
        # Qt handles Tab/Backtab for focus traversal at this level and sends Shift+Tab as
        # Key_Backtab, so intercept them here BEFORE the default focus navigation. Otherwise
        # the keypress never reaches keyPressEvent and the input is "eaten".
        if ev.type() == QEvent.Type.KeyPress:
            k = ev.key()
            if k == Qt.Key.Key_Tab:
                self.navRequested.emit("right")
                return True
            if k == Qt.Key.Key_Backtab:
                self.navRequested.emit("left")
                return True
        return super().event(ev)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        mod = event.modifiers()
        ctrl = bool(mod & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mod & Qt.KeyboardModifier.ShiftModifier)
        # remember which arrow keys are held, so Ctrl+V can squish into a side/corner
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right):
            self._held_arrows.add(key)
        if ctrl and key == Qt.Key.Key_Z:
            self.redo() if shift else self.undo()
            return
        if ctrl and key == Qt.Key.Key_C:
            self.copy_strokes()
            return
        if ctrl and key == Qt.Key.Key_V:
            self._paste_with_squish(shift)
            return
        if ctrl and key == Qt.Key.Key_H:
            self.clearRequested.emit()
            return
        if ctrl:
            super().keyPressEvent(event)
            return
        if key == Qt.Key.Key_Left:
            self.navRequested.emit("left")
        elif key == Qt.Key.Key_Right:
            self.navRequested.emit("right")
        elif key == Qt.Key.Key_Up:
            self.navRequested.emit("up")
        elif key == Qt.Key.Key_Down:
            self.navRequested.emit("down")
        elif key == Qt.Key.Key_W:
            self.nudge(0, 1)
        elif key == Qt.Key.Key_S:
            self.nudge(0, -1)
        elif key == Qt.Key.Key_A:
            self.nudge(-1, 0)
        elif key == Qt.Key.Key_D:
            self.nudge(1, 0)
        elif key == Qt.Key.Key_PageUp:
            self.nudge(0, 1)
        elif key == Qt.Key.Key_PageDown:
            self.nudge(0, -1)
        elif key == Qt.Key.Key_F:
            self.flip_horizontal()
        elif key == Qt.Key.Key_B:
            self.toggle_selected_shape()
        elif key == Qt.Key.Key_R:
            self.reverse_selected()
        elif key == Qt.Key.Key_Delete:
            self.delete_selected()
        elif key == Qt.Key.Key_N:
            self.toggleWidthRequested.emit()
        elif key == Qt.Key.Key_M:
            self.toggleCombiningRequested.emit()
        else:
            super().keyPressEvent(event)

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

    def selected_subcomponent(self) -> Subcomponent | None:
        i = self.selected_subcomponent_index()
        return self._glyph.subcomponents[i] if i >= 0 else None

    def delete_selected(self) -> bool:
        i = self._selected_index
        if 0 <= i < self.stroke_count():
            self._snapshot()
            del self._glyph.strokes[i]
        elif 0 <= self.selected_subcomponent_index() < self.subcomponent_count():
            self._snapshot()
            del self._glyph.subcomponents[self.selected_subcomponent_index()]
        else:
            return False
        self._selected_index = -1
        self.update()
        self.glyphChanged.emit()
        return True

    def reverse_selected(self) -> None:
        """Reverse the selected stroke's points — this is what flips an arc's bend."""
        if 0 <= self._selected_index < len(self._glyph.strokes):
            self._snapshot()
            s = self._glyph.strokes[self._selected_index]
            self._glyph.strokes[self._selected_index] = s.reversed()
            self.update()
            self.glyphChanged.emit()

    def toggle_selected_shape(self) -> None:
        """Switch the selected stroke between a straight line and a single-quadratic arc."""
        if 0 <= self._selected_index < len(self._glyph.strokes):
            self._snapshot()
            s = self._glyph.strokes[self._selected_index]
            new_shape = SHAPE_LINE if s.shape == SHAPE_ARC else SHAPE_ARC
            self._glyph.strokes[self._selected_index] = Stroke(s.p1, s.p2, new_shape, s.origin)
            self.update()
            self.glyphChanged.emit()

    def nudge(self, dx: int, dy: int) -> None:
        """Shift every point of every stroke, and every instance's box, by ``(dx, dy)`` cells."""
        strokes = self._glyph.strokes
        subs = self._glyph.subcomponents
        if not strokes and not subs:
            return
        self._snapshot()
        for i, s in enumerate(strokes):
            strokes[i] = Stroke(Point(s.p1.x + dx, s.p1.y + dy),
                                Point(s.p2.x + dx, s.p2.y + dy), s.shape, s.origin)
        for i, sub in enumerate(subs):
            subs[i] = sub.with_box((sub.start[0] + dx, sub.start[1] + dy),
                                   (sub.end[0] + dx, sub.end[1] + dy))
        self.update()
        self.glyphChanged.emit()

    def copy_strokes(self) -> None:
        """Copy the current glyph's contents — strokes *and* subcomponents — to the clipboard.

        The source codepoint is remembered too, so a paste can record where the items came from
        (provenance) and so the 'copy as subcomponent' mode can instance that glyph.
        """
        self._clip = [Stroke(s.p1, s.p2, s.shape, s.origin) for s in self._glyph.strokes]
        self._clip_subs = self._copy_subcomponents(self._glyph.subcomponents)
        self._clip_src = self._glyph.codepoint

    def _paste_as_subcomponent(self, direction: str = "", fraction: float = 0.5) -> bool:
        """Instance the copied glyph instead of pasting its contents (the panel's toggle).

        Returns False when no handler is connected, so the plain paste happens instead.
        """
        if self.paste_subcomponent_fn is None:
            return False
        self.paste_subcomponent_fn(self._clip_src, direction, fraction)
        return True

    def _subcomponent_mode(self) -> bool:
        return bool(self.subcomponent_mode_fn is not None and self.subcomponent_mode_fn())

    def _legal_pasted_subcomponents(self, subs: list) -> list:
        """Drop pasted instances that would close a subcomponent cycle, warning if any were.

        Pasting a glyph's contents into the glyph it instances would otherwise create a
        self-reference (or an indirect loop); cycles must stay impossible.
        """
        if self.subcomponent_cycle_fn is None:
            return subs
        target = self._glyph.codepoint
        kept, dropped = [], 0
        for s in subs:
            if self.subcomponent_cycle_fn(target, s.codepoint):
                dropped += 1
            else:
                kept.append(s)
        if dropped:
            self.subcomponentCycleBlocked.emit(dropped)
        return kept

    def paste_strokes(self) -> None:
        """Append the clipboard's contents to the current glyph.

        Pasting *adds* rather than replaces; to replace, the user clears the glyph first. With the
        related panel's 'Copy as subcomponent' mode on, a paste instead **instances** the glyph the
        contents were copied from (the cycle check lives with the handler).
        """
        if not self._clip and not self._clip_subs:
            return
        if self._subcomponent_mode() and self._paste_as_subcomponent():
            return
        self._snapshot()
        self._glyph.strokes.extend(self._stamp_paste(self._clip, self._clip))
        subs = self._legal_pasted_subcomponents(self._clip_subs)
        self._glyph.subcomponents.extend(
            self._stamp_subcomponent_paste(subs, subs))
        self.update()
        self.glyphChanged.emit()

    def _stamp_subcomponent_paste(self, src: list, transformed: list,
                                  direction: str = None, fraction: float = None) -> list:
        """Give pasted instances their provenance (same rule as strokes, same helper)."""
        from ..model import copied_origin
        target_cp = self._glyph.codepoint
        return [t.with_origin(copied_origin(self._clip_src, target_cp, s.origin, direction, fraction))
                for t, s in zip(transformed, src)]

    def _stamp_paste(self, src: list, transformed: list,
                     direction: str = None, fraction: float = None) -> list:
        """Give pasted strokes their provenance, per stroke (see ``model.copied_origin``).

        Stamps the clipboard's source codepoint only when it is allocated and differs from the
        glyph being pasted into; otherwise each stroke carries its own existing provenance data
        through unchanged (pasting into the same codepoint, or pasting out of an unallocated
        scratch glyph, keeps the copied data rather than overwriting or deleting it).
        """
        from ..model import copied_origin
        target_cp = self._glyph.codepoint
        return [t.with_origin(copied_origin(self._clip_src, target_cp, s.origin, direction, fraction))
                for t, s in zip(transformed, src)]

    def _paste_with_squish(self, shift: bool) -> None:
        """Ctrl+V. If arrow keys are held, squish the paste toward that side/corner
        (Shift -> 2/3 size, otherwise 1/2); otherwise it's a plain append paste.

        Strokes and instances are squished by the same map, and in 'copy as subcomponent' mode the
        instance's bounding box lands in that same fraction of the grid.
        """
        if not self._clip and not self._clip_subs:
            return
        direction = self._squish_direction(self._held_arrows)
        fraction = 2.0 / 3.0 if shift else 0.5
        if self._subcomponent_mode():
            if self._paste_as_subcomponent(direction, fraction):
                return
            self.paste_strokes()
            return
        if not direction:
            self.paste_strokes()
            return
        self._snapshot()
        src = self._clip
        squished = squish_strokes(src, direction, self._cols(), fraction)
        self._glyph.strokes.extend(self._stamp_paste(src, squished, direction, fraction))
        sub_src = self._legal_pasted_subcomponents(self._clip_subs)
        sub_squished = [squish_subcomponent(s, direction, self._cols(), fraction)
                        for s in sub_src]
        self._glyph.subcomponents.extend(
            self._stamp_subcomponent_paste(sub_src, sub_squished, direction, fraction))
        self.update()
        self.glyphChanged.emit()

    def _squish_direction(self, held: set) -> str:
        """'up'/'down'/'left'/'right' (one arrow), a corner like 'upleft' (two arrows), or ''."""
        v = "up" if Qt.Key.Key_Up in held else ("down" if Qt.Key.Key_Down in held else "")
        h = "left" if Qt.Key.Key_Left in held else ("right" if Qt.Key.Key_Right in held else "")
        if v and h:
            return v + h
        return v or h

    def keyReleaseEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right):
            self._held_arrows.discard(key)
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event) -> None:
        self._held_arrows.clear()
        super().focusOutEvent(event)

    def append_strokes(self, strokes) -> None:
        """Append a list of strokes to the current glyph."""
        if not strokes:
            return
        self._snapshot()
        self._glyph.strokes.extend(
            [Stroke(s.p1, s.p2, s.shape, s.origin) for s in strokes])
        self.update()
        self.glyphChanged.emit()

    def add_subcomponent(self, sub: Subcomponent) -> None:
        """Append a subcomponent instance to the current glyph (one undo step)."""
        self._snapshot()
        self._glyph.add_subcomponent(sub)
        self._select_item(self.stroke_count() + self.subcomponent_count() - 1)
        self.glyphChanged.emit()

    def pull_subcomponent(self, index: int) -> bool:
        """Turn instance ``index`` into its contents, killing the instance.

        The referenced glyph's **actual contents** are inlined, appropriately transformed:

        * its own strokes come in as strokes — the box transform applied to their endpoints, on
          the cell lattice, with the points swapped where the box mirrors so an arc keeps bending
          the right way — and
        * its own **subcomponents come in as subcomponents**, their boxes mapped by the same
          transform, so nested instances stay instances instead of collapsing into one flat
          stroke list.

        Because the source glyph has no path back here (that would be a cycle), inlining its
        references can never create one.
        """
        if not (0 <= index < self.subcomponent_count()):
            return False
        sub = self._glyph.subcomponents[index]
        src = self.glyph_provider(sub.codepoint) if self.glyph_provider is not None else None
        from ..model import copied_origin
        host_cp = self._glyph.codepoint
        self._snapshot()
        del self._glyph.subcomponents[index]
        if src is not None:
            x = subcomponent_xform(sub, src)
            for s, (p1, p2, shape) in zip(src.strokes, own_cell_strokes(src, x)):
                self._glyph.add_stroke(Stroke(
                    Point(round(p1[0] - 0.5), round(p1[1] - 0.5)),
                    Point(round(p2[0] - 0.5), round(p2[1] - 0.5)),
                    shape,
                    copied_origin(sub.codepoint, host_cp, s.origin),
                ))
            for child in src.subcomponents:
                self._glyph.add_subcomponent(transform_subcomponent(child, x))
        self._selected_index = -1
        self.update()
        self.glyphChanged.emit()
        return True

    def flip_horizontal(self) -> None:
        """Mirror every stroke, and every instance's box, left-right across the glyph's centre.

        Only an arc's point order is swapped: the mirror flips the handedness of the geometry,
        and an arc's bulge comes from the point ordering, so without the swap arcs would re-bow
        in the *opposite* direction. A straight line has no bend, so its points are mirrored but
        NOT reversed. An instance's box is mirrored in cell coordinates (``x -> cols - x``),
        which is the same physical mirror; a negatively sized box stays negatively sized.
        """
        cols = self._cols()
        m = cols - 1
        strokes = self._glyph.strokes
        subs = self._glyph.subcomponents
        if not strokes and not subs:
            return
        self._snapshot()
        for i, s in enumerate(strokes):
            p1 = Point(m - s.p1.x, s.p1.y)
            p2 = Point(m - s.p2.x, s.p2.y)
            strokes[i] = Stroke(p2, p1, s.shape, s.origin) if s.shape == SHAPE_ARC else Stroke(p1, p2, s.shape, s.origin)
        for i, sub in enumerate(subs):
            subs[i] = sub.with_box((cols - sub.start[0], sub.start[1]),
                                   (cols - sub.end[0], sub.end[1]))
        self.update()
        self.glyphChanged.emit()

    def flip_vertical(self) -> None:
        """Mirror every stroke, and every instance's box, top-bottom across the glyph's centre.

        Same handedness caveat as :meth:`flip_horizontal`: only an arc's points are swapped so
        it keeps bending the right way; a straight line's points are mirrored but not reversed.
        """
        m = GRID_H - 1
        strokes = self._glyph.strokes
        subs = self._glyph.subcomponents
        if not strokes and not subs:
            return
        self._snapshot()
        for i, s in enumerate(strokes):
            p1 = Point(s.p1.x, m - s.p1.y)
            p2 = Point(s.p2.x, m - s.p2.y)
            strokes[i] = Stroke(p2, p1, s.shape, s.origin) if s.shape == SHAPE_ARC else Stroke(p1, p2, s.shape, s.origin)
        for i, sub in enumerate(subs):
            subs[i] = sub.with_box((sub.start[0], GRID_H - sub.start[1]),
                                   (sub.end[0], GRID_H - sub.end[1]))
        self.update()
        self.glyphChanged.emit()

    def rotate_90_cw(self) -> None:
        """Rotate every stroke, and every instance's box, 90 degrees clockwise, in 16x16 space.

        The rotation is carried out on the 16x16 lattice even when the glyph is 8x16 (a narrow
        glyph becomes wide and lands somewhere in the 16x16 box; the user nudges it into place
        afterwards). Rotation is orientation-preserving, so the point order is NOT swapped. In
        cell coordinates the same rotation is ``(x, y) -> (y, 16 - x)``.
        """
        n = 16
        strokes = self._glyph.strokes
        subs = self._glyph.subcomponents
        if not strokes and not subs:
            return
        self._snapshot()
        for i, s in enumerate(strokes):
            p1 = Point(s.p1.y, n - 1 - s.p1.x)
            p2 = Point(s.p2.y, n - 1 - s.p2.x)
            strokes[i] = Stroke(p1, p2, s.shape, s.origin)
        for i, sub in enumerate(subs):
            subs[i] = sub.with_box((sub.start[1], n - sub.start[0]),
                                   (sub.end[1], n - sub.end[0]))
        self.update()
        self.glyphChanged.emit()

    # --- subcomponent boxes --------------------------------------------------
    SUB_PEN = QColor(150, 60, 190)          # not selected
    SUB_PEN_SEL = QColor(0, 110, 220)       # selected

    @staticmethod
    def subcomponent_corners(sub: Subcomponent) -> list:
        """The instance's four box corners (cell coords), starting at its ``start`` corner."""
        x0, y0, x1, y1 = sub.box
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]

    def _subcomponent_scene_corners(self, sub: Subcomponent) -> list:
        return [self._cell_to_scene(x, y) for x, y in self.subcomponent_corners(sub)]

    def _paint_subcomponent_box(self, p: QPainter, sub: Subcomponent, selected: bool) -> None:
        """A dashed bounding polygon plus a square handle on each corner (scene coords)."""
        corners = self._subcomponent_scene_corners(sub)
        color = self.SUB_PEN_SEL if selected else self.SUB_PEN
        p.save()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(color, 2.0 if selected else 1.5, Qt.PenStyle.DashLine))
        p.drawPolygon(QPolygonF(corners))
        h = self.HANDLE_PX
        p.setPen(QPen(color, 1.0))
        p.setBrush(QBrush(QColor(255, 255, 255)))
        for c in corners:
            p.drawRect(QRectF(c.x() - h, c.y() - h, 2 * h, 2 * h))
        p.restore()

    def _subcomponent_handle_at(self, pos: QPointF):
        """``(subcomponent index, corner index)`` whose handle is under ``pos``, else ``None``."""
        for i in range(len(self._glyph.subcomponents) - 1, -1, -1):
            for k, c in enumerate(self._subcomponent_scene_corners(self._glyph.subcomponents[i])):
                if abs(c.x() - pos.x()) <= self.HANDLE_TOL and abs(c.y() - pos.y()) <= self.HANDLE_TOL:
                    return (i, k)
        return None

    def _subcomponent_at(self, pos: QPointF):
        """Index of the instance whose box (interior or handle) is under ``pos``, else ``None``."""
        handle = self._subcomponent_handle_at(pos)
        if handle is not None:
            return handle[0]
        for i in range(len(self._glyph.subcomponents) - 1, -1, -1):
            sc = self._subcomponent_scene_corners(self._glyph.subcomponents[i])
            xs = [c.x() for c in sc]
            ys = [c.y() for c in sc]
            if min(xs) <= pos.x() <= max(xs) and min(ys) <= pos.y() <= max(ys):
                return i
        return None

    def _move_subcomponent_corner(self, i: int, k: int, pos: QPointF) -> None:
        """Drag corner ``k`` of instance ``i`` to ``pos`` (rounded to the grid, clamped).

        A corner may be dragged past the opposite one, which makes the box negatively sized and
        therefore mirrors the instance.
        """
        subs = self._glyph.subcomponents
        if not (0 <= i < len(subs)):
            return
        sub = subs[i]
        cx, cy = self._scene_to_cell(pos)
        nx = max(0, min(GRID_W, int(round(cx))))
        ny = max(0, min(GRID_H, int(round(cy))))
        sx, sy = sub.start
        ex, ey = sub.end
        if k in (0, 3):
            sx = nx
        else:
            ex = nx
        if k in (0, 1):
            sy = ny
        else:
            ey = ny
        subs[i] = sub.with_box((sx, sy), (ex, ey))
        self.update()

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

    def _cell_to_scene(self, x: float, y: float) -> QPointF:
        """Cell coordinates (integer values are cell CORNERS) -> scene point."""
        r = self._grid_rect()
        c = self._cell()
        return QPointF(r.left() + x * c, r.top() + (GRID_H - y) * c)

    def _scene_to_cell(self, pos: QPointF):
        """Scene point -> cell coordinates (the inverse of :meth:`_cell_to_scene`)."""
        r = self._grid_rect()
        c = self._cell()
        return ((pos.x() - r.left()) / c, GRID_H - (pos.y() - r.top()) / c)

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
                    qpix, baseline_px, cell_px, origin_px, advance_px = ghost
                except (TypeError, ValueError):
                    qpix, baseline_px, origin_px, advance_px = ghost, rect.height(), None, None
                    cell_px = None
                if not qpix.isNull():
                    cell = self._cell()
                    # The ghost is scaled by HEIGHT alone: the cell the provider reports (the
                    # reference's ascender+descender box, or the em) is fitted to the line height
                    # and the width simply follows the aspect. Width never drives the fit, so a
                    # ghost is free to be wider than the glyph's cell. The cell is NOT simply the
                    # bitmap height — the bitmap may carry ink outside it.
                    span = float(cell_px) if cell_px else float(qpix.height())
                    target_h = (max(1, int(round(rect.height() * qpix.height() / span)))
                                if span > 0 else qpix.height())
                    scaled = qpix.scaledToHeight(target_h, Qt.TransformationMode.SmoothTransformation)
                    scene_base_y = rect.top() + (GRID_H - self.baseline) * cell
                    if baseline_px is None:
                        # Provider says its bitmap IS its cell and has no baseline to pin: start
                        # the box where the descender ends, so the box's bottom sits on the grid
                        # bottom and its top on the grid top.
                        top = rect.top()
                    else:
                        top = scene_base_y - baseline_px * (scaled.height() / qpix.height())
                    # Centre the reference glyph by its ADVANCE box, not by its ink: the bitmap
                    # carries its pen origin and advance width, so the advance box (not the ink
                    # bbox) is centred in this glyph's cell area. Ink-centring slides any glyph
                    # with asymmetric side bearings — a period or an apostrophe drifts to the
                    # middle of its cell instead of sitting where its advance puts it.
                    k = scaled.width() / qpix.width() if qpix.width() > 0 else 1.0
                    if advance_px is None:
                        left = rect.center().x() - scaled.width() / 2.0
                    else:
                        left = (rect.center().x()
                                - origin_px * k
                                - (advance_px / 2.0) * k)
                    p.setOpacity(0.20)
                    p.drawPixmap(left, top, scaled)
                    p.setOpacity(1.0)

        # stroke fills — real-time outline from our own fast geometry (pure Python),
        # drawn as ONE non-zero-winding path so inner counters punch their holes. We do NOT clip
        # to the grid rect: out-of-bounds strokes stay visible (in red). Subcomponent instances
        # contribute their transformed strokes here, exactly as the compiler expands them.
        p.save()
        self._apply_grid_transform(p, rect)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(30, 30, 30)))
        p.drawPath(self._path_from(glyph_contours(
            self._glyph, cap=PEN_CAP, baseline=self.baseline, resolve=self.glyph_provider)))
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
        sel_sub = self.selected_subcomponent()
        if sel_sub is not None:
            p.setBrush(QBrush(QColor(0, 110, 220)))
            p.drawPath(self._path_from(subcomponent_contours(
                sel_sub, self.glyph_provider, cap=PEN_CAP, baseline=self.baseline)))
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

        # subcomponents: a distinctive dashed bounding box per instance, with a handle on each
        # corner — the instances are driven by those handles (their contents are not editable).
        sel_i = self.selected_subcomponent_index()
        for i, sub in enumerate(self._glyph.subcomponents):
            self._paint_subcomponent_box(p, sub, selected=(i == sel_i))

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

        # Wireframe: draw a coloured outline around each stroke (distinct colour per stroke),
        # on top of everything (rendered last). Optionally shows the stroke's axis line.
        if self.wireframe and self._glyph.strokes:
            self._paint_wireframe(p, rect)

    # --- wireframe -----------------------------------------------------------
    _WF_COLORS = [
        "#E6194B", "#3CB44B", "#4363D8", "#F58231", "#911EB4", "#42D4F4",
        "#F032E6", "#9A6324", "#469990", "#DCBEFF", "#FFD8B1", "#A9A9A9",
    ]

    # Outline pen width, as a fraction of a grid cell. These outlines are drawn under the
    # font-unit transform (`c / SCALE`), so a pen width given in raw font units is multiplied by
    # `1 / SCALE` on its way to the screen: 1.5 units meant 1.5/64 of a cell when SCALE was 64,
    # and lowering SCALE to 16 without touching the constant made every wireframe outline 4x
    # thicker. Deriving the width from SCALE keeps the on-screen thickness fixed.
    _WF_PEN_CELLS = 1.5 / 64

    def _paint_wireframe(self, p: QPainter, rect: QRectF) -> None:
        # Outlines (font units) under the grid transform.
        p.save()
        self._apply_grid_transform(p, rect)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for i, st in enumerate(self._glyph.strokes):
            color = QColor(self._WF_COLORS[i % len(self._WF_COLORS)])
            p.setPen(QPen(color, self._WF_PEN_CELLS * SCALE))
            p.drawPath(self._path_from(
                [stroke_outline(st, cap=PEN_CAP, baseline=self.baseline)]))
        p.restore()
        # Axis line + endpoints in scene coords (no grid transform).
        for i, st in enumerate(self._glyph.strokes):
            color = QColor(self._WF_COLORS[i % len(self._WF_COLORS)])
            a = self._grid_to_scene(st.p1.x, st.p1.y)
            b = self._grid_to_scene(st.p2.x, st.p2.y)
            p.setPen(QPen(color, 1.0, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawLine(QPointF(a.x(), a.y()), QPointF(b.x(), b.y()))
            p.setPen(QPen(color, 1.2))
            p.setBrush(color)
            p.drawEllipse(a, 2.0, 2.0)
            p.drawEllipse(b, 2.0, 2.0)

    # --- interaction ---------------------------------------------------------
    # _drag is None, ("endpoint", idx, ep) to move an endpoint, ("subcorner", i, k) to move a
    # subcomponent box corner, or ("new",) to draw a stroke.
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
                    strokes[idx] = (Stroke(gp, s.p2, s.shape, s.origin) if ep == 0
                                    else Stroke(s.p1, gp, s.shape, s.origin))
                    self.update()  # repaint the canvas only; the grid refresh is
                    # deferred to mouseRelease so it doesn't run on every drag step.
            elif kind == "subcorner":
                self._move_subcomponent_corner(self._drag[1], self._drag[2], pos)
            else:  # "new": preview the stroke while dragging
                self._hover = self._scene_to_grid(pos)
                self.update()
        else:
            self._hover = self._scene_to_grid(pos)
            self.update()

    def _select_item(self, index: int) -> None:
        if self._selected_index != index:
            self._selected_index = index
            self.selectionChanged.emit()
        self.update()

    def mousePressEvent(self, event) -> None:
        pos = event.position()
        if event.button() == Qt.MouseButton.RightButton:
            # Right-clicking an instance selects it and asks the main window for its menu.
            i = self._subcomponent_at(pos)
            if i is not None:
                self._select_item(self.stroke_count() + i)
                self.subcomponentMenuRequested.emit(i)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        gp = self._scene_to_grid(pos)
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)

        if not shift:
            handle = self._subcomponent_handle_at(pos)
            if handle is not None:
                i, k = handle
                self._snapshot()  # one undo step covers the whole drag
                self._drag = ("subcorner", i, k)
                self._pending = None
                self._hover = None
                self._select_item(self.stroke_count() + i)
                return
            endpoint = self._find_endpoint(gp)
            if endpoint is not None:
                idx, ep = endpoint
                self._snapshot()  # one undo step covers the whole drag
                self._drag = ("endpoint", idx, ep)
                self._select_item(idx)
                self._pending = None  # selecting/dragging a node cancels any half-made (click1) stroke
                self._hover = None
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
        if kind in ("endpoint", "subcorner"):
            self._drag = None
            self.update()
            self.glyphChanged.emit()  # commit the drag once, on release
        else:  # "new"
            gp = self._scene_to_grid(event.position())
            if self._pending is not None and gp != self._pending:
                try:
                    self._snapshot()
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

    def _find_endpoint(self, gp: Point):
        """Return ``(index, 0|1)`` for a stroke endpoint whose cell IS ``gp``, else ``None``.

        Draw-vs-select is keyed on the editor grid CELL that was clicked: an existing
        endpoint is selected only when the clicked cell's grid point equals one of a stroke's
        endpoints exactly (no pixel-distance tolerance). Newest stroke wins at a shared node.
        """
        for i in range(len(self._glyph.strokes) - 1, -1, -1):
            s = self._glyph.strokes[i]
            for ep, pt in enumerate((s.p1, s.p2)):
                if pt == gp:
                    return (i, ep)
        return None


