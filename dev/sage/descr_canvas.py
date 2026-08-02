"""DDS-pixel canvas for editing textures_descr UV regions."""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QPainter,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PyQt6.QtWidgets import (
    QComboBox,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from .box_chrome import (
    FOCUS_LABEL_Z,
    IdleBorderChrome,
    OutsideLabelChrome,
    SEL_BLUE,
    SEL_FILL_ATLAS,
    SELECT_LABEL_TEXT,
    FocusCaptionOverlay,
)
from .cursor_coords import CursorCoordsHud
from .descr_model import DescrDocument, DescrRegion, DescrSheet
from .pixel_grid import PixelGridItem
from .settings import ZOOM_SCALE_MAX, ZOOM_SCALE_MIN, clamp_label_font_size
from .textures import TextureResolver
from .undo import GeoEdit, GeoState, UndoStack

HANDLE = 8.0
MIN_SIZE = 2.0

# Arrow-key nudge: 1px per press; hold → 500ms delay, then 1px / 50ms.
_NUDGE_DELAY_MS = 500
_NUDGE_REPEAT_MS = 50


def _arrow_nudge_delta(key: int) -> tuple[int, int] | None:
    if key == Qt.Key.Key_Left:
        return (-1, 0)
    if key == Qt.Key.Key_Right:
        return (1, 0)
    if key == Qt.Key.Key_Up:
        return (0, -1)
    if key == Qt.Key.Key_Down:
        return (0, 1)
    return None


def _pil_to_pixmap(img: Image.Image) -> QPixmap:
    img = img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    qimg = QImage(
        data, img.width, img.height, img.width * 4, QImage.Format.Format_RGBA8888
    ).copy()
    return QPixmap.fromImage(qimg)


class RegionItem(QGraphicsRectItem):
    """Editable UV box on the atlas sheet."""

    def __init__(
        self,
        region: DescrRegion,
        *,
        show_box_border: bool = False,
        show_box_fill: bool = False,
        show_element_labels: bool = False,
        label_font_size: int = 5,
    ) -> None:
        super().__init__(0, 0, max(region.width, 1), max(region.height, 1))
        self.region = region
        self.show_box_border = bool(show_box_border)
        self.show_box_fill = bool(show_box_fill)
        self.show_element_labels = bool(show_element_labels)
        self.label_font_size = clamp_label_font_size(label_font_size)
        self.setPos(region.x, region.y)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        # Scene-level chrome (same as UI editor) — never parented inside the box.
        self._caption = OutsideLabelChrome()
        self._idle_border = IdleBorderChrome()
        self._resizing = False
        self._resize_corner: str | None = None
        self._resize_start = QPointF()
        self._start_rect = QRectF()
        self._start_pos = QPointF()
        self._geo_before: GeoState | None = None
        self._updating = False
        self.refresh()

    def attach_overlays(self, scene: QGraphicsScene) -> None:
        self._caption.attach(scene)
        self._idle_border.attach(scene)
        self.refresh()

    def detach_overlays(self, scene: QGraphicsScene) -> None:
        self._caption.detach(scene)
        self._idle_border.detach(scene)

    def set_box_style(
        self, *, show_border: bool | None = None, show_fill: bool | None = None
    ) -> None:
        if show_border is not None:
            self.show_box_border = bool(show_border)
        if show_fill is not None:
            self.show_box_fill = bool(show_fill)
        self.refresh()

    def set_show_element_labels(self, show: bool) -> None:
        self.show_element_labels = bool(show)
        self.refresh()

    def set_label_font_size(self, size: int) -> None:
        self.label_font_size = clamp_label_font_size(size)
        self.refresh()

    def refresh(self) -> None:
        self._updating = True
        self.setRect(0, 0, max(self.region.width, 1), max(self.region.height, 1))
        self.setPos(self.region.x, self.region.y)
        selected = self.isSelected()
        if selected:
            pen = QPen(SEL_BLUE, 2.0)
            pen.setCosmetic(True)
            self.setPen(pen)
            self.setBrush(
                QBrush(SEL_FILL_ATLAS)
                if self.show_box_fill
                else QBrush(Qt.BrushStyle.NoBrush)
            )
        else:
            self.setPen(QPen(Qt.PenStyle.NoPen))
            self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        # Idle yellow border is scene-level (above sheet content), like UI editor.
        self._idle_border.sync(
            item_pos=self.pos(),
            width=self.region.width,
            height=self.region.height,
            show=bool(self.show_box_border and not selected),
        )
        # Grey captions hide while selected — focus caption is scene-owned.
        self._caption.apply(
            text=self.region.atlas_id,
            item_pos=self.pos(),
            visible=bool(self.show_element_labels and not selected),
            font_size=self.label_font_size,
        )
        self.setToolTip(
            f"{self.region.atlas_id}\n"
            f"{int(self.region.x)},{int(self.region.y)} "
            f"{int(self.region.width)}×{int(self.region.height)}"
        )
        self._updating = False
        scene = self.scene()
        if isinstance(scene, DescrScene):
            scene.sync_focus_caption()

    def _hit_handle(self, pos: QPointF) -> str | None:
        r = self.rect()
        x, y = pos.x(), pos.y()
        near_l = abs(x - r.left()) <= HANDLE
        near_r = abs(x - r.right()) <= HANDLE
        near_t = abs(y - r.top()) <= HANDLE
        near_b = abs(y - r.bottom()) <= HANDLE
        if near_t and near_l:
            return "tl"
        if near_t and near_r:
            return "tr"
        if near_b and near_l:
            return "bl"
        if near_b and near_r:
            return "br"
        if near_t:
            return "t"
        if near_b:
            return "b"
        if near_l:
            return "l"
        if near_r:
            return "r"
        return None

    def hoverMoveEvent(self, event) -> None:  # noqa: N802
        corner = self._hit_handle(event.pos())
        cursors = {
            "tl": Qt.CursorShape.SizeFDiagCursor,
            "br": Qt.CursorShape.SizeFDiagCursor,
            "tr": Qt.CursorShape.SizeBDiagCursor,
            "bl": Qt.CursorShape.SizeBDiagCursor,
            "t": Qt.CursorShape.SizeVerCursor,
            "b": Qt.CursorShape.SizeVerCursor,
            "l": Qt.CursorShape.SizeHorCursor,
            "r": Qt.CursorShape.SizeHorCursor,
        }
        if corner:
            self.setCursor(cursors[corner])
        else:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # noqa: N802
        self.unsetCursor()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._geo_before = GeoState(
                path=self.region.atlas_id,
                x=self.region.x,
                y=self.region.y,
                width=self.region.width,
                height=self.region.height,
            )
            corner = self._hit_handle(event.pos())
            if corner:
                self._resizing = True
                self._resize_corner = corner
                self._resize_start = event.scenePos()
                self._start_rect = QRectF(
                    self.region.x, self.region.y, self.region.width, self.region.height
                )
                self._start_pos = QPointF(self.region.x, self.region.y)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._resizing and self._resize_corner:
            delta = event.scenePos() - self._resize_start
            r = QRectF(self._start_rect)
            c = self._resize_corner
            if "l" in c:
                r.setLeft(r.left() + delta.x())
            if "r" in c:
                r.setRight(r.right() + delta.x())
            if "t" in c:
                r.setTop(r.top() + delta.y())
            if "b" in c:
                r.setBottom(r.bottom() + delta.y())
            if r.width() < MIN_SIZE:
                if "l" in c:
                    r.setLeft(r.right() - MIN_SIZE)
                else:
                    r.setRight(r.left() + MIN_SIZE)
            if r.height() < MIN_SIZE:
                if "t" in c:
                    r.setTop(r.bottom() - MIN_SIZE)
                else:
                    r.setBottom(r.top() + MIN_SIZE)
            scene = self.scene()
            if isinstance(scene, DescrScene):
                r = r.intersected(QRectF(0, 0, scene.sheet_w, scene.sheet_h))
            self.region.set_geometry(
                x=r.x(), y=r.y(), width=r.width(), height=r.height()
            )
            self.refresh()
            if isinstance(scene, DescrScene):
                scene.geometry_changed.emit(self.region)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._resizing = False
        self._resize_corner = None
        super().mouseReleaseEvent(event)
        self._commit_geo()

    def itemChange(self, change, value):  # noqa: N802
        if (
            change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged
            and not self._updating
            and not self._resizing
        ):
            pos = self.pos()
            scene = self.scene()
            x, y = pos.x(), pos.y()
            if isinstance(scene, DescrScene):
                x = max(0.0, min(x, scene.sheet_w - self.region.width))
                y = max(0.0, min(y, scene.sheet_h - self.region.height))
            self.region.set_geometry(x=x, y=y)
            if abs(x - pos.x()) > 0.01 or abs(y - pos.y()) > 0.01:
                self._updating = True
                self.setPos(x, y)
                self._updating = False
            if isinstance(scene, DescrScene):
                scene.geometry_changed.emit(self.region)
            self._caption.apply(
                text=self.region.atlas_id,
                item_pos=self.pos(),
                visible=bool(self.show_element_labels and not self.isSelected()),
                font_size=self.label_font_size,
            )
            self._idle_border.sync(
                item_pos=self.pos(),
                width=self.region.width,
                height=self.region.height,
                show=bool(self.show_box_border and not self.isSelected()),
            )
            if isinstance(scene, DescrScene):
                scene.sync_focus_caption()
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.refresh()
        return super().itemChange(change, value)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        scene = self.scene()
        if isinstance(scene, DescrScene):
            scene.request_rename.emit(self.region)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _commit_geo(self) -> None:
        if self._geo_before is None:
            return
        after = GeoState(
            path=self.region.atlas_id,
            x=self.region.x,
            y=self.region.y,
            width=self.region.width,
            height=self.region.height,
        )
        edit = GeoEdit.single(self._geo_before, after)
        self._geo_before = None
        if not edit.changed():
            return
        scene = self.scene()
        if isinstance(scene, DescrScene):
            scene.push_geo_edit(edit)
            if scene.doc is not None:
                scene.doc.mark_dirty()


class DescrScene(QGraphicsScene):
    geometry_changed = pyqtSignal(object)  # DescrRegion
    selection_changed_region = pyqtSignal(object)  # DescrRegion | None
    request_rename = pyqtSignal(object)  # DescrRegion
    undo_stack_changed = pyqtSignal()
    sheet_missing = pyqtSignal(str)

    def __init__(self, resolver: TextureResolver) -> None:
        super().__init__()
        self.resolver = resolver
        self.doc: DescrDocument | None = None
        self.sheet: DescrSheet | None = None
        self.sheet_w = 256.0
        self.sheet_h = 256.0
        self.dds_path: Path | None = None
        self.undo_stack = UndoStack()
        self._items: dict[str, RegionItem] = {}
        self.show_box_border = False
        self.show_box_fill = False
        self.show_element_labels = False
        self.label_font_size = 5
        self._bg = QGraphicsPixmapItem()
        self._bg.setZValue(-100)
        self._bg.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.addItem(self._bg)
        self._frame = QGraphicsRectItem()
        self._frame.setPen(QPen(QColor(90, 90, 100), 1))
        self._frame.setBrush(QBrush(QColor(28, 28, 32)))
        self._frame.setZValue(-200)
        self._frame.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.addItem(self._frame)
        self._focus_caption = FocusCaptionOverlay()
        self._focus_caption.setZValue(FOCUS_LABEL_Z)
        self.addItem(self._focus_caption)
        self._pixel_grid = PixelGridItem(self.sheet_w, self.sheet_h)
        self.addItem(self._pixel_grid)
        self.selectionChanged.connect(self._on_sel)

    def set_pixel_grid_visible(self, visible: bool) -> None:
        self._pixel_grid.setVisible(bool(visible))

    def set_pixel_grid_step(self, step: int) -> None:
        self._pixel_grid.set_step(step)

    def _make_item(self, region: DescrRegion) -> RegionItem:
        return RegionItem(
            region,
            show_box_border=self.show_box_border,
            show_box_fill=self.show_box_fill,
            show_element_labels=self.show_element_labels,
            label_font_size=self.label_font_size,
        )

    def _add_item(self, region: DescrRegion) -> RegionItem:
        item = self._make_item(region)
        self.addItem(item)
        item.attach_overlays(self)
        self._items[region.atlas_id] = item
        return item

    def _remove_item(self, item: RegionItem) -> None:
        item.detach_overlays(self)
        self.removeItem(item)

    def sync_focus_caption(self) -> None:
        reg = self.selected_region()
        if reg is None:
            self._focus_caption.clear()
            return
        item = self._items.get(reg.atlas_id)
        if item is None:
            self._focus_caption.clear()
            return
        self._focus_caption.bind_above(
            item_pos=item.pos(),
            text=reg.atlas_id,
            font_size=self.label_font_size,
            text_color=SELECT_LABEL_TEXT,
        )

    def set_box_style(
        self, *, show_border: bool | None = None, show_fill: bool | None = None
    ) -> None:
        if show_border is not None:
            self.show_box_border = bool(show_border)
        if show_fill is not None:
            self.show_box_fill = bool(show_fill)
        for item in self._items.values():
            item.set_box_style(show_border=show_border, show_fill=show_fill)

    def set_show_element_labels(self, show: bool) -> None:
        self.show_element_labels = bool(show)
        for item in self._items.values():
            item.set_show_element_labels(show)

    def set_label_font_size(self, size: int) -> None:
        self.label_font_size = clamp_label_font_size(size)
        for item in self._items.values():
            item.set_label_font_size(self.label_font_size)
        self.sync_focus_caption()

    def push_geo_edit(self, edit: GeoEdit) -> None:
        self.undo_stack.push(edit)
        self.undo_stack_changed.emit()

    def clear_undo(self) -> None:
        self.undo_stack.clear()
        self.undo_stack_changed.emit()

    def _on_sel(self) -> None:
        for item in self.selectedItems():
            if isinstance(item, RegionItem):
                item.refresh()
                for other in self._items.values():
                    if other is not item:
                        other.refresh()
                self.sync_focus_caption()
                self.selection_changed_region.emit(item.region)
                return
        for item in self._items.values():
            item.refresh()
        self.sync_focus_caption()
        self.selection_changed_region.emit(None)

    def selected_region(self) -> DescrRegion | None:
        regs = self.selected_regions()
        return regs[-1] if regs else None

    def selected_regions(self) -> list[DescrRegion]:
        out: list[DescrRegion] = []
        for item in self.selectedItems():
            if isinstance(item, RegionItem):
                out.append(item.region)
        return out

    def set_sheet(self, doc: DescrDocument, sheet: DescrSheet | None) -> None:
        self.doc = doc
        self.sheet = sheet
        for item in list(self._items.values()):
            self._remove_item(item)
        self._items.clear()
        self.clear_undo()
        self._focus_caption.clear()

        if sheet is None:
            self.sheet_w, self.sheet_h = 256.0, 256.0
            self._bg.setPixmap(QPixmap())
            self._frame.setRect(0, 0, self.sheet_w, self.sheet_h)
            self._pixel_grid.set_grid_size(self.sheet_w, self.sheet_h)
            self.setSceneRect(-64, -64, self.sheet_w + 128, self.sheet_h + 128)
            self.dds_path = None
            return

        dds = self.resolver.find_dds(sheet.file_name)
        self.dds_path = dds
        if dds is None:
            self.sheet_w, self.sheet_h = 512.0, 512.0
            self._bg.setPixmap(QPixmap())
            self.sheet_missing.emit(sheet.file_name)
        else:
            try:
                img = Image.open(dds)
                img.load()
                pix = _pil_to_pixmap(img.convert("RGBA"))
                self.sheet_w = float(pix.width())
                self.sheet_h = float(pix.height())
                self._bg.setPixmap(pix)
            except OSError:
                self.sheet_w, self.sheet_h = 512.0, 512.0
                self._bg.setPixmap(QPixmap())
                self.sheet_missing.emit(sheet.file_name)

        self._frame.setRect(0, 0, self.sheet_w, self.sheet_h)
        self._pixel_grid.set_grid_size(self.sheet_w, self.sheet_h)
        self._bg.setPos(0, 0)
        self.setSceneRect(-64, -64, self.sheet_w + 128, self.sheet_h + 128)

        for reg in sheet.regions:
            self._add_item(reg)
        self.sync_focus_caption()

    def refresh_item(self, region: DescrRegion) -> None:
        item: RegionItem | None = None
        for key, it in list(self._items.items()):
            if it.region is region:
                if key != region.atlas_id:
                    del self._items[key]
                    self._items[region.atlas_id] = it
                item = it
                break
        if item is None:
            for it in self.items():
                if isinstance(it, RegionItem) and it.region is region:
                    item = it
                    self._items[region.atlas_id] = it
                    break
        if item is not None:
            item.refresh()
            self.sync_focus_caption()

    def apply_geo_edit(self, edit: GeoEdit, *, use_after: bool) -> DescrRegion | None:
        last: DescrRegion | None = None
        for before, after in edit.parts:
            state = after if use_after else before
            # path stored as atlas_id; may have been renamed — try current keys
            reg = None
            if self.doc is not None:
                reg = self.doc.find_region(state.path)
            if reg is None and self.sheet is not None:
                # Match by current item geo path from before
                for item in self._items.values():
                    if item.region.atlas_id == before.path or item.region.atlas_id == after.path:
                        reg = item.region
                        break
            if reg is None:
                continue
            reg.set_geometry(
                x=state.x, y=state.y, width=state.width, height=state.height
            )
            self.refresh_item(reg)
            last = reg
        return last

    def select_region(self, region: DescrRegion | None) -> None:
        self.select_regions([] if region is None else [region])

    def select_regions(self, regions: list) -> None:
        """Replace selection with the given atlas regions."""
        self.clearSelection()
        want = {id(r) for r in regions}
        for item in self._items.values():
            if id(item.region) in want:
                item.setSelected(True)

    def add_region_at(self, x: float, y: float, w: float = 32.0, h: float = 32.0) -> DescrRegion | None:
        if self.doc is None or self.sheet is None:
            return None
        base = "ui_new_texture"
        n = 1
        atlas_id = base
        while self.doc.id_exists(atlas_id):
            n += 1
            atlas_id = f"{base}_{n}"
        w = min(w, max(1.0, self.sheet_w - x))
        h = min(h, max(1.0, self.sheet_h - y))
        reg = self.doc.add_region(
            self.sheet, atlas_id=atlas_id, x=x, y=y, width=w, height=h
        )
        item = self._add_item(reg)
        self.clearSelection()
        item.setSelected(True)
        return reg

    def remove_selected(self) -> bool:
        if self.doc is None or self.sheet is None:
            return False
        reg = self.selected_region()
        if reg is None:
            return False
        item = self._items.pop(reg.atlas_id, None)
        if item is not None:
            self._remove_item(item)
        self.doc.remove_region(self.sheet, reg)
        self.sync_focus_caption()
        return True

    def duplicate_selected(self) -> DescrRegion | None:
        if self.doc is None or self.sheet is None:
            return None
        reg = self.selected_region()
        if reg is None:
            return None
        new_reg = self.doc.duplicate_region(self.sheet, reg)
        item = self._add_item(new_reg)
        self.clearSelection()
        item.setSelected(True)
        return new_reg


class DescrView(QGraphicsView):
    view_changed = pyqtSignal()

    def __init__(self, scene: DescrScene) -> None:
        super().__init__(scene)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QBrush(QColor(18, 18, 20)))
        self._panning = False
        self._pan_start = QPointF()
        self._nudge_key: int | None = None
        self._nudge_dx = 0
        self._nudge_dy = 0
        self._nudge_items: list[RegionItem] = []
        self._nudge_befores: dict[RegionItem, GeoState] = {}
        self._nudge_delay = QTimer(self)
        self._nudge_delay.setSingleShot(True)
        self._nudge_delay.timeout.connect(self._on_nudge_delay)
        self._nudge_repeat = QTimer(self)
        self._nudge_repeat.setInterval(_NUDGE_REPEAT_MS)
        self._nudge_repeat.timeout.connect(self._apply_nudge_step)
        self._coords = CursorCoordsHud(self)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        scene.selectionChanged.connect(self._on_selection_changed_nudge)

    def handle_nudge_key_press(self, event) -> bool:
        """Arrow nudge. Returns True if the event was consumed."""
        delta = _arrow_nudge_delta(event.key())
        if delta is None:
            return False
        if event.isAutoRepeat():
            return self._nudge_key == event.key()
        scene = self.scene()
        if not isinstance(scene, DescrScene):
            return False
        items = [
            i
            for i in scene.selectedItems()
            if isinstance(i, RegionItem) and i.isVisible()
        ]
        if not items:
            return False
        if self._nudge_key is not None:
            self.end_nudge()
        self._nudge_key = event.key()
        self._nudge_dx, self._nudge_dy = delta
        self._nudge_items = items
        self._nudge_befores = {
            item: GeoState(
                path=item.region.atlas_id,
                x=item.region.x,
                y=item.region.y,
                width=item.region.width,
                height=item.region.height,
            )
            for item in items
        }
        self._apply_nudge_step()
        self._nudge_delay.start(_NUDGE_DELAY_MS)
        return True

    def handle_nudge_key_release(self, event) -> bool:
        if _arrow_nudge_delta(event.key()) is None:
            return False
        if event.isAutoRepeat():
            return self._nudge_key == event.key()
        if self._nudge_key != event.key():
            return False
        self.end_nudge()
        return True

    def end_nudge(self) -> None:
        self._nudge_delay.stop()
        self._nudge_repeat.stop()
        items = self._nudge_items
        befores = self._nudge_befores
        self._nudge_key = None
        self._nudge_dx = 0
        self._nudge_dy = 0
        self._nudge_items = []
        self._nudge_befores = {}
        if not items:
            return
        pairs: list[tuple[GeoState, GeoState]] = []
        for item in items:
            before = befores.get(item)
            if before is None:
                continue
            after = GeoState(
                path=item.region.atlas_id,
                x=item.region.x,
                y=item.region.y,
                width=item.region.width,
                height=item.region.height,
            )
            if before != after:
                pairs.append((before, after))
        scene = self.scene()
        if not isinstance(scene, DescrScene):
            return
        if pairs:
            scene.push_geo_edit(GeoEdit.multi(pairs))
            if scene.doc is not None:
                scene.doc.mark_dirty()
        scene.geometry_changed.emit(items[-1].region)

    def _on_nudge_delay(self) -> None:
        if self._nudge_key is None:
            return
        self._apply_nudge_step()
        self._nudge_repeat.start()

    def _apply_nudge_step(self) -> None:
        if self._nudge_key is None or not self._nudge_items:
            return
        scene = self.scene()
        if not isinstance(scene, DescrScene):
            return
        dx, dy = float(self._nudge_dx), float(self._nudge_dy)
        for item in self._nudge_items:
            x = item.region.x + dx
            y = item.region.y + dy
            x = max(0.0, min(x, scene.sheet_w - item.region.width))
            y = max(0.0, min(y, scene.sheet_h - item.region.height))
            item.region.set_geometry(x=x, y=y)
            item.refresh()
        scene.sync_focus_caption()

    def _on_selection_changed_nudge(self) -> None:
        if self._nudge_key is not None:
            self.end_nudge()

    def fit_stage(self) -> None:
        scene = self.scene()
        if not isinstance(scene, DescrScene):
            return
        self.fitInView(
            QRectF(0, 0, scene.sheet_w, scene.sheet_h),
            Qt.AspectRatioMode.KeepAspectRatio,
        )
        self.view_changed.emit()

    def zoom_scale(self) -> float:
        return abs(float(self.transform().m11()))

    def set_zoom_scale(self, scale: float) -> None:
        try:
            scale = float(scale)
        except (TypeError, ValueError):
            return
        if scale <= 0:
            return
        scale = max(ZOOM_SCALE_MIN, min(scale, ZOOM_SCALE_MAX))
        center = self.mapToScene(self.viewport().rect().center())
        self.resetTransform()
        self.scale(scale, scale)
        self.centerOn(center)
        self.view_changed.emit()

    def _zoom_by_factor(self, factor: float) -> None:
        if factor <= 0:
            return
        current = self.zoom_scale()
        target = max(ZOOM_SCALE_MIN, min(current * factor, ZOOM_SCALE_MAX))
        if abs(target - current) < 1e-9:
            return
        self.scale(target / current, target / current)
        self.view_changed.emit()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self._zoom_by_factor(factor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton) or (
            event.button() == Qt.MouseButton.LeftButton
            and event.modifiers() & Qt.KeyboardModifier.AltModifier
        ):
            self._panning = True
            self._pan_start = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        scene_pos = self.mapToScene(event.position().toPoint())
        # Prefer float-accurate map when available.
        inverted, ok = self.viewportTransform().inverted()
        if ok:
            scene_pos = inverted.map(QPointF(event.position()))
        self._coords.update_scene_pos(scene_pos)
        if self._panning:
            delta = event.position() - self._pan_start
            self._pan_start = event.position()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - int(delta.x())
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - int(delta.y())
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._panning:
            self._panning = False
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class DescrBoard(QWidget):
    """Sheet combo + graphics view for atlas editing."""

    geometry_changed = pyqtSignal(object)
    selection_changed = pyqtSignal(object)
    document_dirty = pyqtSignal()
    undo_stack_changed = pyqtSignal()
    status_message = pyqtSignal(str)
    sheet_changed = pyqtSignal()
    regions_changed = pyqtSignal()

    def __init__(self, resolver: TextureResolver, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.resolver = resolver
        self.doc = DescrDocument()
        self.scene = DescrScene(resolver)
        self.view = DescrView(self.scene)

        self.sheet_label = QLabel("")
        self.sheet_label.setStyleSheet("color: #9a9a9a; margin-left: 5px;")

        sheet_row = QHBoxLayout()
        sheet_row.setContentsMargins(5, 0, 0, 0)
        sheet_row.setSpacing(8)
        sheet_title = QLabel("Sheet")
        sheet_title.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.sheet_combo = QComboBox()
        self.sheet_combo.setMaximumWidth(300)
        self.sheet_combo.setMinimumWidth(160)
        self.sheet_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.sheet_combo.setMinimumContentsLength(12)
        self.sheet_combo.currentIndexChanged.connect(self._on_sheet_combo)
        sheet_row.addWidget(sheet_title)
        sheet_row.addWidget(self.sheet_combo, stretch=0)
        sheet_row.addStretch(1)

        header = QVBoxLayout()
        header.setContentsMargins(0, 2, 0, 2)
        header.setSpacing(2)
        header.addWidget(self.sheet_label)
        header.addLayout(sheet_row)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addLayout(header)
        lay.addWidget(self.view, stretch=1)

        self.scene.geometry_changed.connect(self._on_geo)
        self.scene.selection_changed_region.connect(self.selection_changed.emit)
        self.scene.request_rename.connect(self._rename_region)
        self.scene.undo_stack_changed.connect(self.undo_stack_changed.emit)
        self.scene.sheet_missing.connect(self._on_sheet_missing)
        self._block_combo = False

    def _on_geo(self, region: DescrRegion) -> None:
        self.doc.mark_dirty()
        self.document_dirty.emit()
        self.geometry_changed.emit(region)
        self.status_message.emit(
            f"{region.atlas_id} · {int(region.x)},{int(region.y)} "
            f"{int(region.width)}×{int(region.height)}"
        )

    def _on_sheet_missing(self, file_name: str) -> None:
        self.status_message.emit(f"Missing DDS for sheet: {file_name}")

    def set_document(self, doc: DescrDocument) -> None:
        self.doc = doc
        self._block_combo = True
        self.sheet_combo.clear()
        for sheet in doc.sheets:
            self.sheet_combo.addItem(sheet.file_name, sheet)
        self._block_combo = False
        if doc.sheets:
            self._show_sheet(doc.sheets[0])
            self.sheet_combo.setCurrentIndex(0)
        else:
            self.scene.set_sheet(doc, None)
            self.sheet_label.setText("(no sheets)")
            self.sheet_changed.emit()

    def _on_sheet_combo(self, index: int) -> None:
        if self._block_combo or index < 0:
            return
        sheet = self.sheet_combo.itemData(index)
        if isinstance(sheet, DescrSheet):
            self._show_sheet(sheet)

    def _show_sheet(self, sheet: DescrSheet) -> None:
        self.scene.set_sheet(self.doc, sheet)
        dds = self.scene.dds_path
        if dds is not None:
            self.sheet_label.setText(
                f"{int(self.scene.sheet_w)}×{int(self.scene.sheet_h)} · {dds.name}"
            )
        else:
            self.sheet_label.setText(f"missing · {sheet.file_name}")
        self.view.fit_stage()
        self.sheet_changed.emit()

    def current_sheet(self) -> DescrSheet | None:
        return self.scene.sheet

    def fit_stage(self) -> None:
        self.view.fit_stage()

    def _rename_region(self, region: DescrRegion) -> None:
        new_id, ok = QInputDialog.getText(
            self, "Rename atlas id", "Atlas id:", text=region.atlas_id
        )
        if not ok:
            return
        new_id = new_id.strip()
        if not new_id or new_id == region.atlas_id:
            return
        if self.doc.id_exists(new_id, except_region=region):
            QMessageBox.warning(self, "Duplicate id", f"Id already used: {new_id}")
            return
        old = region.atlas_id
        region.set_id(new_id)
        # Fix item map key
        item = self.scene._items.pop(old, None)
        if item is not None:
            self.scene._items[new_id] = item
            item.refresh()
        self.doc.mark_dirty()
        self.document_dirty.emit()
        self.regions_changed.emit()
        self.selection_changed.emit(region)
        self.status_message.emit(f"Renamed {old} → {new_id}")

    def rename_selected(self) -> None:
        reg = self.scene.selected_region()
        if reg is not None:
            self._rename_region(reg)

    def delete_selected(self) -> None:
        if self.scene.remove_selected():
            self.document_dirty.emit()
            self.regions_changed.emit()
            self.selection_changed.emit(None)

    def duplicate_selected(self) -> None:
        if self.scene.duplicate_selected():
            self.document_dirty.emit()
            self.regions_changed.emit()

    def add_region(self) -> None:
        # Center of view
        center = self.view.mapToScene(self.view.viewport().rect().center())
        x = max(0.0, center.x() - 16)
        y = max(0.0, center.y() - 16)
        if self.scene.add_region_at(x, y):
            self.document_dirty.emit()
            self.regions_changed.emit()
