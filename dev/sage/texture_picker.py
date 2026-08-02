"""Texture picker — Anomaly-style atlas ids first, DDS paths as secondary.

Engine model (CUITextureMaster):
- No backslash → atlas id from textures_descr (file + UV)
- With ``\\`` → DDS path under gamedata/textures (optional XML UV crop)
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from PIL import Image
from PyQt6.QtCore import QObject, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap, QWheelEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from .textures import (
    AtlasCatalogEntry,
    DdsCatalogEntry,
    PICKER_THUMB_SIZE,
    PickerRootGroup,
    TexturePick,
    TextureResolver,
    build_picker_thumb_image,
)

_ROLE_KIND = int(Qt.ItemDataRole.UserRole)
_ROLE_NAME = int(Qt.ItemDataRole.UserRole) + 1
_ROLE_PATH = int(Qt.ItemDataRole.UserRole) + 2
_ROLE_FILE = int(Qt.ItemDataRole.UserRole) + 3
_ROLE_UV = int(Qt.ItemDataRole.UserRole) + 4  # "x,y,w,h" or ""
_ROLE_SHORT = int(Qt.ItemDataRole.UserRole) + 5  # icon-grid caption
_THUMB = PICKER_THUMB_SIZE
_SCROLL_SETTLE_MS = 80
_MAX_WORKERS = 8
# Wheel: fixed pixels per 120° notch (not a fraction of content height).
_WHEEL_STEP_PX = 120


class _FixedWheelList(QListWidget):
    """List whose wheel scroll distance is independent of document length."""

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        pd = event.pixelDelta()
        ad = event.angleDelta()
        if not pd.isNull() and (pd.x() != 0 or pd.y() != 0):
            dx, dy = int(pd.x()), int(pd.y())
        elif ad.x() != 0 or ad.y() != 0:
            dx = int(ad.x() / 120.0 * _WHEEL_STEP_PX)
            dy = int(ad.y() / 120.0 * _WHEEL_STEP_PX)
        else:
            super().wheelEvent(event)
            return
        if dy:
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() - dy)
        if dx:
            bar = self.horizontalScrollBar()
            bar.setValue(bar.value() - dx)
        event.accept()


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


def _pil_to_qimage(img: Image.Image, size: int = _THUMB) -> QImage:
    """Convert an already-sized RGBA thumb (or any PIL image) to QImage."""
    img = img.convert("RGBA")
    if img.size != (size, size):
        # Letterbox path for unexpected sizes; normal cache hits are size×size.
        from .textures import letterbox_thumb

        img = letterbox_thumb(img, size)
    data = img.tobytes("raw", "RGBA")
    return QImage(data, size, size, size * 4, QImage.Format.Format_RGBA8888).copy()


def _thumb_qimage_from_dds(
    path: Path,
    *,
    uv: tuple[float, float, float, float] | None = None,
    size: int = _THUMB,
) -> QImage | None:
    """Decode off the UI thread (disk thumb cache → DDS RGBA cache)."""
    img = build_picker_thumb_image(path, uv=uv, size=size)
    if img is None:
        return None
    return _pil_to_qimage(img, size)


class _ThumbBridge(QObject):
    """Marshal worker results back to the UI thread."""

    ready = pyqtSignal(str, object)  # cache key, QImage | None


class TexturePickerDialog(QDialog):
    """Pick a textures_descr atlas id (default) or a full DDS path."""

    def __init__(
        self,
        resolver: TextureResolver,
        parent: QWidget | None = None,
        *,
        current_name: str = "",
        prefer_path: bool = False,
        root_groups: list[PickerRootGroup] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select texture")
        self.setModal(True)
        self.resize(820, 580)
        self._resolver = resolver
        self._root_groups = list(root_groups or [])
        if not self._root_groups:
            roots = tuple(resolver.dds_search_roots())
            descr = tuple(
                list(resolver.gamedata_descr_roots) + list(resolver.descr_scan_roots)
            )
            self._root_groups = [PickerRootGroup("All", roots, descr)]
        # None = not loaded for current root (avoid building DDS catalog in atlas mode).
        self._atlas: list[AtlasCatalogEntry] | None = None
        self._dds: list[DdsCatalogEntry] | None = None
        self._placeholder = _placeholder_icon()
        self._fail = _fail_icon()
        self._thumb_cache: dict[str, QIcon] = {}
        # Visible items waiting for a decode (key → item).
        self._pending: dict[str, QListWidgetItem] = {}
        # In-flight worker futures (key → Future).
        self._inflight: dict[str, Future] = {}
        self._wanted: set[str] = set()
        self._current = (current_name or "").strip()
        self._mode = "path" if prefer_path else "atlas"
        self._pool = ThreadPoolExecutor(max_workers=_MAX_WORKERS)
        self._bridge = _ThumbBridge()
        self._bridge.ready.connect(self._on_thumb_ready)

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

        root_row = QHBoxLayout()
        root_row.addWidget(QLabel("Root"))
        self.root_combo = QComboBox()
        for g in self._root_groups:
            n_tex = len(g.texture_roots)
            n_descr = len(g.descr_roots)
            self.root_combo.addItem(f"{g.label}  ({n_tex} tex · {n_descr} descr)", g)
        self.root_combo.setToolTip(
            "Limit the list to one install / custom root. Custom is default (small)."
        )
        root_row.addWidget(self.root_combo, stretch=1)
        lay.addLayout(root_row)
        self.root_combo.currentIndexChanged.connect(self._on_root_changed)

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

        self.list = _FixedWheelList()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setMovement(QListWidget.Movement.Static)
        self.list.setWordWrap(True)
        self.list.setUniformItemSizes(True)
        self.list.setLayoutMode(QListView.LayoutMode.Batched)
        self.list.setBatchSize(64)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.verticalScrollBar().setSingleStep(_WHEEL_STEP_PX // 3)
        self.list.horizontalScrollBar().setSingleStep(_WHEEL_STEP_PX // 3)
        self.list.itemDoubleClicked.connect(self.accept)
        self.list.itemSelectionChanged.connect(self._on_selection_changed)
        self.list.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.list.horizontalScrollBar().valueChanged.connect(self._on_scroll)
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

        # Debounce: rebuild visible thumb work after scroll settles.
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(_SCROLL_SETTLE_MS)
        self._scroll_timer.timeout.connect(self._sync_visible_thumbs)

        self._set_icon_mode(True)
        QTimer.singleShot(0, self._scan_and_populate)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._cancel_all_thumbs()
        self._pool.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(event)

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

    def _on_mode_changed(self, _checked: bool = False) -> None:
        self._mode = "atlas" if self.mode_atlas.isChecked() else "path"
        self._ensure_mode_catalog()
        self._populate_list()

    def _on_root_changed(self, _index: int = 0) -> None:
        self._atlas = None
        self._dds = None
        self._scan_and_populate()

    def _selected_root_group(self) -> PickerRootGroup:
        data = self.root_combo.currentData()
        if isinstance(data, PickerRootGroup):
            return data
        return self._root_groups[0]

    def _ensure_mode_catalog(self) -> None:
        """Load only the catalog needed for the current mode + root."""
        group = self._selected_root_group()
        if self._mode == "atlas":
            if self._atlas is not None:
                return
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                if group.label == "All":
                    self._atlas = self._resolver.scan_atlas_catalog(
                        source_roots=None
                    )
                else:
                    self._atlas = self._resolver.scan_atlas_catalog(
                        source_roots=list(group.descr_roots)
                    )
            finally:
                QApplication.restoreOverrideCursor()
            return
        if self._dds is not None:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            if group.label == "All":
                self._dds = self._resolver.dds_catalog_for_roots(None)
            else:
                self._dds = self._resolver.dds_catalog_for_roots(
                    list(group.texture_roots)
                )
        finally:
            QApplication.restoreOverrideCursor()

    def _set_icon_mode(self, on: bool) -> None:
        if on:
            self.list.setViewMode(QListWidget.ViewMode.IconMode)
            self.list.setIconSize(QSize(_THUMB, _THUMB))
            self.list.setGridSize(QSize(_THUMB + 48, _THUMB + 56))
            self.list.setSpacing(8)
            hint = QSize(_THUMB + 48, _THUMB + 56)
        else:
            self.list.setViewMode(QListWidget.ViewMode.ListMode)
            self.list.setIconSize(QSize(0, 0))
            self.list.setGridSize(QSize())
            self.list.setSpacing(0)
            hint = QSize(240, 22)
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item is not None:
                item.setSizeHint(hint)
                if on:
                    item.setIcon(self._placeholder)
                else:
                    item.setIcon(QIcon())
                item.setText(self._item_label(item, preview=on))

    def _item_label(self, item: QListWidgetItem, *, preview: bool) -> str:
        short = str(item.data(_ROLE_SHORT) or item.data(_ROLE_NAME) or "")
        if preview:
            return short
        kind = str(item.data(_ROLE_KIND) or "")
        file_name = str(item.data(_ROLE_FILE) or "")
        if kind == "atlas" and file_name:
            return f"{short}  ·  {file_name}"
        return short

    def _on_preview_toggled(self, checked: bool) -> None:
        self._set_icon_mode(checked)
        if checked:
            self._sync_visible_thumbs()
        else:
            self._cancel_all_thumbs()

    def _on_scroll(self, _value: int = 0) -> None:
        if not self.preview_check.isChecked():
            return
        # Drop work for cells that left the viewport immediately; load after settle.
        self._prune_pending_to_visible()
        self._scroll_timer.start()

    def _scan_and_populate(self) -> None:
        self._ensure_mode_catalog()
        self._populate_list()

    def _populate_list(self) -> None:
        self._cancel_all_thumbs()
        self.list.setUpdatesEnabled(False)
        self.list.clear()
        select_row = -1
        cur = self._current.lower().replace("/", "\\")
        preview = self.preview_check.isChecked()
        hint = (
            QSize(_THUMB + 48, _THUMB + 56) if preview else QSize(240, 22)
        )
        atlas = self._atlas or []
        dds_list = self._dds or []

        try:
            if self._mode == "atlas":
                self.filter_edit.setPlaceholderText(
                    "Filter atlas id or sheet (e.g. ui_inGame2_button, ui\\dots)…"
                )
                # One find_dds per unique sheet — GAMMA has thousands of ids, few sheets.
                dds_by_file: dict[str, str] = {}
                for entry in atlas:
                    fn = entry.file_name
                    if fn in dds_by_file:
                        continue
                    dds = self._resolver.find_dds(fn)
                    dds_by_file[fn] = str(dds) if dds else ""
                for i, entry in enumerate(atlas):
                    short = entry.atlas_id
                    if entry.is_stem:
                        short = f"{entry.atlas_id}  (stem)"
                    item = QListWidgetItem()
                    item.setData(_ROLE_KIND, "atlas")
                    item.setData(_ROLE_NAME, entry.atlas_id)
                    item.setData(_ROLE_SHORT, short)
                    item.setData(_ROLE_PATH, dds_by_file.get(entry.file_name, ""))
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
                    item.setText(self._item_label(item, preview=preview))
                    item.setIcon(self._placeholder if preview else QIcon())
                    item.setSizeHint(hint)
                    self.list.addItem(item)
                    if cur and entry.atlas_id.lower() == cur:
                        select_row = i
                self.status.setText(f"{len(atlas)} atlas ids")
            else:
                self.filter_edit.setPlaceholderText(
                    "Filter DDS path (e.g. ui\\, lightgem)…"
                )
                for i, entry in enumerate(dds_list):
                    item = QListWidgetItem()
                    item.setData(_ROLE_KIND, "path")
                    item.setData(_ROLE_NAME, entry.logical)
                    item.setData(_ROLE_SHORT, entry.logical)
                    item.setData(_ROLE_PATH, str(entry.path))
                    item.setData(_ROLE_FILE, entry.logical)
                    item.setData(_ROLE_UV, "")
                    item.setToolTip(f"{entry.logical}\n{entry.path}")
                    item.setText(self._item_label(item, preview=preview))
                    item.setIcon(self._placeholder if preview else QIcon())
                    item.setSizeHint(hint)
                    self.list.addItem(item)
                    if cur and entry.logical.lower() == cur:
                        select_row = i
                self.status.setText(f"{len(dds_list)} DDS paths")

            self._apply_filter(self.filter_edit.text())
            if select_row >= 0:
                self.list.setCurrentRow(select_row)
                self.list.scrollToItem(self.list.item(select_row))
        finally:
            self.list.setUpdatesEnabled(True)
        if self.preview_check.isChecked():
            self._sync_visible_thumbs()
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
            self._sync_visible_thumbs()

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

    def _iter_visible_items(self) -> list[QListWidgetItem]:
        """Items whose cells intersect the viewport (not merely unfiltered)."""
        rect = self.list.viewport().rect()
        # Slight pad so near-edge cells start loading before fully on screen.
        rect = rect.adjusted(-8, -8, 8, 8)
        out: list[QListWidgetItem] = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item is None or item.isHidden():
                continue
            idx = self.list.indexFromItem(item)
            if not idx.isValid():
                continue
            if self.list.visualRect(idx).intersects(rect):
                out.append(item)
        return out

    def _prune_pending_to_visible(self) -> None:
        """Cancel queued/in-flight work for cells that left the viewport."""
        visible_keys = {self._cache_key(it) for it in self._iter_visible_items()}
        self._wanted = visible_keys
        for key in list(self._pending):
            if key not in visible_keys:
                self._pending.pop(key, None)
        for key, fut in list(self._inflight.items()):
            if key in visible_keys:
                continue
            fut.cancel()
            self._inflight.pop(key, None)

    def _cancel_all_thumbs(self) -> None:
        self._scroll_timer.stop()
        self._wanted.clear()
        self._pending.clear()
        for fut in self._inflight.values():
            fut.cancel()
        self._inflight.clear()

    def _sync_visible_thumbs(self) -> None:
        """Apply cached icons + start async decode only for visible cells."""
        if not self.preview_check.isChecked():
            return
        visible = self._iter_visible_items()
        wanted = {self._cache_key(it) for it in visible}
        self._wanted = wanted

        # Drop pending for off-screen cells.
        for key in list(self._pending):
            if key not in wanted:
                self._pending.pop(key, None)
        for key, fut in list(self._inflight.items()):
            if key in wanted:
                continue
            fut.cancel()
            self._inflight.pop(key, None)

        for item in visible:
            key = self._cache_key(item)
            cached = self._thumb_cache.get(key)
            if cached is not None:
                item.setIcon(cached)
                continue
            if key in self._inflight or key in self._pending:
                continue
            self._pending[key] = item
            self._submit_thumb(key, item)

    def _submit_thumb(self, key: str, item: QListWidgetItem) -> None:
        path_s = str(item.data(_ROLE_PATH) or "")
        uv_s = str(item.data(_ROLE_UV) or "")
        if not path_s:
            self._thumb_cache[key] = self._fail
            item.setIcon(self._fail)
            self._pending.pop(key, None)
            return
        uv: tuple[float, float, float, float] | None = None
        if uv_s:
            try:
                parts = [float(x) for x in uv_s.split(",")]
                if len(parts) == 4:
                    uv = (parts[0], parts[1], parts[2], parts[3])
            except ValueError:
                uv = None
        path = Path(path_s)

        def work() -> tuple[str, QImage | None]:
            return key, _thumb_qimage_from_dds(path, uv=uv, size=_THUMB)

        fut = self._pool.submit(work)
        self._inflight[key] = fut

        def _done(f: Future) -> None:
            try:
                if f.cancelled():
                    return
                result = f.result()
            except Exception:
                result = (key, None)
            self._bridge.ready.emit(result[0], result[1])

        fut.add_done_callback(_done)

    def _on_thumb_ready(self, key: str, image: object) -> None:
        self._inflight.pop(key, None)
        item = self._pending.pop(key, None)
        if isinstance(image, QImage):
            icon = QIcon(QPixmap.fromImage(image))
        else:
            icon = self._fail
        self._thumb_cache[key] = icon
        # Only paint if preview on and still wanted (visible); cache kept for later.
        if not self.preview_check.isChecked() or key not in self._wanted:
            return
        if item is None:
            # Find by key among visible if pending was pruned.
            for it in self._iter_visible_items():
                if self._cache_key(it) == key:
                    item = it
                    break
        if item is not None:
            item.setIcon(icon)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.preview_check.isChecked():
            self._scroll_timer.start()
