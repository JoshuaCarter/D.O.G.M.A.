"""Weight → pts fold (live UI / export) for SALE loadouts."""

from __future__ import annotations

import math
import re
from typing import Any

# Normalization curves for Scale max (0→ceiling → 0→1 contribution).
CURVE_LINEAR = "linear"
CURVE_EXP = "exp"
CURVE_LOG = "log"
CURVE_IDS = (CURVE_LINEAR, CURVE_EXP, CURVE_LOG)
CURVE_LABELS = {
    CURVE_LINEAR: "Linear",
    CURVE_EXP: "Exponential",
    CURVE_LOG: "Logarithmic",
}
# Steepness for exp/log maps (both pin 0→0 and 1→1).
_CURVE_K = 2.5


def normalize_curve(curve: str | None) -> str:
    c = (curve or CURVE_LINEAR).strip().lower()
    if c in ("exponential", "expo"):
        return CURVE_EXP
    if c in ("logarithmic", "loga"):
        return CURVE_LOG
    if c in CURVE_IDS:
        return c
    return CURVE_LINEAR


def apply_score_curve(t01: float, curve: str | None) -> float:
    """Map linear 0–1 contribution through linear / exp / log."""
    t = _clamp01(t01)
    c = normalize_curve(curve)
    if c == CURVE_EXP:
        return (math.exp(_CURVE_K * t) - 1.0) / (math.exp(_CURVE_K) - 1.0)
    if c == CURVE_LOG:
        return math.log(1.0 + _CURVE_K * t) / math.log(1.0 + _CURVE_K)
    return t


def merge_curves(stored: dict[str, Any] | None) -> dict[str, str]:
    """Sparse stat_key → curve id (omit / linear = default)."""
    out: dict[str, str] = {}
    if not isinstance(stored, dict):
        return out
    for k, v in stored.items():
        c = normalize_curve(str(v) if v is not None else None)
        if c != CURVE_LINEAR:
            out[str(k)] = c
    return out

# (stat_key, weight_key, scale_slider_max, inverse, default_weight)
# default weight 0 = excluded until the slider is raised; regenerate always stores all stats.
# Order = SALE sidebar / detail table. Score cols = tip Min..Max torso tiers.
# scale_slider_max = hard max for the Scale max slider AND default 0–x ceiling.
# Hit power: engine/Lua is 0–1; SALE display/scoring/ceilings use tip-style percent.
HIT_POWER_PCT_CEILING = 200.0
# Values at or below this are treated as engine fractions and ×100 for SALE.
_HIT_POWER_FRACTION_MAX = 5.0
# Armor protections stay as engine raw in items.yml. Display / Diff / scoring use
# Better Stats Bars tip %: ceil(clamp(|raw| / (max_damage * factor), 0, 1) * 100).
# (Vanilla utils_ui uses actor_condition zone maxes instead — that reads ~23% rad here.)
PROTECTION_TIP_CEILING = 100.0
FRACTION_STAT_KEYS = frozenset(
    {
        "radiation_protection",
        "fire_wound_protection",
        "strike_protection",
        "wound_protection",
        "explosion_protection",
        "shock_protection",
        "burn_protection",
        "chemical_burn_protection",
        "telepathy_protection",
    }
)
# Tip denom = max_damage[k] * factor (BSB prepare_stats_table).
# Defaults ≈ G.A.M.M.A. Keybinds fixes BSB scan (ignore_sections + use_game_values=max).
DEFAULT_PROTECTION_TIP_DENOM: dict[str, float] = {
    "fire_wound_protection": 1.236,
    "burn_protection": 10.0,
    "shock_protection": 10.0,
    "chemical_burn_protection": 8.0,
    "radiation_protection": 0.102,
    "telepathy_protection": 0.6,
    "wound_protection": 1.9,
    "strike_protection": 2.0,
    "explosion_protection": 3.0,
}
# Back-compat alias used by app load paths.
DEFAULT_PROTECTION_ZONE_MAX = DEFAULT_PROTECTION_TIP_DENOM

# BSB stats_prot_to_dmg + damage_threshold (G.A.M.M.A. Keybinds fixes copy).
_BSB_HIT_TO_DMG = {
    "burn": "fire",
    "light_burn": "fire",
    "shock": "shock",
    "chemical_burn": "acid",
    "acid": "acid",
    "radiation": "radia",
    "radia": "radia",
    "telepatic": "psi",
    "psi": "psi",
    "strike": "wound",
    "explosion": "explosion",
    "wound": "wound",
    "fire_wound": "fire_wound",
}
_BSB_DAMAGE_THRESHOLD = {
    "fire": 1.0,
    "shock": 1.0,
    "radia": 0.1,
    "psi": 1.0,
    "acid": 1.0,
    "wound": 1.96,
    "fire_wound": 1.37,
    "explosion": 3.1,
    "strike": 2.0,
}
_BSB_TIP_FACTOR: dict[str, tuple[str, float]] = {
    "fire_wound_protection": ("fire_wound", 1.0),
    "burn_protection": ("fire", 10.0),
    "shock_protection": ("shock", 10.0),
    "chemical_burn_protection": ("acid", 10.0),
    "radiation_protection": ("radia", 10.0),
    "telepathy_protection": ("psi", 1.0),
    "wound_protection": ("wound", 1.0),
    "strike_protection": ("strike", 1.0),
    "explosion_protection": ("explosion", 1.0),
}
# Non-prefix entries from BSB ignore_sections (mp_* / wpn_knife* handled by prefix).
_BSB_IGNORE_SECTIONS = frozenset(
    {
        "bibliotekar_normal",
        "bibliotekar_strong",
        "bibliotekar_weak",
        "campfire",
        "campfire_base",
        "campfire_base_noshadow",
        "fireball_acidic_zone",
        "fireball_electric_zone",
        "fireball_zone",
        "generator_dust",
        "generator_dust_static",
        "generator_electra",
        "generator_torrid",
        "m_bibliotekar_e",
        "pri_a17_gauss_rifle",
        "zone_burning_fuzz",
        "zone_burning_fuzz1",
        "zone_burning_fuzz_average",
        "zone_burning_fuzz_strong",
        "zone_burning_fuzz_weak",
        "zone_buzz",
        "zone_buzz_average",
        "zone_buzz_strong",
        "zone_buzz_weak",
        "zone_gravi_zone",
        "zone_hvatalka",
        "zone_liana",
        "zone_mine_acidic",
        "zone_mine_acidic_big",
        "zone_mine_electric",
        "zone_mine_thermal",
        "zone_monolith",
        "zone_no_gravity",
        "zone_radioactive",
        "zone_sarcofag",
        "zone_student",
        "zone_teleport",
        "zone_witches_galantine",
        "zone_witches_galantine_average",
        "zone_witches_galantine_strong",
        "zone_witches_galantine_weak",
        "zone_zharka_static",
        "zone_zharka_static_average",
        "zone_zharka_static_strong",
        "zone_zharka_static_weak",
    }
)


def _bsb_ignored_section(sec: str) -> bool:
    if not sec:
        return True
    if sec.startswith("wpn_knife") or sec.startswith("mp_"):
        return True
    return sec in _BSB_IGNORE_SECTIONS


def hit_power_pct(raw: Any) -> float:
    """Convert engine hit_power (0–1) to SALE percent; leave percent values as-is."""
    try:
        v = float(raw or 0)
    except (TypeError, ValueError):
        return 0.0
    if v <= 0:
        return 0.0
    if v <= _HIT_POWER_FRACTION_MAX:
        return v * 100.0
    return v


def _f_ltx(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def build_bsb_max_damages(
    sections: dict[str, dict[str, str]] | None,
    *,
    abf_compatibility: bool = False,
) -> dict[str, float]:
    """Mirror G.A.M.M.A. Keybinds BSB build_tables() (use_game_values=max)."""
    out = {k: 0.0 for k in _BSB_DAMAGE_THRESHOLD}
    if not isinstance(sections, dict):
        return out

    def add_damage(dmg: float, key: str) -> None:
        thr = _BSB_DAMAGE_THRESHOLD.get(key)
        if thr is None:
            return
        if dmg > 0 and dmg <= thr and dmg > out[key]:
            out[key] = dmg

    for sec, d in sections.items():
        if not isinstance(d, dict) or _bsb_ignored_section(sec):
            continue
        hit = (d.get("hit_type") or d.get("hit_type_blast") or "").strip().lower()
        if not hit:
            continue
        dmg_key = _BSB_HIT_TO_DMG.get(hit)
        if not dmg_key:
            continue

        dmg = 0.0
        ap = (d.get("attack_params") or "").strip()
        if dmg_key == "fire_wound":
            hp_s = (d.get("hit_power") or "0").split(",")[0].strip()
            base = _f_ltx(hp_s)
            ammo = d.get("ammo_class") or ""
            scale = 1.0
            if ammo.strip():
                mx = 0.0
                for a in ammo.split(","):
                    a = a.strip()
                    if not a:
                        continue
                    ad = sections.get(a) or {}
                    ka = _f_ltx(ad.get("k_hit"), 1.0)
                    if ka > mx and ka <= _BSB_DAMAGE_THRESHOLD["fire_wound"]:
                        mx = ka
                scale = mx or 1.0
            dmg = base * scale
        elif ap:
            apsec = sections.get(ap) or {}
            mx = 0.0
            thr = _BSB_DAMAGE_THRESHOLD[dmg_key]
            for val in apsec.values():
                parts = [p.strip() for p in str(val).split(",")]
                if len(parts) != 11:
                    continue
                dd = _f_ltx(parts[1])
                if dd > mx and dd <= thr:
                    mx = dd
            dmg = mx
        elif dmg_key == "explosion":
            dmg = _f_ltx(d.get("hit_power") or d.get("hit_power_blast"))
            if dmg <= 0:
                pwr = _f_ltx(d.get("max_start_power"))
                if pwr > 0:
                    dmg = pwr
        else:
            pwr = _f_ltx(d.get("max_start_power"))
            if pwr > 0:
                scale = 1.0
                cls = (d.get("class") or "").upper()
                if abf_compatibility and dmg_key in ("fire", "acid"):
                    scale = 0.1
                elif cls == "ZS_RADIO":
                    scale = 0.1
                dmg = pwr * scale

        add_damage(dmg, dmg_key)
        if hit == "strike":
            add_damage(dmg, "strike")
        elif hit == "explosion":
            add_damage(dmg, "explosion")
        tube = _f_ltx(d.get("tube_damage"))
        if tube > 0:
            add_damage(min(tube * 0.5, 1.0), "psi")
    return out


def protection_tip_denoms_from_sections(
    sections: dict[str, dict[str, str]] | None,
) -> dict[str, float]:
    """Tip denominators (max_damage * factor) from merged configs + BSB factors."""
    out = dict(DEFAULT_PROTECTION_TIP_DENOM)
    max_d = build_bsb_max_damages(sections)
    for sk, (dk, factor) in _BSB_TIP_FACTOR.items():
        md = float(max_d.get(dk) or 0)
        if md > 0 and factor > 0:
            out[sk] = md * factor
    return out


def protection_zones_from_sections(
    sections: dict[str, dict[str, str]] | None,
) -> dict[str, float]:
    """Alias: tip denoms used as the |raw|/denom scale for armor tip %."""
    return protection_tip_denoms_from_sections(sections)


def protection_tip_pct(
    raw: Any,
    key: str,
    zones: dict[str, float] | None = None,
) -> float:
    """Engine protection → BSB tip-style percent (continuous; tip UI ceils)."""
    try:
        v = float(raw or 0)
    except (TypeError, ValueError):
        return 0.0
    if v <= 0:
        return 0.0
    zmap = zones or DEFAULT_PROTECTION_TIP_DENOM
    z = float(zmap.get(key) or DEFAULT_PROTECTION_TIP_DENOM.get(key) or 1.0)
    if z <= 0:
        z = 1.0
    return min(PROTECTION_TIP_CEILING, abs(v) / z * 100.0)


def protection_tip_display(
    raw: Any,
    key: str,
    zones: dict[str, float] | None = None,
) -> int:
    """Inventory tip percent text: ceil(clamp(|raw|/denom,0,1)*100)."""
    pct = protection_tip_pct(raw, key, zones)
    if pct <= 0:
        return 0
    return int(math.ceil(pct - 1e-12))


def fraction_pct(
    raw: Any,
    key: str | None = None,
    zones: dict[str, float] | None = None,
) -> float:
    """Tip-style percent for Diff / sort; key required for accurate zone scaling."""
    if key:
        return protection_tip_pct(raw, key, zones)
    try:
        v = float(raw or 0)
    except (TypeError, ValueError):
        return 0.0
    if v <= 0:
        return 0.0
    if v <= _HIT_POWER_FRACTION_MAX:
        return v * 100.0
    return v


WEAPON_WEIGHTS = [
    ("cost", "w_price", 200000, False, 0.5),
    ("hit_power", "w_hit_power", HIT_POWER_PCT_CEILING, False, 0.0),
    ("min_dmg", "w_min_dmg", 500, False, 0.0),
    ("lgt_dmg", "w_lgt_dmg", 500, False, 0.0),
    ("lgtp_dmg", "w_lgtp_dmg", 500, False, 0.0),
    ("mid_dmg", "w_mid_dmg", 500, False, 0.0),
    ("midp_dmg", "w_midp_dmg", 500, False, 0.0),
    ("hvy_dmg", "w_hvy_dmg", 500, False, 0.0),
    ("hvyp_dmg", "w_hvyp_dmg", 500, False, 0.0),
    ("exo_dmg", "w_exo_dmg", 500, False, 0.0),
    ("max_dmg", "w_max_dmg", 500, False, 0.0),
    ("min_dps", "w_min_dps", 500, False, 0.5),
    ("lgt_dps", "w_lgt_dps", 500, False, 0.5),
    ("lgtp_dps", "w_lgtp_dps", 500, False, 0.5),
    ("mid_dps", "w_mid_dps", 500, False, 0.5),
    ("midp_dps", "w_midp_dps", 500, False, 0.5),
    ("hvy_dps", "w_hvy_dps", 500, False, 0.5),
    ("hvyp_dps", "w_hvyp_dps", 500, False, 0.5),
    ("exo_dps", "w_exo_dps", 500, False, 0.5),
    ("max_dps", "w_max_dps", 500, False, 0.5),
    ("reload_s", "w_reload", 10, True, 0.0),
    ("rpm", "w_rpm", 1200, False, 0.0),
    ("mag", "w_mag", 200, False, 0.0),
    ("burst", "w_burst", 20, False, 0.5),
    ("spread_ads", "w_spread_ads", 3, True, 0.5),
    ("spread_hip", "w_spread_hip", 10, True, 0.5),
    ("scope", "w_scope", 1, False, 0.5),
    ("att", "w_att", 1, False, 0.5),
    ("silencer", "w_silencer", 1, False, 0.5),
]

# Binary flags — no 0–x scale slider.
NO_CEILING_STATS = frozenset({"scope", "att", "silencer"})

# (stat_key, weight_key, scale_slider_max, inverse, default_weight)
# a_price / cost ceiling is added separately (outfit vs helmet soft cap).
# carry_weight / artefact_count are outfit-only (hidden for helmets).
OUTFIT_ONLY_STATS = frozenset({"carry_weight", "artefact_count"})
CARRY_WEIGHT_PCT_CEILING = 150.0
ARTEFACT_COUNT_CEILING = 8.0
ARMOR_WEIGHTS = [
    ("radiation_protection", "a_rad", PROTECTION_TIP_CEILING, False, 0.5),
    ("fire_wound_protection", "a_fire_wound", PROTECTION_TIP_CEILING, False, 0.5),
    ("strike_protection", "a_strike", PROTECTION_TIP_CEILING, False, 0.5),
    ("wound_protection", "a_wound", PROTECTION_TIP_CEILING, False, 0.5),
    ("explosion_protection", "a_explosion", PROTECTION_TIP_CEILING, False, 0.5),
    ("shock_protection", "a_shock", PROTECTION_TIP_CEILING, False, 0.5),
    ("burn_protection", "a_burn", PROTECTION_TIP_CEILING, False, 0.5),
    ("chemical_burn_protection", "a_chem", PROTECTION_TIP_CEILING, False, 0.5),
    ("telepathy_protection", "a_psy", PROTECTION_TIP_CEILING, False, 0.5),
    ("carry_weight", "a_carry", CARRY_WEIGHT_PCT_CEILING, False, 0.5),
    ("artefact_count", "a_artefact", ARTEFACT_COUNT_CEILING, False, 0.5),
]

ARMOR_COST_CEILING_OUTFIT = 100000.0
ARMOR_COST_CEILING_HELMET = 20000.0


def armor_weight_rows(*, is_helmet: bool) -> list[tuple]:
    """ARMOR_WEIGHTS rows visible for this category (outfits get carry/slots)."""
    if is_helmet:
        return [t for t in ARMOR_WEIGHTS if t[0] not in OUTFIT_ONLY_STATS]
    return list(ARMOR_WEIGHTS)


def default_weapon_weights() -> dict[str, float]:
    """Code-level default weapon score weights (pre any balance.yml)."""
    return {wkey: float(default_w) for _sk, wkey, _c, _i, default_w in WEAPON_WEIGHTS}


def default_ceilings_weapon() -> dict[str, float]:
    return {
        sk: float(ceiling)
        for sk, _wk, ceiling, _inv, _dw in WEAPON_WEIGHTS
        if sk not in NO_CEILING_STATS
    }


def default_ceilings_armor(*, is_helmet: bool) -> dict[str, float]:
    out = {
        sk: float(ceiling)
        for sk, _wk, ceiling, _inv, _dw in armor_weight_rows(is_helmet=is_helmet)
    }
    out["cost"] = (
        ARMOR_COST_CEILING_HELMET if is_helmet else ARMOR_COST_CEILING_OUTFIT
    )
    return out


def merge_ceilings(
    defaults: dict[str, float], stored: dict[str, Any] | None
) -> dict[str, float]:
    out = dict(defaults)
    if isinstance(stored, dict):
        for k, v in stored.items():
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if fv <= 0:
                continue
            key = str(k)
            # Old balance used 0–1 hit_power / protection ceilings; ignore those.
            if key == "hit_power" and fv <= _HIT_POWER_FRACTION_MAX:
                continue
            if key in FRACTION_STAT_KEYS and fv <= _HIT_POWER_FRACTION_MAX:
                continue
            # Cap at code slider max (defaults); drop runaway values from old ×5 UI.
            cap = out.get(key)
            if cap is not None and fv > cap:
                fv = cap
            out[key] = fv
    return out

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
}

FACTIONS = list(FACTION_BLOC.keys())

# UI display names (ids stay stalker/killer/greh/…).
# Anomaly: killer = Mercenary, greh = Sin (not the other way around).
FACTION_LABELS = {
    "stalker": "Loner",
    "bandit": "Bandit",
    "dolg": "Duty",
    "army": "Military",
    "renegade": "Renegade",
    "ecolog": "Ecologists",
    "freedom": "Freedom",
    "killer": "Mercenary",
    "isg": "ISG",
    "csky": "Clear Sky",
    "monolith": "Monolith",
    "greh": "Sin",
}


def faction_label(faction: str) -> str:
    fac = (faction or "").strip()
    if fac == "Default":
        return "Baseline"
    return FACTION_LABELS.get(fac, fac)


# Underscore tokens in section ids → SALE faction id.
# e.g. wpn_ks23_ecolog → ecolog, wpn_aek_duty → dolg, wpn_aug_merc → killer.
_SECTION_FACTION_TOKENS = {
    "ecolog": "ecolog",
    "freedom": "freedom",
    "duty": "dolg",
    "dolg": "dolg",
    "bandit": "bandit",
    "army": "army",
    "stalker": "stalker",
    "loner": "stalker",
    "monolith": "monolith",
    "csky": "csky",
    "killer": "killer",
    "merc": "killer",
    "renegade": "renegade",
    "isg": "isg",
    "greh": "greh",
    "sin": "greh",
}


def section_name_faction_token(sec: str) -> str | None:
    """Raw faction key token from ``sec`` (e.g. ``ecolog``, ``duty``, ``merc``)."""
    parts = [p for p in (sec or "").strip().lower().split("_") if p]
    for part in reversed(parts):
        if part in _SECTION_FACTION_TOKENS:
            return part
    return None


def section_name_faction(sec: str) -> str | None:
    """If ``sec`` embeds a faction key token, return that faction id.

    Scans underscore parts from the end so ``wpn_ak74u_m1_isg`` → isg and
    style prefixes like ``military_…`` do not steal the match.
    """
    tok = section_name_faction_token(sec)
    if tok is None:
        return None
    return _SECTION_FACTION_TOKENS.get(tok)


def weapon_name_faction_ok(sec: str, faction: str) -> bool:
    """False when name-locked to another faction (paint / tooltip only)."""
    fac = (faction or "").strip()
    if not fac or fac == "Default":
        return True
    locked = section_name_faction(sec)
    if locked is None:
        return True
    return locked == fac


def armor_faction_ok(
    sec: str,
    entry: dict[str, Any] | None,
    faction: str,
    *,
    allow_universal: bool = True,
) -> bool:
    """Whether outfit/helmet matches the viewed faction (paint / tooltip only).

    Does not control LTX include — checkbox alone does.

    - Explicit ``community`` matching the faction always passes (so
      ``cs_stalker_outfit`` / community=csky stays Clear Sky despite the
      ``stalker`` name token).
    - Empty / ``actor`` community passes only when ``allow_universal`` and the
      section is not name-locked to another faction.
    - Any other community fails (painted wrong-faction).
    """
    fac = (faction or "").strip()
    if not fac or fac == "Default":
        return True
    want = FACTION_COMMUNITY.get(fac, fac)
    community = ((entry or {}).get("community") or "").strip()
    if community == want:
        return True
    if allow_universal and community in ("", "actor"):
        return weapon_name_faction_ok(sec, fac)
    return False


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


# Raw score display multiplier (UI only): show round(score01 * 1000).
SCORE_RAW_DISPLAY = 1000


def score_raw_display(score01: float) -> int:
    """Integer raw score for tiles: score01 × 1000 (no fractional part)."""
    return int(round(_clamp01(score01 or 0.0) * SCORE_RAW_DISPLAY))


def _terms_score01(terms: dict[str, tuple[float, float]]) -> float:
    sum_nw = 0.0
    sum_w = 0.0
    for w, final in terms.values():
        if w <= 0:
            continue
        sum_nw += final
        sum_w += w
    if sum_w <= 0:
        return 0.0
    return _clamp01(sum_nw / sum_w)


_SPREAD_STATS = frozenset({"spread_ads", "spread_hip"})


def weapon_stat_terms(
    stats: dict[str, Any],
    weights: dict[str, float],
    ceilings: dict[str, float] | None = None,
    curves: dict[str, str] | None = None,
    *,
    kind: str | None = None,
    zero_shotgun_spread: bool = False,
) -> dict[str, tuple[float, float]]:
    """stat_key → (slider_weight, final_weight) where final = curve(n01) * w."""
    ceil = merge_ceilings(default_ceilings_weapon(), ceilings)
    cmap = merge_curves(curves)
    skip_spread = bool(zero_shotgun_spread) and (
        str(kind or "").strip().lower() == "w_shotgun"
    )
    out: dict[str, tuple[float, float]] = {}
    for stat_key, wkey, default_ceiling, inverse, default_w in WEAPON_WEIGHTS:
        w = float(weights.get(wkey, default_w) or 0)
        if skip_spread and stat_key in _SPREAD_STATS:
            # Drop spread from the weighted average for shotguns.
            out[stat_key] = (0.0, 0.0)
            continue
        raw = float(stats.get(stat_key) or 0)
        if stat_key == "hit_power":
            raw = hit_power_pct(raw)
        if w <= 0:
            out[stat_key] = (w, 0.0)
            continue
        if stat_key in ("scope", "att", "silencer"):
            # Binary flag: only 0 or 1 enters the weighted score.
            n01 = 1.0 if raw > 0 else 0.0
        else:
            ceiling = float(ceil.get(stat_key, default_ceiling) or default_ceiling)
            n01 = _inv_norm01(raw, ceiling) if inverse else _norm01(raw, ceiling)
            n01 = apply_score_curve(n01, cmap.get(stat_key))
        out[stat_key] = (w, n01 * w)
    return out


def armor_stat_terms(
    stats: dict[str, Any],
    weights: dict[str, float],
    *,
    is_helmet: bool,
    ceilings: dict[str, float] | None = None,
    curves: dict[str, str] | None = None,
    zones: dict[str, float] | None = None,
) -> dict[str, tuple[float, float]]:
    """stat_key → (slider_weight, final_weight); includes cost via a_price."""
    ceil = merge_ceilings(default_ceilings_armor(is_helmet=is_helmet), ceilings)
    cmap = merge_curves(curves)
    out: dict[str, tuple[float, float]] = {}
    for stat_key, wkey, default_ceiling, _inv, default_w in armor_weight_rows(
        is_helmet=is_helmet
    ):
        w = float(weights.get(wkey, default_w) or 0)
        if stat_key in FRACTION_STAT_KEYS:
            raw = protection_tip_pct(stats.get(stat_key), stat_key, zones)
        else:
            try:
                raw = float(stats.get(stat_key) or 0)
            except (TypeError, ValueError):
                raw = 0.0
        if w <= 0:
            out[stat_key] = (w, 0.0)
            continue
        ceiling = float(ceil.get(stat_key, default_ceiling) or default_ceiling)
        n01 = apply_score_curve(_norm01(raw, ceiling), cmap.get(stat_key))
        out[stat_key] = (w, n01 * w)
    w_price = float(weights.get("a_price", 0.5) or 0)
    cost = float(stats.get("cost") or 0)
    if w_price <= 0:
        out["cost"] = (w_price, 0.0)
    else:
        soft = float(
            ceil.get(
                "cost",
                ARMOR_COST_CEILING_HELMET if is_helmet else ARMOR_COST_CEILING_OUTFIT,
            )
        )
        # Higher cost → higher score (same direction as weapon w_price).
        n01 = apply_score_curve(_norm01(cost, soft), cmap.get("cost"))
        out["cost"] = (w_price, n01 * w_price)
    return out


def weapon_score01(
    stats: dict[str, Any],
    weights: dict[str, float],
    ceilings: dict[str, float] | None = None,
    curves: dict[str, str] | None = None,
    *,
    kind: str | None = None,
    zero_shotgun_spread: bool = False,
) -> float:
    """Weighted average score in 0–1 (input to price scale + raw display)."""
    return _terms_score01(
        weapon_stat_terms(
            stats,
            weights,
            ceilings,
            curves,
            kind=kind,
            zero_shotgun_spread=zero_shotgun_spread,
        )
    )


# In-game gun repair class chips (G.A.M.M.A. Repair Kit Renaming).
REPAIR_TYPE_TIER = {
    "pistol": "A",
    "shotgun": "B",
    "rifle_5": "C",
    "rifle_7": "D",
}


def repair_type_tier(repair_type: str | None) -> str:
    """Map LTX ``repair_type`` → inventory letter A..D (blank if unknown)."""
    key = (repair_type or "").strip().lower()
    return REPAIR_TYPE_TIER.get(key, "")


def armor_score01(
    stats: dict[str, Any],
    weights: dict[str, float],
    *,
    is_helmet: bool,
    ceilings: dict[str, float] | None = None,
    curves: dict[str, str] | None = None,
    zones: dict[str, float] | None = None,
) -> float:
    """Weighted average score in 0–1 (input to price scale + raw display)."""
    return _terms_score01(
        armor_stat_terms(
            stats,
            weights,
            is_helmet=is_helmet,
            ceilings=ceilings,
            curves=curves,
            zones=zones,
        )
    )


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
    return s.endswith("_bad") or s.endswith("_verybad") or "_bad_" in s


_AMMO_CALIBRE_RE = re.compile(
    r"^(ammo_[0-9]+(?:\.[0-9]+)?x[0-9]+(?:\.[0-9]+)?)", re.IGNORECASE
)
_AMMO_NUMERIC_RE = re.compile(r"^(ammo_[0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)


def ammo_family(sec: str) -> str:
    """Collapse ammo_5.56x45_fmj_bad → ammo_5.56x45 (NATO/WP grouping only)."""
    s = (sec or "").strip()
    if not s:
        return ""
    s = re.sub(r"_(bad|verybad)$", "", s, flags=re.IGNORECASE)
    m = _AMMO_CALIBRE_RE.match(s)
    if m:
        return m.group(1).lower()
    m = _AMMO_NUMERIC_RE.match(s)
    if m:
        return m.group(1).lower()
    parts = s.split("_")
    if len(parts) >= 2:
        return f"{parts[0]}_{parts[1]}".lower()
    return s.lower()


def ammo_family_label(family: str) -> str:
    """ammo_5.56x45 → 5.56x45"""
    f = (family or "").strip()
    if f.lower().startswith("ammo_"):
        return f[5:]
    return f or "?"


# Per-family origin: "nato" | "wp" | "both" | "neither"
# Keys are ammo_family() ids.
# Rule: NATO / Warsaw = standard military rifle/pistol/MG calibres for that bloc.
# Civilian revolver, WWII, oddball, and **all shotgun shells** → neither (OTHER).
_AMMO_FAMILY_BLOC: dict[str, str] = {
    # NATO / Western military standards
    "ammo_5.56x45": "nato",
    "ammo_9x19": "nato",
    "ammo_7.62x51": "nato",
    "ammo_338": "nato",
    "ammo_magnum": "nato",  # .300 Win Mag — Western precision rifle
    "ammo_5.7x28": "nato",
    # Warsaw Pact / Soviet–Russian military standards
    "ammo_5.45x39": "wp",
    "ammo_7.62x39": "wp",
    "ammo_9x18": "wp",
    "ammo_7.62x25": "wp",
    "ammo_9x39": "wp",
    "ammo_12.7x55": "wp",
    "ammo_7.62x54": "wp",
    "ammo_pkm": "wp",  # 7.62×54R belts
    "ammo_9x21": "wp",  # 9×21 Gyurza (SR-1)
    # OTHER — shotgun shells, revolver, WWII, oddball
    "ammo_12x70": "neither",
    "ammo_12x76": "neither",
    "ammo_20x70": "neither",
    "ammo_23x75": "neither",  # KS-23
    "ammo_23": "neither",
    "ammo_357": "neither",  # .357 Magnum
    "ammo_11.43x23": "neither",  # .45 ACP
    "ammo_7.92x33": "neither",  # 7.92×33 Kurz
    "ammo_gauss": "neither",
}


def ammo_family_bloc(family: str) -> str:
    """Return nato|wp|both|neither for an ammo family."""
    fam = (family or "").strip().lower()
    if fam in _AMMO_FAMILY_BLOC:
        return _AMMO_FAMILY_BLOC[fam]
    # Heuristic fallback for unknown families (keeps UI useful after new ammo).
    s = fam
    if any(
        x in s
        for x in (
            "5.56",
            "9x19",
            "7.62x51",
            "5.7",
            "338",
            "magnum",
            ".308",
        )
    ):
        return "nato"
    if any(
        x in s
        for x in (
            "5.45",
            "7.62x39",
            "9x18",
            "7.62x25",
            "9x39",
            "12.7x55",
            "7.62x54",
            "pkm",
            "9x21",
            "pmm",
        )
    ):
        return "wp"
    if any(x in s for x in ("12x70", "12x76", "20x70", "23x75", "shot")):
        return "neither"
    return "neither"


def weapon_ammo_bloc(ammo_class: object) -> str:
    """nato|wp|both|neither from a gun's ammo_class (same table as ammo sidebar)."""
    if isinstance(ammo_class, str):
        parts = [p.strip() for p in ammo_class.split(",") if p.strip()]
    elif isinstance(ammo_class, (list, tuple)):
        parts = [str(p).strip() for p in ammo_class if str(p).strip()]
    else:
        parts = []
    blocs = {ammo_family_bloc(ammo_family(p)) for p in parts}
    nato = "nato" in blocs
    wp = "wp" in blocs
    if nato and wp:
        return "both"
    if nato:
        return "nato"
    if wp:
        return "wp"
    return "neither"


def ammo_bloc_tag(bloc: str) -> str:
    """Short badge: N / W / NW / -"""
    return {"nato": "N", "wp": "W", "both": "NW", "neither": "-"}.get(
        (bloc or "").lower(), "-"
    )


def ammo_bloc_label(bloc: str) -> str:
    return {
        "nato": "NATO",
        "wp": "Warsaw",
        "both": "NATO + Warsaw",
        "neither": "neither",
    }.get((bloc or "").lower(), "neither")


def ammo_section_label(sec: str) -> str:
    """ammo_5.56x45_fmj → 5.56x45 fmj"""
    s = (sec or "").strip()
    if s.lower().startswith("ammo_"):
        s = s[5:]
    return s.replace("_", " ") or "?"


def collect_ammo_sections(weapons: dict[str, Any] | None) -> list[str]:
    """Sorted unique ammo sections used by any weapon (excludes _bad / _verybad)."""
    seen: set[str] = set()
    out: list[str] = []
    for entry in (weapons or {}).values():
        for a in entry.get("ammo_class") or []:
            sec = str(a).strip()
            if sec and not is_bad_ammo(sec) and sec not in seen:
                seen.add(sec)
                out.append(sec)
    return sorted(out)


def ammo_section_cost(ammo_pool: dict[str, Any] | None, sec: str) -> float:
    """Inventory cost for an ammo section (0 if unknown)."""
    entry = (ammo_pool or {}).get(sec) or {}
    try:
        return float(entry.get("cost") or 0)
    except (TypeError, ValueError):
        return 0.0


def sort_ammo_sections_by_family_price(
    sections: list[str],
    ammo_pool: dict[str, Any] | None = None,
) -> list[str]:
    """Keep calibre families together; order families and members by cost asc."""
    secs = [str(s).strip() for s in sections if str(s).strip()]
    if not secs:
        return []
    fam_min: dict[str, float] = {}
    for sec in secs:
        fam = ammo_family(sec)
        c = ammo_section_cost(ammo_pool, sec)
        prev = fam_min.get(fam)
        if prev is None or c < prev:
            fam_min[fam] = c
    return sorted(
        secs,
        key=lambda s: (
            fam_min.get(ammo_family(s), 0.0),
            ammo_family(s),
            ammo_section_cost(ammo_pool, s),
            s,
        ),
    )


def ammo_is_enabled(ammo_sec: str, enabled_map: dict[str, bool] | None) -> bool:
    """Missing keys mean enabled. Legacy family keys still apply to matching sections."""
    em = enabled_map or {}
    sec = (ammo_sec or "").strip()
    if not sec:
        return True
    if sec in em:
        return bool(em[sec])
    fam = ammo_family(sec)
    if fam in em:
        return bool(em[fam])
    return True


def weapon_enabled_ammos(
    ammo_class: list[str] | None, enabled_map: dict[str, bool] | None
) -> list[str]:
    """Enabled non-bad ammo sections for a weapon."""
    out: list[str] = []
    for a in ammo_class or []:
        sec = str(a).strip()
        if sec and not is_bad_ammo(sec) and ammo_is_enabled(sec, enabled_map):
            out.append(sec)
    return out

