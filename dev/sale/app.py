"""SALE — Stalker Anomaly Loadout Editor main window."""

from __future__ import annotations

import shutil
import sys
import time
import traceback
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QThread, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QFont,
    QIcon,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
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
    QSlider,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .balance import (
    ammo_enabled_map,
    clear_override,
    effective_category,
    get_item_ltx_override,
    is_ammo_family_enabled,
    is_overridden,
    item_in_ltx,
    load_balance,
    override_count,
    save_balance,
    set_ammo_family_enabled,
    set_ceiling,
    set_curve,
    set_override,
    toggle_item_ltx_override,
)
from .diaglog import LOG_PATH, attach_log_view, get_logger, setup_logging
from .export_ltx import default_export_path, export_shop_ltx
from .labels import pretty_ceiling_tip, pretty_label, pretty_tip
from .regenerate import load_items, regenerate
from .kind_limits import (
    MAX_ITEM_LIMIT,
    MAX_KIND_LIMIT,
    item_limit_for,
    kind_label,
    kind_limit_for,
    present_weapon_kinds,
    select_pool_for_ltx,
    select_weapons_for_ltx,
    set_item_limit,
    set_kind_limit,
    weapon_kind,
)
from .spawn_filter import (
    has_attached_scope,
    has_attached_silencer,
    is_explosive_weapon,
    is_gauss_weapon,
    name_blocked,
)
from .score import (
    ARMOR_WEIGHTS,
    CURVE_IDS,
    CURVE_LABELS,
    CURVE_LINEAR,
    FACTIONS,
    WEAPON_WEIGHTS,
    NO_CEILING_STATS,
    ammo_bloc_label,
    ammo_bloc_tag,
    ammo_family,
    ammo_family_bloc,
    ammo_section_label,
    armor_pts,
    armor_stat_terms,
    collect_ammo_sections,
    sort_ammo_sections_by_family_price,
    default_ceilings_armor,
    faction_label,
    hit_power_pct,
    normalize_curve,
    section_name_faction,
    section_name_faction_token,
    weapon_ammo_allowed,
    weapon_name_faction_ok,
    weapon_pts,
    weapon_stat_terms,
)
from .settings import (
    CACHE,
    THUMBS_DIR,
    get_deploy_target,
    load_settings,
    save_settings,
    set_deploy_target,
)

log = get_logger("app")

CATS = ("weapons", "outfits", "helmets")
# Item tile; cell is icon + half the previous inter-box gutter (was +24×+30).
# Display size in the icon grid (must fit inside GRID_CELL_*).
# Display size; cached weapon/ammo thumbs are 4× inv_grid, armor 2× — scaled to fit.
GRID_ICON_W = 200
GRID_ICON_H = 100
GRID_CELL_W = 212
GRID_CELL_H = 115
# Per-item LTX override checkbox (bottom-right): empty=auto, filled=force.
_ITEM_CB_SIZE = 16
_ITEM_CB_MARGIN = 4
# Click target larger than the drawn box (IconMode hit-tests are fiddly).
_ITEM_CB_HIT = 36

# Stat name colors (CSS) by key family.
_DPS_KEYS = ("dps",)
_DMG_KEYS = (
    "dmg",
    "ap",
    "penetr",
    "wound",
    "burn",
    "shock",
    "chemical",
    "radiation",
    "telepatic",
    "explosion",
    "fire_wound",
    "strike",
)
_FIRE_KEYS = ("rpm", "mag", "reload", "burst", "fire_mode", "ammo")
_HANDLE_KEYS = ("spread", "recoil", "disp", "accuracy", "inert", "cam")
_ECON_KEYS = ("cost", "price", "weight", "condition")


def _stat_color(key: str) -> str:
    k = key.lower()
    if "hit_power" in k:
        return "#3d8b5a"  # dark green
    # DPS before Dmg — "*_dps" must not pick up the Dmg purple.
    if any(p in k for p in _DPS_KEYS):
        return "#ff7a45"  # bright orange (DPS)
    if any(p in k for p in _DMG_KEYS):
        return "#c39bd3"  # purple (Dmg)
    if any(p in k for p in _FIRE_KEYS):
        return "#5ec8ff"  # cyan
    if any(p in k for p in _HANDLE_KEYS):
        return "#e6c35c"  # gold
    if any(p in k for p in _ECON_KEYS):
        return "#7dcea0"  # green
    if k.startswith("a_") or "protect" in k:
        return "#c39bd3"  # purple (armor)
    return "#aab2bf"  # muted


def _n01_color(n01: float) -> str:
    """Lerp muted red (0) → bright green (1) for normalized Final values."""
    t = max(0.0, min(1.0, float(n01)))
    r0, g0, b0 = 0x8A, 0x4A, 0x4A
    r1, g1, b1 = 0x3D, 0xDC, 0x6E
    r = int(round(r0 + (r1 - r0) * t))
    g = int(round(g0 + (g1 - g0) * t))
    b = int(round(b0 + (b1 - b0) * t))
    return f"#{r:02x}{g:02x}{b:02x}"


def _signed_diff_norm(primary: float, compare: float) -> float:
    """Map primary−compare into [−1, 1] relative to the larger magnitude."""
    d = float(primary) - float(compare)
    scale = max(abs(float(primary)), abs(float(compare)), abs(d), 1e-9)
    return max(-1.0, min(1.0, d / scale))


def _signed_diff_color(n: float) -> str:
    """Lerp red (−1) → white (0) → green (+1) for compare Diff."""
    t = max(-1.0, min(1.0, float(n)))
    wr, wg, wb = 0xF0, 0xF0, 0xF0
    if t <= 0:
        r0, g0, b0 = 0xFF, 0x4A, 0x4A
        u = t + 1.0  # −1→0, 0→1
        r = int(round(r0 + (wr - r0) * u))
        g = int(round(g0 + (wg - g0) * u))
        b = int(round(b0 + (wb - b0) * u))
    else:
        r1, g1, b1 = 0x3D, 0xE0, 0x6E
        r = int(round(wr + (r1 - wr) * t))
        g = int(round(wg + (g1 - wg) * t))
        b = int(round(wb + (b1 - wb) * t))
    return f"#{r:02x}{g:02x}{b:02x}"


_HEADER_FONT = "font-size: 13px; font-weight: 700;"
_HEADER_LABEL_STYLE = f"QLabel {{ {_HEADER_FONT} color: #d0d0d0; }}"
_TAB_STYLE = (
    "QTabBar::tab { color: #bbb; padding: 5px 12px; background: #2a2a2a; "
    "border: 1px solid #333; margin-right: 2px; }"
    "QTabBar::tab:selected { color: #fff; background: #4a4a4a; }"
)
_TABLE_STYLE = (
    "QTableWidget { background: #1e1e1e; color: #e8e8e8; border: none; "
    "font-family: Consolas, monospace; font-size: 12px; "
    "gridline-color: #2a2a2a; alternate-background-color: #242424; "
    "outline: none; }"
    "QTableWidget::item:selected { background: transparent; }"
    "QTableWidget::item:hover { background: transparent; }"
    f"QHeaderView::section {{ background: #2a2a2a; color: #ddd; border: none; "
    f"padding: 5px 8px; {_HEADER_FONT} text-align: left; }}"
)
_SORT_NAME_BG = QColor(48, 78, 118)
# Stats excluded from scoring (weight 0) — Weight / Final columns.
_STAT_EXCLUDED_FG = "#555555"
_CLEAR_BTN_STYLE = (
    "QPushButton {"
    "  color: #d0d0d0; background: #2a2a2a; border: 1px solid #444;"
    "  padding: 0; font-weight: 700; font-size: 12px;"
    "}"
    "QPushButton:hover { color: #fff; border-color: #666; }"
    "QPushButton:disabled { color: #555; background: #1e1e1e; border-color: #333; }"
)


def _header_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(_HEADER_LABEL_STYLE)
    return lbl


def _nice_item_name(sec: str, entry: dict[str, Any] | None = None) -> str:
    """Friendly name for detail/grid: resolved inv_name text, else cleaned section id."""
    raw = str((entry or {}).get("name") or "").strip()
    sec_l = (sec or "").lower()
    # Skip unresolved string-table keys / bare section ids.
    unresolved = (
        not raw
        or raw.lower() == sec_l
        or raw.startswith("st_")
        or raw.endswith("_name")
    )
    if not unresolved:
        return raw
    short = sec or ""
    for prefix in ("wpn_", "outfit_", "helm_", "helmet_"):
        if short.startswith(prefix):
            short = short[len(prefix) :]
            break
    return short.replace("_", " ") or sec or "?"


def _fmt_stat_val(val: Any) -> str:
    if val is None:
        return "—"
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, int):
        return str(val)
    if isinstance(val, float):
        if abs(val - round(val)) < 1e-9:
            return str(int(round(val)))
        return f"{val:.2f}"
    if isinstance(val, (list, tuple)):
        return ", ".join(_fmt_stat_val(x) for x in val)
    return str(val)


def _item_checkbox_rect(width: int = GRID_ICON_W, height: int = GRID_ICON_H) -> QRect:
    """Checkbox bounds in icon-local coordinates (bottom-right)."""
    s = _ITEM_CB_SIZE
    m = _ITEM_CB_MARGIN
    return QRect(width - m - s, height - m - s, s, s)


def _icon_with_pts(
    thumb: str | None,
    pts: int,
    *,
    in_shop: bool,
    faction_blocked: bool = False,
    under_pts: bool = True,
    selected: bool | str = False,
    name: str = "",
    faction_tag: str = "",
    in_ltx: bool = True,
    override: str | None = None,
    width: int = GRID_ICON_W,
    height: int = GRID_ICON_H,
) -> QIcon:
    canvas = QPixmap(width, height)
    # selected: True/"primary" = blue; "compare" = red; else default.
    sel = "primary" if selected is True else (selected or "")
    if sel == "primary":
        canvas.fill(QColor(36, 72, 120))
    elif sel == "compare":
        canvas.fill(QColor(110, 40, 40))
    elif in_ltx:
        canvas.fill(QColor(28, 30, 32))
    elif under_pts and faction_blocked:
        canvas.fill(QColor(28, 20, 20))
    elif under_pts:
        canvas.fill(QColor(24, 22, 20))
    else:
        canvas.fill(QColor(22, 22, 22))
    painter = QPainter(canvas)
    try:
        if thumb and Path(thumb).is_file():
            pix = QPixmap(thumb)
            if not pix.isNull():
                scaled = pix.scaled(
                    width - 10,
                    height - 10,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                x = (width - scaled.width()) // 2
                y = (height - scaled.height()) // 2
                painter.drawPixmap(x, y, scaled)
        # Below pts: green (in LTX), muted red (faction/ammo block), else orange.
        # Over pts threshold: grey (unless force-included → green via in_ltx).
        if in_ltx:
            border = QColor(40, 120, 70)
            pts_color = QColor(90, 220, 120)
        elif under_pts and faction_blocked:
            border = QColor(100, 58, 58)
            pts_color = QColor(150, 100, 100)
        elif under_pts:
            border = QColor(100, 82, 58)
            pts_color = QColor(150, 128, 100)
        else:
            border = QColor(55, 55, 55)
            pts_color = QColor(140, 140, 140)
        painter.setPen(border)
        painter.drawRect(0, 0, width - 1, height - 1)
        pts_s = str(int(pts))
        font = QFont("Consolas", 10)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        tw = metrics.horizontalAdvance(pts_s) + 8
        th = metrics.height() + 2
        painter.fillRect(3, 3, tw, th, QColor(0, 0, 0, 180))
        painter.setPen(pts_color)
        painter.drawText(7, 3 + metrics.ascent(), pts_s)
        tag = (faction_tag or "").strip()
        if tag:
            tag_font = QFont("Consolas", 8)
            tag_font.setBold(True)
            painter.setFont(tag_font)
            tm = painter.fontMetrics()
            tag_w = tm.horizontalAdvance(tag) + 8
            tag_h = tm.height() + 2
            tag_x = width - tag_w - 3
            painter.fillRect(tag_x, 3, tag_w, tag_h, QColor(0, 0, 0, 180))
            # Mute faction tag when the gun is not in the LTX set.
            painter.setPen(
                QColor(200, 185, 140) if in_ltx else QColor(110, 105, 90)
            )
            painter.drawText(tag_x + 4, 3 + tm.ascent(), tag)
        if name:
            name_font = QFont("Consolas", 8)
            painter.setFont(name_font)
            nm = painter.fontMetrics()
            max_w = max(40, width - _ITEM_CB_SIZE - _ITEM_CB_MARGIN * 2 - 8)
            text = nm.elidedText(name, Qt.TextElideMode.ElideRight, max_w)
            y = height - 4
            painter.setPen(QColor(230, 230, 230))
            painter.drawText(4, y, text)
        # Override checkbox: empty = auto; filled = force include/exclude.
        cb = _item_checkbox_rect(width, height)
        painter.fillRect(cb, QColor(0, 0, 0, 200))
        forced = override in ("include", "exclude")
        painter.setPen(QColor(120, 160, 120) if in_ltx else QColor(90, 90, 90))
        painter.drawRect(cb.adjusted(0, 0, -1, -1))
        if forced:
            x0, y0 = cb.x(), cb.y()
            s = cb.width()
            if override == "include":
                pen = QPen(QColor(90, 200, 120))
                pen.setWidthF(1.8)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.drawLine(x0 + 3, y0 + s // 2, x0 + s // 2 - 1, y0 + s - 4)
                painter.drawLine(x0 + s // 2 - 1, y0 + s - 4, x0 + s - 3, y0 + 3)
            else:
                pen = QPen(QColor(180, 180, 180))
                pen.setWidthF(1.6)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.drawLine(x0 + 3, y0 + 3, x0 + s - 3, y0 + s - 3)
                painter.drawLine(x0 + s - 3, y0 + 3, x0 + 3, y0 + s - 3)
    finally:
        painter.end()
    return QIcon(canvas)


class RegenWorker(QThread):
    progress = pyqtSignal(str, int, int)
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, anomaly: Path | None, gamma: Path | None) -> None:
        super().__init__()
        self.anomaly = anomaly
        self.gamma = gamma

    def run(self) -> None:
        log.info(
            "RegenWorker start anomaly=%s gamma=%s",
            self.anomaly,
            self.gamma,
        )
        t0 = time.perf_counter()
        try:
            path = regenerate(
                self.anomaly,
                self.gamma,
                progress=lambda m, c, t: self.progress.emit(m, c, t),
            )
            log.info(
                "RegenWorker ok in %.2fs -> %s",
                time.perf_counter() - t0,
                path,
            )
            self.finished_ok.emit(str(path))
        except Exception as exc:  # noqa: BLE001
            log.exception("RegenWorker failed after %.2fs", time.perf_counter() - t0)
            self.failed.emit(f"{exc}\n\nSee log:\n{LOG_PATH}")


# Input events eaten while the busy overlay is up (app-wide filter).
_BUSY_BLOCK_EVENTS = frozenset(
    {
        QEvent.Type.MouseButtonPress,
        QEvent.Type.MouseButtonRelease,
        QEvent.Type.MouseButtonDblClick,
        QEvent.Type.MouseMove,
        QEvent.Type.Wheel,
        QEvent.Type.KeyPress,
        QEvent.Type.KeyRelease,
        QEvent.Type.ShortcutOverride,
        QEvent.Type.Shortcut,
        QEvent.Type.ContextMenu,
        QEvent.Type.Enter,
        QEvent.Type.Leave,
        QEvent.Type.HoverEnter,
        QEvent.Type.HoverLeave,
        QEvent.Type.HoverMove,
        QEvent.Type.DragEnter,
        QEvent.Type.DragMove,
        QEvent.Type.Drop,
        QEvent.Type.TouchBegin,
        QEvent.Type.TouchUpdate,
        QEvent.Type.TouchEnd,
        QEvent.Type.Gesture,
    }
)


class BusyOverlayHost(QWidget):
    """Hosts a child widget with a darker grey overlay that blocks input while busy.

    Does **not** call setEnabled(False) on content — that can make sliders emit
    valueChanged(min) and corrupt Scale max / weights. Instead an app-wide event
    filter discards all input for the duration.
    """

    def __init__(self, child: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._content = child
        self._busy = False
        self._app_filter = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(child)
        self.overlay = QWidget(self)
        self.overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.overlay.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Darker grey ~70% alpha.
        self.overlay.setStyleSheet("background-color: rgba(24, 24, 24, 180);")
        self.overlay.hide()
        self.overlay.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.overlay.setGeometry(self.rect())

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if self._busy and event.type() in _BUSY_BLOCK_EVENTS:
            return True
        return super().eventFilter(obj, event)

    def set_busy(self, busy: bool) -> None:
        busy = bool(busy)
        was = self._busy
        self._busy = busy
        self.overlay.setVisible(busy)
        app = QApplication.instance()
        if busy:
            self.overlay.setGeometry(self.rect())
            self.overlay.raise_()
            self.overlay.setFocus(Qt.FocusReason.OtherFocusReason)
            if app is not None and not self._app_filter:
                app.installEventFilter(self)
                self._app_filter = True
        else:
            if app is not None and self._app_filter:
                app.removeEventFilter(self)
                self._app_filter = False
            # Drop any focus acquired during busy so widgets don't keep a stuck grab.
            if was and self.overlay.hasFocus():
                self.clearFocus()


class FineStepSlider(QSlider):
    """Horizontal slider whose wheel moves one singleStep and doesn't scroll parents."""

    def wheelEvent(self, event) -> None:  # noqa: N802
        # Busy overlay uses an app filter; belt-and-suspenders if that is bypassed.
        host = self.window()
        ui = getattr(host, "ui_host", None)
        if ui is not None and getattr(ui, "_busy", False):
            event.accept()
            return
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta == 0:
            event.ignore()
            return
        step = max(1, int(self.singleStep()))
        # Reversed vs Qt default: scroll up = decrease, scroll down = increase.
        if delta > 0:
            self.setValue(max(self.minimum(), self.value() - step))
        else:
            self.setValue(min(self.maximum(), self.value() + step))
        event.accept()


def _curve_picker_icon(curve: str, size: int = 16) -> QIcon:
    """Tiny polyline icon: linear / ease-in (exp) / ease-out (log)."""
    c = normalize_curve(curve)
    pix = QPixmap(size, size)
    pix.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pix)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(190, 210, 230))
        pen.setWidthF(1.6)
        painter.setPen(pen)
        m = 2.0
        w = size - 2 * m
        h = size - 2 * m
        pts = []
        steps = 10
        for i in range(steps + 1):
            t = i / steps
            if c == "exp":
                y01 = t * t
            elif c == "log":
                u = 1.0 - t
                y01 = 1.0 - u * u
            else:
                y01 = t
            pts.append((m + t * w, m + (1.0 - y01) * h))
        for i in range(len(pts) - 1):
            painter.drawLine(
                int(pts[i][0]),
                int(pts[i][1]),
                int(pts[i + 1][0]),
                int(pts[i + 1][1]),
            )
    finally:
        painter.end()
    return QIcon(pix)


class WeightRow(QWidget):
    changed = pyqtSignal(str, float, bool)  # key, value, is_weight
    cleared = pyqtSignal(str, bool)
    curve_changed = pyqtSignal(str, str)  # key, curve id
    drag_started = pyqtSignal()
    drag_ended = pyqtSignal()

    def __init__(
        self,
        key: str,
        label: str,
        value: float,
        *,
        is_weight: bool,
        overridden: bool,
        tip: str = "",
        kind: str | None = None,
        editable: bool = True,
        show_clear: bool = True,
        clearable: bool = True,
        slider_max: int | None = None,
        show_curve: bool = False,
        curve: str = CURVE_LINEAR,
    ) -> None:
        super().__init__()
        self.key = key
        self.is_weight = is_weight
        self.clearable = bool(clearable)
        self.curve = normalize_curve(curve)
        # kind: weight | int | float (float uses 0.01 steps via ×100)
        if kind:
            self.kind = kind
        else:
            self.kind = "weight" if is_weight else "int"
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.lbl = QLabel(label)
        if tip:
            self.lbl.setToolTip(tip)
        if overridden:
            self.lbl.setStyleSheet("color: #e6a23c; font-weight: bold;")
        elif not editable:
            self.lbl.setStyleSheet("color: #777;")
        lay.addWidget(self.lbl, 2)
        self.slider = FineStepSlider(Qt.Orientation.Horizontal)
        if self.kind == "weight":
            # 0..100 ↔ 0.00..1.00 so one wheel tick = 0.01
            self.slider.setRange(0, 100)
            self.slider.setSingleStep(1)
            self.slider.setPageStep(1)
            self.slider.setValue(int(round(float(value) * 100)))
        elif self.kind == "float":
            hi = int(slider_max) if slider_max else max(1000, int(round(float(value) * 100 * 4)))
            hi = max(hi, 100)
            self.slider.setRange(1, hi)
            self.slider.setSingleStep(1)
            self.slider.setPageStep(10)
            self.slider.setValue(
                max(1, min(hi, int(round(float(value) * 100))))
            )
        else:
            hi = int(slider_max) if slider_max else 2000
            hi = max(hi, 2)
            self.slider.setRange(1, hi)
            self.slider.setSingleStep(1)
            self.slider.setPageStep(1)
            self.slider.setValue(max(1, min(hi, int(round(float(value))))))
        self.slider.valueChanged.connect(self._on_slide)
        self.slider.sliderPressed.connect(self.drag_started.emit)
        self.slider.sliderReleased.connect(self.drag_ended.emit)
        lay.addWidget(self.slider, 3)
        self.val = QLineEdit(self._fmt(value))
        self.val.setFixedWidth(56)
        self.val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.val.setStyleSheet(
            "QLineEdit { background: #2a2a2a; color: #e8e8e8; border: 1px solid #444; "
            "padding: 1px 4px; font-family: Consolas, monospace; }"
        )
        self.val.editingFinished.connect(self._on_edit)
        lay.addWidget(self.val)
        self.curve_btn = QToolButton()
        self.curve_btn.setFixedSize(22, 22)
        self.curve_btn.setAutoRaise(True)
        self.curve_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.curve_btn.setStyleSheet(
            "QToolButton { border: 1px solid #444; border-radius: 2px; padding: 0; "
            "background: #2a2a2a; }"
            "QToolButton:hover { border-color: #6a8aaa; }"
            "QToolButton:disabled { border-color: #333; background: #222; }"
        )
        menu = QMenu(self.curve_btn)
        for cid in CURVE_IDS:
            act = QAction(CURVE_LABELS[cid], menu)
            act.setData(cid)
            act.setCheckable(True)
            menu.addAction(act)
        menu.triggered.connect(self._on_curve_menu)
        self.curve_btn.setMenu(menu)
        self._sync_curve_btn()
        if show_curve:
            lay.addWidget(self.curve_btn)
        else:
            self.curve_btn.hide()
        self.btn = QPushButton("x")
        self.btn.setFixedWidth(22)
        self.btn.setStyleSheet(_CLEAR_BTN_STYLE)
        self.btn.setEnabled(bool(self.clearable and overridden))
        self.btn.setToolTip(
            "Clear faction override"
            if self.clearable
            else "Baseline — no faction overrides"
        )
        self.btn.clicked.connect(lambda: self.cleared.emit(self.key, self.is_weight))
        if show_clear:
            lay.addWidget(self.btn)
        else:
            self.btn.hide()
        if not editable:
            self.slider.setEnabled(False)
            self.val.setReadOnly(True)
            self.curve_btn.setEnabled(False)
            self.val.setStyleSheet(
                "QLineEdit { background: #222; color: #888; border: 1px solid #333; "
                "padding: 1px 4px; font-family: Consolas, monospace; }"
            )

    def _sync_curve_btn(self) -> None:
        self.curve_btn.setIcon(_curve_picker_icon(self.curve))
        self.curve_btn.setIconSize(QSize(14, 14))
        label = CURVE_LABELS.get(self.curve, CURVE_LABELS[CURVE_LINEAR])
        self.curve_btn.setToolTip(
            f"Score curve: {label}\n"
            "How 0→scale-max maps into the 0–1 score contribution."
        )
        menu = self.curve_btn.menu()
        if menu is not None:
            for act in menu.actions():
                act.setChecked(str(act.data()) == self.curve)

    def displayed_value(self) -> float:
        """Value currently shown (slider), not a stale line-edit string."""
        return float(self._slider_to_value(self.slider.value()))

    def displayed_curve(self) -> str:
        return normalize_curve(self.curve)

    def set_signals_blocked(self, block: bool) -> None:
        self.blockSignals(block)
        self.slider.blockSignals(block)
        self.val.blockSignals(block)
        self.curve_btn.blockSignals(block)
        menu = self.curve_btn.menu()
        if menu is not None:
            menu.blockSignals(block)

    def _on_curve_menu(self, action: QAction) -> None:
        cid = normalize_curve(str(action.data() or CURVE_LINEAR))
        if cid == self.curve:
            return
        self.curve = cid
        self._sync_curve_btn()
        self.curve_changed.emit(self.key, self.curve)

    def _fmt(self, v: float) -> str:
        if self.kind == "weight":
            return f"{v:.2f}"
        if self.kind == "float":
            if abs(v - round(v)) < 1e-9 and abs(v) >= 10:
                return str(int(round(v)))
            return f"{v:.2f}"
        return str(int(round(v)))

    def _emit_value(self, val: float) -> None:
        self.changed.emit(self.key, float(val), self.is_weight)

    def _on_slide(self, v: int) -> None:
        val = self._slider_to_value(v)
        self.val.blockSignals(True)
        self.val.setText(self._fmt(val))
        self.val.blockSignals(False)
        self._emit_value(val)

    def _on_edit(self) -> None:
        text = self.val.text().strip()
        try:
            raw = float(text)
        except ValueError:
            # Snap back to current slider value.
            self.val.setText(self._fmt(self._slider_to_value(self.slider.value())))
            return
        if self.kind == "weight":
            raw = max(0.0, min(1.0, raw))
            slider_v = int(round(raw * 100))
        elif self.kind == "float":
            lo = self.slider.minimum() / 100.0
            hi = self.slider.maximum() / 100.0
            raw = max(lo, min(hi, raw))
            slider_v = int(round(raw * 100))
        else:
            lo, hi = self.slider.minimum(), self.slider.maximum()
            raw = max(float(lo), min(float(hi), raw))
            slider_v = int(round(raw))
        self.slider.blockSignals(True)
        self.slider.setValue(slider_v)
        self.slider.blockSignals(False)
        val = self._slider_to_value(slider_v)
        self.val.setText(self._fmt(val))
        self._emit_value(val)

    def _slider_to_value(self, v: int) -> float:
        if self.kind in ("weight", "float"):
            return v / 100.0
        return float(v)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        log.info("MainWindow.__init__ begin")
        self.setWindowTitle("SALE — Stalker Anomaly Loadout Editor")
        # Opaque dark fill before first paint (same as SAGE).
        self.setAutoFillBackground(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setPalette(_dark_palette())
        self._start_maximized = False
        try:
            self.settings = load_settings()
            w = int(self.settings.get("window_w") or 1600)
            h = int(self.settings.get("window_h") or 900)
            self.resize(max(800, w), max(500, h))
            # Do NOT showMaximized() here — that paints a white HWND before
            # Fusion/dark titlebar (SAGE uses setWindowState, show later in main).
            self._start_maximized = bool(self.settings.get("window_maximized"))
            if self._start_maximized:
                self.setWindowState(
                    self.windowState() | Qt.WindowState.WindowMaximized
                )
            log.info(
                "settings loaded anomaly=%r gamma=%r size=%sx%s max=%s",
                self.settings.get("anomaly_root"),
                self.settings.get("gamma_root"),
                w,
                h,
                self._start_maximized,
            )
            self.balance = load_balance()
            self.items: dict[str, Any] = load_items()
            meta = (self.items or {}).get("meta") or {}
            log.info(
                "items.yml loaded counts=%s generated=%s",
                meta.get("counts"),
                meta.get("generated"),
            )
            self.faction = str(self.settings.get("faction") or "Default")
            cat0 = str(self.settings.get("category") or "weapons")
            self.category = cat0 if cat0 in CATS else "weapons"
            self._rows: list[
                tuple[str, dict[str, Any], int, bool, bool, bool]
            ] = []
            self._sel_by_cat: dict[str, str | None] = {c: None for c in CATS}
            self._compare_by_cat: dict[str, str | None] = {
                c: None for c in CATS
            }
            saved_sel = self.settings.get("selection") or {}
            if isinstance(saved_sel, dict):
                for c in CATS:
                    if saved_sel.get(c):
                        self._sel_by_cat[c] = str(saved_sel[c])
            self._worker: RegenWorker | None = None
            # Balance edits stay in memory until Ctrl+S (or close/export) saves + recalcs.
            self._balance_dirty = False
            self._recalc_running = False
            self._recalc_queued = False

            root = QWidget()
            v = QVBoxLayout(root)

            actions = QHBoxLayout()
            self.btn_regen = QPushButton("Regenerate")
            self.btn_regen.clicked.connect(self._regen)
            self.btn_load = QPushButton("Reload YML")
            self.btn_load.clicked.connect(self._reload_items)
            self.btn_export = QPushButton("Export LTX")
            self.btn_export.setToolTip(
                "Write loadout LTX (Ctrl+E → repo path · Ctrl+D deploy)"
            )
            self.btn_export.clicked.connect(self._export)
            self.btn_deploy = QPushButton("Deploy")
            self.btn_deploy.setToolTip(
                "Generate LTX and overwrite remembered target (Ctrl+D · Ctrl+Shift+D as…)"
            )
            self.btn_deploy.clicked.connect(self._deploy)
            self.btn_open_log = QPushButton("Open log")
            self.btn_open_log.clicked.connect(self._open_log)
            self.sort_box = QComboBox()
            self.sort_box.setMinimumWidth(140)
            self.sort_box.currentTextChanged.connect(self._on_sort_key_changed)
            self.sort_desc = QCheckBox("Desc")
            self.sort_desc.setChecked(False)
            self.sort_desc.setToolTip("Sort descending (off = ascending)")
            self.sort_desc.setStyleSheet("QCheckBox { color: #d0d0d0; }")
            self.sort_desc.toggled.connect(lambda _: self._run_list_refresh())
            actions.addWidget(self.btn_regen)
            actions.addWidget(self.btn_load)
            actions.addWidget(self.btn_export)
            actions.addWidget(self.btn_deploy)
            actions.addWidget(self.btn_open_log)
            actions.addWidget(_header_label("Sort:"))
            actions.addWidget(self.sort_box)
            actions.addWidget(self.sort_desc)
            actions.addStretch(1)
            v.addLayout(actions)

            ammo_wrap = QVBoxLayout()
            ammo_wrap.setSpacing(2)
            ammo_hdr = QHBoxLayout()
            self._ammo_expanded = bool(self.settings.get("ammo_expanded", True))
            self.btn_ammo_collapse = QToolButton()
            self.btn_ammo_collapse.setCheckable(True)
            self.btn_ammo_collapse.setChecked(self._ammo_expanded)
            self.btn_ammo_collapse.setToolButtonStyle(
                Qt.ToolButtonStyle.ToolButtonTextBesideIcon
            )
            self.btn_ammo_collapse.setStyleSheet(
                "QToolButton { color: #d0d0d0; font-size: 13px; font-weight: 700; "
                "border: none; background: transparent; padding: 2px 4px; }"
                "QToolButton:hover { color: #fff; }"
            )
            self.btn_ammo_collapse.setToolTip("Show / hide ammo filters")
            self.btn_ammo_collapse.toggled.connect(self._on_ammo_collapse)
            ammo_hdr.addWidget(self.btn_ammo_collapse)
            ammo_hdr.addStretch(1)
            ammo_wrap.addLayout(ammo_hdr)
            self.ammo_host = QWidget()
            self.ammo_host.setStyleSheet(
                "QWidget#ammoHost { border: 1px solid #333; background: #1a1a1a; }"
            )
            self.ammo_host.setObjectName("ammoHost")
            # Invisible 2-col table: Type | horizontal ammo toggles (no scroll).
            self.ammo_layout = QGridLayout(self.ammo_host)
            self.ammo_layout.setContentsMargins(2, 2, 2, 2)
            self.ammo_layout.setHorizontalSpacing(6)
            self.ammo_layout.setVerticalSpacing(2)
            self.ammo_layout.setColumnStretch(1, 1)
            ammo_wrap.addWidget(self.ammo_host)
            self.ammo_panel = QWidget()
            self.ammo_panel.setLayout(ammo_wrap)
            v.addWidget(self.ammo_panel)
            self._ammo_buttons: dict[str, QToolButton] = {}
            self._ammo_used_by_sel: set[str] = set()
            self._sync_ammo_collapse_ui()

            self.main_tabs = QTabWidget()
            self.main_tabs.setStyleSheet(_TAB_STYLE)
            editor = QWidget()
            editor_v = QVBoxLayout(editor)
            editor_v.setContentsMargins(0, 0, 0, 0)

            self.tabs = QTabWidget()
            self.tabs.setStyleSheet(_TAB_STYLE)
            for _cat, title in (
                ("weapons", "Weapons"),
                ("outfits", "Outfits"),
                ("helmets", "Helmets"),
            ):
                self.tabs.addTab(QWidget(), title)
            self.tabs.currentChanged.connect(self._on_tab)
            editor_v.addWidget(self.tabs)

            SIDEBAR_W = 460
            body = QHBoxLayout()
            body.setContentsMargins(0, 0, 0, 0)
            body.setSpacing(0)

            left_col = QWidget()
            left_col.setFixedWidth(SIDEBAR_W)
            left_v = QVBoxLayout(left_col)
            left_v.setContentsMargins(6, 6, 6, 0)
            left_v.setSpacing(4)

            fac = QHBoxLayout()
            fac.setContentsMargins(0, 0, 0, 0)
            fac.setSpacing(6)
            fac.addWidget(_header_label("Faction:"))
            self.faction_box = QComboBox()
            self.faction_box.addItem(faction_label("Default"), "Default")
            for f in FACTIONS:
                self.faction_box.addItem(faction_label(f), f)
            self.faction_box.currentTextChanged.connect(self._on_faction)
            fac.addWidget(self.faction_box, 1)
            self.faction_meta = QLabel("")
            self.faction_meta.setStyleSheet("color: #9aa3ad;")
            fac.addWidget(self.faction_meta)
            left_v.addLayout(fac)

            # Under faction: weapons allow/kind caps, or outfit/helmet max count.
            self.cat_filters_host = QWidget()
            cf = QVBoxLayout(self.cat_filters_host)
            cf.setContentsMargins(0, 4, 0, 4)
            cf.setSpacing(4)

            self.weapon_filters_host = QWidget()
            wf = QVBoxLayout(self.weapon_filters_host)
            wf.setContentsMargins(0, 0, 0, 0)
            wf.setSpacing(4)
            wcfg0 = effective_category(self.balance, "Default", "weapons")
            allow_row = QHBoxLayout()
            allow_row.setContentsMargins(0, 0, 0, 0)
            allow_row.setSpacing(12)
            self.chk_allow_suppressed = QCheckBox("Allow suppressed")
            self.chk_allow_suppressed.setChecked(
                bool(wcfg0.get("allow_suppressed", False))
            )
            self.chk_allow_suppressed.setToolTip(
                "Off: guns with an attached/integrated silencer are naturally "
                "out of shop. On: they follow normal score rules."
            )
            self.chk_allow_suppressed.setStyleSheet("QCheckBox { color: #d0d0d0; }")
            self.chk_allow_suppressed.toggled.connect(self._on_allow_suppressed)
            self.chk_allow_scoped = QCheckBox("Allow scoped")
            self.chk_allow_scoped.setChecked(bool(wcfg0.get("allow_scoped", False)))
            self.chk_allow_scoped.setToolTip(
                "Off: guns with a built-in/attached scope are naturally out of "
                "shop. On: they follow normal score rules."
            )
            self.chk_allow_scoped.setStyleSheet("QCheckBox { color: #d0d0d0; }")
            self.chk_allow_scoped.toggled.connect(self._on_allow_scoped)
            allow_row.addWidget(self.chk_allow_suppressed)
            allow_row.addWidget(self.chk_allow_scoped)
            allow_row.addStretch(1)
            wf.addLayout(allow_row)
            self.kind_limits_host = QWidget()
            self.kind_limits_layout = QVBoxLayout(self.kind_limits_host)
            self.kind_limits_layout.setContentsMargins(0, 2, 0, 0)
            self.kind_limits_layout.setSpacing(2)
            self._kind_limit_boxes: dict[str, QSpinBox] = {}
            wf.addWidget(self.kind_limits_host)
            cf.addWidget(self.weapon_filters_host)

            self.armor_limit_host = QWidget()
            al = QHBoxLayout(self.armor_limit_host)
            al.setContentsMargins(0, 0, 0, 0)
            al.setSpacing(6)
            self.armor_limit_label = QLabel("Max items")
            self.armor_limit_label.setStyleSheet("QLabel { color: #d0d0d0; }")
            self.armor_limit_label.setToolTip(
                "At most N lowest-pts items in LTX (not a minimum). "
                "Force-includes first; force-excludes never enter."
            )
            self.armor_limit_box = QSpinBox()
            self.armor_limit_box.setRange(0, MAX_ITEM_LIMIT)
            self.armor_limit_box.setSingleStep(1)
            self.armor_limit_box.setFixedWidth(52)
            self.armor_limit_box.setToolTip(self.armor_limit_label.toolTip())
            self.armor_limit_box.setStyleSheet(
                "QSpinBox { background: #2a2a2a; color: #e8e8e8; "
                "border: 1px solid #444; padding: 1px 2px; }"
            )
            self.armor_limit_box.valueChanged.connect(self._on_armor_limit_changed)
            al.addWidget(self.armor_limit_label, 1)
            al.addWidget(self.armor_limit_box)
            cf.addWidget(self.armor_limit_host)

            left_v.addWidget(self.cat_filters_host)
            self._rebuild_kind_limit_boxes()
            self._sync_cat_filters_ui()

            self.sidebar_tabs = QTabWidget()
            self.sidebar_tabs.setStyleSheet(_TAB_STYLE)
            weights_scroll, self.weights_host, self.weights_layout = (
                self._make_sidebar_scroll_page()
            )
            ceilings_scroll, self.ceilings_host, self.ceilings_layout = (
                self._make_sidebar_scroll_page()
            )
            self.sidebar_tabs.addTab(weights_scroll, "Weights")
            self.sidebar_tabs.addTab(ceilings_scroll, "Scale max")
            left_v.addWidget(self.sidebar_tabs, 1)
            body.addWidget(left_col)

            self.list = QListWidget()
            self.list.setViewMode(QListWidget.ViewMode.IconMode)
            self.list.setFlow(QListWidget.Flow.LeftToRight)
            self.list.setWrapping(True)
            self.list.setIconSize(QSize(GRID_ICON_W, GRID_ICON_H))
            self.list.setGridSize(QSize(GRID_CELL_W, GRID_CELL_H))
            self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
            self.list.setMovement(QListWidget.Movement.Static)
            self.list.setUniformItemSizes(True)
            self.list.setWordWrap(False)
            self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
            self.list.setSpacing(1)  # half of previous 2
            self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
            self.list.setStyleSheet(
                "QListWidget { background: #1a1a1a; border: none; outline: none; }"
                "QListWidget::item {"
                "  color: transparent; background: transparent; padding: 0;"
                "}"
                "QListWidget::item:selected {"
                "  background: transparent;"
                "}"
                "QListWidget::item:selected:active {"
                "  background: transparent;"
                "}"
            )
            self.list.viewport().installEventFilter(self)
            self.list.installEventFilter(self)
            self.installEventFilter(self)
            self.list.currentItemChanged.connect(self._on_select)
            body.addWidget(self.list, 1)

            detail_host = QWidget()
            detail_host.setFixedWidth(SIDEBAR_W)
            detail_lay = QVBoxLayout(detail_host)
            detail_lay.setContentsMargins(6, 6, 6, 6)
            detail_lay.setSpacing(6)
            self.detail_info = self._make_kv_table(["Info", "Value"])
            self.detail_info.clicked.connect(
                lambda idx: self._on_detail_sort_click(
                    self.detail_info, idx.row(), idx.column()
                )
            )
            detail_lay.addWidget(self.detail_info)
            self.detail_stats = self._make_kv_table(
                ["Stat", "Value", "Weight", "Final", "Diff"]
            )
            self.detail_stats.clicked.connect(
                lambda idx: self._on_detail_sort_click(
                    self.detail_stats, idx.row(), idx.column()
                )
            )
            detail_lay.addWidget(self.detail_stats, 1)
            body.addWidget(detail_host)
            editor_v.addLayout(body, 1)

            self.main_tabs.addTab(editor, "Editor")

            self.log_view = QPlainTextEdit()
            self.log_view.setReadOnly(True)
            self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            self.log_view.setStyleSheet(
                "QPlainTextEdit { background: #1e1e1e; color: #d4d4d4; font-family: Consolas, monospace; }"
            )
            self.main_tabs.addTab(self.log_view, "Log")
            v.addWidget(self.main_tabs, 1)

            self.status = QLabel(f"Ready — log: {LOG_PATH}")
            v.addWidget(self.status)

            # Full-window busy overlay (shown only while a debounced recalc runs).
            self.ui_host = BusyOverlayHost(root)
            self.setCentralWidget(self.ui_host)

            save_sc = QShortcut(QKeySequence.StandardKey.Save, self)
            save_sc.setContext(Qt.ShortcutContext.WindowShortcut)
            save_sc.activated.connect(self._force_save)

            export_sc = QShortcut(QKeySequence("Ctrl+E"), self)
            export_sc.setContext(Qt.ShortcutContext.WindowShortcut)
            export_sc.activated.connect(self._generate_ltx)

            deploy_sc = QShortcut(QKeySequence("Ctrl+D"), self)
            deploy_sc.setContext(Qt.ShortcutContext.WindowShortcut)
            deploy_sc.activated.connect(self._deploy)

            deploy_as_sc = QShortcut(QKeySequence("Ctrl+Shift+D"), self)
            deploy_as_sc.setContext(Qt.ShortcutContext.WindowShortcut)
            deploy_as_sc.activated.connect(self._deploy_as)

            esc_sc = QShortcut(QKeySequence("Esc"), self)
            esc_sc.setContext(Qt.ShortcutContext.WindowShortcut)
            esc_sc.activated.connect(self._deselect_all)

            attach_log_view(self.log_view)
            self._restore_ui_state()
            self._rebuild_weights()
            self._rebuild_ammo_toggles()
            self._refresh_sort_options(
                prefer=str(self.settings.get("sort_key") or "pts")
            )
            self._rebuild_list()
            log.info("MainWindow.__init__ done")
        except Exception:
            log.critical(
                "MainWindow.__init__ crashed\n%s",
                traceback.format_exc(),
            )
            raise

    def _make_kv_table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        # No selection chrome; current cell still updates for Ctrl+C / context copy.
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setShowGrid(False)
        table.setAlternatingRowColors(True)
        table.setWordWrap(False)
        table.setTextElideMode(Qt.TextElideMode.ElideNone)
        hdr = table.horizontalHeader()
        hdr.setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        for c in range(len(headers) - 1):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(
            len(headers) - 1, QHeaderView.ResizeMode.Stretch
        )
        hdr.setStretchLastSection(True)
        hdr.setMinimumSectionSize(40)
        table.setStyleSheet(_TABLE_STYLE)
        table.setCursor(Qt.CursorShape.ArrowCursor)
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.customContextMenuRequested.connect(
            lambda pos, t=table: self._kv_table_context_menu(t, pos)
        )
        copy_sc = QShortcut(QKeySequence.StandardKey.Copy, table)
        copy_sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        copy_sc.activated.connect(lambda t=table: self._copy_kv_table_selection(t))
        return table

    def _copy_kv_table_selection(self, table: QTableWidget) -> None:
        item = table.currentItem()
        if item is None:
            return
        QApplication.clipboard().setText(item.text())

    def _kv_table_context_menu(self, table: QTableWidget, pos) -> None:
        menu = QMenu(table)
        act = menu.addAction("Copy")
        act.setShortcut(QKeySequence.StandardKey.Copy)
        chosen = menu.exec(table.viewport().mapToGlobal(pos))
        if chosen is act:
            self._copy_kv_table_selection(table)

    def _fill_kv_table(
        self,
        table: QTableWidget,
        rows: list[tuple],
    ) -> None:
        """rows: (key, [col values…], name_color[, optional cell_colors for vals])."""
        ncols = table.columnCount()
        table.setRowCount(len(rows))
        align_l = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        for row, entry in enumerate(rows):
            key = entry[0]
            vals = entry[1]
            color = entry[2]
            cell_colors = entry[3] if len(entry) > 3 else None
            name_item = QTableWidgetItem(pretty_label(key))
            name_item.setForeground(QColor(color))
            font = name_item.font()
            font.setBold(True)
            name_item.setFont(font)
            name_item.setTextAlignment(align_l)
            name_item.setData(Qt.ItemDataRole.UserRole, key)
            # Stats names can be sort-highlighted; info names never are.
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            table.setItem(row, 0, name_item)
            for c in range(1, ncols):
                text = vals[c - 1] if c - 1 < len(vals) else ""
                cell = QTableWidgetItem(text)
                fg = "#e8e8e8"
                if cell_colors and c - 1 < len(cell_colors):
                    cc = cell_colors[c - 1]
                    if cc is not None:
                        fg = cc
                cell.setForeground(QColor(fg))
                cell.setTextAlignment(align_l)
                cell.setData(Qt.ItemDataRole.UserRole, key)
                cell.setFlags(Qt.ItemFlag.ItemIsEnabled)
                table.setItem(row, c, cell)
        hdr = table.horizontalHeader()
        for c in range(max(0, ncols - 1)):
            table.resizeColumnToContents(c)
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
            pad = 20 if c == 0 else 8
            table.setColumnWidth(c, table.columnWidth(c) + pad)
        if ncols > 1:
            hdr.setSectionResizeMode(ncols - 1, QHeaderView.ResizeMode.Stretch)
            hdr.setStretchLastSection(True)
        # Fit info table height to rows; stats keeps stretch.
        if table is self.detail_info:
            rows_h = sum(table.rowHeight(i) for i in range(table.rowCount()))
            header_h = table.horizontalHeader().height()
            table.setFixedHeight(header_h + rows_h + 4)
        elif table is self.detail_stats:
            self._apply_stats_sort_highlight()

    def _sort_keys_for_category(self) -> list[str]:
        keys: list[str] = ["pts", "name", "cost", "sec", "in_shop", "community"]
        if self.category == "weapons":
            keys.append("ammo")
            keys.extend(sk for sk, *_ in WEAPON_WEIGHTS if sk not in keys)
        else:
            keys.extend(sk for sk, *_ in ARMOR_WEIGHTS if sk not in keys)
        pool = (self.items.get(self.category) or {}) if self.items else {}
        extras: set[str] = set()
        for entry in pool.values():
            extras.update((entry.get("stats") or {}).keys())
        for k in sorted(extras):
            if k not in keys:
                keys.append(k)
        return keys

    def _current_sort_key(self) -> str:
        data = self.sort_box.currentData()
        if data:
            return str(data)
        text = self.sort_box.currentText() or "pts"
        # Legacy settings may still store raw keys as the visible text.
        return text

    def _set_sort_key(self, key: str) -> None:
        idx = self.sort_box.findData(key)
        if idx < 0:
            self.sort_box.addItem(pretty_label(key), key)
            tip = pretty_tip(key)
            if tip:
                self.sort_box.setItemData(
                    self.sort_box.count() - 1, tip, Qt.ItemDataRole.ToolTipRole
                )
            idx = self.sort_box.findData(key)
        if idx >= 0:
            self.sort_box.setCurrentIndex(idx)

    def _refresh_sort_options(self, prefer: str | None = None) -> None:
        keys = self._sort_keys_for_category()
        cur = prefer or self._current_sort_key() or "pts"
        self.sort_box.blockSignals(True)
        self.sort_box.clear()
        for k in keys:
            self.sort_box.addItem(pretty_label(k), k)
            tip = pretty_tip(k)
            if tip:
                self.sort_box.setItemData(
                    self.sort_box.count() - 1, tip, Qt.ItemDataRole.ToolTipRole
                )
        idx = self.sort_box.findData(cur)
        if idx < 0:
            idx = self.sort_box.findText(cur)
        self.sort_box.setCurrentIndex(idx if idx >= 0 else 0)
        self.sort_box.blockSignals(False)

    def _apply_stats_sort_highlight(self) -> None:
        """Highlight only the stats-table name matching the active sort key."""
        table = getattr(self, "detail_stats", None)
        if table is None:
            return
        sort_key = self._current_sort_key() or ""
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item is None:
                continue
            key = str(item.data(Qt.ItemDataRole.UserRole) or "")
            if key and key == sort_key:
                item.setBackground(_SORT_NAME_BG)
            else:
                item.setBackground(QBrush())

    def _on_sort_key_changed(self, _text: str) -> None:
        self._apply_stats_sort_highlight()
        self._run_list_refresh()

    def _on_detail_sort_click(
        self, table: QTableWidget, row: int, column: int = 0
    ) -> None:
        # Only the name column sorts; value / weight / final are inert.
        if column != 0:
            return
        # Info names can still sort, but only stats names get a persistent highlight.
        item = table.item(row, 0)
        if not item:
            return
        key = str(item.data(Qt.ItemDataRole.UserRole) or item.text())
        if not key:
            return
        cur = self._current_sort_key()
        self.sort_box.blockSignals(True)
        if cur == key:
            # Reselect → clear sort key (back to pts). Desc toggle is left alone.
            self._set_sort_key("pts")
        else:
            self._set_sort_key(key)
        self.sort_box.blockSignals(False)
        self._apply_stats_sort_highlight()
        self._run_list_refresh()

    def _row_sort_value(
        self,
        sec: str,
        entry: dict[str, Any],
        pts: int,
        in_shop: bool,
        key: str,
    ) -> Any:
        if key == "pts":
            return int(pts)
        if key == "name":
            return (entry.get("name") or sec).lower()
        if key == "sec":
            return sec.lower()
        if key == "cost":
            stats = entry.get("stats") or {}
            return float(entry.get("cost") or stats.get("cost") or 0)
        if key == "in_shop":
            return 1 if in_shop else 0
        if key == "community":
            return (entry.get("community") or "").lower()
        if key == "ammo":
            return ",".join(str(a) for a in (entry.get("ammo_class") or [])).lower()
        raw = (entry.get("stats") or {}).get(key)
        if key == "hit_power":
            return hit_power_pct(raw)
        if isinstance(raw, bool):
            return 1.0 if raw else 0.0
        if isinstance(raw, (int, float)):
            return float(raw)
        if raw is None or raw == "":
            return 0.0
        try:
            return float(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            # Keep numeric sort stable across the list.
            return 0.0

    def _open_log(self) -> None:
        log.info("open log requested: %s", LOG_PATH)
        try:
            from PyQt6.QtCore import QUrl
            from PyQt6.QtGui import QDesktopServices

            QDesktopServices.openUrl(QUrl.fromLocalFile(str(LOG_PATH)))
        except Exception:  # noqa: BLE001
            log.exception("failed to open log file")
            QMessageBox.information(self, "Log", str(LOG_PATH))

    def _browse(self, edit: QLineEdit) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select folder", edit.text())
        if d:
            edit.setText(d)
            log.info("browse selected %s", d)

    def _save_window_geom(self) -> None:
        self.settings["window_maximized"] = self.isMaximized()
        if not self.isMaximized():
            self.settings["window_w"] = int(self.width())
            self.settings["window_h"] = int(self.height())

    def _persist_ui_state(self) -> None:
        # anomaly_root / gamma_root are set from the Regenerate dialog.
        self.settings["faction"] = self.faction
        self.settings["category"] = self.category
        self.settings["sort_key"] = self._current_sort_key() or "pts"
        self.settings["sort_dir"] = "Desc" if self.sort_desc.isChecked() else "Asc"
        self.settings["selection"] = {
            c: self._sel_by_cat.get(c) for c in CATS if self._sel_by_cat.get(c)
        }
        self.settings["ammo_expanded"] = bool(self._ammo_expanded)
        self._save_window_geom()

    def _restore_ui_state(self) -> None:
        fac = str(self.settings.get("faction") or "Default")
        idx = self.faction_box.findData(fac)
        if idx < 0:
            # Legacy settings may have stored a display label.
            idx = self.faction_box.findText(fac)
            if idx < 0:
                for i in range(self.faction_box.count()):
                    if faction_label(str(self.faction_box.itemData(i) or "")) == fac:
                        idx = i
                        break
        self.faction_box.blockSignals(True)
        self.faction_box.setCurrentIndex(idx if idx >= 0 else 0)
        self.faction_box.blockSignals(False)
        data = self.faction_box.currentData()
        self.faction = str(data if data is not None else self.faction_box.currentText())

        cat = str(self.settings.get("category") or "weapons")
        if cat not in CATS:
            cat = "weapons"
        self.category = cat
        self.tabs.blockSignals(True)
        self.tabs.setCurrentIndex(CATS.index(cat))
        self.tabs.blockSignals(False)

        sdir = str(self.settings.get("sort_dir") or "Asc")
        self.sort_desc.blockSignals(True)
        self.sort_desc.setChecked(sdir == "Desc")
        self.sort_desc.blockSignals(False)

        n = override_count(self.balance, self.faction)
        self.faction_meta.setText(f"({n} overrides)" if n else "")

        self._ammo_expanded = bool(self.settings.get("ammo_expanded", True))
        self.btn_ammo_collapse.blockSignals(True)
        self.btn_ammo_collapse.setChecked(self._ammo_expanded)
        self.btn_ammo_collapse.blockSignals(False)
        self._sync_ammo_collapse_ui()

    def _sync_ammo_collapse_ui(self) -> None:
        expanded = bool(self._ammo_expanded)
        self.btn_ammo_collapse.setText("Ammo" if expanded else "Ammo")
        self.btn_ammo_collapse.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.ammo_host.setVisible(expanded)

    def _on_ammo_collapse(self, expanded: bool) -> None:
        self._ammo_expanded = bool(expanded)
        self._sync_ammo_collapse_ui()
        self.settings["ammo_expanded"] = self._ammo_expanded
        save_settings(self.settings)
        log.debug("ammo panel expanded=%s", self._ammo_expanded)

    def _save_roots(self) -> None:
        self._persist_ui_state()
        save_settings(self.settings)
        log.debug(
            "roots saved anomaly=%r gamma=%r",
            self.settings.get("anomaly_root"),
            self.settings.get("gamma_root"),
        )

    def _prompt_regen_paths(self) -> tuple[Path | None, Path | None] | None:
        """Modal path picker for Regenerate. Returns (anomaly, gamma) or None if cancelled."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Regenerate — scan roots")
        dlg.setMinimumWidth(560)
        dlg.setStyleSheet(
            "QDialog { background: #252525; }"
            "QLabel { color: #d0d0d0; }"
            "QLineEdit { background: #1e1e1e; color: #e8e8e8; border: 1px solid #444; "
            "padding: 4px 6px; }"
            "QPushButton { background: #3a3a3a; color: #eee; border: 1px solid #555; "
            "padding: 5px 12px; }"
            "QPushButton:hover { background: #4a4a4a; }"
        )
        lay = QVBoxLayout(dlg)
        lay.setSpacing(10)
        tip = QLabel("Set Anomaly and/or GAMMA roots, then Scan to rebuild items.yml.")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #9aa3ad;")
        lay.addWidget(tip)

        def path_row(label: str, initial: str) -> tuple[QLineEdit, QHBoxLayout]:
            row = QHBoxLayout()
            row.addWidget(_header_label(label))
            edit = QLineEdit(initial)
            row.addWidget(edit, 1)
            browse = QPushButton("…")
            browse.setFixedWidth(36)
            browse.clicked.connect(lambda: self._browse(edit))
            row.addWidget(browse)
            return edit, row

        anomaly_edit, a_row = path_row(
            "Anomaly:", str(self.settings.get("anomaly_root") or "")
        )
        gamma_edit, g_row = path_row(
            "GAMMA:", str(self.settings.get("gamma_root") or "")
        )
        lay.addLayout(a_row)
        lay.addLayout(g_row)

        buttons = QDialogButtonBox()
        scan_btn = buttons.addButton("Scan", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        scan_btn.setDefault(True)
        lay.addWidget(buttons)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            log.info("Regenerate cancelled (path dialog)")
            return None

        a_txt = anomaly_edit.text().strip()
        g_txt = gamma_edit.text().strip()
        self.settings["anomaly_root"] = a_txt
        self.settings["gamma_root"] = g_txt
        self._save_roots()
        anomaly = Path(a_txt) if a_txt else None
        gamma = Path(g_txt) if g_txt else None
        return anomaly, gamma

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            self._flush_slider_focus()
            self._commit_sliders_to_balance()
            self._persist_ui_state()
            save_settings(self.settings)
            save_balance(self.balance)
            self._balance_dirty = False
            log.info(
                "saved on close balance=%s settings=%s",
                "ok",
                "ok",
            )
        except Exception:  # noqa: BLE001
            log.exception("save on close failed")
        super().closeEvent(event)

    def _set_window_busy(self, busy: bool) -> None:
        host = getattr(self, "ui_host", None)
        if host is not None:
            host.set_busy(busy)
            if busy:
                QApplication.processEvents()

    def _iter_weight_rows(self) -> list[WeightRow]:
        rows: list[WeightRow] = []
        for lay in (self.weights_layout, self.ceilings_layout):
            for i in range(lay.count()):
                w = lay.itemAt(i).widget()
                if isinstance(w, WeightRow):
                    rows.append(w)
        return rows

    def _block_slider_signals(self, block: bool) -> None:
        for row in self._iter_weight_rows():
            row.set_signals_blocked(block)

    def _read_slider_ui_state(self) -> dict[str, tuple[float, str]]:
        """key → (displayed value, curve id)."""
        out: dict[str, tuple[float, str]] = {}
        for row in self._iter_weight_rows():
            out[row.key] = (row.displayed_value(), row.displayed_curve())
        return out

    def _flush_slider_focus(self) -> None:
        """Clear focus so in-progress line edits commit via editingFinished."""
        fw = QApplication.focusWidget()
        if fw is not None and self.isAncestorOf(fw):
            fw.clearFocus()

    def _commit_kind_limits_from_ui(self) -> None:
        """Spinboxes → balance (source of truth before kind-cap select)."""
        boxes = getattr(self, "_kind_limit_boxes", None) or {}
        for kind, box in boxes.items():
            try:
                set_kind_limit(self.balance, kind, int(box.value()))
            except Exception:  # noqa: BLE001
                log.exception("kind limit commit failed kind=%s", kind)

    def _commit_armor_limit_from_ui(self) -> None:
        """Outfit/helmet max-items spinbox → balance."""
        box = getattr(self, "armor_limit_box", None)
        if box is None or self.category not in ("outfits", "helmets"):
            return
        try:
            set_item_limit(self.balance, self.category, int(box.value()))
        except Exception:  # noqa: BLE001
            log.exception("armor limit commit failed cat=%s", self.category)

    def _on_armor_limit_changed(self, value: int) -> None:
        if self.category not in ("outfits", "helmets"):
            return
        set_item_limit(self.balance, self.category, int(value))
        self._mark_balance_dirty()

    def _sync_cat_filters_ui(self) -> None:
        """Show weapon kind caps or armor max-items under the faction dropdown."""
        is_wpn = self.category == "weapons"
        is_armor = self.category in ("outfits", "helmets")
        if hasattr(self, "weapon_filters_host"):
            self.weapon_filters_host.setVisible(is_wpn)
        if hasattr(self, "armor_limit_host"):
            self.armor_limit_host.setVisible(is_armor)
        if is_armor and hasattr(self, "armor_limit_box"):
            cfg = effective_category(self.balance, self.faction, self.category)
            self.armor_limit_box.blockSignals(True)
            self.armor_limit_box.setValue(item_limit_for(cfg))
            self.armor_limit_box.blockSignals(False)
            label = "Max outfits" if self.category == "outfits" else "Max helmets"
            self.armor_limit_label.setText(label)

    def _commit_sliders_to_balance(self) -> int:
        """Write current sidebar slider/toggle UI into ``self.balance``.

        UI is the source of truth at save time — avoids stale balance when a
        signal was blocked or a line-edit hadn't flushed yet.
        """
        fac = self.faction
        cat = self.category
        n = 0
        self._commit_kind_limits_from_ui()
        self._commit_armor_limit_from_ui()
        for row in self._iter_weight_rows():
            key = row.key
            val = float(row.displayed_value())
            if key.startswith("ceiling:"):
                if fac != "Default":
                    continue
                stat_key = key[8:]
                if val <= 0:
                    continue
                set_ceiling(self.balance, cat, stat_key, val)
                set_curve(self.balance, cat, stat_key, row.displayed_curve())
                n += 1
                continue
            store: Any = val
            if key == "include_universal_armor":
                store = val >= 0.5
            set_override(
                self.balance, fac, cat, key, store, weight=bool(row.is_weight)
            )
            n += 1
        # Toggle rows (not WeightRow) — keyed via objectName.
        for i in range(self.weights_layout.count()):
            host = self.weights_layout.itemAt(i).widget()
            if host is None:
                continue
            for cb in host.findChildren(QCheckBox):
                key = cb.objectName()
                if not key:
                    continue
                set_override(
                    self.balance,
                    fac,
                    cat,
                    key,
                    bool(cb.isChecked()),
                    weight=False,
                )
                n += 1
        if n:
            self._balance_dirty = True
            log.debug(
                "committed %d slider/toggle value(s) → balance fac=%s cat=%s",
                n,
                fac,
                cat,
            )
        return n

    def _balance_slider_state(self) -> dict[str, tuple[float, str]]:
        """Authoritative key → (value, curve) from stored balance (current fac/cat)."""
        fac = self.faction
        cat = self.category
        cfg = effective_category(self.balance, fac, cat)
        out: dict[str, tuple[float, str]] = {}
        out["max_pts"] = (float(cfg.get("max_pts") or 900), CURVE_LINEAR)
        out["cost_mult"] = (float(cfg.get("cost_mult") or 1000), CURVE_LINEAR)
        weights = cfg.get("weights") or {}
        if cat == "weapons":
            for _sk, wkey, _c, _i, default_w in WEAPON_WEIGHTS:
                out[wkey] = (float(weights.get(wkey, default_w)), CURVE_LINEAR)
        else:
            out["a_price"] = (float(weights.get("a_price", 0.5)), CURVE_LINEAR)
            for _sk, wkey, _c, _i, default_w in ARMOR_WEIGHTS:
                out[wkey] = (float(weights.get(wkey, default_w)), CURVE_LINEAR)
        ceil_map = cfg.get("ceilings") or {}
        curve_map = cfg.get("curves") or {}
        if cat == "weapons":
            for sk, _wk, default_c, _inv, _dw in WEAPON_WEIGHTS:
                if sk in NO_CEILING_STATS:
                    continue
                cap = float(default_c)
                try:
                    cur = float(ceil_map.get(sk, default_c))
                except (TypeError, ValueError):
                    cur = cap
                if cur <= 0:
                    cur = cap
                cur = min(cur, cap)
                out[f"ceiling:{sk}"] = (cur, normalize_curve(curve_map.get(sk)))
        else:
            defs = default_ceilings_armor(is_helmet=cat == "helmets")
            for sk, default_c in defs.items():
                cap = float(default_c)
                try:
                    cur = float(ceil_map.get(sk, default_c))
                except (TypeError, ValueError):
                    cur = cap
                if cur <= 0:
                    cur = cap
                cur = min(cur, cap)
                out[f"ceiling:{sk}"] = (cur, normalize_curve(curve_map.get(sk)))
        return out

    def _resync_sliders_from_balance(self, *, reason: str) -> int:
        """Failsafe: detect UI↔balance drift, then rebuild sidebar from balance.

        Returns number of drifted keys found before resync.
        """
        expected = self._balance_slider_state()
        before = self._read_slider_ui_state()
        drift: list[str] = []
        for key, exp in expected.items():
            got = before.get(key)
            if got is None:
                continue
            if abs(got[0] - exp[0]) > 1e-6 or got[1] != exp[1]:
                drift.append(key)
                log.warning(
                    "slider drift (%s) key=%s ui=%s balance=%s",
                    reason,
                    key,
                    got,
                    exp,
                )
        if drift:
            log.warning(
                "slider drift after %s — %d key(s); forcing UI resync from balance",
                reason,
                len(drift),
            )
        # Always rebuild from stored balance so display is authoritative.
        self._rebuild_weights()
        after = self._read_slider_ui_state()
        bad: list[str] = []
        for key, exp in expected.items():
            got = after.get(key)
            if got is None or abs(got[0] - exp[0]) > 1e-6 or got[1] != exp[1]:
                bad.append(key)
        if bad:
            log.error(
                "slider resync incomplete after %s: %s",
                reason,
                ", ".join(bad[:20]),
            )
        else:
            log.debug(
                "slider resync ok after %s keys=%d drift_was=%d",
                reason,
                len(expected),
                len(drift),
            )
        return len(drift)

    def _save_now(self) -> bool:
        """Save balance/settings and recalculate scores. False if blocked/failed."""
        if self._recalc_running:
            self._recalc_queued = True
            self.status.setText("Save queued — wait for recalc…")
            log.info("save during recalc — queued")
            return False
        try:
            # UI → balance before any disk write / score rebuild.
            self._flush_slider_focus()
            self._commit_sliders_to_balance()
            self._persist_ui_state()
            save_settings(self.settings)
            self._run_score_refresh()
            log.info("save + recalc ok")
            return True
        except Exception:  # noqa: BLE001
            log.exception("save failed")
            self.status.setText("Save failed — see Log tab")
            return False

    def _force_save(self) -> None:
        """Ctrl+S: save balance/settings and recalculate item scores."""
        if self._save_now():
            self.status.setText("Saved — scores recalculated")

    def _on_allow_suppressed(self, checked: bool) -> None:
        w = (self.balance.get("default") or {}).setdefault("weapons", {})
        w["allow_suppressed"] = bool(checked)
        self._mark_balance_dirty()

    def _on_allow_scoped(self, checked: bool) -> None:
        w = (self.balance.get("default") or {}).setdefault("weapons", {})
        w["allow_scoped"] = bool(checked)
        self._mark_balance_dirty()

    def _rebuild_kind_limit_boxes(self) -> None:
        """Build 0–10 kind quota spinboxes for kinds present in the weapon pool."""
        lay = self.kind_limits_layout
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._kind_limit_boxes.clear()
        weapons = (self.items or {}).get("weapons") or {}
        kinds = present_weapon_kinds(weapons)
        if not kinds:
            self.kind_limits_host.setVisible(False)
            return
        self.kind_limits_host.setVisible(True)
        cfg = effective_category(self.balance, "Default", "weapons")
        for kind in kinds:
            cell = QWidget()
            cell_l = QHBoxLayout(cell)
            cell_l.setContentsMargins(0, 0, 0, 0)
            cell_l.setSpacing(6)
            name = QLabel(kind_label(kind))
            name.setStyleSheet("QLabel { color: #d0d0d0; }")
            name.setToolTip(
                f"{kind}: max N in LTX (not a minimum). "
                f"Auto-picks lowest-pts eligible guns; force-includes first."
            )
            box = QSpinBox()
            box.setRange(0, MAX_KIND_LIMIT)
            box.setSingleStep(1)
            box.setFixedWidth(52)
            box.setToolTip(name.toolTip())
            box.setStyleSheet(
                "QSpinBox { background: #2a2a2a; color: #e8e8e8; "
                "border: 1px solid #444; padding: 1px 2px; }"
            )
            val0 = kind_limit_for(cfg, kind)

            def _on_change(v: int, k: str = kind) -> None:
                set_kind_limit(self.balance, k, int(v))
                self._mark_balance_dirty()

            box.blockSignals(True)
            box.setValue(val0)
            box.blockSignals(False)
            box.valueChanged.connect(_on_change)
            cell_l.addWidget(name, 1)
            cell_l.addWidget(box)
            lay.addWidget(cell)
            self._kind_limit_boxes[kind] = box

    def _mark_balance_dirty(self) -> None:
        """Record an in-memory balance edit; scores refresh only on Ctrl+S."""
        self._balance_dirty = True
        if self._recalc_running:
            self._recalc_queued = True

    def _run_list_refresh(self) -> None:
        """Grey out and rebuild the item list (sort / desc — no disk save)."""
        if self._recalc_running:
            self._recalc_queued = True
            return
        self._recalc_running = True
        self._block_slider_signals(True)
        self._set_window_busy(True)
        try:
            self._rebuild_list()
        finally:
            self._set_window_busy(False)
            self._block_slider_signals(False)
            self._recalc_running = False
            if self._recalc_queued:
                self._recalc_queued = False
                if self._balance_dirty:
                    self._run_score_refresh()
                else:
                    self._run_list_refresh()

    def _run_score_refresh(self) -> None:
        """Save balance, grey out, rebuild scores; loop if more edits queued."""
        if self._recalc_running:
            self._recalc_queued = True
            return
        # Re-commit in case caller skipped _save_now, or UI changed mid-queue.
        self._flush_slider_focus()
        self._commit_sliders_to_balance()
        self._recalc_running = True
        self._block_slider_signals(True)
        self._set_window_busy(True)
        try:
            while True:
                self._recalc_queued = False
                # Capture whatever the UI currently shows before writing disk.
                self._commit_sliders_to_balance()
                try:
                    save_balance(self.balance)
                except Exception:  # noqa: BLE001
                    log.exception("balance save during recalc failed")
                self._balance_dirty = False
                self._rebuild_list()
                if not self._recalc_queued and not self._balance_dirty:
                    break
        finally:
            self._set_window_busy(False)
            self._block_slider_signals(False)
            self._recalc_running = False
            try:
                # Verify UI still matches what we saved (should be a no-op).
                self._resync_sliders_from_balance(reason="save")
            except Exception:  # noqa: BLE001
                log.exception("post-save slider resync failed")
            if self._recalc_queued or self._balance_dirty:
                self._recalc_queued = False
                self._run_score_refresh()

    def _on_faction(self, _name: str) -> None:
        data = self.faction_box.currentData()
        fac = str(data if data is not None else _name)
        log.debug("faction -> %s (%s)", fac, faction_label(fac))
        self.faction = fac
        n = override_count(self.balance, fac)
        self.faction_meta.setText(f"({n} overrides)" if n else "")
        self._rebuild_weights()
        self._rebuild_ammo_toggles()
        self._run_list_refresh()

    def _on_tab(self, idx: int) -> None:
        # Stash selection under the category we're leaving.
        cur = self.list.currentItem()
        if cur is not None:
            self._sel_by_cat[self.category] = cur.data(Qt.ItemDataRole.UserRole)
        self.category = CATS[idx] if 0 <= idx < len(CATS) else "weapons"
        log.debug("category tab -> %s", self.category)
        self.ammo_panel.setVisible(self.category == "weapons")
        self._sync_cat_filters_ui()
        self._rebuild_weights()
        self._refresh_sort_options()
        self._run_list_refresh()

    def _ammo_thumb_path(self, sec: str) -> str | None:
        """Cached thumbs only (produced by Regenerate)."""
        if not sec:
            return None
        entry = ((self.items or {}).get("ammo") or {}).get(sec) or {}
        cached = str(entry.get("thumb") or "").strip()
        if cached and Path(cached).is_file():
            return cached
        for cand in (
            THUMBS_DIR / f"{sec}.inv.png",
            THUMBS_DIR / f"{sec}.fallback.png",
        ):
            if cand.is_file():
                return str(cand)
        return None

    # Ammo toggle: wide texture box, 2px outer margin.
    _AMMO_ICON_W = 90
    _AMMO_ICON_H = 54

    def _ammo_cost(self, sec: str) -> int:
        entry = ((self.items or {}).get("ammo") or {}).get(sec) or {}
        try:
            return max(0, int(round(float(entry.get("cost") or 0))))
        except (TypeError, ValueError):
            return 0

    def _ammo_box_icon(
        self, thumb: str | None, *, enabled: bool = True, cost: int = 0
    ) -> QIcon:
        """Wide icon box; texture fitted inside; grey cost number top-left (no bg)."""
        w = self._AMMO_ICON_W
        h = self._AMMO_ICON_H
        canvas = QPixmap(w, h)
        if enabled:
            canvas.fill(QColor(36, 72, 48))
        else:
            canvas.fill(QColor(30, 30, 30))
        painter = QPainter(canvas)
        try:
            if thumb and Path(thumb).is_file():
                pix = QPixmap(thumb)
                if not pix.isNull():
                    scaled = pix.scaled(
                        w,
                        h,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    x = (w - scaled.width()) // 2
                    y = (h - scaled.height()) // 2
                    painter.drawPixmap(x, y, scaled)
            if cost > 0:
                font = QFont("Consolas", 8)
                painter.setFont(font)
                painter.setPen(QColor(255, 255, 255))
                painter.drawText(2, 2 + painter.fontMetrics().ascent(), str(cost))
        finally:
            painter.end()
        return QIcon(canvas)

    def _ammo_tip(self, sec: str, enabled: bool) -> str:
        fam = ammo_family(sec)
        bloc = ammo_family_bloc(fam)
        tag = ammo_bloc_tag(bloc)
        cost = self._ammo_cost(sec)
        cost_s = f"{cost:,} RU".replace(",", " ") if cost > 0 else "—"
        return (
            f"{sec}\n"
            f"cost: {cost_s}\n"
            f"bloc: {tag} ({ammo_bloc_label(bloc)})\n"
            f"{'enabled' if enabled else 'DISABLED — not in loadout; gun kept if ≥1 other ammo on'}"
        )

    def _refresh_ammo_button_icon(self, sec: str, enabled: bool) -> None:
        btn = self._ammo_buttons.get(sec)
        if btn is None:
            return
        btn.setIcon(
            self._ammo_box_icon(
                self._ammo_thumb_path(sec),
                enabled=enabled,
                cost=self._ammo_cost(sec),
            )
        )
        btn.setToolTip(self._ammo_tip(sec, enabled))
        self._apply_ammo_btn_border(sec)

    @staticmethod
    def _ammo_btn_stylesheet(*, used: bool) -> str:
        """Border colors: blue = used by selected gun; else green on / orange off."""
        base = (
            "QToolButton { border-radius: 0px; padding: 0px; margin: 0px; "
            "background: transparent; }"
        )
        if used:
            return (
                base
                + "QToolButton { border: 1px solid #5ec8ff; }"
                + "QToolButton:checked { border-color: #5ec8ff; }"
                + "QToolButton:!checked { border-color: #5ec8ff; }"
            )
        return (
            base
            + "QToolButton { border: 1px solid #444; }"
            + "QToolButton:checked { border-color: #6a9e6a; }"
            + "QToolButton:!checked { border-color: #96826a; }"
        )

    def _apply_ammo_btn_border(self, sec: str) -> None:
        btn = self._ammo_buttons.get(sec)
        if btn is None:
            return
        btn.setStyleSheet(
            self._ammo_btn_stylesheet(used=sec in self._ammo_used_by_sel)
        )

    def _highlight_weapon_ammos(self, ammo_secs: set[str] | None) -> None:
        """Blue border on ammo boxes the selected weapon uses (border only)."""
        self._ammo_used_by_sel = set(ammo_secs or ())
        for sec in self._ammo_buttons:
            self._apply_ammo_btn_border(sec)

    def _make_ammo_toggle(self, sec: str) -> QToolButton:
        """One ammo toggle: texture box only (name in tooltip)."""
        enabled = is_ammo_family_enabled(self.balance, self.faction, sec)
        iw, ih = self._AMMO_ICON_W, self._AMMO_ICON_H

        btn = QToolButton()
        btn.setCheckable(True)
        btn.setChecked(enabled)
        btn.setAutoRaise(False)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        btn.setIconSize(QSize(iw, ih))
        btn.setFixedSize(iw, ih)
        btn.setIcon(
            self._ammo_box_icon(
                self._ammo_thumb_path(sec),
                enabled=enabled,
                cost=self._ammo_cost(sec),
            )
        )
        btn.setToolTip(self._ammo_tip(sec, enabled))
        btn.setStyleSheet(
            self._ammo_btn_stylesheet(used=sec in self._ammo_used_by_sel)
        )
        btn.toggled.connect(lambda on, s=sec: self._on_ammo_toggled(s, on))
        self._ammo_buttons[sec] = btn
        return btn

    def _rebuild_ammo_toggles(self) -> None:
        """Rebuild ammo toggles as invisible 2-col table: Type | horizontal ammos."""
        while self.ammo_layout.count():
            item = self.ammo_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._ammo_buttons = {}
        sections = collect_ammo_sections((self.items or {}).get("weapons") or {})
        ammo_pool = (self.items or {}).get("ammo") or {}
        # Within each bloc: calibre families together, families + members by cost asc.
        nato = sort_ammo_sections_by_family_price(
            [s for s in sections if ammo_family_bloc(ammo_family(s)) == "nato"],
            ammo_pool,
        )
        warsaw = sort_ammo_sections_by_family_price(
            [s for s in sections if ammo_family_bloc(ammo_family(s)) == "wp"],
            ammo_pool,
        )
        other = sort_ammo_sections_by_family_price(
            [
                s
                for s in sections
                if ammo_family_bloc(ammo_family(s)) not in ("nato", "wp")
            ],
            ammo_pool,
        )
        groups: list[tuple[str, list[str]]] = [
            ("NATO", nato),
            ("WARSAW", warsaw),
            ("OTHER", other),
        ]
        type_style = (
            "QLabel { color: #d0d0d0; font-size: 12px; font-weight: 700; "
            "font-family: Consolas, monospace; padding: 0; }"
        )
        row_btn_style = (
            "QPushButton { color: #ccc; background: #2a2a2a; border: 1px solid #444; "
            "padding: 1px 6px; font-size: 10px; }"
            "QPushButton:hover { background: #3a3a3a; }"
        )
        row = 0
        for title, secs in groups:
            if not secs:
                continue
            type_host = QWidget()
            type_lay = QVBoxLayout(type_host)
            type_lay.setContentsMargins(0, 0, 0, 0)
            type_lay.setSpacing(2)
            type_lbl = QLabel(title)
            type_lbl.setStyleSheet(type_style)
            type_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            type_lay.addWidget(type_lbl)
            btn_row = QHBoxLayout()
            btn_row.setContentsMargins(0, 0, 0, 0)
            btn_row.setSpacing(4)
            btn_all = QPushButton("All")
            btn_all.setFixedWidth(36)
            btn_all.setStyleSheet(row_btn_style)
            btn_all.setToolTip(f"Enable all {title} ammo for this faction")
            btn_all.clicked.connect(
                lambda _=False, ss=list(secs): self._set_row_ammo(ss, True)
            )
            btn_none = QPushButton("None")
            btn_none.setFixedWidth(44)
            btn_none.setStyleSheet(row_btn_style)
            btn_none.setToolTip(f"Disable all {title} ammo for this faction")
            btn_none.clicked.connect(
                lambda _=False, ss=list(secs): self._set_row_ammo(ss, False)
            )
            btn_row.addWidget(btn_all)
            btn_row.addWidget(btn_none)
            btn_row.addStretch(1)
            type_lay.addLayout(btn_row)
            type_lay.addStretch(1)
            self.ammo_layout.addWidget(
                type_host, row, 0, Qt.AlignmentFlag.AlignTop
            )

            row_host = QWidget()
            row_host.setFixedHeight(self._AMMO_ICON_H + 2)
            row_lay = QHBoxLayout(row_host)
            row_lay.setContentsMargins(0, 0, 0, 0)
            row_lay.setSpacing(2)
            for sec in secs:
                row_lay.addWidget(self._make_ammo_toggle(sec))
            row_lay.addStretch(1)
            self.ammo_layout.addWidget(row_host, row, 1)
            row += 1
        self.ammo_panel.setVisible(self.category == "weapons")
        # Re-apply selection highlight after rebuild.
        self._highlight_weapon_ammos(self._ammo_used_by_sel)
        log.debug(
            "ammo toggles fac=%s sections=%d off=%d",
            self.faction,
            len(sections),
            sum(
                1
                for s in sections
                if not is_ammo_family_enabled(self.balance, self.faction, s)
            ),
        )

    def _migrate_legacy_ammo_family(self, sec: str) -> None:
        """If an old calibre-family off key still applies, expand to per-section offs."""
        fam = ammo_family(sec)
        ae = ammo_enabled_map(self.balance, self.faction)
        if fam not in ae or bool(ae[fam]):
            return
        siblings = [
            s
            for s in collect_ammo_sections((self.items or {}).get("weapons") or {})
            if ammo_family(s) == fam and s != sec
        ]
        set_ammo_family_enabled(self.balance, self.faction, fam, True)  # drop legacy key
        for s in siblings:
            set_ammo_family_enabled(self.balance, self.faction, s, False)

    def _on_ammo_toggled(self, sec: str, enabled: bool) -> None:
        log.debug(
            "ammo toggle fac=%s sec=%s enabled=%s",
            self.faction,
            sec,
            enabled,
        )
        if enabled:
            self._migrate_legacy_ammo_family(sec)
        set_ammo_family_enabled(self.balance, self.faction, sec, enabled)
        n = override_count(self.balance, self.faction)
        self.faction_meta.setText(f"({n} overrides)" if n else "")
        self._refresh_ammo_button_icon(sec, enabled)
        self._mark_balance_dirty()

    def _set_row_ammo(self, sections: list[str], enabled: bool) -> None:
        """Enable/disable only the ammo sections in one type row."""
        if enabled:
            for sec in sections:
                self._migrate_legacy_ammo_family(sec)
        for sec in sections:
            set_ammo_family_enabled(self.balance, self.faction, sec, enabled)
        n = override_count(self.balance, self.faction)
        self.faction_meta.setText(f"({n} overrides)" if n else "")
        self._rebuild_ammo_toggles()
        self._mark_balance_dirty()

    @staticmethod
    def _make_sidebar_scroll_page() -> tuple[QScrollArea, QWidget, QVBoxLayout]:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
        )
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(4, 4, 4, 4)
        scroll.setWidget(host)
        return scroll, host, lay

    @staticmethod
    def _clear_layout(lay: QVBoxLayout) -> None:
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "color: #9aa3ad; font-weight: bold; margin-top: 2px; margin-bottom: 2px;"
        )
        return lbl

    def _section_break(self, title: str) -> QWidget:
        """Horizontal rule + section title to split slider groups by nature."""
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 10, 0, 2)
        lay.setSpacing(4)
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Plain)
        line.setFixedHeight(1)
        line.setStyleSheet("background: #4a4a4a; border: none;")
        lay.addWidget(line)
        lay.addWidget(self._section_label(title))
        return wrap

    def _rebuild_weights(self) -> None:
        try:
            self._clear_layout(self.weights_layout)
            self._clear_layout(self.ceilings_layout)
            fac = self.faction
            cat = self.category
            cfg = effective_category(self.balance, fac, cat)
            ceilings_locked = fac != "Default"
            clearable = fac != "Default"

            def add_scalar(key: str, value: float) -> None:
                ov = is_overridden(self.balance, fac, cat, key, weight=False)
                row = WeightRow(
                    key,
                    pretty_label(key),
                    value,
                    is_weight=False,
                    overridden=ov,
                    tip=pretty_tip(key),
                    kind="int",
                    slider_max=5000,
                    clearable=clearable,
                )
                row.changed.connect(self._weight_changed)
                row.cleared.connect(self._weight_cleared)
                self.weights_layout.addWidget(row)

            def add_weight(key: str, value: float) -> None:
                ov = is_overridden(self.balance, fac, cat, key, weight=True)
                row = WeightRow(
                    key,
                    pretty_label(key),
                    value,
                    is_weight=True,
                    overridden=ov,
                    tip=pretty_tip(key),
                    clearable=clearable,
                )
                row.changed.connect(self._weight_changed)
                row.cleared.connect(self._weight_cleared)
                self.weights_layout.addWidget(row)

            def add_ceiling(stat_key: str, value: float, slider_cap: float) -> None:
                # slider_cap is the hard max for this Scale max slider (0–x).
                # Code defaults in WEAPON_WEIGHTS / ARMOR_* are those caps — not ×5 headroom.
                cap = float(slider_cap) if slider_cap and float(slider_cap) > 0 else 1.0
                try:
                    cur = float(value)
                except (TypeError, ValueError):
                    cur = cap
                if cur <= 0:
                    cur = cap
                cur = min(cur, cap)
                if cap <= 10:
                    kind = "float"
                    smax = max(1, int(round(cap * 100)))
                else:
                    kind = "int"
                    smax = max(1, int(round(cap)))
                tip = pretty_ceiling_tip(stat_key)
                if ceilings_locked:
                    tip = f"{tip} (Baseline only — shared by all factions.)"
                tip = f"{tip} Slider range 0–{cap:g}."
                curve = normalize_curve((curve_map or {}).get(stat_key))
                row = WeightRow(
                    f"ceiling:{stat_key}",
                    pretty_label(stat_key),
                    cur,
                    is_weight=False,
                    overridden=False,
                    tip=tip,
                    kind=kind,
                    editable=not ceilings_locked,
                    show_clear=False,
                    slider_max=smax,
                    show_curve=True,
                    curve=curve,
                )
                row.changed.connect(self._ceiling_changed)
                row.curve_changed.connect(self._ceiling_curve_changed)
                self.ceilings_layout.addWidget(row)

            def add_toggle(key: str, checked: bool) -> None:
                ov = is_overridden(self.balance, fac, cat, key, weight=False)
                row = QWidget()
                lay = QHBoxLayout(row)
                lay.setContentsMargins(0, 2, 0, 4)
                lay.setSpacing(6)
                cb = QCheckBox(pretty_label(key))
                cb.setObjectName(key)
                tip = pretty_tip(key)
                if tip:
                    cb.setToolTip(tip)
                cb.setChecked(bool(checked))
                if ov:
                    cb.setStyleSheet(
                        "QCheckBox { color: #e6a23c; font-weight: bold; }"
                    )
                else:
                    cb.setStyleSheet("QCheckBox { color: #d0d0d0; }")
                cb.toggled.connect(
                    lambda on, k=key: self._weight_changed(k, 1.0 if on else 0.0, False)
                )
                lay.addWidget(cb, 1)
                btn = QPushButton("x")
                btn.setFixedWidth(22)
                btn.setStyleSheet(_CLEAR_BTN_STYLE)
                btn.setEnabled(bool(clearable and ov))
                btn.setToolTip(
                    "Clear faction override"
                    if clearable
                    else "Baseline — no faction overrides"
                )
                btn.clicked.connect(
                    lambda _=False, k=key: self._weight_cleared(k, False)
                )
                lay.addWidget(btn)
                self.weights_layout.addWidget(row)

            # Tab 1: Budget + stat weights (per-faction).
            self.weights_layout.addWidget(self._section_label("Budget"))
            if cat == "outfits":
                add_toggle(
                    "include_universal_armor",
                    bool(cfg.get("include_universal_armor", True)),
                )
            add_scalar("max_pts", float(cfg.get("max_pts") or 900))
            add_scalar("cost_mult", float(cfg.get("cost_mult") or 1000))

            self.weights_layout.addWidget(self._section_break("Stat weights"))
            weights = cfg.get("weights") or {}
            if cat == "weapons":
                for _sk, wkey, _c, _i, default_w in WEAPON_WEIGHTS:
                    add_weight(wkey, float(weights.get(wkey, default_w)))
            else:
                add_weight("a_price", float(weights.get("a_price", 0.5)))
                for _sk, wkey, _c, _i, default_w in ARMOR_WEIGHTS:
                    add_weight(wkey, float(weights.get(wkey, default_w)))
            self.weights_layout.addStretch(1)

            # Tab 2: Scale maxes (0–x) — Baseline-only; other factions inherit.
            ceil_note = "Normalization ceilings (0–x)"
            if ceilings_locked:
                ceil_note += " — Baseline only"
            self.ceilings_layout.addWidget(self._section_label(ceil_note))
            ceil_map = cfg.get("ceilings") or {}
            curve_map = cfg.get("curves") or {}
            if cat == "weapons":
                for sk, _wk, default_c, _inv, _dw in WEAPON_WEIGHTS:
                    if sk in NO_CEILING_STATS:
                        continue
                    add_ceiling(
                        sk, float(ceil_map.get(sk, default_c)), float(default_c)
                    )
            else:
                defs = default_ceilings_armor(is_helmet=cat == "helmets")
                # Price first, then protections (match weight order).
                add_ceiling(
                    "cost",
                    float(ceil_map.get("cost", defs["cost"])),
                    float(defs["cost"]),
                )
                for sk, _wk, default_c, _inv, _dw in ARMOR_WEIGHTS:
                    add_ceiling(
                        sk, float(ceil_map.get(sk, default_c)), float(default_c)
                    )
            self.ceilings_layout.addStretch(1)
        except Exception:
            log.exception("_rebuild_weights failed fac=%s cat=%s", self.faction, self.category)
            raise

    def _ceiling_changed(self, key: str, value: float, _is_weight: bool) -> None:
        if self._recalc_running or self.faction != "Default":
            return
        stat_key = key[8:] if key.startswith("ceiling:") else key
        if value <= 0:
            return
        log.debug(
            "ceiling set cat=%s key=%s value=%s",
            self.category,
            stat_key,
            value,
        )
        set_ceiling(self.balance, self.category, stat_key, float(value))
        self._mark_balance_dirty()

    def _ceiling_curve_changed(self, key: str, curve: str) -> None:
        if self._recalc_running or self.faction != "Default":
            return
        stat_key = key[8:] if key.startswith("ceiling:") else key
        log.debug(
            "ceiling curve set cat=%s key=%s curve=%s",
            self.category,
            stat_key,
            curve,
        )
        set_curve(self.balance, self.category, stat_key, curve)
        self._mark_balance_dirty()

    def _weight_changed(self, key: str, value: float, is_weight: bool) -> None:
        if self._recalc_running:
            # Overlay should block input; if a signal still slips through, queue.
            self._balance_dirty = True
            self._recalc_queued = True
            return
        store: Any = value
        if key == "include_universal_armor":
            store = value >= 0.5
        log.debug(
            "override set fac=%s cat=%s key=%s value=%r weight=%s",
            self.faction,
            self.category,
            key,
            store,
            is_weight,
        )
        set_override(
            self.balance, self.faction, self.category, key, store, weight=is_weight
        )
        n = override_count(self.balance, self.faction)
        self.faction_meta.setText(f"({n} overrides)" if n else "")
        if key == "include_universal_armor":
            # Recreate toggle row so override styling/reset btn stay in sync.
            self._rebuild_weights()
            self._mark_balance_dirty()
            return
        # Orange = faction override only; Baseline edits are the shared base.
        if self.faction != "Default":
            for i in range(self.weights_layout.count()):
                w = self.weights_layout.itemAt(i).widget()
                if (
                    isinstance(w, WeightRow)
                    and w.key == key
                    and w.is_weight == is_weight
                ):
                    w.lbl.setStyleSheet("color: #e6a23c; font-weight: bold;")
                    if w.clearable:
                        w.btn.setEnabled(True)
        self._mark_balance_dirty()

    def _weight_cleared(self, key: str, is_weight: bool) -> None:
        log.debug(
            "override clear fac=%s cat=%s key=%s weight=%s",
            self.faction,
            self.category,
            key,
            is_weight,
        )
        clear_override(
            self.balance, self.faction, self.category, key, weight=is_weight
        )
        fac = self.faction
        if fac != "Default":
            fov = (self.balance.get("factions") or {}).get(fac) or {}
            cblock = fov.get(self.category) or {}
            if not cblock:
                fov.pop(self.category, None)
            if not fov:
                (self.balance.get("factions") or {}).pop(fac, None)
        self._rebuild_weights()
        self._mark_balance_dirty()

    def _row_pts_inshop(
        self, sec: str, entry: dict[str, Any]
    ) -> tuple[int, bool, bool, bool]:
        """Return (pts, in_shop, faction_blocked, under_pts).

        ``under_pts`` is score below max_pts (independent of ammo/faction/kind).
        ``faction_blocked`` = ammo toggle / name-lock / outfit community.
        ``in_shop`` = under_pts and not faction/allow blocked (pre kind-cap).
        """
        cfg = effective_category(self.balance, self.faction, self.category)
        stats = entry.get("stats") or {}
        ceilings = cfg.get("ceilings") or {}
        curves = cfg.get("curves") or {}
        if self.category == "weapons":
            pts = weapon_pts(
                stats,
                cfg.get("weights") or {},
                float(cfg.get("cost_mult") or 1000),
                ceilings,
                curves,
            )
            under = pts < float(cfg.get("max_pts") or 900)
            ammo_map = ammo_enabled_map(self.balance, self.faction)
            name_ok = weapon_name_faction_ok(sec, self.faction)
            ammo_ok = weapon_ammo_allowed(entry.get("ammo_class") or [], ammo_map)
            allow_ok = True
            if not bool(cfg.get("allow_suppressed", False)) and has_attached_silencer(
                entry=entry
            ):
                allow_ok = False
            if not bool(cfg.get("allow_scoped", False)) and has_attached_scope(
                entry=entry
            ):
                allow_ok = False
            gear_ok = name_ok and ammo_ok
            return pts, under and gear_ok and allow_ok, (not gear_ok), under
        is_helm = self.category == "helmets"
        pts = armor_pts(
            stats,
            cfg.get("weights") or {},
            float(cfg.get("cost_mult") or 1000),
            is_helmet=is_helm,
            ceilings=ceilings,
            curves=curves,
        )
        under = pts < float(cfg.get("max_pts") or 550)
        if is_helm or self.faction == "Default":
            return pts, under, False, under
        from .score import FACTION_COMMUNITY

        want = FACTION_COMMUNITY.get(self.faction, self.faction)
        community = (entry.get("community") or "").strip()
        allow_univ = bool(cfg.get("include_universal_armor", True))
        gear_ok = community == want or (allow_univ and community in ("", "actor"))
        return pts, under and gear_ok, (not gear_ok), under

    def _clear_detail(self) -> None:
        self._fill_kv_table(self.detail_info, [])
        self._fill_kv_table(self.detail_stats, [])
        self._highlight_weapon_ammos(None)

    def _rebuild_list(self) -> None:
        t0 = time.perf_counter()
        try:
            # Restore per-category selection (set on select / when leaving a tab).
            want_sec = self._sel_by_cat.get(self.category)
            cur = self.list.currentItem()
            if cur is not None:
                cur_sec = cur.data(Qt.ItemDataRole.UserRole)
                if cur_sec in (self.items.get(self.category) or {}):
                    want_sec = cur_sec
                    self._sel_by_cat[self.category] = cur_sec

            self.list.blockSignals(True)
            self.list.clear()
            pool = (self.items.get(self.category) or {}) if self.items else {}
            rows: list[tuple[str, dict[str, Any], int, bool, bool, bool]] = []
            for sec, entry in pool.items():
                if name_blocked(sec):
                    continue
                if self.category == "weapons" and (
                    is_explosive_weapon(
                        sec, ammo_class=entry.get("ammo_class") or []
                    )
                    or is_gauss_weapon(
                        sec, ammo_class=entry.get("ammo_class") or []
                    )
                ):
                    continue
                try:
                    pts, in_shop, fac_blocked, under_pts = self._row_pts_inshop(
                        sec, entry
                    )
                except Exception:  # noqa: BLE001
                    log.exception("pts failed for %s", sec)
                    pts, in_shop, fac_blocked, under_pts = 0, False, False, False
                rows.append((sec, entry, pts, in_shop, fac_blocked, under_pts))

            selected_ltx: set[str] | None = None
            if self.category == "weapons":
                # Spinbox values win (may not have been flushed via valueChanged).
                self._commit_kind_limits_from_ui()
                wcfg = effective_category(self.balance, self.faction, "weapons")
                eligible = {
                    sec: pts for sec, _e, pts, ok, _fb, _u in rows if ok
                }
                selected_ltx = select_weapons_for_ltx(
                    pool, self.balance, eligible=eligible, cat_cfg=wcfg
                )
                log.debug(
                    "kind caps %s → ltx %d / eligible %d",
                    {
                        k: kind_limit_for(wcfg, k)
                        for k in present_weapon_kinds(pool)
                    },
                    len(selected_ltx),
                    len(eligible),
                )
            elif self.category in ("outfits", "helmets"):
                self._commit_armor_limit_from_ui()
                acfg = effective_category(
                    self.balance, self.faction, self.category
                )
                eligible = {
                    sec: pts for sec, _e, pts, ok, _fb, _u in rows if ok
                }
                limit = item_limit_for(acfg)
                selected_ltx = select_pool_for_ltx(
                    pool, self.balance, eligible=eligible, limit=limit
                )
                log.debug(
                    "%s max_items=%d → ltx %d / eligible %d",
                    self.category,
                    limit,
                    len(selected_ltx),
                    len(eligible),
                )
            # Final LTX flag per row (caps applied); used for paint + in_shop sort.
            in_ltx_map: dict[str, bool] = {}
            for sec, _e, _pts, in_shop, _fb, _u in rows:
                ov = get_item_ltx_override(self.balance, sec)
                if selected_ltx is not None:
                    in_ltx_map[sec] = sec in selected_ltx
                else:
                    in_ltx_map[sec] = item_in_ltx(in_shop, ov)

            sort_key = self._current_sort_key() or "pts"
            descending = self.sort_desc.isChecked()

            def _sort_val(r: tuple) -> object:
                sec, entry, pts, in_shop, _fb, _u = r
                if sort_key == "in_shop":
                    return 1 if in_ltx_map.get(sec) else 0
                return self._row_sort_value(sec, entry, pts, in_shop, sort_key)

            rows.sort(key=_sort_val, reverse=descending)
            self._rows = rows

            thumb_ok = 0
            restore_item: QListWidgetItem | None = None
            n_ltx = 0
            want_cmp = self._compare_by_cat.get(self.category)
            if want_cmp and want_cmp == want_sec:
                want_cmp = None
                self._compare_by_cat[self.category] = None
            for sec, entry, pts, in_shop, fac_blocked, under_pts in rows:
                # Name is painted bottom-left on the tile (no under-icon label).
                label = _nice_item_name(sec, entry)
                item = QListWidgetItem("")
                # Cached thumbs only (produced by Regenerate).
                thumb = str(entry.get("thumb") or "").strip()
                if thumb and not Path(thumb).is_file():
                    thumb = ""
                if not thumb:
                    for cand in (
                        THUMBS_DIR / f"{sec}.inv.png",
                        THUMBS_DIR / f"{sec}.fallback.png",
                    ):
                        if cand.is_file():
                            thumb = str(cand)
                            break
                if thumb:
                    thumb_ok += 1
                if want_sec and sec == want_sec:
                    sel_mode: bool | str = "primary"
                elif want_cmp and sec == want_cmp:
                    sel_mode = "compare"
                else:
                    sel_mode = False
                fac_tag = section_name_faction_token(sec) or ""
                ov = get_item_ltx_override(self.balance, sec)
                in_ltx = bool(in_ltx_map.get(sec))
                if in_ltx:
                    n_ltx += 1
                # Below pts: green (in LTX) or orange (not). Grey only over pts.
                paint_blocked = bool(fac_blocked) and not bool(in_shop)
                try:
                    item.setIcon(
                        _icon_with_pts(
                            thumb or None,
                            pts,
                            in_shop=in_shop,
                            faction_blocked=paint_blocked,
                            under_pts=under_pts,
                            selected=sel_mode,
                            name=label,
                            faction_tag=fac_tag,
                            in_ltx=in_ltx,
                            override=ov,
                        )
                    )
                except Exception:  # noqa: BLE001
                    log.exception("icon compose failed sec=%s", sec)
                block_note = ""
                if ov == "exclude":
                    block_note = "  |  force-excluded from LTX"
                elif ov == "include":
                    block_note = "  |  force-included in LTX"
                elif fac_blocked:
                    locked = (
                        section_name_faction(sec)
                        if self.category == "weapons"
                        else None
                    )
                    if (
                        locked
                        and self.faction != "Default"
                        and locked != self.faction
                    ):
                        block_note = (
                            f"  |  name-locked to {faction_label(locked)}"
                        )
                    elif self.category == "weapons":
                        block_note = "  |  ammo type disabled"
                    else:
                        block_note = "  |  faction community"
                elif not in_ltx and in_shop and self.category == "weapons":
                    block_note = "  |  over kind pts quota"
                elif not in_shop and self.category == "weapons":
                    if has_attached_silencer(entry=entry) and not bool(
                        effective_category(
                            self.balance, self.faction, "weapons"
                        ).get("allow_suppressed", False)
                    ):
                        block_note = "  |  suppressed (Allow suppressed off)"
                    elif has_attached_scope(entry=entry) and not bool(
                        effective_category(
                            self.balance, self.faction, "weapons"
                        ).get("allow_scoped", False)
                    ):
                        block_note = "  |  scoped (Allow scoped off)"
                tip = (
                    f"{sec}\n{entry.get('name') or ''}\n"
                    f"{pts} pts  |  in_ltx={in_ltx}  eligible={in_shop}{block_note}\n"
                    f"Checkbox: empty=auto · click=force opposite"
                )
                item.setToolTip(tip)
                item.setData(Qt.ItemDataRole.UserRole, sec)
                # Cache paint inputs for selection highlight refresh.
                item.setData(
                    Qt.ItemDataRole.UserRole + 1,
                    {
                        "thumb": thumb or None,
                        "pts": pts,
                        "in_shop": in_shop,
                        "faction_blocked": paint_blocked,
                        "under_pts": under_pts,
                        "name": label,
                        "faction_tag": fac_tag,
                        "in_ltx": in_ltx,
                        "override": ov,
                        # Used to restore tile color when clearing a force override
                        # before the next Ctrl+S rebuild.
                        "auto_in": in_ltx if ov is None else in_shop,
                    },
                )
                # Let gridSize own layout — custom sizeHint breaks IconMode alignment.
                self.list.addItem(item)
                if want_sec and sec == want_sec:
                    restore_item = item
            self.list.blockSignals(False)
            if restore_item is not None:
                self.list.setCurrentItem(restore_item)
                # Populate detail without relying on the blocked signal during fill.
                self._on_select(restore_item, None)
            else:
                self._clear_detail()
            # After pts recalc / resort, always show the top of the grid.
            self.list.scrollToTop()
            elapsed = time.perf_counter() - t0
            n_blocked = sum(1 for r in rows if r[4])
            self.status.setText(
                f"{len(rows)} {self.category}  "
                f"(faction={faction_label(self.faction)})  "
                f"in_ltx={n_ltx}  blocked={n_blocked}  {elapsed:.2f}s"
            )
            log.info(
                "rebuild_list cat=%s fac=%s rows=%d in_ltx=%d blocked=%d thumbs=%d in %.3fs",
                self.category,
                self.faction,
                len(rows),
                n_ltx,
                n_blocked,
                thumb_ok,
                elapsed,
            )
        except Exception:
            self.list.blockSignals(False)
            log.exception(
                "_rebuild_list crashed cat=%s fac=%s",
                self.category,
                self.faction,
            )
            raise

    def _set_item_selected_icon(
        self,
        item: QListWidgetItem | None,
        *,
        selected: bool | str = False,
    ) -> None:
        if item is None:
            return
        meta = item.data(Qt.ItemDataRole.UserRole + 1) or {}
        if not isinstance(meta, dict):
            return
        try:
            item.setIcon(
                _icon_with_pts(
                    meta.get("thumb"),
                    int(meta.get("pts") or 0),
                    in_shop=bool(meta.get("in_shop")),
                    faction_blocked=bool(meta.get("faction_blocked")),
                    under_pts=bool(meta.get("under_pts", True)),
                    selected=selected,
                    name=str(meta.get("name") or ""),
                    faction_tag=str(meta.get("faction_tag") or ""),
                    in_ltx=bool(meta.get("in_ltx", meta.get("in_shop"))),
                    override=meta.get("override"),
                )
            )
        except Exception:  # noqa: BLE001
            log.exception("selection icon refresh failed")

    def _item_by_sec(self, sec: str | None) -> QListWidgetItem | None:
        if not sec:
            return None
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it is not None and it.data(Qt.ItemDataRole.UserRole) == sec:
                return it
        return None

    def _compare_sec(self) -> str | None:
        return self._compare_by_cat.get(self.category)

    def _clear_compare(self, *, refresh_detail: bool = False) -> None:
        old = self._item_by_sec(self._compare_sec())
        self._compare_by_cat[self.category] = None
        if old is not None and old is not self.list.currentItem():
            self._set_item_selected_icon(old, selected=False)
        if refresh_detail and self.list.currentItem() is not None:
            self._populate_detail(self.list.currentItem())

    def _set_compare_item(self, item: QListWidgetItem) -> None:
        primary = self.list.currentItem()
        if primary is None or item is primary:
            return
        sec = item.data(Qt.ItemDataRole.UserRole)
        if not sec:
            return
        sec = str(sec)
        old_sec = self._compare_sec()
        if old_sec == sec:
            self._clear_compare(refresh_detail=True)
            return
        old = self._item_by_sec(old_sec)
        if old is not None and old is not primary:
            self._set_item_selected_icon(old, selected=False)
        self._compare_by_cat[self.category] = sec
        self._set_item_selected_icon(item, selected="compare")
        self._populate_detail(primary)
        log.debug("compare %s vs %s", primary.data(Qt.ItemDataRole.UserRole), sec)

    def _item_icon_origin(self, item: QListWidgetItem) -> QPoint:
        """Top-left of the painted icon inside the list viewport.

        IconMode places the decoration at the top-center of the cell (not
        vertically centered) — matching that keeps checkbox hits aligned.
        """
        vrect = self.list.visualItemRect(item)
        ix = vrect.x() + max(0, (vrect.width() - GRID_ICON_W) // 2)
        iy = vrect.y() + max(0, (vrect.height() - GRID_ICON_H) // 2)
        # Prefer top alignment when the cell is taller than the icon (IconMode).
        if vrect.height() > GRID_ICON_H + 2:
            iy = vrect.y()
        return QPoint(ix, iy)

    def _item_checkbox_hit(self, item: QListWidgetItem, pos: QPoint) -> bool:
        """True if ``pos`` (viewport coords) is on this tile's override checkbox."""
        vrect = self.list.visualItemRect(item)
        # Generous cell-corner target — survives icon padding / DPI quirks.
        corner = QRect(
            vrect.right() - _ITEM_CB_HIT + 1,
            vrect.bottom() - _ITEM_CB_HIT + 1,
            _ITEM_CB_HIT,
            _ITEM_CB_HIT,
        )
        if corner.contains(pos):
            return True
        origin = self._item_icon_origin(item)
        cb = _item_checkbox_rect()
        drawn = QRect(
            origin.x() + cb.x() - 6,
            origin.y() + cb.y() - 6,
            cb.width() + 12,
            cb.height() + 12,
        )
        return drawn.contains(pos)

    def _item_for_checkbox_click(self, pos: QPoint) -> QListWidgetItem | None:
        """Resolve which tile's checkbox was clicked (``itemAt`` misses edges)."""
        item = self.list.itemAt(pos)
        if item is not None:
            return item if self._item_checkbox_hit(item, pos) else None
        # itemAt missed (padding/gaps): check checkbox corners of on-screen tiles.
        vp = self.list.viewport().rect()
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it is None:
                continue
            vr = self.list.visualItemRect(it)
            if not vr.intersects(vp):
                continue
            if self._item_checkbox_hit(it, pos):
                return it
        return None

    def _toggle_item_included(self, item: QListWidgetItem) -> None:
        sec = item.data(Qt.ItemDataRole.UserRole)
        if not sec:
            return
        sec = str(sec)
        meta = item.data(Qt.ItemDataRole.UserRole + 1) or {}
        if not isinstance(meta, dict):
            meta = {}
        # Opposite of current LTX membership; colors/quotas fully sync on Ctrl+S.
        currently_in = bool(meta.get("in_ltx"))
        prev_ov = get_item_ltx_override(self.balance, sec)
        if prev_ov is None:
            meta["auto_in"] = currently_in
        ov = toggle_item_ltx_override(
            self.balance, sec, natural_in_shop=currently_in
        )
        if ov == "include":
            in_ltx = True
        elif ov == "exclude":
            in_ltx = False
        else:
            in_ltx = bool(meta.get("auto_in", meta.get("in_shop")))
        meta["override"] = ov
        meta["in_ltx"] = in_ltx
        item.setData(Qt.ItemDataRole.UserRole + 1, meta)
        self._mark_balance_dirty()
        if item is self.list.currentItem():
            sel_mode: bool | str = "primary"
        elif sec == self._compare_sec():
            sel_mode = "compare"
        else:
            sel_mode = False
        try:
            item.setIcon(
                _icon_with_pts(
                    meta.get("thumb"),
                    int(meta.get("pts") or 0),
                    in_shop=bool(meta.get("in_shop")),
                    faction_blocked=bool(meta.get("faction_blocked")),
                    under_pts=bool(meta.get("under_pts", True)),
                    selected=sel_mode,
                    name=str(meta.get("name") or ""),
                    faction_tag=str(meta.get("faction_tag") or ""),
                    in_ltx=in_ltx,
                    override=ov,
                )
            )
        except Exception:  # noqa: BLE001
            log.exception("include toggle icon refresh failed sec=%s", sec)
        tip = item.toolTip() or ""
        base = tip.split("\n")
        if len(base) >= 3:
            pts_line = base[2].split("  |  ")[0]
            if ov == "exclude":
                note = "  |  force-excluded from LTX"
            elif ov == "include":
                note = "  |  force-included in LTX"
            else:
                note = ""
            base[2] = (
                f"{pts_line}  |  in_ltx={in_ltx}  "
                f"eligible={bool(meta.get('in_shop'))}{note}"
            )
            base = base[:3] + ["Checkbox: empty=auto · click=force opposite"]
            item.setToolTip("\n".join(base[:4]))
        if item is self.list.currentItem():
            self._populate_detail(item)
        log.debug(
            "item ltx override sec=%s ov=%s was_in=%s in_ltx=%s",
            sec,
            ov,
            currently_in,
            in_ltx,
        )

    def _deselect_list_item(self) -> None:
        """Clear primary grid selection + detail panel."""
        if self.list.currentItem() is None and not self.list.selectedItems():
            return
        self.list.clearSelection()
        self.list.setCurrentItem(None)

    def _deselect_all(self) -> None:
        """Escape: clear compare + primary selection."""
        self._clear_compare(refresh_detail=False)
        self._deselect_list_item()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if isinstance(event, QMouseEvent):
            et = event.type()
            # Right-click a tile (with a primary selected) → compare (red).
            if (
                et == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.RightButton
            ):
                if obj is self.list or obj is self.list.viewport():
                    pos = event.position().toPoint()
                    hit = self.list.itemAt(pos)
                    primary = self.list.currentItem()
                    if hit is not None and primary is not None:
                        if hit is primary:
                            self._clear_compare(refresh_detail=True)
                        else:
                            self._set_compare_item(hit)
                    return True  # no list context menu / rubber-band
            # Checkbox clicks on item tiles — consume so selection does not change.
            if obj is self.list.viewport() and et in (
                QEvent.Type.MouseButtonPress,
                QEvent.Type.MouseButtonDblClick,
            ):
                if event.button() == Qt.MouseButton.LeftButton:
                    pos = event.position().toPoint()
                    item = self._item_for_checkbox_click(pos)
                    if item is not None:
                        if et == QEvent.Type.MouseButtonPress:
                            self._toggle_item_included(item)
                        return True
        return super().eventFilter(obj, event)

    def _on_select(
        self, cur: QListWidgetItem | None, prev: QListWidgetItem | None
    ) -> None:
        cmp_sec = self._compare_sec()
        if prev is not None:
            prev_sec = prev.data(Qt.ItemDataRole.UserRole)
            if cmp_sec and prev_sec == cmp_sec:
                self._set_item_selected_icon(prev, selected="compare")
            else:
                self._set_item_selected_icon(prev, selected=False)
        if cur is not None:
            cur_sec = cur.data(Qt.ItemDataRole.UserRole)
            if cmp_sec and cur_sec == cmp_sec:
                # Promoting compare → primary clears the compare slot.
                self._compare_by_cat[self.category] = None
            self._set_item_selected_icon(cur, selected="primary")
        if not cur:
            self._sel_by_cat[self.category] = None
            self._highlight_weapon_ammos(None)
            self._clear_detail()
            return
        self._sel_by_cat[self.category] = cur.data(Qt.ItemDataRole.UserRole)
        self._populate_detail(cur)

    def _stat_terms_for(
        self, sec: str, entry: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, tuple[float, float]], Any, Any]:
        """Return (stats_dict, terms, cost_v, pts)."""
        stats = dict(entry.get("stats") or {})
        cost_v = entry.get("cost")
        if cost_v is None:
            cost_v = stats.get("cost")
        stats["cost"] = cost_v
        cfg = effective_category(self.balance, self.faction, self.category)
        wmap = cfg.get("weights") or {}
        ceilings = cfg.get("ceilings") or {}
        curves = cfg.get("curves") or {}
        if self.category == "weapons":
            terms = weapon_stat_terms(stats, wmap, ceilings, curves)
        else:
            terms = armor_stat_terms(
                stats,
                wmap,
                is_helmet=self.category == "helmets",
                ceilings=ceilings,
                curves=curves,
            )
        pts, *_ = self._row_pts_inshop(sec, entry)
        return stats, terms, cost_v, pts

    def _populate_detail(self, cur: QListWidgetItem) -> None:
        sec = cur.data(Qt.ItemDataRole.UserRole)
        if not sec:
            self._clear_detail()
            return
        try:
            entry = (self.items.get(self.category) or {}).get(sec) or {}
            pts, in_shop, fac_blocked, under_pts = self._row_pts_inshop(
                sec, entry
            )
            ammos = [
                str(a).strip()
                for a in (entry.get("ammo_class") or [])
                if str(a).strip()
            ]
            if self.category == "weapons":
                self._highlight_weapon_ammos(set(ammos))
            else:
                self._highlight_weapon_ammos(None)
            ammo_s = (
                ", ".join(ammo_section_label(a) for a in ammos) if ammos else "—"
            )
            ov = get_item_ltx_override(self.balance, str(sec))
            meta = cur.data(Qt.ItemDataRole.UserRole + 1) or {}
            if isinstance(meta, dict) and "in_ltx" in meta:
                in_ltx = bool(meta.get("in_ltx"))
            else:
                in_ltx = item_in_ltx(in_shop, ov)
            if in_ltx or ov == "include":
                shop_s = "force-in" if ov == "include" else "true"
                shop_c = "#7dcea0"
            elif under_pts and fac_blocked:
                shop_s = "blocked"
                shop_c = "#96646a"  # muted red (faction / ammo lock)
            elif under_pts:
                shop_s = "force-out" if ov == "exclude" else "false"
                shop_c = "#96826a"  # muted orange (kind cap / allow / etc.)
            else:
                shop_s = "force-out" if ov == "exclude" else "false"
                shop_c = "#aab2bf"
            info_rows: list[tuple[str, list[str], str]] = [
                ("sec", [str(sec)], "#aab2bf"),
                ("name", [_nice_item_name(str(sec), entry)], "#aab2bf"),
                ("in_shop", [shop_s], shop_c),
                ("community", [str(entry.get("community") or "—")], "#aab2bf"),
            ]
            cmp_sec = self._compare_sec()
            cmp_entry: dict[str, Any] | None = None
            if cmp_sec:
                cmp_entry = (self.items.get(self.category) or {}).get(
                    cmp_sec
                ) or {}
                info_rows.append(
                    (
                        "compare",
                        [_nice_item_name(str(cmp_sec), cmp_entry)],
                        "#e07070",
                    )
                )
            if self.category == "weapons":
                info_rows.append(
                    (
                        "kind",
                        [kind_label(weapon_kind(entry, str(sec)))],
                        "#aab2bf",
                    )
                )
                info_rows.append(("ammo", [ammo_s], "#5ec8ff"))
            self._fill_kv_table(self.detail_info, info_rows)

            stats, terms, cost_v, _pts = self._stat_terms_for(str(sec), entry)
            cmp_stats: dict[str, Any] | None = None
            cmp_terms: dict[str, tuple[float, float]] | None = None
            cmp_pts: int | None = None
            if cmp_sec and cmp_entry is not None:
                cmp_stats, cmp_terms, _cc, cmp_pts = self._stat_terms_for(
                    str(cmp_sec), cmp_entry
                )

            def _wf(
                key: str, tmap: dict[str, tuple[float, float]]
            ) -> tuple[str, str, float | None, bool, float | None]:
                pair = tmap.get(key)
                if not pair:
                    return "—", "—", None, False, None
                w, final = pair
                excluded = w <= 0
                n01 = (final / w) if w > 0 else None
                return f"{w:.2f}", f"{final:.3f}", n01, excluded, float(final)

            def _raw_num(key: str, st: dict[str, Any], cost: Any) -> float | None:
                if key == "cost":
                    v = cost
                elif key == "hit_power":
                    v = hit_power_pct(st.get(key))
                elif key == "pts":
                    return None
                else:
                    v = st.get(key)
                try:
                    return float(v)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    return None

            # Build rows; Diff = primary − compare (Final when weighted, else Value).
            draft: list[dict[str, Any]] = []

            def _add_row(
                key: str,
                vals: list[str],
                name_c: str,
                cell_colors: list[str | None],
                diff_v: float | None,
                diff_a: float | None = None,
                diff_b: float | None = None,
            ) -> None:
                draft.append(
                    {
                        "key": key,
                        "vals": vals,
                        "name_c": name_c,
                        "cell_colors": cell_colors,
                        "diff_v": diff_v,
                        "diff_a": diff_a,
                        "diff_b": diff_b,
                    }
                )

            if cmp_pts is not None:
                _add_row(
                    "pts",
                    [str(int(pts)), "—", "—", ""],
                    "#7dcea0",
                    [None, None, None, None],
                    float(pts - cmp_pts),
                    float(pts),
                    float(cmp_pts),
                )
            else:
                _add_row(
                    "pts",
                    [str(int(pts)), "—", "—", ""],
                    "#7dcea0",
                    [None, None, None, None],
                    None,
                )
            seen: set[str] = set()
            if self.category == "weapons":
                ordered = [k for k, *_ in WEAPON_WEIGHTS]
            else:
                ordered = ["cost"] + [k for k, *_ in ARMOR_WEIGHTS]
            for key in ordered:
                seen.add(key)
                w_s, f_s, n01, excluded, final_v = _wf(key, terms)
                if key == "cost":
                    raw = cost_v
                elif key == "hit_power":
                    raw = hit_power_pct(stats.get(key))
                else:
                    raw = stats.get(key)
                if excluded:
                    cell_colors: list[str | None] = [
                        None,
                        _STAT_EXCLUDED_FG,
                        _STAT_EXCLUDED_FG,
                        None,
                    ]
                else:
                    cell_colors = [
                        None,
                        None,
                        _n01_color(n01) if n01 is not None else None,
                        None,
                    ]
                diff_v: float | None = None
                diff_a: float | None = None
                diff_b: float | None = None
                if cmp_terms is not None and cmp_stats is not None:
                    _cw, _cf, _cn, _ce, c_final = _wf(key, cmp_terms)
                    if final_v is not None and c_final is not None:
                        diff_a, diff_b = final_v, c_final
                        diff_v = final_v - c_final
                    else:
                        a = _raw_num(key, stats, cost_v)
                        b = _raw_num(
                            key, cmp_stats, cmp_stats.get("cost")
                        )
                        if a is not None and b is not None:
                            diff_a, diff_b, diff_v = a, b, a - b
                _add_row(
                    key,
                    [_fmt_stat_val(raw), w_s, f_s, ""],
                    _stat_color(key),
                    cell_colors,
                    diff_v,
                    diff_a,
                    diff_b,
                )
            for key in sorted(
                k
                for k in stats.keys()
                if k not in seen and k not in ("is_helmet", "col_src")
            ):
                diff_v = None
                diff_a = None
                diff_b = None
                if cmp_stats is not None:
                    a = _raw_num(key, stats, cost_v)
                    b = _raw_num(key, cmp_stats, cmp_stats.get("cost"))
                    if a is not None and b is not None:
                        diff_a, diff_b, diff_v = a, b, a - b
                _add_row(
                    key,
                    [_fmt_stat_val(stats[key]), "—", "—", ""],
                    _stat_color(key),
                    [None, None, None, None],
                    diff_v,
                    diff_a,
                    diff_b,
                )

            stat_rows: list[tuple] = []
            for r in draft:
                vals = list(r["vals"])
                colors = list(r["cell_colors"])
                dv = r["diff_v"]
                if dv is None or cmp_sec is None:
                    vals[3] = "—"
                    colors[3] = _STAT_EXCLUDED_FG
                else:
                    vals[3] = f"{dv:+.3f}"
                    a = r.get("diff_a")
                    b = r.get("diff_b")
                    if a is not None and b is not None:
                        n = _signed_diff_norm(float(a), float(b))
                    else:
                        n = 0.0 if abs(dv) < 1e-12 else (1.0 if dv > 0 else -1.0)
                    colors[3] = _signed_diff_color(n)
                stat_rows.append(
                    (r["key"], vals, r["name_c"], colors)
                )
            self._fill_kv_table(self.detail_stats, stat_rows)
            log.debug(
                "select %s pts=%s in_shop=%s compare=%s",
                sec,
                pts,
                in_shop,
                cmp_sec,
            )
        except Exception:
            log.exception("select failed sec=%s", sec)

    def _regen(self) -> None:
        paths = self._prompt_regen_paths()
        if paths is None:
            return
        anomaly, gamma = paths
        if not gamma and not anomaly:
            QMessageBox.warning(self, "SALE", "Set Anomaly and/or GAMMA roots first.")
            return
        log.info("Regenerate Scan anomaly=%s gamma=%s", anomaly, gamma)
        self.btn_regen.setEnabled(False)
        self.status.setText("Regenerating…")
        self._worker = RegenWorker(anomaly, gamma)
        self._worker.progress.connect(self._regen_progress)
        self._worker.finished_ok.connect(self._regen_done)
        self._worker.failed.connect(self._regen_fail)
        self._worker.start()

    def _regen_progress(self, msg: str, cur: int, total: int) -> None:
        self.status.setText(f"{msg}  ({cur}/{total})")
        if cur == 1 or cur == total or (total and cur % 100 == 0):
            log.debug("regen progress %s %s/%s", msg, cur, total)

    def _regen_done(self, path: str) -> None:
        self.btn_regen.setEnabled(True)
        try:
            self.items = load_items(Path(path))
            meta = (self.items or {}).get("meta") or {}
            log.info("regen done path=%s counts=%s", path, meta.get("counts"))
            self.status.setText("Scan complete")
            self._refresh_sort_options()
            self._rebuild_ammo_toggles()
            self._rebuild_kind_limit_boxes()
            self._rebuild_list()
        except Exception as exc:  # noqa: BLE001
            log.exception("post-regen load/rebuild failed")
            QMessageBox.critical(
                self,
                "Regenerate load failed",
                f"{exc}\n\nSee log:\n{LOG_PATH}",
            )

    def _regen_fail(self, err: str) -> None:
        self.btn_regen.setEnabled(True)
        self.status.setText("Regenerate failed — see Log tab")
        log.error("regen UI fail: %s", err.splitlines()[0] if err else "?")
        QMessageBox.critical(self, "Regenerate failed", err)

    def _reload_items(self) -> None:
        log.info("Reload YML")
        try:
            self.items = load_items()
            meta = (self.items or {}).get("meta") or {}
            log.info("reloaded counts=%s", meta.get("counts"))
            self._refresh_sort_options()
            self._rebuild_ammo_toggles()
            self._rebuild_kind_limit_boxes()
            self._rebuild_list()
        except Exception as exc:  # noqa: BLE001
            log.exception("reload failed")
            QMessageBox.critical(self, "Reload failed", f"{exc}\n\n{LOG_PATH}")

    def _prepare_export(self) -> bool:
        """Save + recalc first, then allow LTX write. Returns False if blocked/no items."""
        if not self.items:
            QMessageBox.warning(self, "Export", "No items.yml — Regenerate first.")
            return False
        return self._save_now()

    def _write_ltx(self, path: Path, *, notify: bool) -> None:
        try:
            if self._recalc_running:
                self.status.setText("Export blocked — wait for recalc…")
                return
            self._set_window_busy(True)
            try:
                export_shop_ltx(self.items, self.balance, path)
            finally:
                self._set_window_busy(False)
            log.info("Export wrote %s", path)
            self.status.setText(f"Exported → {path}")
            if notify:
                QMessageBox.information(self, "Export", f"Wrote:\n{path}")
        except Exception as exc:  # noqa: BLE001
            log.exception("export failed path=%s", path)
            QMessageBox.critical(
                self,
                "Export failed",
                f"{exc}\n\nSee log:\n{LOG_PATH}",
            )

    def _generate_ltx(self) -> None:
        """Ctrl+E: save + recalc, then write loadout LTX to the default repo path."""
        if not self._prepare_export():
            return
        dest = default_export_path()
        log.info("Ctrl+E generate LTX → %s", dest)
        self._write_ltx(dest, notify=False)
        self.status.setText(f"Saved + exported → {dest}")

    def _export(self) -> None:
        if not self._prepare_export():
            return
        dest = default_export_path()
        log.info("Export dialog default=%s", dest)
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export new_game_loadouts DLTX",
            str(dest),
            "LTX (*.ltx)",
        )
        if not path:
            log.info("Export cancelled")
            return
        self._write_ltx(Path(path), notify=True)

    def _deploy_dialog_start(self) -> str:
        source = default_export_path()
        remembered = get_deploy_target(self.settings, source)
        if remembered is not None:
            if remembered.is_file() or remembered.parent.is_dir():
                return str(remembered)
        saved = Path(str(self.settings.get("last_deploy_dir") or ""))
        if saved.is_dir():
            return str(saved / source.name)
        gamma = Path(str(self.settings.get("gamma_root") or "")).expanduser()
        dogma_cfg = gamma / "mods" / "DOGMA" / "gamedata" / "configs"
        if dogma_cfg.is_dir():
            return str(dogma_cfg / source.name)
        return str(source)

    def _deploy(self) -> bool:
        """Ctrl+D: generate LTX and overwrite remembered deploy target."""
        if not self._prepare_export():
            return False
        source = default_export_path()
        target = get_deploy_target(self.settings, source)
        if target is None:
            return self._deploy_as()
        return self._deploy_to(target)

    def _deploy_as(self) -> bool:
        """Ctrl+Shift+D: pick (or re-pick) deploy overwrite target, then deploy."""
        if not self._prepare_export():
            return False
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Deploy loadout LTX as",
            self._deploy_dialog_start(),
            "LTX (*.ltx);;All (*.*)",
        )
        if not path:
            log.info("Deploy As cancelled")
            return False
        return self._deploy_to(Path(path))

    def _deploy_to(self, target: Path) -> bool:
        """Generate repo LTX then copy it onto ``target`` (SAGE-style deploy)."""
        source = default_export_path()
        try:
            if self._recalc_running:
                self.status.setText("Deploy blocked — wait for recalc…")
                return False
            self._set_window_busy(True)
            try:
                export_shop_ltx(self.items, self.balance, source)
                target = target.expanduser()
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            finally:
                self._set_window_busy(False)
            set_deploy_target(self.settings, source, target)
            save_settings(self.settings)
            log.info("Deployed %s → %s", source, target)
            self.status.setText(f"Deployed → {target}")
            return True
        except Exception as exc:  # noqa: BLE001
            log.exception("deploy failed target=%s", target)
            QMessageBox.critical(
                self,
                "Deploy failed",
                f"{exc}\n\nSee log:\n{LOG_PATH}",
            )
            return False


_DARK_BG = QColor(18, 18, 20)
_DARK_PANEL = QColor(30, 30, 34)
_DARK_BASE = QColor(30, 30, 34)
_DARK_TEXT = QColor(212, 212, 212)
_DARK_DISABLED = QColor(120, 120, 128)
_DARK_HIGHLIGHT = QColor(38, 79, 120)
_DARK_MID = QColor(50, 50, 56)
_GREY_INAPPLICABLE = "#555555"


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

        # Ensure native HWND exists before DWM attribute (safe while still hidden).
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
    font = QFont("Consolas")
    font.setFamilies(["Consolas", "Cascadia Mono", "Courier New"])
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPointSize(9)
    return font


def _checkbox_check_image_url() -> str:
    dest = CACHE / "ui" / "checkbox_check.png"
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
        "QCheckBox::indicator, QListWidget::indicator {"
        "  width: 14px; height: 14px;"
        "  border: 1px solid #3A3A40;"
        "  border-radius: 2px;"
        "  background-color: #0E0E10;"
        "}"
        "QCheckBox::indicator:hover, QListWidget::indicator:hover {"
        "  background-color: #161618; border-color: #55555C;"
        "}"
        "QCheckBox::indicator:disabled {"
        "  background-color: #0A0A0C; border-color: #2A2A30;"
        "}"
        "QCheckBox::indicator:checked, QListWidget::indicator:checked {"
        "  background-color: #0E0E10;"
        "  border-color: #3A3A40;"
        f"  image: url({check_img});"
        "}"
        "QCheckBox::indicator:checked:hover, QListWidget::indicator:checked:hover {"
        "  background-color: #161618; border-color: #55555C;"
        f"  image: url({check_img});"
        "}"
        "QCheckBox::indicator:checked:disabled {"
        "  background-color: #0A0A0C; border-color: #2A2A30;"
        f"  image: url({check_img});"
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
    log.info("starting SALE argv=%s", argv)
    try:
        # Before the first widget exists so the HWND isn't created light.
        QApplication.setStyle("Fusion")
        app = QApplication(argv)
        _apply_dark_theme(app)
        app.setApplicationName("SALE — Stalker Anomaly Loadout Editor")
        win = MainWindow()
        # Opaque dark fill before show — avoids the white first frame on Windows.
        win.setAutoFillBackground(True)
        win.setPalette(_dark_palette())
        win.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        host = getattr(win, "ui_host", None)
        if host is not None:
            host.setAutoFillBackground(True)
            host.setPalette(_dark_palette())
            host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # DWM dark frame before first paint (winId creates HWND while still hidden).
        _apply_windows_dark_titlebar(win)
        win.show()
        _apply_windows_dark_titlebar(win)
        app.processEvents()
        log.info("entering event loop")
        code = app.exec()
        log.info("event loop exited code=%s", code)
        return code
    except Exception:
        log.critical("main() crashed\n%s", traceback.format_exc())
        raise


if __name__ == "__main__":
    raise SystemExit(main())
