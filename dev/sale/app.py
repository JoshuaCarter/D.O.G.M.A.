"""SALE — Stalker Anomaly Loadout Editor main window."""

from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QSize, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .balance import (
    clear_override,
    effective_category,
    is_overridden,
    load_balance,
    override_count,
    save_balance,
    set_override,
)
from .diaglog import LOG_PATH, attach_log_view, get_logger, setup_logging
from .export_ltx import default_export_path, export_shop_ltx
from .regenerate import load_items, regenerate
from .spawn_filter import name_blocked
from .thumbs import iter_texture_roots, make_thumb, set_texture_roots
from .score import (
    ARMOR_WEIGHTS,
    FACTIONS,
    WEAPON_WEIGHTS,
    armor_pts,
    armor_stat_terms,
    bloc_ok,
    weapon_bloc,
    weapon_pts,
    weapon_stat_terms,
)
from .settings import THUMBS_DIR, load_settings, save_settings

log = get_logger("app")

CATS = ("weapons", "outfits", "helmets")
# Item tile; cell is icon + half the previous inter-box gutter (was +24×+30).
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
    "gridline-color: #2a2a2a; alternate-background-color: #242424; }"
    f"QHeaderView::section {{ background: #2a2a2a; color: #ddd; border: none; "
    f"padding: 5px 8px; {_HEADER_FONT} text-align: left; }}"
)


def _header_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(_HEADER_LABEL_STYLE)
    return lbl


def _fmt_stat_val(val: Any) -> str:
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
    name: str = "",
    width: int = GRID_ICON_W,
    height: int = GRID_ICON_H,
) -> QIcon:
    canvas = QPixmap(width, height)
    canvas.fill(QColor(28, 30, 32) if in_shop else QColor(22, 22, 22))
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
        # Green = in shop; dark orange = calibre/faction filtered; grey = over threshold.
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


class WeightRow(QWidget):
    changed = pyqtSignal(str, float, bool)  # key, value, is_weight
    cleared = pyqtSignal(str, bool)

    def __init__(
        self, key: str, label: str, value: float, *, is_weight: bool, overridden: bool
    ) -> None:
        super().__init__()
        self.key = key
        self.is_weight = is_weight
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.lbl = QLabel(label)
        if overridden:
            self.lbl.setStyleSheet("color: #e6a23c; font-weight: bold;")
        lay.addWidget(self.lbl, 2)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        if is_weight:
            self.slider.setRange(0, 100)
            self.slider.setValue(int(round(float(value) * 100)))
        elif key == "include_universal_armor":
            self.slider.setRange(0, 1)
            self.slider.setValue(1 if value else 0)
        else:
            self.slider.setRange(1, 2000)
            self.slider.setValue(int(value))
        self.slider.valueChanged.connect(self._on_slide)
        lay.addWidget(self.slider, 3)
        self.val = QLineEdit(self._fmt(value))
        self.val.setFixedWidth(52)
        self.val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.val.setStyleSheet(
            "QLineEdit { background: #2a2a2a; color: #e8e8e8; border: 1px solid #444; "
            "padding: 1px 4px; font-family: Consolas, monospace; }"
        )
        self.val.editingFinished.connect(self._on_edit)
        lay.addWidget(self.val)
        self.btn = QPushButton("↺")
        self.btn.setFixedWidth(28)
        self.btn.setEnabled(overridden)
        self.btn.setToolTip("Clear override (use Default)")
        self.btn.clicked.connect(lambda: self.cleared.emit(self.key, self.is_weight))
        lay.addWidget(self.btn)

    def _fmt(self, v: float) -> str:
        return f"{v:.2f}" if self.is_weight else str(int(v))

    def _emit_value(self, val: float) -> None:
        self.changed.emit(self.key, float(val), self.is_weight)

    def _on_slide(self, v: int) -> None:
        if self.is_weight:
            val = v / 100.0
        elif self.key == "include_universal_armor":
            val = 1.0 if v else 0.0
        else:
            val = float(v)
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
        if self.is_weight:
            raw = max(0.0, min(1.0, raw))
            slider_v = int(round(raw * 100))
        elif self.key == "include_universal_armor":
            raw = 1.0 if raw >= 0.5 else 0.0
            slider_v = 1 if raw else 0
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
        if self.is_weight:
            return v / 100.0
        if self.key == "include_universal_armor":
            return 1.0 if v else 0.0
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
            self.faction = "Default"
            self.category = "weapons"
            self._rows: list[tuple[str, dict[str, Any], int, bool, bool]] = []
            self._sel_by_cat: dict[str, str | None] = {c: None for c in CATS}
            self._worker: RegenWorker | None = None

            root = QWidget()
            self.setCentralWidget(root)
            v = QVBoxLayout(root)

            # Top bar
            top = QHBoxLayout()
            self.anomaly_edit = QLineEdit(self.settings.get("anomaly_root", ""))
            self.gamma_edit = QLineEdit(self.settings.get("gamma_root", ""))
            top.addWidget(_header_label("Anomaly:"))
            top.addWidget(self.anomaly_edit, 2)
            b_a = QPushButton("…")
            b_a.clicked.connect(lambda: self._browse(self.anomaly_edit))
            top.addWidget(b_a)
            top.addWidget(_header_label("GAMMA:"))
            top.addWidget(self.gamma_edit, 2)
            b_g = QPushButton("…")
            b_g.clicked.connect(lambda: self._browse(self.gamma_edit))
            top.addWidget(b_g)
            v.addLayout(top)

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
            self.sort_dir_box = QComboBox()
            self.sort_dir_box.addItems(["Asc", "Desc"])
            self.sort_dir_box.currentTextChanged.connect(
                lambda _: self._rebuild_list()
            )
            self.filter_box = QComboBox()
            self.filter_box.addItems(["all", "in-shop", "over-threshold"])
            self.filter_box.currentTextChanged.connect(lambda _: self._rebuild_list())
            actions.addWidget(self.btn_regen)
            actions.addWidget(self.btn_load)
            actions.addWidget(self.btn_export)
            actions.addWidget(self.btn_open_log)
            actions.addWidget(_header_label("Sort:"))
            actions.addWidget(self.sort_box)
            actions.addWidget(self.sort_dir_box)
            actions.addWidget(_header_label("Filter:"))
            actions.addWidget(self.filter_box)
            actions.addStretch(1)
            v.addLayout(actions)

            fac = QHBoxLayout()
            fac.addWidget(_header_label("Faction:"))
            self.faction_box = QComboBox()
            self.faction_box.addItem("Default")
            for f in FACTIONS:
                self.faction_box.addItem(f)
            self.faction_box.currentTextChanged.connect(self._on_faction)
            fac.addWidget(self.faction_box)
            self.faction_meta = QLabel("")
            fac.addWidget(self.faction_meta)
            fac.addStretch(1)
            v.addLayout(fac)

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

            left_scroll = QScrollArea()
            left_scroll.setWidgetResizable(True)
            left_scroll.setFixedWidth(SIDEBAR_W)
            left_scroll.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            self.weights_host = QWidget()
            self.weights_layout = QVBoxLayout(self.weights_host)
            left_scroll.setWidget(self.weights_host)
            body.addWidget(left_scroll)

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
                "  background: #2a4a3a;"
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
                lambda idx: self._on_detail_sort_click(self.detail_info, idx.row())
            )
            detail_lay.addWidget(self.detail_info)
            self.detail_stats = self._make_kv_table(
                ["Stat", "Value", "Weight", "Final"]
            )
            self.detail_stats.clicked.connect(
                lambda idx: self._on_detail_sort_click(self.detail_stats, idx.row())
            )
            detail_lay.addWidget(self.detail_stats, 1)
            body.addWidget(detail_host)
            editor_v.addLayout(body, 1)

            self._configure_textures()

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
            self._rebuild_weights()
            self._refresh_sort_options(prefer="pts")
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
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
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
        table.setCursor(Qt.CursorShape.PointingHandCursor)
        return table

    def _fill_kv_table(
        self,
        table: QTableWidget,
        rows: list[tuple[str, list[str], str]],
    ) -> None:
        """rows: (key, [col values…], name_color)."""
        ncols = table.columnCount()
        table.setRowCount(len(rows))
        align_l = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        for row, (key, vals, color) in enumerate(rows):
            name_item = QTableWidgetItem(key)
            name_item.setForeground(QColor(color))
            font = name_item.font()
            font.setBold(True)
            name_item.setFont(font)
            name_item.setTextAlignment(align_l)
            name_item.setData(Qt.ItemDataRole.UserRole, key)
            name_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            )
            table.setItem(row, 0, name_item)
            for c in range(1, ncols):
                text = vals[c - 1] if c - 1 < len(vals) else ""
                cell = QTableWidgetItem(text)
                cell.setForeground(QColor("#e8e8e8"))
                cell.setTextAlignment(align_l)
                cell.setData(Qt.ItemDataRole.UserRole, key)
                cell.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                )
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

    def _refresh_sort_options(self, prefer: str | None = None) -> None:
        keys = self._sort_keys_for_category()
        cur = prefer or self.sort_box.currentText() or "pts"
        self.sort_box.blockSignals(True)
        self.sort_box.clear()
        self.sort_box.addItems(keys)
        idx = self.sort_box.findText(cur)
        self.sort_box.setCurrentIndex(idx if idx >= 0 else 0)
        self.sort_box.blockSignals(False)

    def _on_sort_key_changed(self, _text: str) -> None:
        self._rebuild_list()

    def _on_detail_sort_click(self, table: QTableWidget, row: int) -> None:
        item = table.item(row, 0)
        if not item:
            return
        key = str(item.data(Qt.ItemDataRole.UserRole) or item.text())
        if not key:
            return
        cur = self.sort_box.currentText()
        if cur == key:
            # Toggle Asc ↔ Desc.
            nxt = "Desc" if self.sort_dir_box.currentText() == "Asc" else "Asc"
            self.sort_dir_box.blockSignals(True)
            self.sort_dir_box.setCurrentText(nxt)
            self.sort_dir_box.blockSignals(False)
        else:
            if self.sort_box.findText(key) < 0:
                self.sort_box.blockSignals(True)
                self.sort_box.addItem(key)
                self.sort_box.blockSignals(False)
            self.sort_box.blockSignals(True)
            self.sort_box.setCurrentText(key)
            self.sort_box.blockSignals(False)
            self.sort_dir_box.blockSignals(True)
            self.sort_dir_box.setCurrentText("Asc")
            self.sort_dir_box.blockSignals(False)
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

    def _configure_textures(self) -> None:
        anomaly = (
            Path(self.anomaly_edit.text().strip())
            if self.anomaly_edit.text().strip()
            else None
        )
        gamma = (
            Path(self.gamma_edit.text().strip())
            if self.gamma_edit.text().strip()
            else None
        )
        set_texture_roots(iter_texture_roots(anomaly, gamma))

    def _save_window_geom(self) -> None:
        self.settings["window_maximized"] = self.isMaximized()
        if not self.isMaximized():
            self.settings["window_w"] = int(self.width())
            self.settings["window_h"] = int(self.height())

    def _save_roots(self) -> None:
        self.settings["anomaly_root"] = self.anomaly_edit.text().strip()
        self.settings["gamma_root"] = self.gamma_edit.text().strip()
        self._save_window_geom()
        save_settings(self.settings)
        self._configure_textures()
        log.debug(
            "roots saved anomaly=%r gamma=%r",
            self.settings["anomaly_root"],
            self.settings["gamma_root"],
        )

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            self._save_window_geom()
            save_settings(self.settings)
        except Exception:  # noqa: BLE001
            log.exception("save window size on close failed")
        super().closeEvent(event)

    def _on_faction(self, name: str) -> None:
        log.debug("faction -> %s", name)
        self.faction = name
        n = override_count(self.balance, name)
        self.faction_meta.setText(f"({n} overrides)" if n else "")
        self._rebuild_weights()
        self._rebuild_list()

    def _on_tab(self, idx: int) -> None:
        # Stash selection under the category we're leaving.
        cur = self.list.currentItem()
        if cur is not None:
            self._sel_by_cat[self.category] = cur.data(Qt.ItemDataRole.UserRole)
        self.category = CATS[idx] if 0 <= idx < len(CATS) else "weapons"
        log.debug("category tab -> %s", self.category)
        self._rebuild_weights()
        self._refresh_sort_options()
        self._rebuild_list()

    def _rebuild_weights(self) -> None:
        try:
            while self.weights_layout.count():
                item = self.weights_layout.takeAt(0)
                w = item.widget()
                if w:
                    w.deleteLater()
            fac = self.faction
            cat = self.category
            cfg = effective_category(self.balance, fac, cat)

            def add_scalar(key: str, label: str, value: float) -> None:
                ov = is_overridden(self.balance, fac, cat, key, weight=False)
                row = WeightRow(key, label, value, is_weight=False, overridden=ov)
                row.changed.connect(self._weight_changed)
                row.cleared.connect(self._weight_cleared)
                self.weights_layout.addWidget(row)

            def add_weight(key: str, label: str, value: float) -> None:
                ov = is_overridden(self.balance, fac, cat, key, weight=True)
                row = WeightRow(key, label, value, is_weight=True, overridden=ov)
                row.changed.connect(self._weight_changed)
                row.cleared.connect(self._weight_cleared)
                self.weights_layout.addWidget(row)

            add_scalar("max_pts", "max_pts (threshold)", float(cfg.get("max_pts") or 900))
            add_scalar("cost_mult", "cost_mult", float(cfg.get("cost_mult") or 1000))
            if cat == "outfits":
                univ = 1.0 if cfg.get("include_universal_armor", True) else 0.0
                add_scalar("include_universal_armor", "include_universal_armor", univ)

            weights = cfg.get("weights") or {}
            if cat == "weapons":
                for _sk, wkey, _c, _i, default_w in WEAPON_WEIGHTS:
                    add_weight(wkey, wkey, float(weights.get(wkey, default_w)))
            else:
                for _sk, wkey, _c, _i, default_w in ARMOR_WEIGHTS:
                    add_weight(wkey, wkey, float(weights.get(wkey, default_w)))
                add_weight("a_price", "a_price", float(weights.get("a_price", 0.5)))

            self.weights_layout.addStretch(1)
        except Exception:
            log.exception("_rebuild_weights failed fac=%s cat=%s", self.faction, self.category)
            raise

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
        save_balance(self.balance)
        n = override_count(self.balance, self.faction)
        self.faction_meta.setText(f"({n} overrides)" if n else "")
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
        self._rebuild_list()

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
        save_balance(self.balance)
        self._rebuild_weights()
        self._rebuild_list()

    def _row_pts_inshop(
        self, sec: str, entry: dict[str, Any]
    ) -> tuple[int, bool, bool]:
        """Return (pts, in_shop, faction_blocked).

        ``faction_blocked`` means calibre/community filter excluded it (border orange).
        """
        fac = self.faction if self.faction != "Default" else "stalker"
        cfg = effective_category(self.balance, self.faction, self.category)
        stats = entry.get("stats") or {}
        if self.category == "weapons":
            pts = weapon_pts(
                stats, cfg.get("weights") or {}, float(cfg.get("cost_mult") or 1000)
            )
            under = pts < float(cfg.get("max_pts") or 900)
            bloc = weapon_bloc(sec, ",".join(entry.get("ammo_class") or []))
            from .score import FACTION_BLOC

            gear_ok = bloc_ok(bloc, FACTION_BLOC.get(fac, "both"))
            return pts, under and gear_ok, (not gear_ok)
        is_helm = self.category == "helmets"
        pts = armor_pts(
            stats,
            cfg.get("weights") or {},
            float(cfg.get("cost_mult") or 1000),
            is_helmet=is_helm,
        )
        under = pts < float(cfg.get("max_pts") or 550)
        if is_helm:
            return pts, under, False
        from .score import FACTION_COMMUNITY

        want = FACTION_COMMUNITY.get(fac, fac)
        community = (entry.get("community") or "").strip()
        allow_univ = bool(cfg.get("include_universal_armor", True))
        gear_ok = community == want or (allow_univ and community in ("", "actor"))
        return pts, under and gear_ok, (not gear_ok)

    def _clear_detail(self) -> None:
        self._fill_kv_table(self.detail_info, [])
        self._fill_kv_table(self.detail_stats, [])

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
                try:
                    pts, in_shop, fac_blocked = self._row_pts_inshop(sec, entry)
                except Exception:  # noqa: BLE001
                    log.exception("pts failed for %s", sec)
                    pts, in_shop, fac_blocked = 0, False, False
                rows.append((sec, entry, pts, in_shop, fac_blocked))

            filt = self.filter_box.currentText()
            if filt == "in-shop":
                rows = [r for r in rows if r[3]]
            elif filt == "over-threshold":
                rows = [r for r in rows if not r[3]]

            sort_key = self.sort_box.currentText() or "pts"
            descending = self.sort_dir_box.currentText() == "Desc"
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
                short = sec
                for prefix in ("wpn_", "outfit_", "helm_", "helmet_"):
                    if short.startswith(prefix):
                        short = short[len(prefix) :]
                        break
                # Name is painted bottom-left on the tile (no under-icon label).
                label = short.replace("_", " ")
                item = QListWidgetItem("")
                # Real inv_grid crop when DDS roots are configured.
                thumb_path = THUMBS_DIR / f"{sec}.inv.png"
                if not thumb_path.is_file():
                    try:
                        make_thumb(sec, entry, THUMBS_DIR)
                    except Exception:  # noqa: BLE001
                        log.exception("lazy thumb %s", sec)
                thumb = ""
                for cand in (
                    THUMBS_DIR / f"{sec}.inv.png",
                    THUMBS_DIR / f"{sec}.fallback.png",
                ):
                    if cand.is_file():
                        thumb = str(cand)
                        break
                if thumb:
                    thumb_ok += 1
                try:
                    item.setIcon(
                        _icon_with_pts(
                            thumb or None,
                            pts,
                            in_shop=in_shop,
                            faction_blocked=fac_blocked,
                            name=label,
                        )
                    )
                except Exception:  # noqa: BLE001
                    log.exception("icon compose failed sec=%s", sec)
                tip = (
                    f"{sec}\n{entry.get('name') or ''}\n"
                    f"{pts} pts  |  in_shop={in_shop}"
                    f"{'  |  faction/calibre blocked' if fac_blocked else ''}"
                )
                item.setToolTip(tip)
                item.setData(Qt.ItemDataRole.UserRole, sec)
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
            elapsed = time.perf_counter() - t0
            self.status.setText(
                f"{len(rows)} {self.category}  (faction={self.faction})  "
                f"{elapsed:.2f}s"
            )
            log.info(
                "rebuild_list cat=%s fac=%s rows=%d in_shop=%d thumbs=%d in %.3fs",
                self.category,
                self.faction,
                len(rows),
                sum(1 for r in rows if r[3]),
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

    def _on_select(
        self, cur: QListWidgetItem | None, _prev: QListWidgetItem | None
    ) -> None:
        if not cur:
            return
        sec = cur.data(Qt.ItemDataRole.UserRole)
        self._sel_by_cat[self.category] = sec
        try:
            entry = (self.items.get(self.category) or {}).get(sec) or {}
            pts, in_shop, fac_blocked = self._row_pts_inshop(sec, entry)
            ammo = entry.get("ammo_class") or []
            ammo_s = ", ".join(str(a) for a in ammo) if ammo else "—"
            shop_s = "true" if in_shop else ("blocked" if fac_blocked else "false")
            shop_c = (
                "#7dcea0" if in_shop else ("#96826a" if fac_blocked else "#aab2bf")
            )
            info_rows: list[tuple[str, list[str], str]] = [
                ("sec", [str(sec)], "#aab2bf"),
                ("name", [str(entry.get("name") or sec)], "#aab2bf"),
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
            if self.category == "weapons":
                terms = weapon_stat_terms(stats, wmap)
            else:
                terms = armor_stat_terms(
                    stats, wmap, is_helmet=self.category == "helmets"
                )

            def _wf(key: str) -> tuple[str, str]:
                pair = terms.get(key)
                if not pair:
                    return "—", "—"
                w, final = pair
                return f"{w:.2f}", f"{final:.3f}"

            stat_rows: list[tuple[str, list[str], str]] = [
                ("pts", [str(int(pts)), "—", "—"], "#7dcea0"),
            ]
            # Weighted stats first (cost included), then any leftover raw stats.
            seen: set[str] = set()
            ordered = [k for k, *_ in (WEAPON_WEIGHTS if self.category == "weapons" else ARMOR_WEIGHTS)]
            if self.category != "weapons" and "cost" not in ordered:
                ordered.append("cost")
            for key in ordered:
                seen.add(key)
                w_s, f_s = _wf(key)
                raw = cost_v if key == "cost" else stats.get(key)
                stat_rows.append(
                    (key, [_fmt_stat_val(raw), w_s, f_s], _stat_color(key))
                )
            for key in sorted(k for k in stats.keys() if k not in seen):
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
        self._save_roots()
        anomaly = (
            Path(self.anomaly_edit.text().strip())
            if self.anomaly_edit.text().strip()
            else None
        )
        gamma = (
            Path(self.gamma_edit.text().strip())
            if self.gamma_edit.text().strip()
            else None
        )
        if not gamma and not anomaly:
            QMessageBox.warning(self, "SALE", "Set Anomaly and/or GAMMA roots first.")
            return
        log.info("Regenerate clicked anomaly=%s gamma=%s", anomaly, gamma)
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
            self.status.setText(f"Regenerated → {path}")
            self._refresh_sort_options()
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
            self._rebuild_list()
        except Exception as exc:  # noqa: BLE001
            log.exception("reload failed")
            QMessageBox.critical(self, "Reload failed", f"{exc}\n\n{LOG_PATH}")

    def _export(self) -> None:
        if not self.items:
            QMessageBox.warning(self, "Export", "No items.yml — Regenerate first.")
            return
        self._save_roots()
        gamma = (
            Path(self.gamma_edit.text().strip())
            if self.gamma_edit.text().strip()
            else None
        )
        dest = default_export_path(gamma if gamma and gamma.is_dir() else None)
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
                gamma=gamma if gamma and gamma.is_dir() else None,
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
