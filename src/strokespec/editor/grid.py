"""Interactive glyph grid: a FontForge-style, individually clickable cell container.

Each cell is its own selectable item in a ``QListView`` (icon mode). It shows:
  * the authored stroke glyph (if any), or
  * a reference-font preview (for codepoints with no stroke glyph yet), or
  * an empty slot.
Clicking a cell emits the codepoint so the main window opens it in the glyph editor.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import unicodedata2 as unicodedata

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
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QStyledItemDelegate,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..model import StrokeFont, Glyph, GRID_H
from ..refbrowser import ReferenceLibrary
from .fontfallback import native_text_families
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
        if glyph is not None and (glyph.strokes or glyph.subcomponents):
            pm = QPixmap(PIX_W, PIX_H)
            pm.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pm)
            paint_stroke_glyph(
                painter, glyph, QRectF(2, 2, PIX_W - 4, PIX_H - 4),
                color=QColor(25, 25, 25),
                baseline=self.strokefont.baseline,
                resolve=self.strokefont.get,
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

        # Small native-text character badge (top-left), drawn here rather than with a per-cell
        # QLabel: setIndexWidget invalidates the icon-mode item layout, and a wrapping QListView
        # lays out ALL of its rows whenever the layout is invalidated — so on a 40k-cell block
        # every scroll step re-laid out every row (~87k model callbacks, ~110ms per step).
        # Painting it costs one drawText on the cells that were actually exposed.
        ch = chr(cp) if _is_printable(cp) else ""
        if ch:
            badge = QRectF(rect.left() + 2.0, rect.top() + 2.0, 18.0, 18.0)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 210))
            painter.drawRoundedRect(badge, 4.0, 4.0)
            f = QFont()
            f.setFamilies(native_text_families(cp))
            f.setPixelSize(13)
            painter.setFont(f)
            painter.setPen(QColor(60, 60, 72))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, ch)

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


class BlockPane(QWidget):
    """One independent grid view: a block selector + its own clickable cell list.

    A pane owns its own block combo, model, list, badges and per-block scroll memory, so two
    panes can show two different blocks at the same time. A shared search box and 'Show
    unassigned' checkbox (owned by the parent GlyphGrid) filter every pane; clicking a cell emits
    ``glyphChosen`` so the single editor opens that codepoint.
    """

    glyphChosen = Signal(int)  # emits the codepoint

    def __init__(self, strokefont: StrokeFont, reflib: ReferenceLibrary,
                 search: QLineEdit, show_all: QCheckBox, parent=None) -> None:
        super().__init__(parent)
        self.strokefont = strokefont
        self.reflib = reflib
        self._search = search
        self._show_all = show_all

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
        self._scroll_memory: dict = {}
        self._prev_block: Optional[str] = None

        self._block_combo = QComboBox()
        self._block_combo.addItems(block_names())

        self._count_label = QLabel("0 glyphs")
        header = QHBoxLayout()
        header.addWidget(QLabel("Block:"))
        header.addWidget(self._block_combo, 1)
        header.addWidget(self._count_label)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(header)
        lay.addWidget(self._list, 1)

        self._block_combo.currentIndexChanged.connect(self.refresh)
        self._list.clicked.connect(self._on_clicked)
        self._list.doubleClicked.connect(self._on_clicked)
        self.refresh()

    # --- public API used by the parent GlyphGrid ------------------------------
    def refresh(self) -> None:
        self._model.invalidate_previews()
        self._recompute()

    def invalidate_previews(self) -> None:
        self._model.invalidate_previews()

    def invalidate_preview(self, cp: int) -> None:
        self._model.invalidate_preview(cp)

    def codepoints(self) -> List[int]:
        return self._model.codepoints()

    def column_count(self) -> int:
        cell = self._list.gridSize().width()
        vw = self._list.viewport().width()
        return max(1, (vw if vw else cell) // max(1, cell))

    def select_codepoint(self, cp: int, scroll: bool = True) -> None:
        cps = self._model.codepoints()
        try:
            row = cps.index(cp)
        except ValueError:
            return
        idx = self._model.index(row, 0)
        self._list.setCurrentIndex(idx)
        if scroll:
            self._list.scrollTo(idx, QAbstractItemView.ScrollHint.PositionAtCenter)

    def select_block(self, name: str) -> None:
        i = self._block_combo.findText(name)
        if i >= 0:
            self._block_combo.setCurrentIndex(i)

    def block_name(self) -> str:
        return self._block_combo.currentText()

    # --- internal ------------------------------------------------------------
    def _on_clicked(self, index) -> None:
        self.glyphChosen.emit(self._model.codepoint_at(index.row()))

    def _current_codepoint(self) -> Optional[int]:
        idx = self._list.currentIndex()
        if idx.isValid():
            return self._model.codepoint_at(idx.row())
        return None

    def _recompute(self) -> None:
        selected = self._current_codepoint()
        name = self._block_combo.currentText()
        start, end = block_range(name)
        cps = self._compute_candidate_list(start, end)
        self._model.set_codepoints(cps)
        covered, allocated = self._block_coverage(start, end)
        pct = (100.0 * covered / allocated) if allocated else 0.0
        self._count_label.setText(f"{len(cps)} glyphs · {pct:.0f}% of {allocated} allocated")
        if selected is not None and selected in cps:
            self.select_codepoint(selected, scroll=False)
        else:
            saved = self._scroll_memory.get(name)
            if saved is not None:
                self._list.verticalScrollBar().setValue(saved)
        self._prev_block = name

    def _block_coverage(self, start: int, end: int) -> tuple:
        allocated = covered = 0
        for cp in range(start, end + 1):
            if 0xD800 <= cp <= 0xDFFF:
                continue
            if not unicodedata.name(chr(cp), ""):
                continue
            allocated += 1
            if self.strokefont.has(cp):
                covered += 1
        return covered, allocated

    def _compute_candidate_list(self, start: int, end: int) -> List[int]:
        from PySide6.QtGui import QCursor
        unassigned_only = self._show_all.isChecked()
        q = self._search.text().strip()
        cps: List[int] = []
        app = QApplication.instance()
        if app is not None:
            app.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            for cp in range(start, end + 1):
                if 0xD800 <= cp <= 0xDFFF:
                    continue
                if q and not self._matches(cp, q):
                    continue
                if unassigned_only:
                    if not self.strokefont.has(cp):
                        cps.append(cp)
                else:
                    cps.append(cp)
        finally:
            if app is not None:
                app.restoreOverrideCursor()
        return cps

    @staticmethod
    def _matches(cp: int, q: str) -> bool:
        # Split the query on whitespace and require EVERY subterm to appear somewhere (as a
        # case-insensitive substring), in any order and not necessarily consecutively — i.e. an
        # AND across the whitespace-separated terms, e.g. "latin small a".
        terms = q.lower().split()
        if not terms:
            return True
        ch = chr(cp)
        name = unicodedata.name(ch, "")
        hay = " ".join(filter(None, [
            name.lower(), ch.lower(),
            f"u+{cp:04x}", f"{cp:x}", f"{cp:04x}",
        ]))
        return all(t in hay for t in terms)


class GlyphGrid(QWidget):
    """The left panel: a toolbar + one or two BlockPanes (grid views) shown at once.

    A single editor is shared: clicking a cell in any pane opens that codepoint in the one glyph
    editor. The '2nd' toggle reveals a second BlockPane so two blocks are visible simultaneously.
    Search and 'Show unassigned' are shared across all panes; per-block scroll memory lives per
    pane.
    """

    glyphChosen = Signal(int)  # emits the codepoint

    def __init__(self, strokefont: StrokeFont, reflib: ReferenceLibrary, parent=None) -> None:
        super().__init__(parent)
        self.strokefont = strokefont
        self.reflib = reflib

        self._search = QLineEdit()
        self._search.setPlaceholderText("Find codepoint (hex or char)…")
        self._search.setClearButtonEnabled(True)

        self._show_all = QCheckBox("Show unassigned")
        self._show_all.setToolTip(
            "Checked: show ONLY codepoints not yet authored in the stroke set (a reference "
            "render does not count as assigned). Unchecked: no filtering — show every codepoint."
        )

        self._second_block_btn = QToolButton()
        self._second_block_btn.setText("2nd block")
        self._second_block_btn.setCheckable(True)
        self._second_block_btn.setToolTip("Show a second block alongside the first")

        topbar = QHBoxLayout()
        topbar.addWidget(self._search, 1)
        topbar.addWidget(self._show_all)
        topbar.addWidget(self._second_block_btn)

        self._panes: List[BlockPane] = []
        self._pane_lay = QVBoxLayout()
        self._pane_lay.setSpacing(10)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addLayout(topbar)
        lay.addLayout(self._pane_lay)

        self._active_pane: Optional[BlockPane] = None
        self._add_pane(primary=True)

        self._search.textChanged.connect(self._refresh_all)
        self._show_all.toggled.connect(self._refresh_all)
        self._second_block_btn.toggled.connect(self._toggle_second)

    def _add_pane(self, primary: bool) -> BlockPane:
        pane = BlockPane(self.strokefont, self.reflib, self._search, self._show_all, self)
        pane.glyphChosen.connect(self._on_pane_chosen)
        self._pane_lay.addWidget(pane, 1)
        self._panes.append(pane)
        if self._active_pane is None:
            self._active_pane = pane
        if not primary and len(self._panes) >= 2:
            # default the second pane to the block right after the first, so the two panes don't
            # start on the same block.
            names = block_names()
            i = names.index(self._panes[0].block_name()) if self._panes[0].block_name() in names else 0
            self._panes[1].select_block(names[(i + 1) % len(names)])
        return pane

    def _on_pane_chosen(self, cp: int) -> None:
        sender = self.sender()
        if isinstance(sender, BlockPane):
            self._active_pane = sender
        self.glyphChosen.emit(cp)

    def _toggle_second(self, on: bool) -> None:
        if on and len(self._panes) < 2:
            self._add_pane(primary=False)
            self._refresh_all()
        elif not on and len(self._panes) == 2:
            second = self._panes.pop()
            self._pane_lay.removeWidget(second)
            second.deleteLater()
            if self._active_pane is second:
                self._active_pane = self._panes[0] if self._panes else None
            self._refresh_all()

    def _refresh_all(self) -> None:
        for pane in self._panes:
            pane.refresh()

    # --- public API routed to the active pane (used by the main window) --------
    def refresh(self) -> None:
        self._refresh_all()

    def invalidate_previews(self) -> None:
        for pane in self._panes:
            pane.invalidate_previews()

    def invalidate_preview(self, cp: int) -> None:
        for pane in self._panes:
            pane.invalidate_preview(cp)

    def codepoints(self) -> List[int]:
        return self._active_pane.codepoints() if self._active_pane else []

    def column_count(self) -> int:
        return self._active_pane.column_count() if self._active_pane else 1

    def select_codepoint(self, cp: int, scroll: bool = True) -> None:
        if self._active_pane is not None:
            self._active_pane.select_codepoint(cp, scroll)

    def select_block(self, name: str) -> None:
        # Open-block navigation targets the primary pane.
        if self._panes:
            self._panes[0].select_block(name)

    def current_panes(self) -> List[BlockPane]:
        return list(self._panes)


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
