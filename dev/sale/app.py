"""SALE — Stalker Anomaly Loadout Editor main window."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
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
from .diaglog import get_logger, setup_logging
from .export_ltx import default_export_path, export_shop_ltx
from .regenerate import load_items, regenerate
from .score import (
    ARMOR_WEIGHTS,
    FACTIONS,
    WEAPON_WEIGHTS,
    armor_pts,
    bloc_ok,
    weapon_bloc,
    weapon_pts,
)
from .settings import load_settings, save_settings

log = get_logger("app")

CATS = ("weapons", "outfits", "helmets")


class RegenWorker(QThread):
    progress = pyqtSignal(str, int, int)
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, anomaly: Path | None, gamma: Path | None) -> None:
        super().__init__()
        self.anomaly = anomaly
        self.gamma = gamma

    def run(self) -> None:
        try:
            path = regenerate(
                self.anomaly,
                self.gamma,
                progress=lambda m, c, t: self.progress.emit(m, c, t),
            )
            self.finished_ok.emit(str(path))
        except Exception as exc:  # noqa: BLE001
            log.exception("regenerate failed")
            self.failed.emit(str(exc))


class WeightRow(QWidget):
    changed = pyqtSignal(str, float, bool)  # key, value, is_weight
    cleared = pyqtSignal(str, bool)

    def __init__(self, key: str, label: str, value: float, *, is_weight: bool, overridden: bool) -> None:
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
        self.val = QLabel(self._fmt(value))
        self.val.setMinimumWidth(48)
        lay.addWidget(self.val)
        self.btn = QPushButton("↺")
        self.btn.setFixedWidth(28)
        self.btn.setEnabled(overridden)
        self.btn.setToolTip("Clear override (use Default)")
        self.btn.clicked.connect(lambda: self.cleared.emit(self.key, self.is_weight))
        lay.addWidget(self.btn)

    def _fmt(self, v: float) -> str:
        return f"{v:.2f}" if self.is_weight else str(int(v))

    def _on_slide(self, v: int) -> None:
        if self.is_weight:
            val: float | bool = v / 100.0
        elif self.key == "include_universal_armor":
            val = bool(v)
        else:
            val = float(v)
        self.val.setText(self._fmt(float(val) if not isinstance(val, bool) else (1 if val else 0)))
        self.changed.emit(self.key, float(val) if not isinstance(val, bool) else (1.0 if val else 0.0), self.is_weight)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("SALE — Stalker Anomaly Loadout Editor")
        self.resize(1280, 800)
        self.settings = load_settings()
        self.balance = load_balance()
        self.items: dict[str, Any] = load_items()
        self.faction = "Default"
        self.category = "weapons"
        self._rows: list[tuple[str, dict[str, Any], int, bool]] = []
        self._worker: RegenWorker | None = None

        root = QWidget()
        self.setCentralWidget(root)
        v = QVBoxLayout(root)

        # Top bar
        top = QHBoxLayout()
        self.anomaly_edit = QLineEdit(self.settings.get("anomaly_root", ""))
        self.gamma_edit = QLineEdit(self.settings.get("gamma_root", ""))
        top.addWidget(QLabel("Anomaly:"))
        top.addWidget(self.anomaly_edit, 2)
        b_a = QPushButton("…")
        b_a.clicked.connect(lambda: self._browse(self.anomaly_edit))
        top.addWidget(b_a)
        top.addWidget(QLabel("GAMMA:"))
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
        self.sort_box = QComboBox()
        self.sort_box.addItems(["pts", "name", "cost", "mut_dps"])
        self.sort_box.currentTextChanged.connect(lambda _: self._rebuild_list())
        self.filter_box = QComboBox()
        self.filter_box.addItems(["all", "in-shop", "over-threshold"])
        self.filter_box.currentTextChanged.connect(lambda _: self._rebuild_list())
        actions.addWidget(self.btn_regen)
        actions.addWidget(self.btn_load)
        actions.addWidget(self.btn_export)
        actions.addWidget(QLabel("Sort:"))
        actions.addWidget(self.sort_box)
        actions.addWidget(QLabel("Filter:"))
        actions.addWidget(self.filter_box)
        actions.addStretch(1)
        v.addLayout(actions)

        fac = QHBoxLayout()
        fac.addWidget(QLabel("Faction:"))
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

        self.tabs = QTabWidget()
        for cat, title in (
            ("weapons", "Weapons"),
            ("outfits", "Outfits"),
            ("helmets", "Helmets"),
        ):
            self.tabs.addTab(QWidget(), title)
        self.tabs.currentChanged.connect(self._on_tab)
        v.addWidget(self.tabs)

        split = QSplitter(Qt.Orientation.Horizontal)
        # Left weights
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        self.weights_host = QWidget()
        self.weights_layout = QVBoxLayout(self.weights_host)
        left_scroll.setWidget(self.weights_host)
        split.addWidget(left_scroll)

        # Center list
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._on_select)
        split.addWidget(self.list)

        # Right detail
        self.detail = QLabel("Select an item")
        self.detail.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        right = QScrollArea()
        right.setWidgetResizable(True)
        right.setWidget(self.detail)
        split.addWidget(right)
        split.setSizes([280, 600, 320])
        v.addWidget(split, 1)

        self.status = QLabel("Ready")
        v.addWidget(self.status)

        self._rebuild_weights()
        self._rebuild_list()

    def _browse(self, edit: QLineEdit) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select folder", edit.text())
        if d:
            edit.setText(d)

    def _save_roots(self) -> None:
        self.settings["anomaly_root"] = self.anomaly_edit.text().strip()
        self.settings["gamma_root"] = self.gamma_edit.text().strip()
        save_settings(self.settings)

    def _on_faction(self, name: str) -> None:
        self.faction = name
        n = override_count(self.balance, name)
        self.faction_meta.setText(f"({n} overrides)" if n else "")
        self._rebuild_weights()
        self._rebuild_list()

    def _on_tab(self, idx: int) -> None:
        self.category = CATS[idx] if 0 <= idx < len(CATS) else "weapons"
        self._rebuild_weights()
        self._rebuild_list()

    def _rebuild_weights(self) -> None:
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
            # Toggle as 0/1 slider
            univ = 1.0 if cfg.get("include_universal_armor", True) else 0.0
            add_scalar("include_universal_armor", "include_universal_armor", univ)

        weights = cfg.get("weights") or {}
        if cat == "weapons":
            for _sk, wkey, _c, _i in WEAPON_WEIGHTS:
                add_weight(wkey, wkey, float(weights.get(wkey, 0.5)))
        else:
            for _sk, wkey, _c, _i in ARMOR_WEIGHTS:
                add_weight(wkey, wkey, float(weights.get(wkey, 0.5)))
            add_weight("a_price", "a_price", float(weights.get("a_price", 0.5)))

        self.weights_layout.addStretch(1)

    def _weight_changed(self, key: str, value: float, is_weight: bool) -> None:
        store: Any = value
        if key == "include_universal_armor":
            store = value >= 0.5
        set_override(
            self.balance, self.faction, self.category, key, store, weight=is_weight
        )
        save_balance(self.balance)
        n = override_count(self.balance, self.faction)
        self.faction_meta.setText(f"({n} overrides)" if n else "")
        # Mark override accent without rebuilding every slider tick
        for i in range(self.weights_layout.count()):
            w = self.weights_layout.itemAt(i).widget()
            if isinstance(w, WeightRow) and w.key == key and w.is_weight == is_weight:
                w.lbl.setStyleSheet("color: #e6a23c; font-weight: bold;")
                w.btn.setEnabled(self.faction != "Default")
        self._rebuild_list()

    def _weight_cleared(self, key: str, is_weight: bool) -> None:
        clear_override(
            self.balance, self.faction, self.category, key, weight=is_weight
        )
        # prune empty category blocks
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

    def _row_pts_inshop(self, sec: str, entry: dict[str, Any]) -> tuple[int, bool]:
        fac = self.faction if self.faction != "Default" else "stalker"
        cfg = effective_category(self.balance, self.faction, self.category)
        # When viewing Default, use Default numbers but stalker gear filters for highlight
        stats = entry.get("stats") or {}
        if self.category == "weapons":
            pts = weapon_pts(stats, cfg.get("weights") or {}, float(cfg.get("cost_mult") or 1000))
            under = pts < float(cfg.get("max_pts") or 900)
            bloc = weapon_bloc(sec, ",".join(entry.get("ammo_class") or []))
            from .score import FACTION_BLOC

            gear_ok = bloc_ok(bloc, FACTION_BLOC.get(fac, "both"))
            return pts, under and gear_ok
        is_helm = self.category == "helmets"
        pts = armor_pts(
            stats,
            cfg.get("weights") or {},
            float(cfg.get("cost_mult") or 1000),
            is_helmet=is_helm,
        )
        under = pts < float(cfg.get("max_pts") or 550)
        if is_helm:
            return pts, under
        from .score import FACTION_COMMUNITY

        want = FACTION_COMMUNITY.get(fac, fac)
        community = (entry.get("community") or "").strip()
        allow_univ = bool(cfg.get("include_universal_armor", True))
        gear_ok = community == want or (allow_univ and community in ("", "actor"))
        return pts, under and gear_ok

    def _rebuild_list(self) -> None:
        self.list.clear()
        pool = (self.items.get(self.category) or {}) if self.items else {}
        rows: list[tuple[str, dict[str, Any], int, bool]] = []
        for sec, entry in pool.items():
            pts, in_shop = self._row_pts_inshop(sec, entry)
            rows.append((sec, entry, pts, in_shop))

        filt = self.filter_box.currentText()
        if filt == "in-shop":
            rows = [r for r in rows if r[3]]
        elif filt == "over-threshold":
            rows = [r for r in rows if not r[3]]

        sort_key = self.sort_box.currentText()
        if sort_key == "name":
            rows.sort(key=lambda r: (r[1].get("name") or r[0]).lower())
        elif sort_key == "cost":
            rows.sort(key=lambda r: float(r[1].get("cost") or 0))
        elif sort_key == "mut_dps":
            rows.sort(
                key=lambda r: float((r[1].get("stats") or {}).get("mut_dps") or 0),
                reverse=True,
            )
        else:
            rows.sort(key=lambda r: r[2])

        self._rows = rows
        for sec, entry, pts, in_shop in rows:
            name = entry.get("name") or sec
            text = f"{'* ' if in_shop else '  '}{name}   {pts} pts"
            item = QListWidgetItem(text)
            thumb = entry.get("thumb") or ""
            if thumb and Path(thumb).is_file():
                item.setIcon(QPixmap(thumb).scaled(48, 24, Qt.AspectRatioMode.KeepAspectRatio))
            if in_shop:
                item.setBackground(Qt.GlobalColor.darkGreen)
            else:
                item.setForeground(Qt.GlobalColor.gray)
            item.setData(Qt.ItemDataRole.UserRole, sec)
            self.list.addItem(item)
        self.status.setText(f"{len(rows)} {self.category}  (faction={self.faction})")

    def _on_select(self, cur: QListWidgetItem | None, _prev: QListWidgetItem | None) -> None:
        if not cur:
            return
        sec = cur.data(Qt.ItemDataRole.UserRole)
        entry = (self.items.get(self.category) or {}).get(sec) or {}
        pts, in_shop = self._row_pts_inshop(sec, entry)
        stats = entry.get("stats") or {}
        lines = [
            f"{sec}",
            f"name: {entry.get('name')}",
            f"pts: {pts}   in_shop: {in_shop}",
            f"cost: {entry.get('cost')}",
            f"community: {entry.get('community')}",
            f"ammo_class: {entry.get('ammo_class')}",
            "",
            "stats:",
        ]
        for k in sorted(stats.keys()):
            lines.append(f"  {k}: {stats[k]}")
        self.detail.setText("\n".join(lines))

    def _regen(self) -> None:
        self._save_roots()
        anomaly = Path(self.anomaly_edit.text().strip()) if self.anomaly_edit.text().strip() else None
        gamma = Path(self.gamma_edit.text().strip()) if self.gamma_edit.text().strip() else None
        if not gamma and not anomaly:
            QMessageBox.warning(self, "SALE", "Set Anomaly and/or GAMMA roots first.")
            return
        self.btn_regen.setEnabled(False)
        self.status.setText("Regenerating…")
        self._worker = RegenWorker(anomaly, gamma)
        self._worker.progress.connect(
            lambda m, c, t: self.status.setText(f"{m}  ({c}/{t})")
        )
        self._worker.finished_ok.connect(self._regen_done)
        self._worker.failed.connect(self._regen_fail)
        self._worker.start()

    def _regen_done(self, path: str) -> None:
        self.btn_regen.setEnabled(True)
        self.items = load_items(Path(path))
        self.status.setText(f"Regenerated → {path}")
        self._rebuild_list()

    def _regen_fail(self, err: str) -> None:
        self.btn_regen.setEnabled(True)
        self.status.setText("Regenerate failed")
        QMessageBox.critical(self, "Regenerate failed", err)

    def _reload_items(self) -> None:
        self.items = load_items()
        self._rebuild_list()

    def _export(self) -> None:
        if not self.items:
            QMessageBox.warning(self, "Export", "No items.yml — Regenerate first.")
            return
        self._save_roots()
        gamma = Path(self.gamma_edit.text().strip()) if self.gamma_edit.text().strip() else None
        dest = default_export_path(gamma if gamma and gamma.is_dir() else None)
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export new_game_loadouts DLTX",
            str(dest),
            "LTX (*.ltx)",
        )
        if not path:
            return
        export_shop_ltx(
            self.items,
            self.balance,
            Path(path),
            gamma=gamma if gamma and gamma.is_dir() else None,
        )
        self.status.setText(f"Exported → {path}")
        QMessageBox.information(self, "Export", f"Wrote:\n{path}")


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    log.info("starting SALE")
    app = QApplication(argv or sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
