"""Main window: interactive grid on the left, glyph editor on the right."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QCloseEvent,
    QIcon,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..model import SHAPE_ARC, SHAPE_LINE, Glyph, StrokeFont
from ..refbrowser import ReferenceLibrary
from .glyph_canvas import GlyphCanvas
from .grid import GlyphGrid
from .uiutil import pil_to_qpixmap


_ICON_HEX = QColor(60, 60, 72)


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


class ReferenceFontsDock(QWidget):
    """List of loaded reference fonts with add/clear controls."""

    def __init__(self, reflib: ReferenceLibrary, on_change, parent=None) -> None:
        super().__init__(parent)
        self._reflib = reflib
        self._on_change = on_change
        lay = QVBoxLayout(self)
        self._list = QListWidget()
        lay.addWidget(self._list)
        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add Folder…")
        clear_btn = QPushButton("Clear")
        btn_row.addWidget(add_btn)
        btn_row.addWidget(clear_btn)
        lay.addLayout(btn_row)
        add_btn.clicked.connect(self.add_folder)
        clear_btn.clicked.connect(self.clear)
        self._refresh()

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
        top.addStretch(1)
        top.addWidget(QLabel("Width:"))
        self._width_combo = QComboBox()
        self._width_combo.addItem("16 (full)", 16)
        self._width_combo.addItem("8 (half)", 8)
        top.addWidget(self._width_combo)
        self._combining = QCheckBox("Combining")
        top.addWidget(self._combining)
        self._clear_btn = QPushButton("Clear")
        top.addWidget(self._clear_btn)

        self.canvas = GlyphCanvas()

        self._stroke_list = QListWidget()
        self._stroke_list.setMinimumWidth(190)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        tool_row = QHBoxLayout()
        self._btn_line = QToolButton(); self._btn_line.setText("Line")
        self._btn_arc = QToolButton(); self._btn_arc.setText("Arc")
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

        # single-row tool controls: copy / paste / flip + the 1-pixel nudge arrows (icons only)
        nudge_row = QHBoxLayout()
        nudge_row.setSpacing(2)
        nudge_row.setContentsMargins(0, 0, 0, 0)
        b_copy = QToolButton(); b_copy.setIcon(_icon_copy()); b_copy.setFixedSize(22, 22)
        b_paste = QToolButton(); b_paste.setIcon(_icon_paste()); b_paste.setFixedSize(22, 22)
        b_flip = QToolButton(); b_flip.setIcon(_icon_flip()); b_flip.setFixedSize(22, 22)
        b_n = QToolButton(); b_n.setArrowType(Qt.ArrowType.UpArrow); b_n.setFixedSize(20, 20)
        b_s = QToolButton(); b_s.setArrowType(Qt.ArrowType.DownArrow); b_s.setFixedSize(20, 20)
        b_w = QToolButton(); b_w.setArrowType(Qt.ArrowType.LeftArrow); b_w.setFixedSize(20, 20)
        b_e = QToolButton(); b_e.setArrowType(Qt.ArrowType.RightArrow); b_e.setFixedSize(20, 20)
        for w in (b_copy, b_paste, b_flip):
            nudge_row.addWidget(w)
        nudge_row.addSpacing(6)
        for w in (b_n, b_s, b_w, b_e):
            nudge_row.addWidget(w)
        nudge_row.addStretch(1)
        col.addLayout(nudge_row)

        btn_delete = QPushButton("Delete selected")
        btn_reverse = QPushButton("Reverse points (flip arc)")
        btn_toggle = QPushButton("Toggle line/arc")
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
        btn_delete.clicked.connect(self.canvas.delete_selected)
        btn_reverse.clicked.connect(self.canvas.reverse_selected)
        btn_toggle.clicked.connect(self.canvas.toggle_selected_shape)
        self._width_combo.currentIndexChanged.connect(self._on_width_changed)
        self._combining.toggled.connect(self._on_combining_changed)
        self._clear_btn.clicked.connect(self._on_clear)
        self._btn_line.clicked.connect(lambda: self._set_tool(SHAPE_LINE))
        self._btn_arc.clicked.connect(lambda: self._set_tool(SHAPE_ARC))
        self._show_ghost.toggled.connect(self._on_ghost_toggled)
        b_copy.clicked.connect(self.canvas.copy_strokes)
        b_paste.clicked.connect(self.canvas.paste_strokes)
        b_flip.clicked.connect(self.canvas.flip_horizontal)
        b_n.clicked.connect(lambda: self.canvas.nudge(0, 1))
        b_s.clicked.connect(lambda: self.canvas.nudge(0, -1))
        b_w.clicked.connect(lambda: self.canvas.nudge(-1, 0))
        b_e.clicked.connect(lambda: self.canvas.nudge(1, 0))
        self.canvas.glyphChanged.connect(self._on_canvas_changed)

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
        self._sync_stroke_list()

    def glyph(self) -> Optional[Glyph]:
        return self._glyph

    def refresh_canvas(self) -> None:
        self.canvas.update()
        self._sync_stroke_list()

    def set_reference_provider(self, provider) -> None:
        self.canvas.reference_provider = provider
        self.canvas.update()

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
                self._stroke_list.addItem(
                    f"{i}: {kind} ({s.p1.x},{s.p1.y})→({s.p2.x},{s.p2.y})"
                )
            if 0 <= self.canvas._selected_index < len(self._glyph.strokes):
                self._stroke_list.setCurrentRow(self.canvas._selected_index)
        self._stroke_list.blockSignals(False)


class MainWindow(QMainWindow):
    def __init__(self, path: Optional[str] = None) -> None:
        super().__init__()
        self.setWindowTitle("strokespec — stroke fallback font editor")
        self.resize(1280, 780)

        self.strokefont = StrokeFont()
        self.reflib = ReferenceLibrary()
        self._path: Optional[str] = path
        self._dirty = False
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
        dock = QDockWidget("Reference Fonts", self)
        dock.setWidget(self._refdock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

        self._build_menu()
        self._grid.glyphChosen.connect(self._open_codepoint)
        self._editor.glyphChanged.connect(self._on_glyph_changed)
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
            ("&New", self._new, "Ctrl+N"),
            ("&Open…", self._open, "Ctrl+O"),
            ("&Save", self._save, "Ctrl+S"),
            ("Save &As…", self._save_as, "Ctrl+Shift+S"),
            ("&Compile TTF…", self._compile, "Ctrl+T"),
            ("&Quit", self.close, "Ctrl+Q"),
        ]:
            act = QAction(text, self)
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
        f = self.reflib.first_with(cp)
        if f is None:
            return None
        res = f.glyph_bitmap(cp, 160)
        if res is None:
            return None
        img, baseline_px, cap_px = res
        return (pil_to_qpixmap(img), baseline_px, cap_px)

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

    def _on_glyph_changed(self) -> None:
        changed_in_place = False
        if self._pending_commit is not None:
            glyph = self._pending_commit
            if glyph.strokes or glyph.combining or glyph.width != 16:
                stored = self.strokefont.ensure(glyph.codepoint)
                stored.strokes[:] = glyph.strokes
                stored.width = glyph.width
                stored.combining = glyph.combining
                stored.name = glyph.name
                self._pending_commit = None
                # switch the editor onto the stored glyph so further edits apply directly
                self._editor.set_glyph(stored)
                self._grid.refresh()  # a brand-new glyph may appear in this block
                self._dirty = True
                self._set_window_title()
            return  # opening a not-yet-edited glyph is not a change
        # editing an existing glyph in place: repaint only the single cell, never the whole grid
        self._mark_dirty(invalidate_grid=False)
        self._grid.invalidate_preview(self._editor.glyph().codepoint)

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
        try:
            from ..compiler import compile_strokefont
            from PySide6.QtWidgets import QApplication
            self.statusBar().showMessage("Compiling with Google font tools…")
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                compile_strokefont(
                    self.strokefont,
                    path,
                    family_name=self.strokefont.metadata.get("name", "strokespec"),
                )
            finally:
                QApplication.restoreOverrideCursor()
            self.statusBar().showMessage(f"Wrote {path}")
            QMessageBox.information(self, "Compile", f"Wrote TTF:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Compile failed", str(e))
            self.statusBar().clearMessage()

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
