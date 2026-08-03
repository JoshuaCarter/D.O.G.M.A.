"""Mirror Anomaly debug spawner item filters (ui_debug_launcher get_spawn_table)."""

from __future__ import annotations

import re
from pathlib import Path

from .diaglog import get_logger

log = get_logger("spawn_filter")

# BAS/3DSS “scope already installed” suffixes (addon section ids glued onto the gun).
# These set parent_section=self so they slip past is_spawnable_gear; not base shop guns.
# Also catches polluted aliases (Enhanced Recoil bodyless :inherit → wrong thumbs).
_INSTALLED_ADDON_SUFFIX = re.compile(
    r"(?:_upg\d+|_u2p2g0r|_up2g0r)$",
    re.IGNORECASE,
)
# Upgrade-kit nicknames in UI text, e.g. Lebedev PL-15 "Bearcat", AK-74M "Venom".
_QUOTED_NICKNAME = re.compile(r'["\u201c\u201d«»]')


def has_quoted_nickname(display_name: str | None) -> bool:
    """True when the resolved inv_name has a quoted kit nickname."""
    return bool(display_name) and bool(_QUOTED_NICKNAME.search(str(display_name)))


def _status_attached(raw: object) -> bool:
    """True when Anomaly addon status is installed/integrated (``1``), not slot (``2``)."""
    if raw is None:
        return False
    s = str(raw).strip().lower()
    if s in ("true", "on", "yes"):
        return True
    try:
        return abs(float(s) - 1.0) < 1e-9
    except (TypeError, ValueError):
        return False


def has_attached_silencer(d: dict[str, str] | None = None, *, entry: dict | None = None) -> bool:
    """Integrated/attached silencer (``silencer_status == 1``), not an empty slot."""
    if entry is not None and "silencer_attached" in entry:
        return bool(entry.get("silencer_attached"))
    return _status_attached((d or {}).get("silencer_status"))


def has_attached_scope(d: dict[str, str] | None = None, *, entry: dict | None = None) -> bool:
    """Built-in/attached scope (``scope_status == 1``), not an empty rail."""
    if entry is not None and "scope_attached" in entry:
        return bool(entry.get("scope_attached"))
    return _status_attached((d or {}).get("scope_status"))

def load_spawner_blacklist(anomaly: Path | None) -> set[str]:
    ignore: set[str] = set()
    if not anomaly:
        return ignore
    for cand in (
        anomaly / "tools" / "_unpacked" / "configs" / "plugins" / "spawner_blacklist.ltx",
        anomaly / "gamedata" / "configs" / "plugins" / "spawner_blacklist.ltx",
    ):
        if not cand.is_file():
            continue
        section = ""
        for raw in cand.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.split(";", 1)[0].strip()
            if not line:
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1].strip().lower()
                continue
            if section == "ignore_sections":
                ignore.add(line)
        log.info("spawner blacklist %s (%d)", cand, len(ignore))
        break
    return ignore


def name_blocked(sec: str) -> bool:
    s = sec.lower()
    # Spawner-style + MP leftovers (wpn_knife_mp) whose grids target old atlases.
    if "tch_" in s or "mp_" in s or s.endswith("_mp") or "_mp_" in s or "_base" in s:
        return True
    # Ballistics CW pack stubs ([wpn_foo_cw]:wpn_other) — not real spawnables.
    if s.endswith("_cw") or "_cw_" in s:
        return True
    # Upgrade/kit skins (wpn_ak74_custom, wpn_ak12_custom_mono_kit) — not base gear.
    if s.endswith("_custom") or "_custom_" in s:
        return True
    # Alt skins / non-base variants (wpn_colt1911_alt).
    if s.endswith("_alt") or "_alt_" in s:
        return True
    # Upgrade / repair kits (wpn_*_upgr_kit, helmet_repair_kit, *_upkit).
    if "kit" in s or "repair_kit" in s:
        return True
    # Installed-scope variants (wpn_sig220_upg220, wpn_sig220_n_u2p2g0r) — buy base + attach.
    if _INSTALLED_ADDON_SUFFIX.search(s):
        return True
    # GIMP phantom: empty [wpn_mk23]:wpn_cz75 — real MK23 in this pack is wpn_usp.
    if s == "wpn_mk23":
        return True
    # GIMP “Anarchy” 1911 — special enhanced handgun (laser/hud kit), not a faction.
    if s.endswith("_anarchy") or "_anarchy_" in s:
        return True
    # Melee knives / axes — not loadout-shop guns.
    if "knife" in s or "axe" in s:
        return True
    # Decorative / non-functional gear (decor_psi_helmet, wpn_toz34_decor).
    if s.startswith("decor_") or "_decor_" in s or s.endswith("_decor"):
        return True
    # Explosives / launchers — scored poorly by ballistic math; excluded for now.
    if _explosive_name(s):
        return True
    # Gauss rifle / ammo — unique quest gear, not loadout-shop.
    if "gauss" in s:
        return True
    # Nimble / story / task uniques (not marked quest_item — name conventions).
    # e.g. wpn_svu_nimble, wpn_g36nimble_rwap, wpn_ak74u_snag, wpn_pm_actor.
    if "nimble" in s:
        return True
    for tok in ("snag", "trapper", "zulus", "luckygun"):
        if s.endswith(f"_{tok}") or f"_{tok}_" in s:
            return True
    # Actor-bound uniques (zat b33 PM etc.) — only suffix form (avoid snd_shoot_actor).
    if s.endswith("_actor") or "_actor_" in s:
        return True
    # One-off Nimble/stock exclusives without the above tokens.
    if s in ("wpn_toz34_mark4", "pri_a17_gauss_rifle", "wpn_gauss_quest"):
        return True
    # Yantar story psy-helmets (devices / quest gear — not loadout helmets).
    if s in ("bad_psy_helmet", "good_psy_helmet"):
        return True
    return False


def is_special_spawn_path(d: dict[str, str] | None) -> bool:
    """True when ``$spawn`` points at unique/quest_items trees (high precision)."""
    if not d:
        return False
    spawn = (d.get("$spawn") or "").strip().lower().replace("/", "\\")
    if not spawn:
        return False
    return (
        "\\unique\\" in spawn
        or "\\uniq\\" in spawn
        or "quest_items\\" in spawn
        or spawn.startswith("quest_items\\")
    )


def _explosive_name(s: str) -> bool:
    s = s.lower()
    needles = (
        "rpg",
        "m79",
        "rg-6",
        "rg6",
        "grenade",
        "rocket",
        "gp25",
        "gp-25",
        "ag36",
        "mgl",
        "ags_",
        "ags30",
        "ags-30",
        "panzerschreck",
        "law_",
        "rpg7",
    )
    return any(n in s for n in needles)


def _ammo_is_explosive(ammo_sec: str, sections: dict[str, dict[str, str]] | None = None) -> bool:
    a = (ammo_sec or "").strip()
    if not a:
        return False
    al = a.lower()
    if any(
        x in al
        for x in (
            "vog-",
            "vog_",
            "m209",
            "og-7",
            "og7",
            "grenade",
            "rocket",
            "rpg",
        )
    ):
        return True
    if not sections:
        return False
    d = sections.get(a) or {}
    if (d.get("fake_grenade_name") or "").strip():
        return True
    flag = str(d.get("grenade_ammo") or "").strip().lower()
    return flag in ("true", "1", "on", "yes")


def is_explosive_weapon(
    sec: str,
    d: dict[str, str] | None = None,
    sections: dict[str, dict[str, str]] | None = None,
    *,
    ammo_class: list[str] | None = None,
) -> bool:
    """True for GLs / rockets / grenade launchers (exclude from SALE for now)."""
    if _explosive_name(sec):
        return True
    d = d or {}
    cls = (d.get("class") or "").upper()
    if cls in ("WP_ROCKET", "WP_GRENADE", "G_RPG7"):
        return True
    if (d.get("hit_type_blast") or "").strip() and (d.get("blast") or "").strip():
        # Weapon-native explosion (RPG shells etc.) — not underbarrel stubs.
        try:
            if float(d.get("blast") or 0) > 0:
                return True
        except (TypeError, ValueError):
            return True
    ammos = ammo_class
    if ammos is None:
        ammos = [
            p.strip()
            for p in str(d.get("ammo_class") or "").split(",")
            if p.strip()
        ]
    for ammo in ammos or []:
        if _ammo_is_explosive(ammo, sections):
            return True
    return False


def is_gauss_weapon(
    sec: str,
    d: dict[str, str] | None = None,
    *,
    ammo_class: list[str] | None = None,
) -> bool:
    """True for gauss rifle / anything chambered in gauss ammo."""
    if "gauss" in (sec or "").lower():
        return True
    ammos = ammo_class
    if ammos is None and d is not None:
        ammos = [
            p.strip()
            for p in str(d.get("ammo_class") or "").split(",")
            if p.strip()
        ]
    return any("gauss" in str(a).lower() for a in (ammos or []))


def has_installed_upgrades(d: dict[str, str] | None) -> bool:
    """True if LTX would show the inventory upgrade arrow (``ui_upgrade_arrow2``).

    Mirrors Anomaly ``utils_item.has_upgrades(nil, sec)``:
    non-empty ``installed_upgrades`` = mechanic-tree upgrades baked into the section
    (``*_camo`` / ``*_modern`` skins, etc.). Scope addons like ``_upg220`` are a
    different system and do not set this field.
    """
    if not d:
        return False
    raw = str(d.get("installed_upgrades") or "").strip()
    if not raw:
        return False
    # Drop comments / empty CSV crumbs.
    parts = [p.strip() for p in raw.split(",") if p.strip() and not p.strip().startswith(";")]
    return bool(parts)


def is_spawnable_gear(
    sec: str,
    d: dict[str, str],
    *,
    ignore: set[str],
    require_parent_self: bool = True,
) -> bool:
    """True if section would survive debug item-spawner gates for gear."""
    if not sec or sec.startswith("!") or " " in sec:
        return False
    if sec in ignore:
        return False
    if name_blocked(sec):
        return False
    # Pre-upgraded skins show the white upgrade arrow in inventory — not base shop gear.
    if has_installed_upgrades(d):
        return False
    # $spawn under unique/uniq/quest_items — story guns even without name tokens.
    if is_special_spawn_path(d):
        return False
    try:
        w = float(d.get("inv_grid_width") or 0)
        h = float(d.get("inv_grid_height") or 0)
    except (TypeError, ValueError):
        return False
    if w <= 0 or h <= 0:
        return False
    if "inv_grid_x" not in d and "inv_grid_y" not in d:
        return False
    if require_parent_self:
        parent = (d.get("parent_section") or sec).strip()
        if parent != sec:
            return False
    return True


def looks_like_weapon(d: dict[str, str]) -> bool:
    cls = (d.get("class") or "").upper()
    kind = (d.get("kind") or "").lower()
    key = kind or cls.lower()
    if key in ("w_misc", "wp_scope", "wp_silen", "wp_glaun", "s_wpn_misc", "wp_binoc", "ii_bolt", "w_base"):
        return False
    if d.get("ammo_class") and cls.startswith("WP_"):
        return True
    if d.get("ammo_class") and any(k in d for k in ("rpm", "ammo_mag_size", "hit_power")):
        return True
    if kind.startswith("w_") and kind != "w_misc" and d.get("ammo_class"):
        return True
    return False
