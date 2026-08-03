"""Default + per-faction override balance state."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from .diaglog import get_logger
from .score import (
    ARMOR_WEIGHTS,
    CURVE_LINEAR,
    WEAPON_WEIGHTS,
    default_ceilings_armor,
    default_ceilings_weapon,
    merge_ceilings,
    merge_curves,
    normalize_curve,
)
from .settings import BALANCE_YML, LEGACY_BALANCE_YML, ensure_dirs

log = get_logger("balance")


def _default_weights_weapon() -> dict[str, float]:
    return {wkey: float(default_w) for _sk, wkey, _c, _i, default_w in WEAPON_WEIGHTS}


def _default_weights_armor() -> dict[str, float]:
    out = {wkey: float(default_w) for _sk, wkey, _c, _i, default_w in ARMOR_WEIGHTS}
    out["a_price"] = 0.5
    return out


def default_balance() -> dict[str, Any]:
    return {
        "default": {
            "weapons": {
                "max_pts": 900,
                "cost_mult": 1000,
                # Off = naturally exclude guns with attached silencer / scope.
                "allow_suppressed": False,
                "allow_scoped": False,
                # Per LTX kind (w_pistol, …): at most N lowest-pts guns (0–10).
                "kind_limits": {},
                "weights": _default_weights_weapon(),
                "ceilings": default_ceilings_weapon(),
            },
            "outfits": {
                "max_pts": 550,
                "cost_mult": 1000,
                "include_universal_armor": True,
                # At most N lowest-pts outfits in LTX (0–10).
                "max_items": 10,
                "weights": _default_weights_armor(),
                "ceilings": default_ceilings_armor(is_helmet=False),
            },
            "helmets": {
                "max_pts": 250,
                "cost_mult": 1000,
                "max_items": 10,
                "weights": _default_weights_armor(),
                "ceilings": default_ceilings_armor(is_helmet=True),
            },
        },
        "factions": {},
    }


# Old score keys → tooltip-aligned Min/Lgt/Lgt+/Mid/Mid+ (order matters).
_WEIGHT_KEY_RENAMES: list[tuple[str, str]] = [
    ("w_mut_dmg", "w_min_dmg"),
    ("w_mut_dps", "w_min_dps"),
    ("w_mid_dmg", "w_lgtp_dmg"),  # old mid was Lgt+
    ("w_mid_dps", "w_lgtp_dps"),
    ("w_hvy_dmg", "w_mid_dmg"),  # old hvy was Mid
    ("w_hvy_dps", "w_mid_dps"),
    ("w_max_dmg", "w_midp_dmg"),
    ("w_max_dps", "w_midp_dps"),
]


def _migrate_weight_keys(weights: dict[str, Any] | None) -> bool:
    """Rename legacy weight keys in place. Returns True if anything changed."""
    if not isinstance(weights, dict) or not weights:
        return False
    changed = False
    # Current schema uses w_mid_* for Mid; do not drop it when w_lgtp_* also exists.
    _keep_when_both = {"w_mid_dmg", "w_mid_dps"}
    for old, new in _WEIGHT_KEY_RENAMES:
        if old not in weights:
            continue
        if new not in weights:
            weights[new] = weights.pop(old)
            changed = True
            continue
        if old in _keep_when_both:
            continue
        weights.pop(old)
        changed = True
    if "w_rounds" in weights:
        weights.pop("w_rounds", None)
        changed = True
    # Undo bad rename if it landed in a balance file.
    if "a_br" in weights:
        if "a_fire_wound" not in weights:
            weights["a_fire_wound"] = weights.pop("a_br")
        else:
            weights.pop("a_br", None)
        changed = True
    return changed


def load_balance(path: Path | None = None) -> dict[str, Any]:
    ensure_dirs()
    p = path or BALANCE_YML
    # One-time migrate from old cache/balance.yml location.
    if path is None and not p.is_file() and LEGACY_BALANCE_YML.is_file():
        try:
            p.write_text(
                LEGACY_BALANCE_YML.read_text(encoding="utf-8"), encoding="utf-8"
            )
            log.info("migrated balance %s -> %s", LEGACY_BALANCE_YML, p)
        except Exception:
            log.exception("balance migrate failed; reading legacy path")
            p = LEGACY_BALANCE_YML
    if not p.is_file():
        log.info("balance defaults (no file %s)", p)
        return default_balance()
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        log.exception("balance load failed %s — using defaults", p)
        return default_balance()
    base = default_balance()
    migrated = False
    for cat in ("weapons", "outfits", "helmets"):
        dcat = data.get("default", {}).get(cat) or {}
        bcat = base["default"][cat]
        bcat.update(
            {
                k: v
                for k, v in dcat.items()
                if k not in ("weights", "ceilings", "curves")
            }
        )
        if "weights" in dcat:
            w = dict(dcat["weights"] or {})
            migrated = _migrate_weight_keys(w) or migrated
            bcat["weights"].update(w)
        if "ceilings" in dcat and isinstance(dcat.get("ceilings"), dict):
            bcat["ceilings"] = merge_ceilings(
                bcat.get("ceilings") or {}, dcat.get("ceilings")
            )
        if "curves" in dcat and isinstance(dcat.get("curves"), dict):
            bcat["curves"] = merge_curves(dcat.get("curves"))
        # Drop any faction-copied ceilings left from older experiments.
    base["factions"] = data.get("factions") or {}
    for _fac, fblock in (base.get("factions") or {}).items():
        for _cat, cblock in (fblock or {}).items():
            if not isinstance(cblock, dict):
                continue
            if "weights" in cblock:
                migrated = _migrate_weight_keys(cblock.get("weights")) or migrated
            # Scale ceilings / curves are Default-only; strip from faction overrides.
            if "ceilings" in cblock:
                cblock.pop("ceilings", None)
                migrated = True
            if "curves" in cblock:
                cblock.pop("curves", None)
                migrated = True
    # Legacy excluded_items (default-on include) → item_ltx_overrides force-exclude.
    if isinstance(data.get("excluded_items"), dict) and data["excluded_items"]:
        ovs = base.setdefault("item_ltx_overrides", {})
        if not isinstance(ovs, dict):
            ovs = {}
            base["item_ltx_overrides"] = ovs
        for sec, flag in data["excluded_items"].items():
            if flag and str(sec) not in ovs:
                ovs[str(sec)] = "exclude"
        migrated = True
    if "excluded_items" in data:
        migrated = True
    # Do not keep excluded_items on the live balance object.
    base.pop("excluded_items", None)
    if isinstance(data.get("item_ltx_overrides"), dict):
        ovs = base.setdefault("item_ltx_overrides", {})
        if not isinstance(ovs, dict):
            ovs = {}
            base["item_ltx_overrides"] = ovs
        for sec, mode in data["item_ltx_overrides"].items():
            m = str(mode or "").strip().lower()
            if m in ("include", "exclude"):
                ovs[str(sec)] = m
    if migrated:
        log.info("balance migrated score weight keys → Min/Lgt/Lgt+/Mid/Mid+")
        try:
            save_balance(base, p)
        except Exception:
            log.exception("balance re-save after key migrate failed")
    log.info("balance loaded factions=%d from %s", len(base["factions"]), p)
    return base


def save_balance(data: dict[str, Any], path: Path | None = None) -> None:
    ensure_dirs()
    p = path or BALANCE_YML
    try:
        p.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        log.info("balance saved %s", p)
    except Exception:
        log.exception("balance save failed %s", p)
        raise


def effective_category(
    balance: dict[str, Any], faction: str, category: str
) -> dict[str, Any]:
    """Resolve Default + faction overrides for one category.

    ``ceilings`` / ``curves`` always come from Default (shared scale maxes).
    """
    base = deepcopy(balance["default"][category])
    # Ensure ceilings always present / merged with code defaults.
    is_helm = category == "helmets"
    if category == "weapons":
        base["ceilings"] = merge_ceilings(
            default_ceilings_weapon(), base.get("ceilings")
        )
    else:
        base["ceilings"] = merge_ceilings(
            default_ceilings_armor(is_helmet=is_helm), base.get("ceilings")
        )
    base["curves"] = merge_curves(base.get("curves"))
    if faction == "Default":
        return base
    fov = (balance.get("factions") or {}).get(faction) or {}
    ov = fov.get(category) or {}
    for k, v in ov.items():
        if k in ("ceilings", "curves"):
            continue  # Default-only
        if k == "weights":
            base["weights"].update(v or {})
        else:
            base[k] = v
    return base


def default_ceilings_for(
    balance: dict[str, Any], category: str
) -> dict[str, float]:
    """Scale maxes from Default only."""
    return effective_category(balance, "Default", category).get("ceilings") or {}


def set_ceiling(
    balance: dict[str, Any], category: str, stat_key: str, value: float
) -> None:
    """Write a scale-max ceiling (Default only)."""
    cat = balance.setdefault("default", {}).setdefault(category, {})
    ceilings = cat.setdefault("ceilings", {})
    ceilings[stat_key] = float(value)


def set_curve(
    balance: dict[str, Any], category: str, stat_key: str, curve: str
) -> None:
    """Write a scale-max curve (Default only). Linear omits the key."""
    cat = balance.setdefault("default", {}).setdefault(category, {})
    curves = cat.setdefault("curves", {})
    if not isinstance(curves, dict):
        curves = {}
        cat["curves"] = curves
    c = normalize_curve(curve)
    if c == CURVE_LINEAR:
        curves.pop(stat_key, None)
        if not curves:
            cat.pop("curves", None)
    else:
        curves[stat_key] = c


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
    if key in ("ceilings", "curves"):
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
            elif k == "ammo_enabled":
                # Count only explicit offs (and any true overrides).
                n += len(v or {})
            else:
                n += 1
    return n


def ammo_enabled_map(balance: dict[str, Any], faction: str) -> dict[str, bool]:
    """Sparse map of ammo section (or legacy family) → enabled. Missing = enabled."""
    if faction == "Default":
        raw = (balance.get("default") or {}).get("weapons") or {}
    else:
        raw = ((balance.get("factions") or {}).get(faction) or {}).get("weapons") or {}
    ae = raw.get("ammo_enabled") or {}
    if not isinstance(ae, dict):
        return {}
    return {str(k): bool(v) for k, v in ae.items()}


def is_ammo_family_enabled(
    balance: dict[str, Any], faction: str, family: str
) -> bool:
    """``family`` may be a full ammo section id or a legacy calibre family key."""
    from .score import ammo_is_enabled

    return ammo_is_enabled(family, ammo_enabled_map(balance, faction))


def set_ammo_family_enabled(
    balance: dict[str, Any], faction: str, family: str, enabled: bool
) -> None:
    """Persist toggle for one ammo section (or legacy family key). On = omit key."""
    if faction == "Default":
        cat = balance.setdefault("default", {}).setdefault("weapons", {})
    else:
        cat = (
            balance.setdefault("factions", {})
            .setdefault(faction, {})
            .setdefault("weapons", {})
        )
    ae = cat.setdefault("ammo_enabled", {})
    if not isinstance(ae, dict):
        ae = {}
        cat["ammo_enabled"] = ae
    if enabled:
        ae.pop(family, None)
        if not ae:
            cat.pop("ammo_enabled", None)
            if faction != "Default":
                fblock = (balance.get("factions") or {}).get(faction) or {}
                wblock = fblock.get("weapons") or {}
                if not wblock:
                    fblock.pop("weapons", None)
                if not fblock:
                    (balance.get("factions") or {}).pop(faction, None)
    else:
        ae[family] = False


def clear_ammo_enabled(balance: dict[str, Any], faction: str) -> None:
    """Reset all ammo toggles for faction (all on)."""
    if faction == "Default":
        (balance.get("default") or {}).get("weapons", {}).pop("ammo_enabled", None)
        return
    fblock = (balance.get("factions") or {}).get(faction) or {}
    wblock = fblock.get("weapons") or {}
    wblock.pop("ammo_enabled", None)
    if not wblock:
        fblock.pop("weapons", None)
    if not fblock:
        (balance.get("factions") or {}).pop(faction, None)


def get_item_ltx_override(balance: dict[str, Any], sec: str) -> str | None:
    """Return ``\"include\"`` / ``\"exclude\"`` force, or None = follow natural shop."""
    ovs = balance.get("item_ltx_overrides")
    if not isinstance(ovs, dict):
        return None
    mode = ovs.get(str(sec))
    if mode in ("include", "exclude"):
        return str(mode)
    return None


def set_item_ltx_override(
    balance: dict[str, Any], sec: str, mode: str | None
) -> None:
    """Set/clear per-item LTX force. ``mode`` is include/exclude/None."""
    key = str(sec)
    ovs = balance.setdefault("item_ltx_overrides", {})
    if not isinstance(ovs, dict):
        ovs = {}
        balance["item_ltx_overrides"] = ovs
    if mode in ("include", "exclude"):
        ovs[key] = mode
        return
    ovs.pop(key, None)
    if not ovs:
        balance.pop("item_ltx_overrides", None)


def item_in_ltx(natural_in_shop: bool, override: str | None) -> bool:
    """Resolve final LTX membership from natural shop + optional force."""
    if override == "exclude":
        return False
    if override == "include":
        return True
    return bool(natural_in_shop)


def is_item_included(balance: dict[str, Any], sec: str, *, natural_in_shop: bool) -> bool:
    """Final LTX include for ``sec`` given its natural shop eligibility."""
    return item_in_ltx(natural_in_shop, get_item_ltx_override(balance, sec))


def toggle_item_ltx_override(
    balance: dict[str, Any], sec: str, *, natural_in_shop: bool
) -> str | None:
    """Empty→force opposite of natural; filled→clear back to auto. Returns new override."""
    cur = get_item_ltx_override(balance, sec)
    if cur is None:
        nxt: str | None = "exclude" if natural_in_shop else "include"
    else:
        nxt = None
    set_item_ltx_override(balance, sec, nxt)
    return nxt
