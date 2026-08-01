"""DDS-pixel canvas for editing textures_descr UV regions."""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
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
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from .descr_model import DescrDocument, DescrRegion, DescrSheet
from .textures import TextureResolver
from .undo import GeoEdit, GeoState, UndoStack

HANDLE = 8.0
MIN_SIZE = 2.0
SEL_BLUE = QColor(40, 130, 255)
SEL_FILL = QColor(40, 130, 255, 40)
IDLE_PEN = QColor(220, 200, 60, 200)
HOVER_PEN = QColor(255, 255, 255)


def _pil_to_pixmap(img: Image.Image) -> QPixmap:
    img = img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    qimg = QImage(
        data, img.width, img.height, img.width * 4, QImage.Format.Format_RGBA8888
    ).copy()
    return QPixmap.fromImage(qimg)


class RegionItem(QGraphicsRectItem):
    """Editable UV box on the atlas sheet."""

    def __init__(self, region: DescrRegion) -> None:
        super().__init__(0, 0, max(region.width, 1), max(region.height, 1))
        self.region = region
        self.setPos(region.x, region.y)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.setPen(QPen(IDLE_PEN, 1.0))
        self.setBrush(QBrush(QColor(0, 0, 0, 0)))
        self._label = QGraphicsSimpleTextItem(region.atlas_id, self)
        self._label.setBrush(QBrush(QColor(240, 240, 245)))
        self._label.setFont(QFont("Segoe UI", 8))
        self._label.setPos(2, 2)
        self._label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._resizing = False
        self._resize_corner: str | None = None
        self._resize_start = QPointF()
        self._start_rect = QRectF()
        self._start_pos = QPointF()
        self._geo_before: GeoState | None = None
        self._updating = False
        self.refresh()

    def refresh(self) -> None:
        self._updating = True
        self.setRect(0, 0, max(self.region.width, 1), max(self.region.height, 1))
        self.setPos(self.region.x, self.region.y)
        self._label.setText(self.region.atlas_id)
        selected = self.isSelected()
        if selected:
            self.setPen(QPen(SEL_BLUE, 2.0))
            self.setBrush(QBrush(SEL_FILL))
        else:
            self.setPen(QPen(IDLE_PEN, 1.0))
            self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.setToolTip(
            f"{self.region.atlas_id}\n"
            f"{int(self.region.x)},{int(self.region.y)} "
            f"{int(self.region.width)}×{int(self.region.height)}"
        )
        self._updating = False

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
        self.selectionChanged.connect(self._on_sel)

    def push_geo_edit(self, edit: GeoEdit) -> None:
        self.undo_stack.push(edit)
        self.undo_stack_changed.emit()

    def clear_undo(self) -> None:
        self.undo_stack.clear()
        self.undo_stack_changed.emit()

    def _on_sel(self) -> None:
        for item in self.selectedItems():
            if isinstance(item, RegionItem):
                self.selection_changed_region.emit(item.region)
                return
        self.selection_changed_region.emit(None)

    def selected_region(self) -> DescrRegion | None:
        for item in self.selectedItems():
            if isinstance(item, RegionItem):
                return item.region
        return None

    def set_sheet(self, doc: DescrDocument, sheet: DescrSheet | None) -> None:
        self.doc = doc
        self.sheet = sheet
        for item in list(self._items.values()):
            self.removeItem(item)
        self._items.clear()
        self.clear_undo()

        if sheet is None:
            self.sheet_w, self.sheet_h = 256.0, 256.0
            self._bg.setPixmap(QPixmap())
            self._frame.setRect(0, 0, self.sheet_w, self.sheet_h)
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
        self._bg.setPos(0, 0)
        self.setSceneRect(-64, -64, self.sheet_w + 128, self.sheet_h + 128)

        for reg in sheet.regions:
            item = RegionItem(reg)
            self.addItem(item)
            self._items[reg.atlas_id] = item

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
        self.clearSelection()
        if region is None:
            return
        for item in self._items.values():
            if item.region is region:
                item.setSelected(True)
                break

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
        item = RegionItem(reg)
        self.addItem(item)
        self._items[reg.atlas_id] = item
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
            self.removeItem(item)
        self.doc.remove_region(self.sheet, reg)
        return True

    def duplicate_selected(self) -> DescrRegion | None:
        if self.doc is None or self.sheet is None:
            return None
        reg = self.selected_region()
        if reg is None:
            return None
        new_reg = self.doc.duplicate_region(self.sheet, reg)
        item = RegionItem(new_reg)
        self.addItem(item)
        self._items[new_reg.atlas_id] = item
        self.clearSelection()
        item.setSelected(True)
        return new_reg


class DescrView(QGraphicsView):
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
        self.setMouseTracking(True)

    def fit_stage(self) -> None:
        scene = self.scene()
        if not isinstance(scene, DescrScene):
            return
        self.fitInView(
            QRectF(0, 0, scene.sheet_w, scene.sheet_h),
            Qt.AspectRatioMode.KeepAspectRatio,
        )

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

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

    def __init__(self, resolver: TextureResolver, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.resolver = resolver
        self.doc = DescrDocument()
        self.scene = DescrScene(resolver)
        self.view = DescrView(self.scene)

        top = QHBoxLayout()
        top.addWidget(QLabel("Sheet"))
        self.sheet_combo = QComboBox()
        self.sheet_combo.currentIndexChanged.connect(self._on_sheet_combo)
        top.addWidget(self.sheet_combo, stretch=1)
        self.sheet_label = QLabel("")
        self.sheet_label.setStyleSheet("color: #9a9a9a;")
        top.addWidget(self.sheet_label)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addLayout(top)
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
        self.status_message.emit(f"Renamed {old} → {new_id}")

    def rename_selected(self) -> None:
        reg = self.scene.selected_region()
        if reg is not None:
            self._rename_region(reg)

    def delete_selected(self) -> None:
        if self.scene.remove_selected():
            self.document_dirty.emit()

    def duplicate_selected(self) -> None:
        if self.scene.duplicate_selected():
            self.document_dirty.emit()

    def add_region(self) -> None:
        # Center of view
        center = self.view.mapToScene(self.view.viewport().rect().center())
        x = max(0.0, center.x() - 16)
        y = max(0.0, center.y() - 16)
        if self.scene.add_region_at(x, y):
            self.document_dirty.emit()
