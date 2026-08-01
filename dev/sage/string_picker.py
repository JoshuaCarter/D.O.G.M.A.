"""String-table picker: catalog of scanned ids with text preview."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .strings import StringCatalogEntry, StringResolver, strip_engine_markup

_ROLE_ID = int(Qt.ItemDataRole.UserRole)
_ROLE_TEXT = int(Qt.ItemDataRole.UserRole) + 1
_ROLE_PATH = int(Qt.ItemDataRole.UserRole) + 2


class StringPickerDialog(QDialog):
    """Pick a string id from scanned text roots. Preview body shown by default."""

    def __init__(
        self,
        resolver: StringResolver,
        parent: QWidget | None = None,
        *,
        current_id: str = "",
        start_filter: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select text")
        self.setModal(True)
        self.resize(720, 520)
        self._resolver = resolver
        self._entries: list[StringCatalogEntry] = []
        self._current_id = (current_id or "").strip()

        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText(
            "Filter by id or text (e.g. st_new_game, Start)…"
        )
        if start_filter:
            self.filter_edit.setText(start_filter)
        self.filter_edit.textChanged.connect(self._apply_filter)
        top.addWidget(self.filter_edit, stretch=1)
        lay.addLayout(top)

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

    def _scan_and_populate(self) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._entries = self._resolver.scan_catalog()
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
        if needle:
            self.status.setText(f"{visible} / {total} strings")
        else:
            self.status.setText(f"{total} strings")

    def _on_selection_changed(self) -> None:
        entry = self.selected_entry()
        if self._ok is not None:
            self._ok.setEnabled(entry is not None)
        if entry is None:
            self.preview.setText("Select a string to preview.")
            return
        body = strip_engine_markup(entry.text) or "(empty)"
        self.preview.setText(f"{entry.string_id}\n{body}")
