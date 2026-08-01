"""Texture picker — Anomaly-style atlas ids first, DDS paths as secondary.

Engine model (CUITextureMaster):
- No backslash → atlas id from textures_descr (file + UV)
- With ``\\`` → DDS path under gamedata/textures (optional XML UV crop)
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from .textures import (
    AtlasCatalogEntry,
    DdsCatalogEntry,
    TexturePick,
    TextureResolver,
    scan_dds_catalog,
)

_ROLE_KIND = int(Qt.ItemDataRole.UserRole)
_ROLE_NAME = int(Qt.ItemDataRole.UserRole) + 1
_ROLE_PATH = int(Qt.ItemDataRole.UserRole) + 2
_ROLE_FILE = int(Qt.ItemDataRole.UserRole) + 3
_ROLE_UV = int(Qt.ItemDataRole.UserRole) + 4  # "x,y,w,h" or ""
_THUMB = 96
_BATCH = 10


def _placeholder_icon(size: int = _THUMB) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(QColor("#2A2A30"))
    return QIcon(pix)


def _fail_icon(size: int = _THUMB) -> QIcon:
    fail = QPixmap(size, size)
    fail.fill(QColor("#3A2020"))
    painter = QPainter(fail)
    painter.setPen(QColor("#CC6666"))
    painter.drawText(fail.rect(), Qt.AlignmentFlag.AlignCenter, "?")
    painter.end()
    return QIcon(fail)


def _pil_to_icon(img: Image.Image, size: int = _THUMB) -> QIcon:
    img = img.convert("RGBA")
    img.thumbnail((size, size), Image.Resampling.LANCZOS)
    bg = Image.new("RGBA", (size, size), (42, 42, 48, 255))
    ox = (size - img.width) // 2
    oy = (size - img.height) // 2
    bg.paste(img, (ox, oy), img)
    data = bg.tobytes("raw", "RGBA")
    qimg = QImage(data, size, size, size * 4, QImage.Format.Format_RGBA8888).copy()
    return QIcon(QPixmap.fromImage(qimg))


def _thumb_from_dds(
    path: Path,
    *,
    uv: tuple[float, float, float, float] | None = None,
    size: int = _THUMB,
) -> QIcon | None:
    try:
        img = Image.open(path)
        img.load()
        img = img.convert("RGBA")
    except OSError:
        return None
    if uv is not None:
        x, y, w, h = uv
        if w > 0 and h > 0:
            left = max(0, int(x))
            top = max(0, int(y))
            right = min(img.width, int(x + w))
            bottom = min(img.height, int(y + h))
            if right > left and bottom > top:
                img = img.crop((left, top, right, bottom))
    return _pil_to_icon(img, size)


class TexturePickerDialog(QDialog):
    """Pick a textures_descr atlas id (default) or a full DDS path."""

    def __init__(
        self,
        resolver: TextureResolver,
        parent: QWidget | None = None,
        *,
        current_name: str = "",
        prefer_path: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select texture")
        self.setModal(True)
        self.resize(820, 580)
        self._resolver = resolver
        self._atlas: list[AtlasCatalogEntry] = []
        self._dds: list[DdsCatalogEntry] = []
        self._placeholder = _placeholder_icon()
        self._fail = _fail_icon()
        self._thumb_cache: dict[str, QIcon] = {}
        self._thumb_queue: list[QListWidgetItem] = []
        self._current = (current_name or "").strip()
        self._mode = "path" if prefer_path else "atlas"

        lay = QVBoxLayout(self)

        mode_row = QHBoxLayout()
        self.mode_atlas = QRadioButton("Atlas (textures_descr)")
        self.mode_path = QRadioButton("DDS path")
        self.mode_atlas.setToolTip(
            "Anomaly atlas ids — no backslash. UV comes from textures_descr "
            "(required for buttons / checkboxes / shared crops)."
        )
        self.mode_path.setToolTip(
            "Direct DDS under textures\\ (e.g. ui\\foo). Optional XML UV crop "
            "is separate; picker writes the full file with no UV attrs."
        )
        group = QButtonGroup(self)
        group.addButton(self.mode_atlas)
        group.addButton(self.mode_path)
        if self._mode == "path":
            self.mode_path.setChecked(True)
        else:
            self.mode_atlas.setChecked(True)
        self.mode_atlas.toggled.connect(self._on_mode_changed)
        mode_row.addWidget(self.mode_atlas)
        mode_row.addWidget(self.mode_path)
        mode_row.addStretch(1)
        self.preview_check = QCheckBox("Preview")
        self.preview_check.setChecked(True)
        self.preview_check.toggled.connect(self._on_preview_toggled)
        mode_row.addWidget(self.preview_check)
        lay.addLayout(mode_row)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter…")
        self.filter_edit.textChanged.connect(self._apply_filter)
        lay.addWidget(self.filter_edit)

        self.status = QLabel("Scanning…")
        self.status.setStyleSheet("color: #9a9a9a;")
        lay.addWidget(self.status)

        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        self.detail.setStyleSheet(
            "color: #D4D4D4; background: #252528; padding: 8px; border-radius: 4px;"
        )
        self.detail.setMinimumHeight(48)
        lay.addWidget(self.detail)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setMovement(QListWidget.Movement.Static)
        self.list.setWordWrap(True)
        self.list.setUniformItemSizes(True)
        self.list.itemDoubleClicked.connect(self.accept)
        self.list.itemSelectionChanged.connect(self._on_selection_changed)
        self.list.verticalScrollBar().valueChanged.connect(self._on_scroll)
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

        self._thumb_timer = QTimer(self)
        self._thumb_timer.setInterval(0)
        self._thumb_timer.timeout.connect(self._load_thumb_batch)

        self._set_icon_mode(True)
        QTimer.singleShot(0, self._scan_and_populate)

    def selected_pick(self) -> TexturePick | None:
        item = self.list.currentItem()
        if item is None or item.isHidden():
            return None
        kind = str(item.data(_ROLE_KIND) or "")
        name = str(item.data(_ROLE_NAME) or "")
        path_s = str(item.data(_ROLE_PATH) or "")
        if not kind or not name:
            return None
        return TexturePick(
            name=name,
            kind=kind,
            dds_path=Path(path_s) if path_s else None,
        )

    # Back-compat for older callers
    def selected_entry(self) -> DdsCatalogEntry | None:
        pick = self.selected_pick()
        if pick is None or pick.kind != "path" or pick.dds_path is None:
            return None
        return DdsCatalogEntry(logical=pick.name, path=pick.dds_path)

    def _on_mode_changed(self, _checked: bool = False) -> None:
        self._mode = "atlas" if self.mode_atlas.isChecked() else "path"
        self._populate_list()

    def _set_icon_mode(self, on: bool) -> None:
        if on:
            self.list.setViewMode(QListWidget.ViewMode.IconMode)
            self.list.setIconSize(QSize(_THUMB, _THUMB))
            self.list.setGridSize(QSize(_THUMB + 48, _THUMB + 56))
            self.list.setSpacing(8)
            hint = QSize(_THUMB + 48, _THUMB + 56)
        else:
            self.list.setViewMode(QListWidget.ViewMode.ListMode)
            self.list.setIconSize(QSize(28, 28))
            self.list.setGridSize(QSize())
            self.list.setSpacing(2)
            hint = QSize(240, 32)
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item is not None:
                item.setSizeHint(hint)

    def _on_preview_toggled(self, checked: bool) -> None:
        self._set_icon_mode(checked)
        if checked:
            self._queue_visible_thumbs()
            if not self._thumb_timer.isActive():
                self._thumb_timer.start()

    def _on_scroll(self, _value: int) -> None:
        if not self.preview_check.isChecked():
            return
        self._queue_visible_thumbs()
        if not self._thumb_timer.isActive():
            self._thumb_timer.start()

    def _scan_and_populate(self) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._atlas = self._resolver.scan_atlas_catalog()
            self._dds = scan_dds_catalog(self._resolver.dds_search_roots())
        finally:
            QApplication.restoreOverrideCursor()
        self._populate_list()

    def _populate_list(self) -> None:
        self.list.clear()
        self._thumb_queue.clear()
        select_row = -1
        cur = self._current.lower().replace("/", "\\")
        hint = (
            QSize(_THUMB + 48, _THUMB + 56)
            if self.preview_check.isChecked()
            else QSize(240, 32)
        )

        if self._mode == "atlas":
            self.filter_edit.setPlaceholderText(
                "Filter atlas id or sheet (e.g. ui_inGame2_button, ui\\dots)…"
            )
            for i, entry in enumerate(self._atlas):
                label = entry.atlas_id
                if entry.is_stem:
                    label = f"{entry.atlas_id}  (stem)"
                item = QListWidgetItem(self._placeholder, label)
                item.setData(_ROLE_KIND, "atlas")
                item.setData(_ROLE_NAME, entry.atlas_id)
                dds = self._resolver.find_dds(entry.file_name)
                item.setData(_ROLE_PATH, str(dds) if dds else "")
                item.setData(_ROLE_FILE, entry.file_name)
                item.setData(
                    _ROLE_UV,
                    f"{entry.x},{entry.y},{entry.width},{entry.height}",
                )
                tip = (
                    f"{entry.atlas_id}\n"
                    f"sheet: {entry.file_name}\n"
                    f"UV {int(entry.x)},{int(entry.y)} "
                    f"{int(entry.width)}×{int(entry.height)}"
                )
                if entry.is_stem:
                    tip += f"\n(button stem → {entry.state_id})"
                tip += f"\n{entry.source}"
                item.setToolTip(tip)
                item.setSizeHint(hint)
                self.list.addItem(item)
                if cur and entry.atlas_id.lower() == cur:
                    select_row = i
            self.status.setText(f"{len(self._atlas)} atlas ids")
        else:
            self.filter_edit.setPlaceholderText(
                "Filter DDS path (e.g. ui\\, lightgem)…"
            )
            for i, entry in enumerate(self._dds):
                item = QListWidgetItem(self._placeholder, entry.logical)
                item.setData(_ROLE_KIND, "path")
                item.setData(_ROLE_NAME, entry.logical)
                item.setData(_ROLE_PATH, str(entry.path))
                item.setData(_ROLE_FILE, entry.logical)
                item.setData(_ROLE_UV, "")
                item.setToolTip(f"{entry.logical}\n{entry.path}")
                item.setSizeHint(hint)
                self.list.addItem(item)
                if cur and entry.logical.lower() == cur:
                    select_row = i
            self.status.setText(f"{len(self._dds)} DDS paths")

        self._apply_filter(self.filter_edit.text())
        if select_row >= 0:
            self.list.setCurrentRow(select_row)
            self.list.scrollToItem(self.list.item(select_row))
        if self.preview_check.isChecked():
            self._queue_visible_thumbs()
            self._thumb_timer.start()
        self._on_selection_changed()

    def _apply_filter(self, text: str) -> None:
        needle = (text or "").strip().lower().replace("/", "\\")
        visible = 0
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item is None:
                continue
            name = str(item.data(_ROLE_NAME) or "").lower()
            file_name = str(item.data(_ROLE_FILE) or "").lower()
            show = not needle or needle in name or needle in file_name
            item.setHidden(not show)
            if show:
                visible += 1
        total = self.list.count()
        kind = "atlas ids" if self._mode == "atlas" else "DDS paths"
        if needle:
            self.status.setText(f"{visible} / {total} {kind}")
        else:
            self.status.setText(f"{total} {kind}")
        if self.preview_check.isChecked():
            self._queue_visible_thumbs()
            if not self._thumb_timer.isActive():
                self._thumb_timer.start()

    def _on_selection_changed(self) -> None:
        pick = self.selected_pick()
        if self._ok is not None:
            self._ok.setEnabled(pick is not None)
        item = self.list.currentItem()
        if pick is None or item is None:
            self.detail.setText(
                "Atlas mode writes an id (no \\); engine UV comes from "
                "textures_descr. DDS path mode writes ui\\file with no UV."
            )
            return
        if pick.kind == "atlas":
            uv = str(item.data(_ROLE_UV) or "")
            sheet = str(item.data(_ROLE_FILE) or "")
            self.detail.setText(
                f"{pick.name}  →  sheet {sheet}  UV {uv}\n"
                f"XML: <texture>{pick.name}</texture>  (UV from textures_descr)"
            )
        else:
            self.detail.setText(
                f"{pick.name}  (full DDS path)\n"
                f"XML: <texture>{pick.name}</texture>  (no UV attrs)"
            )

    def _cache_key(self, item: QListWidgetItem) -> str:
        path = str(item.data(_ROLE_PATH) or "")
        uv = str(item.data(_ROLE_UV) or "")
        return f"{path}|{uv}"

    def _queue_visible_thumbs(self) -> None:
        self._thumb_queue.clear()
        rect = self.list.viewport().rect()
        queued: set[int] = set()
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item is None or item.isHidden():
                continue
            key = self._cache_key(item)
            if key in self._thumb_cache:
                item.setIcon(self._thumb_cache[key])
                continue
            idx = self.list.indexFromItem(item)
            if idx.isValid() and self.list.visualRect(idx).intersects(rect):
                self._thumb_queue.append(item)
                queued.add(i)
        for i in range(self.list.count()):
            if i in queued:
                continue
            item = self.list.item(i)
            if item is None or item.isHidden():
                continue
            key = self._cache_key(item)
            if key in self._thumb_cache:
                item.setIcon(self._thumb_cache[key])
                continue
            self._thumb_queue.append(item)

    def _load_thumb_batch(self) -> None:
        if not self.preview_check.isChecked():
            self._thumb_timer.stop()
            return
        n = 0
        while self._thumb_queue and n < _BATCH:
            item = self._thumb_queue.pop(0)
            n += 1
            if item is None or item.isHidden():
                continue
            key = self._cache_key(item)
            if key in self._thumb_cache:
                item.setIcon(self._thumb_cache[key])
                continue
            path_s = str(item.data(_ROLE_PATH) or "")
            uv_s = str(item.data(_ROLE_UV) or "")
            icon: QIcon | None = None
            if path_s:
                uv = None
                if uv_s:
                    try:
                        parts = [float(x) for x in uv_s.split(",")]
                        if len(parts) == 4:
                            uv = (parts[0], parts[1], parts[2], parts[3])
                    except ValueError:
                        uv = None
                icon = _thumb_from_dds(Path(path_s), uv=uv)
            if icon is None:
                icon = self._fail
            self._thumb_cache[key] = icon
            item.setIcon(icon)
        if not self._thumb_queue:
            self._thumb_timer.stop()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.preview_check.isChecked():
            self._queue_visible_thumbs()
            if not self._thumb_timer.isActive():
                self._thumb_timer.start()
