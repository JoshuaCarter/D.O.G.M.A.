"""Weight → pts fold (live UI / export) for Stat Derived Loadout."""

from __future__ import annotations

from typing import Any

WEAPON_WEIGHTS = [
    ("mut_dps", "w_mut_dps", 800, False),
    ("lgt_dps", "w_lgt_dps", 800, False),
    ("mid_dps", "w_mid_dps", 800, False),
    ("hvy_dps", "w_hvy_dps", 800, False),
    ("max_dps", "w_max_dps", 800, False),
    ("burst", "w_burst", 10, False),
    ("spread_ads", "w_spread_ads", 1, True),
    ("spread_hip", "w_spread_hip", 10, True),
    ("scope", "w_scope", 1, False),
    ("silencer", "w_silencer", 1, False),
    ("cost", "w_price", 30000, True),
]

ARMOR_WEIGHTS = [
    ("fire_wound_protection", "a_fire_wound", 1, False),
    ("strike_protection", "a_strike", 1, False),
    ("wound_protection", "a_wound", 1, False),
    ("explosion_protection", "a_explosion", 1, False),
    ("shock_protection", "a_shock", 1, False),
    ("burn_protection", "a_burn", 1, False),
    ("chemical_burn_protection", "a_chem", 1, False),
    ("radiation_protection", "a_rad", 1, False),
    ("telepathy_protection", "a_psy", 1, False),
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


def weapon_pts(stats: dict[str, Any], weights: dict[str, float], cost_mult: float) -> int:
    sum_nw = 0.0
    sum_w = 0.0
    for stat_key, wkey, ceiling, inverse in WEAPON_WEIGHTS:
        w = float(weights.get(wkey, 0.5) or 0)
        if w <= 0:
            continue
        raw = float(stats.get(stat_key) or 0)
        n01 = _inv_norm01(raw, ceiling) if inverse else _norm01(raw, ceiling)
        if stat_key in ("scope", "silencer"):
            n01 = 1.0 if raw > 0 else 0.0
        sum_nw += n01 * w
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
    sum_nw = 0.0
    sum_w = 0.0
    for stat_key, wkey, _ceil, _inv in ARMOR_WEIGHTS:
        w = float(weights.get(wkey, 0.5) or 0)
        if w <= 0:
            continue
        raw = float(stats.get(stat_key) or 0)
        n01 = _clamp01(raw)
        sum_nw += n01 * w
        sum_w += w
    w_price = float(weights.get("a_price", 0.5) or 0)
    if w_price > 0:
        soft = 20000 if is_helmet else 100000
        cost = float(stats.get("cost") or 0)
        n01 = _inv_norm01(cost, soft)
        sum_nw += n01 * w_price
        sum_w += w_price
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
