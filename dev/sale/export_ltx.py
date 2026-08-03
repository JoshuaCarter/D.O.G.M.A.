"""Export native new_game_loadouts DLTX — no in-game Lua needed."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .balance import effective_category
from .diaglog import get_logger
from .score import (
    FACTION_BLOC,
    FACTION_COMMUNITY,
    FACTIONS,
    armor_pts,
    bloc_ok,
    is_bad_ammo,
    weapon_bloc,
    weapon_pts,
)

log = get_logger("export")

# Stock / GAMMA shop lines to strip (true,*) so only editor gear remains.
# Parsed from a live new_game_loadouts.ltx when available.
_LOADOUT_SECTIONS = ["shared"] + [f"{f}_loadout" for f in FACTIONS]

_REPO_SRC = Path(__file__).resolve().parents[2] / "src" / "tweaks" / "stat_derived_loadout"
_DEFAULT_EXPORT = (
    _REPO_SRC / "configs" / "mod_new_game_loadouts_dogma_stat_derived.ltx"
)


def _in_shop_weapon(
    entry: dict[str, Any], faction: str, cat_cfg: dict[str, Any]
) -> tuple[bool, int]:
    stats = entry.get("stats") or {}
    pts = weapon_pts(stats, cat_cfg.get("weights") or {}, float(cat_cfg.get("cost_mult") or 1000))
    if pts >= float(cat_cfg.get("max_pts") or 900):
        return False, pts
    bloc = weapon_bloc("", ",".join(entry.get("ammo_class") or []))
    want = FACTION_BLOC.get(faction, "both")
    if not bloc_ok(bloc, want):
        return False, pts
    return True, pts


def _in_shop_armor(
    entry: dict[str, Any],
    faction: str,
    cat_cfg: dict[str, Any],
    *,
    is_helmet: bool,
) -> tuple[bool, int]:
    stats = entry.get("stats") or {}
    pts = armor_pts(
        stats,
        cat_cfg.get("weights") or {},
        float(cat_cfg.get("cost_mult") or 1000),
        is_helmet=is_helmet,
    )
    if pts >= float(cat_cfg.get("max_pts") or 550):
        return False, pts
    if is_helmet:
        return True, pts
    want = FACTION_COMMUNITY.get(faction, faction)
    community = (entry.get("community") or "").strip()
    allow_univ = bool(cat_cfg.get("include_universal_armor", True))
    if community == want:
        return True, pts
    if allow_univ and community in ("", "actor"):
        return True, pts
    return False, pts


def first_good_ammo(ammo_class: list[str] | None) -> str | None:
    for ammo in ammo_class or []:
        if ammo and not is_bad_ammo(ammo):
            return ammo
    return None


def build_faction_shop(
    items: dict[str, Any], balance: dict[str, Any], faction: str
) -> dict[str, int]:
    """sec -> pts for purchasable gear (ammo granted via ammo_count, not shop)."""
    shop: dict[str, int] = {}
    wcfg = effective_category(balance, faction, "weapons")
    ocfg = effective_category(balance, faction, "outfits")
    hcfg = effective_category(balance, faction, "helmets")

    for sec, entry in (items.get("weapons") or {}).items():
        ok, pts = _in_shop_weapon(entry, faction, wcfg)
        if ok:
            shop[sec] = pts

    for sec, entry in (items.get("outfits") or {}).items():
        ok, pts = _in_shop_armor(entry, faction, ocfg, is_helmet=False)
        if ok:
            shop[sec] = pts

    for sec, entry in (items.get("helmets") or {}).items():
        ok, pts = _in_shop_armor(entry, faction, hcfg, is_helmet=True)
        if ok:
            shop[sec] = pts

    return shop


def collect_ammo_for_shops(
    items: dict[str, Any], shops: dict[str, dict[str, int]]
) -> dict[str, int]:
    """ammo_sec -> rounds (4 stacks)."""
    ammo_box = {
        sec: float((meta or {}).get("box_size") or 50)
        for sec, meta in (items.get("ammo") or {}).items()
    }
    out: dict[str, int] = {}
    weapons = items.get("weapons") or {}
    for shop in shops.values():
        for sec in shop:
            entry = weapons.get(sec)
            if not entry:
                continue
            ammo = first_good_ammo(entry.get("ammo_class"))
            if not ammo:
                continue
            box = ammo_box.get(ammo, 50.0)
            out[ammo] = int(round(4 * box))
    return out


def collect_ammo_type_overrides(
    items: dict[str, Any], shops: dict[str, dict[str, int]]
) -> dict[str, str]:
    """weapon -> first non-bad ammo (stock would otherwise pick bad first)."""
    out: dict[str, str] = {}
    weapons = items.get("weapons") or {}
    for shop in shops.values():
        for sec in shop:
            entry = weapons.get(sec)
            if not entry:
                continue
            ammo = first_good_ammo(entry.get("ammo_class"))
            if ammo:
                out[sec] = ammo
    return out


_SEC_RE = re.compile(r"^\[([^\]]+)\]")
_KV_RE = re.compile(r"^([A-Za-z0-9_\-\.]+)\s*=")


def parse_purchasable_keys(ltx_path: Path) -> dict[str, list[str]]:
    """section -> keys whose value starts with true (shop pick list)."""
    found: dict[str, list[str]] = {s: [] for s in _LOADOUT_SECTIONS}
    if not ltx_path.is_file():
        return found
    section = ""
    for raw in ltx_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.split(";")[0].strip()
        if not line:
            continue
        m = _SEC_RE.match(line)
        if m:
            section = m.group(1).split(":")[0].strip()
            continue
        if section not in found:
            continue
        km = _KV_RE.match(line)
        if not km:
            continue
        key = km.group(1)
        val = line.split("=", 1)[1].strip().lower()
        if val.startswith("true"):
            found[section].append(key)
    return found


def find_stock_loadouts(gamma: Path | None) -> Path | None:
    candidates: list[Path] = []
    if gamma:
        candidates.extend(
            [
                gamma / "overwrite" / "gamedata" / "configs" / "items" / "settings" / "new_game_loadouts.ltx",
                gamma / "gamedata" / "configs" / "items" / "settings" / "new_game_loadouts.ltx",
            ]
        )
    # Anomaly unpack next to common GAMMA layouts
    candidates.append(
        Path(r"c:\Anomaly\tools\_unpacked\configs\items\settings\new_game_loadouts.ltx")
    )
    for p in candidates:
        if p.is_file():
            return p
    return None


def export_shop_ltx(
    items: dict[str, Any],
    balance: dict[str, Any],
    dest: Path,
    *,
    stock_ltx: Path | None = None,
    gamma: Path | None = None,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)

    shops = {f: build_faction_shop(items, balance, f) for f in FACTIONS}
    ammo_counts = collect_ammo_for_shops(items, shops)
    ammo_types = collect_ammo_type_overrides(items, shops)

    stock = stock_ltx or find_stock_loadouts(gamma)
    strip = parse_purchasable_keys(stock) if stock else {s: [] for s in _LOADOUT_SECTIONS}
    if stock:
        log.info("stripping stock shop keys from %s", stock)
    else:
        log.warning("no stock new_game_loadouts.ltx — not stripping prior shop lines")

    lines = [
        "; DOGMA Stat Derived Loadout — generated by SALE (Stalker Anomaly Loadout Editor)",
        "; Patches items/settings/new_game_loadouts.ltx (native UINewGame format).",
        "; No Lua required. sec = true/false, qty, pts[, eco_lock]",
        f"; generated = {datetime.now(timezone.utc).isoformat()}",
        "",
    ]

    # Remove stock purchasables from shared (inherited by all factions).
    shared_strip = strip.get("shared") or []
    if shared_strip:
        lines.append("![shared]")
        for key in sorted(set(shared_strip)):
            lines.append(f"!{key}")
        lines.append("")

    for faction in FACTIONS:
        sec_name = f"{faction}_loadout"
        shop = shops[faction]
        faction_strip = strip.get(sec_name) or []
        # Also drop any shared keys we re-add? No — shared already stripped.
        lines.append(f"![{sec_name}]")
        for key in sorted(set(faction_strip)):
            if key not in shop:
                lines.append(f"!{key}")
        for item_sec in sorted(shop.keys()):
            pts = int(shop[item_sec])
            lines.append(f"{item_sec} = true,1,{pts}")
        lines.append("")

    if ammo_types:
        lines.append("![ammo_type_per_wpn]")
        for wpn in sorted(ammo_types.keys()):
            lines.append(f"{wpn} = {ammo_types[wpn]}")
        lines.append("")

    if ammo_counts:
        lines.append("![ammo_count]")
        for ammo in sorted(ammo_counts.keys()):
            lines.append(f"{ammo} = {ammo_counts[ammo]}")
        lines.append("")

    dest.write_text("\n".join(lines), encoding="utf-8")
    n_items = sum(len(s) for s in shops.values())
    log.info(
        "exported %s (%d faction shops, %d item rows, %d ammo counts)",
        dest,
        len(FACTIONS),
        n_items,
        len(ammo_counts),
    )
    return dest


def default_export_path(gamma: Path | None = None) -> Path:
    """Prefer repo feature config; optional gamma overwrite for live testing."""
    if gamma:
        live = (
            gamma
            / "overwrite"
            / "gamedata"
            / "configs"
            / "mod_new_game_loadouts_dogma_stat_derived.ltx"
        )
        return live
    return _DEFAULT_EXPORT
