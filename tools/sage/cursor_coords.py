"""Top-left scene x,y strip (blends with the ruler bar)."""

from __future__ import annotations

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtWidgets import QLabel, QWidget


class CursorCoordsHud:
    """Fixed top-left label: ruler-bar height/bg, no border, mouse-transparent."""

    def __init__(
        self,
        host: QWidget,
        *,
        height: int = 15,
        bg: tuple[int, int, int] = (30, 30, 34),
    ) -> None:
        self._height = max(1, int(height))
        self._label = QLabel(host)
        self._label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._label.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._label.setAlignment(
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        )
        r, g, b = bg
        self._label.setStyleSheet(
            "QLabel {"
            f" background-color: rgb({r}, {g}, {b});"
            " color: #C8C8D0;"
            " padding: 0 6px;"
            " border: none;"
            " font: 11px 'Cascadia Mono', 'Consolas', 'Courier New', monospace;"
            "}"
        )
        self._label.setText("0, 0")
        self._label.setFixedHeight(self._height)
        self._label.adjustSize()
        self._label.setFixedHeight(self._height)
        self._label.move(0, 0)
        self._label.show()
        self._label.raise_()

    def update_scene_pos(self, scene_pos: QPointF) -> None:
        x = int(round(scene_pos.x()))
        y = int(round(scene_pos.y()))
        self._label.setText(f"{x}, {y}")
        self._label.adjustSize()
        self._label.setFixedHeight(self._height)
        self._label.move(0, 0)
        self._label.show()
        self._label.raise_()

    def raise_hud(self) -> None:
        self._label.raise_()
