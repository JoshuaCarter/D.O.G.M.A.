"""SALE — Stalker Anomaly Loadout Editor main window."""

from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QFont,
    QIcon,
    QKeySequence,
    QPainter,
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
    is_ammo_family_enabled,
    is_overridden,
    load_balance,
    override_count,
    save_balance,
    set_ammo_family_enabled,
    set_ceiling,
    set_curve,
    set_override,
)
from .diaglog import LOG_PATH, attach_log_view, get_logger, setup_logging
from .export_ltx import default_export_path, export_shop_ltx
from .labels import pretty_ceiling_tip, pretty_label, pretty_tip
from .regenerate import load_items, regenerate
from .spawn_filter import is_explosive_weapon, is_gauss_weapon, name_blocked
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
    default_ceilings_armor,
    faction_label,
    hit_power_pct,
    normalize_curve,
    weapon_ammo_allowed,
    weapon_pts,
    weapon_stat_terms,
)
from .settings import THUMBS_DIR, load_settings, save_settings

log = get_logger("app")

CATS = ("weapons", "outfits", "helmets")
# Item tile; cell is icon + half the previous inter-box gutter (was +24×+30).
# Display size in the icon grid (must fit inside GRID_CELL_*).
# Display size; cached weapon/ammo thumbs are 4× inv_grid, armor 2× — scaled to fit.
GRID_ICON_W = 200
GRID_ICON_H = 100
GRID_CELL_W = 212
GRID_CELL_H = 115

# Stat name colors (CSS) by key family.
_DMG_KEYS = (
    "hit_power",
    "dmg",
    "dps",
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
    if any(p in k for p in _DMG_KEYS):
        return "#ff7a45"  # orange-red
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


def _icon_with_pts(
    thumb: str | None,
    pts: int,
    *,
    in_shop: bool,
    faction_blocked: bool = False,
    selected: bool = False,
    name: str = "",
    width: int = GRID_ICON_W,
    height: int = GRID_ICON_H,
) -> QIcon:
    canvas = QPixmap(width, height)
    if selected:
        canvas.fill(QColor(36, 72, 120))  # subtle blue behind texture
    elif in_shop:
        canvas.fill(QColor(28, 30, 32))
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
        # Green = in shop; dark orange = ammo/faction filtered; grey = over threshold.
        # Selection only changes the canvas fill above — keep border/pts colors.
        if in_shop:
            border = QColor(40, 120, 70)
            pts_color = QColor(90, 220, 120)
        elif faction_blocked:
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
        if name:
            name_font = QFont("Consolas", 8)
            painter.setFont(name_font)
            nm = painter.fontMetrics()
            text = nm.elidedText(name, Qt.TextElideMode.ElideRight, width - 8)
            # Text only (no full-width bar); pts badge keeps its own black bg.
            y = height - 4
            painter.setPen(QColor(230, 230, 230))
            painter.drawText(4, y, text)
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


class FineStepSlider(QSlider):
    """Horizontal slider whose wheel moves one singleStep and doesn't scroll parents."""

    def wheelEvent(self, event) -> None:  # noqa: N802
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
        slider_max: int | None = None,
        show_curve: bool = False,
        curve: str = CURVE_LINEAR,
    ) -> None:
        super().__init__()
        self.key = key
        self.is_weight = is_weight
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
        self.btn = QPushButton("↺")
        self.btn.setFixedWidth(28)
        self.btn.setEnabled(overridden)
        self.btn.setToolTip("Clear override (use Default)")
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
        try:
            self.settings = load_settings()
            w = int(self.settings.get("window_w") or 1600)
            h = int(self.settings.get("window_h") or 900)
            self.resize(max(800, w), max(500, h))
            if self.settings.get("window_maximized"):
                self.showMaximized()
            log.info(
                "settings loaded anomaly=%r gamma=%r size=%sx%s max=%s",
                self.settings.get("anomaly_root"),
                self.settings.get("gamma_root"),
                w,
                h,
                bool(self.settings.get("window_maximized")),
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
            self._rows: list[tuple[str, dict[str, Any], int, bool, bool]] = []
            self._sel_by_cat: dict[str, str | None] = {c: None for c in CATS}
            saved_sel = self.settings.get("selection") or {}
            if isinstance(saved_sel, dict):
                for c in CATS:
                    if saved_sel.get(c):
                        self._sel_by_cat[c] = str(saved_sel[c])
            self._worker: RegenWorker | None = None
            # Coalesce rapid weight/ceiling slider edits into one save + list rebuild.
            self._balance_dirty = False
            self._slider_drag_depth = 0
            self._score_refresh_timer = QTimer(self)
            self._score_refresh_timer.setSingleShot(True)
            self._score_refresh_timer.timeout.connect(self._on_score_refresh_timeout)

            root = QWidget()
            self.setCentralWidget(root)
            v = QVBoxLayout(root)

            actions = QHBoxLayout()
            self.btn_regen = QPushButton("Regenerate")
            self.btn_regen.clicked.connect(self._regen)
            self.btn_load = QPushButton("Reload YML")
            self.btn_load.clicked.connect(self._reload_items)
            self.btn_export = QPushButton("Export LTX")
            self.btn_export.clicked.connect(self._export)
            self.btn_open_log = QPushButton("Open log")
            self.btn_open_log.clicked.connect(self._open_log)
            self.sort_box = QComboBox()
            self.sort_box.setMinimumWidth(140)
            self.sort_box.currentTextChanged.connect(self._on_sort_key_changed)
            self.sort_desc = QCheckBox("Desc")
            self.sort_desc.setChecked(False)
            self.sort_desc.setToolTip("Sort descending (off = ascending)")
            self.sort_desc.setStyleSheet("QCheckBox { color: #d0d0d0; }")
            self.sort_desc.toggled.connect(lambda _: self._rebuild_list())
            actions.addWidget(self.btn_regen)
            actions.addWidget(self.btn_load)
            actions.addWidget(self.btn_export)
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

            SIDEBAR_W = 400
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
                ["Stat", "Value", "Weight", "Final"]
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
                if (
                    cell_colors
                    and c - 1 < len(cell_colors)
                    and cell_colors[c - 1]
                ):
                    fg = cell_colors[c - 1]
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
        self._rebuild_list()

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
        self._rebuild_list()

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
            self._flush_score_refresh(rebuild=False)
            self._persist_ui_state()
            save_settings(self.settings)
            save_balance(self.balance)
            log.info(
                "saved on close balance=%s settings=%s",
                "ok",
                "ok",
            )
        except Exception:  # noqa: BLE001
            log.exception("save on close failed")
        super().closeEvent(event)

    def _schedule_score_refresh(self) -> None:
        """Debounce balance save + full list/pts rebuild after slider edits."""
        self._balance_dirty = True
        if self._slider_drag_depth > 0:
            # Wait for mouse-up; release handler will schedule.
            if self._score_refresh_timer.isActive():
                self._score_refresh_timer.stop()
            return
        self._score_refresh_timer.start(1000)

    def _flush_score_refresh(self, *, rebuild: bool = True) -> None:
        """Apply any pending debounced save (and optionally rebuild now)."""
        if self._score_refresh_timer.isActive():
            self._score_refresh_timer.stop()
        if self._balance_dirty:
            try:
                save_balance(self.balance)
            except Exception:  # noqa: BLE001
                log.exception("debounced balance save failed")
            self._balance_dirty = False
        if rebuild:
            self._rebuild_list()

    def _on_score_refresh_timeout(self) -> None:
        if self._slider_drag_depth > 0:
            return
        self._flush_score_refresh(rebuild=True)

    def _on_slider_drag_start(self) -> None:
        self._slider_drag_depth += 1
        if self._score_refresh_timer.isActive():
            self._score_refresh_timer.stop()

    def _on_slider_drag_end(self) -> None:
        self._slider_drag_depth = max(0, self._slider_drag_depth - 1)
        if self._slider_drag_depth == 0 and self._balance_dirty:
            self._schedule_score_refresh()

    def _wire_slider_row(self, row: WeightRow) -> None:
        row.drag_started.connect(self._on_slider_drag_start)
        row.drag_ended.connect(self._on_slider_drag_end)

    def _on_faction(self, _name: str) -> None:
        self._flush_score_refresh(rebuild=False)
        data = self.faction_box.currentData()
        fac = str(data if data is not None else _name)
        log.debug("faction -> %s (%s)", fac, faction_label(fac))
        self.faction = fac
        n = override_count(self.balance, fac)
        self.faction_meta.setText(f"({n} overrides)" if n else "")
        self._rebuild_weights()
        self._rebuild_ammo_toggles()
        self._rebuild_list()

    def _on_tab(self, idx: int) -> None:
        self._flush_score_refresh(rebuild=False)
        # Stash selection under the category we're leaving.
        cur = self.list.currentItem()
        if cur is not None:
            self._sel_by_cat[self.category] = cur.data(Qt.ItemDataRole.UserRole)
        self.category = CATS[idx] if 0 <= idx < len(CATS) else "weapons"
        log.debug("category tab -> %s", self.category)
        self.ammo_panel.setVisible(self.category == "weapons")
        self._rebuild_weights()
        self._refresh_sort_options()
        self._rebuild_list()

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
        groups: list[tuple[str, list[str]]] = [
            (
                "NATO",
                [s for s in sections if ammo_family_bloc(ammo_family(s)) == "nato"],
            ),
            (
                "WARSAW",
                [s for s in sections if ammo_family_bloc(ammo_family(s)) == "wp"],
            ),
            (
                "OTHER",
                [
                    s
                    for s in sections
                    if ammo_family_bloc(ammo_family(s)) not in ("nato", "wp")
                ],
            ),
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
        self._schedule_score_refresh()

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
        self._schedule_score_refresh()

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
            # Rows are destroyed; clear any in-progress drag tracking.
            self._slider_drag_depth = 0
            self._clear_layout(self.weights_layout)
            self._clear_layout(self.ceilings_layout)
            fac = self.faction
            cat = self.category
            cfg = effective_category(self.balance, fac, cat)
            ceilings_locked = fac != "Default"

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
                )
                row.changed.connect(self._weight_changed)
                row.cleared.connect(self._weight_cleared)
                self._wire_slider_row(row)
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
                )
                row.changed.connect(self._weight_changed)
                row.cleared.connect(self._weight_cleared)
                self._wire_slider_row(row)
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
                    tip = f"{tip} (Default faction only — shared by all factions.)"
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
                self._wire_slider_row(row)
                self.ceilings_layout.addWidget(row)

            def add_toggle(key: str, checked: bool) -> None:
                ov = is_overridden(self.balance, fac, cat, key, weight=False)
                row = QWidget()
                lay = QHBoxLayout(row)
                lay.setContentsMargins(0, 2, 0, 4)
                lay.setSpacing(6)
                cb = QCheckBox(pretty_label(key))
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
                btn = QPushButton("↺")
                btn.setFixedWidth(28)
                btn.setEnabled(ov)
                btn.setToolTip("Clear override (use Default)")
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

            # Tab 2: Scale maxes (0–x) — Default-only; other factions inherit.
            ceil_note = "Normalization ceilings (0–x)"
            if ceilings_locked:
                ceil_note += " — Default only"
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
        if self.faction != "Default":
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
        self._schedule_score_refresh()

    def _ceiling_curve_changed(self, key: str, curve: str) -> None:
        if self.faction != "Default":
            return
        stat_key = key[8:] if key.startswith("ceiling:") else key
        log.debug(
            "ceiling curve set cat=%s key=%s curve=%s",
            self.category,
            stat_key,
            curve,
        )
        set_curve(self.balance, self.category, stat_key, curve)
        self._schedule_score_refresh()

    def _weight_changed(self, key: str, value: float, is_weight: bool) -> None:
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
            self._schedule_score_refresh()
            return
        # Orange = faction override only; Default edits are the baseline.
        if self.faction != "Default":
            for i in range(self.weights_layout.count()):
                w = self.weights_layout.itemAt(i).widget()
                if (
                    isinstance(w, WeightRow)
                    and w.key == key
                    and w.is_weight == is_weight
                ):
                    w.lbl.setStyleSheet("color: #e6a23c; font-weight: bold;")
                    w.btn.setEnabled(True)
        self._schedule_score_refresh()

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
        self._schedule_score_refresh()

    def _row_pts_inshop(
        self, sec: str, entry: dict[str, Any]
    ) -> tuple[int, bool, bool]:
        """Return (pts, in_shop, faction_blocked).

        ``faction_blocked`` means ammo-type toggle or outfit community excluded it
        (orange border). Ammo toggles are per-faction (all on by default).
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
            gear_ok = weapon_ammo_allowed(entry.get("ammo_class") or [], ammo_map)
            return pts, under and gear_ok, (not gear_ok)
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
            return pts, under, False
        from .score import FACTION_COMMUNITY

        want = FACTION_COMMUNITY.get(self.faction, self.faction)
        community = (entry.get("community") or "").strip()
        allow_univ = bool(cfg.get("include_universal_armor", True))
        gear_ok = community == want or (allow_univ and community in ("", "actor"))
        return pts, under and gear_ok, (not gear_ok)

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
            rows: list[tuple[str, dict[str, Any], int, bool, bool]] = []
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
                    pts, in_shop, fac_blocked = self._row_pts_inshop(sec, entry)
                except Exception:  # noqa: BLE001
                    log.exception("pts failed for %s", sec)
                    pts, in_shop, fac_blocked = 0, False, False
                rows.append((sec, entry, pts, in_shop, fac_blocked))

            sort_key = self._current_sort_key() or "pts"
            descending = self.sort_desc.isChecked()
            rows.sort(
                key=lambda r: self._row_sort_value(
                    r[0], r[1], r[2], r[3], sort_key
                ),
                reverse=descending,
            )

            self._rows = rows
            thumb_ok = 0
            restore_item: QListWidgetItem | None = None
            for sec, entry, pts, in_shop, fac_blocked in rows:
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
                is_sel = bool(want_sec and sec == want_sec)
                try:
                    item.setIcon(
                        _icon_with_pts(
                            thumb or None,
                            pts,
                            in_shop=in_shop,
                            faction_blocked=fac_blocked,
                            selected=is_sel,
                            name=label,
                        )
                    )
                except Exception:  # noqa: BLE001
                    log.exception("icon compose failed sec=%s", sec)
                tip = (
                    f"{sec}\n{entry.get('name') or ''}\n"
                    f"{pts} pts  |  in_shop={in_shop}"
                    f"{'  |  ammo type disabled' if fac_blocked else ''}"
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
                        "faction_blocked": fac_blocked,
                        "name": label,
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
            n_shop = sum(1 for r in rows if r[3])
            n_blocked = sum(1 for r in rows if r[4])
            self.status.setText(
                f"{len(rows)} {self.category}  "
                f"(faction={faction_label(self.faction)})  "
                f"in_shop={n_shop}  blocked={n_blocked}  {elapsed:.2f}s"
            )
            log.info(
                "rebuild_list cat=%s fac=%s rows=%d in_shop=%d blocked=%d thumbs=%d in %.3fs",
                self.category,
                self.faction,
                len(rows),
                n_shop,
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
        self, item: QListWidgetItem | None, *, selected: bool
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
                    selected=selected,
                    name=str(meta.get("name") or ""),
                )
            )
        except Exception:  # noqa: BLE001
            log.exception("selection icon refresh failed")

    def _on_select(
        self, cur: QListWidgetItem | None, prev: QListWidgetItem | None
    ) -> None:
        self._set_item_selected_icon(prev, selected=False)
        self._set_item_selected_icon(cur, selected=True)
        if not cur:
            self._highlight_weapon_ammos(None)
            return
        sec = cur.data(Qt.ItemDataRole.UserRole)
        self._sel_by_cat[self.category] = sec
        try:
            entry = (self.items.get(self.category) or {}).get(sec) or {}
            pts, in_shop, fac_blocked = self._row_pts_inshop(sec, entry)
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
            shop_s = "true" if in_shop else ("blocked" if fac_blocked else "false")
            shop_c = (
                "#7dcea0" if in_shop else ("#96826a" if fac_blocked else "#aab2bf")
            )
            info_rows: list[tuple[str, list[str], str]] = [
                ("sec", [str(sec)], "#aab2bf"),
                ("name", [_nice_item_name(str(sec), entry)], "#aab2bf"),
                ("in_shop", [shop_s], shop_c),
                ("community", [str(entry.get("community") or "—")], "#aab2bf"),
            ]
            if self.category == "weapons":
                info_rows.append(("ammo", [ammo_s], "#5ec8ff"))
            self._fill_kv_table(self.detail_info, info_rows)

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

            def _wf(key: str) -> tuple[str, str, float | None]:
                pair = terms.get(key)
                if not pair:
                    return "—", "—", None
                w, final = pair
                # Final column color tracks normalized 0–1 (final / weight).
                n01 = (final / w) if w > 0 else None
                return f"{w:.2f}", f"{final:.3f}", n01

            stat_rows: list[tuple] = [
                ("pts", [str(int(pts)), "—", "—"], "#7dcea0"),
            ]
            # Weighted stats first (cost included), then any leftover raw stats.
            seen: set[str] = set()
            if self.category == "weapons":
                ordered = [k for k, *_ in WEAPON_WEIGHTS]
            else:
                ordered = ["cost"] + [k for k, *_ in ARMOR_WEIGHTS]
            for key in ordered:
                seen.add(key)
                w_s, f_s, n01 = _wf(key)
                if key == "cost":
                    raw = cost_v
                elif key == "hit_power":
                    raw = hit_power_pct(stats.get(key))
                else:
                    raw = stats.get(key)
                cell_colors = [
                    None,
                    None,
                    _n01_color(n01) if n01 is not None else None,
                ]
                stat_rows.append(
                    (
                        key,
                        [_fmt_stat_val(raw), w_s, f_s],
                        _stat_color(key),
                        cell_colors,
                    )
                )
            for key in sorted(
                k
                for k in stats.keys()
                if k not in seen and k not in ("is_helmet", "col_src")
            ):
                stat_rows.append(
                    (
                        key,
                        [_fmt_stat_val(stats[key]), "—", "—"],
                        _stat_color(key),
                    )
                )
            self._fill_kv_table(self.detail_stats, stat_rows)
            log.debug("select %s pts=%s in_shop=%s", sec, pts, in_shop)
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
            self._rebuild_list()
        except Exception as exc:  # noqa: BLE001
            log.exception("reload failed")
            QMessageBox.critical(self, "Reload failed", f"{exc}\n\n{LOG_PATH}")

    def _export(self) -> None:
        if not self.items:
            QMessageBox.warning(self, "Export", "No items.yml — Regenerate first.")
            return
        self._flush_score_refresh(rebuild=False)
        self._persist_ui_state()
        save_settings(self.settings)
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
        try:
            export_shop_ltx(
                self.items,
                self.balance,
                Path(path),
            )
            log.info("Export wrote %s", path)
            self.status.setText(f"Exported → {path}")
            QMessageBox.information(self, "Export", f"Wrote:\n{path}")
        except Exception as exc:  # noqa: BLE001
            log.exception("export failed path=%s", path)
            QMessageBox.critical(
                self,
                "Export failed",
                f"{exc}\n\nSee log:\n{LOG_PATH}",
            )


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    log.info("starting SALE argv=%s", argv or sys.argv)
    try:
        app = QApplication(argv or sys.argv)
        win = MainWindow()
        win.show()
        log.info("entering event loop")
        code = app.exec()
        log.info("event loop exited code=%s", code)
        return code
    except Exception:
        log.critical("main() crashed\n%s", traceback.format_exc())
        raise


if __name__ == "__main__":
    raise SystemExit(main())
