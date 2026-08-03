"""Display names + short tooltips for SALE sliders / stats (UI only)."""

from __future__ import annotations

# key -> (display name, short tooltip)
_LABELS: dict[str, tuple[str, str]] = {
    # Controls
    "price_scale_min": (
        "Scale min",
        "Shop pts for the cheapest checked item (price scale always on).",
    ),
    "price_scale_max": (
        "Scale max",
        "Shop pts for the most expensive checked item (price scale always on).",
    ),
    "include_universal_armor": (
        "Include factionless",
        "Empty/actor community outfits/helmets count as this faction "
        "(else painted wrong-faction). Checkbox still decides LTX.",
    ),
    # Meta / sort
    "pts": (
        "Shop pts",
        "Price-scale value: relative 0–1 score maps cheapest→min, dearest→max.",
    ),
    "score_raw": (
        "Raw score",
        "Weighted score 0–1 × 1000 (integer). Sets distance along the price scale.",
    ),
    "name": ("Name", "Friendly item name."),
    "sec": ("ID", "Config section id."),
    "in_ltx": ("In LTX", "Checked for export (checkbox on tile)."),
    "community": ("Community", "Outfit faction community tag."),
    "ammo": ("Ammo", "Calibres this weapon uses."),
    # Weapon stats / weights (score cols = tip Min .. Max torso tiers)
    "cost": ("Cost", "Base item cost (lower scores better when weighted)."),
    "w_price": ("Price", "Base item cost (lower scores better when weighted)."),
    "hit_power": (
        "Hit power",
        "Weapon hit power as percent (engine 0–1 ×100; tip-style). Scale max 0–200.",
    ),
    "w_hit_power": (
        "Hit power",
        "Weapon hit power as percent (engine 0–1 ×100; tip-style). Scale max 0–200.",
    ),
    "min_dmg": ("Dmg vs Min", "Best ammo vs Min torso (0.011); mutant row if higher."),
    "w_min_dmg": ("Dmg vs Min", "Best ammo vs Min torso (0.011); mutant row if higher."),
    "lgt_dmg": ("Dmg vs Lgt", "Best ammo vs Lgt torso armor (0.075)."),
    "w_lgt_dmg": ("Dmg vs Lgt", "Best ammo vs Lgt torso armor (0.075)."),
    "lgtp_dmg": ("Dmg vs Lgt+", "Best ammo vs Lgt+ torso armor (0.1)."),
    "w_lgtp_dmg": ("Dmg vs Lgt+", "Best ammo vs Lgt+ torso armor (0.1)."),
    "mid_dmg": ("Dmg vs Mid", "Best ammo vs Mid torso armor (0.15)."),
    "w_mid_dmg": ("Dmg vs Mid", "Best ammo vs Mid torso armor (0.15)."),
    "midp_dmg": ("Dmg vs Mid+", "Best ammo vs Mid+ torso armor (0.2)."),
    "w_midp_dmg": ("Dmg vs Mid+", "Best ammo vs Mid+ torso armor (0.2)."),
    "hvy_dmg": ("Dmg vs Hvy", "Best ammo vs Hvy torso armor (0.25)."),
    "w_hvy_dmg": ("Dmg vs Hvy", "Best ammo vs Hvy torso armor (0.25)."),
    "hvyp_dmg": ("Dmg vs Hvy+", "Best ammo vs Hvy+ torso armor (0.4)."),
    "w_hvyp_dmg": ("Dmg vs Hvy+", "Best ammo vs Hvy+ torso armor (0.4)."),
    "exo_dmg": ("Dmg vs Exo", "Best ammo vs Exo torso armor (0.55)."),
    "w_exo_dmg": ("Dmg vs Exo", "Best ammo vs Exo torso armor (0.55)."),
    "max_dmg": ("Dmg vs Max", "Best ammo vs Max torso armor (0.65)."),
    "w_max_dmg": ("Dmg vs Max", "Best ammo vs Max torso armor (0.65)."),
    "min_dps": ("DPS vs Min", "Sustained DPS vs Min torso / mutant."),
    "w_min_dps": ("DPS vs Min", "Sustained DPS vs Min torso / mutant."),
    "lgt_dps": ("DPS vs Lgt", "Sustained DPS vs Lgt torso armor."),
    "w_lgt_dps": ("DPS vs Lgt", "Sustained DPS vs Lgt torso armor."),
    "lgtp_dps": ("DPS vs Lgt+", "Sustained DPS vs Lgt+ torso armor."),
    "w_lgtp_dps": ("DPS vs Lgt+", "Sustained DPS vs Lgt+ torso armor."),
    "mid_dps": ("DPS vs Mid", "Sustained DPS vs Mid torso armor."),
    "w_mid_dps": ("DPS vs Mid", "Sustained DPS vs Mid torso armor."),
    "midp_dps": ("DPS vs Mid+", "Sustained DPS vs Mid+ torso armor."),
    "w_midp_dps": ("DPS vs Mid+", "Sustained DPS vs Mid+ torso armor."),
    "hvy_dps": ("DPS vs Hvy", "Sustained DPS vs Hvy torso armor."),
    "w_hvy_dps": ("DPS vs Hvy", "Sustained DPS vs Hvy torso armor."),
    "hvyp_dps": ("DPS vs Hvy+", "Sustained DPS vs Hvy+ torso armor."),
    "w_hvyp_dps": ("DPS vs Hvy+", "Sustained DPS vs Hvy+ torso armor."),
    "exo_dps": ("DPS vs Exo", "Sustained DPS vs Exo torso armor."),
    "w_exo_dps": ("DPS vs Exo", "Sustained DPS vs Exo torso armor."),
    "max_dps": ("DPS vs Max", "Sustained DPS vs Max torso armor."),
    "w_max_dps": ("DPS vs Max", "Sustained DPS vs Max torso armor."),
    "reload_s": ("Reload time", "Reload duration in seconds (lower is better)."),
    "w_reload": ("Reload time", "Reload duration in seconds (lower is better)."),
    "rpm": ("Fire rate", "Rounds per minute."),
    "w_rpm": ("Fire rate", "Rounds per minute."),
    "mag": ("Mag size", "Magazine capacity."),
    "w_mag": ("Mag size", "Magazine capacity."),
    "burst": ("Burst", "Effective controllable burst length."),
    "w_burst": ("Burst", "Effective controllable burst length."),
    "spread_ads": ("ADS spread", "Aim-down-sights dispersion (lower is better)."),
    "w_spread_ads": ("ADS spread", "Aim-down-sights dispersion (lower is better)."),
    "spread_hip": ("Hip spread", "Hipfire dispersion (lower is better)."),
    "w_spread_hip": ("Hip spread", "Hipfire dispersion (lower is better)."),
    "scope": ("Scope", "Has an optic / scoped fire mode."),
    "w_scope": ("Scope", "Has an optic / scoped fire mode."),
    "silencer": ("Silencer", "Supports or includes a suppressor."),
    "w_silencer": ("Silencer", "Supports or includes a suppressor."),
    # Armor
    "a_price": ("Price", "Base item cost (lower scores better when weighted)."),
    "radiation_protection": ("Radiation", "Protection vs radiation."),
    "a_rad": ("Radiation", "Protection vs radiation."),
    "fire_wound_protection": ("Ballistic", "Protection vs bullets / fire-wound."),
    "a_fire_wound": ("Ballistic", "Protection vs bullets / fire-wound."),
    "strike_protection": ("Strike", "Protection vs blunt strike."),
    "a_strike": ("Strike", "Protection vs blunt strike."),
    "wound_protection": ("Wound", "Protection vs rupture / wound."),
    "a_wound": ("Wound", "Protection vs rupture / wound."),
    "explosion_protection": ("Explosion", "Protection vs explosion."),
    "a_explosion": ("Explosion", "Protection vs explosion."),
    "shock_protection": ("Shock", "Protection vs electric shock."),
    "a_shock": ("Shock", "Protection vs electric shock."),
    "burn_protection": ("Burn", "Protection vs burn."),
    "a_burn": ("Burn", "Protection vs burn."),
    "chemical_burn_protection": ("Chemical", "Protection vs chemical burn."),
    "a_chem": ("Chemical", "Protection vs chemical burn."),
    "telepathy_protection": ("Psy", "Protection vs psy / telepathy."),
    "a_psy": ("Psy", "Protection vs psy / telepathy."),
}


def pretty_label(key: str) -> str:
    """Human label for UI; falls back to de-underscored key."""
    k = (key or "").strip()
    if k in _LABELS:
        return _LABELS[k][0]
    return k.replace("_", " ").strip() or "?"


def pretty_tip(key: str) -> str:
    """Short tooltip; empty if unknown."""
    k = (key or "").strip()
    if k in _LABELS:
        return _LABELS[k][1]
    return ""


def pretty_ceiling_tip(stat_key: str) -> str:
    """Tooltip for 0–x scale-max sliders."""
    base = pretty_tip(stat_key)
    head = "Normalization scale max (0–x). Raw values at/above x score as 1.0."
    return f"{head} {base}".strip() if base else head
