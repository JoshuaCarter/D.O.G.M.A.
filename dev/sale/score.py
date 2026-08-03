"""Weight → pts fold (live UI / export) for Stat Derived Loadout."""

from __future__ import annotations

from typing import Any

# (stat_key, weight_key, ceiling, inverse, default_weight)
# default 0 = excluded until the slider is raised; regenerate always stores all stats.
WEAPON_WEIGHTS = [
    ("hit_power", "w_hit_power", 3, False, 0.0),
    ("mut_dmg", "w_mut_dmg", 800, False, 0.0),
    ("lgt_dmg", "w_lgt_dmg", 800, False, 0.0),
    ("mid_dmg", "w_mid_dmg", 800, False, 0.0),
    ("hvy_dmg", "w_hvy_dmg", 800, False, 0.0),
    ("max_dmg", "w_max_dmg", 800, False, 0.0),
    ("mut_dps", "w_mut_dps", 1000, False, 0.5),
    ("lgt_dps", "w_lgt_dps", 1000, False, 0.5),
    ("mid_dps", "w_mid_dps", 1000, False, 0.5),
    ("hvy_dps", "w_hvy_dps", 1000, False, 0.5),
    ("max_dps", "w_max_dps", 1000, False, 0.5),
    ("burst", "w_burst", 30, False, 0.5),
    ("spread_ads", "w_spread_ads", 1.5, True, 0.5),
    ("spread_hip", "w_spread_hip", 20, True, 0.5),
    ("scope", "w_scope", 1, False, 0.5),
    ("silencer", "w_silencer", 1, False, 0.5),
    ("reload_s", "w_reload", 5, True, 0.0),
    ("rpm", "w_rpm", 1500, False, 0.0),
    ("mag", "w_mag", 100, False, 0.0),
    ("rounds_n", "w_rounds", 12, False, 0.0),
    ("cost", "w_price", 30000, True, 0.5),
]

# (stat_key, weight_key, ceiling, inverse, default_weight)
ARMOR_WEIGHTS = [
    ("fire_wound_protection", "a_fire_wound", 1, False, 0.5),
    ("strike_protection", "a_strike", 1, False, 0.5),
    ("wound_protection", "a_wound", 1, False, 0.5),
    ("explosion_protection", "a_explosion", 1, False, 0.5),
    ("shock_protection", "a_shock", 1, False, 0.5),
    ("burn_protection", "a_burn", 1, False, 0.5),
    ("chemical_burn_protection", "a_chem", 1, False, 0.5),
    ("radiation_protection", "a_rad", 1, False, 0.5),
    ("telepathy_protection", "a_psy", 1, False, 0.5),
]

FACTION_BLOC = {
    "stalker": "wp",
    "bandit": "wp",
    "dolg": "wp",
    "army": "wp",
    "renegade": "wp",
    "ecolog": "wp",
    "freedom": "nato",
    "killer": "nato",
    "isg": "nato",
    "csky": "both",
    "monolith": "both",
    "greh": "both",
    "zombied": "wp",
}

FACTION_COMMUNITY = {
    "stalker": "stalker",
    "bandit": "bandit",
    "dolg": "dolg",
    "freedom": "freedom",
    "killer": "killer",
    "army": "army",
    "ecolog": "ecolog",
    "monolith": "monolith",
    "csky": "csky",
    "renegade": "renegade",
    "greh": "greh",
    "isg": "isg",
    "zombied": "stalker",
}

FACTIONS = list(FACTION_BLOC.keys())


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _norm01(raw: float, max_v: float) -> float:
    if max_v <= 0:
        return 0.0
    return _clamp01(raw / max_v)


def _inv_norm01(raw: float, max_v: float) -> float:
    if max_v <= 0:
        return 0.0
    return _clamp01(1.0 - raw / max_v)


def score_to_points(avg01: float, cost_mult: float) -> int:
    pts = int(round((avg01 or 0) * cost_mult))
    return max(1, pts)


def weapon_stat_terms(
    stats: dict[str, Any], weights: dict[str, float]
) -> dict[str, tuple[float, float]]:
    """stat_key → (slider_weight, final_weight) where final = n01 * w."""
    out: dict[str, tuple[float, float]] = {}
    for stat_key, wkey, ceiling, inverse, default_w in WEAPON_WEIGHTS:
        w = float(weights.get(wkey, default_w) or 0)
        raw = float(stats.get(stat_key) or 0)
        if w <= 0:
            out[stat_key] = (w, 0.0)
            continue
        n01 = _inv_norm01(raw, ceiling) if inverse else _norm01(raw, ceiling)
        if stat_key in ("scope", "silencer"):
            n01 = 1.0 if raw > 0 else 0.0
        out[stat_key] = (w, n01 * w)
    return out


def armor_stat_terms(
    stats: dict[str, Any],
    weights: dict[str, float],
    *,
    is_helmet: bool,
) -> dict[str, tuple[float, float]]:
    """stat_key → (slider_weight, final_weight); includes cost via a_price."""
    out: dict[str, tuple[float, float]] = {}
    for stat_key, wkey, _ceil, _inv, default_w in ARMOR_WEIGHTS:
        w = float(weights.get(wkey, default_w) or 0)
        raw = float(stats.get(stat_key) or 0)
        if w <= 0:
            out[stat_key] = (w, 0.0)
            continue
        n01 = _clamp01(raw)
        out[stat_key] = (w, n01 * w)
    w_price = float(weights.get("a_price", 0.5) or 0)
    cost = float(stats.get("cost") or 0)
    if w_price <= 0:
        out["cost"] = (w_price, 0.0)
    else:
        soft = 20000 if is_helmet else 100000
        n01 = _inv_norm01(cost, soft)
        out["cost"] = (w_price, n01 * w_price)
    return out


def weapon_pts(stats: dict[str, Any], weights: dict[str, float], cost_mult: float) -> int:
    terms = weapon_stat_terms(stats, weights)
    sum_nw = 0.0
    sum_w = 0.0
    for w, final in terms.values():
        if w <= 0:
            continue
        sum_nw += final
        sum_w += w
    avg = (sum_nw / sum_w) if sum_w > 0 else 0.0
    return score_to_points(avg, cost_mult)


def armor_pts(
    stats: dict[str, Any],
    weights: dict[str, float],
    cost_mult: float,
    *,
    is_helmet: bool,
) -> int:
    terms = armor_stat_terms(stats, weights, is_helmet=is_helmet)
    sum_nw = 0.0
    sum_w = 0.0
    for w, final in terms.values():
        if w <= 0:
            continue
        sum_nw += final
        sum_w += w
    avg = (sum_nw / sum_w) if sum_w > 0 else 0.0
    return score_to_points(avg, cost_mult)


def weapon_bloc(sec: str, ammo_class: str) -> str:
    s = (ammo_class or "").lower()
    nato = any(
        x in s
        for x in (
            "5.56",
            "9x19",
            ".45",
            "11.43",
            "7.62x51",
            ".308",
            "12x70",
            "5.7",
            ".338",
        )
    )
    wp = any(
        x in s
        for x in ("5.45", "7.62x39", "9x18", "7.62x25", "9x39", "12.7x55", "pmm")
    )
    if nato and wp:
        return "both"
    if nato:
        return "nato"
    if wp:
        return "wp"
    return "both"


def bloc_ok(item_bloc: str, faction_bloc: str) -> bool:
    if faction_bloc == "both" or item_bloc == "both":
        return True
    return item_bloc == faction_bloc


def is_bad_ammo(sec: str) -> bool:
    s = (sec or "").lower()
    return "_bad" in s or "_verybad" in s
