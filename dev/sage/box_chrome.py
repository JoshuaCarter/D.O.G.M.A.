"""Shared box chrome for UI-layout and atlas editors.

Scene-level outside labels (above the box) and idle yellow borders — same
rules in both editors so captions never sit inside the rect by accident.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPen,
)
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QStyleOptionGraphicsItem,
    QWidget,
)

from .settings import LABEL_FONT_MIN, clamp_label_font_size

# Editor-decoration overlay bands (above all document content).
# Within a band, callers may add a tiny doc-order epsilon.
LABEL_Z = 1_000_000.0
IDLE_CHROME_Z = 10_000_000.0
FOCUS_LABEL_Z = 10_000_004.0

SEL_BLUE = QColor(40, 130, 255)
SEL_BLUE_FILL = QColor(40, 130, 255, 38)  # 15% alpha
LABEL_GREY = QColor(160, 160, 165)
IDLE_YELLOW = QColor(150, 140, 40, 220)
FOCUS_LABEL_BG = QColor(0, 0, 0)
SELECT_LABEL_TEXT = SEL_BLUE
HOVER_LABEL_TEXT = QColor(255, 255, 255)
LABEL_SHADOW = QColor(0, 0, 0, 220)

_ABOVE_GAP = 2.0
_FOCUS_PAD = 1.0


def label_font(size: int) -> QFont:
    return QFont("Segoe UI", max(LABEL_FONT_MIN, clamp_label_font_size(size)))


def anchor_above_box(text_br: QRectF, *, gap: float = _ABOVE_GAP) -> tuple[float, float]:
    """Item-local offset: caption outside above the box, bottom-left at origin."""
    lx = 0.0 - text_br.x()
    ly = -gap - text_br.height() - text_br.y()
    return lx, ly


def anchor_right_of_tip(
    tip: QPointF, text_br: QRectF, *, gap: float = 6.0
) -> tuple[float, float]:
    """Item-local offset: caption to the right of a diamond tip."""
    lx = tip.x() + gap - text_br.x()
    ly = tip.y() - (text_br.y() + text_br.height() / 2.0)
    return lx, ly


def focus_bg_rect(
    text_item: QGraphicsSimpleTextItem,
    *,
    text: str,
    lx: float,
    meta: bool,
) -> QRectF:
    """Black plate behind a focus caption (tight metrics + pad)."""
    fm = QFontMetricsF(text_item.font())
    loose = fm.boundingRect(text)
    tight = fm.tightBoundingRect(text)
    item_br = text_item.boundingRect()
    mapped = tight.translated(item_br.x() - loose.x(), item_br.y() - loose.y())
    pad = _FOCUS_PAD
    top = mapped.top() - pad
    bottom = mapped.bottom() + pad
    right = mapped.right() + pad
    left = mapped.left() - pad if meta else -lx
    return QRectF(left, top, max(right - left, 1.0), bottom - top)


class OutsideLabelChrome:
    """Scene-level tag caption + 1px shadow, anchored outside the box."""

    def __init__(self) -> None:
        self.text = QGraphicsSimpleTextItem()
        self.text.setBrush(QBrush(LABEL_GREY))
        self.text.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.text.setAcceptHoverEvents(False)
        self.text.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.shadow = QGraphicsSimpleTextItem()
        self.shadow.setBrush(QBrush(LABEL_SHADOW))
        self.shadow.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.shadow.setAcceptHoverEvents(False)
        self.shadow.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.hide()

    def hide(self) -> None:
        self.text.setText("")
        self.text.setVisible(False)
        self.shadow.setText("")
        self.shadow.setVisible(False)

    def attach(self, scene: QGraphicsScene, *, z_base: float = LABEL_Z) -> None:
        self.shadow.setZValue(z_base)
        self.text.setZValue(z_base + 0.01)
        if self.shadow.scene() is not scene:
            scene.addItem(self.shadow)
        if self.text.scene() is not scene:
            scene.addItem(self.text)

    def detach(self, scene: QGraphicsScene) -> None:
        for lab in (self.text, self.shadow):
            if lab.scene() is scene:
                scene.removeItem(lab)

    def set_font_size(self, size: int) -> None:
        font = label_font(size)
        self.text.setFont(font)
        self.shadow.setFont(font)

    def apply(
        self,
        *,
        text: str,
        item_pos: QPointF,
        visible: bool,
        font_size: int,
        color: QColor | None = None,
        tip: QPointF | None = None,
    ) -> None:
        if not visible or not text:
            self.hide()
            return
        self.set_font_size(font_size)
        brush = QBrush(color if color is not None else LABEL_GREY)
        self.text.setBrush(brush)
        self.text.setText(text)
        self.shadow.setText(text)
        br = self.text.boundingRect()
        if tip is not None:
            lx, ly = anchor_right_of_tip(tip, br)
        else:
            lx, ly = anchor_above_box(br)
        ox, oy = item_pos.x(), item_pos.y()
        self.shadow.setPos(ox + lx + 1.0, oy + ly + 1.0)
        self.text.setPos(ox + lx, oy + ly)
        self.shadow.setVisible(True)
        self.text.setVisible(True)


class IdleBorderChrome:
    """Scene-level yellow outline for unselected boxes (above content)."""

    def __init__(self) -> None:
        self.rect = QGraphicsRectItem()
        self.rect.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.rect.setAcceptHoverEvents(False)
        self.rect.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.rect.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.rect.setZValue(IDLE_CHROME_Z)
        self.rect.hide()

    def attach(self, scene: QGraphicsScene) -> None:
        self.rect.setZValue(IDLE_CHROME_Z)
        if self.rect.scene() is not scene:
            scene.addItem(self.rect)

    def detach(self, scene: QGraphicsScene) -> None:
        if self.rect.scene() is scene:
            scene.removeItem(self.rect)

    def sync(
        self,
        *,
        item_pos: QPointF,
        width: float,
        height: float,
        show: bool,
    ) -> None:
        if not show:
            self.rect.hide()
            return
        pen = QPen(IDLE_YELLOW)
        pen.setWidth(1)
        pen.setCosmetic(True)
        self.rect.setPen(pen)
        self.rect.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.rect.setPos(item_pos)
        self.rect.setRect(0, 0, max(width, 1.0), max(height, 1.0))
        self.rect.show()


class FocusCaptionOverlay(QGraphicsItem):
    """Focus tag in front of chrome: text + black bg (select / hover)."""

    def __init__(self) -> None:
        super().__init__()
        self._bg = QGraphicsRectItem(self)
        self._bg.setPen(QPen(Qt.PenStyle.NoPen))
        self._bg.setBrush(QBrush(FOCUS_LABEL_BG))
        self._bg.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._bg.setZValue(0)
        self._text = QGraphicsSimpleTextItem(self)
        self._text.setBrush(QBrush(SELECT_LABEL_TEXT))
        self._text.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._text.setZValue(1)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setAcceptHoverEvents(False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemHasNoContents, True)
        self.hide()

    def clear(self) -> None:
        self.hide()

    def bind_above(
        self,
        *,
        item_pos: QPointF,
        text: str,
        font_size: int,
        text_color: QColor,
        tip: QPointF | None = None,
    ) -> None:
        if not text:
            self.hide()
            return
        self.prepareGeometryChange()
        font = label_font(font_size)
        self._text.setFont(font)
        self._text.setBrush(QBrush(text_color))
        self._text.setText(text)
        self._text.setPos(0, 0)
        br = self._text.boundingRect()
        if tip is not None:
            lx, ly = anchor_right_of_tip(tip, br)
            meta = True
        else:
            lx, ly = anchor_above_box(br)
            meta = False
        self.setPos(item_pos.x() + lx, item_pos.y() + ly)
        self._bg.setRect(focus_bg_rect(self._text, text=text, lx=lx, meta=meta))
        self.show()

    def boundingRect(self) -> QRectF:  # noqa: N802
        return self._bg.rect()

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        return
