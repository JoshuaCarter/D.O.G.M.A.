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
# Official model names that use quotes in the string table (not kit skins).
_QUOTED_OFFICIAL = frozenset({"wpn_oc33", "wpn_glock17_m1"})
# Real guns whose section id ends in _custom (not AK-74 "custom" kits).
_CUSTOM_OK = frozenset({"wpn_korth_custom"})


def has_quoted_nickname(display_name: str | None) -> bool:
    """True when the resolved inv_name has a quoted kit nickname."""
    return bool(display_name) and bool(_QUOTED_NICKNAME.search(str(display_name)))


def is_quoted_kit_skin(sec: str, display_name: str | None) -> bool:
    """Quoted kit nickname, excluding official names like OTs-33 \"Pernach\"."""
    if sec in _QUOTED_OFFICIAL:
        return False
    return has_quoted_nickname(display_name)


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
    if (s.endswith("_custom") or "_custom_" in s) and s not in _CUSTOM_OK:
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
    # Named kit / custom optic stubs that set parent_section=self.
    if any(
        x in s
        for x in (
            "_apsabigo",
            "_vitup",
            "_gurza_up",
            "_sr1upgr",
            "_mod9",
            "p90gamma",
            "_p90_gs",
        )
    ) or s.endswith("_gs"):
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
    # Engine animation stubs (bolt-throw hit proxy — not a shop weapon).
    if s.startswith("animation_hit") or "animation_hit_" in s:
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
    ltx_parent: str | None = None,
    sections: dict[str, dict[str, str]] | None = None,
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
        explicit = (d.get("parent_section") or "").strip()
        if explicit:
            # Body parent_section must name this section (base shop item).
            if explicit != sec:
                return False
        elif ltx_parent and ltx_parent != sec and sections is not None:
            # No body parent_section — only reject *prefix clones* of another gun
            # that reuse its inv_name (``[wpn_fn57_aimpoint]:wpn_fn57``). Distinct
            # variants (``[wpn_toz34_obrez]:wpn_toz34``) keep their own inv_name.
            parent_d = sections.get(ltx_parent) or {}
            if looks_like_weapon(parent_d):
                if sec.lower().startswith(ltx_parent.lower() + "_"):
                    child_inv = (d.get("inv_name") or "").strip().lower()
                    par_inv = (parent_d.get("inv_name") or "").strip().lower()
                    if not child_inv or child_inv == par_inv:
                        return False
    return True


# Skin / remake tails stripped when a shorter base section is also in the pool.
_SKIN_TAIL_RE = re.compile(
    r"(_modern|_camo|_sup|_new|_rwap|_rail|_black|_apsabigo|_sniper)$",
    re.IGNORECASE,
)
# Visual remakes — drop even when inv_name text differs slightly from the base.
_FORCE_DROP_TAILS = frozenset(
    {"_modern", "_camo", "_sup", "_rail", "_apsabigo", "_sniper", "_new"}
)


def _norm_display(name: str | None) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def is_same_name_weapon_alias(
    sec: str,
    d: dict[str, str],
    *,
    ltx_parent: str | None,
    sections: dict[str, dict[str, str]],
    display_name: str,
    parent_display_name: str,
) -> bool:
    """True for stub aliases that reuse another gun's inv_name via LTX inherit.

    Example: ``[wpn_axmc]:wpn_l96a1`` both resolve to \"L96A1\".
    Also ``[wpn_sig220]:wpn_sig220_n`` with ``parent_section = self`` — still an
    alias of the real BAS section. Allows intentional BAS remakes
    (``wpn_ppsh_bas`` / ``wpn_sks_b``) that replace the plain section.
    """
    ps = (d.get("parent_section") or "").strip()
    # Real attach trees point at another section; self/empty can still be LTX aliases.
    if ps and ps != sec:
        return False
    if not ltx_parent or ltx_parent == sec:
        return False
    if not looks_like_weapon(sections.get(ltx_parent) or {}):
        return False
    if _norm_display(display_name) != _norm_display(parent_display_name):
        return False
    # Mag / calibre remakes share inv_name but are different guns (toz106 2rd vs 4rd).
    if _combat_fingerprint(d) != _combat_fingerprint(sections.get(ltx_parent) or {}):
        return False
    sl = sec.lower()
    pl = ltx_parent.lower()
    # Inverted skin inherit (``[wpn_tt33]:wpn_tt33_modern``) — keep the plain gun.
    m = _SKIN_TAIL_RE.search(ltx_parent)
    if m and ltx_parent[: m.start()].lower() == sl:
        return False
    # GAMMA remakes used in loadouts — keep these over the inherited plain gun.
    if sl.endswith("_bas") or sl.endswith("_b"):
        return False
    return True


def _is_bas_remake(sec: str) -> bool:
    sl = sec.lower()
    return sl.endswith("_bas") or (sl.endswith("_b") and not sl.endswith("_bas"))


def _combat_fingerprint(d: dict[str, str]) -> tuple[str, str]:
    """Primary ammo + mag — same print means a duplicate listing, not a remake."""
    ammo = (d.get("ammo_class") or "").split(",")[0].strip().lower()
    return (ammo, (d.get("ammo_mag_size") or "").strip())


def _model_fingerprint(d: dict[str, str]) -> tuple[str, str, str]:
    """Combat plus HUD — full MP5 vs MP5K share a name but are different guns."""
    ammo, mag = _combat_fingerprint(d)
    return (ammo, mag, (d.get("hud") or "").strip().lower())


def _keep_rank(sec: str, display_key: str, d: dict[str, str]) -> tuple:
    """Higher wins when collapsing same-name combat clones."""
    sl = sec.lower()
    bas = 3 if _is_bas_remake(sec) else 0
    n = 1 if sl.endswith("_n") else 0
    body = sl[4:] if sl.startswith("wpn_") else sl
    tokens = [t for t in re.findall(r"[a-z0-9]+", display_key) if len(t) >= 3]
    overlap = sum(1 for t in tokens if t in body)
    try:
        cost = float((d.get("cost") or "0").strip() or 0)
    except ValueError:
        cost = 0.0
    return (bas, n, overlap, cost, len(body))


def prefer_unique_weapons(
    secs: list[str],
    sections: dict[str, dict[str, str]],
    display_names: dict[str, str],
    section_parents: dict[str, str] | None = None,
) -> list[str]:
    """Drop skin duplicates and plain stubs when a better sibling is present.

    - ``wpn_glock_modern`` dropped if ``wpn_glock`` is kept (same display name)
    - ``wpn_pm`` dropped if ``wpn_pm_bas`` is kept (same display name)
    - ``wpn_ak74m`` dropped if ``wpn_ak74m_n`` is kept (same display name)
    - ``wpn_val_tac`` dropped if ``wpn_val`` shares the name
    - ``wpn_sig220`` dropped if ``wpn_sig220_n`` is kept (LTX alias, same name)
    - Calibre / mag siblings (ithaca 12 vs 20, RPK drum) stay when combat differs
    """
    parents = section_parents or {}
    kept = set(secs)
    # 1) Suffix skins when base section exists.
    for sec in list(kept):
        m = _SKIN_TAIL_RE.search(sec)
        if not m:
            continue
        base = sec[: m.start()]
        if base not in kept:
            continue
        tail = m.group(1).lower()
        same = _norm_display(display_names.get(sec)) == _norm_display(
            display_names.get(base)
        )
        if same or tail in _FORCE_DROP_TAILS:
            kept.discard(sec)

    # 2) _tac with identical display name as base.
    for sec in list(kept):
        if not sec.lower().endswith("_tac"):
            continue
        base = sec[:-4]
        if base in kept and _norm_display(display_names.get(sec)) == _norm_display(
            display_names.get(base)
        ):
            kept.discard(sec)

    def _by_name() -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for sec in kept:
            key = _norm_display(display_names.get(sec))
            if not key:
                continue
            out.setdefault(key, []).append(sec)
        return out

    # 3) Same display name: prefer BAS / _b, then _n, over every other sibling.
    for group in _by_name().values():
        if len(group) < 2:
            continue
        preferred = [s for s in group if _is_bas_remake(s)]
        if not preferred:
            preferred = [s for s in group if s.lower().endswith("_n")]
        if not preferred:
            continue
        for s in group:
            if s not in preferred:
                kept.discard(s)

    # 4) Same display name: drop LTX child aliases (keep the inherited parent).
    #    Remakes (_bas/_b) that inherit the plain gun: drop the plain parent instead.
    for group in _by_name().values():
        if len(group) < 2:
            continue
        group_set = set(group)
        for sec in group:
            if sec not in kept:
                continue
            parent = parents.get(sec)
            if not parent or parent == sec or parent not in group_set or parent not in kept:
                continue
            if _combat_fingerprint(sections.get(sec) or {}) != _combat_fingerprint(
                sections.get(parent) or {}
            ):
                continue
            if _is_bas_remake(sec):
                kept.discard(parent)
            else:
                kept.discard(sec)

    # 5) Same display name + identical combat fingerprint → keep one ranked winner.
    for key, group in _by_name().items():
        if len(group) < 2:
            continue
        by_fp: dict[tuple[str, str, str], list[str]] = {}
        for sec in group:
            if sec not in kept:
                continue
            fp = _model_fingerprint(sections.get(sec) or {})
            by_fp.setdefault(fp, []).append(sec)
        for clones in by_fp.values():
            if len(clones) < 2:
                continue
            winner = max(
                clones,
                key=lambda s: _keep_rank(s, key, sections.get(s) or {}),
            )
            for s in clones:
                if s != winner:
                    kept.discard(s)

    return sorted(kept)


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
