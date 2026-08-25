"""String-table picker: catalog of scanned ids with text preview."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QRect, Qt, QTimer
from PyQt6.QtGui import QColor, QFontMetrics, QPainter
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from .strings import (
    StringCatalogEntry,
    StringRootGroup,
    StringResolver,
    build_string_picker_root_groups,
    strip_engine_markup,
)

_ROLE_ID = int(Qt.ItemDataRole.UserRole)
_ROLE_TEXT = int(Qt.ItemDataRole.UserRole) + 1
_ROLE_PATH = int(Qt.ItemDataRole.UserRole) + 2
_HIGHLIGHT_MAX = 1000
_MATCH_BG = QColor("#5A4A20")
_MATCH_FG = QColor("#FFE08A")


class _MatchHighlightDelegate(QStyledItemDelegate):
    """Draw list rows with case-insensitive substring highlights."""

    def __init__(self, owner: StringPickerDialog) -> None:
        super().__init__(owner)
        self._owner = owner

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # noqa: N802
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text or ""
        needle = self._owner._highlight_needle
        opt.text = ""
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        assert style is not None
        style.drawControl(
            QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget
        )
        text_rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemText, opt, widget
        )
        if not text_rect.isValid():
            text_rect = opt.rect.adjusted(4, 0, -4, 0)
        if opt.state & QStyle.StateFlag.State_Selected:
            color = opt.palette.highlightedText().color()
        else:
            color = opt.palette.text().color()
        painter.save()
        painter.setFont(opt.font)
        _draw_highlighted_text(
            painter,
            text_rect,
            text,
            needle,
            color=color,
            match_bg=_MATCH_BG,
            match_fg=_MATCH_FG,
        )
        painter.restore()


def _draw_highlighted_text(
    painter: QPainter,
    rect: QRect,
    text: str,
    needle: str,
    *,
    color: QColor,
    match_bg: QColor,
    match_fg: QColor,
) -> None:
    fm = QFontMetrics(painter.font())
    flags = int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
    if not needle:
        painter.setPen(color)
        painter.drawText(rect, flags, text)
        return
    lower = text.lower()
    n = needle.lower()
    x = rect.x()
    y = rect.y()
    h = rect.height()
    max_x = rect.right()
    pos = 0
    while pos < len(text) and x <= max_x:
        idx = lower.find(n, pos)
        if idx < 0:
            chunk = text[pos:]
            painter.setPen(color)
            painter.drawText(QRect(x, y, max_x - x + 1, h), flags, chunk)
            break
        if idx > pos:
            chunk = text[pos:idx]
            w = fm.horizontalAdvance(chunk)
            painter.setPen(color)
            painter.drawText(QRect(x, y, w, h), flags, chunk)
            x += w
        match = text[idx : idx + len(needle)]
        w = fm.horizontalAdvance(match)
        painter.fillRect(QRect(x, y + 1, w, h - 2), match_bg)
        painter.setPen(match_fg)
        painter.drawText(QRect(x, y, w, h), flags, match)
        x += w
        pos = idx + len(needle)


class StringPickerDialog(QDialog):
    """Pick a string id from scanned text roots. Preview body shown by default."""

    def __init__(
        self,
        resolver: StringResolver,
        parent: QWidget | None = None,
        *,
        current_id: str = "",
        start_filter: str = "",
        anomaly_root: str = "",
        gamma_root: str = "",
        root_groups: list[StringRootGroup] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select text")
        self.setModal(True)
        self.resize(720, 520)
        self._resolver = resolver
        self._entries: list[StringCatalogEntry] = []
        self._current_id = (current_id or "").strip()
        self._highlight_needle = ""
        self._root_groups = root_groups or build_string_picker_root_groups(
            resolver,
            anomaly_root=anomaly_root,
            gamma_root=gamma_root,
        )

        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Root"))
        self.root_combo = QComboBox()
        for g in self._root_groups:
            self.root_combo.addItem(f"{g.label}  ({len(g.text_roots)} dir)", g)
        self.root_combo.setToolTip(
            "Limit the list to one install / custom root. Custom is default."
        )
        top.addWidget(self.root_combo, stretch=1)
        lay.addLayout(top)
        self.root_combo.currentIndexChanged.connect(self._on_root_changed)

        filt = QHBoxLayout()
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText(
            "Filter by id or text (e.g. st_new_game, Start)…"
        )
        if start_filter:
            self.filter_edit.setText(start_filter)
        self.filter_edit.textChanged.connect(self._apply_filter)
        filt.addWidget(self.filter_edit, stretch=1)
        lay.addLayout(filt)

        self.status = QLabel("Scanning strings…")
        self.status.setStyleSheet("color: #9a9a9a;")
        lay.addWidget(self.status)

        self.preview = QLabel("")
        self.preview.setWordWrap(True)
        self.preview.setStyleSheet(
            "color: #D4D4D4; background: #252528; padding: 8px; border-radius: 4px;"
        )
        self.preview.setMinimumHeight(56)
        self.preview.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        lay.addWidget(self.preview)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setUniformItemSizes(True)
        self.list.setItemDelegate(_MatchHighlightDelegate(self))
        self.list.itemDoubleClicked.connect(self.accept)
        self.list.itemSelectionChanged.connect(self._on_selection_changed)
        lay.addWidget(self.list, stretch=1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if self._ok is not None:
            self._ok.setEnabled(False)
        lay.addWidget(buttons)

        QTimer.singleShot(0, self._scan_and_populate)

    def selected_entry(self) -> StringCatalogEntry | None:
        item = self.list.currentItem()
        if item is None or item.isHidden():
            return None
        sid = item.data(_ROLE_ID)
        body = item.data(_ROLE_TEXT)
        path = item.data(_ROLE_PATH)
        if not sid or not path:
            return None
        return StringCatalogEntry(
            string_id=str(sid),
            text=str(body or ""),
            source=Path(str(path)),
        )

    def _selected_root_group(self) -> StringRootGroup:
        data = self.root_combo.currentData()
        if isinstance(data, StringRootGroup):
            return data
        return self._root_groups[0]

    def _on_root_changed(self, _index: int = 0) -> None:
        self._scan_and_populate()

    def _scan_and_populate(self) -> None:
        group = self._selected_root_group()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            if group.label == "All":
                self._entries = self._resolver.scan_catalog(source_roots=None)
            else:
                self._entries = self._resolver.scan_catalog(
                    source_roots=list(group.text_roots)
                )
        finally:
            QApplication.restoreOverrideCursor()
        self.list.clear()
        select_row = -1
        cur = self._current_id.lower()
        for i, entry in enumerate(self._entries):
            preview = strip_engine_markup(entry.text).replace("\n", " · ")
            if len(preview) > 80:
                preview = preview[:77] + "…"
            label = f"{entry.string_id}  —  {preview}" if preview else entry.string_id
            item = QListWidgetItem(label)
            item.setData(_ROLE_ID, entry.string_id)
            item.setData(_ROLE_TEXT, entry.text)
            item.setData(_ROLE_PATH, str(entry.source))
            item.setToolTip(
                f"{entry.string_id}\n{strip_engine_markup(entry.text)}\n{entry.source}"
            )
            self.list.addItem(item)
            if cur and entry.string_id.lower() == cur:
                select_row = i
        self.status.setText(f"{len(self._entries)} strings")
        self._apply_filter(self.filter_edit.text())
        if select_row >= 0:
            self.list.setCurrentRow(select_row)
            self.list.scrollToItem(self.list.item(select_row))
        self._on_selection_changed()

    def _apply_filter(self, text: str) -> None:
        needle = (text or "").strip().lower()
        visible = 0
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item is None:
                continue
            sid = str(item.data(_ROLE_ID) or "").lower()
            body = strip_engine_markup(str(item.data(_ROLE_TEXT) or "")).lower()
            show = not needle or needle in sid or needle in body
            item.setHidden(not show)
            if show:
                visible += 1
        total = len(self._entries)
        # Highlight match spans only when the filtered set is small enough.
        self._highlight_needle = needle if needle and visible < _HIGHLIGHT_MAX else ""
        if needle:
            self.status.setText(f"{visible} / {total} strings")
        else:
            self.status.setText(f"{total} strings")
        self.list.viewport().update()

    def _on_selection_changed(self) -> None:
        entry = self.selected_entry()
        if self._ok is not None:
            self._ok.setEnabled(entry is not None)
        if entry is None:
            self.preview.setText("Select a string to preview.")
            return
        body = strip_engine_markup(entry.text) or "(empty)"
        self.preview.setText(f"{entry.string_id}\n{body}")
