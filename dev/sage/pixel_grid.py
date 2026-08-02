"""Scene-aligned pixel grid: 1 device-pixel strokes on integer HUD/sheet lines."""

from __future__ import annotations

from PyQt6.QtCore import QLineF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem

# Grey: every 10th grid line @ 0.5 alpha, others @ 0.2.
PIXEL_GRID_MINOR = QColor(128, 128, 128, 51)  # ~0.2
PIXEL_GRID_MAJOR = QColor(128, 128, 128, 128)  # 0.5
PIXEL_GRID_MAJOR_EVERY = 10  # every Nth *grid line* is major
PIXEL_GRID_Z = 10_000_008.0
# Below this device-px per grid cell, step-1 minors read as a wash.
_MINOR_LOD_SCALE = 2.0


def _line_range(start: int, stop: int, step: int) -> range:
    """Inclusive multiples of ``step`` in ``[start, stop]``."""
    if stop < start or step <= 0:
        return range(0)
    first = ((start + step - 1) // step) * step
    return range(first, stop + 1, step)


class PixelGridItem(QGraphicsItem):
    """Grid lines every ``step`` scene pixels; stroke is 1 screen pixel."""

    def __init__(self, width: float, height: float, *, step: int = 1) -> None:
        super().__init__()
        self._w = max(1.0, float(width))
        self._h = max(1.0, float(height))
        self._step = max(1, int(step))
        self.setZValue(PIXEL_GRID_Z)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setAcceptHoverEvents(False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemUsesExtendedStyleOption, True)
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

    def set_step(self, step: int) -> None:
        step = max(1, int(step))
        if step == self._step:
            return
        self._step = step
        self.update()

    def boundingRect(self) -> QRectF:  # noqa: N802
        return QRectF(-0.5, -0.5, self._w + 1.0, self._h + 1.0)

    def paint(  # noqa: N802
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget=None,
    ) -> None:
        stage = QRectF(0.0, 0.0, self._w, self._h)
        er = option.exposedRect.intersected(stage)
        if er.isEmpty():
            return

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, False)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

        t = painter.transform()
        sx = abs(float(t.m11()))
        sy = abs(float(t.m22()))
        x0 = max(0, int(er.left()) - 1)
        x1 = min(int(self._w), int(er.right()) + 1)
        y0 = max(0, int(er.top()) - 1)
        y1 = min(int(self._h), int(er.bottom()) + 1)
        if x1 < x0 or y1 < y0:
            return

        y_lo = er.top()
        y_hi = er.bottom()
        x_lo = er.left()
        x_hi = er.right()
        step = self._step
        major_period = step * PIXEL_GRID_MAJOR_EVERY

        def draw_v(xs: list[int], color: QColor) -> None:
            if not xs:
                return
            pen = QPen(color)
            pen.setWidth(1)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawLines([QLineF(float(x), y_lo, float(x), y_hi) for x in xs])

        def draw_h(ys: list[int], color: QColor) -> None:
            if not ys:
                return
            pen = QPen(color)
            pen.setWidth(1)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawLines([QLineF(x_lo, float(y), x_hi, float(y)) for y in ys])

        xs = list(_line_range(x0, x1, step))
        ys = list(_line_range(y0, y1, step))
        # step==1 at low zoom: minors ≈ wash; still draw majors.
        if step == 1 and min(sx, sy) < _MINOR_LOD_SCALE:
            painter.fillRect(er, PIXEL_GRID_MINOR)
            majors_x = [x for x in xs if x % major_period == 0]
            majors_y = [y for y in ys if y % major_period == 0]
            draw_v(majors_x, PIXEL_GRID_MAJOR)
            draw_h(majors_y, PIXEL_GRID_MAJOR)
            return

        minors_x = [x for x in xs if x % major_period != 0]
        minors_y = [y for y in ys if y % major_period != 0]
        majors_x = [x for x in xs if x % major_period == 0]
        majors_y = [y for y in ys if y % major_period == 0]
        draw_v(minors_x, PIXEL_GRID_MINOR)
        draw_h(minors_y, PIXEL_GRID_MINOR)
        draw_v(majors_x, PIXEL_GRID_MAJOR)
        draw_h(majors_y, PIXEL_GRID_MAJOR)
