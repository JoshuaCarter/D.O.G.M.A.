"""QGraphicsView canvas for 1024x768 Stalker UI layout."""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QWheelEvent,
)
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QStyle,
    QStyleOptionGraphicsItem,
    QWidget,
)

from .model import LayoutNode
from .settings import LABEL_FONT_MIN, UI_HEIGHT, UI_WIDTH
from .textures import TextureResolver
from .undo import GeoEdit, GeoState, UndoStack


HANDLE = 8.0
MIN_SIZE = 4.0

# Selection / hover (shared with elements tree chrome)
SEL_CYAN = QColor(0, 220, 230)
SEL_CYAN_FILL = QColor(0, 200, 210, 70)
SEL_CYAN_HANDLE_EDGE = QColor(0, 140, 150)
HOVER_BLUE = QColor(50, 130, 255)
HOVER_BLUE_FILL = QColor(50, 130, 255, 90)
STACK_GREY = QColor(210, 210, 215, 220)
STACK_GREY_FILL = QColor(180, 180, 190, 70)
STACK_HANDLE = QColor(255, 255, 255, 220)
STACK_HANDLE_EDGE = QColor(255, 255, 255, 180)


def _path_under_section(node_path: str, section_path: str) -> bool:
    """True if node is the section itself or a descendant (slash paths)."""
    if not section_path:
        return False
    if node_path == section_path:
        return True
    return node_path.startswith(section_path + "/")


class WidgetItem(QGraphicsRectItem):
    """Drawable UI widget box with optional texture fill and resize handles."""

    def __init__(
        self,
        node: LayoutNode,
        resolver: TextureResolver,
        *,
        label_font_size: int = 8,
        show_element_labels: bool = True,
        center_element_labels: bool = False,
        show_box_border: bool = False,
        show_box_fill: bool = False,
    ) -> None:
        super().__init__(0, 0, max(node.width, 1), max(node.height, 1))
        self.node = node
        self.resolver = resolver
        self.label_font_size = max(LABEL_FONT_MIN, int(label_font_size))
        self.show_element_labels = show_element_labels
        self.center_element_labels = center_element_labels
        self.show_box_border = show_box_border
        self.show_box_fill = show_box_fill
        self._hovered = False
        self.setPos(node.abs_x, node.abs_y)
        flags = (
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        # Tag captions may extend past the box; never clip them.
        self.setFlags(flags)
        self.setAcceptHoverEvents(True)
        self._pixmap_item = QGraphicsPixmapItem(self)
        self._pixmap_item.setZValue(-2)
        self._pixmap_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._label_item = QGraphicsSimpleTextItem(self)
        self._label_item.setZValue(1)
        self._label_item.setBrush(QBrush(QColor(245, 245, 245)))
        self._label_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._label_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        # Shadow for readability on dark chrome
        self._label_shadow = QGraphicsSimpleTextItem(self)
        self._label_shadow.setZValue(0.5)
        self._label_shadow.setBrush(QBrush(QColor(0, 0, 0, 220)))
        self._label_shadow.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._label_shadow.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self._apply_label_font()
        self._missing = False
        self._resizing = False
        self._resize_corner: str | None = None
        self._resize_start = QPointF()
        self._start_rect = QRectF()
        self._start_pos = QPointF()
        self._updating = False
        self._geo_before: GeoState | None = None
        self._stack_peer = False  # overlapping alt under cursor (old grey selection)
        self.refresh_look()

    def set_label_font_size(self, size: int) -> None:
        self.label_font_size = max(LABEL_FONT_MIN, int(size))
        self._apply_label_font()
        self._apply_label()

    def set_show_element_labels(self, show: bool) -> None:
        self.show_element_labels = bool(show)
        self._apply_label()

    def set_center_element_labels(self, center: bool) -> None:
        self.center_element_labels = bool(center)
        self._apply_label()

    def _apply_label_font(self) -> None:
        font = QFont("Segoe UI", max(LABEL_FONT_MIN, self.label_font_size))
        self._label_item.setFont(font)
        self._label_shadow.setFont(font)

    def set_box_style(self, *, show_border: bool | None = None, show_fill: bool | None = None) -> None:
        if show_border is not None:
            self.show_box_border = show_border
        if show_fill is not None:
            self.show_box_fill = show_fill
        self.refresh_look()

    def hoverEnterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self.refresh_look()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self.refresh_look()
        super().hoverLeaveEvent(event)

    def refresh_look(self) -> None:
        self._updating = True
        self.setRect(0, 0, max(self.node.width, 1), max(self.node.height, 1))
        self.setPos(self.node.abs_x, self.node.abs_y)
        self.setVisible(self.node.visible)
        self._apply_texture()
        self._apply_label()
        selected = self.isSelected()
        peer = self._stack_peer and not selected
        # Toggles show all; when off, still preview the single hover hit-target
        draw_border = self.show_box_border or self._hovered or selected or peer
        draw_fill = self.show_box_fill
        if self.node.from_meta:
            # Diamond is painted in paint(); keep the rect itself invisible.
            self.setPen(QPen(Qt.PenStyle.NoPen))
            self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        elif draw_border:
            if selected:
                color = SEL_CYAN
            elif peer:
                color = STACK_GREY
            elif self._missing:
                color = QColor(220, 60, 60) if self._hovered else QColor(200, 80, 80, 220)
            elif self._hovered:
                color = HOVER_BLUE
            elif not self._pixmap_item.pixmap().isNull():
                color = QColor(220, 220, 100, 90)
            else:
                color = QColor(200, 200, 80, 180)
            pen = QPen(color)
            pen.setWidth(2 if selected else 1)
            pen.setCosmetic(True)
            self.setPen(pen)
        else:
            self.setPen(QPen(Qt.PenStyle.NoPen))

        if self.node.from_meta:
            pass
        elif selected and self._pixmap_item.pixmap().isNull():
            self.setBrush(QBrush(SEL_CYAN_FILL))
        elif peer and self._pixmap_item.pixmap().isNull():
            self.setBrush(QBrush(QColor(180, 180, 190, 40)))
        elif self._hovered and self._pixmap_item.pixmap().isNull():
            self.setBrush(QBrush(HOVER_BLUE_FILL))
        elif draw_fill and self._pixmap_item.pixmap().isNull():
            if self._missing:
                self.setBrush(QBrush(QColor(120, 30, 30, 90), Qt.BrushStyle.BDiagPattern))
            else:
                self.setBrush(QBrush(QColor(40, 60, 90, 50)))
        else:
            self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        tip = self.node.path or self.node.tag
        if self.node.from_meta:
            tip += "\n[meta handle - not in XML]"
        tip += f"\nabs ({int(self.node.abs_x)},{int(self.node.abs_y)}) "
        tip += f"{int(self.node.width)}×{int(self.node.height)}"
        if self.node.texture and self.node.texture.name:
            tip += f"\ntexture: {self.node.texture.name}"
            resolved = self.resolver.resolve_ref(self.node.texture)
            if resolved.atlas_id and resolved.atlas_id != self.node.texture.name:
                tip += f" → {resolved.atlas_id}"
            if resolved.error:
                tip += f"\n⚠ {resolved.error}"
            elif resolved.path:
                tip += f"\n✓ {resolved.path}"
        if self.node.text and self.node.text.content:
            tip += f"\ntext: {self.node.text.content}"
            # String resolution is owned by the app Log/Properties (StringResolver).
        self.setToolTip(tip)
        self._updating = False

    def _apply_texture(self) -> None:
        self._pixmap_item.setPixmap(QPixmap())
        self._missing = False
        if not self.node.texture:
            return
        resolved = self.resolver.resolve_ref(self.node.texture)
        if resolved.image is None:
            self._missing = bool(self.node.texture.name)
            return
        img = resolved.image
        data = img.tobytes("raw", "RGBA")
        qimg = QImage(data, img.width, img.height, img.width * 4, QImage.Format.Format_RGBA8888).copy()
        pix = QPixmap.fromImage(qimg)
        target_w = max(int(self.node.width), 1)
        target_h = max(int(self.node.height), 1)
        if self.node.stretch or True:
            # Preview: always fit texture into widget (stretch is the common case)
            pix = pix.scaled(
                target_w,
                target_h,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        self._pixmap_item.setPixmap(pix)
        self._pixmap_item.setPos(0, 0)

    def boundingRect(self) -> QRectF:  # noqa: N802
        r = super().boundingRect()
        if not self._label_item.isVisible():
            return r
        lr = self._label_item.mapRectToParent(self._label_item.boundingRect())
        sr = self._label_shadow.mapRectToParent(self._label_shadow.boundingRect())
        return r.united(lr).united(sr).adjusted(-1, -1, 2, 2)

    def shape(self):  # noqa: N802
        # Hit-test / select only the widget box (or meta diamond) - never the tag caption.
        path = QPainterPath()
        if self.node.from_meta:
            path.addPolygon(self._meta_diamond_poly())
        else:
            path.addRect(self.rect())
        return path

    def _meta_diamond_poly(self) -> QPolygonF:
        r = self.rect()
        cx = r.center().x()
        cy = r.center().y()
        hw = r.width() / 2
        hh = r.height() / 2
        return QPolygonF(
            [
                QPointF(cx, cy - hh),
                QPointF(cx + hw, cy),
                QPointF(cx, cy + hh),
                QPointF(cx - hw, cy),
            ]
        )

    def _apply_label(self) -> None:
        """Draw full element/tag name; caption is display-only (not hit-tested)."""
        self.prepareGeometryChange()
        tag = self.node.tag or self.node.path or ""
        if not tag or not self.show_element_labels:
            self._label_item.setText("")
            self._label_item.setVisible(False)
            self._label_shadow.setText("")
            self._label_shadow.setVisible(False)
            return
        self._apply_label_font()
        self._label_item.setText(tag)
        self._label_item.setVisible(True)
        self._label_shadow.setText(tag)
        self._label_shadow.setVisible(True)
        if self.node.from_meta:
            if self.isSelected():
                brush = SEL_CYAN
            elif self._stack_peer:
                brush = STACK_GREY
            elif self._hovered:
                brush = HOVER_BLUE
            else:
                brush = QColor(220, 220, 120)
            self._label_item.setBrush(QBrush(brush))
            br = self._label_item.boundingRect()
            if self.center_element_labels:
                r = self.rect()
                x = r.center().x() - (br.x() + br.width() / 2.0)
                y = r.center().y() - (br.y() + br.height() / 2.0)
            else:
                # Sit to the right of the diamond tip (not at the diamond's top-left).
                tip = self._meta_diamond_poly().at(1)  # right tip
                gap = 6.0
                x = tip.x() + gap - br.x()
                y = tip.y() - (br.y() + br.height() / 2.0)
        else:
            self._label_item.setBrush(QBrush(QColor(245, 245, 245)))
            br = self._label_item.boundingRect()
            r = self.rect()
            if self.center_element_labels:
                x = r.center().x() - (br.x() + br.width() / 2.0)
                y = r.center().y() - (br.y() + br.height() / 2.0)
            else:
                x = 2.0 - br.x()
                y = 1.0 - br.y()
        self._label_shadow.setPos(x + 1, y + 1)
        self._label_item.setPos(x, y)

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value):  # noqa: N802
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            # Repaint diamond/box colors on select (yellow → blue).
            self.update()
            self.refresh_look()
        if (
            change == QGraphicsItem.GraphicsItemChange.ItemPositionChange
            and not self._updating
            and not self._resizing
            and self.scene()
        ):
            p = value
            if isinstance(p, QPointF):
                return QPointF(round(p.x()), round(p.y()))
        if (
            change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged
            and not self._updating
            and not self._resizing
        ):
            self._sync_node_from_item()
            scene = self.scene()
            if isinstance(scene, UiScene):
                scene.geometry_changed.emit(self.node)
        return super().itemChange(change, value)

    def _sync_node_from_item(self) -> None:
        ox, oy = self.node.coord_origin()
        local_x = self.pos().x() - ox
        local_y = self.pos().y() - oy
        self.node.set_geometry(
            x=round(local_x),
            y=round(local_y),
            width=round(self.rect().width()),
            height=round(self.rect().height()),
        )
        root = self.node
        while root.parent is not None:
            root = root.parent
        root.recompute_absolute(0.0, 0.0)

    def _handle_at(self, pos: QPointF) -> str | None:
        if self.node.from_meta:
            return None  # diamond markers are move-only
        r = self.rect()
        # Corners first so they win near the intersections.
        handles = {
            "br": QPointF(r.right(), r.bottom()),
            "bl": QPointF(r.left(), r.bottom()),
            "tr": QPointF(r.right(), r.top()),
            "tl": QPointF(r.left(), r.top()),
            "t": QPointF(r.center().x(), r.top()),
            "b": QPointF(r.center().x(), r.bottom()),
            "l": QPointF(r.left(), r.center().y()),
            "r": QPointF(r.right(), r.center().y()),
        }
        for name, pt in handles.items():
            if abs(pos.x() - pt.x()) <= HANDLE and abs(pos.y() - pt.y()) <= HANDLE:
                return name
        return None

    def hoverMoveEvent(self, event) -> None:  # noqa: N802
        handle = self._handle_at(event.pos())
        cursors = {
            "br": Qt.CursorShape.SizeFDiagCursor,
            "tl": Qt.CursorShape.SizeFDiagCursor,
            "tr": Qt.CursorShape.SizeBDiagCursor,
            "bl": Qt.CursorShape.SizeBDiagCursor,
            "t": Qt.CursorShape.SizeVerCursor,
            "b": Qt.CursorShape.SizeVerCursor,
            "l": Qt.CursorShape.SizeHorCursor,
            "r": Qt.CursorShape.SizeHorCursor,
        }
        if handle:
            self.setCursor(cursors[handle])
        else:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        super().hoverMoveEvent(event)

    def _snapshot_geo(self) -> GeoState:
        return GeoState(
            path=self.node.path,
            x=self.node.x,
            y=self.node.y,
            width=self.node.width,
            height=self.node.height,
        )

    def _commit_geo_edit(self) -> None:
        if self._geo_before is None:
            return
        after = self._snapshot_geo()
        edit = GeoEdit(before=self._geo_before, after=after)
        self._geo_before = None
        if not edit.changed():
            return
        scene = self.scene()
        if isinstance(scene, UiScene):
            scene.push_geo_edit(edit)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._geo_before = self._snapshot_geo()
            corner = self._handle_at(event.pos())
            if corner:
                self._resizing = True
                self._resize_corner = corner
                self._resize_start = event.scenePos()
                self._start_rect = QRectF(self.rect())
                self._start_pos = QPointF(self.pos())
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._resizing and self._resize_corner:
            delta = event.scenePos() - self._resize_start
            r = QRectF(self._start_rect)
            pos = QPointF(self._start_pos)
            c = self._resize_corner
            if "r" in c:
                r.setWidth(max(MIN_SIZE, r.width() + delta.x()))
            if "l" in c:
                new_w = max(MIN_SIZE, r.width() - delta.x())
                pos.setX(self._start_pos.x() + (r.width() - new_w))
                r.setWidth(new_w)
            if "b" in c:
                r.setHeight(max(MIN_SIZE, r.height() + delta.y()))
            if "t" in c:
                new_h = max(MIN_SIZE, r.height() - delta.y())
                pos.setY(self._start_pos.y() + (r.height() - new_h))
                r.setHeight(new_h)
            self._updating = True
            self.setRect(0, 0, round(r.width()), round(r.height()))
            self.setPos(round(pos.x()), round(pos.y()))
            self._updating = False
            self._sync_node_from_item()
            self._apply_texture()
            scene = self.scene()
            if isinstance(scene, UiScene):
                scene.geometry_changed.emit(self.node)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._resizing:
            self._resizing = False
            self._resize_corner = None
            scene = self.scene()
            if isinstance(scene, UiScene):
                scene.refresh_item_positions()
                scene.geometry_changed.emit(self.node)
            self._commit_geo_edit()
            event.accept()
            return
        super().mouseReleaseEvent(event)
        self._commit_geo_edit()

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        if self.node.from_meta:
            # Cyan = selected; blue = hover; grey = stack peer; yellow = idle.
            if self.isSelected():
                color = SEL_CYAN
                fill = SEL_CYAN_FILL
                width = 2
            elif self._stack_peer:
                color = STACK_GREY
                fill = STACK_GREY_FILL
                width = 1
            elif self._hovered:
                color = HOVER_BLUE
                fill = HOVER_BLUE_FILL
                width = 1
            else:
                color = QColor(200, 200, 80, 220)
                fill = QColor(200, 200, 80, 70)
                width = 1
            pen = QPen(color)
            pen.setWidth(width)
            pen.setCosmetic(True)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(pen)
            painter.setBrush(QBrush(fill))
            painter.drawPolygon(self._meta_diamond_poly())
            return
        opt = QStyleOptionGraphicsItem(option)
        opt.state &= ~QStyle.StateFlag.State_Selected
        super().paint(painter, opt, widget)
        if self.isSelected():
            ring = QPen(SEL_CYAN, 2)
            ring.setCosmetic(True)
            painter.setPen(ring)
            painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            painter.drawRect(self.rect())
            painter.setPen(QPen(SEL_CYAN_HANDLE_EDGE, 1))
            painter.setBrush(QBrush(SEL_CYAN))
            self._draw_handles(painter)
        elif self._stack_peer:
            # Outline only - no white handles (those sat behind the selected cyan corners).
            ring = QPen(STACK_HANDLE_EDGE, 1)
            ring.setCosmetic(True)
            painter.setPen(ring)
            painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            painter.drawRect(self.rect())
        elif self._hovered:
            ring = QPen(HOVER_BLUE, 1)
            ring.setCosmetic(True)
            painter.setPen(ring)
            painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            painter.drawRect(self.rect())

    def _draw_handles(self, painter: QPainter) -> None:
        r = self.rect()
        hs = HANDLE / 2
        for pt in (
            QPointF(r.left(), r.top()),
            QPointF(r.right(), r.top()),
            QPointF(r.left(), r.bottom()),
            QPointF(r.right(), r.bottom()),
            QPointF(r.center().x(), r.top()),
            QPointF(r.center().x(), r.bottom()),
            QPointF(r.left(), r.center().y()),
            QPointF(r.right(), r.center().y()),
        ):
            painter.drawRect(QRectF(pt.x() - hs, pt.y() - hs, HANDLE, HANDLE))


class UiScene(QGraphicsScene):
    geometry_changed = pyqtSignal(object)  # LayoutNode
    selection_node_changed = pyqtSignal(object)  # LayoutNode | None
    undo_stack_changed = pyqtSignal()

    def __init__(
        self,
        resolver: TextureResolver,
        *,
        label_font_size: int = 8,
        show_element_labels: bool = True,
        center_element_labels: bool = False,
        show_box_border: bool = False,
        show_box_fill: bool = False,
    ) -> None:
        super().__init__(0, 0, UI_WIDTH, UI_HEIGHT)
        self.resolver = resolver
        self.label_font_size = max(LABEL_FONT_MIN, int(label_font_size))
        self.show_element_labels = show_element_labels
        self.center_element_labels = center_element_labels
        self.show_box_border = show_box_border
        self.show_box_fill = show_box_fill
        self.doc: LayoutNode | None = None
        self._items: dict[str, WidgetItem] = {}
        self._layer_visible: dict[str, bool] = {}
        self.undo_stack = UndoStack()
        self._stage = QGraphicsRectItem(0, 0, UI_WIDTH, UI_HEIGHT)
        self._stage.setBrush(QBrush(QColor(28, 28, 32)))
        self._stage.setPen(QPen(QColor(90, 90, 100), 2))
        self._stage.setZValue(-1000)
        self._stage.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self._stage.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.addItem(self._stage)
        self.selectionChanged.connect(self._on_selection_changed)

    def push_geo_edit(self, edit: GeoEdit) -> None:
        self.undo_stack.push(edit)
        self.undo_stack_changed.emit()

    def clear_undo(self) -> None:
        self.undo_stack.clear()
        self.undo_stack_changed.emit()

    def apply_geo_state(self, state: GeoState) -> LayoutNode | None:
        if self.doc is None:
            return None
        node = self.doc.find_by_path(state.path)
        if node is None:
            return None
        node.set_geometry(
            x=state.x,
            y=state.y,
            width=state.width,
            height=state.height,
        )
        self.doc.recompute_absolute(0.0, 0.0)
        self.refresh_item_positions()
        return node

    def set_label_font_size(self, size: int) -> None:
        self.label_font_size = max(LABEL_FONT_MIN, int(size))
        for item in self._items.values():
            item.set_label_font_size(self.label_font_size)

    def set_show_element_labels(self, show: bool) -> None:
        self.show_element_labels = bool(show)
        for item in self._items.values():
            item.set_show_element_labels(self.show_element_labels)

    def set_center_element_labels(self, center: bool) -> None:
        self.center_element_labels = bool(center)
        for item in self._items.values():
            item.set_center_element_labels(self.center_element_labels)

    def set_box_style(
        self,
        *,
        show_border: bool | None = None,
        show_fill: bool | None = None,
    ) -> None:
        if show_border is not None:
            self.show_box_border = show_border
        if show_fill is not None:
            self.show_box_fill = show_fill
        for item in self._items.values():
            item.set_box_style(
                show_border=self.show_box_border,
                show_fill=self.show_box_fill,
            )

    def set_document(self, doc: LayoutNode | None) -> None:
        for item in list(self._items.values()):
            self.removeItem(item)
        self._items.clear()
        self.clear_undo()
        self._layer_visible = {}
        self.doc = doc
        if doc is None:
            return
        drawables = doc.iter_drawables()
        # Depth primary (deeper above ancestors), document order as tie-break.
        ordered = sorted(
            enumerate(drawables),
            key=lambda pair: (pair[1].hierarchy_depth(), pair[0]),
        )
        for z, (_doc_index, node) in enumerate(ordered):
            item = WidgetItem(
                node,
                self.resolver,
                label_font_size=self.label_font_size,
                show_element_labels=self.show_element_labels,
                center_element_labels=self.center_element_labels,
                show_box_border=self.show_box_border,
                show_box_fill=self.show_box_fill,
            )
            item.setZValue(float(z))
            self.addItem(item)
            self._items[node.path] = item
        # Visibility applied after caller sets layer toggles via set_layer_states.

    def refresh_item_positions(self) -> None:
        for item in self._items.values():
            item.refresh_look()

    def set_layer_states(self, states: dict[str, bool]) -> None:
        """Replace layer toggle map and hard-apply visibility to all nodes."""
        self._layer_visible = {str(k): bool(v) for k, v in states.items()}
        self.apply_layer_visibility()

    def set_section_visibility(self, section_path: str, visible: bool) -> None:
        """Update one layer toggle, then recompute all node visibility from toggles."""
        if not section_path:
            return
        self._layer_visible[section_path] = bool(visible)
        self.apply_layer_visibility()

    def apply_layer_visibility(self) -> None:
        """Hard hide/show: a node is visible only if every containing layer is on."""
        if self.doc is None:
            return
        for node in self.doc.iter_all():
            if not node.path:
                continue
            node.visible = self._effective_layer_visible(node.path)
        self.refresh_item_positions()

    def _effective_layer_visible(self, node_path: str) -> bool:
        if not self._layer_visible:
            return True
        for layer_path, enabled in self._layer_visible.items():
            if not _path_under_section(node_path, layer_path):
                continue
            if not enabled:
                return False
        return True

    def select_path(self, path: str) -> None:
        item = self._items.get(path)
        self.clearSelection()
        if item:
            item.setSelected(True)
            for v in self.views():
                v.centerOn(item)

    def item_for_node(self, node: LayoutNode) -> WidgetItem | None:
        return self._items.get(node.path)

    def missing_texture_count(self) -> int:
        return sum(1 for i in self._items.values() if i._missing and i.isVisible())

    def textured_count(self) -> int:
        n = 0
        for i in self._items.values():
            if i.isVisible() and i.node.texture and not i._missing and not i._pixmap_item.pixmap().isNull():
                n += 1
        return n

    def _on_selection_changed(self) -> None:
        selected = [i for i in self.selectedItems() if isinstance(i, WidgetItem)]
        if selected:
            self.selection_node_changed.emit(selected[0].node)
        else:
            self.selection_node_changed.emit(None)

    def render_to_image(self, scale: float = 1.0) -> QImage:
        w = int(UI_WIDTH * scale)
        h = int(UI_HEIGHT * scale)
        image = QImage(w, h, QImage.Format.Format_ARGB32)
        image.fill(QColor(18, 18, 20))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.render(painter, QRectF(0, 0, w, h), QRectF(0, 0, UI_WIDTH, UI_HEIGHT))
        painter.end()
        return image


class UiCanvas(QGraphicsView):
    def __init__(self, scene: UiScene) -> None:
        super().__init__(scene)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setBackgroundBrush(QBrush(QColor(18, 18, 20)))
        self._panning = False
        self._pan_start = QPointF()
        self.scroll_select = False
        self._stack_peer_items: set[WidgetItem] = set()
        self.setMouseTracking(True)
        self.scene().selectionChanged.connect(self._on_selection_changed_stack)
        self.fit_stage()

    def set_scroll_select(self, enabled: bool) -> None:
        self.scroll_select = bool(enabled)

    def _on_selection_changed_stack(self) -> None:
        self._refresh_stack_peers()

    def _set_stack_peers(self, peers: set[WidgetItem]) -> None:
        old = self._stack_peer_items
        if old == peers:
            return
        for item in old - peers:
            item._stack_peer = False
            item.refresh_look()
        for item in peers - old:
            item._stack_peer = True
            item.refresh_look()
        self._stack_peer_items = peers

    def _refresh_stack_peers(self, view_pos: QPointF | None = None) -> None:
        """Grey-highlight overlapping widgets under the cursor (scroll-select stack)."""
        stack: list[WidgetItem] = []
        if view_pos is not None:
            stack = self._widget_items_at(view_pos)
        if len(stack) < 2:
            selected = [i for i in self.scene().selectedItems() if isinstance(i, WidgetItem)]
            if selected:
                center = selected[0].mapToScene(selected[0].rect().center())
                stack = self._widget_items_at(QPointF(self.mapFromScene(center)))
        if len(stack) < 2:
            self._set_stack_peers(set())
            return
        peers = {item for item in stack if not item.isSelected()}
        self._set_stack_peers(peers)

    def _update_pan_limits(self) -> None:
        """Allow panning until each stage edge reaches the opposite viewport edge."""
        vis = self.mapToScene(self.viewport().rect()).boundingRect()
        mw = max(float(vis.width()), 1.0)
        mh = max(float(vis.height()), 1.0)
        # One viewport of overscroll past each side of the 1024×768 stage.
        self.setSceneRect(-mw, -mh, UI_WIDTH + 2.0 * mw, UI_HEIGHT + 2.0 * mh)

    def fit_stage(self) -> None:
        self.fitInView(QRectF(0, 0, UI_WIDTH, UI_HEIGHT), Qt.AspectRatioMode.KeepAspectRatio)
        self._update_pan_limits()

    def view_state(self) -> dict[str, float]:
        t = self.transform()
        center = self.mapToScene(self.viewport().rect().center())
        return {
            "scale": float(t.m11()),
            "cx": float(center.x()),
            "cy": float(center.y()),
        }

    def restore_view_state(self, state: dict[str, float] | None) -> None:
        if not state:
            self.fit_stage()
            return
        try:
            scale = float(state["scale"])
            cx = float(state["cx"])
            cy = float(state["cy"])
        except (KeyError, TypeError, ValueError):
            self.fit_stage()
            return
        if scale <= 0:
            self.fit_stage()
            return
        self.resetTransform()
        self.scale(scale, scale)
        self._update_pan_limits()
        self.centerOn(cx, cy)
        self._update_pan_limits()

    def _widget_items_at(self, view_pos: QPointF) -> list[WidgetItem]:
        """Visible widget items under the cursor, topmost first."""
        pt = view_pos.toPoint()
        out: list[WidgetItem] = []
        for item in self.items(pt):
            if isinstance(item, WidgetItem) and item.isVisible():
                out.append(item)
        return out

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        # MMB / Alt-drag pan: ignore wheel (no zoom mid-pan).
        if self._panning:
            event.accept()
            return

        mods = event.modifiers()
        delta = event.angleDelta()
        dy = int(delta.y())
        dx = int(delta.x())
        if dy == 0 and dx == 0:
            pixel = event.pixelDelta()
            dy = int(pixel.y())
            dx = int(pixel.x())

        # Ctrl+scroll → zoom (overrides scroll-select)
        if mods & Qt.KeyboardModifier.ControlModifier:
            step = dy if dy != 0 else dx
            if step != 0:
                factor = 1.15 if step > 0 else 1 / 1.15
                self.scale(factor, factor)
                self._update_pan_limits()
            event.accept()
            return

        # Shift+scroll → pan left/right (overrides scroll-select)
        if mods & Qt.KeyboardModifier.ShiftModifier:
            step = dx if dx != 0 else dy
            if step != 0:
                bar = self.horizontalScrollBar()
                bar.setValue(bar.value() - step)
            event.accept()
            return

        # Alt+scroll → pan up/down (overrides scroll-select)
        if mods & Qt.KeyboardModifier.AltModifier:
            step = dy if dy != 0 else dx
            if step != 0:
                bar = self.verticalScrollBar()
                bar.setValue(bar.value() - step)
            event.accept()
            return

        # Plain wheel: scroll-select when enabled, otherwise zoom
        if self.scroll_select:
            self._scroll_select_wheel(event)
            event.accept()
            return

        step = dy if dy != 0 else dx
        if step != 0:
            factor = 1.15 if step > 0 else 1 / 1.15
            self.scale(factor, factor)
            self._update_pan_limits()
        event.accept()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_pan_limits()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._update_pan_limits()

    def _scroll_select_wheel(self, event: QWheelEvent) -> bool:
        """Cycle selection through stacked widgets under the cursor. No wrapping."""
        delta = event.angleDelta().y()
        if delta == 0:
            delta = event.pixelDelta().y()
        if delta == 0:
            return False
        stack = self._widget_items_at(event.position())
        if not stack:
            return False
        current = -1
        for i, item in enumerate(stack):
            if item.isSelected():
                current = i
                break
        if delta > 0:
            # Wheel up → next (deeper in the stack)
            nxt = current + 1
            if nxt >= len(stack):
                return True  # already at bottom - no wrap
        else:
            # Wheel down → previous (toward top)
            nxt = current - 1
            if nxt < 0:
                return True  # already at top / none - no wrap
        self.scene().clearSelection()
        stack[nxt].setSelected(True)
        self._refresh_stack_peers(event.position())
        return True

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.MiddleButton or (
            event.button() == Qt.MouseButton.LeftButton
            and event.modifiers() & Qt.KeyboardModifier.AltModifier
        ):
            self._panning = True
            self._pan_start = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)
        self._refresh_stack_peers(event.position())

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
        self._refresh_stack_peers(event.position())

    def leaveEvent(self, event) -> None:  # noqa: N802
        # Fall back to selected-item stack so peers don't vanish on leave.
        self._refresh_stack_peers(None)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._panning and event.button() in (
            Qt.MouseButton.MiddleButton,
            Qt.MouseButton.LeftButton,
        ):
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)
        self._refresh_stack_peers(event.position())
