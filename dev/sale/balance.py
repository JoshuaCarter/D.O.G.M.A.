"""Default + per-faction override balance state."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from .score import ARMOR_WEIGHTS, WEAPON_WEIGHTS
from .settings import BALANCE_YML, ensure_dirs


def _default_weights_weapon() -> dict[str, float]:
    return {wkey: 0.5 for _sk, wkey, _c, _i in WEAPON_WEIGHTS}


def _default_weights_armor() -> dict[str, float]:
    out = {wkey: 0.5 for _sk, wkey, _c, _i in ARMOR_WEIGHTS}
    out["a_price"] = 0.5
    return out


def default_balance() -> dict[str, Any]:
    return {
        "default": {
            "weapons": {
                "max_pts": 900,
                "cost_mult": 1000,
                "weights": _default_weights_weapon(),
            },
            "outfits": {
                "max_pts": 550,
                "cost_mult": 1000,
                "include_universal_armor": True,
                "weights": _default_weights_armor(),
            },
            "helmets": {
                "max_pts": 250,
                "cost_mult": 1000,
                "weights": _default_weights_armor(),
            },
        },
        "factions": {},
    }


def load_balance(path: Path | None = None) -> dict[str, Any]:
    ensure_dirs()
    p = path or BALANCE_YML
    if not p.is_file():
        return default_balance()
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    base = default_balance()
    # Shallow merge defaults
    for cat in ("weapons", "outfits", "helmets"):
        dcat = data.get("default", {}).get(cat) or {}
        bcat = base["default"][cat]
        bcat.update({k: v for k, v in dcat.items() if k != "weights"})
        if "weights" in dcat:
            bcat["weights"].update(dcat["weights"] or {})
    base["factions"] = data.get("factions") or {}
    return base


def save_balance(data: dict[str, Any], path: Path | None = None) -> None:
    ensure_dirs()
    p = path or BALANCE_YML
    p.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def effective_category(
    balance: dict[str, Any], faction: str, category: str
) -> dict[str, Any]:
    """Resolve Default + faction overrides for one category."""
    base = deepcopy(balance["default"][category])
    if faction == "Default":
        return base
    fov = (balance.get("factions") or {}).get(faction) or {}
    ov = fov.get(category) or {}
    for k, v in ov.items():
        if k == "weights":
            base["weights"].update(v or {})
        else:
            base[k] = v
    return base


def set_override(
    balance: dict[str, Any],
    faction: str,
    category: str,
    key: str,
    value: Any,
    *,
    weight: bool = False,
) -> None:
    if faction == "Default":
        cat = balance["default"][category]
        if weight:
            cat["weights"][key] = value
        else:
            cat[key] = value
        return
    factions = balance.setdefault("factions", {})
    fblock = factions.setdefault(faction, {})
    cblock = fblock.setdefault(category, {})
    if weight:
        cblock.setdefault("weights", {})[key] = value
    else:
        cblock[key] = value


def clear_override(
    balance: dict[str, Any],
    faction: str,
    category: str,
    key: str,
    *,
    weight: bool = False,
) -> None:
    if faction == "Default":
        return
    fov = (balance.get("factions") or {}).get(faction) or {}
    cblock = fov.get(category) or {}
    if weight:
        w = cblock.get("weights") or {}
        w.pop(key, None)
        if not w:
            cblock.pop("weights", None)
    else:
        cblock.pop(key, None)
    if not cblock and faction in (balance.get("factions") or {}):
        balance["factions"].pop(faction, None)
    elif not cblock:
        pass


def is_overridden(
    balance: dict[str, Any],
    faction: str,
    category: str,
    key: str,
    *,
    weight: bool = False,
) -> bool:
    if faction == "Default":
        return False
    cblock = ((balance.get("factions") or {}).get(faction) or {}).get(category) or {}
    if weight:
        return key in (cblock.get("weights") or {})
    return key in cblock


def override_count(balance: dict[str, Any], faction: str) -> int:
    if faction == "Default":
        return 0
    fov = (balance.get("factions") or {}).get(faction) or {}
    n = 0
    for _cat, cblock in fov.items():
        for k, v in (cblock or {}).items():
            if k == "weights":
                n += len(v or {})
            else:
                n += 1
    return n
