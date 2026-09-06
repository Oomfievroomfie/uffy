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
    QEvent,
    QModelIndex,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QListView,
    QStyledItemDelegate,
    QStyle,
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

        # Small plaintext character is supplied by a NATIVE QLabel per cell (setIndexWidget in
        # _populate_badges), so it is NOT drawn here via drawText.
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
        # track which rows currently have a native badge QLabel (only the visible ones)
        self._badge_rows: set = set()
        self._list.verticalScrollBar().valueChanged.connect(self._update_visible_badges)
        # A window resize that grows the viewport reveals rows that were off-screen before;
        # scroll's valueChanged does NOT fire for that, so run the lazy badge update on resize too.
        self._list.viewport().installEventFilter(self)
        # Per-block vertical-scroll memory, keyed by block name. Switching to another block and
        # back restores this block's last scroll offset. ``_prev_block`` is the block the list is
        # still showing (captured before the model reset discards the old content).
        self._scroll_memory: dict = {}
        self._prev_block: Optional[str] = None

        self._block_combo = QComboBox()
        self._block_combo.addItems(block_names())

        self._search = QLineEdit()
        self._search.setPlaceholderText("Find codepoint (hex or char)…")
        self._search.setClearButtonEnabled(True)

        self._show_all = QCheckBox("Show unassigned")
        self._show_all.setToolTip(
            "Checked: show ONLY codepoints not yet authored in the stroke set (a reference "
            "render does not count as assigned). Unchecked: no filtering — show every codepoint."
        )

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

    def column_count(self) -> int:
        """How many cells fit across the grid (used for up/down navigation)."""
        cell = self._list.gridSize().width()
        vw = self._list.viewport().width()
        return max(1, (vw if vw else cell) // max(1, cell))

    def select_codepoint(self, cp: int, scroll: bool = True) -> None:
        """Highlight ``cp`` in the grid (if it is shown).

        ``scroll`` centres the item on screen — used for explicit navigation so the view follows
        the cursor. Callers that must NOT move the view (e.g. restoring the selection after an
        edit that fired a refresh) pass ``scroll=False`` so the list only moves when the user
        actually scrolls it.
        """
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

    def _refresh(self) -> None:
        self._compute_and_refresh()

    def _current_codepoint(self) -> Optional[int]:
        """The codepoint currently selected in the grid, or ``None``."""
        idx = self._list.currentIndex()
        if idx.isValid():
            return self._model.codepoint_at(idx.row())
        return None

    def _compute_and_refresh(self) -> None:
        # Remember the selected card and restore it after the model reset, so refreshing (e.g.
        # toggling 'Show unassigned', or a grid.refresh() from committing a glyph) does not
        # make the list forget which card is selected.
        selected = self._current_codepoint()
        start, end = block_range(self._block_combo.currentText())
        # Save the block the list is *still showing* (self._prev_block) before the model reset
        # below discards its scroll offset, so a later switch back can restore it.
        if self._prev_block is not None:
            self._scroll_memory[self._prev_block] = self._list.verticalScrollBar().value()
        cps = self._compute_candidate_list(start, end)
        self._model.set_codepoints(cps)
        covered, allocated = self._block_coverage(start, end)
        pct = (100.0 * covered / allocated) if allocated else 0.0
        self._count_label.setText(f"{len(cps)} glyphs · {pct:.0f}% of allocated covered")
        self._clear_badges()
        self._update_visible_badges()
        # Badge creation needs the view to be laid out (indexAt must find real cells). It runs
        # once here, but short blocks (e.g. Ogham, 32 items) fit in the viewport and never
        # scroll, so valueChanged never re-triggers it. Schedule a re-run for after the event
        # loop lays the view out, so those blocks get their native-text badges too.
        QTimer.singleShot(0, self._update_visible_badges)
        current = self._block_combo.currentText()
        if selected is not None and selected in cps:
            # Restore the highlight WITHOUT re-centring: opening/editing a codepoint must not
            # make the list jump (the user only wants it to move when they scroll it).
            self.select_codepoint(selected, scroll=False)
        else:
            # Restore this block's own remembered scroll offset (covers block switches where
            # the previously selected card is not in the new block).
            saved = self._scroll_memory.get(current)
            if saved is not None:
                self._list.verticalScrollBar().setValue(saved)
            self._update_visible_badges()
        self._prev_block = current

    def _clear_badges(self) -> None:
        for row in list(self._badge_rows):
            self._list.setIndexWidget(self._model.index(row, 0), None)
        self._badge_rows.clear()

    def eventFilter(self, obj, event) -> bool:
        # Recreate badges lazily when the viewport is resized (e.g. window grown), so rows that
        # were off-screen and now visible get their native-text badge. Deferred so the new layout
        # is in place before indexAt is queried.
        if obj is self._list.viewport() and event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self._update_visible_badges)
        return super().eventFilter(obj, event)

    def _update_visible_badges(self) -> None:
        """Give every *visible* cell a native text label (QLabel) for the character badge.

        Only the cells currently in the viewport get a widget (created lazily, refreshed on
        scroll), so huge blocks (e.g. CJK Unified Ideographs) load fast instead of creating
        thousands of upfront widgets.
        """
        from PySide6.QtWidgets import QLabel, QWidget
        from PySide6.QtCore import QPoint, QSize
        vp = self._list.viewport()
        if not vp.rect().isValid():
            return
        first = self._list.indexAt(QPoint(vp.rect().left() + 2, vp.rect().top() + 2))
        last = self._list.indexAt(QPoint(vp.rect().left() + 2, vp.rect().bottom() - 2))
        if not first.isValid():
            return
        if not last.isValid():
            # The bottom of the viewport is empty space (a short block, e.g. Ogham's 32 items,
            # leaves a gap below the last row, so indexAt at the bottom returns nothing). Find the
            # actual last visible row by walking down until an item's rect leaves the viewport.
            # This only runs when the bottom corner is empty — long blocks have a valid `last`,
            # so the lazy per-visible-row attachment is unchanged and there is no perf regression.
            vp_h = vp.rect().height()
            last = first
            for r in range(first.row(), self._model.rowCount()):
                idx = self._model.index(r, 0)
                vr = self._list.visualRect(idx)
                if not vr.isEmpty() and vr.top() < vp_h:
                    last = idx
                else:
                    break
        lo, hi = min(first.row(), last.row()), max(first.row(), last.row())
        # drop widgets for rows that scrolled out of view
        for row in list(self._badge_rows):
            if not (lo <= row <= hi):
                self._list.setIndexWidget(self._model.index(row, 0), None)
                self._badge_rows.discard(row)
        # create widgets for visible rows
        for row in range(lo, hi + 1):
            if row in self._badge_rows:
                continue
            cp = self._model.codepoint_at(row)
            ch = chr(cp) if _is_printable(cp) else ""
            # transparent container matches the cell; the badge is a fixed 18x18 label at the
            # same (2,2) inset as before, so the view resizing the container can never grow it.
            container = QWidget()
            container.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            container.setAutoFillBackground(False)
            lbl = QLabel(container)
            lbl.setGeometry(2, 2, 18, 18)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            # Native-text font, mirroring the big "native reference" panel (_ref_img): take the
            # application font (which carries the fallback families, incl. Unifont), set the
            # pixel size, re-apply, and set the text after. Do NOT set font-size via a stylesheet
            # (that replaces the font with a single-family one and drops the fallback chain, so
            # e.g. Ogham rendered as a blank box instead of via a fallback font).
            f = QFont()
            f.setFamilies(native_text_families(cp))
            f.setPixelSize(13)
            lbl.setFont(f)
            lbl.setText(ch)
            lbl.setStyleSheet("background:rgba(255,255,255,210); color:#3c3c48;")
            self._list.setIndexWidget(self._model.index(row, 0), container)
            self._badge_rows.add(row)

    def _block_coverage(self, start: int, end: int) -> tuple:
        """(covered, allocated) for the block's allocated codepoints.

        ``allocated`` = Unicode-assigned codepoints in [start, end]; ``covered`` = the allocated
        codepoints that actually have authored strokes in the stroke set.
        """
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
        from PySide6.QtWidgets import QApplication
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
                if q:
                    # search filter
                    if not self._matches(cp, q):
                        continue
                if unassigned_only:
                    # checked: show ONLY codepoints not authored in the stroke set (a reference
                    # render does not count as assigned)
                    if not self.strokefont.has(cp):
                        cps.append(cp)
                else:
                    # unchecked: no filtering — show every codepoint in the block
                    cps.append(cp)
        finally:
            if app is not None:
                app.restoreOverrideCursor()
        return cps

    def _matches(self, cp: int, q: str) -> bool:
        q = q.lower()
        if not q:
            return True
        ch = chr(cp)
        name = unicodedata.name(ch, "")
        # Plain case-insensitive substring search across the character name, the character
        # itself, and the codepoint's hex forms (e.g. "ed" finds names containing "turned"
        # and also U+00ED via its "00ed" hex; "4e2d" finds U+4E2D via its "u+4e2d").
        hay = " ".join(filter(None, [
            name.lower(), ch.lower(),
            f"u+{cp:04x}", f"{cp:x}", f"{cp:04x}",
        ]))
        return q in hay


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
