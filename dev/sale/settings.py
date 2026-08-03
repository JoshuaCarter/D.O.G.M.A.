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
ITEMS_YML = CACHE / "items.yml"
BALANCE_YML = CACHE / "balance.yml"
THUMBS_DIR = CACHE / "thumbs"


def ensure_dirs() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)


def load_settings() -> dict[str, Any]:
    ensure_dirs()
    if not SETTINGS_PATH.is_file():
        return {}
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.exception("settings load failed: %s", exc)
        return {}


def save_settings(data: dict[str, Any]) -> None:
    ensure_dirs()
    SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    log.info("settings saved")
