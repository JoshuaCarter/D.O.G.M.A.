"""Persist Anomaly / GAMMA roots and last paths."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .diaglog import get_logger

log = get_logger("settings")

PKG = Path(__file__).resolve().parent
CACHE = PKG / "cache"
SETTINGS_PATH = PKG / "settings.json"
# Keep balance next to settings (not under cache/) so thumb/regen wipes don't touch it.
BALANCE_YML = PKG / "balance.yml"
LEGACY_BALANCE_YML = CACHE / "balance.yml"
ITEMS_YML = CACHE / "items.yml"
THUMBS_DIR = CACHE / "thumbs"
# Purchasable keys stripped on export (captured at regenerate from stock LTX).
STOCK_STRIP_YML = CACHE / "stock_strip.yml"


def ensure_dirs() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)


def load_settings() -> dict[str, Any]:
    ensure_dirs()
    if not SETTINGS_PATH.is_file():
        return {}
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.exception("settings load failed: %s", exc)
        return {}
    if not isinstance(data, dict):
        return {}
    data["deploy_targets"] = normalize_deploy_targets(data.get("deploy_targets"))
    last = data.get("last_deploy_dir") or ""
    if isinstance(last, str) and last.strip():
        try:
            p = Path(last).expanduser()
            data["last_deploy_dir"] = str(p.resolve()) if p.is_dir() else ""
        except OSError:
            data["last_deploy_dir"] = ""
    else:
        data["last_deploy_dir"] = ""
    return data


def save_settings(data: dict[str, Any]) -> None:
    ensure_dirs()
    data["deploy_targets"] = normalize_deploy_targets(data.get("deploy_targets"))
    SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    log.info("settings saved")


def _resolved_path_key(path: str | Path) -> str:
    try:
        return str(Path(path).expanduser().resolve())
    except OSError:
        return str(Path(path).expanduser())


def normalize_deploy_targets(value: object) -> dict[str, str]:
    """Map source LTX path → deploy target path (resolved strings)."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for raw_src, raw_dst in value.items():
        if not isinstance(raw_src, str) or not isinstance(raw_dst, str):
            continue
        if not raw_src.strip() or not raw_dst.strip():
            continue
        out[_resolved_path_key(raw_src)] = _resolved_path_key(raw_dst)
    return out


def set_deploy_target(settings: dict, source: Path, target: Path) -> None:
    """Remember deploy overwrite path for a source file."""
    targets = normalize_deploy_targets(settings.get("deploy_targets"))
    targets[_resolved_path_key(source)] = _resolved_path_key(target)
    settings["deploy_targets"] = targets
    parent = target if target.is_dir() else target.parent
    try:
        if parent.is_dir():
            settings["last_deploy_dir"] = str(parent.resolve())
    except OSError:
        settings["last_deploy_dir"] = str(parent)


def get_deploy_target(settings: dict, source: Path | None) -> Path | None:
    if source is None:
        return None
    targets = normalize_deploy_targets(settings.get("deploy_targets"))
    key = _resolved_path_key(source)
    raw = targets.get(key)
    if not raw:
        # Case-insensitive fallback (Windows).
        low = key.lower()
        for sk, tv in targets.items():
            if sk.lower() == low:
                raw = tv
                break
    if not raw:
        return None
    return Path(raw)
