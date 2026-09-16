"""Main window: interactive grid on the left, glyph editor on the right."""

from __future__ import annotations

import math
import unicodedata2 as unicodedata
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QThread, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QCloseEvent,
    QFont,
    QFontMetricsF,
    QIcon,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..model import SHAPE_ARC, SHAPE_LINE, Glyph, StrokeFont
from ..refbrowser import ReferenceLibrary
from .glyph_canvas import GlyphCanvas
from .grid import GlyphGrid, _tint_grey
from .fontfallback import native_text_families
from .uiutil import pil_to_qpixmap, paint_stroke_glyph


_ICON_HEX = QColor(60, 60, 72)

# related-glyph thumbnail size / reference render pixel size (matches the grid cell look)
REL_PIX_W, REL_PIX_H = 60, 60
REL_REF_PX = 96
# related-glyph cell size (a bit taller than the codepoint grid to fit two buttons)
# related-glyph cell size (tall enough for the preview, the char/ID and TWO button rows)
REL_CELL_W, REL_CELL_H = 96, 172
# big static "native reference" image: width enough for wide glyphs (e.g. Arabic), and a TALLER
# height so tall descenders fit
REF_IMG_SIZE = 240
REF_IMG_H = 180
# pixel size the native-font ghost is rendered at; the canvas rescales it to the cell height
NATIVE_GHOST_PX = 160


def native_reference_bitmap(cp: int):
    """Render ``cp`` in the OS's *native* font, as a ghost bitmap for the editor canvas.

    The alternative to the reference-fonts list: instead of a font the author loaded from disk,
    use the same family stack the "Native reference" panel uses. Returns the same
    ``(pixmap, baseline_px, cell_px, origin_px, advance_px)`` contract as the reference-font
    provider — the canvas centres the ghost by that advance box — or ``None`` when there is
    nothing to draw.
    """
    if not isinstance(cp, int) or not (0 < cp < 0x110000):
        return None
    ch = chr(cp)
    if not ch.isprintable():
        return None
    f = QFont()
    f.setFamilies(native_text_families(cp))
    f.setPixelSize(NATIVE_GHOST_PX)
    fm = QFontMetricsF(f)
    advance = float(fm.horizontalAdvance(ch))
    if advance <= 0.0:
        return None
    asc = float(fm.ascent())
    desc = float(fm.descent())
    cell = asc + desc
    if cell <= 0.0:
        return None
    # Qt reports the ink box with the pen origin at x=0 and the baseline at y=0, so the pen
    # origin is at image x ``-left`` and the baseline at image y ``asc``.
    br = fm.boundingRect(ch)
    left = min(0.0, float(br.left()))
    right = max(advance, float(br.right()))
    # A Qt font engine with no usable fonts (e.g. the offscreen platform, which ships no font
    # database) reports a degenerate ink box tens of thousands of pixels wide. Nothing that big
    # can be a glyph: drop back to the advance box instead of allocating that pixmap every paint.
    if right - left > NATIVE_GHOST_PX * 2 or cell > NATIVE_GHOST_PX * 2:
        left, right = 0.0, advance
    if right <= left:
        return None
    pm = QPixmap(max(1, int(math.ceil(right - left))), max(1, int(math.ceil(cell))))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setFont(f)
    p.setPen(QColor(0, 0, 0))
    p.drawText(QPointF(-left, asc), ch)
    p.end()
    return pm, asc, cell, -left, advance


def _make_icon(size: int, draw) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    draw(p)
    p.end()
    return QIcon(pm)


def _icon_copy() -> QIcon:
    def d(p):
        p.setPen(QPen(_ICON_HEX, 1.4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(8.5, 1.5, 8, 11), 1.5, 1.5)
        p.drawRoundedRect(QRectF(3.0, 5.5, 8, 11), 1.5, 1.5)
    return _make_icon(20, d)


def _icon_paste() -> QIcon:
    def d(p):
        p.setPen(QPen(_ICON_HEX, 1.4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(4.0, 3.0, 12, 14), 1.5, 1.5)
        p.drawRoundedRect(QRectF(7.0, 1.0, 6, 5), 1.5, 1.5)
    return _make_icon(20, d)


def _icon_flip() -> QIcon:
    def d(p):
        p.setPen(QPen(_ICON_HEX, 1.4))
        p.setBrush(_ICON_HEX)
        # horizontal-flip: two mirrored triangles
        p.drawPolygon(QPolygonF([QPointF(4, 3), QPointF(16, 9), QPointF(4, 15)]))
        p.setBrush(QColor(255, 255, 255))
        p.drawPolygon(QPolygonF([QPointF(16, 4), QPointF(16, 14), QPointF(5, 9)]))
    return _make_icon(20, d)


def _icon_flip_v() -> QIcon:
    def d(p):
        p.setPen(QPen(_ICON_HEX, 1.4))
        p.setBrush(_ICON_HEX)
        # vertical-flip: two mirrored triangles (visual mirror of _icon_flip)
        p.drawPolygon(QPolygonF([QPointF(4, 3), QPointF(16, 3), QPointF(10, 15)]))
        p.setBrush(QColor(255, 255, 255))
        p.drawPolygon(QPolygonF([QPointF(5, 17), QPointF(15, 17), QPointF(10, 10)]))
    return _make_icon(20, d)


def _icon_rotate() -> QIcon:
    def d(p):
        p.setPen(QPen(_ICON_HEX, 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        # a clockwise circular arrow (open arc) with an arrowhead suggesting 90-degree rotation
        p.drawArc(QRectF(3.5, 3.5, 13, 13), 40 * 16, 250 * 16)
        p.setBrush(_ICON_HEX)
        # arrowhead at the end of the arc (top-left, pointing clockwise)
        p.drawPolygon(QPolygonF([
            QPointF(3.2, 6.2), QPointF(7.2, 3.4), QPointF(5.4, 8.4),
        ]))
    return _make_icon(20, d)


# related-glyph cell roles (QStandardItemModel)
REL_CP = Qt.ItemDataRole.UserRole
REL_HAS = Qt.ItemDataRole.UserRole + 1
REL_PIX = Qt.ItemDataRole.UserRole + 2


def _stroke_origin_tooltip(s) -> str:
    """Tooltip text for a stroke's provenance (``''`` when it carries none).

    Shows the codepoint (and character name) the stroke was copied from, plus the squish
    direction and amount when it was squished.
    """
    o = getattr(s, "origin", None)
    if o is None:
        return ""
    ch = chr(o.codepoint) if 0 <= o.codepoint <= 0x10FFFF else ""
    try:
        import unicodedata2 as unicodedata
        name = unicodedata.name(ch, "")
    except Exception:
        name = ""
    head = f"Copied from U+{o.codepoint:04X}"
    if ch and not ch.isspace():
        head += f"  {ch}"
    if name:
        head += f"\n{name}"
    if o.direction:
        amt = ""
        if o.fraction is not None:
            amt = {0.5: "1/2", 0.6667: "2/3"}.get(round(o.fraction, 4), f"{o.fraction:g}")
        head += f"\nSquished {o.direction}" + (f" to {amt} size" if amt else "")
    return head


def _rel_button_rects(rect: QRectF) -> tuple:
    """Button rects inside a related cell (shared by paint + hit-test).

    Returns ``(copy, open, arrows, diags)`` where ``arrows`` maps 'up'/'down'/'left'/'right' to
    its rect and ``diags`` maps 'upleft'/'upright'/'downleft'/'downright' to its rect. Copy/Open
    are on a row; the four axial (copy-and-squish) arrows are a compact, centered group under
    them, and the four diagonal (copy-and-squish) buttons sit below those.
    """
    m = 6.0
    row_h = 18.0
    row3_y = rect.bottom() - m - row_h              # diagonals (bottom row)
    row2_y = row3_y - (row_h + 2.0)                 # axial arrows
    row1_y = row2_y - 22.0                          # copy/open row (above the arrows)
    bw = (rect.width() - 2 * m - 4.0) / 2.0
    copy = QRectF(rect.left() + m, row1_y, bw, row_h)
    opn = QRectF(copy.right() + 4.0, row1_y, bw, row_h)

    def _group(dirs, y):
        aw, gap = 18.0, 2.0
        total = len(dirs) * aw + (len(dirs) - 1) * gap
        startx = rect.center().x() - total / 2.0
        return {d: QRectF(startx + i * (aw + gap), y, aw, row_h) for i, d in enumerate(dirs)}

    arrows = _group(["up", "down", "left", "right"], row2_y)
    diags = _group(["upleft", "upright", "downleft", "downright"], row3_y)
    return copy, opn, arrows, diags


class _RelatedDelegate(QStyledItemDelegate):
    """Paints each related cell like the codepoint grid (thumbnail + char badge) plus two
    buttons (Copy / Open) at the bottom. Mirrors GlyphGridDelegate's styling."""

    def sizeHint(self, option, index) -> "QSize":
        return QSize(REL_CELL_W, REL_CELL_H)

    def paint(self, painter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = option.rect.adjusted(3, 3, -3, -3)
        cp = index.data(REL_CP)
        has = bool(index.data(REL_HAS))

        bg = QColor(241, 242, 246)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, 6, 6)

        copy_r, opn_r, arrows, diags = _rel_button_rects(QRectF(rect))
        # preview fills the space above the button rows
        btn_h = rect.bottom() - copy_r.top()   # top edge of the copy/open row
        label_h = 14.0
        avail_w = rect.width() - 8.0
        avail_h = rect.height() - btn_h - label_h - 12.0
        pm = index.data(REL_PIX)
        if pm is not None and not pm.isNull():
            scaled = pm.scaled(
                avail_w, avail_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            px = rect.left() + (rect.width() - scaled.width()) / 2.0
            py = rect.top() + 2.0 + max(0.0, (avail_h - scaled.height()) / 2.0)
            painter.drawPixmap(px, py, scaled)

        # char badge (top-left)
        ch = chr(cp)
        if ch.isprintable() and ch != "\ufffd":
            badge = QRectF(rect.left() + 2.0, rect.top() + 2.0, 16.0, 16.0)
            painter.setBrush(QColor(255, 255, 255, 210))
            painter.drawRoundedRect(badge, 4.0, 4.0)
            painter.setPen(QColor(60, 60, 72))
            painter.setFont(QFont("", 8))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, ch)

        # codepoint id (just above the copy/open row)
        painter.setPen(QColor(130, 130, 140))
        painter.setFont(QFont("", 8))
        painter.drawText(
            QRectF(rect.left(), copy_r.top() - label_h, rect.width(), label_h),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
            f"U+{cp:04X}",
        )

        # buttons: Copy + Open, the four axial copy-and-squish arrows, then the diagonals
        self._draw_button(painter, copy_r, "Copy", enabled=has)
        self._draw_button(painter, opn_r, "Open", enabled=True)
        for d in ["up", "down", "left", "right"]:
            self._draw_button(painter, arrows[d], {"up": "↑", "down": "↓", "left": "←", "right": "→"}[d], enabled=has)
        for d in ["upleft", "upright", "downleft", "downright"]:
            self._draw_button(
                painter, diags[d],
                {"upleft": "↖", "upright": "↗", "downleft": "↙", "downright": "↘"}[d],
                enabled=has,
            )
        painter.restore()

    @staticmethod
    def _draw_button(painter, r: QRectF, text: str, enabled: bool) -> None:
        bg = QColor(228, 231, 237) if enabled else QColor(238, 238, 240)
        fg = QColor(35, 35, 42) if enabled else QColor(160, 160, 168)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(r, 4.0, 4.0)
        painter.setPen(fg)
        painter.setFont(QFont("", 8))
        painter.drawText(r, Qt.AlignmentFlag.AlignCenter, text)


class _RelatedListView(QListView):
    """The related-glyph list: same containerized IconMode list as the codepoint grid.
    Clicking a cell BODY does nothing; only the Copy / Open buttons act."""

    copyRequested = Signal(int)
    openRequested = Signal(int)
    squishRequested = Signal(int, str, float)  # (codepoint, up/down/left/right, fraction)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setSpacing(4)
        self.setUniformItemSizes(True)
        self.setMovement(QListView.Movement.Static)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setGridSize(QSize(REL_CELL_W, REL_CELL_H))
        self.setIconSize(QSize(REL_PIX_W, REL_PIX_H))
        # First-class per-pixel wheel scrolling: smooth scrollwheels/trackpads scroll
        # continuously by pixel delta, and a discrete notch scrolls a controlled amount
        # (governed by QApplication.wheelScrollLines; 1 line = one tall cell, ~ half a page)
        # instead of a whole page that cuts glyphs off top/bottom.
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            idx = self.indexAt(event.position().toPoint())
            if idx.isValid():
                cp = idx.data(REL_CP)
                has = bool(idx.data(REL_HAS))
                copy_r, opn_r, arrows, diags = _rel_button_rects(
                    QRectF(self.visualRect(idx)).adjusted(3, 3, -3, -3)
                )
                pos = event.position()
                if copy_r.contains(pos) and has:
                    self.copyRequested.emit(cp)
                    return
                if opn_r.contains(pos):
                    self.openRequested.emit(cp)
                    return
                if has:
                    for d, r in list(arrows.items()) + list(diags.items()):
                        if r.contains(pos):
                            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                            frac = 2.0 / 3.0 if shift else 0.5
                            self.squishRequested.emit(cp, d, frac)
                            return
        super().mouseReleaseEvent(event)  # cell body click: do nothing more


class ReferenceFontsDock(QWidget):
    """List of loaded reference fonts (top) + related glyphs (bottom) with add/clear controls."""

    def __init__(self, reflib: ReferenceLibrary, on_change, parent=None) -> None:
        super().__init__(parent)
        self._reflib = reflib
        self._on_change = on_change
        self._copy_strokes = None   # callable(strokes) to copy a related glyph onto the canvas
        self._open_cp = None        # callable(cp) to jump the editor to a codepoint
        self._squish_cb = None      # callable(cp, direction) to copy + squish into a half
        self._strokefont = None     # used to know whether a related glyph has authored data
        self._native_ghost_fn = None  # callable(on) to source the editor ghost natively

        lay = QVBoxLayout(self)
        self._list = QListWidget()
        lay.addWidget(self._list)
        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add Folder…")
        clear_btn = QPushButton("Clear")
        btn_row.addWidget(add_btn)
        btn_row.addWidget(clear_btn)
        lay.addLayout(btn_row)

        # where the editor's reference ghost comes from. Off by default: the ghost follows the
        # reference fonts listed above. On: it follows the codepoint's NATIVE reference instead
        # (the OS font stack), the same source as the "Native reference" panel below.
        self._native_ghost_check = QCheckBox("Ghost from native reference")
        self._native_ghost_check.setChecked(False)
        self._native_ghost_check.setToolTip(
            "Draw the editor's reference ghost with the codepoint's native (OS font)\n"
            "reference instead of the reference fonts listed above."
        )
        self._native_ghost_check.toggled.connect(self._on_native_ghost_toggled)
        lay.addWidget(self._native_ghost_check)

        # related glyphs (base char / hanzi components) in the same vertical slot. A
        # containerized IconMode list identical in styling to the main codepoint list (with a
        # white/dark area, aligned cells, scrollbars). Always visible even when empty;
        # clicking a cell body does nothing; only the Copy (grey if no data) / Open buttons act.
        self._rel_title = QLabel("Related glyphs")
        self._rel_title.setStyleSheet("font-weight: bold;")
        self._rel_model = QStandardItemModel(self)
        self._rel_list = _RelatedListView()
        self._rel_list.setModel(self._rel_model)
        self._rel_list.setItemDelegate(_RelatedDelegate(self._rel_list))
        self._rel_list.copyRequested.connect(self._copy)
        self._rel_list.openRequested.connect(self._open)
        self._rel_list.squishRequested.connect(self._squish)
        lay.addWidget(self._rel_title)
        lay.addWidget(self._rel_list, 1)
        self._set_related([])

        # third panel: a big static "native reference" — the current codepoint shown as a
        # plain QLabel in the widget's NATIVE font (no manual OS-font loading, no overlay).
        self._ref_title = QLabel("Native reference")
        self._ref_title.setStyleSheet("font-weight: bold;")
        self._ref_img = QLabel()
        self._ref_img.setFixedSize(REF_IMG_SIZE, REF_IMG_H)
        self._ref_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = self._ref_img.font()
        f.setPixelSize(110)
        self._ref_img.setFont(f)
        # a faint border + distinct background so the label's bounds are visible (you can tell
        # when a wide/tall glyph is being cut off at an edge).
        self._ref_img.setStyleSheet("background:#eceef2; border:1px solid #b8bcc4;")
        lay.addWidget(self._ref_title)
        lay.addWidget(self._ref_img)

        add_btn.clicked.connect(self.add_folder)
        clear_btn.clicked.connect(self.clear)
        self._refresh()

    def set_native_reference(self, cp: int) -> None:
        """Show the current codepoint as a big character in the widget's native font."""
        ch = ""
        if isinstance(cp, int) and 0 < cp < 0x110000:
            c = chr(cp)
            if c.isprintable():
                ch = c
        from PySide6.QtGui import QFont
        f = QFont()
        f.setFamilies(native_text_families(cp))
        f.setPixelSize(110)
        self._ref_img.setFont(f)
        self._ref_img.setText(ch)

    def set_copy_callback(self, cb) -> None:
        self._copy_strokes = cb

    def set_native_ghost_callback(self, cb) -> None:
        self._native_ghost_fn = cb

    def _on_native_ghost_toggled(self, on: bool) -> None:
        if self._native_ghost_fn is not None:
            self._native_ghost_fn(bool(on))

    def set_open_callback(self, cb) -> None:
        self._open_cp = cb

    def set_squish_callback(self, cb) -> None:
        self._squish_cb = cb

    def set_related(self, codepoints, strokefont=None) -> None:
        """Rebuild the related-glyph list for the given codepoints."""
        if strokefont is not None:
            self._strokefont = strokefont
        rows = []
        for cp in codepoints:
            name = ""
            try:
                name = unicodedata.name(chr(cp))
            except ValueError:
                pass
            g = self._strokefont.get(cp) if self._strokefont is not None else None
            has_data = g is not None and bool(g.strokes)
            rows.append((cp, name, has_data))
        self._set_related(rows)

    def _set_related(self, rows) -> None:
        # Always visible, even when empty. Each related glyph becomes one cell in the list.
        self._rel_model.clear()
        for cp, name, has_data in rows:
            item = QStandardItem()
            item.setData(cp, REL_CP)
            item.setData(has_data, REL_HAS)
            item.setData(self._render_related_pixmap(cp), REL_PIX)
            item.setToolTip(f"U+{cp:04X}  {name}")
            self._rel_model.appendRow(item)

    def _render_related_pixmap(self, cp: int):
        """A thumbnail of a related glyph: authored strokes (black) or reference font (grey)."""
        from PySide6.QtGui import QPainter, QPixmap
        from PySide6.QtCore import QRectF
        glyph = self._strokefont.get(cp) if self._strokefont is not None else None
        if glyph is not None and glyph.strokes:
            pm = QPixmap(REL_PIX_W, REL_PIX_H)
            pm.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pm)
            paint_stroke_glyph(
                painter, glyph, QRectF(2, 2, REL_PIX_W - 4, REL_PIX_H - 4),
                color=QColor(25, 25, 25), baseline=self._strokefont.baseline,
            )
            painter.end()
            return pm
        if self._reflib is not None and self._reflib.has(cp):
            img = self._reflib.render_first(cp, box_px=REL_PIX_W, pixel_size=REL_REF_PX)
            if img is not None:
                return pil_to_qpixmap(_tint_grey(img))
        return None

    def _copy(self, cp) -> None:
        if self._copy_strokes is not None:
            self._copy_strokes(cp)

    def _open(self, cp) -> None:
        if self._open_cp is not None:
            self._open_cp(cp)

    def _squish(self, cp, direction, fraction) -> None:
        if self._squish_cb is not None:
            self._squish_cb(cp, direction, fraction)

    def add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder of reference fonts")
        if folder:
            n = self._reflib.add_folders([folder], recursive=True)
            self._refresh()
            if self._on_change:
                self._on_change()
            QMessageBox.information(self, "Reference fonts", f"Added {n} font(s).")

    def clear(self) -> None:
        self._reflib.clear()
        self._refresh()
        if self._on_change:
            self._on_change()

    def _refresh(self) -> None:
        self._list.clear()
        for f in self._reflib:
            self._list.addItem(QListWidgetItem(f"{f.family}  ({f.path.name})"))


class GlyphEditorPanel(QWidget):
    """Right-hand panel: codepoint controls + stroke canvas + stroke list."""

    glyphChanged = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._glyph: Optional[Glyph] = None

        top = QHBoxLayout()
        self._cp_label = QLabel("U+0000")
        self._cp_label.setStyleSheet("font-weight:bold;")
        top.addWidget(self._cp_label)
        self._wireframe = QCheckBox("Wireframe")
        self._wireframe.setToolTip(
            "Draw a coloured outline around each stroke (each stroke a distinct colour), on top"
        )
        top.addWidget(self._wireframe)
        top.addStretch(1)
        top.addWidget(QLabel("Width:"))
        self._width_combo = QComboBox()
        self._width_combo.addItem("16 (full)", 16)
        self._width_combo.addItem("8 (half)", 8)
        self._width_combo.setToolTip("Glyph width: 8 (half) or 16 (full) — N toggles")
        top.addWidget(self._width_combo)
        self._combining = QCheckBox("Combining")
        self._combining.setToolTip("Combining mark (zero advance width) — M toggles")
        top.addWidget(self._combining)
        self._empty = QCheckBox("Empty")
        self._empty.setToolTip("Intentionally blank but assigned glyph (e.g. a space) — persists it")
        top.addWidget(self._empty)
        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setToolTip("Remove all strokes from this glyph (Ctrl+H)")
        top.addWidget(self._clear_btn)

        self.canvas = GlyphCanvas()
        self._wireframe.toggled.connect(self.canvas.set_wireframe)

        self._stroke_list = QListWidget()
        self._stroke_list.setMinimumWidth(190)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        tool_row = QHBoxLayout()
        self._btn_line = QToolButton(); self._btn_line.setText("Line")
        self._btn_line.setToolTip("Draw straight-line strokes (tool)")
        self._btn_arc = QToolButton(); self._btn_arc.setText("Arc")
        self._btn_arc.setToolTip("Draw single-quadratic arc strokes (tool)")
        self._btn_line.setCheckable(True)
        self._btn_arc.setCheckable(True)
        self._btn_line.setChecked(True)
        tool_row.addWidget(self._btn_line)
        tool_row.addWidget(self._btn_arc)
        tool_row.addStretch(1)
        col.addLayout(tool_row)

        opt_row = QHBoxLayout()
        self._show_ghost = QCheckBox("Reference ghost")
        self._show_ghost.setChecked(True)
        opt_row.addWidget(self._show_ghost)
        opt_row.addStretch(1)
        col.addLayout(opt_row)

        # tool controls row: copy / paste / flip / rotate (icons only)
        nudge_row = QHBoxLayout()
        nudge_row.setSpacing(2)
        nudge_row.setContentsMargins(0, 0, 0, 0)
        b_copy = QToolButton(); b_copy.setIcon(_icon_copy()); b_copy.setFixedSize(22, 22)
        b_copy.setToolTip("Copy all strokes (Ctrl+C)")
        b_paste = QToolButton(); b_paste.setIcon(_icon_paste()); b_paste.setFixedSize(22, 22)
        b_paste.setToolTip("Paste (append) strokes (Ctrl+V)")
        b_flip = QToolButton(); b_flip.setIcon(_icon_flip()); b_flip.setFixedSize(22, 22)
        b_flip.setToolTip("Flip horizontally (F)")
        b_flip_v = QToolButton(); b_flip_v.setIcon(_icon_flip_v()); b_flip_v.setFixedSize(22, 22)
        b_flip_v.setToolTip("Flip vertically")
        b_rot = QToolButton(); b_rot.setIcon(_icon_rotate()); b_rot.setFixedSize(22, 22)
        b_rot.setToolTip("Rotate 90\u00b0 clockwise")
        # 1-pixel nudge arrows live on their own row so this row stays narrow.
        b_n = QToolButton(); b_n.setArrowType(Qt.ArrowType.UpArrow); b_n.setFixedSize(20, 20)
        b_n.setToolTip("Nudge whole glyph up (W)")
        b_s = QToolButton(); b_s.setArrowType(Qt.ArrowType.DownArrow); b_s.setFixedSize(20, 20)
        b_s.setToolTip("Nudge whole glyph down (S)")
        b_w = QToolButton(); b_w.setArrowType(Qt.ArrowType.LeftArrow); b_w.setFixedSize(20, 20)
        b_w.setToolTip("Nudge whole glyph left (A)")
        b_e = QToolButton(); b_e.setArrowType(Qt.ArrowType.RightArrow); b_e.setFixedSize(20, 20)
        b_e.setToolTip("Nudge whole glyph right (D)")
        for w in (b_copy, b_paste, b_flip, b_flip_v, b_rot):
            nudge_row.addWidget(w)
        nudge_row.addStretch(1)
        col.addLayout(nudge_row)
        # nudge arrows on their own row
        nudge_row2 = QHBoxLayout()
        nudge_row2.setSpacing(2)
        nudge_row2.setContentsMargins(0, 0, 0, 0)
        for w in (b_n, b_s, b_w, b_e):
            nudge_row2.addWidget(w)
        nudge_row2.addStretch(1)
        col.addLayout(nudge_row2)

        btn_delete = QPushButton("Delete selected")
        btn_delete.setToolTip("Delete the selected stroke (Delete)")
        btn_reverse = QPushButton("Reverse points (flip arc)")
        btn_reverse.setToolTip("Reverse the selected stroke's points (flips an arc's bend) (R)")
        btn_toggle = QPushButton("Toggle line/arc")
        btn_toggle.setToolTip("Switch the selected stroke between line and arc (B)")
        col.addWidget(btn_delete)
        col.addWidget(btn_reverse)
        col.addWidget(btn_toggle)
        col.addStretch(1)

        mid = QHBoxLayout()
        mid.addWidget(self._stroke_list, 1)
        mid.addLayout(col)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.canvas, 1)
        lay.addLayout(mid)

        self._stroke_list.currentRowChanged.connect(self._on_stroke_selected)
        btn_delete.clicked.connect(self._focus_back(self.canvas.delete_selected))
        btn_reverse.clicked.connect(self._focus_back(self.canvas.reverse_selected))
        btn_toggle.clicked.connect(self._focus_back(self.canvas.toggle_selected_shape))
        self._width_combo.currentIndexChanged.connect(self._on_width_changed)
        self._combining.toggled.connect(self._on_combining_changed)
        self._empty.toggled.connect(self._on_empty_changed)
        self._clear_btn.clicked.connect(self._focus_back(self._on_clear))
        self._btn_line.clicked.connect(self._focus_back(lambda: self._set_tool(SHAPE_LINE)))
        self._btn_arc.clicked.connect(self._focus_back(lambda: self._set_tool(SHAPE_ARC)))
        self._show_ghost.toggled.connect(self._on_ghost_toggled)
        b_copy.clicked.connect(self._focus_back(self.canvas.copy_strokes))
        b_paste.clicked.connect(self._focus_back(self.canvas.paste_strokes))
        b_flip.clicked.connect(self._focus_back(self.canvas.flip_horizontal))
        b_flip_v.clicked.connect(self._focus_back(self.canvas.flip_vertical))
        b_rot.clicked.connect(self._focus_back(self.canvas.rotate_90_cw))
        b_n.clicked.connect(self._focus_back(lambda: self.canvas.nudge(0, 1)))
        b_s.clicked.connect(self._focus_back(lambda: self.canvas.nudge(0, -1)))
        b_w.clicked.connect(self._focus_back(lambda: self.canvas.nudge(-1, 0)))
        b_e.clicked.connect(self._focus_back(lambda: self.canvas.nudge(1, 0)))
        self._stroke_list.itemClicked.connect(lambda _item: self.canvas.setFocus())
        self.canvas.glyphChanged.connect(self._on_canvas_changed)
        self.canvas.clearRequested.connect(self._on_clear)
        self.canvas.toggleWidthRequested.connect(self._toggle_width)
        self.canvas.toggleCombiningRequested.connect(self._toggle_combining)

    def _toggle_width(self) -> None:
        cur = self._width_combo.currentData()
        new = 8 if cur == 16 else 16
        idx = self._width_combo.findData(new)
        if idx >= 0:
            self._width_combo.setCurrentIndex(idx)  # triggers _on_width_changed

    def _toggle_combining(self) -> None:
        self._combining.toggle()  # triggers _on_combining_changed

    def _on_canvas_changed(self) -> None:
        # Canvas edits propagate up so MainWindow can initialise/commit the glyph and
        # mark the document dirty (otherwise a newly drawn glyph would never be saved).
        self._sync_stroke_list()
        self.glyphChanged.emit()

    def set_glyph(self, glyph: Glyph) -> None:
        self._glyph = glyph
        self.canvas.set_glyph(glyph)
        self._cp_label.setText(f"U+{glyph.codepoint:04X}")
        idx = self._width_combo.findData(glyph.width)
        self._width_combo.blockSignals(True)
        self._width_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._width_combo.blockSignals(False)
        self._combining.blockSignals(True)
        self._combining.setChecked(glyph.combining)
        self._combining.blockSignals(False)
        self._empty.blockSignals(True)
        self._empty.setChecked(glyph.empty)
        self._empty.blockSignals(False)
        self._sync_stroke_list()

    def glyph(self) -> Optional[Glyph]:
        return self._glyph

    def refresh_canvas(self) -> None:
        self.canvas.update()
        self._sync_stroke_list()

    def set_reference_provider(self, provider) -> None:
        self.canvas.reference_provider = provider
        self.canvas.update()

    def _focus_back(self, run):
        """Run a control's action, then hand keyboard focus back to the canvas."""
        def go(*args):
            run()
            self.canvas.setFocus()
        return go

    def _set_tool(self, tool: str) -> None:
        self._btn_line.blockSignals(True)
        self._btn_arc.blockSignals(True)
        self._btn_line.setChecked(tool == SHAPE_LINE)
        self._btn_arc.setChecked(tool == SHAPE_ARC)
        self._btn_line.blockSignals(False)
        self._btn_arc.blockSignals(False)
        self.canvas.set_tool(tool)

    def _on_ghost_toggled(self, on: bool) -> None:
        self.canvas.show_reference = on
        self.canvas.update()

    def _on_stroke_selected(self, row: int) -> None:
        self.canvas.select_stroke(row)

    def _on_width_changed(self, _idx: int) -> None:
        if self._glyph is not None and self._glyph.width != self._width_combo.currentData():
            self._glyph.width = self._width_combo.currentData()
            self.canvas.update()  # the grid rect/cell size depends on the glyph width
            self.glyphChanged.emit()

    def _on_combining_changed(self, on: bool) -> None:
        if self._glyph is not None and self._glyph.combining != on:
            self._glyph.combining = on
            self.glyphChanged.emit()

    def _on_empty_changed(self, on: bool) -> None:
        if self._glyph is not None and self._glyph.empty != on:
            self._glyph.empty = on
            self.glyphChanged.emit()

    def _on_clear(self) -> None:
        if self._glyph is None:
            return
        if self._glyph.strokes and QMessageBox.question(
            self, "Clear strokes", "Remove all strokes from this glyph?"
        ) != QMessageBox.StandardButton.Yes:
            return
        self._glyph.strokes.clear()
        self.canvas.update()
        self.glyphChanged.emit()

    def _sync_stroke_list(self) -> None:
        self._stroke_list.blockSignals(True)
        self._stroke_list.clear()
        if self._glyph is not None:
            for i, s in enumerate(self._glyph.strokes):
                kind = "Arc" if s.shape == SHAPE_ARC else "Ln "
                item = QListWidgetItem(
                    f"{i}: {kind} ({s.p1.x},{s.p1.y})→({s.p2.x},{s.p2.y})"
                )
                tip = _stroke_origin_tooltip(s)
                if tip:
                    item.setToolTip(tip)
                self._stroke_list.addItem(item)
            if 0 <= self.canvas._selected_index < len(self._glyph.strokes):
                self._stroke_list.setCurrentRow(self.canvas._selected_index)
        self._stroke_list.blockSignals(False)


class _CompileThread(QThread):
    """Compiles the font off the UI thread, reporting progress via signals.

    ``progress(done, total)`` — ``total`` is the glyph count while the UFO is built
    (determinate); ``total`` is ``None`` while the Google CLI compiler runs (indeterminate).
    """

    progress = Signal(object, object)
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, strokefont, path: str, family: str) -> None:
        super().__init__()
        self._sf = strokefont
        self._path = path
        self._family = family

    def run(self) -> None:
        from ..compiler import compile_strokefont  # local import to avoid cycles

        def cb(done, total):
            # Throttle emissions so a huge glyph set does not flood the UI event queue.
            if total is None or done == total or done % max(1, total // 100) == 0:
                self.progress.emit(done, total)

        try:
            compile_strokefont(
                self._sf,
                self._path,
                family_name=self._family,
                on_progress=cb,
            )
        except Exception as e:
            self.failed.emit(str(e))
            return
        self.succeeded.emit(self._path)


class MainWindow(QMainWindow):
    def __init__(self, path: Optional[str] = None) -> None:
        super().__init__()
        self.setWindowTitle("strokespec — stroke fallback font editor")
        self.resize(1280, 780)

        self.strokefont = StrokeFont()
        self.reflib = ReferenceLibrary()
        self._path: Optional[str] = path
        self._dirty = False
        self._native_ghost = False   # ghost source: reference fonts (False) or native font (True)
        self._pending_commit: Optional[Glyph] = None

        # Load the project's `ref fonts/` folder (if present) by default.
        from pathlib import Path as _Path
        repo_root = _Path(__file__).resolve().parents[3]
        default_ref = repo_root / "ref fonts"
        if default_ref.is_dir():
            self.reflib.add_folders([str(default_ref)], recursive=False)

        self._grid = GlyphGrid(self.strokefont, self.reflib)
        self._editor = GlyphEditorPanel()
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(self._grid)
        self._splitter.addWidget(self._editor)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 2)
        self.setCentralWidget(self._splitter)

        self._refdock = ReferenceFontsDock(self.reflib, self._on_refs_changed)
        self._refdock.set_copy_callback(self._copy_related_strokes)
        self._refdock.set_open_callback(self._open_codepoint)
        self._refdock.set_squish_callback(self._squish_related_strokes)
        self._refdock.set_native_ghost_callback(self._on_native_ghost)
        dock = QDockWidget("Reference Fonts", self)
        dock.setWidget(self._refdock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

        self._build_menu()
        self._grid.glyphChosen.connect(self._open_codepoint)
        self._editor.glyphChanged.connect(self._on_glyph_changed)
        self._editor.canvas.navRequested.connect(self._on_nav)
        self._editor.set_reference_provider(self._reference_for_codepoint)
        self._editor.canvas.set_metrics(
            self.strokefont.baseline, self.strokefont.x_height, self.strokefont.cap_height
        )

        if path:
            self._load_into(path)
        self._open_codepoint(0x20)

    # --- menus ---------------------------------------------------------------
    def _build_menu(self) -> None:
        m = self.menuBar()
        fm = m.addMenu("&File")
        for text, slot, shortcut in [
            ("&New", self._new, ""),  # no Ctrl+N: reserved for the editor width toggle area
            ("&Open…", self._open, "Ctrl+O"),
            ("&Save", self._save, "Ctrl+S"),
            ("Save &As…", self._save_as, "Ctrl+Shift+S"),
            ("&Compile TTF…", self._compile, "Ctrl+T"),
            ("&Quit", self.close, "Ctrl+Q"),
        ]:
            act = QAction(text, self)
            if shortcut:
                act.setShortcut(QKeySequence(shortcut))
            act.triggered.connect(slot)
            fm.addAction(act)
        rm = m.addMenu("&Reference")
        ref_add = QAction("Add Font Folder…", self)
        ref_add.triggered.connect(self._refdock.add_folder)
        rm.addAction(ref_add)

        fm_ = m.addMenu("&Font")
        metrics = QAction("Metrics &Options…", self)
        metrics.triggered.connect(self._edit_metrics)
        fm_.addAction(metrics)

    # --- reference helper ----------------------------------------------------
    def _reference_for_codepoint(self, cp: int):
        if self._native_ghost:
            return native_reference_bitmap(cp)
        f = self.reflib.first_with(cp)
        if f is None:
            return None
        res = f.glyph_bitmap(cp, 160)
        if res is None:
            return None
        img, baseline_px, cap_px, origin_px, advance_px = res
        return (pil_to_qpixmap(img), baseline_px, cap_px, origin_px, advance_px)

    def _on_native_ghost(self, on: bool) -> None:
        """Switch the editor's reference ghost between the reference fonts and the native font."""
        self._native_ghost = bool(on)
        self._editor.refresh_canvas()

    def _on_refs_changed(self) -> None:
        self._grid.refresh()
        self._editor.refresh_canvas()

    # --- glyph selection -----------------------------------------------------
    def _open_codepoint(self, cp: int) -> None:
        existing = self.strokefont.get(cp)
        if existing is not None:
            self._pending_commit = None
            self._editor.set_glyph(existing)  # edit in place
        else:
            glyph = Glyph(cp)
            self._pending_commit = glyph
            self._editor.set_glyph(glyph)
        self._update_related()
        # opening from the codepoint list should hand keyboard focus to the canvas so its
        # hotkeys (WASD nudge, F flip, B toggle, Ctrl+Z/C/V/H…) work immediately.
        self._editor.canvas.setFocus(Qt.FocusReason.MouseFocusReason)

    def _on_nav(self, direction: str) -> None:
        """Tab / arrow keys: move in the codepoint list and auto-open the moved-to glyph.

        'left'/'right' step one cell (wrapping); 'up'/'down' step a whole row (clamped).
        """
        cps = self._grid.codepoints()
        if not cps:
            return
        cur = self._editor.glyph().codepoint
        try:
            i = cps.index(cur)
        except ValueError:
            return
        step = {"left": -1, "right": 1, "up": -self._grid.column_count(),
                "down": self._grid.column_count()}[direction]
        if direction in ("left", "right"):
            j = (i + step) % len(cps)
        else:
            j = min(max(0, i + step), len(cps) - 1)
        self._open_codepoint(cps[j])
        self._grid.select_codepoint(cps[j])

    def _update_related(self) -> None:
        """Refresh the 'Related glyphs' panel and the 'Native reference' image for the current glyph."""
        g = self._editor.glyph()
        if g is None:
            self._refdock.set_related([])
            self._refdock.set_native_reference(None)
            return
        from ..rels import related_codepoints
        self._refdock.set_related(related_codepoints(g.codepoint), self.strokefont)
        self._refdock.set_native_reference(g.codepoint)

    def _copy_related_strokes(self, cp: int) -> None:
        """Append the strokes of the related glyph ``cp`` (if it has data) onto the canvas."""
        g = self.strokefont.get(cp)
        if g is not None and g.strokes:
            # Provenance: the glyph actually copied from (never traced back to its own sources).
            # Copied from an unallocated scratch glyph (or into the same codepoint), the existing
            # provenance data is carried through rather than overwritten.
            cur = self._editor.glyph()
            target_cp = cur.codepoint if cur is not None else -1
            from ..model import copied_origin
            self._editor.canvas.append_strokes(
                [s.with_origin(copied_origin(cp, target_cp, s.origin)) for s in g.strokes])
        self._editor.canvas.setFocus()  # hand focus back to the canvas

    def _squish_related_strokes(self, cp: int, direction: str, fraction: float = 0.5) -> None:
        """Copy + squish the related glyph's strokes into a fraction of the current glyph's grid.

        ``fraction`` is 1/2 normally, or 2/3 when Shift is held. Only the added strokes are
        squished (a linear transform, then rounding); existing strokes are untouched. The append
        is a single undo/redo step.
        """
        g = self.strokefont.get(cp)
        if g is None or not g.strokes:
            return
        cur = self._editor.glyph()
        width = cur.cell_width_grid if cur is not None else 16
        target_cp = cur.codepoint if cur is not None else -1
        from ..model import squish_strokes, copied_origin
        src = list(g.strokes)
        out = squish_strokes(src, direction, width, fraction=fraction)
        out = [o.with_origin(copied_origin(cp, target_cp, s.origin, direction, fraction))
               for o, s in zip(out, src)]
        if out:
            self._editor.canvas.append_strokes(out)
        self._editor.canvas.setFocus()  # hand focus back to the canvas

    def _store_glyph(self, glyph: Glyph) -> Glyph:
        """Persist the current editor glyph into the stroke set (add or update)."""
        stored = self.strokefont.ensure(glyph.codepoint)
        stored.strokes[:] = glyph.strokes
        stored.width = glyph.width
        stored.combining = glyph.combining
        stored.name = glyph.name
        stored.empty = glyph.empty
        return stored

    def _on_glyph_changed(self) -> None:
        """Resolve the glyph's assigned/unassigned state.

        A glyph is ASSIGNED iff it has strokes or is marked ``empty`` (an intentional blank).
        Otherwise (no strokes, not marked empty) it is UNASSIGNED: it is dropped from the stroke
        set (so 'Show unassigned' shows it again). Combining marks are treated exactly like any
        other glyph — they are zero-ADVANCE, not zero-ink, so an authored mark has strokes and
        an intentional blank mark must be marked Empty. The 'Empty' checkbox is the control for
        keeping any blank glyph as assigned.
        """
        glyph = self._editor.glyph()
        if glyph is None:
            return
        assigned = bool(glyph.strokes) or glyph.empty
        in_set = self.strokefont.has(glyph.codepoint)

        if assigned:
            stored = self._store_glyph(glyph)
            if self._pending_commit is not None or not in_set:
                # a brand-new glyph (or one reassigned after being cleared): switch onto the
                # stored glyph and refresh the grid so it appears in the block.
                self._pending_commit = None
                self._editor.set_glyph(stored)
                if stored.strokes:
                    self._editor.canvas._selected_index = len(stored.strokes) - 1
                    self._editor._sync_stroke_list()
                    self._editor.canvas.update()
                self._grid.refresh()
                self._dirty = True
                self._set_window_title()
            else:
                # editing an existing glyph in place: repaint only its single cell.
                self._mark_dirty(invalidate_grid=False)
                self._grid.invalidate_preview(glyph.codepoint)
        else:
            # cleared to empty without the Empty flag -> unassigned, drop from the set
            if in_set:
                self.strokefont.remove(glyph.codepoint)
                self._grid.refresh()
            self._pending_commit = None

    def _mark_dirty(self, invalidate_grid: bool = False) -> None:
        self._dirty = True
        self.setWindowTitle(
            f"strokespec — {self._path or 'untitled'}{' *' if self._dirty else ''}"
        )
        if invalidate_grid:
            self._grid.invalidate_previews()

    # --- document lifecycle --------------------------------------------------
    def _new(self) -> None:
        self.strokefont.glyphs.clear()
        self.strokefont.metadata = StrokeFont().metadata
        self._path = None
        self._dirty = False
        self._set_window_title()
        self._editor.canvas.set_metrics(
            self.strokefont.baseline, self.strokefont.x_height, self.strokefont.cap_height
        )
        self._grid.refresh()
        self._open_codepoint(0x20)

    def _open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open stroke set", "", "Strokespec (*.strokes.json *.json)"
        )
        if path:
            self._load_into(path)

    def _load_into(self, path: str) -> None:
        try:
            loaded = StrokeFont.load(path)
        except Exception as e:
            QMessageBox.critical(self, "Open failed", str(e))
            return
        self.strokefont.glyphs = loaded.glyphs
        self.strokefont.metadata = loaded.metadata
        self._path = path
        self._dirty = False
        self._set_window_title()
        self._editor.canvas.set_metrics(
            self.strokefont.baseline, self.strokefont.x_height, self.strokefont.cap_height
        )
        self._grid.refresh()
        self._open_codepoint(0x20)

    def _save(self) -> None:
        if not self._path:
            self._save_as()
            return
        self._write(self._path)

    def _save_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save stroke set", "glyphs", "Strokespec (*.strokes.json)"
        )
        if path:
            if not path.lower().endswith(".json"):
                path += ".strokes.json"
            self._path = path
            self._write(path)

    def _write(self, path: str) -> None:
        try:
            self.strokefont.save(path)
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return
        self._dirty = False
        self._set_window_title()

    def _set_window_title(self) -> None:
        star = " *" if self._dirty else ""
        self.setWindowTitle(f"strokespec — {self._path or 'untitled'}{star}")

    def _edit_metrics(self) -> None:
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout
        dlg = QDialog(self)
        dlg.setWindowTitle("Font Metrics")
        form = QFormLayout(dlg)
        baseline_spin = QDoubleSpinBox()
        baseline_spin.setRange(0.0, 16.0)
        baseline_spin.setSingleStep(0.5)
        baseline_spin.setValue(self.strokefont.baseline)
        xh_spin = QDoubleSpinBox()
        xh_spin.setRange(0.0, 16.0)
        xh_spin.setSingleStep(0.5)
        xh_spin.setValue(self.strokefont.x_height)
        ch_spin = QDoubleSpinBox()
        ch_spin.setRange(0.0, 16.0)
        ch_spin.setSingleStep(0.5)
        ch_spin.setValue(self.strokefont.cap_height)
        form.addRow("Baseline (cells from bottom):", baseline_spin)
        form.addRow("x-height (cells):", xh_spin)
        form.addRow("cap-height (cells):", ch_spin)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.strokefont.metadata["baseline"] = baseline_spin.value()
            self.strokefont.metadata["x_height"] = xh_spin.value()
            self.strokefont.metadata["cap_height"] = ch_spin.value()
            self._editor.canvas.set_metrics(
            self.strokefont.baseline, self.strokefont.x_height, self.strokefont.cap_height
        )
            self._editor.refresh_canvas()
            self._grid.refresh()
            self._dirty = True
            self._set_window_title()

    def _compile(self) -> None:
        if not self.strokefont.glyphs:
            QMessageBox.information(self, "Compile", "No glyphs to compile yet.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save compiled TTF", "fallback", "TTF font (*.ttf)"
        )
        if not path:
            return
        if not path.lower().endswith(".ttf"):
            path += ".ttf"

        family = self.strokefont.metadata.get("name", "Uffy Fallback")
        dlg = QProgressDialog(f"Compiling {path}…", None, 0, 0, self)
        dlg.setWindowTitle("Compile")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setValue(0)

        thread = _CompileThread(self.strokefont, path, family)

        def on_progress(done, total):
            if total is None:
                dlg.setRange(0, 0)  # indeterminate: CLI compiler is running
                dlg.setLabelText("Compiling with Google font tools…")
            else:
                dlg.setRange(0, max(1, total))
                dlg.setValue(int(done))
                dlg.setLabelText(f"Building outlines… {done}/{total}")

        def on_succeeded(out):
            dlg.close()
            self.statusBar().showMessage(f"Wrote {out}")
            QMessageBox.information(self, "Compile", f"Wrote TTF:\n{out}")

        def on_failed(msg):
            dlg.close()
            self.statusBar().clearMessage()
            QMessageBox.critical(self, "Compile failed", msg)

        thread.progress.connect(on_progress)
        thread.succeeded.connect(on_succeeded)
        thread.failed.connect(on_failed)
        self._compile_thread = thread  # keep a reference so it is not collected
        self._compile_dlg = dlg
        dlg.show()
        thread.start()

    # --- close ---------------------------------------------------------------
    def closeEvent(self, event: QCloseEvent) -> None:
        if self._dirty:
            ret = QMessageBox.question(
                self,
                "Unsaved changes",
                "Save changes before quitting?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if ret == QMessageBox.StandardButton.Save:
                self._save()
                if self._dirty:
                    event.ignore()
                    return
            elif ret == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
        event.accept()
