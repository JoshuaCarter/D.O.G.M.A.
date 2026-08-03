"""Weapon/armor calculate for SALE.

Primary path: lupa → src/_common/scripts/dogma_item_stats.script (sole math owner).
This module keeps a Python mirror as fallback if lupa/Lua fails to load, and
builds input tables from merged LTX (including sniper_ap_bonus).
"""

from __future__ import annotations

import math
from typing import Any

TORSO_TIERS = [
    {"armor": 0.011, "ap_scale": 0.8889, "hit_frac": 0.8500},
    {"armor": 0.075, "ap_scale": 0.8562, "hit_frac": 0.6250},
    {"armor": 0.100, "ap_scale": 0.7955, "hit_frac": 0.5000},
    {"armor": 0.150, "ap_scale": 0.8117, "hit_frac": 0.4783},
    {"armor": 0.200, "ap_scale": 0.7412, "hit_frac": 0.3618},
    {"armor": 0.250, "ap_scale": 0.7400, "hit_frac": 0.3050},
    {"armor": 0.400, "ap_scale": 0.7500, "hit_frac": 0.2000},
    {"armor": 0.550, "ap_scale": 0.7000, "hit_frac": 0.1500},
    {"armor": 0.650, "ap_scale": 0.7000, "hit_frac": 0.1500},
]
SCORE_COLS = len(TORSO_TIERS)  # Min .. Max
BONE_DMG_TORSO = 0.9
BALLISTIC_BONUS = 1.1
K_AP_COMBAT_SCALE = 10
AP_TAIL = 0.80
ARMOR_CHIP_FRAC = 0.60
HARD_FAIL_SCALE = 0.0025 * 62.5
DAMAGE_SCALE = 100
DEFAULT_DIST = 30
MUTANT_BASE_MULT = 0.85
MUTANT_AMMO_MULT_DEFAULT = 0.85
BURST_KICK_DEG = 3
ACTOR_DISP_BASE = 1.5
ACTOR_DISP_VEL = 2.5
VEL_MAX = 10
SPEED_RUN = 5.0

HP_AMMO_MULT = {
    "ammo_357_hp_mag": 2,
    "ammo_9x18_pmm": 2,
    "ammo_9x19_pbp": 2,
    "ammo_7.62x25_p": 1.75,
    "ammo_11.43x23_hydro": 2.7,
    "ammo_5.45x39_ep": 1.45,
    "ammo_5.56x45_ss190": 1.33,
    "ammo_12x76_zhekan": 3.5,
    "ammo_12x76_dart": 1.5,
    "ammo_12x70_buck": 3,
    "ammo_23x75_shrapnel": 2,
    "ammo_23x75_barrikada": 3.5,
    "ammo_20x70_buck": 2,
    "ammo_338_federal": 10,
}
MUTANT_AMMO_MULT = {
    "ammo_12x70_buck": 0.90,
    "ammo_20x70_buck": 0.90,
    "ammo_23x75_shrapnel": 1.00,
    "ammo_12x76_zhekan": 1.00,
    "ammo_23x75_barrikada": 1.00,
    "ammo_9x19_pbp": 1.00,
    "ammo_9x18_pmm": 1.00,
    "ammo_5.45x39_ep": 1.00,
    "ammo_5.56x45_ss190": 1.00,
    "ammo_7.62x25_ps": 1.00,
    "ammo_7.62x25_p": 1.75,
    "ammo_357_hp_mag": 1.00,
    "ammo_11.43x23_hydro": 1.00,
    "ammo_11.43x23_fmj": 1.00,
}

PROT_KEYS = [
    "fire_wound_protection",
    "strike_protection",
    "wound_protection",
    "explosion_protection",
    "shock_protection",
    "burn_protection",
    "chemical_burn_protection",
    "radiation_protection",
    "telepathy_protection",
]


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def dist_atten(air_res: float, dist: float) -> float:
    k = air_res if air_res is not None else 0.05
    return 1 + (dist / 200) * k * 0.5 / (1 - k + 0.1)


def sustained_dps(dmg: float, rpm: float, mag: float, reload_s: float) -> float:
    if dmg <= 0 or mag <= 0 or rpm <= 0:
        return 0.0
    fire_s = mag * 60 / rpm
    cycle = fire_s + max(0.0, reload_s)
    if cycle <= 0:
        return 0.0
    return dmg * mag / cycle


def _effective_dmg(
    hit_power: float,
    k_hit: float,
    k_ap: float,
    air_res: float,
    armor: float,
    ap_scale: float,
    hit_frac: float,
    dist: float,
    pellets: float,
    ammo_sec: str,
    sniper_ap: float,
) -> float:
    ap = (
        (k_ap * K_AP_COMBAT_SCALE + sniper_ap)
        * ap_scale
        / dist_atten(air_res, dist)
        * AP_TAIL
    )
    power = (
        hit_power
        / dist_atten(air_res, dist)
        * k_hit
        * BONE_DMG_TORSO
        * ap_scale
        * BALLISTIC_BONUS
        * pellets
    )
    if armor <= 0 or ap >= armor:
        dmg = power
    else:
        soft = armor / (1 + ARMOR_CHIP_FRAC)
        if ap > soft:
            dmg = power * hit_frac
        else:
            hp_mult = HP_AMMO_MULT.get(ammo_sec, 1)
            dmg = HARD_FAIL_SCALE * power * hit_frac / hp_mult
    return dmg * DAMAGE_SCALE


def weapon_calculate(inp: dict[str, Any]) -> dict[str, Any]:
    from . import lua_calc

    lua_out = lua_calc.weapon_calculate(inp)
    if lua_out is not None:
        return lua_out
    return _weapon_calculate_py(inp)


def _weapon_calculate_py(inp: dict[str, Any]) -> dict[str, Any]:
    dist = _f(inp.get("dist"), DEFAULT_DIST)
    hit_power = _f(inp.get("hit_power"), 0)
    rpm = _f(inp.get("rpm"), 0)
    mag = _f(inp.get("ammo_mag_size"), 0)
    reload_s = _f(inp.get("reload_s"), 0)
    if reload_s <= 0:
        reload_s = 1.2 + 0.7 * max(mag, 1) if int(inp.get("use_mag") or 1) == 0 else 2.5
    sniper_ap = _f(inp.get("sniper_ap_bonus"), 0)
    rounds = inp.get("rounds") or []

    col_max = [0.0] * SCORE_COLS
    for r in rounds:
        k_hit = _f(r.get("k_hit"), 1)
        k_ap = _f(r.get("k_ap"), 0)
        air = _f(r.get("air_resistance"), 0.05)
        pellets = _f(r.get("pellets"), 1)
        sec = str(r.get("sec") or "")
        for i in range(SCORE_COLS):
            t = TORSO_TIERS[i]
            dmg = _effective_dmg(
                hit_power, k_hit, k_ap, air,
                t["armor"], t["ap_scale"], t["hit_frac"],
                dist, pellets, sec, sniper_ap,
            )
            if dmg > col_max[i]:
                col_max[i] = dmg

    mut_max = 0.0
    for r in rounds:
        air = _f(r.get("air_resistance"), 0.05)
        k_hit = _f(r.get("k_hit"), 1)
        pellets = _f(r.get("pellets"), 1)
        sec = str(r.get("sec") or "")
        dmg = math.floor(
            hit_power / dist_atten(air, dist)
            * k_hit * pellets
            * MUTANT_BASE_MULT * MUTANT_AMMO_MULT.get(sec, MUTANT_AMMO_MULT_DEFAULT)
            * DAMAGE_SCALE + 0.5
        )
        if dmg > mut_max:
            mut_max = float(dmg)
    if mut_max > col_max[0]:
        col_max[0] = mut_max

    fire_disp = _f(inp.get("fire_dispersion_base"), 0)
    if fire_disp <= 0:
        ads, hip = 0.0, 0.0
    else:
        pdm_base = _f(inp.get("PDM_disp_base"), 1)
        pdm_vel = _f(inp.get("PDM_disp_vel_factor"), 1)
        actor = ACTOR_DISP_BASE * pdm_base * (
            1 + (SPEED_RUN / VEL_MAX) * ACTOR_DISP_VEL * pdm_vel
        )
        ads, hip = fire_disp, fire_disp + actor

    burst = _f(inp.get("burst_shots"), 0)
    if burst <= 0:
        zoom_disp = _f(inp.get("zoom_cam_dispersion"), 0)
        hip_disp = _f(inp.get("cam_dispersion"), 0)
        disp = zoom_disp if zoom_disp > 0 else hip_disp
        if disp <= 0:
            burst = 200
        else:
            inc = _f(inp.get("zoom_cam_dispersion_inc"), 0) or _f(
                inp.get("cam_dispersion_inc"), 0
            )
            return_on = inp.get("cam_return", True)
            relax = 0.0
            if return_on:
                relax = _f(inp.get("zoom_cam_relax_speed"), 0) or _f(
                    inp.get("cam_relax_speed"), 0
                )
            dt = 60 / rpm if rpm > 0 else 0
            climb = 0.0
            burst = 200
            for n in range(1, 201):
                climb += disp + (n - 1) * inc
                if return_on and relax > 0 and dt > 0:
                    climb = max(0.0, climb - relax * dt)
                if climb >= BURST_KICK_DEG:
                    burst = n
                    break

    # Keys match tooltip torso columns Min .. Max.
    return {
        "hit_power": hit_power,
        "min_dmg": col_max[0],
        "lgt_dmg": col_max[1],
        "lgtp_dmg": col_max[2],
        "mid_dmg": col_max[3],
        "midp_dmg": col_max[4],
        "hvy_dmg": col_max[5],
        "hvyp_dmg": col_max[6],
        "exo_dmg": col_max[7],
        "max_dmg": col_max[8],
        "min_dps": sustained_dps(col_max[0], rpm, mag, reload_s),
        "lgt_dps": sustained_dps(col_max[1], rpm, mag, reload_s),
        "lgtp_dps": sustained_dps(col_max[2], rpm, mag, reload_s),
        "mid_dps": sustained_dps(col_max[3], rpm, mag, reload_s),
        "midp_dps": sustained_dps(col_max[4], rpm, mag, reload_s),
        "hvy_dps": sustained_dps(col_max[5], rpm, mag, reload_s),
        "hvyp_dps": sustained_dps(col_max[6], rpm, mag, reload_s),
        "exo_dps": sustained_dps(col_max[7], rpm, mag, reload_s),
        "max_dps": sustained_dps(col_max[8], rpm, mag, reload_s),
        "burst": burst,
        "spread_ads": ads,
        "spread_hip": hip,
        "scope": 1 if _f(inp.get("scope_status"), 0) > 0 else 0,
        "silencer": (
            1
            if (
                _f(inp.get("silencer_status"), 0) > 0
                or bool(inp.get("integrated_silencer"))
                or _has_integrated_silencer(
                    str(inp.get("sec") or ""),
                    str(inp.get("parent_section") or "") or None,
                )
            )
            else 0
        ),
        "reload_s": reload_s,
        "rpm": rpm,
        "mag": mag,
        "cost": _f(inp.get("cost"), 0),
    }


def armor_calculate(inp: dict[str, Any]) -> dict[str, Any]:
    from . import lua_calc

    lua_out = lua_calc.armor_calculate(inp)
    if lua_out is not None:
        return lua_out
    return _armor_calculate_py(inp)


def _armor_calculate_py(inp: dict[str, Any]) -> dict[str, Any]:
    prots = inp.get("protections") or inp
    out: dict[str, Any] = {
        "cost": _f(inp.get("cost"), 0),
    }
    for k in PROT_KEYS:
        out[k] = _f(prots.get(k), 0)
    return out


# Fallback lists (kept in sync with dogma_item_stats) when lupa is down.
_SNIPER_AP_BONUS = 0.05
_SNIPERS = {
    "wpn_dvl10_m1",
    "wpn_dvl10",
    "wpn_l96a1",
    "wpn_l96a1m",
    "wpn_m98b",
    "wpn_m24",
    "wpn_remington700",
    "wpn_remington700_archangel",
    "wpn_remington700_lapua700",
    "wpn_remington700_magpul_pro",
    "wpn_remington700_mod_x_gen3",
    "wpn_steyr_scout_big",
    "wpn_sv98",
    "wpn_sv98_custom",
    "wpn_k98_mod",
    "wpn_wa2000",
    "wpn_trg",
    "wpn_mosin",
}
_INTEGRATED_SILENCER = {
    "wpn_dvl10_m1",
    "wpn_vssk",
    "wpn_val_tac",
    "wpn_vintorez",
    "wpn_val",
    "wpn_val_modern",
    "wpn_vintorez_m1",
    "wpn_vintorez_m2",
    "wpn_vintorez_isg",
    "wpn_mp5sd",
    "wpn_mp5sd_custom",
    "wpn_mp5sd_new",
}


def _sniper_ap_bonus(sec: str, parent: str | None) -> float:
    from . import lua_calc

    if lua_calc.available():
        return lua_calc.sniper_ap_bonus(sec, parent or sec)
    key = parent or sec
    return _SNIPER_AP_BONUS if key in _SNIPERS else 0.0


def _has_integrated_silencer(sec: str, parent: str | None) -> bool:
    from . import lua_calc

    if lua_calc.available():
        return lua_calc.has_integrated_silencer(sec, parent or sec)
    return (parent or sec) in _INTEGRATED_SILENCER or sec in _INTEGRATED_SILENCER


def build_weapon_input(sec: str, sections: dict[str, dict[str, str]]) -> dict[str, Any]:
    d = sections.get(sec) or {}

    def gf(key: str, default: float = 0.0) -> float:
        return _f(d.get(key), default)

    hp = d.get("hit_power")
    if hp and "," in str(hp):
        hp = str(hp).split(",")[0].strip()
    from .score import is_bad_ammo

    rounds = []
    for part in str(d.get("ammo_class") or "").split(","):
        ammo = part.strip()
        if not ammo or is_bad_ammo(ammo) or ammo not in sections:
            continue
        a = sections[ammo]
        # buck_shot=0 on grenade/rocket ammo means "not a shotgun", not zero pellets.
        # Match weapon_tooltips: only values > 1 count as multi-pellet.
        pellets = _f(a.get("buck_shot"), 1)
        if pellets <= 1:
            pellets = 1.0
        rounds.append(
            {
                "sec": ammo,
                "k_hit": _f(a.get("k_hit"), 1),
                "k_ap": _f(a.get("k_ap"), 0),
                "air_resistance": _f(
                    a.get("k_air_resistance") or a.get("air_resistance"), 0.05
                ),
                "pellets": pellets,
            }
        )
    cam_return = True
    if "cam_return" in d:
        cam_return = gf("cam_return", 1) != 0
    parent = (d.get("parent_section") or sec).strip() or sec
    integrated = _has_integrated_silencer(sec, parent)
    sil_status = gf("silencer_status")
    if integrated and sil_status <= 0:
        sil_status = 1.0
    return {
        "hit_power": _f(hp, 0.5),
        "rpm": gf("rpm"),
        "ammo_mag_size": gf("ammo_mag_size"),
        "scope_status": gf("scope_status"),
        "silencer_status": sil_status,
        "integrated_silencer": integrated,
        "sec": sec,
        "parent_section": parent,
        "fire_dispersion_base": gf("fire_dispersion_base"),
        "PDM_disp_base": gf("PDM_disp_base", 1),
        "PDM_disp_vel_factor": gf("PDM_disp_vel_factor", 1),
        "cam_dispersion": gf("cam_dispersion"),
        "zoom_cam_dispersion": gf("zoom_cam_dispersion"),
        "cam_dispersion_inc": gf("cam_dispersion_inc"),
        "zoom_cam_dispersion_inc": gf("zoom_cam_dispersion_inc"),
        "cam_relax_speed": gf("cam_relax_speed"),
        "zoom_cam_relax_speed": gf("zoom_cam_relax_speed"),
        "cam_return": cam_return,
        "sniper_ap_bonus": _sniper_ap_bonus(sec, parent),
        "cost": gf("cost"),
        "rounds": rounds,
    }


def _parse_csv_floats(raw: str) -> list[float]:
    out: list[float] = []
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        out.append(_f(part, 0.0))
    return out


def _bone_armor_value(
    sections: dict[str, dict[str, str]], bones_sec: str, bone_key: str
) -> float | None:
    """Second float of ``bip01_* = 1, 0.35`` (same as utils_item / parse_list)."""
    raw = (sections.get(bones_sec) or {}).get(bone_key)
    if raw is None or str(raw).strip() == "":
        return None
    vals = _parse_csv_floats(raw)
    if len(vals) >= 2:
        return vals[1]
    if len(vals) == 1:
        return vals[0]
    return None


def build_armor_input(sec: str, sections: dict[str, dict[str, str]]) -> dict[str, Any]:
    d = sections.get(sec) or {}
    prots = {k: _f(d.get(k), 0) for k in PROT_KEYS}
    # Engine LTX typo — psy lives on telepatic_protection.
    if prots.get("telepathy_protection", 0) == 0:
        prots["telepathy_protection"] = _f(d.get("telepatic_protection"), 0)
    is_helm = "helm" in sec.lower() or (d.get("class") or "").upper() == "E_HLMET"
    # FireWound (ballistic) is bone armor, not the flat fire_wound_protection key
    # (helmets omit that key; outfits often have a matching flat value).
    bones = (d.get("bones_koeff_protection") or "").strip()
    if bones:
        if is_helm:
            br = _bone_armor_value(sections, bones, "bip01_head")
            if br is not None:
                prots["fire_wound_protection"] = br
        else:
            br = _bone_armor_value(sections, bones, "bip01_spine")
            if br is not None:
                prots["fire_wound_protection"] = br
            # utils_item: add head bone when the outfit has no helmet slot.
            helm_slot = (d.get("helmet_avaliable") or "").strip().lower()
            if helm_slot not in ("true", "1", "yes"):
                head = _bone_armor_value(sections, bones, "bip01_head")
                if head is not None:
                    prots["fire_wound_protection"] = (
                        _f(prots.get("fire_wound_protection"), 0) + head
                    )
    return {
        "protections": prots,
        "cost": _f(d.get("cost"), 0),
        "is_helmet": is_helm,
    }
