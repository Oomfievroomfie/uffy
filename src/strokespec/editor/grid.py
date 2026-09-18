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
    QPointF,
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
from .perflog import count, note, set_context, span
from .uiutil import pil_to_qpixmap, paint_stroke_glyph

CP_ROLE = Qt.ItemDataRole.UserRole
CELL_W, CELL_H = 76, 88
PIX_W, PIX_H = 60, 60
BADGE_W = 18.0          # logical size of the native-text badge in the cell's top-left corner
BADGE_BG = QColor(255, 255, 255, 210)
BADGE_FG = QColor(60, 60, 72)


def _badge_scale() -> float:
    """Device pixel ratio the badge bitmaps are rasterised at (so they stay crisp when scaled)."""
    app = QApplication.instance()
    screen = app.primaryScreen() if app is not None else None
    try:
        s = float(screen.devicePixelRatio()) if screen is not None else 1.0
    except Exception:
        s = 1.0
    return s if s > 0.0 else 1.0


def render_badge_pixmap(cp: int) -> Optional[QPixmap]:
    """The cell's native-text badge (white rounded box + the character), as a bitmap.

    The badge is static, so it is rasterised once, exactly like the preview.
    """
    if not _is_printable(cp):
        return None
    s = _badge_scale()
    px = max(1, int(round(BADGE_W * s)))
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(px / BADGE_W)   # drawn at exactly BADGE_W logical pixels
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    rect = QRectF(0.0, 0.0, BADGE_W, BADGE_W)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(BADGE_BG)
    painter.drawRoundedRect(rect, 4.0, 4.0)
    f = QFont()
    f.setFamilies(native_text_families(cp))
    f.setPixelSize(13)
    painter.setFont(f)
    painter.setPen(BADGE_FG)
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, chr(cp))
    painter.end()
    return pm


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
        self._badge_cache: Dict[int, Optional[QPixmap]] = {}

    def set_codepoints(self, cps: List[int]) -> None:
        """Show a different block's codepoints. The caches are keyed by codepoint and depend on
        nothing about the block, so they survive the switch: re-building them here made every
        block change re-pay every badge (including Qt's unresolved-codepoint searches) and every
        reference preview."""
        self.beginResetModel()
        self._cps = list(cps)
        self.endResetModel()

    def codepoints(self) -> List[int]:
        return list(self._cps)

    def invalidate_previews(self) -> None:
        """Drop every cached preview and tell the view every row changed (a full repaint)."""
        count("model.invalidate_all")
        count("model.dataChanged_rows", len(self._cps))
        with span("model.invalidate_previews"):
            self._pixmap_cache.clear()
            if self._cps:
                top = self.index(0, 0)
                bottom = self.index(len(self._cps) - 1, 0)
                self.dataChanged.emit(top, bottom, [])

    def invalidate_preview(self, cp: int) -> None:
        """Repaint only the one cell for ``cp`` (do NOT touch the rest of the grid)."""
        count("model.invalidate_one")
        with span("model.invalidate_preview"):
            self._pixmap_cache.pop(cp, None)
            try:
                row = self._cps.index(cp)
            except ValueError:
                return
            idx = self.index(row, 0)
            self.dataChanged.emit(idx, idx, [])

    def rowCount(self, parent=QModelIndex()) -> int:
        count("model.rowCount")
        return 0 if parent.isValid() else len(self._cps)

    def codepoint_at(self, row: int) -> int:
        return self._cps[row]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        count("model.data")
        if role == Qt.ItemDataRole.DisplayRole:
            count("model.data.display")
        elif role == Qt.ItemDataRole.ToolTipRole:
            count("model.data.tooltip")
        elif role == Qt.ItemDataRole.SizeHintRole:
            count("model.data.sizehint")
        elif role != CP_ROLE:
            count("model.data.other")
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
            count("preview.hit")
            return self._pixmap_cache[cp]
        count("preview.miss")
        with span("model.preview_pixmap"):
            px = self._render_preview(cp)
            # keep cache bounded to avoid unbounded growth on huge blocks
            if len(self._pixmap_cache) > 4000:
                count("model.pixcache_flush")
                self._pixmap_cache.clear()
            self._pixmap_cache[cp] = px
        return px

    # --- badge caching --------------------------------------------------------
    def badge_pixmap(self, cp: int) -> Optional[QPixmap]:
        """The cell's badge bitmap, rendered once per codepoint (see render_badge_pixmap)."""
        if cp in self._badge_cache:
            count("badge.hit")
            return self._badge_cache[cp]
        count("badge.miss")
        with span("model.badge_pixmap"):
            pm = render_badge_pixmap(cp)
            # bounded like the preview cache; never cleared by invalidate_previews(), because a
            # badge does not depend on the authored strokes at all.
            if len(self._badge_cache) > 4000:
                count("model.badgecache_flush")
                self._badge_cache.clear()
            self._badge_cache[cp] = pm
        return pm

    def _render_preview(self, cp: int) -> Optional[QPixmap]:
        glyph = self.strokefont.get(cp)
        if glyph is not None and (glyph.strokes or glyph.subcomponents):
            count("preview.authored")
            with span("model.preview_strokes"):
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
        with span("model.preview_lookup"):
            has = self.reflib.has(cp)
        if has:
            count("preview.reference")
            with span("model.reference_render"):
                img = self.reflib.render_first(cp, box_px=PIX_W, pixel_size=MAX_REF_PX)
            if img is not None:
                with span("model.reference_convert"):
                    return pil_to_qpixmap(_tint_grey(img))  # fallback ghost: grey, not black
        else:
            count("preview.blank")
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
        count("delegate.paint")
        with span("delegate.paint"):
            self._paint(painter, option, index)

    def _paint(self, painter: QPainter, option, index) -> None:
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
            with span("delegate.preview_scale"):
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
        # It is a cached bitmap, not a live QFont + drawText: a QFont carrying a family chain
        # makes the platform re-resolve (and, for a codepoint no installed font covers, re-run
        # its fallback search) on every frame, for a badge that never changes.
        if _is_printable(cp):
            count("delegate.badge")
            with span("delegate.badge"):
                bpm = self._model.badge_pixmap(cp)
                if bpm is not None:
                    painter.drawPixmap(QPointF(rect.left() + 2.0, rect.top() + 2.0), bpm)

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


class _TimedListView(QListView):
    """The pane's QListView, with its own work timed for the perf log.

    The view's cost is not in any Python span: `paintEvent` drives every `delegate.paint`,
    `doItemsLayout` is where a wrapping icon-mode view re-lays out rows (calling back into the
    model once per row), and `scrollContentsBy` moves it. Timing those three separates "the list
    is slow" from "the delegate is slow" from "Qt re-laid the whole list out".
    """

    def paintEvent(self, event) -> None:
        with span("view.paint"):
            super().paintEvent(event)

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        with span("view.scroll"):
            super().scrollContentsBy(dx, dy)

    def resizeEvent(self, event) -> None:
        with span("view.resize"):
            super().resizeEvent(event)

    def doItemsLayout(self) -> None:
        count("view.layout")
        with span("view.doItemsLayout"):
            super().doItemsLayout()

    def updateGeometries(self) -> None:
        with span("view.updateGeometries"):
            super().updateGeometries()


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
        self._list = _TimedListView(self)
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

        self._block_combo.currentIndexChanged.connect(self.relist)
        self._list.clicked.connect(self._on_clicked)
        self._list.doubleClicked.connect(self._on_clicked)
        self.refresh()

    # --- public API used by the parent GlyphGrid ------------------------------
    def refresh(self) -> None:
        """The authored data changed: drop every cached preview, then rebuild the list."""
        with span("pane.refresh"):
            self._model.invalidate_previews()
            self.relist()

    def relist(self) -> None:
        """Re-filter and re-show this pane's block, keeping every cached bitmap.

        Switching block (or typing in the search box, or toggling 'show unassigned') changes which
        codepoints are *listed*, not what any of them looks like: previews and badges are keyed by
        codepoint and depend on the strokes and the fonts, not on the block. Wiping them here made
        every block open re-render all ~88 visible previews (30-47 ms) and badges (17-25 ms) on
        top of the ~13 ms paint, on every open and every reopen.
        """
        with span("pane.recompute"):
            self._recompute()

    def perf_snapshot(self) -> dict:
        """State for the perf log: anything here that grows over time is a leak."""
        from PySide6.QtGui import QFontDatabase
        return {
            "block": self._block_combo.currentText(),
            "rows": self._model.rowCount(),
            "pixcache": len(self._model._pixmap_cache),
            "qt_fonts": len(QFontDatabase.families()),
            "visible": self._list.viewport().height() // max(1, CELL_H),
            "pane": hex(id(self)),
        }

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
        with span("pane.select_codepoint"):
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
        count("pane.cell_clicked")
        note("cell clicked")
        self.glyphChosen.emit(self._model.codepoint_at(index.row()))

    def _current_codepoint(self) -> Optional[int]:
        idx = self._list.currentIndex()
        if idx.isValid():
            return self._model.codepoint_at(idx.row())
        return None

    def _recompute(self) -> None:
        with span("pane.recompute"):
            selected = self._current_codepoint()
            name = self._block_combo.currentText()
            start, end = block_range(name)
            with span("pane.candidate_list"):
                cps = self._compute_candidate_list(start, end)
            count("pane.rows_set", len(cps))
            with span("pane.set_codepoints"):
                self._model.set_codepoints(cps)
            with span("pane.block_coverage"):
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
            note(f"block '{name}': {len(cps)} rows")

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
        # The panes are the codepoint list, so their state is what the perf log reports
        # alongside the timings (see perflog.py — always on, writes perf.log).
        set_context(self.perf_snapshot)

        self._search.textChanged.connect(self._refresh_all)
        self._show_all.toggled.connect(self._refresh_all)
        self._second_block_btn.toggled.connect(self._toggle_second)

    def _add_pane(self, primary: bool) -> BlockPane:
        pane = BlockPane(self.strokefont, self.reflib, self._search, self._show_all, self)
        note(f"pane created ({len(self._panes) + 1} live)")
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
            pane.relist()

    # --- public API routed to the active pane (used by the main window) --------
    def refresh(self) -> None:
        with span("grid.refresh"):
            # The authored data changed (a glyph was stored, removed, or the file was reloaded):
            # every preview is stale. Merely re-listing a block does not come through here.
            self.invalidate_previews()
            self._refresh_all()

    def perf_snapshot(self) -> dict:
        """Pane count plus the active pane's own snapshot (the perf log reads this)."""
        d = {"panes": len(self._panes)}
        if self._active_pane is not None:
            d.update(self._active_pane.perf_snapshot())
        return d

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
