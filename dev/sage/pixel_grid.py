"""Scene-aligned pixel grid: 1 device-pixel strokes on integer HUD/sheet lines."""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem

# Grey @ 50% alpha.
PIXEL_GRID_LINE = QColor(128, 128, 128, 128)
PIXEL_GRID_Z = 10_000_008.0


class PixelGridItem(QGraphicsItem):
    """Grid on integer scene pixels; stroke width is always 1 screen pixel."""

    def __init__(self, width: float, height: float) -> None:
        super().__init__()
        self._w = max(1.0, float(width))
        self._h = max(1.0, float(height))
        self.setZValue(PIXEL_GRID_Z)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setAcceptHoverEvents(False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.hide()

    def set_grid_size(self, width: float, height: float) -> None:
        w = max(1.0, float(width))
        h = max(1.0, float(height))
        if w == self._w and h == self._h:
            return
        self.prepareGeometryChange()
        self._w = w
        self._h = h
        self.update()

    def boundingRect(self) -> QRectF:  # noqa: N802
        return QRectF(-0.5, -0.5, self._w + 1.0, self._h + 1.0)

    def paint(  # noqa: N802
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget=None,
    ) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, False)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        # Convert 1 device pixel → scene units on each axis (handles zoom + aspect).
        t = painter.transform()
        dx = 1.0 / max(abs(float(t.m11())), 1e-9)
        dy = 1.0 / max(abs(float(t.m22())), 1e-9)
        er = option.exposedRect.adjusted(-1.0, -1.0, 1.0, 1.0)
        x0 = max(0, int(er.left()))
        x1 = min(int(self._w), int(er.right()) + 1)
        y0 = max(0, int(er.top()))
        y1 = min(int(self._h), int(er.bottom()) + 1)
        color = PIXEL_GRID_LINE
        for x in range(x0, x1 + 1):
            painter.fillRect(
                QRectF(float(x) - dx * 0.5, 0.0, dx, self._h), color
            )
        for y in range(y0, y1 + 1):
            painter.fillRect(
                QRectF(0.0, float(y) - dy * 0.5, self._w, dy), color
            )
