"""QGraphicsView canvas for 1024x768 Stalker UI layout."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QPoint, QPointF, QRectF, QTimer, Qt, pyqtSignal
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
    QGraphicsLineItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QGridLayout,
    QSizePolicy,
    QStyle,
    QStyleOptionGraphicsItem,
    QWidget,
)

from .box_chrome import (
    FOCUS_LABEL_Z,
    HOVER_LABEL_TEXT,
    IDLE_CHROME_Z,
    IdleBorderChrome,
    LABEL_Z,
    OutsideLabelChrome,
    SEL_BLUE,
    SEL_BLUE_FILL,
    SELECT_LABEL_TEXT,
    FocusCaptionOverlay,
)
from .diaglog import get_logger
from .fonts import FontResolver
from .model import LayoutNode
from .settings import LABEL_FONT_MIN, UI_HEIGHT, UI_WIDTH
from .strings import StringResolver
from .textures import TextureResolver, gamma_relative_path
from .undo import GeoEdit, GeoState, UndoStack

_log = get_logger("canvas")

# Scene z bands (back → front):
#   widgets (texture+text, document order) → labels → diamonds →
#   idle → hover border → select chrome → hover label → select label (front)
_LABEL_Z = LABEL_Z
_DIAMOND_Z = 2_000_000.0
_IDLE_CHROME_Z = IDLE_CHROME_Z
_HOVER_CHROME_Z = 10_000_001.0
_SELECT_CHROME_Z = 10_000_002.0
_HOVER_LABEL_Z = 10_000_003.0
_SELECT_LABEL_Z = FOCUS_LABEL_Z
_MARQUEE_Z = 10_000_010.0
_GUIDE_Z = 10_000_009.0

RULER_THICKNESS = 15
RULER_MINOR = 8  # scene px between short notches
RULER_MAJOR = 64  # scene px between longer notches
# Right-click on a ruler removes a guide within this many viewport pixels.
RULER_GUIDE_HIT_PX = 6


HANDLE = 10.0  # Invisible corner / edge hit thickness (px, item space)
MIN_SIZE = 4.0
DRAG_THRESHOLD = 5.0  # view px before click becomes marquee / drag

# Selected = normal blue; hovered border = white
HOVER_BORDER = QColor(255, 255, 255)
GUIDE_LINE = QColor(220, 220, 230, 128)  # 1px, ~50% alpha
RULER_BG = QColor(30, 30, 34)
RULER_TICK = QColor(120, 120, 128)
RULER_TICK_MAJOR = QColor(170, 170, 178)


def _path_under_section(node_path: str, section_path: str) -> bool:
    """True if node is the section itself or a descendant (slash paths)."""
    if not section_path:
        return False
    if node_path == section_path:
        return True
    return node_path.startswith(section_path + "/")


class GuideLine(QGraphicsLineItem):
    """1px semi-transparent horizontal or vertical guide across the stage."""

    _SPAN = 50_000.0

    def __init__(self, *, vertical: bool, value: float) -> None:
        super().__init__()
        self.vertical = vertical
        self.value = 0.0
        pen = QPen(GUIDE_LINE)
        pen.setWidth(1)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setZValue(_GUIDE_Z)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.set_value(value)

    def set_value(self, value: float) -> None:
        v = float(round(value))
        self.value = v
        span = self._SPAN
        if self.vertical:
            self.setLine(v, -span, v, span)
        else:
            self.setLine(-span, v, span, v)


class FocusChrome(QGraphicsItem):
    """Select/hover border+fill. Always painted above every other scene item."""

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
        show_fill: bool = False,
    ) -> None:
        if item is None or not item.node.visible or not item.isVisible():
            self.hide()
            return
        self.prepareGeometryChange()
        self._kind = kind
        self._meta = bool(item.node.from_meta)
        # Select fill is toggle-gated; hover never fills.
        self._show_fill = bool(show_fill) if kind == "select" else False
        self.setPos(item.pos())
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
            color = HOVER_BORDER
            fill = QColor(0, 0, 0, 0)
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


class FocusLabelOverlay(FocusCaptionOverlay):
    """UI-layout focus caption — thin wrapper over shared FocusCaptionOverlay."""

    def bind(self, item: WidgetItem | None, *, text_color: QColor) -> None:
        if item is None or not item.node.visible or not item.isVisible():
            self.clear()
            return
        tag = item.node.tag or item.node.path or ""
        if not tag:
            self.clear()
            return
        tip = item._meta_diamond_poly().at(1) if item.node.from_meta else None
        self.bind_above(
            item_pos=item.pos(),
            text=tag,
            font_size=item.label_font_size,
            text_color=text_color,
            tip=tip,
        )


class WidgetItem(QGraphicsRectItem):
    """Drawable UI widget box with optional texture fill and resize handles."""

    def __init__(
        self,
        node: LayoutNode,
        resolver: TextureResolver,
        *,
        strings: StringResolver | None = None,
        fonts: FontResolver | None = None,
        label_font_size: int = 5,
        show_element_labels: bool = False,
        show_box_border: bool = False,
        show_box_fill: bool = False,
    ) -> None:
        super().__init__(0, 0, max(node.width, 1), max(node.height, 1))
        self.node = node
        self.resolver = resolver
        self.strings = strings
        self.fonts = fonts
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
        # Texture then text as normal children (not ItemStacksBehindParent) so both
        # follow the parent WidgetItem's document-order z across overlapping boxes.
        # Child z: texture under text; scene chrome overlays sit far above.
        self._pixmap_item = QGraphicsPixmapItem(self)
        self._pixmap_item.setZValue(0)
        self._pixmap_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._text_item = QGraphicsPixmapItem(self)
        self._text_item.setZValue(1)
        self._text_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        # Native atlas pixels; nearest-neighbor under view zoom (no bilinear mush).
        self._text_item.setTransformationMode(
            Qt.TransformationMode.FastTransformation
        )
        self._text_item.hide()
        # Scene-level outside labels + idle border (shared with atlas editor).
        self._caption = OutsideLabelChrome()
        self._idle_chrome = IdleBorderChrome()
        # Compat aliases used by focus overlay / z stacking.
        self._label_item = self._caption.text
        self._label_shadow = self._caption.shadow
        self._idle_border = self._idle_chrome.rect
        self._missing = False
        # Session-only: Properties checkbox; not written to XML/settings.
        self.show_texture = True
        self._resizing = False
        self._resize_corner: str | None = None
        self._resize_start = QPointF()
        self._start_rect = QRectF()
        self._start_pos = QPointF()
        self._updating = False
        self._geo_before: GeoState | None = None
        self.refresh_look()

    def detach_overlays(self, scene: QGraphicsScene) -> None:
        self._caption.detach(scene)
        self._idle_chrome.detach(scene)

    def set_label_font_size(self, size: int) -> None:
        self.label_font_size = max(LABEL_FONT_MIN, int(size))
        self._apply_label()

    def set_show_element_labels(self, show: bool) -> None:
        self.show_element_labels = bool(show)
        self._apply_label()

    def _apply_label_font(self) -> None:
        self._caption.set_font_size(self.label_font_size)

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
        self.unsetCursor()
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
        self._apply_text()
        self._apply_label()
        if not visible:
            self._idle_border.hide()
            self._text_item.hide()
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemHasNoContents, True)
            self._updating = False
            return
        selected = self.isSelected()
        hovered = self.is_hovered() and not selected
        # Body never paints chrome: select/hover/idle borders+fill are scene overlays.
        # Untextured widgets stay fully empty (texture child only when present).
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        if self.node.from_meta:
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemHasNoContents, False)
        else:
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemHasNoContents, True)
        self._sync_idle_border(selected=selected, hovered=hovered)
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
            if self.strings is not None:
                s = self.strings.resolve(self.node.text.content)
                if s.text and not s.error:
                    tip += f"\n→ {s.text}"
        self.setToolTip(tip)
        self._updating = False

    def _sync_idle_border(self, *, selected: bool, hovered: bool) -> None:
        """Yellow outline above content for idle (non-selected, non-hovered) boxes."""
        show = (
            bool(self.node.visible)
            and self.isVisible()
            and not self.node.from_meta
            and not selected
            and not hovered
            and self.show_box_border
        )
        self._idle_chrome.sync(
            item_pos=self.pos(),
            width=self.node.width,
            height=self.node.height,
            show=show,
        )

    def has_visible_texture(self) -> bool:
        return self.show_texture and not self._pixmap_item.pixmap().isNull()

    def set_show_texture(self, show: bool) -> None:
        show = bool(show)
        if self.show_texture == show:
            return
        self.show_texture = show
        self._apply_texture()

    def _apply_texture(self) -> None:
        self._pixmap_item.setPixmap(QPixmap())
        self._pixmap_item.setVisible(False)
        self._missing = False
        if not self.show_texture or not self.node.texture:
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

    def _apply_text(self) -> None:
        """Paint resolved string-table text with engine bitmap fonts."""
        self._text_item.setPixmap(QPixmap())
        self._text_item.hide()
        ref = self.node.text
        if ref is None or not (ref.content or "").strip():
            return
        content = ref.content.strip()
        body = content
        if self.strings is not None:
            resolved = self.strings.resolve(content)
            if resolved.error and not resolved.is_literal:
                body = content
            else:
                body = resolved.text or content
        if not body.strip():
            return
        color = QColor(
            ref.r if ref.r is not None else 255,
            ref.g if ref.g is not None else 255,
            ref.b if ref.b is not None else 255,
            ref.a if ref.a is not None else 255,
        )
        font_name = (ref.font or "").strip() or "letterica16"
        pix = QPixmap()
        ui_scale = 1.0
        if self.fonts is not None:
            atlas = self.fonts.resolve_font(font_name)
            if atlas is not None:
                pix = self.fonts.render_text(atlas, body, color)
                ui_scale = float(atlas.ui_scale) or 1.0
            else:
                digits = "".join(ch for ch in font_name if ch.isdigit())
                pt = int(digits) if digits else 16
                pix = self.fonts.render_fallback(
                    body,
                    point_size=pt,
                    color=color,
                    max_width=max(int(self.node.width), 1),
                )
        if pix.isNull():
            return
        box_w = max(float(self.node.width), 1.0)
        box_h = max(float(self.node.height), 1.0)
        # Native atlas px × (768 / device_height) → HUD space (engine parity).
        tw = float(pix.width()) * ui_scale
        th = float(pix.height()) * ui_scale
        # Engine default is top-left unless XML sets align / vert_align.
        align = (ref.align or "l").lower()
        valign = (ref.vert_align or "t").lower()
        if align in ("c", "center"):
            x = (box_w - tw) / 2.0
        elif align in ("r", "right"):
            x = box_w - tw
        else:
            x = 0.0
        if valign in ("c", "center"):
            y = (box_h - th) / 2.0
        elif valign in ("b", "bottom"):
            y = box_h - th
        else:
            y = 0.0
        self._text_item.setPixmap(pix)
        self._text_item.setTransformationMode(
            Qt.TransformationMode.FastTransformation
        )
        self._text_item.setScale(ui_scale)
        self._text_item.setPos(x, y)
        self._text_item.show()

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
        # Focus captions are owned by FocusLabelOverlay (select/hover, always on top).
        focused = self.isSelected() or self.is_hovered()
        tip = self._meta_diamond_poly().at(1) if self.node.from_meta else None
        self._caption.apply(
            text=tag,
            item_pos=self.pos(),
            visible=bool(
                tag and self.show_element_labels and self.node.visible and not focused
            ),
            font_size=self.label_font_size,
            tip=tip,
        )

    def follow_overlays_to_pos(self) -> None:
        """Reposition scene overlays to match current item.pos() (no texture work)."""
        if self._label_item.isVisible():
            self._apply_label()
        elif self._idle_border.isVisible():
            self._sync_idle_border(
                selected=self.isSelected(), hovered=self.is_hovered()
            )
        elif self.show_box_border and not self.isSelected() and not self.is_hovered():
            self._sync_idle_border(
                selected=self.isSelected(), hovered=self.is_hovered()
            )

    def _write_geometry_from_pos(self) -> None:
        """Write local x/y/w/h from item.pos/rect without recompute_absolute."""
        ox, oy = self.node.coord_origin()
        self.node.set_geometry(
            x=round(self.pos().x() - ox),
            y=round(self.pos().y() - oy),
            width=round(self.rect().width()),
            height=round(self.rect().height()),
        )

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value):  # noqa: N802
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            # Intentionally empty: any work here (even update()) can crash Qt when
            # combined with ItemHasNoContents / flag churn. Chrome is scene-owned.
            return super().itemChange(change, value)
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

    def _handle_at(self, pos: QPointF, *, hit: float | None = None) -> str | None:
        """Invisible resize zones: corner squares + mid-edge strips.

        ``hit`` is the half-thickness in item space (default ``HANDLE``).
        """
        if self.node.from_meta:
            return None  # diamond markers are move-only
        r = self.rect()
        x, y = pos.x(), pos.y()
        left, right, top, bottom = r.left(), r.right(), r.top(), r.bottom()
        h = HANDLE if hit is None else float(hit)
        near_l = abs(x - left) <= h
        near_r = abs(x - right) <= h
        near_t = abs(y - top) <= h
        near_b = abs(y - bottom) <= h
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
        if near_t and left + h < x < right - h:
            return "t"
        if near_b and left + h < x < right - h:
            return "b"
        if near_l and top + h < y < bottom - h:
            return "l"
        if near_r and top + h < y < bottom - h:
            return "r"
        return None

    def begin_resize(self, corner: str, scene_pos: QPointF) -> None:
        """Start a canvas-driven resize (do not rely on Qt item mouse delivery)."""
        self._geo_before = self._snapshot_geo()
        self._resizing = True
        self._resize_corner = corner
        self._resize_start = QPointF(scene_pos)
        self._start_rect = QRectF(self.rect())
        self._start_pos = QPointF(self.pos())

    def apply_resize(self, scene_pos: QPointF) -> None:
        if not self._resizing or not self._resize_corner:
            return
        delta = scene_pos - self._resize_start
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

    def end_resize(self) -> None:
        if not self._resizing:
            return
        self._resizing = False
        self._resize_corner = None
        scene = self.scene()
        if isinstance(scene, UiScene):
            scene.refresh_item_positions()
            scene.geometry_changed.emit(self.node)
        self._commit_geo_edit()

    def hoverMoveEvent(self, event) -> None:  # noqa: N802
        # Cursor is owned by UiCanvas._update_edit_cursor (selected edge/move only).
        self.unsetCursor()
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
        edit = GeoEdit.single(self._geo_before, after)
        self._geo_before = None
        if not edit.changed():
            return
        scene = self.scene()
        if isinstance(scene, UiScene):
            scene.push_geo_edit(edit)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        # Resize is started by UiCanvas.begin_resize — never via item delivery.
        if event.button() == Qt.MouseButton.LeftButton:
            self._geo_before = self._snapshot_geo()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._resizing and self._resize_corner:
            self.apply_resize(event.scenePos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._resizing:
            self.end_resize()
            event.accept()
            return
        super().mouseReleaseEvent(event)
        self._commit_geo_edit()

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        if not self.node.from_meta:
            return
        # Small meta handle only — select/hover chrome is FocusChrome.
        color = QColor(200, 200, 80, 220)
        fill = QColor(200, 200, 80, 70)
        pen = QPen(color)
        pen.setWidth(1)
        pen.setCosmetic(True)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(pen)
        painter.setBrush(QBrush(fill))
        painter.drawPolygon(self._meta_diamond_poly())


class UiScene(QGraphicsScene):
    geometry_changed = pyqtSignal(object)  # LayoutNode
    selection_node_changed = pyqtSignal(object)  # LayoutNode | None
    undo_stack_changed = pyqtSignal()

    def __init__(
        self,
        resolver: TextureResolver,
        *,
        strings: StringResolver | None = None,
        fonts: FontResolver | None = None,
        label_font_size: int = 5,
        show_element_labels: bool = False,
        show_box_border: bool = False,
        show_box_fill: bool = False,
    ) -> None:
        super().__init__(0, 0, UI_WIDTH, UI_HEIGHT)
        self.resolver = resolver
        self.strings = strings
        self.fonts = fonts
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
        self._hover_chromes: list[FocusChrome] = []
        self._select_chromes: list[FocusChrome] = []
        self._select_label = FocusLabelOverlay()
        self._select_label.setZValue(_SELECT_LABEL_Z)
        self.addItem(self._select_label)
        self._hover_label = FocusLabelOverlay()
        self._hover_label.setZValue(_HOVER_LABEL_Z)
        self.addItem(self._hover_label)
        self._guides: list[GuideLine] = []
        # Marquee drag: paths that currently have white hover preview (None = idle).
        self._marquee_preview: list[WidgetItem] | None = None
        self.selectionChanged.connect(self._on_selection_changed)

    def add_guide(self, *, vertical: bool, value: float) -> GuideLine:
        guide = GuideLine(vertical=vertical, value=value)
        self.addItem(guide)
        self._guides.append(guide)
        return guide

    def remove_guide(self, guide: GuideLine) -> None:
        if guide in self._guides:
            self._guides.remove(guide)
        if guide.scene() is self:
            self.removeItem(guide)

    def clear_guides(self) -> None:
        for guide in list(self._guides):
            self.remove_guide(guide)

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

    def apply_geo_edit(self, edit: GeoEdit, *, use_after: bool) -> LayoutNode | None:
        """Apply every part of a (possibly multi) geometry edit. Returns last node."""
        if self.doc is None:
            return None
        last: LayoutNode | None = None
        for before, after in edit.parts:
            state = after if use_after else before
            node = self.doc.find_by_path(state.path)
            if node is None:
                continue
            node.set_geometry(
                x=state.x,
                y=state.y,
                width=state.width,
                height=state.height,
            )
            last = node
        self.doc.recompute_absolute(0.0, 0.0)
        self.refresh_item_positions()
        return last

    def selected_widgets(self) -> list[WidgetItem]:
        return [
            i
            for i in self.selectedItems()
            if isinstance(i, WidgetItem) and i.node.visible and i.isVisible()
        ]

    @staticmethod
    def move_roots(items: list[WidgetItem]) -> list[WidgetItem]:
        """Selected items whose ancestors are not also selected (avoid double-move)."""
        paths = {i.node.path for i in items}
        roots: list[WidgetItem] = []
        for item in items:
            parent = item.node.parent
            under = False
            while parent is not None:
                if parent.path in paths:
                    under = True
                    break
                parent = parent.parent
            if not under:
                roots.append(item)
        return roots

    def items_in_subtree(self, root: WidgetItem) -> list[WidgetItem]:
        """Root plus every widget item under its layout-node subtree."""
        found: list[WidgetItem] = [root]
        for node in root.node.iter_all():
            if node is root.node or not node.path:
                continue
            item = self._items.get(node.path)
            if item is not None:
                found.append(item)
        return found

    def _ensure_select_chromes(self, count: int) -> None:
        while len(self._select_chromes) < count:
            chrome = FocusChrome()
            chrome.setZValue(_SELECT_CHROME_Z)
            self.addItem(chrome)
            self._select_chromes.append(chrome)

    def _ensure_hover_chromes(self, count: int) -> None:
        while len(self._hover_chromes) < count:
            chrome = FocusChrome()
            chrome.setZValue(_HOVER_CHROME_Z)
            self.addItem(chrome)
            self._hover_chromes.append(chrome)

    def set_marquee_preview(self, items: list[WidgetItem] | None) -> None:
        """White hover borders for fully-enclosed marquee candidates (or clear)."""
        self._marquee_preview = None if items is None else list(items)
        self._sync_focus_chrome()

    def _sync_focus_chrome(self) -> None:
        """Borders/fills always above every texture, label, and diamond."""
        if self._hover_path:
            h = self._items.get(self._hover_path)
            if h is None or not h.node.visible or not h.isVisible():
                self._hover_path = None

        selected = self.selected_widgets()
        selected_set = set(selected)
        primary = selected[-1] if selected else None

        # Labels stay in the label band — never above borders/fills.
        for item in self._items.values():
            base = getattr(item, "_label_z_base", _LABEL_Z)
            item._label_shadow.setZValue(base)
            item._label_item.setZValue(base + 0.01)
            item._idle_border.setZValue(_IDLE_CHROME_Z)

        self._ensure_select_chromes(len(selected))
        self._select_label.setZValue(_SELECT_LABEL_Z)
        self._hover_label.setZValue(_HOVER_LABEL_Z)
        show_fill = bool(self.show_box_fill)
        for i, chrome in enumerate(self._select_chromes):
            chrome.setZValue(_SELECT_CHROME_Z)
            if i < len(selected):
                chrome.bind(selected[i], kind="select", show_fill=show_fill)
            else:
                chrome.bind(None, kind="select", show_fill=False)

        # Marquee preview: white border on every fully enclosed eligible widget.
        if self._marquee_preview is not None:
            preview = [
                i
                for i in self._marquee_preview
                if i.node.visible
                and i.isVisible()
                and not i.node.from_meta
                and i.node.path in self._items
            ]
            self._ensure_hover_chromes(max(len(preview), 1))
            for i, chrome in enumerate(self._hover_chromes):
                chrome.setZValue(_HOVER_CHROME_Z)
                if i < len(preview):
                    chrome.bind(preview[i], kind="hover", show_fill=False)
                else:
                    chrome.bind(None, kind="hover", show_fill=False)
            self._hover_label.bind(None, text_color=HOVER_LABEL_TEXT)
        else:
            hover = self._items.get(self._hover_path) if self._hover_path else None
            if hover is not None and (
                not hover.node.visible
                or not hover.isVisible()
                or hover in selected_set
                or hover.node.from_meta
            ):
                hover = None
            self._ensure_hover_chromes(1)
            for i, chrome in enumerate(self._hover_chromes):
                chrome.setZValue(_HOVER_CHROME_Z)
                if i == 0:
                    chrome.bind(hover, kind="hover", show_fill=False)
                else:
                    chrome.bind(None, kind="hover", show_fill=False)
            self._hover_label.bind(hover, text_color=HOVER_LABEL_TEXT)

        # Caption only for the primary (last) selection.
        self._select_label.bind(primary, text_color=SELECT_LABEL_TEXT)

    def set_label_font_size(self, size: int) -> None:
        self.label_font_size = max(LABEL_FONT_MIN, int(size))
        for item in self._items.values():
            item.set_label_font_size(self.label_font_size)
        self._sync_focus_chrome()

    def set_show_element_labels(self, show: bool) -> None:
        self.show_element_labels = bool(show)
        for item in self._items.values():
            item.set_show_element_labels(self.show_element_labels)
        self._sync_focus_chrome()

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

    def set_document(self, doc: LayoutNode | None) -> None:
        for item in list(self._items.values()):
            item.detach_overlays(self)
            self.removeItem(item)
        self._items.clear()
        self.clear_undo()
        self._layer_visible = {}
        self._hover_path = None
        self._marquee_preview = None
        self.clear_guides()
        self.doc = doc
        if doc is None:
            self._sync_focus_chrome()
            return
        # Document order (preorder DFS): later siblings above earlier branches;
        # children above parents. Texture + UI text share this widget z.
        drawables = doc.iter_drawables()
        for z, node in enumerate(drawables):
            item = WidgetItem(
                node,
                self.resolver,
                strings=self.strings,
                fonts=self.fonts,
                label_font_size=self.label_font_size,
                show_element_labels=self.show_element_labels,
                show_box_border=self.show_box_border,
                show_box_fill=self.show_box_fill,
            )
            # widget (texture+text) < labels < diamonds < chrome
            if node.from_meta:
                item.setZValue(_DIAMOND_Z + float(z))
            else:
                item.setZValue(float(z))
            self.addItem(item)
            item._label_z_base = _LABEL_Z + float(z)
            item._caption.attach(self, z_base=item._label_z_base)
            item._idle_chrome.attach(self)
            item._apply_label()
            self._items[node.path] = item
        self._sync_focus_chrome()
        # Visibility applied after caller sets layer toggles via set_layer_states.

    def refresh_item_positions(self) -> None:
        """Sync item.pos / overlays from node.abs_* (no texture re-resolve)."""
        for item in self._items.values():
            item._updating = True
            item.setRect(0, 0, max(item.node.width, 1), max(item.node.height, 1))
            item.setPos(item.node.abs_x, item.node.abs_y)
            item._updating = False
            item.follow_overlays_to_pos()
        self._sync_focus_chrome()

    def refresh_all_looks(self) -> None:
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
        self.refresh_all_looks()
        # Drop selection / hover on anything now hidden.
        hover = self._hover_path
        if hover and hover in self._items and not self._items[hover].node.visible:
            self.set_hover_path(None)
        for item in list(self.selectedItems()):
            if isinstance(item, WidgetItem) and not item.node.visible:
                item.setSelected(False)
        for view in self.views():
            clear = getattr(view, "_clear_stack_peers", None)
            if callable(clear):
                clear()

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

    def rebind_text_resources(
        self,
        *,
        strings: StringResolver | None = None,
        fonts: FontResolver | None = None,
    ) -> None:
        if strings is not None:
            self.strings = strings
        if fonts is not None:
            self.fonts = fonts
        for item in self._items.values():
            if strings is not None:
                item.strings = strings
            if fonts is not None:
                item.fonts = fonts
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
            if i.isVisible() and i.has_visible_texture():
                n += 1
        return n

    def _on_selection_changed(self) -> None:
        # Avoid mutating item flags / nested setSelected during Qt's selection notify.
        selected = self.selected_widgets()
        paths = [i.node.path for i in selected]
        _log.debug("selection_changed count=%s paths=%s", len(selected), paths)
        if selected:
            self.selection_node_changed.emit(selected[-1].node)
        else:
            self.selection_node_changed.emit(None)
        self._sync_focus_chrome()
        QTimer.singleShot(0, self._after_selection_changed)

    def _after_selection_changed(self) -> None:
        _log.debug("after_selection_changed begin")
        for item in list(self.selectedItems()):
            if isinstance(item, WidgetItem) and (
                not item.node.visible or not item.isVisible()
            ):
                _log.debug("deselect invisible %s", item.node.path)
                item.setSelected(False)
        for item in self._items.values():
            item.refresh_look()
        self._sync_focus_chrome()
        _log.debug(
            "after_selection_changed done selected=%s",
            [i.node.path for i in self.selected_widgets()],
        )

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
    view_changed = pyqtSignal()  # zoom / pan / resize — rulers refresh

    def __init__(self, scene: UiScene) -> None:
        super().__init__(scene)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setBackgroundBrush(QBrush(QColor(18, 18, 20)))
        self._panning = False
        self._pan_button: Qt.MouseButton | None = None
        self._pan_start = QPointF()
        self._stack_peer_paths: frozenset[str] = frozenset()
        # Last Ctrl+click cycle stack under the cursor (deepest/smallest first).
        self._scroll_stack: list[WidgetItem] = []
        self._last_stack_view_pos: QPointF | None = None
        # While pressed: only the pick accepts mouse / is selectable.
        self._press_mouse_restore: (
            list[tuple[WidgetItem, Qt.MouseButton, bool]] | None
        ) = None
        # Left-button gesture: None | "click_or_marquee" | "marquee" | "move" | "resize"
        self._gesture: str | None = None
        self._press_view_pos = QPointF()
        self._press_scene_pos = QPointF()
        self._press_pick: WidgetItem | None = None
        self._move_roots: list[WidgetItem] = []
        self._move_origins: dict[WidgetItem, QPointF] = {}
        self._move_befores: dict[WidgetItem, GeoState] = {}
        self._move_did_drag = False
        self._marquee_origin = QPointF()
        self._marquee_item: QGraphicsRectItem | None = None
        self._last_cursor_view_pos = QPointF()
        self.setMouseTracking(True)
        self.scene().selectionChanged.connect(self._on_selection_changed_stack)
        self.fit_stage()

    def _on_selection_changed_stack(self) -> None:
        # Drop transient Ctrl+click peer greys; keep cursor in sync.
        if self._stack_peer_paths:
            self._set_stack_peers(set())
        self._update_edit_cursor(self._last_cursor_view_pos)

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

    def _clear_stack_peers(self) -> None:
        self._scroll_stack = []
        self._last_stack_view_pos = None
        if self._stack_peer_paths:
            self._set_stack_peers(set())

    def _update_pan_limits(self) -> None:
        """Allow panning until each stage edge reaches the opposite viewport edge."""
        vis = self.mapToScene(self.viewport().rect()).boundingRect()
        mw = max(float(vis.width()), 1.0)
        mh = max(float(vis.height()), 1.0)
        # One viewport of overscroll past each side of the 1024×768 stage.
        self.setSceneRect(-mw, -mh, UI_WIDTH + 2.0 * mw, UI_HEIGHT + 2.0 * mh)
        self.view_changed.emit()

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
        """Visible widgets under the cursor (scroll-targets), smallest box first."""
        scene = self.scene()
        if scene is None:
            return []
        scene_pos = self.mapToScene(view_pos.toPoint())
        out: list[WidgetItem] = []
        seen: set[int] = set()
        for raw in scene.items(scene_pos):
            item = raw
            if not isinstance(item, WidgetItem):
                parent = raw.parentItem()
                if isinstance(parent, WidgetItem):
                    item = parent
                else:
                    continue
            if id(item) in seen:
                continue
            if not item.isVisible() or not item.node.visible:
                continue
            local = item.mapFromScene(scene_pos)
            if not item.shape().contains(local):
                continue
            seen.add(id(item))
            out.append(item)
        out.sort(key=lambda i: max(i.node.width, 1.0) * max(i.node.height, 1.0))
        return out

    @staticmethod
    def _pick_click_target(stack: list[WidgetItem]) -> WidgetItem | None:
        """Smallest scroll-target under the cursor."""
        return stack[0] if stack else None

    def _arm_press_pick(self, pick: WidgetItem) -> None:
        """Only `pick` can be hit/selected until release (keeps smallest target)."""
        self._restore_press_mouse()
        scene = self.scene()
        if not isinstance(scene, UiScene):
            return
        restore: list[tuple[WidgetItem, Qt.MouseButton, bool]] = []
        for item in scene._items.values():
            buttons = item.acceptedMouseButtons()
            selectable = bool(
                item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            )
            restore.append((item, buttons, selectable))
            if item is pick:
                item.setAcceptedMouseButtons(
                    Qt.MouseButton.LeftButton
                    | Qt.MouseButton.RightButton
                    | Qt.MouseButton.MiddleButton
                )
                item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
            else:
                item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self._press_mouse_restore = restore

    def _restore_press_mouse(self) -> None:
        if self._press_mouse_restore is None:
            return
        for item, buttons, selectable in self._press_mouse_restore:
            if item.scene() is self.scene():
                item.setAcceptedMouseButtons(buttons)
                item.setFlag(
                    QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, selectable
                )
        self._press_mouse_restore = None

    def _force_select(self, pick: WidgetItem) -> None:
        """Ensure `pick` is the only selected item (never toggle it off)."""
        scene = self.scene()
        if scene is None:
            return
        for item in list(scene.selectedItems()):
            if item is not pick:
                item.setSelected(False)
        if not pick.isSelected():
            pick.setSelected(True)

    def _select_items(self, items: list[WidgetItem]) -> None:
        scene = self.scene()
        if scene is None:
            return
        scene.clearSelection()
        for item in items:
            item.setSelected(True)

    def _marquee_scene_rect(self, a: QPointF, b: QPointF) -> QRectF:
        return QRectF(a, b).normalized()

    def _ensure_marquee_item(self) -> QGraphicsRectItem:
        scene = self.scene()
        assert isinstance(scene, UiScene)
        if self._marquee_item is None:
            item = QGraphicsRectItem()
            pen = QPen(SEL_BLUE)
            pen.setWidth(1)
            pen.setCosmetic(True)
            pen.setStyle(Qt.PenStyle.DashLine)
            item.setPen(pen)
            item.setBrush(QBrush(QColor(40, 130, 255, 40)))
            item.setZValue(_MARQUEE_Z)
            item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            scene.addItem(item)
            self._marquee_item = item
        return self._marquee_item

    def _clear_marquee(self) -> None:
        if self._marquee_item is not None:
            sc = self._marquee_item.scene()
            if sc is not None:
                sc.removeItem(self._marquee_item)
            self._marquee_item = None
        scene = self.scene()
        if isinstance(scene, UiScene):
            scene.set_marquee_preview(None)

    def _update_marquee(self, scene_pos: QPointF) -> None:
        rect = self._marquee_scene_rect(self._marquee_origin, scene_pos)
        item = self._ensure_marquee_item()
        item.setRect(rect)
        scene = self.scene()
        if isinstance(scene, UiScene):
            scene.set_marquee_preview(self._widgets_fully_in_rect(rect))

    def _widgets_fully_in_rect(self, rect: QRectF) -> list[WidgetItem]:
        """Fully enclosed widgets eligible for marquee select / preview."""
        scene = self.scene()
        if not isinstance(scene, UiScene):
            return []
        out: list[WidgetItem] = []
        for item in scene._items.values():
            # Disabled layers: node.visible is False; also skip hidden graphics items.
            if not item.isVisible() or not item.node.visible:
                continue
            if item.node.from_meta:
                continue  # root diamonds are never marquee-selected
            br = QRectF(
                item.node.abs_x,
                item.node.abs_y,
                max(item.node.width, 1.0),
                max(item.node.height, 1.0),
            )
            if rect.contains(br):
                out.append(item)
        out.sort(key=lambda i: max(i.node.width, 1.0) * max(i.node.height, 1.0))
        return out

    def _begin_group_move(self, selected: list[WidgetItem]) -> None:
        scene = self.scene()
        if not isinstance(scene, UiScene):
            return
        roots = scene.move_roots(selected)
        self._move_roots = roots
        # Visual followers: roots + layout descendants (same delta while dragging).
        moving: dict[int, WidgetItem] = {}
        for root in roots:
            for item in scene.items_in_subtree(root):
                moving[id(item)] = item
        self._move_origins = {i: QPointF(i.pos()) for i in moving.values()}
        self._move_befores = {i: i._snapshot_geo() for i in roots}
        self._move_did_drag = False

    def _apply_group_move(self, scene_pos: QPointF) -> None:
        """Visual-only drag: setPos + overlays. No tree recompute / props / textures."""
        delta = scene_pos - self._press_scene_pos
        if abs(delta.x()) >= 0.5 or abs(delta.y()) >= 0.5:
            self._move_did_drag = True
        dx = round(delta.x())
        dy = round(delta.y())
        for item, origin in self._move_origins.items():
            item._updating = True
            item.setPos(origin.x() + dx, origin.y() + dy)
            item._updating = False
            item.follow_overlays_to_pos()
        scene = self.scene()
        if isinstance(scene, UiScene):
            scene._sync_focus_chrome()

    def _end_group_move(self) -> None:
        if not self._move_did_drag:
            self._move_roots = []
            self._move_origins = {}
            self._move_befores = {}
            return
        pairs: list[tuple[GeoState, GeoState]] = []
        scene = self.scene()
        for item, before in self._move_befores.items():
            item._write_geometry_from_pos()
            after = item._snapshot_geo()
            if before != after:
                pairs.append((before, after))
        if isinstance(scene, UiScene) and scene.doc is not None:
            scene.doc.recompute_absolute(0.0, 0.0)
            if pairs:
                scene.push_geo_edit(GeoEdit.multi(pairs))
            # One props/dirty notify — handler refreshes positions once.
            if self._move_roots:
                scene.geometry_changed.emit(self._move_roots[-1].node)
        self._move_roots = []
        self._move_origins = {}
        self._move_befores = {}
        self._move_did_drag = False

    def _view_drag_distance(self, pos: QPointF) -> float:
        d = pos - self._press_view_pos
        return abs(d.x()) + abs(d.y())

    _RESIZE_CURSORS = {
        "br": Qt.CursorShape.SizeFDiagCursor,
        "tl": Qt.CursorShape.SizeFDiagCursor,
        "tr": Qt.CursorShape.SizeBDiagCursor,
        "bl": Qt.CursorShape.SizeBDiagCursor,
        "t": Qt.CursorShape.SizeVerCursor,
        "b": Qt.CursorShape.SizeVerCursor,
        "l": Qt.CursorShape.SizeHorCursor,
        "r": Qt.CursorShape.SizeHorCursor,
    }

    def _scene_pos_from_view(self, view_pos: QPointF) -> QPointF:
        """Float-accurate view → scene map (mapToScene only accepts QPoint)."""
        inverted, ok = self.viewportTransform().inverted()
        if ok:
            return inverted.map(QPointF(view_pos))
        return self.mapToScene(view_pos.toPoint())

    def _resize_hit_at(self, view_pos: QPointF) -> tuple[WidgetItem, str] | None:
        """Single selected widget's resize handle under the cursor, if any.

        Checked before pick/marquee so a resize cursor cannot fall through to
        selecting a smaller stacked child. Hit thickness is ~HANDLE viewport px
        so the grab zone matches the cursor at any zoom.
        """
        scene = self.scene()
        if not isinstance(scene, UiScene):
            return None
        selected = scene.selected_widgets()
        if len(selected) != 1:
            return None
        item = selected[0]
        if (
            not item.isVisible()
            or not item.node.visible
            or item.node.from_meta
            or item.scene() is not scene
        ):
            return None
        scene_pos = self._scene_pos_from_view(view_pos)
        local = item.mapFromScene(scene_pos)
        scale = abs(self.transform().m11())
        hit = HANDLE / max(scale, 1e-6)
        corner = item._handle_at(local, hit=hit)
        if corner is None:
            return None
        return item, corner

    def _update_edit_cursor(self, view_pos: QPointF) -> None:
        """Selected edge → resize; selected body → move; else arrow."""
        if self._panning:
            return
        if self._gesture == "move":
            self.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
            return
        if self._gesture == "resize":
            return  # keep press-time resize cursor
        if self._gesture is not None:
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            return

        scene = self.scene()
        if not isinstance(scene, UiScene):
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            return

        hit = self._resize_hit_at(view_pos)
        if hit is not None:
            self.viewport().setCursor(self._RESIZE_CURSORS[hit[1]])
            return

        scene_pos = self._scene_pos_from_view(view_pos)
        selected = scene.selected_widgets()
        under: list[WidgetItem] = []
        for item in selected:
            local = item.mapFromScene(scene_pos)
            if item.shape().contains(local):
                under.append(item)
        if not under:
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            return

        self.viewport().setCursor(Qt.CursorShape.SizeAllCursor)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        # Never zoom while panning, or while MMB is held (Windows autoscroll
        # synthesizes wheel deltas from MMB+move / MMB+LMB chords).
        if self._panning or event.buttons() & Qt.MouseButton.MiddleButton:
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

        # Ctrl+scroll → zoom
        if mods & Qt.KeyboardModifier.ControlModifier:
            step = dy if dy != 0 else dx
            if step != 0:
                factor = 1.15 if step > 0 else 1 / 1.15
                self.scale(factor, factor)
                self._update_pan_limits()
            event.accept()
            return

        # Shift+scroll → pan left/right
        if mods & Qt.KeyboardModifier.ShiftModifier:
            step = dx if dx != 0 else dy
            if step != 0:
                bar = self.horizontalScrollBar()
                bar.setValue(bar.value() - step)
            event.accept()
            return

        # Alt+scroll → pan up/down
        if mods & Qt.KeyboardModifier.AltModifier:
            step = dy if dy != 0 else dx
            if step != 0:
                bar = self.verticalScrollBar()
                bar.setValue(bar.value() - step)
            event.accept()
            return

        # Plain wheel → zoom
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

    def _ctrl_click_cycle(self, view_pos: QPointF) -> bool:
        """Ctrl+click cycles stacked widgets toward smaller; wraps to largest."""
        stack = [
            i
            for i in self._widget_items_at(view_pos)
            if i.node.visible and i.isVisible() and i.scene() is self.scene()
        ]
        if not stack:
            return False
        current = -1
        for i, item in enumerate(stack):
            if item.isSelected():
                current = i
                break
        if len(stack) == 1:
            nxt = 0
        elif current < 0:
            # Nothing in stack selected → land on largest, then each click goes smaller.
            nxt = len(stack) - 1
        else:
            nxt = current - 1
            if nxt < 0:
                nxt = len(stack) - 1  # wrap to largest
        _log.debug(
            "ctrl_click_cycle current=%s nxt=%s path=%s stack=%s",
            current,
            nxt,
            stack[nxt].node.path,
            [i.node.path for i in stack[:8]],
        )
        scene = self.scene()
        if scene is None:
            return False
        scene.clearSelection()
        stack[nxt].setSelected(True)
        peers = {item for item in stack if item is not stack[nxt]}
        self._set_stack_peers(peers)
        self._last_stack_view_pos = QPointF(view_pos)
        self._scroll_stack = stack
        return True

    def _handle_left_canvas_press(self, event) -> None:
        """Start click / marquee / move / resize. Selection applies on release (except move)."""
        # Ctrl+click cycles stacked widgets (smaller, wrap to largest).
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Resize cursor wins over ctrl-cycle — same rule as plain left click.
            if self._resize_hit_at(event.position()) is None:
                if self._ctrl_click_cycle(event.position()):
                    self._gesture = None
                    self._press_pick = None
                    event.accept()
                    self._update_edit_cursor(event.position())
                    return
        self._restore_press_mouse()
        self._clear_marquee()
        scene = self.scene()
        self._press_view_pos = QPointF(event.position())
        self._press_scene_pos = self.mapToScene(event.position().toPoint())
        self._gesture = None
        self._move_did_drag = False
        self._press_pick = None

        # If the resize cursor would show, left click can ONLY start a resize
        # (never select / marquee / move a stacked child under the edge).
        resize_hit = self._resize_hit_at(event.position())
        if resize_hit is not None:
            pick, corner = resize_hit
            self._press_pick = pick
            self._gesture = "resize"
            self._press_scene_pos = self._scene_pos_from_view(event.position())
            _log.debug(
                "gesture=resize corner=%s path=%s (canvas-driven)",
                corner,
                pick.node.path,
            )
            # Drive resize ourselves — Qt item delivery often misses edge hits
            # (outside shape / child on top) even when the cursor says resize.
            pick.begin_resize(corner, self._press_scene_pos)
            self.viewport().setCursor(self._RESIZE_CURSORS[corner])
            event.accept()
            return

        stack = self._widget_items_at(event.position())
        pick = self._pick_click_target(stack)
        self._press_pick = pick
        _log.debug(
            "press pick=%s selected=%s stack=%s scene=%s",
            pick.node.path if pick is not None else None,
            pick.isSelected() if pick is not None else False,
            [i.node.path for i in stack[:8]],
            self._press_scene_pos,
        )

        if pick is not None and pick.isSelected():
            # Selected body hit: group-move — never marquee / never resize here
            # (resize already handled above).
            self._gesture = "move"
            selected = (
                scene.selected_widgets()
                if isinstance(scene, UiScene)
                else [pick]
            )
            _log.debug("gesture=move count=%s", len(selected))
            self._begin_group_move(selected)
            event.accept()
            return

        # Empty canvas or unselected widget: click-select or marquee (decide on move).
        self._gesture = "click_or_marquee"
        _log.debug("gesture=click_or_marquee")
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        # Already panning: ignore extra buttons (esp. LMB) so chords can't zoom/select.
        if self._panning:
            event.accept()
            return
        if event.button() == Qt.MouseButton.MiddleButton or (
            event.button() == Qt.MouseButton.LeftButton
            and event.modifiers() & Qt.KeyboardModifier.AltModifier
        ):
            self._panning = True
            self._pan_button = event.button()
            self._pan_start = event.position()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            scene = self.scene()
            if scene is not None:
                scene.clearSelection()
            event.accept()
            self._clear_stack_peers()
            self._update_edit_cursor(event.position())
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._handle_left_canvas_press(event)
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        # Qt sends DblClick instead of a second Press — treat it as another left press.
        if event.button() == Qt.MouseButton.LeftButton:
            self._handle_left_canvas_press(event)
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self._last_cursor_view_pos = QPointF(event.position())
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

        if self._gesture == "click_or_marquee":
            if self._view_drag_distance(event.position()) >= DRAG_THRESHOLD:
                self._gesture = "marquee"
                self._marquee_origin = QPointF(self._press_scene_pos)
                # Marquee replaces selection — drop the current box as soon as the box starts.
                scene = self.scene()
                if scene is not None and scene.selectedItems():
                    scene.clearSelection()
                _log.debug("gesture=marquee (cleared selection)")
                self._update_marquee(self.mapToScene(event.position().toPoint()))
            self._update_edit_cursor(event.position())
            event.accept()
            return

        if self._gesture == "marquee":
            self._update_marquee(self.mapToScene(event.position().toPoint()))
            self._update_edit_cursor(event.position())
            event.accept()
            return

        if self._gesture == "move":
            self._apply_group_move(self.mapToScene(event.position().toPoint()))
            self._update_edit_cursor(event.position())
            event.accept()
            return

        if self._gesture == "resize":
            pick = self._press_pick
            if isinstance(pick, WidgetItem):
                pick.apply_resize(self._scene_pos_from_view(event.position()))
            self._update_edit_cursor(event.position())
            event.accept()
            return

        super().mouseMoveEvent(event)
        self._update_edit_cursor(event.position())
        # Don't retarget hover while a press gesture is active.
        if self._gesture is not None or self._press_mouse_restore is not None:
            return
        stack = self._widget_items_at(event.position())
        scene = self.scene()
        if isinstance(scene, UiScene):
            pick = self._pick_click_target(stack)
            scene.set_hover_path(pick.node.path if pick is not None else None)
        if self._stack_peer_paths:
            self._set_stack_peers(set())

    def leaveEvent(self, event) -> None:  # noqa: N802
        scene = self.scene()
        if isinstance(scene, UiScene):
            scene.set_hover_path(None)
        self._clear_stack_peers()
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        # End pan only when the button that started it is released (not LMB during MMB pan).
        if self._panning and event.button() == self._pan_button:
            self._panning = False
            self._pan_button = None
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            self._update_edit_cursor(event.position())
            event.accept()
            return
        if self._panning:
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton and self._gesture is not None:
            gesture = self._gesture
            pick = self._press_pick
            self._gesture = None
            _log.debug(
                "release gesture=%s pick=%s",
                gesture,
                pick.node.path if pick is not None else None,
            )

            if gesture == "marquee":
                rect = self._marquee_scene_rect(
                    self._marquee_origin,
                    self.mapToScene(event.position().toPoint()),
                )
                self._clear_marquee()
                contained = self._widgets_fully_in_rect(rect)
                _log.debug(
                    "marquee select count=%s rect=%s",
                    len(contained),
                    rect,
                )
                self._select_items(contained)
                event.accept()
            elif gesture == "click_or_marquee":
                # Click without drag: select under cursor, or clear on empty.
                if pick is not None:
                    _log.debug("click select %s", pick.node.path)
                    self._force_select(pick)
                else:
                    _log.debug("click clear selection")
                    scene = self.scene()
                    if scene is not None:
                        scene.clearSelection()
                event.accept()
            elif gesture == "move":
                moved = self._move_did_drag
                self._end_group_move()
                # Click (no drag) on a selected item → select only that item.
                if not moved and pick is not None:
                    _log.debug("move-click → single select %s", pick.node.path)
                    self._force_select(pick)
                else:
                    _log.debug("move end dragged=%s", moved)
                event.accept()
            elif gesture == "resize":
                if isinstance(pick, WidgetItem):
                    pick.end_resize()
                event.accept()
            else:
                super().mouseReleaseEvent(event)

            self._restore_press_mouse()
            self._press_pick = None
            self._update_edit_cursor(event.position())
            stack = self._widget_items_at(event.position())
            scene = self.scene()
            if isinstance(scene, UiScene):
                hover = self._pick_click_target(stack)
                scene.set_hover_path(hover.node.path if hover is not None else None)
            return

        super().mouseReleaseEvent(event)
        self._restore_press_mouse()
        self._update_edit_cursor(event.position())
        stack = self._widget_items_at(event.position())
        scene = self.scene()
        if isinstance(scene, UiScene):
            pick = self._pick_click_target(stack)
            scene.set_hover_path(pick.node.path if pick is not None else None)


class RulerBar(QWidget):
    """Thin top/left ruler with notches aligned to the 1024×768 stage."""

    pressed = pyqtSignal(object)  # QMouseEvent
    moved = pyqtSignal(object)
    released = pyqtSignal(object)

    def __init__(self, orientation: Qt.Orientation, canvas: UiCanvas) -> None:
        super().__init__()
        self.orientation = orientation
        self.canvas = canvas
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        if orientation == Qt.Orientation.Horizontal:
            self.setFixedHeight(RULER_THICKNESS)
            self.setMinimumWidth(0)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.setCursor(Qt.CursorShape.SplitHCursor)
        else:
            self.setFixedWidth(RULER_THICKNESS)
            self.setMinimumHeight(0)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
            self.setCursor(Qt.CursorShape.SplitVCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.pressed.emit(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self.moved.emit(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self.released.emit(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        # Exceptions here abort the Qt process (no traceback) — catch + log.
        try:
            self._paint_ruler(event)
        except Exception:  # noqa: BLE001
            _log.exception(
                "RulerBar.paintEvent failed orientation=%s size=%sx%s",
                self.orientation,
                self.width(),
                self.height(),
            )

    def _paint_ruler(self, event) -> None:  # noqa: ARG002
        painter = QPainter(self)
        painter.fillRect(self.rect(), RULER_BG)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        vp = self.canvas.viewport()
        thick = RULER_THICKNESS
        # mapFrom(viewport, …) is unreliable with QGraphicsView's viewport.
        # Map scene → viewport via viewportTransform, then viewport → ruler via global.
        vt = self.canvas.viewportTransform()
        vp_origin = self.mapFromGlobal(vp.mapToGlobal(QPoint(0, 0)))
        if self.orientation == Qt.Orientation.Horizontal:
            for sx in range(0, UI_WIDTH + 1, RULER_MINOR):
                pt = vt.map(QPointF(float(sx), 0.0))
                x = int(round(vp_origin.x() + pt.x()))
                if x < -2 or x > self.width() + 2:
                    continue
                major = sx % RULER_MAJOR == 0
                tick_h = 10 if major else 5
                painter.setPen(QPen(RULER_TICK_MAJOR if major else RULER_TICK, 1))
                painter.drawLine(x, thick - 1, x, thick - 1 - tick_h)
            painter.setPen(QPen(QColor(60, 60, 68), 1))
            painter.drawLine(0, thick - 1, self.width(), thick - 1)
        else:
            for sy in range(0, UI_HEIGHT + 1, RULER_MINOR):
                pt = vt.map(QPointF(0.0, float(sy)))
                y = int(round(vp_origin.y() + pt.y()))
                if y < -2 or y > self.height() + 2:
                    continue
                major = sy % RULER_MAJOR == 0
                tick_w = 10 if major else 5
                painter.setPen(QPen(RULER_TICK_MAJOR if major else RULER_TICK, 1))
                painter.drawLine(thick - 1, y, thick - 1 - tick_w, y)
            painter.setPen(QPen(QColor(60, 60, 68), 1))
            painter.drawLine(thick - 1, 0, thick - 1, self.height())


class CanvasBoard(QWidget):
    """UiCanvas with top/left ruler bars that spawn horizontal/vertical guides."""

    def __init__(self, scene: UiScene) -> None:
        super().__init__()
        self.canvas = UiCanvas(scene)
        self._top = RulerBar(Qt.Orientation.Horizontal, self.canvas)
        self._left = RulerBar(Qt.Orientation.Vertical, self.canvas)
        corner = QWidget()
        corner.setFixedSize(RULER_THICKNESS, RULER_THICKNESS)
        corner.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        corner.setStyleSheet(
            f"background-color: rgb({RULER_BG.red()},{RULER_BG.green()},{RULER_BG.blue()});"
        )

        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        grid.addWidget(corner, 0, 0)
        grid.addWidget(self._top, 0, 1)
        grid.addWidget(self._left, 1, 0)
        grid.addWidget(self.canvas, 1, 1)
        grid.setRowStretch(1, 1)
        grid.setColumnStretch(1, 1)

        self._drag_guide: GuideLine | None = None
        self._drag_vertical = False  # True = vertical guide (from top bar)
        self._grabber: RulerBar | None = None

        # Top bar → vertical guides (X). Left bar → horizontal guides (Y).
        self._top.pressed.connect(lambda e: self._ruler_press(e, vertical=True))
        self._top.moved.connect(self._ruler_move)
        self._top.released.connect(self._ruler_release)
        self._left.pressed.connect(lambda e: self._ruler_press(e, vertical=False))
        self._left.moved.connect(self._ruler_move)
        self._left.released.connect(self._ruler_release)

        self.canvas.view_changed.connect(self._refresh_rulers)
        self.canvas.horizontalScrollBar().valueChanged.connect(self._refresh_rulers)
        self.canvas.verticalScrollBar().valueChanged.connect(self._refresh_rulers)

    def _refresh_rulers(self, *_args) -> None:
        self._top.update()
        self._left.update()

    def _scene_pos_from_global(self, global_pos) -> QPointF:
        vp = self.canvas.viewport()
        local = vp.mapFromGlobal(global_pos)
        return self.canvas.mapToScene(local)

    def _ruler_press(self, event, *, vertical: bool) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self._ruler_remove_near(event, vertical=vertical)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        scene = self.canvas.scene()
        if not isinstance(scene, UiScene):
            event.ignore()
            return
        sp = self._scene_pos_from_global(event.globalPosition().toPoint())
        value = sp.x() if vertical else sp.y()
        _log.debug("ruler_press vertical=%s value=%.1f", vertical, value)
        self._drag_vertical = vertical
        self._drag_guide = scene.add_guide(vertical=vertical, value=value)
        self._grabber = self._top if vertical else self._left
        self._grabber.grabMouse()
        event.accept()

    def _ruler_remove_near(self, event, *, vertical: bool) -> None:
        """Right-click on a ruler bar: delete the nearest matching guide."""
        scene = self.canvas.scene()
        if not isinstance(scene, UiScene):
            event.ignore()
            return
        sp = self._scene_pos_from_global(event.globalPosition().toPoint())
        click = sp.x() if vertical else sp.y()
        vt = self.canvas.viewportTransform()
        scale = abs(vt.m11() if vertical else vt.m22())
        thresh = RULER_GUIDE_HIT_PX / max(scale, 1e-6)
        best: GuideLine | None = None
        best_d = thresh
        for guide in scene._guides:
            if guide.vertical != vertical:
                continue
            d = abs(guide.value - click)
            if d <= best_d:
                best_d = d
                best = guide
        if best is None:
            _log.debug(
                "ruler_rightclick miss vertical=%s click=%.1f thresh=%.2f",
                vertical,
                click,
                thresh,
            )
            event.accept()
            return
        _log.info(
            "ruler_rightclick remove vertical=%s value=%.1f d=%.2f",
            vertical,
            best.value,
            best_d,
        )
        scene.remove_guide(best)
        event.accept()

    def _ruler_move(self, event) -> None:
        if self._drag_guide is None:
            event.ignore()
            return
        sp = self._scene_pos_from_global(event.globalPosition().toPoint())
        value = sp.x() if self._drag_vertical else sp.y()
        self._drag_guide.set_value(value)
        event.accept()

    def _over_place_zone(self, global_pos) -> bool:
        """Canvas viewport or either ruler bar — release here keeps the guide."""
        if self.canvas.viewport().rect().contains(
            self.canvas.viewport().mapFromGlobal(global_pos)
        ):
            return True
        if self._top.rect().contains(self._top.mapFromGlobal(global_pos)):
            return True
        if self._left.rect().contains(self._left.mapFromGlobal(global_pos)):
            return True
        return False

    def _ruler_release(self, event) -> None:
        if self._grabber is not None:
            self._grabber.releaseMouse()
            self._grabber = None
        guide = self._drag_guide
        self._drag_guide = None
        scene = self.canvas.scene()
        if guide is None or not isinstance(scene, UiScene):
            event.accept()
            return
        gpos = event.globalPosition().toPoint()
        if self._over_place_zone(gpos):
            sp = self._scene_pos_from_global(gpos)
            value = sp.x() if self._drag_vertical else sp.y()
            guide.set_value(value)
            _log.debug(
                "ruler_release keep vertical=%s value=%.1f",
                self._drag_vertical,
                value,
            )
        else:
            _log.debug("ruler_release discard guide")
            scene.remove_guide(guide)
        event.accept()
