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
from .textures import TextureResolver, gamma_relative_path
from .undo import GeoEdit, GeoState, UndoStack

# Scene z bands (back → front):
#   body/diamond → default labels → hover chrome → select chrome → focus label
_LABEL_Z = 1_000_000.0
_DIAMOND_Z = 2_000_000.0
_HOVER_CHROME_Z = 3_000_000.0
_HOVER_LABEL_Z = 3_000_000.5  # hovered element's label above its chrome
_SELECT_CHROME_Z = 3_000_001.0
_SELECT_LABEL_Z = 3_000_001.5  # selected element's label above select chrome


HANDLE = 10.0  # Invisible corner / edge hit thickness (px, item space)
MIN_SIZE = 4.0

# Selected = light blue; hovered = normal blue
SEL_BLUE = QColor(130, 185, 255)
SEL_BLUE_FILL = QColor(130, 185, 255, 45)
HOVER_BLUE = QColor(40, 130, 255)
HOVER_BLUE_FILL = QColor(40, 130, 255, 55)
LABEL_GREY = QColor(160, 160, 165)
IDLE_YELLOW = QColor(200, 200, 80, 200)


def _path_under_section(node_path: str, section_path: str) -> bool:
    """True if node is the section itself or a descendant (slash paths)."""
    if not section_path:
        return False
    if node_path == section_path:
        return True
    return node_path.startswith(section_path + "/")


class FocusChrome(QGraphicsItem):
    """Selection / hover outline+fill drawn above all widget content."""

    def __init__(self) -> None:
        super().__init__()
        self._kind = "hover"  # "hover" | "select"
        self._meta = False
        self._show_fill = False
        self._rect = QRectF()
        self._poly = QPolygonF()
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setAcceptHoverEvents(False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.hide()

    def bind(
        self,
        item: WidgetItem | None,
        *,
        kind: str,
        show_fill: bool,
    ) -> None:
        if item is None or not item.node.visible or not item.isVisible():
            self.hide()
            return
        self.prepareGeometryChange()
        self._kind = kind
        self._meta = bool(item.node.from_meta)
        # Select fill is toggle-gated; hover always draws fill.
        if kind == "select":
            self._show_fill = bool(show_fill)
        else:
            self._show_fill = True
        self.setPos(item.node.abs_x, item.node.abs_y)
        self._rect = QRectF(0, 0, max(item.node.width, 1), max(item.node.height, 1))
        if self._meta:
            self._poly = QPolygonF(item._meta_diamond_poly())
        else:
            self._poly = QPolygonF()
        self.show()
        self.update()

    def boundingRect(self) -> QRectF:  # noqa: N802
        pad = 3.0
        return self._rect.adjusted(-pad, -pad, pad, pad)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        if self._kind == "select":
            color = SEL_BLUE
            fill = SEL_BLUE_FILL if self._show_fill else QColor(0, 0, 0, 0)
            width = 2
        else:
            color = HOVER_BLUE
            fill = HOVER_BLUE_FILL if self._show_fill else QColor(0, 0, 0, 0)
            width = 1
        pen = QPen(color)
        pen.setWidth(width)
        pen.setCosmetic(True)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(pen)
        painter.setBrush(QBrush(fill))
        if self._meta and not self._poly.isEmpty():
            painter.drawPolygon(self._poly)
        else:
            painter.drawRect(self._rect)


class WidgetItem(QGraphicsRectItem):
    """Drawable UI widget box with optional texture fill and resize handles."""

    def __init__(
        self,
        node: LayoutNode,
        resolver: TextureResolver,
        *,
        label_font_size: int = 8,
        show_element_labels: bool = True,
        show_box_border: bool = False,
        show_box_fill: bool = False,
    ) -> None:
        super().__init__(0, 0, max(node.width, 1), max(node.height, 1))
        self.node = node
        self.resolver = resolver
        self.label_font_size = max(LABEL_FONT_MIN, int(label_font_size))
        self.show_element_labels = show_element_labels
        self.show_box_border = show_box_border
        self.show_box_fill = show_box_fill
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
        # Texture under the box fill/border so selection & hover tints sit on top.
        self._pixmap_item.setFlag(
            QGraphicsItem.GraphicsItemFlag.ItemStacksBehindParent, True
        )
        self._pixmap_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        # Labels are scene-level (not children) so they stack above all textures/fills.
        self._label_item = QGraphicsSimpleTextItem()
        self._label_item.setBrush(QBrush(LABEL_GREY))
        self._label_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._label_item.setAcceptHoverEvents(False)
        self._label_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self._label_shadow = QGraphicsSimpleTextItem()
        self._label_shadow.setBrush(QBrush(QColor(0, 0, 0, 220)))
        self._label_shadow.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._label_shadow.setAcceptHoverEvents(False)
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
        self.refresh_look()

    def detach_overlays(self, scene: QGraphicsScene) -> None:
        for lab in (self._label_item, self._label_shadow):
            if lab.scene() is scene:
                scene.removeItem(lab)

    def set_label_font_size(self, size: int) -> None:
        self.label_font_size = max(LABEL_FONT_MIN, int(size))
        self._apply_label_font()
        self._apply_label()

    def set_show_element_labels(self, show: bool) -> None:
        self.show_element_labels = bool(show)
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
        scene = self.scene()
        if isinstance(scene, UiScene):
            scene.set_hover_path(self.node.path)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # noqa: N802
        scene = self.scene()
        if isinstance(scene, UiScene) and scene.hover_path() == self.node.path:
            scene.set_hover_path(None)
        super().hoverLeaveEvent(event)

    def is_hovered(self) -> bool:
        scene = self.scene()
        return isinstance(scene, UiScene) and scene.hover_path() == self.node.path

    def refresh_look(self) -> None:
        self._updating = True
        self.setRect(0, 0, max(self.node.width, 1), max(self.node.height, 1))
        self.setPos(self.node.abs_x, self.node.abs_y)
        visible = bool(self.node.visible)
        self.setVisible(visible)
        # Disabled layers: never selectable / never a scroll-select hit target.
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, visible)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, visible)
        self.setAcceptHoverEvents(visible)
        if not visible and self.isSelected():
            self.setSelected(False)
        self._apply_texture()
        self._apply_label()
        if not visible:
            self._updating = False
            return
        selected = self.isSelected()
        hovered = self.is_hovered() and not selected
        # Idle yellow border only on non-selected, non-hovered (toggle).
        if self.node.from_meta:
            self.setPen(QPen(Qt.PenStyle.NoPen))
            self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        elif not selected and not hovered and self.show_box_border:
            pen = QPen(IDLE_YELLOW)
            pen.setWidth(1)
            pen.setCosmetic(True)
            self.setPen(pen)
        else:
            self.setPen(QPen(Qt.PenStyle.NoPen))
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
                tip += f"\n{gamma_relative_path(resolved.path)}"
        if self.node.text and self.node.text.content:
            tip += f"\ntext: {self.node.text.content}"
            # String resolution is owned by the app Log/Properties (StringResolver).
        self.setToolTip(tip)
        self._updating = False

    def _apply_texture(self) -> None:
        self._pixmap_item.setPixmap(QPixmap())
        self._pixmap_item.setVisible(False)
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
        # Preview: always fit texture into the widget box.
        pix = pix.scaled(
            target_w,
            target_h,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._pixmap_item.setPixmap(pix)
        self._pixmap_item.setPos(0, 0)
        self._pixmap_item.setVisible(True)

    def boundingRect(self) -> QRectF:  # noqa: N802
        # Labels are scene-level overlays (not children) - do not unite their rects here.
        return super().boundingRect()

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
        tag = self.node.tag or self.node.path or ""
        visible = bool(tag) and self.show_element_labels and self.node.visible
        if not visible:
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
            self._label_item.setBrush(QBrush(LABEL_GREY))
            br = self._label_item.boundingRect()
            # Sit to the right of the diamond tip.
            tip = self._meta_diamond_poly().at(1)  # right tip
            gap = 6.0
            lx = tip.x() + gap - br.x()
            ly = tip.y() - (br.y() + br.height() / 2.0)
        else:
            self._label_item.setBrush(QBrush(LABEL_GREY))
            br = self._label_item.boundingRect()
            lx = 2.0 - br.x()
            ly = 1.0 - br.y()
        # Scene coords: labels are not parented (so they stack above all fills/textures).
        ox, oy = self.node.abs_x, self.node.abs_y
        self._label_shadow.setPos(ox + lx + 1, oy + ly + 1)
        self._label_item.setPos(ox + lx, oy + ly)

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value):  # noqa: N802
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            # Repaint diamond/box colors on select (yellow → blue).
            if not self._updating:
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
            self._apply_label()
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
        """Invisible resize zones: 10×10 corners, full mid-edge strips between them."""
        if self.node.from_meta:
            return None  # diamond markers are move-only
        r = self.rect()
        x, y = pos.x(), pos.y()
        left, right, top, bottom = r.left(), r.right(), r.top(), r.bottom()
        hit = HANDLE
        near_l = abs(x - left) <= hit
        near_r = abs(x - right) <= hit
        near_t = abs(y - top) <= hit
        near_b = abs(y - bottom) <= hit
        # Corners win at the intersections.
        if near_l and near_t:
            return "tl"
        if near_r and near_t:
            return "tr"
        if near_l and near_b:
            return "bl"
        if near_r and near_b:
            return "br"
        # Edge strips: whole side minus the corner squares.
        if near_t and left + hit < x < right - hit:
            return "t"
        if near_b and left + hit < x < right - hit:
            return "b"
        if near_l and top + hit < y < bottom - hit:
            return "l"
        if near_r and top + hit < y < bottom - hit:
            return "r"
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
            self._apply_label()
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
            # Base yellow diamond; selected/hover outline is FocusChrome (above content).
            color = QColor(200, 200, 80, 220)
            fill = QColor(200, 200, 80, 70)
            pen = QPen(color)
            pen.setWidth(1)
            pen.setCosmetic(True)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(pen)
            painter.setBrush(QBrush(fill))
            painter.drawPolygon(self._meta_diamond_poly())
            return
        # No body fill — texture (if any) is the pixmap child; select/hover fill is FocusChrome.
        # Untextured widgets stay fully transparent aside from an optional idle outline.
        pen = self.pen()
        if pen.style() != Qt.PenStyle.NoPen:
            painter.setPen(pen)
            painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            painter.drawRect(self.rect())


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
        show_box_border: bool = False,
        show_box_fill: bool = False,
    ) -> None:
        super().__init__(0, 0, UI_WIDTH, UI_HEIGHT)
        self.resolver = resolver
        self.label_font_size = max(LABEL_FONT_MIN, int(label_font_size))
        self.show_element_labels = show_element_labels
        self.show_box_border = show_box_border
        self.show_box_fill = show_box_fill
        self.doc: LayoutNode | None = None
        self._items: dict[str, WidgetItem] = {}
        self._layer_visible: dict[str, bool] = {}
        self._hover_path: str | None = None
        self.undo_stack = UndoStack()
        self._stage = QGraphicsRectItem(0, 0, UI_WIDTH, UI_HEIGHT)
        self._stage.setBrush(QBrush(QColor(28, 28, 32)))
        self._stage.setPen(QPen(QColor(90, 90, 100), 2))
        self._stage.setZValue(-1000)
        self._stage.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self._stage.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.addItem(self._stage)
        self._hover_chrome = FocusChrome()
        self._hover_chrome.setZValue(_HOVER_CHROME_Z)
        self.addItem(self._hover_chrome)
        self._select_chrome = FocusChrome()
        self._select_chrome.setZValue(_SELECT_CHROME_Z)
        self.addItem(self._select_chrome)
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
        self._sync_focus_chrome()

    def _sync_focus_chrome(self) -> None:
        """Selection/hover chrome above content; that element's label above its chrome."""
        # Drop illegal selection/hover on disabled-layer widgets.
        for item in list(self.selectedItems()):
            if isinstance(item, WidgetItem) and (
                not item.node.visible or not item.isVisible()
            ):
                item.setSelected(False)
        if self._hover_path:
            h = self._items.get(self._hover_path)
            if h is None or not h.node.visible or not h.isVisible():
                self._hover_path = None

        selected = [
            i
            for i in self.selectedItems()
            if isinstance(i, WidgetItem) and i.node.visible and i.isVisible()
        ]
        # Max one selected.
        if len(selected) > 1:
            keep = selected[-1]
            self.blockSignals(True)
            try:
                for item in selected:
                    if item is not keep:
                        item.setSelected(False)
            finally:
                self.blockSignals(False)
            selected = [keep]
        sel = selected[0] if selected else None

        hover = self._items.get(self._hover_path) if self._hover_path else None
        if hover is not None and (
            not hover.node.visible or not hover.isVisible() or hover is sel
        ):
            hover = None  # no hover chrome on the selected element

        # Reset labels to default band (under chrome), then raise focus labels.
        for item in self._items.values():
            base = getattr(item, "_label_z_base", _LABEL_Z)
            item._label_shadow.setZValue(base)
            item._label_item.setZValue(base + 0.01)

        self._select_chrome.bind(sel, kind="select", show_fill=self.show_box_fill)
        self._hover_chrome.bind(hover, kind="hover", show_fill=True)

        if hover is not None:
            hover._label_shadow.setZValue(_HOVER_LABEL_Z)
            hover._label_item.setZValue(_HOVER_LABEL_Z + 0.01)
        if sel is not None:
            sel._label_shadow.setZValue(_SELECT_LABEL_Z)
            sel._label_item.setZValue(_SELECT_LABEL_Z + 0.01)

    def set_document(self, doc: LayoutNode | None) -> None:
        for item in list(self._items.values()):
            item.detach_overlays(self)
            self.removeItem(item)
        self._items.clear()
        self.clear_undo()
        self._layer_visible = {}
        self._hover_path = None
        self.doc = doc
        if doc is None:
            self._sync_focus_chrome()
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
                show_box_border=self.show_box_border,
                show_box_fill=self.show_box_fill,
            )
            # texture/fill < label < diamond
            if node.from_meta:
                item.setZValue(_DIAMOND_Z + float(z))
            else:
                item.setZValue(float(z))
            self.addItem(item)
            item._label_z_base = _LABEL_Z + float(z)
            item._label_shadow.setZValue(item._label_z_base)
            item._label_item.setZValue(item._label_z_base + 0.01)
            self.addItem(item._label_shadow)
            self.addItem(item._label_item)
            item._apply_label()
            self._items[node.path] = item
        self._sync_focus_chrome()
        # Visibility applied after caller sets layer toggles via set_layer_states.

    def refresh_item_positions(self) -> None:
        for item in self._items.values():
            item.refresh_look()
        self._sync_focus_chrome()

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
        # Drop selection / hover on anything now hidden.
        hover = self._hover_path
        if hover and hover in self._items and not self._items[hover].node.visible:
            self.set_hover_path(None)
        for item in list(self.selectedItems()):
            if isinstance(item, WidgetItem) and not item.node.visible:
                item.setSelected(False)
        for view in self.views():
            refresh = getattr(view, "_refresh_stack_peers", None)
            if callable(refresh):
                refresh(None)

    def _effective_layer_visible(self, node_path: str) -> bool:
        """Visible iff the deepest matching layer toggle is on.

        Nested layer roots (e.g. popup_*) are independent of their parent
        layer checkbox — only the most specific layer that contains the node
        decides visibility.
        """
        if not self._layer_visible:
            return True
        best_path = ""
        best_on = True
        for layer_path, enabled in self._layer_visible.items():
            if not _path_under_section(node_path, layer_path):
                continue
            if len(layer_path) >= len(best_path):
                best_path = layer_path
                best_on = bool(enabled)
        return best_on

    def select_path(self, path: str) -> None:
        item = self._items.get(path)
        self.clearSelection()
        if item is None or not item.node.visible:
            self.set_hover_path(None)
            return
        item.setSelected(True)
        # Do not pan/zoom the canvas — only the user moves the view.

    def rebind_resolver(self, resolver: TextureResolver) -> None:
        """Swap texture resolver and redraw without rebuilding the scene."""
        self.resolver = resolver
        for item in self._items.values():
            item.resolver = resolver
            item.refresh_look()

    def hover_path(self) -> str | None:
        return self._hover_path

    def set_hover_path(self, path: str | None) -> None:
        """Exactly one widget may show hover style (canvas or Tree driven)."""
        path = path or None
        if path is not None:
            item = self._items.get(path)
            if item is None or not item.node.visible or not item.isVisible():
                path = None
        if self._hover_path == path:
            self._sync_focus_chrome()
            return
        prev = self._hover_path
        self._hover_path = path
        if prev and prev in self._items:
            self._items[prev].refresh_look()
        if path and path in self._items:
            self._items[path].refresh_look()
        self._sync_focus_chrome()

    def set_tree_hover_path(self, path: str | None) -> None:
        """Mirror Tree row hover onto the matching canvas widget."""
        self.set_hover_path(path)

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
        # Enforce max-one selection and reject disabled-layer widgets.
        selected = [
            i
            for i in self.selectedItems()
            if isinstance(i, WidgetItem) and i.node.visible and i.isVisible()
        ]
        for item in list(self.selectedItems()):
            if isinstance(item, WidgetItem) and item not in selected:
                item.setSelected(False)
        if len(selected) > 1:
            keep = selected[-1]
            self.blockSignals(True)
            try:
                for item in selected:
                    if item is not keep:
                        item.setSelected(False)
            finally:
                self.blockSignals(False)
            selected = [keep]
        if selected:
            self.selection_node_changed.emit(selected[0].node)
        else:
            self.selection_node_changed.emit(None)
        # Refresh idle yellow borders on previous/current hover+select targets.
        if self._hover_path and self._hover_path in self._items:
            self._items[self._hover_path].refresh_look()
        for item in selected:
            item.refresh_look()
        self._sync_focus_chrome()

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
    stack_peers_changed = pyqtSignal(object)  # frozenset[str] peer paths

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
        self._stack_peer_paths: frozenset[str] = frozenset()
        self.setMouseTracking(True)
        self.scene().selectionChanged.connect(self._on_selection_changed_stack)
        self.fit_stage()

    def set_scroll_select(self, enabled: bool) -> None:
        self.scroll_select = bool(enabled)

    def _on_selection_changed_stack(self) -> None:
        self._refresh_stack_peers()

    def _set_stack_peers(self, peers: set[WidgetItem]) -> None:
        """Notify Tree of overlapping stack peers (no WYSIWYG grey chrome)."""
        paths = frozenset(
            i.node.path
            for i in peers
            if i.node.path and i.node.visible and i.isVisible()
        )
        if paths == self._stack_peer_paths:
            return
        self._stack_peer_paths = paths
        self.stack_peers_changed.emit(paths)

    def _refresh_stack_peers(self, view_pos: QPointF | None = None) -> None:
        """Grey-highlight overlapping widgets under the cursor (scroll-select stack)."""
        stack: list[WidgetItem] = []
        if view_pos is not None:
            stack = self._widget_items_at(view_pos)
        if len(stack) < 2:
            selected = [
                i
                for i in self.scene().selectedItems()
                if isinstance(i, WidgetItem) and i.node.visible and i.isVisible()
            ]
            if selected:
                center = selected[0].mapToScene(selected[0].rect().center())
                stack = self._widget_items_at(QPointF(self.mapFromScene(center)))
        stack = [i for i in stack if i.node.visible and i.isVisible()]
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
            if (
                isinstance(item, WidgetItem)
                and item.isVisible()
                and item.node.visible
            ):
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
