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
                # Remap checked-item shop pts into [min, max] (display + LTX).
                "price_scale_min": 100,
                "price_scale_max": 900,
                # Shotguns skip ADS/hip spread in the weighted score.
                "shotguns_zero_spread": True,
                "weights": _default_weights_weapon(),
                "ceilings": default_ceilings_weapon(),
            },
            "outfits": {
                "include_universal_armor": True,
                "price_scale_min": 100,
                "price_scale_max": 900,
                "weights": _default_weights_armor(),
                "ceilings": default_ceilings_armor(is_helmet=False),
            },
            "helmets": {
                "include_universal_armor": True,
                "price_scale_min": 100,
                "price_scale_max": 900,
                "weights": _default_weights_armor(),
                "ceilings": default_ceilings_armor(is_helmet=True),
            },
        },
        "factions": {},
    }


# Legacy Budget keys from the auto-include era — stripped on load.
_DEAD_CAT_KEYS = (
    "max_pts",
    "cost_mult",
    "kind_limits",
    "max_items",
    "allow_suppressed",
    "allow_scoped",
    "price_scale_enabled",
)


# Legacy weight keys → current Min/Lgt+/… schema (order matters).
# Do not rename w_hvy_* / w_max_* — those are real Hvy / Max tiers now.
_WEIGHT_KEY_RENAMES: list[tuple[str, str]] = [
    ("w_mut_dmg", "w_min_dmg"),
    ("w_mut_dps", "w_min_dps"),
    ("w_mid_dmg", "w_lgtp_dmg"),  # very old mid was Lgt+
    ("w_mid_dps", "w_lgtp_dps"),
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
            for dk in _DEAD_CAT_KEYS:
                if dk in cblock:
                    cblock.pop(dk, None)
                    migrated = True
    for _cat, cblock in (base.get("default") or {}).items():
        if not isinstance(cblock, dict):
            continue
        for dk in _DEAD_CAT_KEYS:
            if dk in cblock:
                cblock.pop(dk, None)
                migrated = True
    if "excluded_items" in data:
        # Pre-checkbox exclude list — membership is checkbox-only now.
        migrated = True
    base.pop("excluded_items", None)
    if isinstance(data.get("item_ltx_overrides"), dict):
        ovs = _normalize_ltx_map(data["item_ltx_overrides"], allow_exclude=False)
        if ovs != (data.get("item_ltx_overrides") or {}):
            migrated = True
        if ovs:
            base["item_ltx_overrides"] = ovs
        else:
            base.pop("item_ltx_overrides", None)
            if data.get("item_ltx_overrides"):
                migrated = True
    # Per-faction LTX deltas (include / exclude vs baseline).
    for _fac, fblock in (base.get("factions") or {}).items():
        if not isinstance(fblock, dict):
            continue
        raw_fac = fblock.get("item_ltx_overrides")
        if not isinstance(raw_fac, dict):
            continue
        cleaned = _normalize_ltx_map(raw_fac, allow_exclude=True)
        if cleaned != raw_fac:
            migrated = True
        if cleaned:
            fblock["item_ltx_overrides"] = cleaned
        else:
            fblock.pop("item_ltx_overrides", None)
            migrated = True
    if migrated:
        log.info("balance migrated (weights / checkbox LTX / dead Budget keys)")
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
    for key, cblock in fov.items():
        if key == "item_ltx_overrides":
            n += len(cblock or {}) if isinstance(cblock, dict) else 0
            continue
        if not isinstance(cblock, dict):
            continue
        for k, v in cblock.items():
            if k == "weights":
                n += len(v or {})
            elif k == "ammo_enabled":
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


def _normalize_ltx_map(
    raw: dict[str, Any] | None, *, allow_exclude: bool
) -> dict[str, str]:
    """Keep only ``include`` (and ``exclude`` when allowed)."""
    out: dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for sec, mode in raw.items():
        m = str(mode or "").strip().lower()
        if m == "include":
            out[str(sec)] = "include"
        elif allow_exclude and m == "exclude":
            out[str(sec)] = "exclude"
    return out


def _baseline_ltx_map(balance: dict[str, Any]) -> dict[str, str]:
    return _normalize_ltx_map(
        balance.get("item_ltx_overrides"), allow_exclude=False
    )


def _faction_ltx_map(balance: dict[str, Any], faction: str) -> dict[str, str]:
    if not faction or faction == "Default":
        return {}
    fblock = (balance.get("factions") or {}).get(faction) or {}
    return _normalize_ltx_map(
        fblock.get("item_ltx_overrides"), allow_exclude=True
    )


def baseline_item_in_ltx(balance: dict[str, Any], sec: str) -> bool:
    """True when Baseline (Default) has the item checked."""
    return _baseline_ltx_map(balance).get(str(sec)) == "include"


def item_in_ltx_for_faction(
    balance: dict[str, Any], faction: str, sec: str
) -> bool:
    """Effective include for a faction: baseline ± faction include/exclude."""
    base_on = baseline_item_in_ltx(balance, sec)
    if not faction or faction == "Default":
        return base_on
    ov = _faction_ltx_map(balance, faction).get(str(sec))
    if ov == "include":
        return True
    if ov == "exclude":
        return False
    return base_on


def ltx_paint_state(
    balance: dict[str, Any], faction: str, sec: str
) -> str:
    """Tile paint vs baseline: ``both`` | ``faction`` | ``baseline`` | ``off``."""
    fac_on = item_in_ltx_for_faction(balance, faction, sec)
    base_on = baseline_item_in_ltx(balance, sec)
    if fac_on and base_on:
        return "both"
    if fac_on and not base_on:
        return "faction"
    if (not fac_on) and base_on:
        return "baseline"
    return "off"


def _set_baseline_ltx(
    balance: dict[str, Any], sec: str, mode: str | None
) -> None:
    key = str(sec)
    ovs = balance.setdefault("item_ltx_overrides", {})
    if not isinstance(ovs, dict):
        ovs = {}
        balance["item_ltx_overrides"] = ovs
    if mode == "include":
        ovs[key] = "include"
    else:
        ovs.pop(key, None)
    if not ovs:
        balance.pop("item_ltx_overrides", None)


def _set_faction_ltx(
    balance: dict[str, Any], faction: str, sec: str, mode: str | None
) -> None:
    """Set faction delta: ``include`` / ``exclude`` / None (inherit baseline)."""
    fac = str(faction or "").strip()
    if not fac or fac == "Default":
        _set_baseline_ltx(balance, sec, mode)
        return
    factions = balance.setdefault("factions", {})
    fblock = factions.setdefault(fac, {})
    if not isinstance(fblock, dict):
        fblock = {}
        factions[fac] = fblock
    ovs = fblock.setdefault("item_ltx_overrides", {})
    if not isinstance(ovs, dict):
        ovs = {}
        fblock["item_ltx_overrides"] = ovs
    key = str(sec)
    if mode in ("include", "exclude"):
        ovs[key] = mode
    else:
        ovs.pop(key, None)
    if not ovs:
        fblock.pop("item_ltx_overrides", None)
    if not fblock:
        factions.pop(fac, None)


def toggle_item_ltx_override(
    balance: dict[str, Any], faction: str, sec: str
) -> bool:
    """Toggle effective include for faction. Returns new effective in-LTX state."""
    fac = str(faction or "Default").strip() or "Default"
    key = str(sec)
    if fac == "Default":
        now = baseline_item_in_ltx(balance, key)
        _set_baseline_ltx(balance, key, None if now else "include")
        return not now

    currently = item_in_ltx_for_faction(balance, fac, key)
    base_on = baseline_item_in_ltx(balance, key)
    if currently:
        # Turn off for this faction.
        if base_on:
            _set_faction_ltx(balance, fac, key, "exclude")
        else:
            _set_faction_ltx(balance, fac, key, None)  # clear force-in
        return False
    # Turn on for this faction.
    if base_on:
        _set_faction_ltx(balance, fac, key, None)  # clear force-out
    else:
        _set_faction_ltx(balance, fac, key, "include")
    return True


# Back-compat aliases used by older call sites during transition.
def get_item_ltx_override(balance: dict[str, Any], sec: str) -> str | None:
    """Baseline-only include flag (Default). Prefer ``item_in_ltx_for_faction``."""
    return "include" if baseline_item_in_ltx(balance, sec) else None


def item_in_ltx(override: str | None) -> bool:
    return override == "include"
