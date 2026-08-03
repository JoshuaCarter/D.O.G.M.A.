"""Display names + short tooltips for SALE sliders / stats (UI only)."""

from __future__ import annotations

# key -> (display name, short tooltip)
_LABELS: dict[str, tuple[str, str]] = {
    # Controls
    "max_pts": ("Max points", "Items at or above this score are out of shop."),
    "cost_mult": ("Cost multiplier", "Scales weighted average into shop points."),
    "include_universal_armor": (
        "Include factionless",
        "Allow outfits with empty/actor community in faction shops.",
    ),
    # Meta / sort
    "pts": ("Points", "Final shop score from weighted stats."),
    "name": ("Name", "Friendly item name."),
    "sec": ("ID", "Config section id."),
    "in_shop": ("In shop", "Under threshold and passes faction/ammo filters."),
    "community": ("Community", "Outfit faction community tag."),
    "ammo": ("Ammo", "Calibres this weapon uses."),
    # Weapon stats / weights (score cols = tooltip Min / Lgt / Lgt+ / Mid / Mid+)
    "cost": ("Cost", "Base item cost (lower scores better when weighted)."),
    "w_price": ("Price", "Base item cost (lower scores better when weighted)."),
    "hit_power": ("Hit power", "Base hit power from best ammo."),
    "w_hit_power": ("Hit power", "Base hit power from best ammo."),
    "min_dmg": ("Best Mutant dmg", "Best ammo vs Min torso; mutant row if higher."),
    "w_min_dmg": ("Best Mutant dmg", "Best ammo vs Min torso; mutant row if higher."),
    "lgt_dmg": ("Dmg vs lgt armor", "Best ammo vs Lgt torso armor (0.075)."),
    "w_lgt_dmg": ("Dmg vs lgt armor", "Best ammo vs Lgt torso armor (0.075)."),
    "lgtp_dmg": ("Dmg vs mid armor", "Best ammo vs Lgt+ torso armor (0.1)."),
    "w_lgtp_dmg": ("Dmg vs mid armor", "Best ammo vs Lgt+ torso armor (0.1)."),
    "mid_dmg": ("Dmg vs hvy armor", "Best ammo vs Mid torso armor (0.15)."),
    "w_mid_dmg": ("Dmg vs hvy armor", "Best ammo vs Mid torso armor (0.15)."),
    "midp_dmg": ("Dmg vs max armor", "Best ammo vs Mid+ torso armor (0.2)."),
    "w_midp_dmg": ("Dmg vs max armor", "Best ammo vs Mid+ torso armor (0.2)."),
    "min_dps": ("Best Mutant DPS", "Sustained DPS vs Min torso / mutant."),
    "w_min_dps": ("Best Mutant DPS", "Sustained DPS vs Min torso / mutant."),
    "lgt_dps": ("DPS vs lgt armor", "Sustained DPS vs Lgt torso armor."),
    "w_lgt_dps": ("DPS vs lgt armor", "Sustained DPS vs Lgt torso armor."),
    "lgtp_dps": ("DPS vs mid armor", "Sustained DPS vs Lgt+ torso armor."),
    "w_lgtp_dps": ("DPS vs mid armor", "Sustained DPS vs Lgt+ torso armor."),
    "mid_dps": ("DPS vs hvy armor", "Sustained DPS vs Mid torso armor."),
    "w_mid_dps": ("DPS vs hvy armor", "Sustained DPS vs Mid torso armor."),
    "midp_dps": ("DPS vs max armor", "Sustained DPS vs Mid+ torso armor."),
    "w_midp_dps": ("DPS vs max armor", "Sustained DPS vs Mid+ torso armor."),
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
