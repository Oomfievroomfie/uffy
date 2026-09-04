"""Interactive glyph grid: a FontForge-style, individually clickable cell container.

Each cell is its own selectable item in a ``QListView`` (icon mode). It shows:
  * the authored stroke glyph (if any), or
  * a reference-font preview (for codepoints with no stroke glyph yet), or
  * an empty slot.
Clicking a cell emits the codepoint so the main window opens it in the glyph editor.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import unicodedata

from PIL import Image

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QRect,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QListView,
    QStyledItemDelegate,
    QStyle,
    QWidget,
)

from ..model import StrokeFont, Glyph, GRID_H
from ..refbrowser import ReferenceLibrary
from .uiutil import pil_to_qpixmap, paint_stroke_glyph

CP_ROLE = Qt.ItemDataRole.UserRole
CELL_W, CELL_H = 76, 88
PIX_W, PIX_H = 60, 60


def _is_printable(cp: int) -> bool:
    if cp < 0x20:
        return False
    if 0x7F <= cp <= 0xA0:
        return False
    if 0xD800 <= cp <= 0xDFFF:
        return False
    return True


def _unicode_name(cp: int) -> str:
    """The codepoint's name from the Unicode database, or '' if unnamed/unassigned."""
    try:
        return unicodedata.name(chr(cp))
    except (ValueError, TypeError):
        return ""


def _tint_grey(img: "Image.Image", rgb=(150, 150, 150)) -> "Image.Image":
    """Recolor an RGBA reference bitmap to grey (keep its alpha mask)."""
    img = img.convert("RGBA")
    out = Image.new("RGBA", img.size, (*rgb, 0))
    out.putalpha(img.getchannel("A"))
    return out


class GlyphGridModel(QAbstractListModel):
    """Exposes a flat list of codepoints, with cached preview pixmaps per cell."""

    def __init__(self, strokefont: StrokeFont, reflib: ReferenceLibrary, parent=None) -> None:
        super().__init__(parent)
        self.strokefont = strokefont
        self.reflib = reflib
        self._cps: List[int] = []
        self._pixmap_cache: Dict[int, Optional[QPixmap]] = {}

    def set_codepoints(self, cps: List[int]) -> None:
        self.beginResetModel()
        self._cps = list(cps)
        self._pixmap_cache.clear()
        self.endResetModel()

    def codepoints(self) -> List[int]:
        return list(self._cps)

    def invalidate_previews(self) -> None:
        self._pixmap_cache.clear()
        if self._cps:
            top = self.index(0, 0)
            bottom = self.index(len(self._cps) - 1, 0)
            self.dataChanged.emit(top, bottom, [])

    def invalidate_preview(self, cp: int) -> None:
        """Repaint only the one cell for ``cp`` (do NOT touch the rest of the grid)."""
        self._pixmap_cache.pop(cp, None)
        try:
            row = self._cps.index(cp)
        except ValueError:
            return
        idx = self.index(row, 0)
        self.dataChanged.emit(idx, idx, [])

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._cps)

    def codepoint_at(self, row: int) -> int:
        return self._cps[row]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._cps)):
            return None
        cp = self._cps[index.row()]
        if role == CP_ROLE:
            return cp
        if role == Qt.ItemDataRole.DisplayRole:
            return chr(cp) if _is_printable(cp) else ""
        if role == Qt.ItemDataRole.ToolTipRole:
            name = _unicode_name(cp)
            return f"U+{cp:04X}  {name}" if name else f"U+{cp:04X}"
        return None

    # --- preview caching ------------------------------------------------------
    def preview_pixmap(self, cp: int) -> Optional[QPixmap]:
        if cp in self._pixmap_cache:
            return self._pixmap_cache[cp]
        px = self._render_preview(cp)
        # keep cache bounded to avoid unbounded growth on huge blocks
        if len(self._pixmap_cache) > 4000:
            self._pixmap_cache.clear()
        self._pixmap_cache[cp] = px
        return px

    def _render_preview(self, cp: int) -> Optional[QPixmap]:
        glyph = self.strokefont.get(cp)
        if glyph is not None and glyph.strokes:
            pm = QPixmap(PIX_W, PIX_H)
            pm.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pm)
            paint_stroke_glyph(
                painter, glyph, QRectF(2, 2, PIX_W - 4, PIX_H - 4),
                color=QColor(25, 25, 25),
                baseline=self.strokefont.baseline,
            )
            painter.end()
            return pm
        if self.reflib.has(cp):
            img = self.reflib.render_first(cp, box_px=PIX_W, pixel_size=MAX_REF_PX)
            if img is not None:
                return pil_to_qpixmap(_tint_grey(img))  # fallback ghost: grey, not black
        return None


MAX_REF_PX = 128


class GlyphGridDelegate(QStyledItemDelegate):
    """Paints each cell: preview + codepoint label."""

    def __init__(self, model: GlyphGridModel, parent=None) -> None:
        super().__init__(parent)
        self._model = model

    def sizeHint(self, option, index) -> QSize:
        return QSize(CELL_W, CELL_H)

    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = option.rect.adjusted(3, 3, -3, -3)
        cp = index.data(CP_ROLE)

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)

        bg = QColor(241, 242, 246)
        if selected:
            bg = QColor(190, 215, 250)
        elif hover:
            bg = QColor(232, 238, 248)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, 6, 6)

        # Character preview fills most of the cell.
        label_h = 16.0
        avail_w = rect.width() - 8.0
        avail_h = rect.height() - label_h - 4.0
        pm = self._model.preview_pixmap(cp)
        if pm is not None and not pm.isNull():
            scaled = pm.scaled(
                avail_w, avail_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            px = rect.left() + (rect.width() - scaled.width()) / 2.0
            py = rect.top() + 2.0 + max(0.0, (avail_h - scaled.height()) / 2.0)
            painter.drawPixmap(px, py, scaled)

        # Small plaintext character, top-left corner (never overlaps the id at the bottom).
        char = chr(cp) if _is_printable(cp) else ""
        if char:
            badge = QRectF(rect.left() + 2.0, rect.top() + 2.0, 18.0, 18.0)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 210))
            painter.drawRoundedRect(badge, 4.0, 4.0)
            painter.setPen(QColor(60, 60, 72))
            painter.setFont(QFont("", 9))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignHCenter, char)

        # Codepoint id pinned to the bottom.
        painter.setPen(QColor(130, 130, 140))
        painter.setFont(QFont("", 8))
        painter.drawText(
            QRectF(rect.left(), rect.bottom() - label_h, rect.width(), label_h),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
            f"U+{cp:04X}",
        )
        if selected:
            painter.setPen(QColor(0, 110, 220))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, 6, 6)
        painter.restore()


class GlyphGrid(QWidget):
    """The grid container: a block selector + search + the clickable cell list."""

    glyphChosen = Signal(int)  # emits the codepoint

    def __init__(self, strokefont: StrokeFont, reflib: ReferenceLibrary, parent=None) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QVBoxLayout, QComboBox, QLineEdit, QCheckBox, QHBoxLayout, QLabel

        self.strokefont = strokefont
        self.reflib = reflib
        self._model = GlyphGridModel(strokefont, reflib, self)

        self._list = QListView(self)
        self._list.setModel(self._model)
        self._list.setItemDelegate(GlyphGridDelegate(self._model, self._list))
        self._list.setViewMode(QListView.ViewMode.IconMode)
        self._list.setFlow(QListView.Flow.LeftToRight)
        self._list.setWrapping(True)
        self._list.setResizeMode(QListView.ResizeMode.Adjust)
        self._list.setSpacing(4)
        self._list.setUniformItemSizes(True)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setMovement(QListView.Movement.Static)
        self._list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._list.setGridSize(QSize(CELL_W, CELL_H))
        self._list.setIconSize(QSize(PIX_W, PIX_H))

        self._block_combo = QComboBox()
        self._block_combo.addItems(block_names())

        self._search = QLineEdit()
        self._search.setPlaceholderText("Find codepoint (hex or char)…")
        self._search.setClearButtonEnabled(True)

        self._show_all = QCheckBox("Show unassigned")
        self._show_all.setToolTip("Show every codepoint in the block, not only authored/reference ones.")

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Block:"))
        bar.addWidget(self._block_combo, 1)
        bar.addWidget(self._search, 2)
        bar.addWidget(self._show_all)

        self._count_label = QLabel("0 glyphs")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addLayout(bar)
        lay.addWidget(self._list, 1)
        lay.addWidget(self._count_label)

        self._block_combo.currentIndexChanged.connect(self._refresh)
        self._search.textChanged.connect(self._refresh)
        self._show_all.toggled.connect(self._refresh)
        self._list.clicked.connect(self._on_clicked)
        self._list.doubleClicked.connect(self._on_clicked)

        self._compute_and_refresh()

    def refresh(self) -> None:
        self._model.invalidate_previews()
        self._refresh()

    def invalidate_previews(self) -> None:
        """Repaint only (do not recompute which codepoints are shown)."""
        self._model.invalidate_previews()

    def invalidate_preview(self, cp: int) -> None:
        """Repaint only the single cell for ``cp``."""
        self._model.invalidate_preview(cp)

    def _on_clicked(self, index) -> None:
        cp = self._model.codepoint_at(index.row())
        self.glyphChosen.emit(cp)

    def codepoints(self) -> List[int]:
        """The currently shown codepoints, in list order."""
        return self._model.codepoints()

    def select_codepoint(self, cp: int) -> None:
        """Highlight + scroll to ``cp`` in the grid (if it is shown)."""
        cps = self._model.codepoints()
        try:
            row = cps.index(cp)
        except ValueError:
            return
        idx = self._model.index(row, 0)
        self._list.setCurrentIndex(idx)
        self._list.scrollTo(idx, QAbstractItemView.ScrollHint.PositionAtCenter)

    def select_block(self, name: str) -> None:
        i = self._block_combo.findText(name)
        if i >= 0:
            self._block_combo.setCurrentIndex(i)

    def _refresh(self) -> None:
        self._compute_and_refresh()

    def _compute_and_refresh(self) -> None:
        start, end = block_range(self._block_combo.currentText())
        cps = self._compute_candidate_list(start, end)
        self._model.set_codepoints(cps)
        self._count_label.setText(f"{len(cps)} glyphs")

    def _compute_candidate_list(self, start: int, end: int) -> List[int]:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QCursor
        show_all = self._show_all.isChecked()
        q = self._search.text().strip()
        cps: List[int] = []
        app = QApplication.instance()
        if app is not None:
            app.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            for cp in range(start, end + 1):
                if 0xD800 <= cp <= 0xDFFF:
                    continue
                if q:
                    # search filter
                    if not self._matches(cp, q):
                        continue
                if self.strokefont.has(cp):
                    cps.append(cp)
                elif show_all:
                    cps.append(cp)
                elif self.reflib.has(cp):
                    cps.append(cp)
        finally:
            if app is not None:
                app.restoreOverrideCursor()
        return cps

    def _matches(self, cp: int, q: str) -> bool:
        q = q.lower()
        if q.startswith("u+") or (q and all(c in "0123456789abcdefx" for c in q)):
            try:
                return cp == int(q.replace("u+", ""), 16)
            except ValueError:
                return False
        # character match
        return (q and chr(cp).lower() == q) or f"u+{cp:04x}".startswith(q)


# --- block table bridge -------------------------------------------------------
from ..unicode_blocks import block_ranges as _ranges  # noqa: E402

_BLOCK_NAMES = [name for name, _s, _e in _ranges()]


def block_names() -> List[str]:
    return list(_BLOCK_NAMES)


def block_range(name: str) -> tuple:
    for n, s, e in _ranges():
        if n == name:
            return s, e
    return 0, 0x7F
