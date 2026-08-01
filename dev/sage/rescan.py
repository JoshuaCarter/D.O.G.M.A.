"""Selective asset-cache rescan dialog and root helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .settings import (
    normalize_custom_roots,
    rescan_asset_roots,
    rescan_custom_asset_roots,
)
from .textures import iter_texture_dir_roots


@dataclass(frozen=True)
class RescanTarget:
    """One checkbox row in the rescan dialog."""

    id: str
    label: str
    group: str  # "custom" | "anomaly" | "gamma"
    default_checked: bool
    custom_path: str = ""


def _norm_key(path: Path | str) -> str:
    try:
        return str(Path(path).resolve()).lower()
    except OSError:
        return str(path).lower().replace("/", "\\")


def _is_under(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except (ValueError, OSError):
        try:
            path.relative_to(base)
            return True
        except ValueError:
            return False


def list_rescan_targets(settings: dict) -> list[RescanTarget]:
    """Build dialog rows: each custom root (default on), Anomaly, GAMMA."""
    out: list[RescanTarget] = []
    customs = normalize_custom_roots(settings.get("custom_roots"))
    for i, path in enumerate(customs):
        name = Path(path).name or path
        out.append(
            RescanTarget(
                id=f"custom:{i}",
                label=f"Custom: {name}",
                group="custom",
                default_checked=True,
                custom_path=path,
            )
        )
    anomaly = str(settings.get("anomaly_root") or "").strip()
    if anomaly:
        out.append(
            RescanTarget(
                id="anomaly",
                label=f"Anomaly: {Path(anomaly).name}",
                group="anomaly",
                default_checked=False,
            )
        )
    gamma = str(settings.get("gamma_root") or "").strip()
    if gamma:
        out.append(
            RescanTarget(
                id="gamma",
                label=f"GAMMA: {Path(gamma).name}",
                group="gamma",
                default_checked=False,
            )
        )
    return out


def _unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        key = _norm_key(path)
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def collect_invalidate_roots(
    settings: dict, selected: list[RescanTarget]
) -> list[Path]:
    """Concrete dirs whose per-root cache shards should be dropped + regenerated."""
    want_anomaly = any(t.group == "anomaly" for t in selected)
    want_gamma = any(t.group == "gamma" for t in selected)
    custom_paths = [t.custom_path for t in selected if t.group == "custom" and t.custom_path]

    out: list[Path] = []
    anom = Path(str(settings.get("anomaly_root") or "").strip()) if want_anomaly else None
    gam = Path(str(settings.get("gamma_root") or "").strip()) if want_gamma else None

    gamedata_lists = (
        list(settings.get("gamedata_texture_roots") or []),
        list(settings.get("gamedata_descr_roots") or []),
        list(settings.get("gamedata_text_roots") or []),
    )
    for base in (anom, gam):
        if base is None or not str(base):
            continue
        for lst in gamedata_lists:
            for raw in lst:
                path = Path(str(raw))
                if _is_under(path, base) or _norm_key(path) == _norm_key(base):
                    out.append(path)

    for custom_s in custom_paths:
        custom = Path(custom_s)
        out.append(custom)
        for key in ("texture_roots", "textures_descr_roots", "text_roots"):
            for raw in settings.get(key) or []:
                path = Path(str(raw))
                if _is_under(path, custom) or _norm_key(path) == _norm_key(custom):
                    out.append(path)
        out.extend(
            iter_texture_dir_roots(
                gamedata_texture_roots=[],
                texture_scan_roots=[custom],
            )
        )
    return _unique_paths(out)


def apply_rescan_selection(settings: dict, selected: list[RescanTarget]) -> dict:
    """Rediscover folder lists for the selected install/custom groups."""
    want_anomaly = any(t.group == "anomaly" for t in selected)
    want_gamma = any(t.group == "gamma" for t in selected)
    want_custom = any(t.group == "custom" for t in selected)
    if want_anomaly or want_gamma:
        # Full install rediscovery (path lists only — cache shards cleared separately).
        rescan_asset_roots(settings)
    elif want_custom:
        rescan_custom_asset_roots(settings)
    return settings


class RescanDialog(QDialog):
    """Choose which install/custom roots to rescan (default: custom only)."""

    def __init__(self, settings: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Rescan asset cache")
        self.setMinimumWidth(420)
        self._targets = list_rescan_targets(settings)
        self._checks: list[tuple[RescanTarget, QCheckBox]] = []

        lay = QVBoxLayout(self)
        lay.addWidget(
            QLabel(
                "Drop and regenerate cache shards for the selected roots only.\n"
                "Other roots keep their existing cache files."
            )
        )

        btn_row = QHBoxLayout()
        sel_all = QPushButton("Select all")
        sel_none = QPushButton("Deselect all")
        sel_all.clicked.connect(self._select_all)
        sel_none.clicked.connect(self._select_none)
        btn_row.addWidget(sel_all)
        btn_row.addWidget(sel_none)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body_l = QVBoxLayout(body)
        body_l.setContentsMargins(0, 0, 0, 0)
        if not self._targets:
            body_l.addWidget(
                QLabel("No roots configured. Set Anomaly / GAMMA / custom in Settings.")
            )
        for target in self._targets:
            cb = QCheckBox(target.label)
            cb.setChecked(target.default_checked)
            if target.custom_path:
                cb.setToolTip(target.custom_path)
            body_l.addWidget(cb)
            self._checks.append((target, cb))
        body_l.addStretch(1)
        scroll.setWidget(body)
        lay.addWidget(scroll, stretch=1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        lay.addWidget(buttons)
        for _, cb in self._checks:
            cb.toggled.connect(self._sync_ok)
        self._sync_ok()

    def _select_all(self) -> None:
        for _, cb in self._checks:
            cb.setChecked(True)

    def _select_none(self) -> None:
        for _, cb in self._checks:
            cb.setChecked(False)

    def _sync_ok(self) -> None:
        if self._ok is not None:
            self._ok.setEnabled(any(cb.isChecked() for _, cb in self._checks))

    def selected_targets(self) -> list[RescanTarget]:
        return [t for t, cb in self._checks if cb.isChecked()]
