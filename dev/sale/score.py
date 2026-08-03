"""Weight → pts fold (live UI / export) for Stat Derived Loadout."""

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
# Order = SALE sidebar / detail table. Score cols = tooltip Min/Lgt/Lgt+/Mid/Mid+.
# scale_slider_max = hard max for the Scale max slider AND default 0–x ceiling.
# Hit power: engine/Lua is 0–1; SALE display/scoring/ceilings use tip-style percent.
HIT_POWER_PCT_CEILING = 200.0
# Values at or below this are treated as engine fractions and ×100 for SALE.
_HIT_POWER_FRACTION_MAX = 5.0


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


WEAPON_WEIGHTS = [
    ("cost", "w_price", 200000, True, 0.5),
    ("hit_power", "w_hit_power", HIT_POWER_PCT_CEILING, False, 0.0),
    ("min_dmg", "w_min_dmg", 500, False, 0.0),
    ("lgt_dmg", "w_lgt_dmg", 500, False, 0.0),
    ("lgtp_dmg", "w_lgtp_dmg", 500, False, 0.0),
    ("mid_dmg", "w_mid_dmg", 500, False, 0.0),
    ("midp_dmg", "w_midp_dmg", 500, False, 0.0),
    ("min_dps", "w_min_dps", 500, False, 0.5),
    ("lgt_dps", "w_lgt_dps", 500, False, 0.5),
    ("lgtp_dps", "w_lgtp_dps", 500, False, 0.5),
    ("mid_dps", "w_mid_dps", 500, False, 0.5),
    ("midp_dps", "w_midp_dps", 500, False, 0.5),
    ("reload_s", "w_reload", 10, True, 0.0),
    ("rpm", "w_rpm", 1200, False, 0.0),
    ("mag", "w_mag", 200, False, 0.0),
    ("burst", "w_burst", 20, False, 0.5),
    ("spread_ads", "w_spread_ads", 3, True, 0.5),
    ("spread_hip", "w_spread_hip", 10, True, 0.5),
    ("scope", "w_scope", 1, False, 0.5),
    ("silencer", "w_silencer", 1, False, 0.5),
]

# Binary flags — no 0–x scale slider.
NO_CEILING_STATS = frozenset({"scope", "silencer"})

# (stat_key, weight_key, scale_slider_max, inverse, default_weight)
# a_price / cost ceiling is added separately (outfit vs helmet soft cap).
ARMOR_WEIGHTS = [
    ("radiation_protection", "a_rad", 1, False, 0.5),
    ("fire_wound_protection", "a_fire_wound", 1, False, 0.5),
    ("strike_protection", "a_strike", 1, False, 0.5),
    ("wound_protection", "a_wound", 1, False, 0.5),
    ("explosion_protection", "a_explosion", 1, False, 0.5),
    ("shock_protection", "a_shock", 1, False, 0.5),
    ("burn_protection", "a_burn", 1, False, 0.5),
    ("chemical_burn_protection", "a_chem", 1, False, 0.5),
    ("telepathy_protection", "a_psy", 1, False, 0.5),
]

ARMOR_COST_CEILING_OUTFIT = 100000.0
ARMOR_COST_CEILING_HELMET = 20000.0


def default_ceilings_weapon() -> dict[str, float]:
    return {
        sk: float(ceiling)
        for sk, _wk, ceiling, _inv, _dw in WEAPON_WEIGHTS
        if sk not in NO_CEILING_STATS
    }


def default_ceilings_armor(*, is_helmet: bool) -> dict[str, float]:
    out = {sk: float(ceiling) for sk, _wk, ceiling, _inv, _dw in ARMOR_WEIGHTS}
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
            # Old balance used 0–1 hit_power ceilings (e.g. 3.0); ignore those.
            if key == "hit_power" and fv <= _HIT_POWER_FRACTION_MAX:
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
    """False when the weapon section is name-locked to another faction."""
    fac = (faction or "").strip()
    if not fac or fac == "Default":
        return True
    locked = section_name_faction(sec)
    if locked is None:
        return True
    return locked == fac


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
    stats: dict[str, Any],
    weights: dict[str, float],
    ceilings: dict[str, float] | None = None,
    curves: dict[str, str] | None = None,
) -> dict[str, tuple[float, float]]:
    """stat_key → (slider_weight, final_weight) where final = curve(n01) * w."""
    ceil = merge_ceilings(default_ceilings_weapon(), ceilings)
    cmap = merge_curves(curves)
    out: dict[str, tuple[float, float]] = {}
    for stat_key, wkey, default_ceiling, inverse, default_w in WEAPON_WEIGHTS:
        w = float(weights.get(wkey, default_w) or 0)
        raw = float(stats.get(stat_key) or 0)
        if stat_key == "hit_power":
            raw = hit_power_pct(raw)
        if w <= 0:
            out[stat_key] = (w, 0.0)
            continue
        if stat_key in ("scope", "silencer"):
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
) -> dict[str, tuple[float, float]]:
    """stat_key → (slider_weight, final_weight); includes cost via a_price."""
    ceil = merge_ceilings(default_ceilings_armor(is_helmet=is_helmet), ceilings)
    cmap = merge_curves(curves)
    out: dict[str, tuple[float, float]] = {}
    for stat_key, wkey, default_ceiling, _inv, default_w in ARMOR_WEIGHTS:
        w = float(weights.get(wkey, default_w) or 0)
        raw = float(stats.get(stat_key) or 0)
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
        n01 = apply_score_curve(_inv_norm01(cost, soft), cmap.get("cost"))
        out["cost"] = (w_price, n01 * w_price)
    return out


def weapon_pts(
    stats: dict[str, Any],
    weights: dict[str, float],
    cost_mult: float,
    ceilings: dict[str, float] | None = None,
    curves: dict[str, str] | None = None,
) -> int:
    terms = weapon_stat_terms(stats, weights, ceilings, curves)
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
    ceilings: dict[str, float] | None = None,
    curves: dict[str, str] | None = None,
) -> int:
    terms = armor_stat_terms(
        stats,
        weights,
        is_helmet=is_helmet,
        ceilings=ceilings,
        curves=curves,
    )
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
# Keys are ammo_family() ids. Shotguns are both (Western + Soviet guns share them).
_AMMO_FAMILY_BLOC: dict[str, str] = {
    # NATO / Western
    "ammo_5.56x45": "nato",
    "ammo_9x19": "nato",
    "ammo_11.43x23": "nato",  # .45 ACP
    "ammo_7.62x51": "nato",
    "ammo_338": "nato",
    "ammo_magnum": "nato",  # .300 / .338-class Western sniper (ammo_magnum_300)
    "ammo_5.7x28": "nato",
    "ammo_357": "nato",
    # Warsaw Pact / Soviet–Russian
    "ammo_5.45x39": "wp",
    "ammo_7.62x39": "wp",
    "ammo_9x18": "wp",
    "ammo_7.62x25": "wp",
    "ammo_9x39": "wp",
    "ammo_12.7x55": "wp",
    "ammo_7.62x54": "wp",
    "ammo_pkm": "wp",  # 7.62×54R belts
    "ammo_9x21": "wp",  # 9×21 Gyurza (SR-1), not IMI
    "ammo_23x75": "wp",  # KS-23
    "ammo_23": "wp",
    # Shared / both
    "ammo_12x70": "both",
    "ammo_12x76": "both",
    "ammo_20x70": "both",
    # Neither (WWII / unique)
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
            "11.43",
            "7.62x51",
            "5.7",
            "338",
            "357",
            "magnum",
            ".45",
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
            "23x75",
            "pmm",
        )
    ):
        return "wp"
    if any(x in s for x in ("12x70", "12x76", "20x70", "shot")):
        return "both"
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


def weapon_ammo_families(ammo_class: list[str] | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for a in ammo_class or []:
        fam = ammo_family(str(a))
        if fam and fam not in seen:
            seen.add(fam)
            out.append(fam)
    return out


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


def collect_ammo_families(weapons: dict[str, Any] | None) -> list[str]:
    """Sorted unique ammo families used by any weapon in the pool."""
    seen: set[str] = set()
    for entry in (weapons or {}).values():
        for fam in weapon_ammo_families(entry.get("ammo_class") or []):
            seen.add(fam)
    return sorted(seen)


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


def weapon_ammo_allowed(
    ammo_class: list[str] | None, enabled_map: dict[str, bool] | None
) -> bool:
    """True if the weapon has ≥1 enabled non-bad ammo (or no ammo_class at all).

    Deselected ammos do not exclude the gun by themselves — only zero enabled does.
    Bad / verybad rounds never count.
    """
    ammos = [str(a).strip() for a in (ammo_class or []) if str(a).strip()]
    if not ammos:
        return True
    good = [a for a in ammos if not is_bad_ammo(a)]
    if not good:
        return False
    return any(ammo_is_enabled(a, enabled_map) for a in good)
