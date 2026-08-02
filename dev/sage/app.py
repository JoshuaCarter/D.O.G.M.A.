"""Main window for the Stalker UI XML layout editor."""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime
from html import escape
from math import isfinite
from pathlib import Path

from sage.diaglog import get_logger, setup_logging

_log_file = get_logger("app")

from PyQt6.QtCore import QEvent, QPoint, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QCursor,
    QFont,
    QIcon,
    QKeySequence,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QShortcut,
    QTextCharFormat,
    QTextCursor,
)
from PyQt6.QtWidgets import (
    QAbstractSlider,
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .canvas import CanvasBoard, UiScene
from .descr_canvas import DescrBoard
from .descr_model import DescrDocument, looks_like_textures_descr
from .model import PATH_SEP, LayoutNode, default_layer_visible, layer_sections
from .db_unpack import check_anomaly_unpack_needed, ensure_anomaly_db_unpacked
from .settings import (
    LABEL_FONT_MAX,
    LABEL_FONT_MIN,
    SIDEBAR_MIN_WIDTH,
    clamp_grid_step,
    clamp_label_font_size,
    GRID_STEP_MAX,
    GRID_STEP_MIN,
    resolve_font_device_height,
    ensure_db_unpacked_and_roots,
    get_deploy_target,
    installs_configured,
    load_settings,
    normalize_custom_roots,
    normalize_preview_aspect,
    normalize_splitter_sizes,
    preview_aspect_combo_items,
    preview_aspect_stretch_x,
    push_recent_file,
    rescan_asset_roots,
    save_settings,
    set_deploy_target,
    summarize_custom_root,
    validate_anomaly_root,
    validate_gamma_root,
)
from .cache_store import PathIndex, asset_index_scan_needed, cache_dir
from .fonts import FontResolver
from .xml_locate import find_atlas_id_span, find_layout_path_span
from .rescan import (
    RescanDialog,
    apply_rescan_selection,
    collect_invalidate_roots,
)
from .strings import StringResolver
from .string_picker import StringPickerDialog
from .texture_picker import TexturePickerDialog
from .textures import TextureResolver, build_picker_root_groups
from .undo import GeoEdit, GeoState, PropEdit
from .xml_highlight import XmlHighlighter
from .xml_io import UiXmlDocument, sanitize_ui_xml_text

DOC_MODE_UI = "ui"
DOC_MODE_ATLAS = "atlas"

TAB_WYSIWYG = 0
TAB_XML = 1
TAB_LOG = 2

# Tree: mark overlapping canvas stack peers (stylesheet blocks setBackground).
_TREE_PEER_ROLE = int(Qt.ItemDataRole.UserRole) + 1
_TREE_LAYER_OFF_ROLE = int(Qt.ItemDataRole.UserRole) + 2

# Prop chrome greys: secondary text vs disabled / unassigned fields.
_GREY_DEFAULT = "#888888"
_GREY_INAPPLICABLE = "#555555"


class _TreePeerDelegate(QStyledItemDelegate):
    """Paint grey rows for stack peers / disabled-layer items."""

    def paint(self, painter, option, index) -> None:  # noqa: N802
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        layer_off = bool(index.data(_TREE_LAYER_OFF_ROLE))
        if layer_off:
            # Deselected layer: muted grey; ignore hover/selection styles.
            opt.state &= ~QStyle.StateFlag.State_MouseOver
            opt.state &= ~QStyle.StateFlag.State_Selected
            opt.palette.setColor(QPalette.ColorRole.Text, QColor(96, 96, 104))
            opt.palette.setColor(QPalette.ColorRole.HighlightedText, QColor(96, 96, 104))
            if selected:
                painter.fillRect(opt.rect, QColor(40, 40, 46))
        elif index.data(_TREE_PEER_ROLE) and not selected:
            painter.fillRect(opt.rect, QColor(58, 58, 64))
            opt.palette.setColor(QPalette.ColorRole.Text, QColor(200, 200, 210))
            opt.palette.setColor(QPalette.ColorRole.HighlightedText, QColor(200, 200, 210))
        super().paint(painter, opt, index)


class BusyOverlay(QWidget):
    """Dim the whole window and show an hourglass while a file loads."""

    _FACES = ("⌛", "⏳")

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._face = 0
        self._message = "Loading"
        self._detail = ""
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def set_message(self, text: str, detail: str = "") -> None:
        self._message = text or "Loading"
        self._detail = detail or ""
        self.update()

    @property
    def message(self) -> str:
        return self._message

    def start(self, message: str = "Loading", detail: str = "") -> None:
        self.set_message(message, detail)
        self._face = 0
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())
        self.raise_()
        self.show()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self.grabKeyboard()
        self._timer.start()
        self.update()

    def stop(self) -> None:
        self._timer.stop()
        self.releaseKeyboard()
        self.hide()
        self._detail = ""

    def _tick(self) -> None:
        self._face = 1 - self._face
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.fillRect(self.rect(), QColor(12, 12, 14, 190))
        cy = self.height() / 2 - 18
        painter.setPen(QPen(QColor(210, 210, 215)))
        emoji_font = QFont("Segoe UI Emoji", 36)
        painter.setFont(emoji_font)
        emoji_rect = self.rect().adjusted(0, int(cy) - 28, 0, 0)
        painter.drawText(
            emoji_rect,
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
            self._FACES[self._face],
        )
        text_font = QFont("Segoe UI", 11)
        painter.setFont(text_font)
        text_rect = self.rect().adjusted(0, int(cy) + 36, 0, 0)
        # Title on its own line; detail (filename / status) below.
        title = self._message
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
            title,
        )
        if self._detail:
            painter.setPen(QPen(QColor(154, 154, 160)))
            detail_font = QFont("Segoe UI", 10)
            painter.setFont(detail_font)
            detail_rect = self.rect().adjusted(24, int(cy) + 58, -24, 0)
            painter.drawText(
                detail_rect,
                int(
                    Qt.AlignmentFlag.AlignHCenter
                    | Qt.AlignmentFlag.AlignTop
                    | Qt.TextFlag.TextWordWrap
                ),
                self._detail,
            )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        event.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        event.accept()


class FitWidthLabel(QLabel):
    """Word-wrapped label that follows pane width without inflating the splitter."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setMinimumWidth(0)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        # Floor width so layout probes near 0 don't invent skyscraper wrap height.
        return super().heightForWidth(max(64, int(width)))

    def sizeHint(self) -> QSize:  # noqa: N802
        w = self.width() if self.width() > 64 else 120
        return QSize(120, self.heightForWidth(w))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(40, 0)


class FitContentScrollArea(QScrollArea):
    """Scroll area that prefers its content height; shrinks (and scrolls) if squeezed."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

    def sizeHint(self) -> QSize:  # noqa: N802
        w = self.widget()
        if w is None:
            return super().sizeHint()
        # Prefer content sizeHint — never current height (that sticks after a squeeze).
        lay = w.layout()
        h = w.sizeHint().height()
        if lay is not None:
            h = max(h, lay.sizeHint().height(), lay.minimumSize().height())
        return QSize(super().sizeHint().width(), h + 2 * self.frameWidth())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(80, 60)


class ElidedLabel(QLabel):
    """Single-line label that elides with … when the pane is narrow.

    Prefixed mode: ``{grey xml truncated}::{key}`` — xml elides first; key stays.
    Horizontal policy is Ignored so trailing action icons keep their fixed width.
    """

    _PREFIX_COLOR = _GREY_DEFAULT

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = text
        self._prefix = ""
        self._key = ""
        self.setWordWrap(False)
        self.setMinimumWidth(0)
        # Shrink freely; siblings (tool buttons) keep Fixed size at the row end.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._apply_elide()

    def set_full_text(self, text: str) -> None:
        self._prefix = ""
        self._key = ""
        self._full = text or ""
        # Leave stylesheet alone — FilePathRow owns placeholder / error colors.
        self._apply_elide()

    def set_xml_key(self, xml_name: str, key: str) -> None:
        """Show grey elided xml filename + ``::`` + text key."""
        self._prefix = (xml_name or "").strip()
        self._key = key or ""
        self._full = (
            f"{self._prefix}::{self._key}" if self._prefix else self._key
        )
        self.setStyleSheet("")
        self._apply_elide()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self) -> None:
        w = max(1, self.width())
        fm = self.fontMetrics()
        if self._prefix:
            sep = "::"
            key = self._key
            reserved = fm.horizontalAdvance(sep + key) + 2
            prefix_w = max(24, w - reserved)
            elided_prefix = fm.elidedText(
                self._prefix, Qt.TextElideMode.ElideRight, prefix_w
            )
            # If key alone still overflows, elide key after a short prefix budget.
            used = fm.horizontalAdvance(elided_prefix + sep)
            if used + fm.horizontalAdvance(key) > w:
                key = fm.elidedText(
                    key, Qt.TextElideMode.ElideRight, max(24, w - used)
                )
            self.setTextFormat(Qt.TextFormat.RichText)
            super().setText(
                f'<span style="color:{self._PREFIX_COLOR};">'
                f"{escape(elided_prefix)}{escape(sep)}</span>"
                f"{escape(key)}"
            )
            return
        self.setTextFormat(Qt.TextFormat.PlainText)
        elided = fm.elidedText(self._full, Qt.TextElideMode.ElideRight, w)
        super().setText(elided)


def windows_explorer_path(path: Path) -> str:
    """Absolute path with backslashes for Windows Explorer / paste into address bar."""
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    return str(resolved)


def _prop_tool_button(
    *,
    tip: str,
    icon: QIcon | None = None,
    text: str = "",
) -> QToolButton:
    """Fixed-size trailing action; never yields width to the elided label."""
    btn = QToolButton()
    btn.setToolTip(tip)
    btn.setAutoRaise(True)
    btn.setFixedSize(22, 22)
    btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    if icon is not None and not icon.isNull():
        btn.setIcon(icon)
        btn.setIconSize(QSize(14, 14))
    elif text:
        btn.setText(text)
    return btn


class FilePathRow(QWidget):
    """Elided value + trailing action icons (copy / clear / browse)."""

    clear_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._explorer_path = ""
        self._has_value = False
        self._browse_enabled = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.name = ElidedLabel("-")
        self.copy_btn = _prop_tool_button(tip="Copy full path", text="📋")
        self.copy_btn.clicked.connect(self._copy_path)
        self.clear_btn = _prop_tool_button(
            tip="Remove",
            icon=self.style().standardIcon(
                QStyle.StandardPixmap.SP_LineEditClearButton
            ),
        )
        # Fallback when the style has no clear glyph.
        if self.clear_btn.icon().isNull():
            self.clear_btn.setText("✕")
        self.clear_btn.clicked.connect(self.clear_clicked.emit)
        lay.addWidget(self.name, stretch=1)
        lay.addWidget(self.copy_btn, stretch=0)
        lay.addWidget(self.clear_btn, stretch=0)
        self._sync_trailing_icons()

    def clear(self, text: str = "-") -> None:
        self._explorer_path = ""
        self._has_value = False
        self.name.set_full_text(text)
        # Placeholders: <none>, (none), -, (empty …), (meta)
        self.name.setStyleSheet(f"color: {_GREY_INAPPLICABLE};")
        self.name.setToolTip("")
        self._sync_trailing_icons()
        self.setVisible(bool((text or "").strip()))

    def set_file(
        self,
        *,
        display: str,
        explorer_path: str = "",
        tip_extra: str = "",
        color: str = "",
        clearable: bool = True,
    ) -> None:
        self._explorer_path = explorer_path
        self._has_value = clearable
        self.name.set_full_text(display)
        self.name.setStyleSheet(f"color: {color};" if color else "")
        tip_parts = [p for p in (explorer_path, tip_extra) if p]
        self.name.setToolTip("\n".join(tip_parts))
        self._sync_trailing_icons()
        self.setVisible(bool((display or "").strip()))

    def set_xml_key(
        self,
        *,
        xml_name: str,
        key: str,
        explorer_path: str = "",
        tip_extra: str = "",
        clearable: bool = True,
    ) -> None:
        """One-line text ref: ``{xml grey}::{key}`` + trailing actions."""
        self._explorer_path = explorer_path
        self._has_value = clearable
        self.name.setStyleSheet("")
        self.name.set_xml_key(xml_name, key)
        tip_parts = [p for p in (explorer_path, tip_extra) if p]
        self.name.setToolTip("\n".join(tip_parts))
        self._sync_trailing_icons()
        self.setVisible(bool((key or xml_name or "").strip()))

    def set_clearable(self, clearable: bool) -> None:
        self._has_value = bool(clearable)
        self._sync_trailing_icons()

    def _sync_trailing_icons(self) -> None:
        """End icons keep fixed width; only the label elides when space is tight."""
        self.copy_btn.setVisible(bool(self._explorer_path))
        self.copy_btn.setEnabled(bool(self._explorer_path))
        # On editable browse rows, clear stays visible (disabled when empty) so
        # trailing icons don't jump or get clipped by a long path.
        show_clear = self._browse_enabled or self._has_value
        self.clear_btn.setVisible(show_clear)
        self.clear_btn.setEnabled(self._has_value)
        trailing = 0
        if self.copy_btn.isVisible():
            trailing += 22 + 4
        if show_clear:
            trailing += 22 + 4
        if getattr(self, "browse_btn", None) is not None and self._browse_enabled:
            trailing += 22 + 4
        self.setMinimumWidth(40 + trailing)

    def _copy_path(self) -> None:
        if not self._explorer_path:
            return
        QApplication.clipboard().setText(self._explorer_path)


class BrowseValueRow(FilePathRow):
    """FilePathRow plus a folder-button to find/change the value via a picker."""

    browse_clicked = pyqtSignal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        browse_tip: str = "Find and change…",
        clear_tip: str = "Remove",
    ) -> None:
        super().__init__(parent)
        self.clear_btn.setToolTip(clear_tip)
        self.browse_btn = _prop_tool_button(
            tip=browse_tip,
            icon=self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon),
        )
        self.browse_btn.setIconSize(QSize(16, 16))
        self.browse_btn.clicked.connect(self.browse_clicked.emit)
        self.layout().addWidget(self.browse_btn, stretch=0)
        self.set_browse_enabled(False)

    def set_browse_enabled(self, enabled: bool) -> None:
        self._browse_enabled = bool(enabled)
        # Browse stays visible whenever the row is editable so the end icons
        # don't jump; only the label shrinks when space is tight.
        self.browse_btn.setVisible(self._browse_enabled)
        self.browse_btn.setEnabled(self._browse_enabled)
        self._sync_trailing_icons()


class TextureValueRow(BrowseValueRow):
    """Texture: <none> 📁   or   Texture: [☑] path 📋 ✕ 📁."""

    def __init__(
        self,
        show_cb: QCheckBox,
        parent: QWidget | None = None,
        *,
        browse_tip: str = "Find and change texture (atlas / DDS)…",
    ) -> None:
        super().__init__(
            parent,
            browse_tip=browse_tip,
            clear_tip="Remove texture",
        )
        self._show_cb = show_cb
        self._has_texture = False
        show_cb.setText("")
        show_cb.setToolTip(
            "Preview only — hide this box’s texture on the canvas (not saved)."
        )
        show_cb.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        lay = self.layout()
        assert lay is not None
        lay.insertWidget(0, show_cb, stretch=0)
        self.set_has_texture(False)

    def has_texture(self) -> bool:
        return self._has_texture

    def set_has_texture(self, has: bool) -> None:
        """Show the preview checkbox only when a texture is assigned."""
        self._has_texture = bool(has)
        self._show_cb.setVisible(self._has_texture)
        self.set_clearable(self._has_texture)

    def clear(self, text: str = "<none>") -> None:
        super().clear(text)
        self.set_has_texture(False)
        # Keep the row itself visible while Texture props are shown.
        self.setVisible(True)

    def set_file(
        self,
        *,
        display: str,
        explorer_path: str = "",
        tip_extra: str = "",
        color: str = "",
        clearable: bool = True,
    ) -> None:
        super().set_file(
            display=display,
            explorer_path=explorer_path,
            tip_extra=tip_extra,
            color=color,
            clearable=clearable,
        )
        self.set_has_texture(True)
        self.setVisible(True)


TexturePathRow = BrowseValueRow  # back-compat alias


class LayerListWidget(QListWidget):
    """Layers list: label click selects the section; checkbox only toggles visibility."""

    label_clicked = pyqtSignal(object)  # QListWidgetItem

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

    def sizeHint(self) -> QSize:  # noqa: N802
        if self.count() <= 0:
            h = 40
        else:
            h = sum(self.sizeHintForRow(i) for i in range(self.count()))
        h += 2 * self.frameWidth() + 4
        return QSize(super().sizeHint().width(), h)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(50, 40)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        item = self.itemAt(event.position().toPoint())
        if item is not None and not self._click_on_checkbox(item, event.position().toPoint()):
            self.label_clicked.emit(item)
        super().mousePressEvent(event)

    def _click_on_checkbox(self, item: QListWidgetItem, pos: QPoint) -> bool:
        index = self.indexFromItem(item)
        if not index.isValid():
            return False
        opt = QStyleOptionViewItem()
        self.initViewItemOption(opt)
        opt.rect = self.visualRect(index)
        opt.features |= QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        opt.checkState = item.checkState()
        check = self.style().subElementRect(
            QStyle.SubElement.SE_ItemViewItemCheckIndicator, opt, self
        )
        # Grow slightly — indicator hit target is tight on some styles.
        return check.adjusted(-2, -2, 2, 2).contains(pos)


def _paths(values: list) -> list[Path]:
    return [Path(p) for p in values if p]


def _path_rich_text(path: str) -> str:
    """Grey ancestor segments; keep the final path name at normal color."""
    path = path or "-"
    if path == "-":
        return f'<span style="color:{_GREY_DEFAULT};">{escape(path)}</span>'
    if PATH_SEP not in path:
        return escape(path)
    parent, sep, name = path.rpartition(PATH_SEP)
    return (
        f'<span style="color:{_GREY_DEFAULT};">{escape(parent)}{escape(sep)}</span>'
        f"{escape(name)}"
    )


class _StartupLinkLabel(QLabel):
    """Left-aligned filename that looks like a link (blue; underline on hover)."""

    clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.setFixedHeight(18)
        self._path: Path | None = None
        self._active = False
        self._apply_style(hovered=False)

    def set_target(self, path: Path | None, *, missing: bool = False) -> None:
        self._path = path if path is not None and not missing else None
        self._active = self._path is not None
        if path is None:
            self.setText("")
            self.setToolTip("")
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.setEnabled(True)
        elif missing:
            self.setText(path.name or str(path))
            self.setToolTip(str(path))
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.setEnabled(False)
        else:
            self.setText(path.name or str(path))
            self.setToolTip(str(path))
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setEnabled(True)
        self._apply_style(hovered=False)

    def _apply_style(self, *, hovered: bool) -> None:
        if not self.isEnabled():
            color = "#5A5A62"
            underline = False
        elif not self._active:
            color = "#4A9EFF"
            underline = False
        else:
            color = "#7AB8FF" if hovered else "#4A9EFF"
            underline = hovered
        self.setStyleSheet(
            f"QLabel {{ color: {color}; background: transparent; border: none; }}"
        )
        font = self.font()
        font.setUnderline(underline)
        self.setFont(font)

    def enterEvent(self, event) -> None:  # noqa: N802
        if self._active:
            self._apply_style(hovered=True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._apply_style(hovered=False)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._active and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class StartupChooserOverlay(QWidget):
    """Dimmed full-window gate: Open… centered, Recents listed under it."""

    path_chosen = pyqtSignal(object)  # Path
    dismissed = pyqtSignal()

    _RECENT_SLOTS = 5
    _ROW_H = 18
    _COL_W = 360

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._start_dir = ""

        open_btn = QPushButton("Open…")
        open_btn.setObjectName("startupOpenBtn")
        open_btn.setFixedHeight(28)
        open_btn.setFixedWidth(self._COL_W)
        open_btn.setStyleSheet(
            "QPushButton#startupOpenBtn {"
            "  background-color: #25252A; color: #FFFFFF;"
            "  border: 1px solid #4A4A52; padding: 4px 12px;"
            "}"
            "QPushButton#startupOpenBtn:hover {"
            "  background-color: #35353C; color: #FFFFFF;"
            "}"
            "QPushButton#startupOpenBtn:pressed {"
            "  background-color: #264F78; color: #FFFFFF;"
            "}"
        )
        open_btn.clicked.connect(self._pick_open)

        self._recents_label = QLabel("Recents")
        self._recents_label.setFixedWidth(self._COL_W)
        self._recents_label.setFixedHeight(self._ROW_H)
        self._recents_label.setStyleSheet(
            "color: #8A8A92; font-weight: 400; background: transparent;"
        )

        self._recent_host = QWidget()
        self._recent_host.setFixedWidth(self._COL_W)
        self._recent_host.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._recent_layout = QVBoxLayout(self._recent_host)
        self._recent_layout.setContentsMargins(0, 0, 0, 0)
        self._recent_layout.setSpacing(6)
        self._recent_slots: list[_StartupLinkLabel] = []
        for _ in range(self._RECENT_SLOTS):
            slot = _StartupLinkLabel()
            slot.clicked.connect(self._on_slot_clicked)
            self._recent_layout.addWidget(slot)
            self._recent_slots.append(slot)
        host_h = (
            self._RECENT_SLOTS * self._ROW_H
            + (self._RECENT_SLOTS - 1) * self._recent_layout.spacing()
        )
        self._recent_host.setFixedHeight(host_h)

        gap = self._ROW_H  # one line between button and Recents
        below_btn = gap + self._ROW_H + host_h  # Recents label + list (+ gap)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addStretch(1)
        # Mirror the block under the button so Open… sits on the window center.
        outer.addSpacing(below_btn)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(open_btn)
        btn_row.addStretch(1)
        outer.addLayout(btn_row)

        outer.addSpacing(gap)

        recents_col = QVBoxLayout()
        recents_col.setContentsMargins(0, 0, 0, 0)
        recents_col.setSpacing(0)
        recents_col.addWidget(self._recents_label)
        recents_col.addWidget(self._recent_host)
        recents_row = QHBoxLayout()
        recents_row.addStretch(1)
        recents_row.addLayout(recents_col)
        recents_row.addStretch(1)
        outer.addLayout(recents_row)

        outer.addStretch(1)
        self.hide()

    def configure(self, recent: list[str], *, start_dir: str) -> None:
        self._start_dir = start_dir
        recent_paths = list(recent)[: self._RECENT_SLOTS]
        for i, slot in enumerate(self._recent_slots):
            if i >= len(recent_paths):
                slot.set_target(None)
                continue
            path = Path(recent_paths[i])
            slot.set_target(path, missing=not path.is_file())

    def _on_slot_clicked(self) -> None:
        slot = self.sender()
        if isinstance(slot, _StartupLinkLabel) and slot._path is not None:
            self._emit_path(slot._path)

    def start(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())
        self.raise_()
        self.show()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def stop(self) -> None:
        self.hide()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(12, 12, 14, 220))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        event.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.dismissed.emit()
            event.accept()
            return
        event.accept()

    def _pick_open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open UI XML",
            self._start_dir,
            "UI XML (*.xml);;All (*.*)",
        )
        if path:
            self._emit_path(Path(path))

    def _emit_path(self, path: Path) -> None:
        self.path_chosen.emit(path)


class InstallRootsDialog(QDialog):
    """Collect / edit Anomaly + GAMMA roots with marker validation."""

    def __init__(
        self,
        settings: dict,
        parent: QWidget | None = None,
        *,
        setup_mode: bool = False,
    ) -> None:
        super().__init__(parent)
        self._setup_mode = setup_mode
        self.settings = dict(settings)
        self.setWindowTitle("SAGE Setup" if setup_mode else "Settings")
        self.setModal(True)
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        if setup_mode:
            layout.addWidget(
                QLabel(
                    "<b>SAGE needs your game installs before the editor can open.</b><br/>"
                    "Anomaly must contain <code>tools\\db_unpacker.bat</code>.<br/>"
                    "GAMMA must contain <code>mods\\G.A.M.M.A. UI\\gamedata\\textures</code>."
                )
            )
        else:
            layout.addWidget(
                QLabel(
                    "Anomaly / GAMMA roots — asset paths are derived automatically. "
                    "Anomaly DB packs unpack when needed."
                )
            )

        form = QFormLayout()
        self.anomaly_edit = QLineEdit(str(self.settings.get("anomaly_root") or ""))
        self.gamma_edit = QLineEdit(str(self.settings.get("gamma_root") or ""))
        self.anomaly_status = QLabel("")
        self.gamma_status = QLabel("")
        form.addRow("Anomaly root", self._path_row(self.anomaly_edit))
        form.addRow("", self.anomaly_status)
        form.addRow("GAMMA root", self._path_row(self.gamma_edit))
        form.addRow("", self.gamma_status)
        layout.addLayout(form)

        # Optional extra folders — scanned for textures / descr / text / future assets.
        layout.addWidget(
            QLabel(
                "<b>Extra scan roots</b> (optional)<br/>"
                "Folders to scan for textures, textures_descr, text, etc. "
                "Resolved last (override Anomaly / GAMMA).<br/>"
                "Add the <b>project folder</b> or its <code>src</code> to edit before "
                "deploy, and/or the <b>deployed mod</b> "
                "(<code>GAMMA\\mods\\…</code>) so resolve matches the game. "
                "Add the folder itself — not a nested <code>textures\\ui</code> "
                "subfolder (that breaks game paths). Among extras, later entries "
                "win — put the deployed mod last if you use both."
            )
        )
        self.custom_list = QListWidget()
        self.custom_list.setMinimumHeight(90)
        for path in normalize_custom_roots(self.settings.get("custom_roots")):
            self.custom_list.addItem(path)
        layout.addWidget(self.custom_list)
        custom_btns = QHBoxLayout()
        add_btn = QPushButton("Add…")
        add_btn.clicked.connect(self._add_custom_root)
        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self._remove_custom_root)
        custom_btns.addWidget(add_btn)
        custom_btns.addWidget(remove_btn)
        custom_btns.addStretch(1)
        layout.addLayout(custom_btns)
        self.custom_status = QLabel("")
        self.custom_status.setStyleSheet("color: #9a9a9a;")
        layout.addWidget(self.custom_status)

        self.unpack_status = QLabel("")
        self.unpack_status.setWordWrap(True)
        self.unpack_status.setStyleSheet("color: #cca700;")
        layout.addWidget(self.unpack_status)

        self.anomaly_edit.textChanged.connect(self._revalidate)
        self.gamma_edit.textChanged.connect(self._revalidate)
        self.custom_list.model().rowsInserted.connect(lambda *_: self._refresh_custom_status())
        self.custom_list.model().rowsRemoved.connect(lambda *_: self._refresh_custom_status())

        buttons = QDialogButtonBox()
        if setup_mode:
            self._continue = buttons.addButton(
                "Continue", QDialogButtonBox.ButtonRole.AcceptRole
            )
            self._continue.clicked.connect(self._try_accept)
        else:
            buttons.addButton(QDialogButtonBox.StandardButton.Ok)
            buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(self._try_accept)
            buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._buttons = buttons
        self._revalidate()
        self._refresh_custom_status()

    def _path_row(self, edit: QLineEdit) -> QWidget:
        row = QWidget()
        hl = QHBoxLayout(row)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(edit, stretch=1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(lambda: self._browse_into(edit))
        hl.addWidget(browse)
        return row

    def _browse_into(self, edit: QLineEdit) -> None:
        start = edit.text().strip() or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Select folder", start)
        if path:
            edit.setText(path)

    def _custom_paths(self) -> list[str]:
        return [
            self.custom_list.item(i).text().strip()
            for i in range(self.custom_list.count())
            if self.custom_list.item(i) is not None
        ]

    def _add_custom_root(self) -> None:
        start = str(Path.home())
        existing = self._custom_paths()
        if existing:
            start = existing[-1]
        path = QFileDialog.getExistingDirectory(
            self, "Select extra folder to scan", start
        )
        if not path:
            return
        try:
            key = str(Path(path).expanduser().resolve())
        except OSError:
            key = path
        low = key.lower()
        for i in range(self.custom_list.count()):
            item = self.custom_list.item(i)
            if item is not None and item.text().strip().lower() == low:
                self.custom_list.setCurrentRow(i)
                self._refresh_custom_status()
                return
        self.custom_list.addItem(key)
        self._refresh_custom_status()

    def _remove_custom_root(self) -> None:
        row = self.custom_list.currentRow()
        if row >= 0:
            self.custom_list.takeItem(row)
            self._refresh_custom_status()

    def _refresh_custom_status(self) -> None:
        paths = normalize_custom_roots(self._custom_paths())
        if not paths:
            self.custom_status.setText("None — editor works with Anomaly + GAMMA only.")
            return
        notes = [f"{Path(p).name}: {summarize_custom_root(p)}" for p in paths]
        self.custom_status.setText(f"{len(paths)} extra root(s) — " + "; ".join(notes))

    def _set_status(self, label: QLabel, ok: bool, detail: str) -> None:
        if ok:
            label.setText(f"OK — {detail}")
            label.setStyleSheet("color: #6a9955;")
        else:
            label.setText(detail)
            label.setStyleSheet("color: #f44747;")

    def _revalidate(self) -> None:
        a_ok, a_msg = validate_anomaly_root(self.anomaly_edit.text())
        g_ok, g_msg = validate_gamma_root(self.gamma_edit.text())
        self._set_status(self.anomaly_status, a_ok, a_msg)
        self._set_status(self.gamma_status, g_ok, g_msg)
        if a_ok:
            need = check_anomaly_unpack_needed(self.anomaly_edit.text())
            if need.needed:
                dest = need.out_root or Path("tools/_unpacked")
                self.unpack_status.setText(
                    f"Anomaly data will unpack into {dest} when you continue "
                    f"(missing: {', '.join(need.missing)}). This may take a minute."
                )
                self.unpack_status.setStyleSheet("color: #cca700;")
            else:
                where = need.present_root or need.out_root
                self.unpack_status.setText(
                    f"Anomaly unpack OK — using {where}" if where else ""
                )
                self.unpack_status.setStyleSheet("color: #6a9955;")
        else:
            self.unpack_status.setText("")
        ready = a_ok and g_ok
        if self._setup_mode:
            self._continue.setEnabled(ready)
        else:
            ok_btn = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
            if ok_btn is not None:
                ok_btn.setEnabled(ready)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self.setEnabled(not busy)
        if busy:
            QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
            if message:
                self.unpack_status.setText(message)
                self.unpack_status.setStyleSheet("color: #cca700;")
        else:
            QApplication.restoreOverrideCursor()
        QApplication.processEvents()

    def _confirm_and_unpack(self, anomaly_root: str) -> bool:
        """Warn if unpack is needed, then unpack while this dialog stays open."""
        need = check_anomaly_unpack_needed(anomaly_root)
        if not need.needed:
            return True
        dest = need.out_root or Path("tools/_unpacked")
        miss = ", ".join(need.missing) if need.missing else "UI assets"
        reply = QMessageBox.warning(
            self,
            "Unpack Anomaly data",
            "Anomaly UI data isn’t unpacked yet.\n\n"
            f"Target:\n{dest}\n\n"
            f"Missing: {miss}\n\n"
            "SAGE will unpack configs.db0 and textures_ui.db0 now. "
            "This may take a minute.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Ok,
        )
        if reply != QMessageBox.StandardButton.Ok:
            return False

        self._set_busy(True, f"Unpacking Anomaly data into {dest}…")
        try:
            result = ensure_anomaly_db_unpacked(anomaly_root=anomaly_root)
        finally:
            self._set_busy(False)

        if result.errors:
            QMessageBox.critical(
                self,
                "Unpack failed",
                "Could not unpack Anomaly data:\n\n" + "\n".join(result.errors),
            )
            self._revalidate()
            return False

        unpacked = ", ".join(result.unpacked) if result.unpacked else "done"
        self.unpack_status.setText(f"Unpacked OK — {unpacked}")
        self.unpack_status.setStyleSheet("color: #6a9955;")
        QApplication.processEvents()
        return True

    def _try_accept(self) -> None:
        a_ok, a_msg = validate_anomaly_root(self.anomaly_edit.text())
        g_ok, g_msg = validate_gamma_root(self.gamma_edit.text())
        if not a_ok or not g_ok:
            QMessageBox.warning(
                self,
                "Invalid installs",
                "Both roots must validate before continuing:\n\n"
                f"Anomaly: {a_msg}\nGAMMA: {g_msg}",
            )
            return
        out = dict(self.settings)
        out["anomaly_root"] = self.anomaly_edit.text().strip()
        out["gamma_root"] = self.gamma_edit.text().strip()
        out["custom_roots"] = normalize_custom_roots(self._custom_paths())
        if not self._confirm_and_unpack(out["anomaly_root"]):
            return
        self._set_busy(True, "Scanning asset folders under Anomaly / GAMMA…")
        try:
            rescan_asset_roots(out)
        finally:
            self._set_busy(False)
        n_tex = len(out.get("gamedata_texture_roots") or [])
        n_descr = len(out.get("gamedata_descr_roots") or [])
        n_text = len(out.get("gamedata_text_roots") or [])
        self.unpack_status.setText(
            f"Scan done — {n_tex} texture, {n_descr} descr, {n_text} text folders"
        )
        self.unpack_status.setStyleSheet("color: #6a9955;")
        QApplication.processEvents()
        self._result = out
        self.accept()

    def result_settings(self) -> dict:
        if getattr(self, "_result", None) is not None:
            return dict(self._result)
        out = dict(self.settings)
        out["anomaly_root"] = self.anomaly_edit.text().strip()
        out["gamma_root"] = self.gamma_edit.text().strip()
        out["custom_roots"] = normalize_custom_roots(self._custom_paths())
        return out


# Back-compat alias used by Edit → Settings…
SettingsDialog = InstallRootsDialog


class MainWindow(QMainWindow):
    def __init__(self, initial: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle("D.O.G.M.A. Stalker Anomaly Gui Editor")
        self.setAutoFillBackground(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setPalette(_dark_palette())
        self.settings = load_settings()
        self._restore_window_geometry()
        # Editor-only preview stretch; never written to settings / .xml.meta.
        self._preview_aspect = ""
        self.doc = UiXmlDocument()
        self.descr_doc = DescrDocument()
        self._doc_mode = DOC_MODE_UI
        # Roots from settings; path shards (+ thumbs) build on first bind.
        self.path_index = PathIndex.load()
        self.path_index.configure_installs(
            self.settings.get("anomaly_root") or "",
            self.settings.get("gamma_root") or "",
        )
        self.resolver = self._make_resolver()
        self.strings = self._make_string_resolver()
        self.fonts = self._make_font_resolver()
        self._resources_ready = False
        self._closing = False
        self.scene = UiScene(
            self.resolver,
            strings=self.strings,
            fonts=self.fonts,
            label_font_size=clamp_label_font_size(self.settings.get("label_font_size")),
            show_element_labels=bool(self.settings.get("show_element_labels", False)),
            show_box_border=bool(self.settings.get("show_box_border", False)),
            show_box_fill=bool(self.settings.get("show_box_fill", False)),
        )
        self.canvas_board = CanvasBoard(self.scene)
        self.canvas = self.canvas_board.canvas
        self.canvas_board.set_rulers_visible(
            bool(self.settings.get("show_rulers", True))
        )
        self.descr_board = DescrBoard(self.resolver)
        grid_on = bool(self.settings.get("show_grid", False))
        self.scene.set_pixel_grid_visible(grid_on)
        self.descr_board.scene.set_pixel_grid_visible(grid_on)
        grid_step = clamp_grid_step(self.settings.get("grid_step"))
        self.scene.set_pixel_grid_step(grid_step)
        self.descr_board.scene.set_pixel_grid_step(grid_step)
        self._apply_preview_aspect()
        self.canvas.fit_stage()
        self.wysiwyg_stack = QStackedWidget()
        self.wysiwyg_stack.addWidget(self.canvas_board)  # index 0 = UI
        self.wysiwyg_stack.addWidget(self.descr_board)  # index 1 = atlas
        self._tree_items_by_path: dict[str, QTreeWidgetItem] = {}
        self._tree_peer_paths: set[str] = set()
        self.raw_editor = QPlainTextEdit()
        self.raw_editor.setPlaceholderText("Open a UI or textures_descr XML…")
        self.raw_editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.raw_editor.setTabStopDistance(
            self.raw_editor.fontMetrics().horizontalAdvance(" ") * 4
        )
        # Dark+ editor chrome (matches VS Code XML highlighting)
        self.raw_editor.setStyleSheet(
            "QPlainTextEdit {"
            " background-color: #1E1E1E;"
            " color: #D4D4D4;"
            " selection-background-color: #264F78;"
            " border: none;"
            "}"
        )
        self._raw_highlighter = XmlHighlighter(self.raw_editor.document())
        self.raw_editor.setUndoRedoEnabled(True)
        self.raw_editor.textChanged.connect(self._on_raw_text_changed)
        self.raw_editor.document().undoAvailable.connect(lambda _a: self._update_undo_actions())
        self.raw_editor.document().redoAvailable.connect(lambda _a: self._update_undo_actions())
        self._raw_dirty = False
        self._tab_guard = False
        self._preview_needs_raw_sync = False
        self._find_matches: list[int] = []
        self._find_index = -1
        self._xml_sel_path: str = ""
        self._xml_sel_atlas: str = ""

        self.xml_page = QWidget()
        xml_layout = QVBoxLayout(self.xml_page)
        xml_layout.setContentsMargins(0, 0, 0, 0)
        xml_layout.setSpacing(0)

        self.find_bar = QWidget()
        self.find_bar.setObjectName("xmlFindBar")
        self.find_bar.setStyleSheet(
            "#xmlFindBar {"
            " background-color: #252526;"
            " border-bottom: 1px solid #3C3C3C;"
            "}"
            "#xmlFindBar QLineEdit {"
            " background-color: #3C3C3C;"
            " color: #CCCCCC;"
            " border: 1px solid #3C3C3C;"
            " border-radius: 2px;"
            " padding: 2px 6px;"
            " selection-background-color: #264F78;"
            "}"
            "#xmlFindBar QLabel { color: #CCCCCC; }"
            "#xmlFindBar QToolButton {"
            " background: transparent;"
            " color: #CCCCCC;"
            " border: none;"
            " padding: 2px 6px;"
            "}"
            "#xmlFindBar QToolButton:hover { background-color: #3C3C3C; }"
            "#xmlFindBar QToolButton:disabled { color: #666666; }"
        )
        find_row = QHBoxLayout(self.find_bar)
        find_row.setContentsMargins(8, 4, 6, 4)
        find_row.setSpacing(4)
        find_row.addStretch(1)
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("Find")
        self.find_edit.setClearButtonEnabled(False)
        self.find_edit.setFixedWidth(220)
        self.find_edit.textChanged.connect(self._on_find_text_changed)
        self.find_edit.returnPressed.connect(self._find_next)
        find_row.addWidget(self.find_edit)
        self.find_count = QLabel("")
        self.find_count.setMinimumWidth(56)
        self.find_count.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        find_row.addWidget(self.find_count)
        self.find_prev_btn = QToolButton()
        self.find_prev_btn.setText("↑")
        self.find_prev_btn.setToolTip("Previous match (Shift+F3)")
        self.find_prev_btn.clicked.connect(self._find_prev)
        find_row.addWidget(self.find_prev_btn)
        self.find_next_btn = QToolButton()
        self.find_next_btn.setText("↓")
        self.find_next_btn.setToolTip("Next match (F3 / Enter)")
        self.find_next_btn.clicked.connect(self._find_next)
        find_row.addWidget(self.find_next_btn)
        self.find_close_btn = QToolButton()
        self.find_close_btn.setText("×")
        self.find_close_btn.setToolTip("Close find (Esc)")
        self.find_close_btn.clicked.connect(self._hide_find_bar)
        find_row.addWidget(self.find_close_btn)
        self.find_bar.hide()

        xml_layout.addWidget(self.find_bar)
        xml_layout.addWidget(self.raw_editor, stretch=1)

        self._find_esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self.xml_page)
        self._find_esc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._find_esc.activated.connect(self._hide_find_bar)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("Editor log…")
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.log_view.setStyleSheet(
            "QPlainTextEdit {"
            " background-color: #1E1E1E;"
            " color: #D4D4D4;"
            " selection-background-color: #264F78;"
            " border: none;"
            "}"
        )
        self._log("info", "SAGE ready")

        self.editor_tabs = QTabWidget()
        self.editor_tabs.addTab(self.wysiwyg_stack, "WYSIWYG")
        self.editor_tabs.addTab(self.xml_page, "XML")
        self.editor_tabs.addTab(self.log_view, "Log")
        self.editor_tabs.currentChanged.connect(self._on_editor_tab_changed)

        self._updating_props = False

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMouseTracking(True)
        self.tree.setItemDelegate(_TreePeerDelegate(self.tree))
        self.tree.setStyleSheet(
            "QTreeWidget {"
            " background-color: #1E1E22;"
            " color: #D4D4D4;"
            " border: none;"
            " outline: none;"
            "}"
            "QTreeWidget::item { padding: 2px 4px; }"
            "QTreeWidget::item:hover:!selected {"
            " background-color: #0D47A1;"
            " color: #E3F2FD;"
            "}"
            "QTreeWidget::item:selected {"
            " background-color: #1A3568;"
            " color: #B0CFFF;"
            "}"
            "QTreeWidget::item:selected:hover {"
            " background-color: #1A3568;"
            " color: #B0CFFF;"
            "}"
        )
        self.tree.itemClicked.connect(self._on_tree_clicked)
        self.tree.itemEntered.connect(self._on_tree_item_entered)
        self.tree.viewport().installEventFilter(self)

        self.layers = LayerListWidget()
        self.layers.itemChanged.connect(self._on_layer_toggled)
        self.layers.label_clicked.connect(self._on_layer_label_clicked)

        self.prop_path = FitWidthLabel("-")
        self.prop_path.setTextFormat(Qt.TextFormat.RichText)
        self.prop_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.prop_meta_note = FitWidthLabel("")
        self.prop_meta_note.setStyleSheet("color: gray;")
        self.prop_meta_note.hide()
        self.edit_x = QLineEdit()
        self.edit_y = QLineEdit()
        self.edit_w = QLineEdit()
        self.edit_h = QLineEdit()
        self.edit_stretch = QCheckBox("stretch")
        self.edit_show_texture = QCheckBox()
        self.prop_texture = TextureValueRow(self.edit_show_texture)
        self.prop_texture.browse_clicked.connect(self._browse_texture)
        self.prop_texture.clear_clicked.connect(self._clear_texture)
        self.prop_text = BrowseValueRow(
            browse_tip="Find and change text…",
            clear_tip="Remove text",
        )
        self.prop_text.browse_clicked.connect(self._browse_text)
        self.prop_text.clear_clicked.connect(self._clear_text)

        def _combo(items: list[tuple[str, str]], *, editable: bool = False) -> QComboBox:
            cb = QComboBox()
            cb.setEditable(editable)
            if editable:
                cb.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            for value, label in items:
                cb.addItem(label, value)
            return cb

        # Engine defaults when XML omits the attr (CUIXmlInit / CUILines).
        self.edit_font = _combo([("", "default (letterica16)")], editable=True)
        self.edit_font.setMinimumWidth(self.edit_font.sizeHint().width() + 10)
        self.edit_align = _combo(
            [
                ("", "default (left)"),
                ("l", "Left (l)"),
                ("c", "Center (c)"),
                ("r", "Right (r)"),
            ]
        )
        self.edit_vert_align = _combo(
            [
                ("", "default (top)"),
                ("t", "Top (t)"),
                ("c", "Center (c)"),
                ("b", "Bottom (b)"),
            ]
        )
        self.edit_complex_mode = QCheckBox("complex_mode")
        self.edit_complex_mode.setToolTip(
            "Engine flComplexMode — wrap / %c[…] color codes (0/1)."
        )
        self.edit_text_r = QLineEdit()
        self.edit_text_g = QLineEdit()
        self.edit_text_b = QLineEdit()
        self.edit_text_a = QLineEdit()
        for ed, tip in (
            (self.edit_text_r, "r 0–255 (blank = omit)"),
            (self.edit_text_g, "g 0–255 (blank = omit)"),
            (self.edit_text_b, "b 0–255 (blank = omit)"),
            (self.edit_text_a, "a 0–255 (blank = omit; engine default 255)"),
        ):
            ed.setPlaceholderText("—")
            ed.setToolTip(tip)
            ed.setMaximumWidth(48)
        self.edit_text_x = QLineEdit()
        self.edit_text_y = QLineEdit()
        for ed, tip in (
            (self.edit_text_x, "Text offset x on <text> (CUILines m_TextOffset)"),
            (self.edit_text_y, "Text offset y on <text>"),
        ):
            ed.setPlaceholderText("0")
            ed.setToolTip(tip)
            ed.setMaximumWidth(56)
        self.edit_left_ident = QLineEdit()
        self.edit_right_ident = QLineEdit()
        self.edit_top_indent = QLineEdit()
        self.edit_bottom_indent = QLineEdit()
        self.edit_vert_interval = QLineEdit()
        for ed, tip in (
            (self.edit_left_ident, "left_ident (engine spelling)"),
            (self.edit_right_ident, "right_ident (engine spelling)"),
            (self.edit_top_indent, "top_indent"),
            (self.edit_bottom_indent, "bottom_indent"),
            (self.edit_vert_interval, "vert_interval"),
        ):
            ed.setPlaceholderText("—")
            ed.setToolTip(tip)
        self.edit_always_show_scroll = QCheckBox("always_show_scroll")
        self.edit_always_show_scroll.setToolTip(
            "ScrollView flag (engine default 1; UI XML usually 0)."
        )

        self._props_geo_before: GeoState | None = None
        for ed in (self.edit_x, self.edit_y, self.edit_w, self.edit_h):
            ed.textChanged.connect(self._on_props_changed)
            ed.editingFinished.connect(self._on_props_editing_finished)

        def _compact_fields(
            *parts: tuple[str, QWidget],
            field_width: int = 40,
            group_gap: int = 8,
        ) -> QWidget:
            """Packed ``x{2px}{input}{gap}y{2px}{input}…`` (no stretch between label/input)."""
            row = QWidget()
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(0)
            for i, (label, widget) in enumerate(parts):
                if i:
                    lay.addSpacing(group_gap)
                if label:
                    lab = QLabel(label)
                    lab.setSizePolicy(
                        QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred
                    )
                    lay.addWidget(lab, 0)
                    lay.addSpacing(2)
                if isinstance(widget, QLineEdit):
                    widget.setFixedWidth(field_width)
                widget.setSizePolicy(
                    QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
                )
                lay.addWidget(widget, 0)
            lay.addStretch(1)
            return row

        def _xy_row(a_label: str, a: QLineEdit, b_label: str, b: QLineEdit) -> QWidget:
            return _compact_fields((a_label, a), (b_label, b), field_width=48)

        def _multi_row(*parts: tuple[str, QWidget]) -> QWidget:
            return _compact_fields(*parts, field_width=40)

        # Own the form labels so meta can tint Pos / Size (Texture box title separately).
        self.prop_label_path = QLabel("Path:")
        self.prop_label_pos = QLabel("Pos:")
        self.prop_label_size = QLabel("Size:")
        self.prop_label_texture = QLabel("Path:")
        self.prop_label_text = QLabel("Path:")
        self.prop_label_font = QLabel("Font:")
        self.prop_label_align = QLabel("Align:")
        self.prop_label_color = QLabel("Color:")
        self.prop_label_text_off = QLabel("Pos:")
        self.prop_label_scroll = QLabel("Pad:")
        self._meta_prop_style = "color: #E67E22;"

        def _form() -> QFormLayout:
            lay = QFormLayout()
            lay.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setHorizontalSpacing(8)
            lay.setVerticalSpacing(10)
            return lay

        props = QWidget()
        self._props_widget = props
        props.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        props_l = QVBoxLayout(props)
        props_l.setContentsMargins(0, 0, 0, 0)
        props_l.setSpacing(8)

        # Path / Pos / Size — unboxed.
        core = QWidget()
        pf = _form()
        core.setLayout(pf)
        pf.addRow(self.prop_label_path, self.prop_path)
        pf.addRow("", self.prop_meta_note)
        pf.addRow(self.prop_label_pos, _xy_row("X", self.edit_x, "Y", self.edit_y))
        pf.addRow(self.prop_label_size, _xy_row("W", self.edit_w, "H", self.edit_h))
        props_l.addWidget(core)

        # Texture box: Path (+ UV under it), stretch — always shown; disable when N/A.
        self._texture_group = QGroupBox("Texture")
        tf = _form()
        self._texture_group.setLayout(tf)
        self.prop_texture_uv = QLabel("<none>")
        self.prop_texture_uv.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.prop_texture_uv.setStyleSheet(f"color: {_GREY_DEFAULT};")
        self._texture_path_stack = QWidget()
        tex_path_l = QVBoxLayout(self._texture_path_stack)
        tex_path_l.setContentsMargins(0, 0, 0, 0)
        tex_path_l.setSpacing(1)
        tex_path_l.addWidget(self.prop_texture)
        tex_path_l.addWidget(self.prop_texture_uv)
        tf.addRow(self.prop_label_texture, self._texture_path_stack)
        tf.addRow("", self.edit_stretch)
        props_l.addWidget(self._texture_group)

        # Text box: Path, Font, Pos, Align HV, Color.
        self._text_group = QGroupBox("Text")
        xf = _form()
        self._text_group.setLayout(xf)
        xf.addRow(self.prop_label_text, self.prop_text)
        xf.addRow(self.prop_label_font, self.edit_font)
        self._text_xy_widget = _xy_row("X", self.edit_text_x, "Y", self.edit_text_y)
        xf.addRow(self.prop_label_text_off, self._text_xy_widget)
        self._align_row = QWidget()
        align_l = QHBoxLayout(self._align_row)
        align_l.setContentsMargins(0, 0, 0, 0)
        align_l.setSpacing(4)
        align_l.addWidget(QLabel("H"))
        align_l.addWidget(self.edit_align, stretch=1)
        align_l.addSpacing(8)
        align_l.addWidget(QLabel("V"))
        align_l.addWidget(self.edit_vert_align, stretch=1)
        xf.addRow(self.prop_label_align, self._align_row)
        self._color_row = _multi_row(
            ("R", self.edit_text_r),
            ("G", self.edit_text_g),
            ("B", self.edit_text_b),
            ("A", self.edit_text_a),
        )
        xf.addRow(self.prop_label_color, self._color_row)
        self._text_flags_row = QWidget()
        text_flags_l = QHBoxLayout(self._text_flags_row)
        text_flags_l.setContentsMargins(0, 0, 0, 0)
        text_flags_l.addWidget(self.edit_complex_mode)
        text_flags_l.addStretch(1)
        xf.addRow("", self._text_flags_row)
        props_l.addWidget(self._text_group)

        # Scroll box (only for scroll-capable widgets).
        self._scroll_group = QGroupBox("Scroll")
        sf = _form()
        self._scroll_group.setLayout(sf)
        self._scroll_pad_row = _multi_row(
            ("L", self.edit_left_ident),
            ("R", self.edit_right_ident),
        )
        self._scroll_indent_row = _multi_row(
            ("T", self.edit_top_indent),
            ("B", self.edit_bottom_indent),
        )
        self._scroll_misc_row = _multi_row(
            ("vert", self.edit_vert_interval),
            ("", self.edit_always_show_scroll),
        )
        sf.addRow(self.prop_label_scroll, self._scroll_pad_row)
        sf.addRow("", self._scroll_indent_row)
        sf.addRow("", self._scroll_misc_row)
        props_l.addWidget(self._scroll_group)

        self._texture_prop_widgets: list[QWidget] = [self._texture_group]
        self._text_prop_widgets: list[QWidget] = [self._text_group]
        self._scroll_prop_widgets: list[QWidget] = [self._scroll_group]
        self._ui_only_prop_widgets: list[QWidget] = [
            self.prop_meta_note,
            *self._texture_prop_widgets,
            *self._text_prop_widgets,
            *self._scroll_prop_widgets,
        ]

        def _section_header(text: str) -> QLabel:
            lab = QLabel(text)
            font = lab.font()
            font.setBold(True)
            # Body UI is 9pt; headers one step up (10px was smaller than 9pt).
            font.setPointSize(10)
            lab.setFont(font)
            lab.setContentsMargins(0, 10, 0, 0)
            return lab

        # Options: full-width strip under the menu bar (content centered).
        self.options_bar = QWidget()
        self.options_bar.setObjectName("optionsBar")
        self.options_bar.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        options_l = QHBoxLayout(self.options_bar)
        # ~50% taller than the old 4px pad; keep controls vertically centered.
        options_l.setContentsMargins(9, 12, 9, 12)
        options_l.setSpacing(16)
        options_l.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.tool_border = QCheckBox("Show borders")
        self.tool_border.setChecked(bool(self.settings.get("show_box_border", False)))
        self.tool_fill = QCheckBox("Fill selected")
        self.tool_fill.setChecked(bool(self.settings.get("show_box_fill", False)))
        self.tool_labels = QCheckBox("Show labels")
        self.tool_labels.setChecked(bool(self.settings.get("show_element_labels", False)))
        self.tool_rulers = QCheckBox("Show guide lines")
        self.tool_rulers.setChecked(bool(self.settings.get("show_rulers", True)))
        self.tool_rulers.setToolTip("Show top/left rulers and guide lines")
        self.tool_grid = QCheckBox("Pixel grid")
        self.tool_grid.setChecked(bool(self.settings.get("show_grid", False)))
        self.tool_grid.setToolTip(
            "Grey grid lines on the HUD/sheet (every N scene pixels; "
            "every 10th line stronger)"
        )
        self.tool_grid_step = QSpinBox()
        self.tool_grid_step.setRange(GRID_STEP_MIN, GRID_STEP_MAX)
        self.tool_grid_step.setSingleStep(1)
        self.tool_grid_step.setValue(clamp_grid_step(self.settings.get("grid_step")))
        self.tool_grid_step.setFixedWidth(52)
        self.tool_grid_step.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.tool_grid_step.setToolTip("Draw a grid line every N scene pixels")
        self.tool_grid_step.setEnabled(bool(self.settings.get("show_grid", False)))
        # Separate label so the edit caret can't sit after a "px" suffix.
        self.tool_grid_step_unit = QLabel("px")
        self.tool_grid_step_unit.setEnabled(bool(self.settings.get("show_grid", False)))
        self.tool_aspect_label = QLabel("Aspect")
        self.tool_aspect = QComboBox()
        self.tool_aspect.setToolTip(
            "Preview-only stretch of the UI stage (does not change or save XML). "
            "Bitmap text is counter-scaled so glyphs stay unstretched."
        )
        self._rebuild_aspect_combo()
        self.tool_font = QSpinBox()
        self.tool_font.setRange(LABEL_FONT_MIN, LABEL_FONT_MAX)
        self.tool_font.setSingleStep(1)
        self.tool_font.setValue(clamp_label_font_size(self.settings.get("label_font_size")))
        self.tool_font.setFixedWidth(52)
        self.tool_font.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.tool_font_unit = QLabel("pt")
        self.tool_zoom = QSlider(Qt.Orientation.Horizontal)
        self.tool_zoom.setRange(100, 1000)
        self.tool_zoom.setSingleStep(5)
        self.tool_zoom.setPageStep(25)
        self.tool_zoom.setTickInterval(100)
        self.tool_zoom.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.tool_zoom.setValue(100)
        self.tool_zoom.setMinimumWidth(140)
        self.tool_zoom.setMaximumWidth(200)
        self.tool_zoom.setToolTip("Canvas zoom: 100% → 1000%")
        self.tool_zoom.setStyleSheet(
            "QSlider::groove:horizontal {"
            "  height: 4px;"
            "  background: #3A3A40;"
            "  border-radius: 2px;"
            "}"
            "QSlider::handle:horizontal {"
            "  background: #6a9955;"
            "  border: none;"
            "  width: 12px;"
            "  height: 12px;"
            "  margin: -4px 0;"
            "  border-radius: 6px;"
            "}"
            "QSlider::handle:horizontal:hover {"
            "  background: #7cb068;"
            "}"
        )
        self.tool_zoom_label = QLabel("100%")
        self.tool_zoom_label.setMinimumWidth(48)
        self._updating_zoom_slider = False
        options_l.addStretch(1)
        for w in (
            self.tool_border,
            self.tool_fill,
            self.tool_labels,
            self.tool_rulers,
            self.tool_grid,
            self.tool_grid_step,
            self.tool_grid_step_unit,
            self.tool_aspect_label,
            self.tool_aspect,
            QLabel("Label size"),
            self.tool_font,
            self.tool_font_unit,
            QLabel("Zoom"),
            self.tool_zoom,
            self.tool_zoom_label,
        ):
            options_l.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)
        options_l.addStretch(1)

        # Left: Layers (fit) + Tree (fill). Right: Properties (fit) + Redo/Undo (fill).
        left = QWidget()
        self._left_sidebar = left
        left.setMinimumWidth(SIDEBAR_MIN_WIDTH)
        left.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.setSpacing(6)
        left_body = QWidget()
        left_body_l = QVBoxLayout(left_body)
        self._left_body_l = left_body_l
        left_body_l.setContentsMargins(9, 0, 9, 9)
        self._layers_section_label = _section_header("Layers")
        left_body_l.addWidget(self._layers_section_label)
        left_body_l.addWidget(self.layers, stretch=0)
        self._list_section_label = _section_header("Tree")
        left_body_l.addWidget(self._list_section_label)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.tree.setMinimumHeight(120)
        self.tree.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        left_body_l.addWidget(self.tree, stretch=1)
        left_l.addWidget(left_body, stretch=1)

        right = QWidget()
        self._right_sidebar = right
        right.setMinimumWidth(SIDEBAR_MIN_WIDTH)
        right.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)
        right_l.setSpacing(6)
        right_body = QWidget()
        right_body_l = QVBoxLayout(right_body)
        self._right_body_l = right_body_l
        right_body_l.setContentsMargins(9, 0, 9, 9)
        right_body_l.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._props_section_label = _section_header("Properties")
        right_body_l.addWidget(self._props_section_label)
        self.props_scroll = FitContentScrollArea()
        self.props_scroll.setWidget(props)
        right_body_l.addWidget(self.props_scroll, stretch=0)
        self._undo_section_label = _section_header("Redo/Undo")
        right_body_l.addWidget(self._undo_section_label)
        self.undo_list = QListWidget()
        self.undo_list.setObjectName("undoHistoryList")
        self.undo_list.setToolTip(
            "Redo (top, 1 = next Ctrl+Y), then Undo (1 = next Ctrl+Z). "
            "Stack keeps up to 100."
        )
        self.undo_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.undo_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.undo_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.undo_list.setMinimumHeight(80)
        self.undo_list.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.undo_list.setWordWrap(False)
        self.undo_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        right_body_l.addWidget(self.undo_list, stretch=1)
        right_l.addWidget(right_body, stretch=1)

        self.split = QSplitter()
        self.split.addWidget(left)
        self.split.addWidget(self.editor_tabs)
        self.split.addWidget(right)
        # Fixed-width sidebars; only the editor stretches with the window.
        # Content must not change sidebar widths - only manual splitter drag.
        self.split.setChildrenCollapsible(False)
        self.split.setStretchFactor(0, 0)
        self.split.setStretchFactor(1, 1)
        self.split.setStretchFactor(2, 0)
        self._splitter_save_timer = QTimer(self)
        self._splitter_save_timer.setSingleShot(True)
        self._splitter_save_timer.setInterval(200)
        self._splitter_save_timer.timeout.connect(self._save_splitter_sizes)
        self.split.splitterMoved.connect(self._on_splitter_moved)
        # Sizes applied again when workspace is revealed (hidden splitters ignore setSizes).
        self._apply_splitter_sizes()

        self._workspace = QWidget()
        workspace_l = QVBoxLayout(self._workspace)
        workspace_l.setContentsMargins(0, 0, 0, 0)
        workspace_l.setSpacing(0)
        workspace_l.addWidget(self.options_bar, stretch=0)
        workspace_l.addWidget(self.split, stretch=1)
        self.setCentralWidget(self._workspace)
        # Keep workspace hidden until a file is chosen and loaded.
        self._workspace_revealed = False
        self._workspace.hide()
        self.menuBar().hide()
        self.statusBar().hide()
        self._startup = StartupChooserOverlay(self)
        self._startup.path_chosen.connect(self._on_startup_path_chosen)
        self._startup.dismissed.connect(self._on_startup_dismissed)
        self._busy = BusyOverlay(self)
        self._busy_open = False

        self.scene.selection_node_changed.connect(self._on_canvas_selection)
        self.scene.geometry_changed.connect(self._on_geometry_changed)
        self.scene.undo_stack_changed.connect(self._on_undo_stack_changed)
        self.canvas.stack_peers_changed.connect(self._on_stack_peers_changed)
        self.descr_board.document_dirty.connect(self._on_descr_dirty)
        self.descr_board.undo_stack_changed.connect(self._on_descr_undo_changed)
        self.descr_board.status_message.connect(self.statusBar().showMessage)
        self.descr_board.selection_changed.connect(self._on_descr_selection)
        self.descr_board.geometry_changed.connect(self._on_descr_geometry)
        self.descr_board.sheet_changed.connect(self._on_descr_sheet_changed)
        self.descr_board.regions_changed.connect(self._fill_region_list)
        self._restoring_meta = False
        self.edit_stretch.toggled.connect(self._on_stretch_toggled)
        self.edit_show_texture.toggled.connect(self._on_show_texture_toggled)
        self.edit_font.currentIndexChanged.connect(self._on_text_enum_changed)
        self.edit_font.lineEdit().editingFinished.connect(self._on_text_font_edited)
        self.edit_align.currentIndexChanged.connect(self._on_text_enum_changed)
        self.edit_vert_align.currentIndexChanged.connect(self._on_text_enum_changed)
        self.edit_complex_mode.toggled.connect(self._on_text_flag_toggled)
        self.edit_always_show_scroll.toggled.connect(self._on_scroll_flag_toggled)
        for ed in (
            self.edit_text_r,
            self.edit_text_g,
            self.edit_text_b,
            self.edit_text_a,
            self.edit_text_x,
            self.edit_text_y,
            self.edit_left_ident,
            self.edit_right_ident,
            self.edit_top_indent,
            self.edit_bottom_indent,
            self.edit_vert_interval,
        ):
            ed.editingFinished.connect(self._on_extra_props_editing_finished)
        self._refresh_font_combo()

        self._build_menu()
        self._set_doc_mode(DOC_MODE_UI)
        self.tool_border.toggled.connect(self._on_toggle_border)
        self.tool_fill.toggled.connect(self._on_toggle_fill)
        self.tool_labels.toggled.connect(self._on_toggle_labels)
        self.tool_rulers.toggled.connect(self._on_toggle_rulers)
        self.tool_grid.toggled.connect(self._on_toggle_grid)
        self.tool_grid_step.valueChanged.connect(self._on_grid_step)
        self.tool_aspect.currentIndexChanged.connect(self._on_preview_aspect)
        self.tool_zoom.valueChanged.connect(self._on_zoom_slider)
        self.canvas.view_changed.connect(self._sync_zoom_slider_from_view)
        self.descr_board.view.view_changed.connect(self._sync_zoom_slider_from_view)
        self.tool_font.valueChanged.connect(self._on_tool_font_size)
        self.statusBar().showMessage(
            "1024×768 HUD · wheel zoom · Alt/MMB pan · drag/resize · arrows nudge · "
            "Ctrl+click cycle · Ctrl+D deploy · Ctrl+Z undo"
        )

        # CLI path opens after show; otherwise the startup chooser runs.
        self._startup_path: Path | None = None
        if initial is not None:
            path = Path(initial)
            if path.is_file():
                self._startup_path = path

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def _nudge_focus_ok(self) -> bool:
        """True when arrows should nudge selection (not typing in a field)."""
        fw = QApplication.focusWidget()
        w = fw
        while w is not None:
            if isinstance(
                w,
                (
                    QLineEdit,
                    QTextEdit,
                    QPlainTextEdit,
                    QAbstractSpinBox,
                    QAbstractSlider,
                    QComboBox,
                ),
            ):
                return False
            w = w.parentWidget()
        return True

    def _nudge_view(self):
        if self._doc_mode == DOC_MODE_ATLAS:
            return self.descr_board.view
        return self.canvas

    def _filter_nudge_key(self, event) -> bool:
        """Consume arrow keys to nudge selected boxes when WYSIWYG is active."""
        if not getattr(self, "_workspace_revealed", False):
            return False
        if self.editor_tabs.currentIndex() != TAB_WYSIWYG:
            return False
        key = event.key()
        if key not in (
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
        ):
            return False
        mods = event.modifiers()
        # Bare arrows only (keypad arrows OK).
        if mods & ~(
            Qt.KeyboardModifier.KeypadModifier
            | Qt.KeyboardModifier.GroupSwitchModifier
        ):
            return False
        if not self._nudge_focus_ok():
            return False
        view = self._nudge_view()
        if event.type() == QEvent.Type.KeyPress:
            return view.handle_nudge_key_press(event)
        if event.type() == QEvent.Type.KeyRelease:
            return view.handle_nudge_key_release(event)
        return False

    def _relayout_sidebar_fit(self) -> None:
        """Layers/Properties size to content; Tree/Undo keep leftover stretch."""
        props = getattr(self, "_props_widget", None)
        if props is not None:
            lay = props.layout()
            if lay is not None:
                lay.activate()
            props.updateGeometry()
        for w in (
            getattr(self, "props_scroll", None),
            getattr(self, "layers", None),
        ):
            if w is not None:
                w.updateGeometry()
        left = getattr(self, "_left_body_l", None)
        right = getattr(self, "_right_body_l", None)
        if left is not None:
            left.activate()
        if right is not None:
            right.activate()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout_sidebar_fit()
        if hasattr(self, "_busy") and self._busy.isVisible():
            self._busy.setGeometry(self.rect())
        if hasattr(self, "_startup") and self._startup.isVisible():
            self._startup.setGeometry(self.rect())

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if hasattr(self, "_busy"):
            self._busy.setGeometry(self.rect())
        if hasattr(self, "_startup") and self._startup.isVisible():
            self._startup.setGeometry(self.rect())

    def _show_busy(self, message: str, detail: str = "") -> None:
        self._busy.setGeometry(self.rect())
        self._busy.start(message, detail=detail)
        QApplication.processEvents()

    def _hide_busy(self) -> None:
        self._busy.stop()

    def _paths_need_asset_scan(self) -> bool:
        """True when any configured root is missing a path/DDS cache shard."""
        s = self.settings

        def _paths(raw: object) -> list[Path]:
            return [Path(str(p)) for p in (raw or []) if p]

        return asset_index_scan_needed(
            texture_scan_roots=_paths(s.get("texture_roots")),
            gamedata_texture_roots=_paths(s.get("gamedata_texture_roots")),
            descr_scan_roots=_paths(s.get("textures_descr_roots")),
            gamedata_descr_roots=_paths(s.get("gamedata_descr_roots")),
            text_scan_roots=_paths(s.get("text_roots")),
            gamedata_text_roots=_paths(s.get("gamedata_text_roots")),
            anomaly_root=str(s.get("anomaly_root") or ""),
            gamma_root=str(s.get("gamma_root") or ""),
        )

    def _asset_index_scan_needed(self) -> bool:
        """True when bind will have to (re)build path/DDS cache shards."""
        if getattr(self, "_resources_ready", False):
            return False
        return self._paths_need_asset_scan()

    def _reveal_workspace(self) -> None:
        if self._workspace_revealed:
            return
        self._workspace_revealed = True
        self._startup.stop()
        self.menuBar().show()
        self.statusBar().show()
        self._workspace.show()
        # Hidden QSplitter ignores setSizes — restore after show.
        self._apply_splitter_sizes()
        self._fit_stage()
        QApplication.processEvents()
        self._apply_splitter_sizes()
        QTimer.singleShot(0, self._apply_splitter_sizes)

    def _fit_stage(self) -> None:
        if self._doc_mode == DOC_MODE_ATLAS:
            self.descr_board.fit_stage()
        else:
            self.canvas.fit_stage()
        self._sync_zoom_slider_from_view()

    def _active_canvas_view(self):
        if self._doc_mode == DOC_MODE_ATLAS:
            return self.descr_board.view
        return self.canvas

    def _on_zoom_slider(self, value: int) -> None:
        if self._updating_zoom_slider:
            return
        self.tool_zoom_label.setText(f"{int(value)}%")
        view = self._active_canvas_view()
        view.set_zoom_scale(float(value) / 100.0)

    def _sync_zoom_slider_from_view(self) -> None:
        if not hasattr(self, "tool_zoom"):
            return
        view = self._active_canvas_view()
        scale = view.zoom_scale()
        pct = int(round(scale * 100.0))
        pct = max(100, min(1000, pct))
        self._updating_zoom_slider = True
        self.tool_zoom.setValue(pct)
        self.tool_zoom_label.setText(f"{pct}%")
        self._updating_zoom_slider = False

    def _set_doc_mode(self, mode: str) -> None:
        self._doc_mode = mode
        atlas = mode == DOC_MODE_ATLAS
        self.wysiwyg_stack.setCurrentIndex(1 if atlas else 0)
        self._left_sidebar.setVisible(True)
        self._right_sidebar.setVisible(True)
        for act in (
            self.descr_rename_a,
            self.descr_dup_a,
            self.descr_new_a,
            self.descr_del_a,
        ):
            act.setEnabled(atlas)
            act.setVisible(atlas)
        for act in (self.border_a, self.fill_a, self.labels_a):
            act.setEnabled(True)
        # Atlas: id + x/y/w/h + region list + Options/Undo. Hide UI-only rows/Layers.
        self.prop_label_path.setText("Id:" if atlas else "Path:")
        self._list_section_label.setText("Regions" if atlas else "Tree")
        for w in self._ui_only_prop_widgets:
            w.setVisible(not atlas)
        self._layers_section_label.setVisible(not atlas)
        self.layers.setVisible(not atlas)
        # Layers/Properties fit content; Tree/Undo fill leftover to the bottom.
        self._left_body_l.setStretchFactor(self.layers, 0)
        self._left_body_l.setStretchFactor(self.tree, 1)
        self._right_body_l.setStretchFactor(self.props_scroll, 0)
        self._right_body_l.setStretchFactor(self.undo_list, 1)
        if atlas:
            self._apply_descr_view_settings()
        # Aspect preview is UI-stage only.
        self.tool_aspect.setEnabled(not atlas)
        self.tool_aspect_label.setEnabled(not atlas)
        # Mode switches can collapse panes; restore remembered widths.
        self._apply_splitter_sizes()
        self._update_undo_actions()
        self._refresh_undo_list()
        self._sync_zoom_slider_from_view()
        self._relayout_sidebar_fit()
        QTimer.singleShot(0, self._relayout_sidebar_fit)

    def _apply_descr_view_settings(self) -> None:
        scene = self.descr_board.scene
        scene.set_box_style(
            show_border=bool(self.settings.get("show_box_border", False)),
            show_fill=bool(self.settings.get("show_box_fill", False)),
        )
        scene.set_show_element_labels(
            bool(self.settings.get("show_element_labels", False))
        )
        scene.set_label_font_size(
            clamp_label_font_size(self.settings.get("label_font_size"))
        )

    def _on_descr_dirty(self) -> None:
        self.descr_doc.mark_dirty()
        self._preview_needs_raw_sync = True

    def _on_descr_undo_changed(self) -> None:
        self._update_undo_actions()
        self._refresh_undo_list()

    def _on_descr_sheet_changed(self) -> None:
        self._fill_region_list()
        self._show_descr_props(self.descr_board.scene.selected_region())

    def _fill_region_list(self) -> None:
        if self._doc_mode != DOC_MODE_ATLAS:
            return
        sheet = self.descr_board.current_sheet()
        selected = self.descr_board.scene.selected_region()
        sel_id = selected.atlas_id if selected is not None else None
        self.tree.blockSignals(True)
        self.tree.clear()
        if sheet is not None:
            for reg in sorted(sheet.regions, key=lambda r: r.atlas_id.lower()):
                item = QTreeWidgetItem([reg.atlas_id])
                item.setData(0, Qt.ItemDataRole.UserRole, reg.atlas_id)
                self.tree.addTopLevelItem(item)
                if reg.atlas_id == sel_id:
                    self.tree.setCurrentItem(item)
        self.tree.blockSignals(False)

    def _show_descr_props(self, region) -> None:
        self._updating_props = True
        self._set_meta_prop_labels(False)
        if region is None:
            self._set_path_label("-")
            for ed in (self.edit_x, self.edit_y, self.edit_w, self.edit_h):
                ed.clear()
                ed.setEnabled(False)
            self._set_geo_field_styles(True)
            self._updating_props = False
            return
        self._set_path_label(region.atlas_id)
        for ed in (self.edit_x, self.edit_y, self.edit_w, self.edit_h):
            ed.setEnabled(True)
        # UV crops are pixel ints — never show decimals.
        self.edit_x.setText(str(int(round(region.x))))
        self.edit_y.setText(str(int(round(region.y))))
        self.edit_w.setText(str(int(round(region.width))))
        self.edit_h.setText(str(int(round(region.height))))
        self._set_geo_field_styles(True)
        self._updating_props = False
        # Sync region list selection
        matches = self.tree.findItems(
            region.atlas_id, Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchRecursive
        )
        for it in matches:
            if it.data(0, Qt.ItemDataRole.UserRole) == region.atlas_id:
                self.tree.blockSignals(True)
                self.tree.setCurrentItem(it)
                self.tree.blockSignals(False)
                break

    def _on_descr_geometry(self, region) -> None:
        if self._doc_mode != DOC_MODE_ATLAS:
            return
        self._show_descr_props(region)

    def _on_descr_selection(self, region) -> None:
        if self._doc_mode != DOC_MODE_ATLAS:
            return
        self._commit_props_geo_undo()
        self._show_descr_props(region)
        self._xml_sel_atlas = region.atlas_id if region is not None else ""
        self._apply_xml_selection_highlight()
        if region is None:
            return
        self.statusBar().showMessage(
            f"{region.atlas_id} · {int(region.x)},{int(region.y)} "
            f"{int(region.width)}×{int(region.height)}"
        )

    def _descr_rename(self) -> None:
        if self._doc_mode == DOC_MODE_ATLAS:
            self.descr_board.rename_selected()

    def _descr_duplicate(self) -> None:
        if self._doc_mode == DOC_MODE_ATLAS:
            self.descr_board.duplicate_selected()

    def _descr_add_region(self) -> None:
        if self._doc_mode == DOC_MODE_ATLAS:
            self.descr_board.add_region()

    def _descr_delete(self) -> None:
        if self._doc_mode == DOC_MODE_ATLAS:
            self.descr_board.delete_selected()

    def _active_path(self) -> Path | None:
        if self._doc_mode == DOC_MODE_ATLAS:
            return self.descr_doc.path
        return self.doc.path

    def _open_startup_file(self) -> None:
        scanning = self._asset_index_scan_needed()
        thumbs_dir = cache_dir() / "thumbs"
        thumbs_empty = (not thumbs_dir.is_dir()) or not any(thumbs_dir.glob("*.png"))
        show_busy = scanning or thumbs_empty
        if show_busy:
            detail = (
                "Rescanning root paths for assets"
                if scanning
                else "Generating texture sheet previews…"
            )
            self._show_busy("Loading", detail=detail)
        try:
            self._bind_resource_roots()
        finally:
            if show_busy:
                self._hide_busy()
        path = self._startup_path
        self._startup_path = None
        if path is not None and path.is_file():
            self.open_path(path)
            return
        self._show_startup_chooser()

    def _bind_resource_roots(self, *, warm_thumbs: bool | None = None) -> None:
        """Attach saved directory lists; build missing shards (+ thumbs on scan)."""
        if getattr(self, "_resources_ready", False):
            return
        scanning = self._paths_need_asset_scan()
        if warm_thumbs is None:
            # Cold/partial path scan, or first run after thumbs support landed.
            thumbs_dir = cache_dir() / "thumbs"
            thumbs_empty = (not thumbs_dir.is_dir()) or not any(
                thumbs_dir.glob("*.png")
            )
            warm_thumbs = scanning or thumbs_empty
        self.path_index = PathIndex.load()
        self.path_index.configure_installs(
            self.settings.get("anomaly_root") or "",
            self.settings.get("gamma_root") or "",
        )
        self.resolver = self._make_resolver()
        self.strings = self._make_string_resolver()
        self.fonts = self._make_font_resolver()
        # Load or build file indexes once so document open is O(1) path lookups.
        t0 = time.perf_counter()
        tex_stats = self.resolver.ensure_indexes()
        str_stats = self.strings.ensure_indexes()
        self.path_index.save()
        index_s = time.perf_counter() - t0
        thumb_stats: dict[str, int] = {}
        thumb_s = 0.0
        if warm_thumbs:
            if self._busy.isVisible():
                self._busy.set_message(
                    self._busy.message,
                    detail="Generating texture sheet previews…",
                )
                QApplication.processEvents()

            def _thumb_progress(done: int, total: int, written: int) -> None:
                if getattr(self, "_closing", False):
                    self.resolver.abort_picker_thumbs()
                    return
                if not self._busy.isVisible():
                    return
                self._busy.set_message(
                    self._busy.message,
                    detail=f"Generating texture sheet previews… {done}/{total}",
                )
                # Allow Quit during warm — closeEvent aborts the thumb pool.
                QApplication.processEvents()
                if getattr(self, "_closing", False):
                    self.resolver.abort_picker_thumbs()

            t1 = time.perf_counter()
            thumb_stats = self.resolver.warm_picker_thumbs(progress=_thumb_progress)
            thumb_s = time.perf_counter() - t1
            if getattr(self, "_closing", False):
                return
        self.scene.rebind_resolver(self.resolver)
        self.scene.rebind_text_resources(strings=self.strings, fonts=self.fonts)
        self.descr_board.resolver = self.resolver
        self.descr_board.scene.resolver = self.resolver
        self._resources_ready = True
        n_tex = len(self.settings.get("gamedata_texture_roots") or [])
        n_descr = len(self.settings.get("gamedata_descr_roots") or [])
        n_text = len(self.settings.get("gamedata_text_roots") or [])
        _log_file.info(
            "resource roots bound texture=%s descr=%s text=%s | "
            "index dds=%s descr_xml=%s text_xml=%s in %.2fs | "
            "thumbs written=%s skipped=%s sheets=%s workers=%s in %.2fs",
            n_tex,
            n_descr,
            n_text,
            tex_stats.get("dds", 0),
            tex_stats.get("descr_files", 0),
            str_stats.get("text_files", 0),
            index_s,
            thumb_stats.get("written", 0),
            thumb_stats.get("skipped", 0),
            thumb_stats.get("sheets", 0),
            thumb_stats.get("workers", 0),
            thumb_s,
        )
        msg = (
            f"Ready · {tex_stats.get('dds', 0)} dds · "
            f"{tex_stats.get('descr_files', 0)} descr · "
            f"{str_stats.get('text_files', 0)} text · indexed in {index_s:.1f}s"
        )
        if thumb_stats:
            msg += (
                f" · {thumb_stats.get('written', 0)} sheet previews in {thumb_s:.1f}s"
            )
        self.statusBar().showMessage(msg)

    def _ensure_resources_indexed(self) -> None:
        """Back-compat alias — roots only; assets resolve on file load."""
        self._bind_resource_roots()

    def _warm_resources_for_doc(self, doc: LayoutNode) -> None:
        """Resolve only atlas / DDS / string / font ids this document references."""
        self._bind_resource_roots()
        t0 = time.perf_counter()
        # Keep DDS map, decoded sheets, strings, and fonts across opens.
        self.resolver.clear_document_cache()
        self.resolver.ensure_indexes()
        self.strings.ensure_indexes()
        self.fonts.ensure_font_index()
        self.resolver.warm_for_document(doc)
        self.strings.warm_for_document(doc)
        self.fonts.warm_for_document(doc)
        if self.path_index is not None:
            self.path_index.save()
        self._refresh_font_combo()
        elapsed = time.perf_counter() - t0
        font_h = getattr(self.fonts, "device_height", 0)
        _log_file.info(
            "doc resources warmed in %.2fs atlas=%s dds=%s strings=%s fonts=%s device_h=%s",
            elapsed,
            self.resolver.atlas_count,
            self.resolver.dds_count,
            self.strings.count,
            self.fonts.count,
            font_h,
        )

    def _show_startup_chooser(self) -> None:
        """Dark window only: centered Open / recent box until a file loads."""
        recent = list(self.settings.get("recent_files") or [])[:5]
        # Startup browse uses same default as File→Open (custom root).
        self._startup.configure(recent, start_dir=self._file_dialog_start())
        self._startup.start()

    def _on_startup_path_chosen(self, path: object) -> None:
        if not isinstance(path, Path):
            path = Path(str(path))
        self._startup.stop()
        self.open_path(path)

    def _on_startup_dismissed(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _make_resolver(self) -> TextureResolver:
        s = self.settings
        return TextureResolver(
            texture_scan_roots=_paths(s.get("texture_roots", [])),
            gamedata_texture_roots=_paths(s.get("gamedata_texture_roots", [])),
            descr_scan_roots=_paths(s.get("textures_descr_roots", [])),
            gamedata_descr_roots=_paths(s.get("gamedata_descr_roots", [])),
            path_index=self.path_index,
        )

    def _make_string_resolver(self) -> StringResolver:
        s = self.settings
        return StringResolver(
            text_scan_roots=_paths(s.get("text_roots", [])),
            gamedata_text_roots=_paths(s.get("gamedata_text_roots", [])),
            path_index=self.path_index,
        )

    def _make_font_resolver(self) -> FontResolver:
        # Match engine Device.dwHeight (atlas bucket + 768/h HUD scale).
        return FontResolver(
            self.resolver,
            path_index=self.path_index,
            device_height=resolve_font_device_height(self.settings),
        )

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        self.open_a = QAction("&Open…", self)
        self.open_a.setShortcut(QKeySequence.StandardKey.Open)
        self.open_a.triggered.connect(self.open_dialog)
        file_menu.addAction(self.open_a)
        self.recents_menu = file_menu.addMenu("Open &Recent")
        self.recents_menu.aboutToShow.connect(self._rebuild_recents_menu)
        self.save_a = QAction("&Save", self)
        self.save_a.setShortcut(QKeySequence.StandardKey.Save)
        self.save_a.triggered.connect(self.save)
        file_menu.addAction(self.save_a)
        self.save_as_a = QAction("Save &As…", self)
        self.save_as_a.setShortcut(QKeySequence.StandardKey.SaveAs)
        self.save_as_a.triggered.connect(self.save_as)
        file_menu.addAction(self.save_as_a)
        self.deploy_a = QAction("&Deploy", self)
        self.deploy_a.setShortcut("Ctrl+D")
        self.deploy_a.setToolTip(
            "Overwrite the remembered deploy target with this document’s content "
            "(keeps the target file name). Prompts if no target is set."
        )
        self.deploy_a.triggered.connect(self.deploy)
        file_menu.addAction(self.deploy_a)
        self.deploy_as_a = QAction("Deploy As…", self)
        self.deploy_as_a.setShortcut("Ctrl+Shift+D")
        self.deploy_as_a.setToolTip(
            "Choose a file to overwrite on Deploy. Source path stays unchanged."
        )
        self.deploy_as_a.triggered.connect(self.deploy_as)
        file_menu.addAction(self.deploy_as_a)
        file_menu.addSeparator()
        quit_a = QAction("&Quit", self)
        quit_a.setShortcut(QKeySequence.StandardKey.Quit)
        quit_a.triggered.connect(self.close)
        file_menu.addAction(quit_a)
        self._rebuild_recents_menu()

        view_menu = self.menuBar().addMenu("&View")
        self.fit_a = QAction("&Fit stage", self)
        self.fit_a.setShortcut("F")
        self.fit_a.triggered.connect(self._fit_stage)
        view_menu.addAction(self.fit_a)
        view_menu.addSeparator()
        self.border_a = QAction("Show &borders", self)
        self.border_a.setCheckable(True)
        self.border_a.setChecked(bool(self.settings.get("show_box_border", False)))
        self.border_a.toggled.connect(self._on_toggle_border)
        view_menu.addAction(self.border_a)
        self.fill_a = QAction("&Fill selected", self)
        self.fill_a.setCheckable(True)
        self.fill_a.setChecked(bool(self.settings.get("show_box_fill", False)))
        self.fill_a.toggled.connect(self._on_toggle_fill)
        view_menu.addAction(self.fill_a)
        self.labels_a = QAction("&Show labels", self)
        self.labels_a.setCheckable(True)
        self.labels_a.setChecked(bool(self.settings.get("show_element_labels", False)))
        self.labels_a.toggled.connect(self._on_toggle_labels)
        view_menu.addAction(self.labels_a)
        self.rulers_a = QAction("Show &guide lines", self)
        self.rulers_a.setCheckable(True)
        self.rulers_a.setChecked(bool(self.settings.get("show_rulers", True)))
        self.rulers_a.toggled.connect(self._on_toggle_rulers)
        view_menu.addAction(self.rulers_a)
        self.grid_a = QAction("&Pixel grid", self)
        self.grid_a.setCheckable(True)
        self.grid_a.setChecked(bool(self.settings.get("show_grid", False)))
        self.grid_a.toggled.connect(self._on_toggle_grid)
        view_menu.addAction(self.grid_a)
        view_menu.addSeparator()
        self.reload_tex_a = QAction("Reload &textures / strings", self)
        self.reload_tex_a.triggered.connect(self._reload_textures)
        view_menu.addAction(self.reload_tex_a)

        edit_menu = self.menuBar().addMenu("&Edit")
        self.undo_a = QAction("&Undo", self)
        self.undo_a.setShortcut(QKeySequence.StandardKey.Undo)
        self.undo_a.triggered.connect(self.undo)
        self.undo_a.setEnabled(False)
        edit_menu.addAction(self.undo_a)
        self.redo_a = QAction("&Redo", self)
        self.redo_a.setShortcuts(
            [
                QKeySequence.StandardKey.Redo,
                QKeySequence("Ctrl+Shift+Z"),
            ]
        )
        self.redo_a.triggered.connect(self.redo)
        self.redo_a.setEnabled(False)
        edit_menu.addAction(self.redo_a)
        edit_menu.addSeparator()
        self.descr_rename_a = QAction("Rename atlas &id…", self)
        self.descr_rename_a.setShortcut("F2")
        self.descr_rename_a.triggered.connect(self._descr_rename)
        edit_menu.addAction(self.descr_rename_a)
        self.descr_dup_a = QAction("Du&plicate region", self)
        self.descr_dup_a.setShortcut("Ctrl+Alt+D")
        self.descr_dup_a.triggered.connect(self._descr_duplicate)
        edit_menu.addAction(self.descr_dup_a)
        self.descr_new_a = QAction("&New region", self)
        self.descr_new_a.setShortcut("Ctrl+N")
        self.descr_new_a.triggered.connect(self._descr_add_region)
        edit_menu.addAction(self.descr_new_a)
        self.descr_del_a = QAction("&Delete region", self)
        self.descr_del_a.setShortcut(QKeySequence.StandardKey.Delete)
        self.descr_del_a.triggered.connect(self._descr_delete)
        edit_menu.addAction(self.descr_del_a)
        edit_menu.addSeparator()
        self.find_a = QAction("&Find…", self)
        self.find_a.setShortcut(QKeySequence.StandardKey.Find)
        self.find_a.triggered.connect(self._show_find_bar)
        edit_menu.addAction(self.find_a)
        self.find_next_a = QAction("Find &Next", self)
        self.find_next_a.setShortcut(QKeySequence.StandardKey.FindNext)
        self.find_next_a.triggered.connect(self._find_next)
        edit_menu.addAction(self.find_next_a)
        self.find_prev_a = QAction("Find Pre&vious", self)
        self.find_prev_a.setShortcut(QKeySequence.StandardKey.FindPrevious)
        self.find_prev_a.triggered.connect(self._find_prev)
        edit_menu.addAction(self.find_prev_a)
        edit_menu.addSeparator()
        self.settings_a = QAction("&Settings…", self)
        self.settings_a.triggered.connect(self.edit_settings)
        edit_menu.addAction(self.settings_a)
        self.rescan_a = QAction("Rescan &asset cache…", self)
        self.rescan_a.setShortcut("Ctrl+Shift+R")
        self.rescan_a.setToolTip(
            "Choose which roots to rescan. Drops only those cache shards and "
            "regenerates them (default: custom roots)."
        )
        self.rescan_a.triggered.connect(self.rescan_asset_paths)
        edit_menu.addAction(self.rescan_a)

        help_menu = self.menuBar().addMenu("&Help")
        about_a = QAction("&About SAGE…", self)
        about_a.triggered.connect(self._show_about)
        help_menu.addAction(about_a)

    def _show_about(self) -> None:
        from sage import __version__

        QMessageBox.about(
            self,
            "About SAGE",
            "<h3>D.O.G.M.A. Stalker Anomaly Gui Editor</h3>"
            f"<p>SAGE {__version__} — visual layout editor for Anomaly UI XML "
            "(1024×768 HUD space).</p>"
            "<p><b>Controls</b></p>"
            "<ul>"
            "<li><b>Wheel</b> / <b>Ctrl+Wheel</b> — zoom (100%–1000%, or options slider)</li>"
            "<li><b>Shift+Wheel</b> — pan horizontally</li>"
            "<li><b>Alt+Wheel</b> — pan vertically</li>"
            "<li><b>Middle-drag</b> or <b>Alt+Left-drag</b> — pan</li>"
            "<li><b>F</b> — fit stage</li>"
            "<li><b>Arrow keys</b> — nudge 1px (hold to repeat)</li>"
            "<li><b>Click</b> — select smallest widget under cursor</li>"
            "<li><b>Ctrl+Click</b> — cycle stacked widgets (toward smaller; "
            "wraps to largest)</li>"
            "<li><b>Drag empty / unselected</b> — marquee (fully enclosed)</li>"
            "<li><b>Drag selected</b> — move (multi-move when several selected)</li>"
            "<li><b>Edge / corner</b> — resize (single selection)</li>"
            "<li><b>Right-click</b> — clear selection</li>"
            "<li><b>Guide rulers</b> — drag guide; right-click near guide to remove "
            "(toggle: Show guide lines)</li>"
            "<li><b>Cursor coords</b> — scene x,y in the top-left ruler corner</li>"
            "<li><b>Aspect</b> — options-bar preview stretch only (not saved; "
            "text stays unstretched)</li>"
            "<li><b>Pixel grid</b> — options-bar toggle + step (every N px)</li>"
            "<li><b>Ctrl+D</b> / <b>Ctrl+Shift+D</b> — Deploy / Deploy As</li>"
            "<li><b>Ctrl+Z / Ctrl+Y</b> (or <b>Ctrl+Shift+Z</b>) — undo / redo</li>"
            "</ul>",
        )

    def _rebuild_recents_menu(self) -> None:
        self.recents_menu.clear()
        recent = list(self.settings.get("recent_files") or [])
        if not recent:
            empty = QAction("(no recent files)", self)
            empty.setEnabled(False)
            self.recents_menu.addAction(empty)
            return
        for i, path_str in enumerate(recent):
            path = Path(path_str)
            label = path.name
            if i < 9:
                label = f"&{i + 1}  {label}"
            elif i == 9:
                label = f"1&0  {label}"
            act = QAction(label, self)
            act.setToolTip(path_str)
            act.setData(path_str)
            act.setEnabled(path.is_file())
            act.triggered.connect(self._open_recent_action)
            self.recents_menu.addAction(act)
        self.recents_menu.addSeparator()
        clear_a = QAction("&Clear Recent", self)
        clear_a.triggered.connect(self._clear_recents)
        self.recents_menu.addAction(clear_a)

    def _open_recent_action(self) -> None:
        act = self.sender()
        if not isinstance(act, QAction):
            return
        path_str = act.data()
        if not path_str:
            return
        path = Path(str(path_str))
        if not path.is_file():
            QMessageBox.warning(self, "Missing file", f"File not found:\n{path}")
            self._rebuild_recents_menu()
            return
        self.open_path(path)

    def _clear_recents(self) -> None:
        self.settings["recent_files"] = []
        save_settings(self.settings)
        self._rebuild_recents_menu()

    def _remember_recent_file(self, path: Path) -> None:
        push_recent_file(self.settings, path)
        save_settings(self.settings)
        self._rebuild_recents_menu()

    def _first_custom_root(self) -> Path | None:
        for raw in normalize_custom_roots(self.settings.get("custom_roots")):
            p = Path(raw).expanduser()
            if p.is_dir():
                return p
        return None

    def _file_dialog_start(self, preferred: Path | None = None) -> str:
        if preferred is not None:
            p = preferred if preferred.is_dir() else preferred.parent
            if p.is_dir():
                return str(p)
        # Default: first Settings custom root (project / stock packs), not Anomaly/cwd.
        custom = self._first_custom_root()
        if custom is not None:
            return str(custom)
        saved = Path(str(self.settings.get("last_file_dir") or ""))
        if saved.is_dir():
            return str(saved)
        return str(Path.cwd())

    def _atlas_dialog_start(self) -> str:
        custom = self._first_custom_root()
        if custom is not None:
            for rel in (
                Path("configs") / "ui" / "textures_descr",
                Path("gamedata") / "configs" / "ui" / "textures_descr",
                Path("textures_descr"),
            ):
                d = custom / rel
                if d.is_dir():
                    return str(d)
            return str(custom)
        saved = Path(str(self.settings.get("last_texture_dir") or ""))
        if saved.is_dir():
            return str(saved)
        for key in ("gamedata_descr_roots", "textures_descr_roots"):
            for raw in self.settings.get(key) or []:
                p = Path(str(raw))
                if p.is_dir():
                    return str(p)
        return self._file_dialog_start()

    def _remember_file_dir(self, path: Path) -> None:
        directory = path if path.is_dir() else path.parent
        if not directory.is_dir():
            return
        key = str(directory.resolve())
        if self.settings.get("last_file_dir") == key:
            return
        self.settings["last_file_dir"] = key
        save_settings(self.settings)

    def _remember_texture_dir(self, path: Path) -> None:
        directory = path if path.is_dir() else path.parent
        if not directory.is_dir():
            return
        try:
            key = str(directory.resolve())
        except OSError:
            key = str(directory)
        if self.settings.get("last_texture_dir") == key:
            return
        self.settings["last_texture_dir"] = key
        save_settings(self.settings)

    def _browse_texture(self) -> None:
        if self._updating_props:
            return
        selected = [i for i in self.scene.selectedItems() if hasattr(i, "node")]
        if not selected:
            return
        node: LayoutNode = selected[0].node
        if node.from_meta or not node.is_drawable:
            return
        current = ""
        prefer_path = False
        if node.texture and node.texture.name:
            current = node.texture.name
            prefer_path = node.texture.is_path
        dlg = TexturePickerDialog(
            self.resolver,
            self,
            current_name=current,
            prefer_path=prefer_path,
            root_groups=build_picker_root_groups(
                self.resolver,
                anomaly_root=str(self.settings.get("anomaly_root") or ""),
                gamma_root=str(self.settings.get("gamma_root") or ""),
            ),
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        pick = dlg.selected_pick()
        if pick is None:
            return
        before = node.capture_prop_state()
        # Atlas: write id, clear XML UV (engine uses textures_descr).
        # Path: write ui\file, clear UV (full DDS). Path+UV crops are edited in XML.
        if not node.set_texture_name(pick.name, clear_uv=True):
            return
        if pick.dds_path is not None:
            # Warm DDS index; atlas UV already ingested while the picker scanned descr.
            if pick.kind == "path":
                self.resolver.remember_dds(pick.name, pick.dds_path)
                self._remember_texture_dir(pick.dds_path)
            else:
                atlas = self.resolver.lookup_atlas(pick.name)
                if atlas is not None:
                    self.resolver.remember_dds(atlas.file_name, pick.dds_path)
        if not self._commit_prop_change(node, before):
            return
        self.statusBar().showMessage(f"Texture → {pick.name}")
        self._log("info", f"texture {node.path}: {pick.name} ({pick.kind})")

    def _clear_texture(self) -> None:
        if self._updating_props:
            return
        selected = [i for i in self.scene.selectedItems() if hasattr(i, "node")]
        if not selected:
            return
        node: LayoutNode = selected[0].node
        if node.from_meta or not node.is_drawable or node.texture is None:
            return
        before = node.capture_prop_state()
        if not node.set_texture_name(""):
            return
        if not self._commit_prop_change(node, before):
            return
        self.statusBar().showMessage("Texture cleared")
        self._log("info", f"texture {node.path}: cleared")

    def open_dialog(self) -> None:
        # Default start: first Settings custom root.
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Open XML",
            self._file_dialog_start(),
            "UI XML (*.xml);;Texture atlas XML (*.xml);;All (*.*)",
        )
        if path:
            self.open_path(Path(path))

    def open_path(self, path: Path) -> None:
        if self._busy_open:
            _log_file.warning("open_path ignored (busy): %s", path)
            return
        _log_file.info("open_path begin: %s", path)
        self._busy_open = True
        scanning = self._asset_index_scan_needed()
        thumbs_dir = cache_dir() / "thumbs"
        thumbs_empty = (
            not getattr(self, "_resources_ready", False)
            and ((not thumbs_dir.is_dir()) or not any(thumbs_dir.glob("*.png")))
        )
        detail_parts = [path.name]
        if scanning:
            detail_parts.append("Rescanning root paths for assets")
        elif thumbs_empty:
            detail_parts.append("Generating texture sheet previews…")
        self._show_busy("Loading", detail="\n".join(detail_parts))
        # Let the overlay paint / spin once before the blocking load work.
        QTimer.singleShot(0, lambda p=path: self._open_path_finish(p))

    def _open_path_finish(self, path: Path) -> None:
        ok = False
        _log_file.debug("_open_path_finish: %s", path)
        try:
            self._bind_resource_roots()
            try:
                text = path.read_text(encoding="utf-8-sig")
            except OSError as exc:
                self._log("error", f"Open failed: {path} - {exc}")
                QMessageBox.critical(self, "Open failed", str(exc))
                return
            is_atlas = looks_like_textures_descr(path=path, text=text)
            if is_atlas:
                ok = self._open_atlas_finish(path, text)
            else:
                ok = self._open_ui_finish(path, text)
        finally:
            self._hide_busy()
            self._busy_open = False
            _log_file.debug("open_path finish ok=%s busy cleared", ok)
            if not ok and not self._workspace_revealed:
                QTimer.singleShot(0, self._show_startup_chooser)

    def _open_ui_finish(self, path: Path, text: str) -> bool:
        try:
            doc = self.doc.load_text(text, path=path)
        except Exception as exc:  # noqa: BLE001
            _log_file.exception("Open failed: %s", path)
            self._log("error", f"Open failed: {path} - {exc}")
            QMessageBox.critical(self, "Open failed", str(exc))
            return False
        self.descr_doc.clear()
        self._set_doc_mode(DOC_MODE_UI)
        self._remember_file_dir(path)
        self._remember_recent_file(path)
        self._set_raw_text(self.doc.source_text)
        self._raw_dirty = False
        self._preview_needs_raw_sync = False
        self._apply_preview_doc(doc)
        self._reveal_workspace()
        self._restore_view_or_fit()
        self.setWindowTitle(f"D.O.G.M.A. Stalker Anomaly Gui Editor - {path.name}")
        widgets = len(doc.iter_drawables())
        meta_n = sum(1 for n in doc.iter_drawables() if n.from_meta)
        self._log(
            "info",
            f"Opened {path} | {widgets} widgets | {meta_n} meta | "
            f"atlas {self.resolver.atlas_count} | dds {self.resolver.dds_count} | "
            f"strings {self.strings.count}",
        )
        self._audit_resources("open")
        missing = self.scene.missing_texture_count()
        textured = self.scene.textured_count()
        self.statusBar().showMessage(
            f"Loaded {path.name} · {widgets} widgets · "
            f"{textured} textured · {missing} missing tex · "
            f"{meta_n} meta · "
            f"{self.resolver.atlas_count} atlas · {self.resolver.dds_count} dds · "
            f"{self.strings.count} strings"
        )
        _log_file.info(
            "open complete: %s widgets=%s meta=%s textured=%s missing_tex=%s",
            path,
            widgets,
            meta_n,
            textured,
            missing,
        )
        return True

    def _open_atlas_finish(self, path: Path, text: str) -> bool:
        try:
            sheets = self.descr_doc.load_text(text, path=path)
        except Exception as exc:  # noqa: BLE001
            _log_file.exception("Open atlas failed: %s", path)
            self._log("error", f"Open failed: {path} - {exc}")
            QMessageBox.critical(self, "Open failed", str(exc))
            return False
        self._bind_resource_roots()
        self.resolver.clear_cache()
        self._set_doc_mode(DOC_MODE_ATLAS)
        self._remember_file_dir(path)
        self._remember_recent_file(path)
        if "textures_descr" in [p.lower() for p in path.parts]:
            self._remember_texture_dir(path.parent)
        self._set_raw_text(self.descr_doc.source_text)
        self._raw_dirty = False
        self._preview_needs_raw_sync = False
        self.descr_board.set_document(self.descr_doc)
        self._apply_descr_view_settings()
        self._fill_region_list()
        self._show_descr_props(None)
        self._reveal_workspace()
        self.descr_board.fit_stage()
        self.setWindowTitle(
            f"D.O.G.M.A. Stalker Anomaly Gui Editor - {path.name} [atlas]"
        )
        n_reg = sum(len(s.regions) for s in sheets)
        self._log(
            "info",
            f"Opened atlas {path} | {len(sheets)} sheet(s) | {n_reg} regions",
        )
        sheet0 = sheets[0].file_name if sheets else "?"
        self.statusBar().showMessage(
            f"Atlas {path.name} · {len(sheets)} sheet(s) · {n_reg} regions · {sheet0}"
        )
        return True

    def _apply_preview_doc(self, doc: LayoutNode) -> None:
        self._warm_resources_for_doc(doc)
        self._restoring_meta = True
        try:
            self.scene.set_document(doc)
            self._fill_tree(doc)
            self._fill_layers(doc)
            undo, redo = self.doc.undo_snapshot()
            self.scene.undo_stack.restore(undo=undo, redo=redo)
            self._update_undo_actions()
            self._refresh_undo_list()
            if self.doc.selection:
                self.scene.select_path(self.doc.selection)
            else:
                self._show_props(None)
        finally:
            self._restoring_meta = False

    def _capture_session_meta(self) -> None:
        """Pull undo / selection / view into the document for .xml.meta."""
        snap = self.scene.undo_stack.serialize()
        selection = ""
        for item in self.scene.selectedItems():
            node = getattr(item, "node", None)
            if node is not None and getattr(node, "path", ""):
                selection = node.path
                break
        self.doc.capture_session(
            undo=snap["undo"],
            redo=snap["redo"],
            selection=selection,
            view=self.canvas.view_state(),
        )

    def _restore_view_or_fit(self) -> None:
        view = self.doc.view_state
        if view:
            self.canvas.restore_view_state(view)
        else:
            self.canvas.fit_stage()

    def _on_undo_stack_changed(self) -> None:
        self._update_undo_actions()
        self._refresh_undo_list()
        if self._restoring_meta:
            return
        # Keep session snapshot current; geo edits already mark xml/meta dirty.
        self._capture_session_meta()

    def _refresh_undo_list(self) -> None:
        """Redo (top) then Undo, numbered per side; dashed rule between when both exist."""
        view = getattr(self, "undo_list", None)
        if view is None:
            return
        stack = (
            self.descr_board.scene.undo_stack
            if self._doc_mode == DOC_MODE_ATLAS
            else self.scene.undo_stack
        )
        redos = stack.recent_redo()
        undos = stack.recent_undo()
        view.blockSignals(True)
        view.clear()
        for i, edit in enumerate(redos, start=1):
            view.addItem(self._undo_history_item(i, edit, side="redo"))
        if redos and undos:
            sep = QListWidgetItem()
            sep.setFlags(Qt.ItemFlag.NoItemFlags)
            sep.setData(Qt.ItemDataRole.UserRole, "separator")
            sep.setSizeHint(QSize(1, 14))
            view.addItem(sep)
            view.setItemWidget(sep, _UndoHistorySeparator(view))
        for i, edit in enumerate(undos, start=1):
            view.addItem(self._undo_history_item(i, edit, side="undo"))
        view.blockSignals(False)

    def _undo_history_item(
        self, index: int, edit, *, side: str
    ) -> QListWidgetItem:
        label = edit.describe()
        item = QListWidgetItem(f"{index}. {label}")
        item.setForeground(QBrush(_UNDO_TONE_COLORS.get(edit.action_tone(), _DARK_TEXT)))
        item.setData(Qt.ItemDataRole.UserRole, side)
        if isinstance(edit, PropEdit):
            item.setToolTip(f"{side}: {edit.before.path}\n{label}")
        else:
            tip_parts = [f"{side}:"]
            for before, after in edit.parts[:6]:
                tip_parts.append(
                    f"{before.path}: "
                    f"({before.x:g},{before.y:g} {before.width:g}×{before.height:g}) → "
                    f"({after.x:g},{after.y:g} {after.width:g}×{after.height:g})"
                )
            if len(edit.parts) > 6:
                tip_parts.append(f"… +{len(edit.parts) - 6} more")
            item.setToolTip("\n".join(tip_parts))
        return item

    def _has_unsaved_changes(self) -> bool:
        if self._doc_mode == DOC_MODE_ATLAS:
            return bool(self.descr_doc.dirty or self._raw_dirty)
        return bool(self.doc.dirty or self.doc.meta_dirty or self._raw_dirty)

    def _set_raw_text(self, text: str) -> None:
        self.raw_editor.blockSignals(True)
        self.raw_editor.setPlainText(text)
        self.raw_editor.blockSignals(False)
        if not self.find_bar.isHidden():
            self._refresh_find_matches(keep_index=True)

    def _mark_xml_dirty(self) -> None:
        self.doc.mark_dirty()
        self._preview_needs_raw_sync = True

    def _on_raw_text_changed(self) -> None:
        self._raw_dirty = True
        self.doc.mark_dirty()
        if not self.find_bar.isHidden():
            self._refresh_find_matches(keep_index=True)

    def _show_find_bar(self) -> None:
        self.editor_tabs.setCurrentIndex(TAB_XML)
        selected = self.raw_editor.textCursor().selectedText().replace("\u2029", "\n")
        if selected and "\n" not in selected:
            self.find_edit.setText(selected)
        self.find_bar.setVisible(True)
        self.find_edit.setFocus()
        self.find_edit.selectAll()
        self._refresh_find_matches(keep_index=False)

    def _hide_find_bar(self) -> None:
        self.find_bar.setVisible(False)
        self._find_matches = []
        self._find_index = -1
        self.find_count.setText("")
        self.raw_editor.setExtraSelections([])
        self._apply_xml_selection_highlight(scroll=False)
        self.raw_editor.setFocus()

    def _on_find_text_changed(self, _text: str) -> None:
        self._refresh_find_matches(keep_index=False)

    def _refresh_find_matches(self, *, keep_index: bool) -> None:
        needle = self.find_edit.text()
        haystack = self.raw_editor.toPlainText()
        matches: list[int] = []
        if needle:
            start = 0
            while True:
                pos = haystack.find(needle, start)
                if pos < 0:
                    break
                matches.append(pos)
                start = pos + max(1, len(needle))
        prev_pos = (
            self._find_matches[self._find_index]
            if keep_index and 0 <= self._find_index < len(self._find_matches)
            else None
        )
        self._find_matches = matches
        if not matches:
            self._find_index = -1
        elif prev_pos is not None and prev_pos in matches:
            self._find_index = matches.index(prev_pos)
        else:
            # Prefer first match at/after cursor
            cursor_pos = self.raw_editor.textCursor().position()
            self._find_index = 0
            for i, pos in enumerate(matches):
                if pos >= cursor_pos:
                    self._find_index = i
                    break
        if self._find_index >= 0:
            self._jump_to_find_index(self._find_index)
        else:
            self._apply_find_highlights()
            self._update_find_chrome()

    def _update_find_chrome(self) -> None:
        n = len(self._find_matches)
        has = n > 0
        self.find_prev_btn.setEnabled(has)
        self.find_next_btn.setEnabled(has)
        if not self.find_edit.text():
            self.find_count.setText("")
        elif not has:
            self.find_count.setText("No results")
        else:
            self.find_count.setText(f"{self._find_index + 1}/{n}")

    def _apply_find_highlights(self) -> None:
        needle = self.find_edit.text()
        extras: list[QTextEdit.ExtraSelection] = []
        if not needle or not self._find_matches:
            self.raw_editor.setExtraSelections(extras)
            return
        match_fmt = QTextCharFormat()
        match_fmt.setBackground(QColor("#613214"))
        current_fmt = QTextCharFormat()
        current_fmt.setBackground(QColor("#9E6A03"))
        current_fmt.setForeground(QColor("#FFFFFF"))
        for i, pos in enumerate(self._find_matches):
            sel = QTextEdit.ExtraSelection()
            cursor = self.raw_editor.textCursor()
            cursor.setPosition(pos)
            cursor.setPosition(pos + len(needle), QTextCursor.MoveMode.KeepAnchor)
            sel.cursor = cursor
            sel.format = current_fmt if i == self._find_index else match_fmt
            extras.append(sel)
        self.raw_editor.setExtraSelections(extras)

    def _apply_xml_selection_highlight(self, *, scroll: bool = True) -> None:
        """Highlight the selected tree/canvas node in the XML editor (no tab switch)."""
        if not self.find_bar.isHidden():
            return
        text = self.raw_editor.toPlainText()
        span: tuple[int, int] | None = None
        if self._doc_mode == DOC_MODE_ATLAS:
            key = self._xml_sel_atlas
            if key:
                span = find_atlas_id_span(text, key)
        else:
            key = self._xml_sel_path
            if key:
                span = find_layout_path_span(text, key)
        extras: list[QTextEdit.ExtraSelection] = []
        if span is not None:
            sel = QTextEdit.ExtraSelection()
            cursor = self.raw_editor.textCursor()
            cursor.setPosition(span[0])
            cursor.setPosition(span[1], QTextCursor.MoveMode.KeepAnchor)
            sel.cursor = cursor
            fmt = QTextCharFormat()
            fmt.setBackground(QColor("#264F78"))
            sel.format = fmt
            extras.append(sel)
            if scroll:
                # Move caret without a real selection (ExtraSelection paints the range).
                nav = self.raw_editor.textCursor()
                nav.clearSelection()
                nav.setPosition(span[0])
                self.raw_editor.setTextCursor(nav)
                self.raw_editor.centerCursor()
        self.raw_editor.setExtraSelections(extras)

    def _jump_to_find_index(self, index: int) -> None:
        if not self._find_matches:
            self._update_find_chrome()
            return
        self._find_index = index % len(self._find_matches)
        pos = self._find_matches[self._find_index]
        needle = self.find_edit.text()
        # Don't create a real text selection - it paints over ExtraSelections
        # with the editor selection color and drops the bright current-match style.
        cursor = self.raw_editor.textCursor()
        cursor.clearSelection()
        cursor.setPosition(pos + len(needle))
        self.raw_editor.setTextCursor(cursor)
        self.raw_editor.centerCursor()
        self._apply_find_highlights()
        self._update_find_chrome()

    def _find_next(self) -> None:
        if self.find_bar.isHidden():
            self._show_find_bar()
            return
        if not self._find_matches:
            self._refresh_find_matches(keep_index=False)
            if not self._find_matches:
                return
            self._jump_to_find_index(0)
            return
        self._jump_to_find_index(self._find_index + 1)

    def _find_prev(self) -> None:
        if self.find_bar.isHidden():
            self._show_find_bar()
            return
        if not self._find_matches:
            self._refresh_find_matches(keep_index=False)
            if not self._find_matches:
                return
            self._jump_to_find_index(len(self._find_matches) - 1)
            return
        self._jump_to_find_index(self._find_index - 1)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape and not self.find_bar.isHidden():
            self._hide_find_bar()
            event.accept()
            return
        super().keyPressEvent(event)

    def _on_editor_tab_changed(self, index: int) -> None:
        if self._tab_guard:
            return
        if index == TAB_XML:
            self._sync_raw_from_preview()
            self._update_undo_actions()
            return
        if index == TAB_WYSIWYG:
            if not self._apply_raw_to_preview():
                self._tab_guard = True
                self.editor_tabs.setCurrentIndex(TAB_XML)
                self._tab_guard = False
            else:
                self._audit_resources("xml → wysiwyg")
            self._update_undo_actions()
            return
        # Log: leave XML/WYSIWYG as-is
        self._update_undo_actions()

    def _sync_raw_from_preview(self) -> None:
        """Push WYSIWYG geometry into the XML editor when preview changed."""
        if self._doc_mode == DOC_MODE_ATLAS:
            if self.descr_doc.root is None:
                return
            if self._raw_dirty:
                return
            if not (self.descr_doc.dirty or self._preview_needs_raw_sync):
                if not self.raw_editor.toPlainText() and self.descr_doc.source_text:
                    self._set_raw_text(self.descr_doc.source_text)
                return
            try:
                text = self.descr_doc.serialize()
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "XML sync failed", str(exc))
                return
            self._set_raw_text(text)
            self._preview_needs_raw_sync = False
            return
        if self.doc.doc is None:
            return
        if self._raw_dirty:
            return
        if not (self.doc.dirty or self._preview_needs_raw_sync):
            if not self.raw_editor.toPlainText() and self.doc.source_text:
                self._set_raw_text(self.doc.source_text)
            return
        try:
            text = self.doc.serialize()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "XML sync failed", str(exc))
            return
        self._set_raw_text(text)
        self._preview_needs_raw_sync = False

    def _apply_raw_to_preview(self) -> bool:
        """Parse XML text into the WYSIWYG view. Returns False on parse failure."""
        if self._doc_mode == DOC_MODE_ATLAS:
            if self.descr_doc.path is None and not self.raw_editor.toPlainText().strip():
                return True
            if not self._raw_dirty and self.descr_doc.root is not None:
                return True
            text = self.raw_editor.toPlainText()
            was_dirty = self._raw_dirty
            try:
                self.descr_doc.load_text(text, path=self.descr_doc.path)
            except Exception as exc:  # noqa: BLE001
                self._log("error", f"Invalid XML: {exc}")
                QMessageBox.critical(self, "Invalid XML", str(exc))
                return False
            if was_dirty:
                self.descr_doc.mark_dirty()
            self._raw_dirty = False
            self._preview_needs_raw_sync = False
            self.descr_board.set_document(self.descr_doc)
            self.descr_board.fit_stage()
            return True
        if self.doc.path is None and not self.raw_editor.toPlainText().strip():
            return True
        if not self._raw_dirty and self.doc.doc is not None:
            return True
        text = self.raw_editor.toPlainText()
        was_dirty = self._raw_dirty
        meta_was_dirty = self.doc.meta_dirty
        try:
            self.doc.sync_meta_from_doc()
            doc = self.doc.load_text(text, path=self.doc.path, keep_meta=True)
        except Exception as exc:  # noqa: BLE001
            self._log("error", f"Invalid XML: {exc}")
            QMessageBox.critical(self, "Invalid XML", str(exc))
            return False
        if was_dirty:
            self.doc.mark_dirty()
        if meta_was_dirty:
            self.doc.mark_meta_dirty()
        self._raw_dirty = False
        self._preview_needs_raw_sync = False
        self._apply_preview_doc(doc)
        self._restore_view_or_fit()
        return True

    def _export_xml_text(self) -> str:
        """Current document XML text for deploy (does not change open path)."""
        if self._doc_mode == DOC_MODE_ATLAS:
            if self.editor_tabs.currentIndex() == TAB_XML or self._raw_dirty:
                text = self.raw_editor.toPlainText()
            else:
                text = self.descr_doc.serialize()
            if not text.endswith("\n") and text != "":
                text += "\n"
            return text
        if self.editor_tabs.currentIndex() == TAB_XML or self._raw_dirty:
            text, _removed = sanitize_ui_xml_text(self.raw_editor.toPlainText())
        else:
            text = self.doc.serialize()
        if not text.endswith("\n") and text != "":
            text += "\n"
        return text

    def _deploy_dialog_start(self) -> str:
        remembered = get_deploy_target(self.settings, self._active_path())
        if remembered is not None:
            # Prefer full path so the dialog keeps the target file name.
            if remembered.is_file() or remembered.parent.is_dir():
                return str(remembered)
        saved = Path(str(self.settings.get("last_deploy_dir") or ""))
        if saved.is_dir():
            return str(saved)
        return self._file_dialog_start(self._active_path())

    def deploy(self) -> bool:
        """Overwrite remembered deploy target; prompt via Deploy As if unset."""
        target = get_deploy_target(self.settings, self._active_path())
        if target is None:
            return self.deploy_as()
        return self._deploy_to(target)

    def deploy_as(self) -> bool:
        """Pick (or re-pick) the deploy overwrite target, then deploy."""
        if self._doc_mode == DOC_MODE_ATLAS:
            title = "Deploy atlas XML as"
            filt = "Texture atlas XML (*.xml);;All (*.*)"
        else:
            title = "Deploy UI XML as"
            filt = "UI XML (*.xml);;All (*.*)"
        path, _ = QFileDialog.getSaveFileName(
            self, title, self._deploy_dialog_start(), filt
        )
        if not path:
            return False
        return self._deploy_to(Path(path))

    def _deploy_to(self, target: Path) -> bool:
        """Write current content to ``target`` (keeps target’s name / path)."""
        try:
            target = target.expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            text = self._export_xml_text()
            target.write_text(text, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            self._log("error", f"Deploy failed: {exc}")
            QMessageBox.critical(self, "Deploy failed", str(exc))
            return False
        source = self._active_path()
        if source is not None:
            set_deploy_target(self.settings, source, target)
            save_settings(self.settings)
        else:
            # Still remember folder for the next Deploy As dialog.
            parent = target.parent
            if parent.is_dir():
                try:
                    self.settings["last_deploy_dir"] = str(parent.resolve())
                except OSError:
                    self.settings["last_deploy_dir"] = str(parent)
                save_settings(self.settings)
        self._log("info", f"Deployed → {target}")
        self.statusBar().showMessage(f"Deployed → {target}")
        return True

    def save(self) -> bool:
        if self._doc_mode == DOC_MODE_ATLAS:
            return self._save_atlas()
        if self.doc.path is None:
            return self.save_as()
        try:
            self._capture_session_meta()
            if self.editor_tabs.currentIndex() == TAB_XML or self._raw_dirty:
                self.doc.save_raw(self.raw_editor.toPlainText())
                self._set_raw_text(self.doc.source_text)
                self._raw_dirty = False
                self._preview_needs_raw_sync = False
                if self.doc.doc is not None:
                    self._apply_preview_doc(self.doc.doc)
                    self._restore_view_or_fit()
                    self._audit_resources("save")
            else:
                self.doc.save()
                self._set_raw_text(self.doc.source_text)
                self._preview_needs_raw_sync = False
        except Exception as exc:  # noqa: BLE001
            self._log("error", f"Save failed: {exc}")
            QMessageBox.critical(self, "Save failed", str(exc))
            return False
        meta = self.doc.meta_path
        extra = f" + {meta.name}" if meta and meta.is_file() else ""
        self._log("info", f"Saved {self.doc.path}{extra}")
        self.statusBar().showMessage(f"Saved {self.doc.path}{extra}")
        return True

    def _save_atlas(self) -> bool:
        if self.descr_doc.path is None:
            return self.save_as()
        try:
            if self.editor_tabs.currentIndex() == TAB_XML or self._raw_dirty:
                self.descr_doc.save_raw(
                    self.raw_editor.toPlainText(), self.descr_doc.path
                )
                self._set_raw_text(self.descr_doc.source_text)
                self._raw_dirty = False
                self._preview_needs_raw_sync = False
                self.descr_board.set_document(self.descr_doc)
            else:
                self.descr_doc.save()
                self._set_raw_text(self.descr_doc.source_text)
                self._preview_needs_raw_sync = False
            self.resolver.clear_cache()
        except Exception as exc:  # noqa: BLE001
            self._log("error", f"Save failed: {exc}")
            QMessageBox.critical(self, "Save failed", str(exc))
            return False
        self._log("info", f"Saved atlas {self.descr_doc.path}")
        self.statusBar().showMessage(f"Saved {self.descr_doc.path}")
        return True

    def save_as(self) -> bool:
        if self._doc_mode == DOC_MODE_ATLAS:
            start = self._file_dialog_start(self.descr_doc.path)
            path, _ = QFileDialog.getSaveFileName(
                self,
                "Save texture atlas XML",
                start,
                "Texture atlas XML (*.xml);;All (*.*)",
            )
            if not path:
                return False
            try:
                if self.editor_tabs.currentIndex() == TAB_XML or self._raw_dirty:
                    self.descr_doc.save_raw(self.raw_editor.toPlainText(), Path(path))
                else:
                    self.descr_doc.save(Path(path))
                self._set_raw_text(self.descr_doc.source_text)
                self._raw_dirty = False
                self._preview_needs_raw_sync = False
                self.descr_board.set_document(self.descr_doc)
                self.resolver.clear_cache()
                self._remember_file_dir(Path(path))
                self._remember_recent_file(Path(path))
                self.setWindowTitle(
                    f"D.O.G.M.A. Stalker Anomaly Gui Editor - {Path(path).name} [atlas]"
                )
            except Exception as exc:  # noqa: BLE001
                self._log("error", f"Save failed: {exc}")
                QMessageBox.critical(self, "Save failed", str(exc))
                return False
            self._log("info", f"Saved atlas {path}")
            self.statusBar().showMessage(f"Saved {path}")
            return True
        start = self._file_dialog_start(self.doc.path)
        path, _ = QFileDialog.getSaveFileName(
            self, "Save UI XML", start, "UI XML (*.xml);;All (*.*)"
        )
        if not path:
            return False
        try:
            self._capture_session_meta()
            if self.editor_tabs.currentIndex() == TAB_XML or self._raw_dirty:
                self.doc.save_raw(self.raw_editor.toPlainText(), Path(path))
                self._set_raw_text(self.doc.source_text)
                self._raw_dirty = False
                self._preview_needs_raw_sync = False
                if self.doc.doc is not None:
                    self._apply_preview_doc(self.doc.doc)
                    self._restore_view_or_fit()
                    self._audit_resources("save as")
            else:
                self.doc.save(Path(path))
                self._set_raw_text(self.doc.source_text)
                self._preview_needs_raw_sync = False
        except Exception as exc:  # noqa: BLE001
            self._log("error", f"Save As failed: {exc}")
            QMessageBox.critical(self, "Save failed", str(exc))
            return False
        saved = Path(path)
        self._remember_file_dir(saved)
        self._remember_recent_file(saved)
        self.setWindowTitle(f"D.O.G.M.A. Stalker Anomaly Gui Editor - {saved.name}")
        meta = self.doc.meta_path
        extra = f" + {meta.name}" if meta and meta.is_file() else ""
        self._log("info", f"Saved {saved}{extra}")
        self.statusBar().showMessage(f"Saved {saved}{extra}")
        return True

    def edit_settings(self) -> None:
        dlg = InstallRootsDialog(self.settings, self, setup_mode=False)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self.settings = dlg.result_settings()
        # Dialog already rescanned on OK.
        save_settings(self.settings)
        self._reload_textures()

    def rescan_asset_paths(self) -> None:
        """Menu: dialog to pick roots; drop only those cache shards and regen."""
        dlg = RescanDialog(self.settings, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        selected = dlg.selected_targets()
        if not selected:
            return
        labels = ", ".join(t.label for t in selected)
        drop_roots: list[Path] = []
        self._show_busy(
            "Rescanning…",
            detail="Refreshing selected root caches",
        )
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            apply_rescan_selection(self.settings, selected)
            save_settings(self.settings)
            drop_roots = collect_invalidate_roots(self.settings, selected)
            if getattr(self, "path_index", None) is None:
                self.path_index = PathIndex.load()
            self.path_index.configure_installs(
                self.settings.get("anomaly_root") or "",
                self.settings.get("gamma_root") or "",
            )
            self.path_index.invalidate_roots(drop_roots)
            # Memory caches must drop so ensure_indexes reloads/merges shards.
            self._resources_ready = False
            self._bind_resource_roots(warm_thumbs=True)
        finally:
            QApplication.restoreOverrideCursor()
            self._hide_busy()
        n_dds = self.resolver.dds_count if getattr(self, "resolver", None) else 0
        n_drop = len(drop_roots)
        self._log(
            "info",
            f"Asset cache rescanned ({labels}) — {n_drop} root shard(s), "
            f"{n_dds} dds indexed",
        )
        self._refresh_open_doc_after_rescan("rescan")
        self.statusBar().showMessage(
            f"Rescan done · {n_drop} root(s) · {n_dds} dds"
        )

    def _refresh_open_doc_after_rescan(self, audit_tag: str) -> None:
        """Rebind / warm the open document after an asset-cache rescan."""
        if self._doc_mode == DOC_MODE_ATLAS and self.descr_doc.root is not None:
            # Keep disk shards; only clear in-memory atlas/DDS decode state.
            self.resolver.clear_cache()
            self.resolver.ensure_indexes()
            self.descr_board.set_document(self.descr_doc)
        elif self.doc.doc:
            self._warm_resources_for_doc(self.doc.doc)
            self.scene.rebind_resolver(self.resolver)
            self.scene.rebind_text_resources(strings=self.strings, fonts=self.fonts)
            self.scene.set_document(self.doc.doc)
        self._on_stack_peers_changed(frozenset())
        if self._doc_mode == DOC_MODE_UI and self.doc.doc:
            self._audit_resources(audit_tag)

    def _sync_tool_controls(self) -> None:
        border = bool(self.settings.get("show_box_border", False))
        fill = bool(self.settings.get("show_box_fill", False))
        labels = bool(self.settings.get("show_element_labels", False))
        rulers = bool(self.settings.get("show_rulers", True))
        grid = bool(self.settings.get("show_grid", False))
        font = clamp_label_font_size(self.settings.get("label_font_size"))
        for w in (self.border_a, self.tool_border):
            w.blockSignals(True)
            w.setChecked(border)
            w.blockSignals(False)
        for w in (self.fill_a, self.tool_fill):
            w.blockSignals(True)
            w.setChecked(fill)
            w.blockSignals(False)
        for w in (self.labels_a, self.tool_labels):
            w.blockSignals(True)
            w.setChecked(labels)
            w.blockSignals(False)
        for w in (self.rulers_a, self.tool_rulers):
            w.blockSignals(True)
            w.setChecked(rulers)
            w.blockSignals(False)
        for w in (self.grid_a, self.tool_grid):
            w.blockSignals(True)
            w.setChecked(grid)
            w.blockSignals(False)
        step = clamp_grid_step(self.settings.get("grid_step"))
        self.tool_grid_step.blockSignals(True)
        self.tool_grid_step.setValue(step)
        self.tool_grid_step.setEnabled(grid)
        self.tool_grid_step.blockSignals(False)
        self.tool_grid_step_unit.setEnabled(grid)
        self.tool_font.blockSignals(True)
        self.tool_font.setValue(font)
        self.tool_font.blockSignals(False)
        self._rebuild_aspect_combo()

    def _rebuild_aspect_combo(self) -> None:
        if not hasattr(self, "tool_aspect"):
            return
        current = normalize_preview_aspect(self._preview_aspect)
        self.tool_aspect.blockSignals(True)
        self.tool_aspect.clear()
        for label, data in preview_aspect_combo_items():
            self.tool_aspect.addItem(label, data)
        idx = self.tool_aspect.findData(current)
        self.tool_aspect.setCurrentIndex(max(0, idx))
        self.tool_aspect.blockSignals(False)

    def _apply_preview_aspect(self) -> None:
        key = normalize_preview_aspect(self._preview_aspect)
        self.canvas.set_aspect_stretch_x(preview_aspect_stretch_x(key))

    def _on_preview_aspect(self, *_args) -> None:
        self._preview_aspect = normalize_preview_aspect(self.tool_aspect.currentData())
        self._apply_preview_aspect()
        self._sync_zoom_slider_from_view()

    def _on_toggle_border(self, checked: bool) -> None:
        self.settings["show_box_border"] = checked
        save_settings(self.settings)
        self.scene.set_box_style(show_border=checked)
        self.descr_board.scene.set_box_style(show_border=checked)
        self._sync_tool_controls()

    def _on_toggle_fill(self, checked: bool) -> None:
        self.settings["show_box_fill"] = checked
        save_settings(self.settings)
        self.scene.set_box_style(show_fill=checked)
        self.descr_board.scene.set_box_style(show_fill=checked)
        self._sync_tool_controls()

    def _on_toggle_labels(self, checked: bool) -> None:
        self.settings["show_element_labels"] = checked
        save_settings(self.settings)
        self.scene.set_show_element_labels(checked)
        self.descr_board.scene.set_show_element_labels(checked)
        self._sync_tool_controls()

    def _on_toggle_rulers(self, checked: bool) -> None:
        self.settings["show_rulers"] = checked
        save_settings(self.settings)
        self.canvas_board.set_rulers_visible(checked)
        self._sync_tool_controls()

    def _on_toggle_grid(self, checked: bool) -> None:
        self.settings["show_grid"] = checked
        save_settings(self.settings)
        self.scene.set_pixel_grid_visible(checked)
        self.descr_board.scene.set_pixel_grid_visible(checked)
        self._sync_tool_controls()

    def _on_grid_step(self, step: int) -> None:
        step = clamp_grid_step(step)
        self.settings["grid_step"] = step
        save_settings(self.settings)
        self.scene.set_pixel_grid_step(step)
        self.descr_board.scene.set_pixel_grid_step(step)
        self._sync_tool_controls()

    def _on_tool_font_size(self, size: int) -> None:
        size = clamp_label_font_size(size)
        self.settings["label_font_size"] = size
        save_settings(self.settings)
        self.scene.set_label_font_size(size)
        self.descr_board.scene.set_label_font_size(size)

    def _reload_textures(self) -> None:
        # Unpack if needed; keep saved path lists (use Edit → Rescan to rediscover).
        before = (
            list(self.settings.get("gamedata_texture_roots") or []),
            list(self.settings.get("gamedata_descr_roots") or []),
            list(self.settings.get("gamedata_text_roots") or []),
        )
        ensure_db_unpacked_and_roots(self.settings)
        after = (
            list(self.settings.get("gamedata_texture_roots") or []),
            list(self.settings.get("gamedata_descr_roots") or []),
            list(self.settings.get("gamedata_text_roots") or []),
        )
        if after != before:
            save_settings(self.settings)
            self._log("info", "Asset roots updated after Anomaly DB unpack check")
        self._resources_ready = False
        self._bind_resource_roots()
        if self._doc_mode == DOC_MODE_ATLAS and self.descr_doc.root is not None:
            self.resolver.clear_cache()
            self.descr_board.set_document(self.descr_doc)
        elif self.doc.doc:
            self._warm_resources_for_doc(self.doc.doc)
            self.scene.rebind_resolver(self.resolver)
            self.scene.rebind_text_resources(strings=self.strings, fonts=self.fonts)
            self.scene.set_document(self.doc.doc)
        self._on_stack_peers_changed(frozenset())
        self._log(
            "info",
            f"Resources rebound | atlas {self.resolver.atlas_count} | "
            f"dds {self.resolver.dds_count} | strings {self.strings.count}",
        )
        if self._doc_mode == DOC_MODE_UI and self.doc.doc:
            self._audit_resources("reload")
            selected = [
                i for i in self.scene.selectedItems() if hasattr(i, "node")
            ]
            if selected:
                self._show_props(selected[0].node)
        self.statusBar().showMessage(
            f"Resources reloaded · {self.resolver.atlas_count} atlas · "
            f"{self.resolver.dds_count} dds · {self.strings.count} strings"
        )

    def _log(self, level: str, message: str) -> None:
        """Append a line to the Log tab (info / warn / error) and sage.log."""
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] {level.upper():<5} {message}"
        lvl = {
            "debug": logging.DEBUG,
            "info": logging.INFO,
            "warn": logging.WARNING,
            "warning": logging.WARNING,
            "error": logging.ERROR,
            "critical": logging.CRITICAL,
        }.get(level.lower(), logging.INFO)
        _log_file.log(lvl, "%s", message)
        view = getattr(self, "log_view", None)
        if view is None:
            return
        view.appendPlainText(line)
        view.moveCursor(QTextCursor.MoveOperation.End)

    def _audit_resources(self, reason: str = "") -> None:
        """Resolve all textures/strings; log summary plus each failure."""
        doc = self.doc.doc
        if doc is None:
            self._log("warn", f"Audit skipped ({reason or 'no document'})")
            return
        tex_ok = tex_fail = 0
        text_ok = text_fail = text_lit = 0
        for node in doc.iter_drawables():
            if node.from_meta:
                continue
            path = node.path or node.tag
            if node.texture and node.texture.name:
                # Probe atlas/DDS presence only — decoding is done during warm/paint.
                ok, err = self.resolver.probe_ref(node.texture)
                if not ok:
                    self._log("error", f"texture {path}: {node.texture.name} → {err}")
                    tex_fail += 1
                else:
                    tex_ok += 1
            if node.text and node.text.content:
                s = self.strings.resolve(node.text.content)
                if s.error:
                    self._log("error", f"text {path}: {node.text.content} → {s.error}")
                    text_fail += 1
                elif s.is_literal:
                    text_lit += 1
                else:
                    text_ok += 1
        label = f"Audit ({reason})" if reason else "Audit"
        self._log(
            "info",
            f"{label}: textures ok={tex_ok} fail={tex_fail} | "
            f"strings ok={text_ok} literal={text_lit} fail={text_fail}",
        )

    def _fill_tree(self, doc: LayoutNode) -> None:
        self.scene.set_tree_hover_path(None)
        self._on_stack_peers_changed(frozenset())
        self.tree.clear()
        self._tree_items_by_path.clear()

        def add(node: LayoutNode, parent_item: QTreeWidgetItem | None) -> None:
            label = node.tag or node.path or "(root)"
            if node.from_meta:
                label += "  [layer root]"
            item = QTreeWidgetItem([label])
            item.setData(0, Qt.ItemDataRole.UserRole, node.path)
            if node.path:
                self._tree_items_by_path[node.path] = item
            if parent_item is None:
                self.tree.addTopLevelItem(item)
            else:
                parent_item.addChild(item)
            for child in node.children:
                add(child, item)

        for child in doc.children:
            add(child, None)
        self.tree.expandToDepth(1)

    def _on_stack_peers_changed(self, paths: object) -> None:
        """Grey-highlight Tree rows for overlapping canvas stack peers."""
        new_paths = set(paths) if paths else set()
        old = self._tree_peer_paths
        if old == new_paths:
            return
        for path in old - new_paths:
            item = self._tree_items_by_path.get(path)
            if item is not None:
                item.setData(0, _TREE_PEER_ROLE, False)
        for path in new_paths - old:
            item = self._tree_items_by_path.get(path)
            if item is None:
                continue
            item.setData(0, _TREE_PEER_ROLE, True)
            parent = item.parent()
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()
        self._tree_peer_paths = new_paths
        self.tree.viewport().update()

    def _fill_layers(self, doc: LayoutNode) -> None:
        self.layers.blockSignals(True)
        self.layers.clear()
        saved = self.doc.layer_states
        states: dict[str, bool] = {}
        for section in layer_sections(doc):
            item = QListWidgetItem(section.path or section.tag)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            if section.path and section.path in saved:
                visible = saved[section.path]
            else:
                visible = default_layer_visible(section)
            item.setCheckState(
                Qt.CheckState.Checked if visible else Qt.CheckState.Unchecked
            )
            item.setData(Qt.ItemDataRole.UserRole, section.path)
            self.layers.addItem(item)
            if section.path:
                states[section.path] = visible
        self.layers.blockSignals(False)
        # Snapshot current toggles into meta map (no dirty - restore/defaults only).
        self.doc.replace_layer_states(states)
        # Deepest matching layer toggle wins (nested layers are independent).
        self.scene.set_layer_states(states)
        self._sync_tree_layer_visibility()
        self.layers.updateGeometry()
        self._relayout_sidebar_fit()

    def _sync_tree_layer_visibility(self) -> None:
        """Grey Tree rows whose nodes are hidden by a deselected layer."""
        doc = self.scene.doc
        for path, item in self._tree_items_by_path.items():
            node = doc.find_by_path(path) if doc is not None else None
            item.setData(0, _TREE_LAYER_OFF_ROLE, node is None or not node.visible)
        self.tree.viewport().update()

    def _on_layer_toggled(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        visible = item.checkState() == Qt.CheckState.Checked
        self.scene.set_section_visibility(path, visible)
        self.doc.set_layer_state(path, visible)
        self._sync_tree_layer_visibility()

    def _on_layer_label_clicked(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        # Clicking a layer root enables it (if off) so its content can show alone.
        if item.checkState() != Qt.CheckState.Checked:
            item.setCheckState(Qt.CheckState.Checked)
        self.scene.select_path(path)

    def _on_tree_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if not key:
            return
        if self._doc_mode == DOC_MODE_ATLAS:
            sheet = self.descr_board.current_sheet()
            if sheet is None:
                return
            for reg in sheet.regions:
                if reg.atlas_id == key:
                    self.descr_board.scene.select_region(reg)
                    return
            return
        node = self.scene.doc.find_by_path(key) if self.scene.doc else None
        if node is None or not node.visible:
            return
        self.scene.select_path(key)

    def _on_tree_item_entered(self, item: QTreeWidgetItem, _col: int) -> None:
        if self._doc_mode == DOC_MODE_ATLAS:
            return
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(path, str) or path not in self.scene._items:
            # Structural / non-drawable rows: leave current canvas hover alone.
            return
        node = self.scene.doc.find_by_path(path) if self.scene.doc else None
        if node is None or not node.visible:
            self.scene.set_tree_hover_path(None)
            return
        self.scene.set_tree_hover_path(path)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        et = event.type()
        if et in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            if self._filter_nudge_key(event):
                return True
        if obj is self.tree.viewport():
            if self._doc_mode == DOC_MODE_ATLAS:
                return super().eventFilter(obj, event)
            if et == QEvent.Type.Leave:
                self.scene.set_tree_hover_path(None)
            elif et == QEvent.Type.MouseMove:
                pos = event.position().toPoint()
                if self.tree.itemAt(pos) is None:
                    self.scene.set_tree_hover_path(None)
        return super().eventFilter(obj, event)

    def changeEvent(self, event) -> None:  # noqa: N802
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow():
            self.canvas.end_nudge()
            self.descr_board.view.end_nudge()
        super().changeEvent(event)

    def _on_canvas_selection(self, node: LayoutNode | None) -> None:
        self._commit_props_geo_undo()
        self._show_props(node)
        if not self._restoring_meta:
            self.doc.set_selection(node.path if node and node.path else "")
        self._xml_sel_path = node.path if node and node.path else ""
        self._apply_xml_selection_highlight()
        if node and node.path:
            # Sync tree selection
            matches = self.tree.findItems(
                node.tag, Qt.MatchFlag.MatchContains | Qt.MatchFlag.MatchRecursive
            )
            for it in matches:
                if it.data(0, Qt.ItemDataRole.UserRole) == node.path:
                    self.tree.setCurrentItem(it)
                    break

    def _commit_props_geo_undo(self) -> None:
        before = self._props_geo_before
        self._props_geo_before = None
        if before is None:
            return
        if self._doc_mode == DOC_MODE_ATLAS:
            edited = self.descr_doc.find_region(before.path)
            if edited is None:
                return
            self.descr_board.scene.push_geo_edit(
                GeoEdit.single(
                    before,
                    GeoState(
                        path=edited.atlas_id,
                        x=edited.x,
                        y=edited.y,
                        width=edited.width,
                        height=edited.height,
                    ),
                )
            )
            return
        if self.doc.doc is None:
            return
        edited = self.doc.doc.find_by_path(before.path)
        if edited is None:
            return
        self.scene.push_geo_edit(
            GeoEdit.single(
                before,
                GeoState(
                    path=edited.path,
                    x=edited.x,
                    y=edited.y,
                    width=edited.width,
                    height=edited.height,
                ),
            )
        )

    def _on_geometry_changed(self, node: LayoutNode) -> None:
        if node.from_meta:
            self.doc.mark_meta_dirty()
        else:
            self._mark_xml_dirty()
        self._show_props(node)
        # Child abs positions: callers that change parent geo should refresh;
        # group-move / resize already refresh before emitting.
        if self.scene.doc is not None:
            # Cheap pos sync only (textures unchanged on move/resize of others).
            self.scene.refresh_item_positions()

    def _pin_splitter_sizes(self) -> None:
        """Keep sidebar widths stable; only manual splitter drag should change them."""
        sizes = self.split.sizes()
        if (
            len(sizes) == 3
            and sizes[0] >= SIDEBAR_MIN_WIDTH
            and sizes[2] >= SIDEBAR_MIN_WIDTH
        ):
            self.split.setSizes(sizes)
            return
        # Layout thrash crushed a pane — restore from settings/defaults.
        self._apply_splitter_sizes()

    def _on_splitter_moved(self, *_args) -> None:
        self._splitter_save_timer.start()

    def _apply_splitter_sizes(self) -> None:
        sizes = normalize_splitter_sizes(self.settings.get("splitter_sizes"))
        self.settings["splitter_sizes"] = sizes
        self.split.setSizes(sizes)

    def _save_splitter_sizes(self) -> None:
        sizes = self.split.sizes()
        if len(sizes) != 3:
            return
        # Never persist crushed panes — keeps next launch at least min width.
        if sizes[0] < SIDEBAR_MIN_WIDTH or sizes[2] < SIDEBAR_MIN_WIDTH:
            return
        normalized = normalize_splitter_sizes(sizes)
        if normalized == normalize_splitter_sizes(self.settings.get("splitter_sizes")):
            return
        self.settings["splitter_sizes"] = normalized
        save_settings(self.settings)

    def _set_path_label(self, path: str) -> None:
        self.prop_path.setText(_path_rich_text(path or "-"))

    def _set_meta_prop_labels(self, from_meta: bool) -> None:
        """Orange Pos / Size / Texture heading for layer-root meta handles."""
        style = self._meta_prop_style if from_meta else ""
        self.prop_label_pos.setStyleSheet(style)
        self.prop_label_size.setStyleSheet(style)
        self.prop_label_path.setStyleSheet("")
        self.prop_path.setStyleSheet("")
        tex = getattr(self, "_texture_group", None)
        if tex is not None:
            tex.setStyleSheet(
                "QGroupBox::title { color: #E67E22; font-weight: bold; }"
                if from_meta
                else ""
            )

    def _set_optional_prop_label(self, label: QLabel, text: str) -> None:
        text = text or ""
        label.setText(text)
        label.setVisible(bool(text.strip()))

    def _set_combo_data(self, combo: QComboBox, value: str) -> None:
        value = (value or "").strip()
        idx = combo.findData(value)
        if idx < 0 and combo.isEditable():
            # Preserve unknown / custom font names in the editable field.
            combo.setCurrentIndex(-1)
            combo.setEditText(value)
            return
        combo.setCurrentIndex(max(0, idx))

    def _combo_data(self, combo: QComboBox) -> str:
        if combo.isEditable() and combo.currentIndex() < 0:
            return combo.currentText().strip()
        data = combo.currentData()
        if data is None:
            return combo.currentText().strip()
        return str(data)

    def _refresh_font_combo(self) -> None:
        if not hasattr(self, "edit_font"):
            return
        from .fonts import ENGINE_FONT_NAMES

        current = self._combo_data(self.edit_font)
        self.edit_font.blockSignals(True)
        self.edit_font.clear()
        self.edit_font.addItem("default (letterica16)", "")
        names = self.fonts.known_font_names() if hasattr(self, "fonts") else []
        if not names:
            names = list(ENGINE_FONT_NAMES)
        for name in names:
            self.edit_font.addItem(name, name)
        self._set_combo_data(self.edit_font, current)
        self.edit_font.blockSignals(False)

    def _parse_optional_int(self, text: str) -> int | None | object:
        """Blank → None (omit attr); invalid → ``...`` (leave unchanged)."""
        raw = text.strip()
        if raw == "" or raw == "—":
            return None
        try:
            return int(raw)
        except ValueError:
            return ...

    def _parse_optional_float(self, text: str) -> float | None | object:
        raw = text.strip()
        if raw == "" or raw == "—":
            return None
        try:
            return float(raw)
        except ValueError:
            return ...

    def _selected_prop_node(self) -> LayoutNode | None:
        selected = [i for i in self.scene.selectedItems() if hasattr(i, "node")]
        if not selected:
            return None
        node: LayoutNode = selected[0].node
        if not node.is_drawable or node.from_meta:
            return None
        return node

    def _refresh_selected_item(self, node: LayoutNode) -> None:
        item = self.scene.item_for_node(node)
        if item:
            item.refresh_look()

    def _fill_text_prop_fields(self, node: LayoutNode) -> None:
        enabled = not node.from_meta
        for w in (
            self.edit_font,
            self.edit_align,
            self.edit_vert_align,
            self.edit_complex_mode,
            self.edit_text_r,
            self.edit_text_g,
            self.edit_text_b,
            self.edit_text_a,
            self.edit_text_x,
            self.edit_text_y,
        ):
            w.setEnabled(enabled)
        ref = node.text
        self._set_combo_data(self.edit_font, ref.font if ref else "")
        self._set_combo_data(self.edit_align, ref.align if ref else "")
        self._set_combo_data(self.edit_vert_align, ref.vert_align if ref else "")
        self.edit_complex_mode.setChecked(bool(ref and ref.complex_mode))
        self.edit_text_r.setText("" if not ref or ref.r is None else str(ref.r))
        self.edit_text_g.setText("" if not ref or ref.g is None else str(ref.g))
        self.edit_text_b.setText("" if not ref or ref.b is None else str(ref.b))
        self.edit_text_a.setText("" if not ref or ref.a is None else str(ref.a))
        # Offset attrs are optional; blank means omit from XML (engine 0).
        text_el = node.element.find("text")
        if not ref or text_el is None:
            self.edit_text_x.setText("")
            self.edit_text_y.setText("")
        else:
            self.edit_text_x.setText(
                _num(ref.x) if text_el.get("x") is not None else ""
            )
            self.edit_text_y.setText(
                _num(ref.y) if text_el.get("y") is not None else ""
            )

    def _fill_scroll_prop_fields(self, node: LayoutNode) -> None:
        if not node.allows_scroll_props():
            return
        for ed, name in (
            (self.edit_left_ident, "left_ident"),
            (self.edit_right_ident, "right_ident"),
            (self.edit_top_indent, "top_indent"),
            (self.edit_bottom_indent, "bottom_indent"),
            (self.edit_vert_interval, "vert_interval"),
        ):
            ed.setEnabled(not node.from_meta)
            val = node.widget_float_attr(name)
            ed.setText("" if val is None else _num(val))
        self.edit_always_show_scroll.setEnabled(not node.from_meta)
        self.edit_always_show_scroll.setChecked(
            node.widget_bool_attr("always_show_scroll", default=False)
        )

    def _apply_prop_visibility(self, node: LayoutNode | None) -> None:
        """Show prop groups for this widget kind; disable inapplicable fields."""
        if self._doc_mode == DOC_MODE_ATLAS:
            return
        ui = node is not None and node.is_drawable
        show_tex = bool(ui and node.allows_texture_props())
        show_text = bool(ui and node.allows_text_props())
        show_scroll = bool(ui and not node.from_meta and node.allows_scroll_props())
        for w in self._texture_prop_widgets:
            w.setVisible(show_tex)
        self.edit_stretch.setVisible(True)
        for w in self._text_prop_widgets:
            w.setVisible(show_text)
        for w in self._scroll_prop_widgets:
            w.setVisible(show_scroll)
        self._sync_asset_prop_assignment(node)

    def _sync_asset_prop_assignment(self, node: LayoutNode | None) -> None:
        """Grey texture/text names+values when unassigned; folder browse stays live."""
        has_tex = bool(
            node
            and not node.from_meta
            and self.prop_texture.has_texture()
        )
        has_text = bool(node and not node.from_meta and node.text)
        can_edit = bool(node and not node.from_meta)

        self.prop_label_texture.setEnabled(has_tex)
        if not has_tex:
            self.prop_texture.name.setStyleSheet(f"color: {_GREY_INAPPLICABLE};")
        # UV sits under Path; always secondary grey (never a separate prop row).
        self.prop_texture_uv.setStyleSheet(f"color: {_GREY_DEFAULT};")
        # Never disable the path row (would grey out the browse folder button).
        self.edit_stretch.setEnabled(
            bool(
                has_tex
                and can_edit
                and node is not None
                and node.allows_stretch()
            )
        )

        for lab in (
            self.prop_label_text,
            self.prop_label_text_off,
            self.prop_label_align,
            self.prop_label_font,
            self.prop_label_color,
        ):
            lab.setEnabled(has_text)
        if not has_text:
            self.prop_text.name.setStyleSheet(f"color: {_GREY_INAPPLICABLE};")
        text_on = bool(has_text and can_edit)
        for w in (
            self._text_xy_widget,
            self._align_row,
            self.edit_font,
            self._color_row,
            self._text_flags_row,
            self.edit_text_x,
            self.edit_text_y,
            self.edit_align,
            self.edit_vert_align,
            self.edit_text_r,
            self.edit_text_g,
            self.edit_text_b,
            self.edit_text_a,
            self.edit_complex_mode,
        ):
            w.setEnabled(text_on)

    def _clear_extra_prop_fields(self) -> None:
        self._set_combo_data(self.edit_font, "")
        self._set_combo_data(self.edit_align, "")
        self._set_combo_data(self.edit_vert_align, "")
        self.edit_complex_mode.setChecked(False)
        for ed in (
            self.edit_text_r,
            self.edit_text_g,
            self.edit_text_b,
            self.edit_text_a,
            self.edit_text_x,
            self.edit_text_y,
            self.edit_left_ident,
            self.edit_right_ident,
            self.edit_top_indent,
            self.edit_bottom_indent,
            self.edit_vert_interval,
        ):
            ed.setText("")
            ed.setEnabled(False)
        self.edit_font.setEnabled(False)
        self.edit_align.setEnabled(False)
        self.edit_vert_align.setEnabled(False)
        self.edit_complex_mode.setEnabled(False)
        self.edit_always_show_scroll.setChecked(False)
        self.edit_always_show_scroll.setEnabled(False)
        self._scroll_group.setVisible(False)

    def _set_texture_uv_row(self, node: LayoutNode | None) -> None:
        """Readonly UV under Texture path: ``X N  Y N  W N  H N`` (pixel ints)."""
        uv: tuple[float, float, float, float] | None = None
        tip = ""
        if node is not None and node.texture and not node.from_meta:
            tex = node.texture
            if tex.has_uv:
                uv = (tex.uv_x, tex.uv_y, tex.uv_w, tex.uv_h)
                tip = "UV from XML <texture> attrs"
            elif not tex.is_path and tex.name:
                entry = self.resolver.lookup_atlas(tex.name)
                if entry is not None:
                    uv = (entry.x, entry.y, entry.width, entry.height)
                    tip = "UV from textures_descr"
        self.prop_texture_uv.setStyleSheet(f"color: {_GREY_DEFAULT};")
        if uv is None:
            self.prop_texture_uv.setText("<none>")
            self.prop_texture_uv.setToolTip("")
            return
        x, y, w, h = (int(round(v)) for v in uv)
        self.prop_texture_uv.setText(f"X {x}  Y {y}  W {w}  H {h}")
        self.prop_texture_uv.setToolTip(tip)

    def _show_props(self, node: LayoutNode | None) -> None:
        self._updating_props = True
        self._set_geo_field_styles(True)
        if node is None or not node.is_drawable:
            self._set_path_label(node.path if node else "-")
            self._set_meta_prop_labels(False)
            self._set_optional_prop_label(self.prop_meta_note, "")
            for ed in (self.edit_x, self.edit_y, self.edit_w, self.edit_h):
                ed.setText("")
                ed.setEnabled(bool(node and node.is_drawable))
            self.edit_stretch.setChecked(False)
            self.edit_show_texture.setChecked(True)
            self.edit_show_texture.setEnabled(False)
            self.prop_texture.clear("<none>")
            self.prop_texture.set_browse_enabled(False)
            self._set_texture_uv_row(None)
            self.prop_text.clear("-")
            self.prop_text.set_browse_enabled(False)
            self._clear_extra_prop_fields()
            self._apply_prop_visibility(None)
            self._updating_props = False
            self._pin_splitter_sizes()
            self._relayout_sidebar_fit()
            QTimer.singleShot(0, self._relayout_sidebar_fit)
            return
        self._set_path_label(node.path or "-")
        self._set_meta_prop_labels(node.from_meta)
        if node.from_meta:
            self._set_optional_prop_label(
                self.prop_meta_note, "(meta handle - saved to .xml.meta)"
            )
        else:
            self._set_optional_prop_label(self.prop_meta_note, "")
        self.edit_x.setEnabled(True)
        self.edit_y.setEnabled(True)
        # Meta handles are fixed diamond markers - position only.
        self.edit_w.setEnabled(not node.from_meta)
        self.edit_h.setEnabled(not node.from_meta)
        self.edit_x.setText(_num(node.x))
        self.edit_y.setText(_num(node.y))
        self.edit_w.setText(_num(node.width))
        self.edit_h.setText(_num(node.height))
        self.edit_stretch.setChecked(node.stretch)
        item = self.scene.item_for_node(node)
        self.edit_show_texture.setChecked(
            True if item is None else bool(item.show_texture)
        )
        self.prop_texture.set_browse_enabled(
            (not node.from_meta) and node.allows_texture_props()
        )
        if node.from_meta:
            self.prop_texture.clear("(meta)")
            self._set_texture_uv_row(None)
        elif node.texture:
            resolved = self.resolver.resolve(node.texture)
            tip_bits: list[str] = []
            name = node.texture.name or ""
            if name:
                kind = "path" if node.texture.is_path else "atlas"
                tip_bits.append(f"{name} ({kind})")
            if node.texture.has_uv:
                tip_bits.append(
                    f"XML UV {int(node.texture.uv_x)},{int(node.texture.uv_y)} "
                    f"{int(node.texture.uv_w)}×{int(node.texture.uv_h)}"
                )
            elif not node.texture.is_path and resolved.atlas_id:
                tip_bits.append("UV from textures_descr")
            if resolved.error:
                display = f"⚠ {resolved.error}"
                tip_bits.append(resolved.error)
                self.prop_texture.set_file(
                    display=display,
                    tip_extra="\n".join(tip_bits),
                )
            else:
                # Prefer showing the XML name (atlas id or path), not only DDS filename.
                display = name or (
                    resolved.path.name if resolved.path else "<none>"
                )
                if display == "<none>":
                    self.prop_texture.clear("<none>")
                else:
                    explorer = (
                        windows_explorer_path(resolved.path)
                        if resolved.path
                        else ""
                    )
                    if resolved.path and not node.texture.is_path:
                        tip_bits.append(f"sheet: {resolved.path.name}")
                    self.prop_texture.set_file(
                        display=display,
                        explorer_path=explorer,
                        tip_extra="\n".join(tip_bits),
                    )
            self._set_texture_uv_row(node)
        else:
            self.prop_texture.clear("<none>")
            self._set_texture_uv_row(None)
        self.prop_text.set_browse_enabled(
            (not node.from_meta) and node.allows_text_props()
        )
        if node.from_meta:
            self.prop_text.clear("(meta)")
        elif node.text and node.text.content:
            content = node.text.content
            resolved = self.strings.resolve(content)
            tip_bits = [content]
            if resolved.text:
                tip_bits.append(resolved.text)
            if resolved.error:
                self.prop_text.set_file(
                    display=f"⚠ {resolved.error}",
                    tip_extra="\n".join(tip_bits),
                    color="#c62828",
                )
            elif resolved.is_literal:
                preview = resolved.text.replace("\n", " · ")
                self.prop_text.set_file(
                    display=preview or content,
                    tip_extra="\n".join(tip_bits),
                    color=_GREY_DEFAULT,
                )
            elif resolved.source is not None:
                self.prop_text.set_xml_key(
                    xml_name=resolved.source.name,
                    key=content,
                    explorer_path=windows_explorer_path(resolved.source),
                    tip_extra="\n".join(tip_bits),
                )
            else:
                self.prop_text.set_file(
                    display=content,
                    tip_extra="\n".join(tip_bits),
                )
        else:
            if not node.text:
                self.prop_text.clear("(none)")
            else:
                self.prop_text.clear("(empty <text>)")
                self.prop_text.set_clearable(True)
        self._fill_text_prop_fields(node)
        self._fill_scroll_prop_fields(node)
        self._apply_prop_visibility(node)
        self.edit_show_texture.setEnabled(node.allows_texture_props())
        self._updating_props = False
        self._pin_splitter_sizes()
        self._relayout_sidebar_fit()
        QTimer.singleShot(0, self._relayout_sidebar_fit)

    def _update_undo_actions(self) -> None:
        if self.editor_tabs.currentIndex() == TAB_XML:
            doc = self.raw_editor.document()
            self.undo_a.setEnabled(doc.isUndoAvailable())
            self.redo_a.setEnabled(doc.isRedoAvailable())
        elif self._doc_mode == DOC_MODE_ATLAS:
            stack = self.descr_board.scene.undo_stack
            self.undo_a.setEnabled(stack.can_undo())
            self.redo_a.setEnabled(stack.can_redo())
        else:
            self.undo_a.setEnabled(self.scene.undo_stack.can_undo())
            self.redo_a.setEnabled(self.scene.undo_stack.can_redo())

    def undo(self) -> None:
        if self.editor_tabs.currentIndex() == TAB_XML:
            if not self.raw_editor.document().isUndoAvailable():
                return
            self.raw_editor.undo()
            self._update_undo_actions()
            self.statusBar().showMessage("Undo XML text")
            return
        if self._doc_mode == DOC_MODE_ATLAS:
            edit = self.descr_board.scene.undo_stack.undo()
            if edit is None or isinstance(edit, PropEdit):
                return
            reg = self.descr_board.scene.apply_geo_edit(edit, use_after=False)
            self.descr_doc.mark_dirty()
            self._preview_needs_raw_sync = True
            self._update_undo_actions()
            self._refresh_undo_list()
            if reg is not None:
                self.descr_board.scene.select_region(reg)
                self._show_descr_props(reg)
                self.statusBar().showMessage(f"Undo {reg.atlas_id}")
            return
        edit = self.scene.undo_stack.undo()
        if edit is None:
            return
        node = self.scene.apply_edit(edit, use_after=False)
        if node is not None and node.from_meta:
            self.doc.mark_meta_dirty()
        else:
            self._mark_xml_dirty()
        self._on_undo_stack_changed()
        if node is not None:
            self.scene.select_path(node.path)
            self._show_props(node)
            self.statusBar().showMessage(f"Undo {edit.describe()}")

    def redo(self) -> None:
        if self.editor_tabs.currentIndex() == TAB_XML:
            if not self.raw_editor.document().isUndoAvailable():
                return
            self.raw_editor.redo()
            self._update_undo_actions()
            self.statusBar().showMessage("Redo XML text")
            return
        if self._doc_mode == DOC_MODE_ATLAS:
            edit = self.descr_board.scene.undo_stack.redo()
            if edit is None or isinstance(edit, PropEdit):
                return
            reg = self.descr_board.scene.apply_geo_edit(edit, use_after=True)
            self.descr_doc.mark_dirty()
            self._preview_needs_raw_sync = True
            self._update_undo_actions()
            self._refresh_undo_list()
            if reg is not None:
                self.descr_board.scene.select_region(reg)
                self._show_descr_props(reg)
                self.statusBar().showMessage(f"Redo {reg.atlas_id}")
            return
        edit = self.scene.undo_stack.redo()
        if edit is None:
            return
        node = self.scene.apply_edit(edit, use_after=True)
        if node is not None and node.from_meta:
            self.doc.mark_meta_dirty()
        else:
            self._mark_xml_dirty()
        self._on_undo_stack_changed()
        if node is not None:
            self.scene.select_path(node.path)
            self._show_props(node)
            self.statusBar().showMessage(f"Redo {edit.describe()}")

    def _push_prop_edit(self, before, after) -> bool:
        """Push a props undo entry when state actually changed."""
        edit = PropEdit(before=before, after=after)
        if not edit.changed():
            return False
        self.scene.push_edit(edit)
        return True

    def _commit_prop_change(self, node: LayoutNode, before) -> bool:
        """Push prop undo (add/remove text/texture, etc.), mark dirty, refresh UI."""
        if not self._push_prop_edit(before, node.capture_prop_state()):
            return False
        self._mark_xml_dirty()
        self._show_props(node)
        self._refresh_selected_item(node)
        return True

    def _parse_geometry_fields(self) -> tuple[float, float, float, float] | None:
        """Return (x,y,w,h) if all fields are valid numbers; else None."""
        try:
            x = float(self.edit_x.text().strip())
            y = float(self.edit_y.text().strip())
            w = float(self.edit_w.text().strip())
            h = float(self.edit_h.text().strip())
        except ValueError:
            return None
        if not all(map(isfinite, (x, y, w, h))):
            return None
        if w < 1.0 or h < 1.0:
            return None
        return x, y, w, h

    def _set_geo_field_styles(self, valid: bool) -> None:
        style = "color: #c62828;" if not valid else ""
        for ed in (self.edit_x, self.edit_y, self.edit_w, self.edit_h):
            ed.setStyleSheet(style)

    def _on_props_changed(self) -> None:
        if self._updating_props:
            return
        if self._doc_mode == DOC_MODE_ATLAS:
            region = self.descr_board.scene.selected_region()
            if region is None:
                return
            parsed = self._parse_geometry_fields()
            self._set_geo_field_styles(parsed is not None)
            if parsed is None:
                return
            x, y, w, h = parsed
            if self._props_geo_before is None:
                self._props_geo_before = GeoState(
                    path=region.atlas_id,
                    x=region.x,
                    y=region.y,
                    width=region.width,
                    height=region.height,
                )
            if (
                region.x == x
                and region.y == y
                and region.width == w
                and region.height == h
            ):
                return
            region.set_geometry(x=x, y=y, width=w, height=h)
            self.descr_doc.mark_dirty()
            self._preview_needs_raw_sync = True
            self.descr_board.scene.refresh_item(region)
            return
        selected = [i for i in self.scene.selectedItems() if hasattr(i, "node")]
        if not selected:
            return
        node: LayoutNode = selected[0].node
        parsed = self._parse_geometry_fields()
        self._set_geo_field_styles(parsed is not None)
        if parsed is None:
            return
        x, y, w, h = parsed
        if self._props_geo_before is None:
            self._props_geo_before = GeoState(
                path=node.path,
                x=node.x,
                y=node.y,
                width=node.width,
                height=node.height,
            )
        if (
            node.x == x
            and node.y == y
            and node.width == w
            and node.height == h
        ):
            return
        node.set_geometry(x=x, y=y, width=w, height=h)
        if not node.from_meta:
            node.apply_geometry_to_element()
        if self.doc.doc:
            self.doc.doc.recompute_absolute(0.0, 0.0)
        if node.from_meta:
            self.doc.mark_meta_dirty()
        else:
            self._mark_xml_dirty()
        self.scene.refresh_item_positions()
        item = self.scene.item_for_node(node)
        if item:
            item.refresh_look()

    def _on_props_editing_finished(self) -> None:
        if self._updating_props:
            return
        # Revert field text to last good values if still invalid
        if self._doc_mode == DOC_MODE_ATLAS:
            region = self.descr_board.scene.selected_region()
            if region is not None and self._parse_geometry_fields() is None:
                self._updating_props = True
                self.edit_x.setText(_num(region.x))
                self.edit_y.setText(_num(region.y))
                self.edit_w.setText(_num(region.width))
                self.edit_h.setText(_num(region.height))
                self._updating_props = False
                self._set_geo_field_styles(True)
            self._commit_props_geo_undo()
            return
        selected = [i for i in self.scene.selectedItems() if hasattr(i, "node")]
        if selected and self._parse_geometry_fields() is None:
            node: LayoutNode = selected[0].node
            self._updating_props = True
            self.edit_x.setText(_num(node.x))
            self.edit_y.setText(_num(node.y))
            self.edit_w.setText(_num(node.width))
            self.edit_h.setText(_num(node.height))
            self._updating_props = False
            self._set_geo_field_styles(True)
        self._commit_props_geo_undo()

    def _browse_text(self) -> None:
        if self._updating_props:
            return
        selected = [i for i in self.scene.selectedItems() if hasattr(i, "node")]
        if not selected:
            return
        node: LayoutNode = selected[0].node
        if node.from_meta or not node.is_drawable:
            return
        current = ""
        if node.text and node.text.content:
            current = node.text.content.strip()
        dlg = StringPickerDialog(
            self.strings,
            self,
            current_id=current,
            anomaly_root=str(self.settings.get("anomaly_root") or ""),
            gamma_root=str(self.settings.get("gamma_root") or ""),
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        entry = dlg.selected_entry()
        if entry is None:
            return
        before = node.capture_prop_state()
        if not node.assign_text(entry.string_id):
            return
        self.strings.remember_string(entry)
        if not self._commit_prop_change(node, before):
            return
        self.statusBar().showMessage(f"Text → {entry.string_id}")
        self._log("info", f"text {node.path}: {entry.string_id}")

    def _clear_text(self) -> None:
        if self._updating_props:
            return
        selected = [i for i in self.scene.selectedItems() if hasattr(i, "node")]
        if not selected:
            return
        node: LayoutNode = selected[0].node
        if node.from_meta or not node.is_drawable or node.text is None:
            return
        before = node.capture_prop_state()
        if not node.clear_text():
            return
        if not self._commit_prop_change(node, before):
            return
        self.statusBar().showMessage("Text cleared")
        self._log("info", f"text {node.path}: cleared")

    def _on_stretch_toggled(self, checked: bool) -> None:
        if self._updating_props:
            return
        node = self._selected_prop_node()
        if node is None:
            return
        before = node.capture_prop_state()
        if not node.apply_widget_props(stretch=checked):
            return
        self._push_prop_edit(before, node.capture_prop_state())
        self._mark_xml_dirty()
        self._refresh_selected_item(node)

    def _on_text_enum_changed(self, *_args) -> None:
        if self._updating_props:
            return
        node = self._selected_prop_node()
        if node is None:
            return
        font = self._combo_data(self.edit_font)
        align = self._combo_data(self.edit_align)
        vert = self._combo_data(self.edit_vert_align)
        before = node.capture_prop_state()
        if not node.apply_text_props(font=font, align=align, vert_align=vert):
            return
        if font:
            self.fonts.resolve_font(font)
        self._push_prop_edit(before, node.capture_prop_state())
        self._mark_xml_dirty()
        self._refresh_selected_item(node)

    def _on_text_font_edited(self) -> None:
        if self._updating_props:
            return
        # Sync editable text into a matching item when possible.
        typed = self.edit_font.currentText().strip()
        idx = self.edit_font.findData(typed)
        if idx >= 0:
            self.edit_font.setCurrentIndex(idx)
        else:
            self._on_text_enum_changed()

    def _on_text_flag_toggled(self, checked: bool) -> None:
        if self._updating_props:
            return
        node = self._selected_prop_node()
        if node is None:
            return
        before = node.capture_prop_state()
        if not node.apply_text_props(complex_mode=checked):
            return
        self._push_prop_edit(before, node.capture_prop_state())
        self._mark_xml_dirty()
        self._refresh_selected_item(node)

    def _on_scroll_flag_toggled(self, checked: bool) -> None:
        if self._updating_props:
            return
        node = self._selected_prop_node()
        if node is None:
            return
        before = node.capture_prop_state()
        if not node.apply_widget_props(always_show_scroll=checked):
            return
        self._push_prop_edit(before, node.capture_prop_state())
        self._mark_xml_dirty()

    def _on_extra_props_editing_finished(self) -> None:
        if self._updating_props:
            return
        node = self._selected_prop_node()
        if node is None:
            return
        before = node.capture_prop_state()
        changed = False
        color_kwargs: dict = {}
        for key, ed in (
            ("r", self.edit_text_r),
            ("g", self.edit_text_g),
            ("b", self.edit_text_b),
            ("a", self.edit_text_a),
        ):
            parsed = self._parse_optional_int(ed.text())
            if parsed is ...:
                continue
            color_kwargs[key] = parsed
        off_kwargs: dict = {}
        for key, ed in (("x", self.edit_text_x), ("y", self.edit_text_y)):
            parsed = self._parse_optional_float(ed.text())
            if parsed is ...:
                continue
            off_kwargs[key] = parsed
        if color_kwargs or off_kwargs:
            changed |= node.apply_text_props(**color_kwargs, **off_kwargs)
        scroll_kwargs: dict = {}
        for key, ed in (
            ("left_ident", self.edit_left_ident),
            ("right_ident", self.edit_right_ident),
            ("top_indent", self.edit_top_indent),
            ("bottom_indent", self.edit_bottom_indent),
            ("vert_interval", self.edit_vert_interval),
        ):
            parsed = self._parse_optional_float(ed.text())
            if parsed is ...:
                continue
            scroll_kwargs[key] = parsed
        if scroll_kwargs:
            changed |= node.apply_widget_props(**scroll_kwargs)
        if not changed:
            return
        self._push_prop_edit(before, node.capture_prop_state())
        self._mark_xml_dirty()
        self._refresh_selected_item(node)

    def _on_show_texture_toggled(self, checked: bool) -> None:
        if self._updating_props:
            return
        selected = [i for i in self.scene.selectedItems() if hasattr(i, "node")]
        if not selected:
            return
        item = selected[0]
        if hasattr(item, "set_show_texture"):
            item.set_show_texture(checked)

    def _restore_window_geometry(self) -> None:
        geo = self.settings.get("window")
        if not isinstance(geo, dict):
            geo = {}
        w = int(geo.get("width") or 1400)
        h = int(geo.get("height") or 900)
        self.resize(max(640, w), max(480, h))
        x, y = geo.get("x"), geo.get("y")
        if x is not None and y is not None:
            self.move(int(x), int(y))
        if geo.get("maximized"):
            self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)

    def _save_window_geometry(self) -> None:
        maximized = self.isMaximized()
        # Use normalGeometry so maximize doesn't overwrite the restored size
        rect = self.normalGeometry() if maximized else self.geometry()
        self.settings["window"] = {
            "x": int(rect.x()),
            "y": int(rect.y()),
            "width": int(rect.width()),
            "height": int(rect.height()),
            "maximized": bool(maximized),
        }
        save_settings(self.settings)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._closing = True
        # Drop thumb workers so cache/ files aren't locked after quit.
        if getattr(self, "resolver", None) is not None:
            self.resolver.abort_picker_thumbs()
        self._capture_session_meta()
        if self._has_unsaved_changes():
            r = QMessageBox.question(
                self,
                "Unsaved changes",
                "Save before quitting?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if r == QMessageBox.StandardButton.Cancel:
                self._closing = False
                event.ignore()
                return
            if r == QMessageBox.StandardButton.Save and not self.save():
                self._closing = False
                event.ignore()
                return
        self._save_splitter_sizes()
        self._save_window_geometry()
        event.accept()


def _num(v: float) -> str:
    if abs(v - round(v)) < 1e-6:
        return str(int(round(v)))
    return f"{v:g}"


def _app_icon() -> QIcon:
    icon_path = Path(__file__).resolve().parent / "assets" / "sage.png"
    return QIcon(str(icon_path)) if icon_path.is_file() else QIcon()


class _UndoHistorySeparator(QWidget):
    """Single-line dashed rule that always spans the list viewport width."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(14)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        pen = QPen(QColor(90, 90, 98))
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setWidth(1)
        painter.setPen(pen)
        y = self.height() // 2
        painter.drawLine(0, y, self.width(), y)


_DARK_BG = QColor(18, 18, 20)
_DARK_PANEL = QColor(30, 30, 34)
_DARK_BASE = QColor(30, 30, 34)
_DARK_TEXT = QColor(212, 212, 212)
_DARK_DISABLED = QColor(120, 120, 128)
_DARK_HIGHLIGHT = QColor(38, 79, 120)
_DARK_MID = QColor(50, 50, 56)
# Undo list tones (muted on dark panel).
_UNDO_TONE_COLORS = {
    "add": QColor(129, 179, 129),  # soft green — assign / create
    "remove": QColor(196, 120, 120),  # soft red — delete / clear
    "change": QColor(196, 176, 110),  # soft amber — value / resize
    "move": QColor(130, 170, 210),  # soft blue — position
}


def _dark_palette() -> QPalette:
    pal = QPalette()
    bg, panel, base, text = _DARK_BG, _DARK_PANEL, _DARK_BASE, _DARK_TEXT
    disabled, highlight, mid = _DARK_DISABLED, _DARK_HIGHLIGHT, _DARK_MID
    pal.setColor(QPalette.ColorRole.Window, bg)
    pal.setColor(QPalette.ColorRole.WindowText, text)
    pal.setColor(QPalette.ColorRole.Base, base)
    pal.setColor(QPalette.ColorRole.AlternateBase, panel)
    pal.setColor(QPalette.ColorRole.Text, text)
    pal.setColor(QPalette.ColorRole.Button, panel)
    pal.setColor(QPalette.ColorRole.ButtonText, text)
    pal.setColor(QPalette.ColorRole.ToolTipBase, panel)
    pal.setColor(QPalette.ColorRole.ToolTipText, text)
    pal.setColor(QPalette.ColorRole.PlaceholderText, disabled)
    pal.setColor(QPalette.ColorRole.BrightText, QColor(255, 80, 80))
    pal.setColor(QPalette.ColorRole.Highlight, highlight)
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    pal.setColor(QPalette.ColorRole.Link, QColor(100, 180, 255))
    pal.setColor(QPalette.ColorRole.Light, mid)
    pal.setColor(QPalette.ColorRole.Mid, mid)
    pal.setColor(QPalette.ColorRole.Dark, bg)
    pal.setColor(QPalette.ColorRole.Shadow, QColor(0, 0, 0))
    for group in (QPalette.ColorGroup.Disabled, QPalette.ColorGroup.Inactive):
        pal.setColor(group, QPalette.ColorRole.WindowText, disabled)
        pal.setColor(group, QPalette.ColorRole.Text, disabled)
        pal.setColor(group, QPalette.ColorRole.ButtonText, disabled)
        pal.setColor(group, QPalette.ColorRole.Highlight, QColor(55, 55, 60))
        pal.setColor(group, QPalette.ColorRole.HighlightedText, disabled)
    return pal


def _apply_windows_dark_titlebar(widget: QWidget) -> None:
    """Ask DWM for a dark title bar so the frame isn't bright on first show."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = int(widget.winId())
        value = ctypes.c_int(1)
        # 20 = DWMWA_USE_IMMERSIVE_DARK_MODE (Win10 1903+); 19 was the older name.
        for attr in (20, 19):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)
            )
    except Exception:
        pass


def _ui_font() -> QFont:
    """App-wide UI typeface: Consolas with monospace fallbacks."""
    font = QFont("Consolas")
    font.setFamilies(["Consolas", "Cascadia Mono", "Courier New"])
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPointSize(9)
    return font


def _checkbox_check_image_url() -> str:
    """Dark-green tick PNG for checked indicators (stylesheet ``image:``)."""
    from .cache_store import cache_dir

    dest = cache_dir() / "ui" / "checkbox_check.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    pix = QPixmap(14, 14)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#2D6A4F"))
    pen.setWidthF(2.2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.drawLine(3, 7, 6, 10)
    painter.drawLine(6, 10, 11, 3)
    painter.end()
    pix.save(str(dest), "PNG")
    # Qt stylesheets want forward slashes.
    return dest.resolve().as_posix()


def _apply_dark_theme(app: QApplication) -> None:
    """Force dark chrome before the first paint (Windows otherwise flashes white)."""
    app.setStyle("Fusion")
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    except Exception:
        pass
    pal = _dark_palette()
    app.setPalette(pal)
    app.setFont(_ui_font())
    check_img = _checkbox_check_image_url()
    # Broad stylesheet so child widgets don't stay on the system light look.
    app.setStyleSheet(
        "* {"
        "  color: #D4D4D4;"
        "  font-family: Consolas, 'Cascadia Mono', 'Courier New', monospace;"
        "}"
        "QMainWindow, QDialog, QWidget, QSplitter, QScrollArea, QFrame, QTabWidget {"
        "  background-color: #121214; color: #D4D4D4;"
        "}"
        "QToolTip { color: #D4D4D4; background-color: #2A2A30; border: 1px solid #555; }"
        "QMenuBar { background-color: #121214; color: #D4D4D4; }"
        "QMenuBar::item:selected { background-color: #264F78; }"
        "QMenu { background-color: #1E1E22; color: #D4D4D4; }"
        "QMenu::item:selected { background-color: #264F78; }"
        "QStatusBar { background-color: #121214; color: #D4D4D4; }"
        "QWidget#optionsBar {"
        "  background-color: #161618;"
        "  border-bottom: 1px solid #3A3A40;"
        "}"
        "QTabWidget::pane { border: 1px solid #3A3A40; top: -1px; background: #121214; }"
        "QTabBar::tab {"
        "  background: #1E1E22; color: #D4D4D4; padding: 6px 12px;"
        "  font-size: 10pt; font-weight: bold;"
        "}"
        "QTabBar::tab:selected { background: #2A2A30; }"
        "QHeaderView::section { background-color: #1E1E22; color: #D4D4D4; "
        "  padding: 4px; border: 1px solid #3A3A40; }"
        "QSplitter::handle { background-color: #2A2A30; }"
        "QLineEdit, QSpinBox, QPlainTextEdit, QTextEdit, QListWidget, QComboBox {"
        "  background-color: #1E1E22; color: #D4D4D4; border: 1px solid #3A3A40; "
        "  selection-background-color: #264F78;"
        "}"
        # GroupBox fill matches panel QWidget so padded prop rows aren't stripes.
        "QGroupBox {"
        "  background-color: #121214;"
        "  border: 1px solid #3A3A40;"
        "  border-radius: 4px;"
        "  margin-top: 10px;"
        "  padding-top: 8px;"
        "  padding-left: 5px;"
        "  padding-right: 5px;"
        "  padding-bottom: 5px;"
        "  color: #D4D4D4;"
        "}"
        "QGroupBox::title {"
        "  subcontrol-origin: margin;"
        "  subcontrol-position: top left;"
        "  left: 8px;"
        "  padding: 0 4px;"
        "  color: #D4D4D4;"
        "  font-weight: bold;"
        "}"
        "QCheckBox, QLabel { background: transparent; color: #D4D4D4; }"
        f"QCheckBox:disabled, QLabel:disabled {{ color: {_GREY_INAPPLICABLE}; }}"
        "QLineEdit:disabled, QComboBox:disabled, QComboBox:disabled QAbstractItemView {"
        f"  color: {_GREY_INAPPLICABLE};"
        "}"
        "QCheckBox::indicator, QListWidget::indicator, QTreeWidget::indicator {"
        "  width: 14px; height: 14px;"
        "  border: 1px solid #3A3A40;"
        "  border-radius: 2px;"
        "  background-color: #0E0E10;"
        "}"
        "QCheckBox::indicator:hover, QListWidget::indicator:hover,"
        "QTreeWidget::indicator:hover {"
        "  background-color: #161618; border-color: #55555C;"
        "}"
        "QCheckBox::indicator:disabled {"
        "  background-color: #0A0A0C; border-color: #2A2A30;"
        "}"
        "QCheckBox::indicator:checked, QListWidget::indicator:checked,"
        "QTreeWidget::indicator:checked {"
        "  background-color: #0E0E10;"
        "  border-color: #3A3A40;"
        f"  image: url({check_img});"
        "}"
        "QCheckBox::indicator:checked:hover, QListWidget::indicator:checked:hover,"
        "QTreeWidget::indicator:checked:hover {"
        "  background-color: #161618; border-color: #55555C;"
        f"  image: url({check_img});"
        "}"
        "QCheckBox::indicator:checked:disabled {"
        "  background-color: #0A0A0C; border-color: #2A2A30;"
        "  image: none;"
        "}"
        "QScrollBar:vertical { background: #121214; width: 12px; }"
        "QScrollBar:horizontal { background: #121214; height: 12px; }"
        "QScrollBar::handle { background: #3A3A40; border-radius: 4px; min-height: 24px; }"
        "QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }"
        "QMessageBox { background-color: #121214; }"
        "QPushButton {"
        "  background-color: #2A2A30; color: #D4D4D4; border: 1px solid #3A3A40; "
        "  padding: 4px 12px;"
        "}"
        "QPushButton:hover { background-color: #3A3A40; }"
        "QPushButton:pressed { background-color: #264F78; }"
    )


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    argv = list(sys.argv if argv is None else argv)
    _log_file.info("main() argv=%s", argv)
    # Before the first widget exists so the HWND isn't created light.
    QApplication.setStyle("Fusion")
    app = QApplication(argv)
    _apply_dark_theme(app)
    app.setApplicationName("D.O.G.M.A. Stalker Anomaly Gui Editor")
    icon = _app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    # Setup until Anomaly + GAMMA roots validate (detect only prefills the dialog).
    settings = load_settings()
    ran_setup = False
    if not installs_configured(settings):
        _log_file.info("install roots missing/invalid — showing SAGE Setup")
        setup = InstallRootsDialog(settings, None, setup_mode=True)
        if not icon.isNull():
            setup.setWindowIcon(icon)
        if setup.exec() != QDialog.DialogCode.Accepted:
            _log_file.info("setup cancelled — exit")
            return 1
        settings = setup.result_settings()
        save_settings(settings)
        ran_setup = True
        _log_file.info(
            "setup saved anomaly=%s gamma=%s",
            settings.get("anomaly_root"),
            settings.get("gamma_root"),
        )

    # Roots already saved but unpack still missing (setup was skipped).
    if not ran_setup:
        need = check_anomaly_unpack_needed(settings.get("anomaly_root"))
        if need.needed:
            _log_file.info("Anomaly unpack needed — showing setup for confirm")
            setup = InstallRootsDialog(settings, None, setup_mode=True)
            if not icon.isNull():
                setup.setWindowIcon(icon)
            if setup.exec() != QDialog.DialogCode.Accepted:
                _log_file.info("unpack setup cancelled — exit")
                return 1
            settings = setup.result_settings()
            save_settings(settings)

    initial = Path(argv[1]) if len(argv) > 1 else None
    _log_file.info("creating MainWindow initial=%s", initial)
    win = MainWindow(initial)
    if not icon.isNull():
        win.setWindowIcon(icon)
    # Opaque dark fill before show — avoids the white first frame on Windows.
    win.setAutoFillBackground(True)
    win.setPalette(_dark_palette())
    win.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    win.show()
    _apply_windows_dark_titlebar(win)
    app.processEvents()
    # Dark shell first; chooser (or CLI path) then Loading then workspace.
    QTimer.singleShot(0, win._open_startup_file)
    _log_file.info("entering app.exec()")
    code = app.exec()
    _log_file.info("app.exec() returned %s", code)
    return code
