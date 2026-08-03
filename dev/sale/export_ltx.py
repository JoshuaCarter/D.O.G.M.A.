"""Export native new_game_loadouts DLTX — no in-game Lua needed."""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .balance import ammo_enabled_map, effective_category
from .diaglog import get_logger
from .score import (
    FACTION_COMMUNITY,
    FACTIONS,
    armor_pts,
    is_bad_ammo,
    weapon_ammo_allowed,
    weapon_enabled_ammos,
    weapon_name_faction_ok,
    weapon_pts,
)
from .settings import STOCK_STRIP_YML, ensure_dirs

log = get_logger("export")


def ceil_pts_10(pts: int | float) -> int:
    """Round points up to the nearest 10 for LTX output (min 10)."""
    p = max(0, int(pts))
    if p <= 0:
        return 10
    return int(math.ceil(p / 10.0) * 10)

# Stock / GAMMA shop lines to strip (true,*) so only editor gear remains.
# Parsed from a live new_game_loadouts.ltx when available.
_LOADOUT_SECTIONS = ["shared"] + [f"{f}_loadout" for f in FACTIONS]

_REPO_SRC = Path(__file__).resolve().parents[2] / "src" / "tweaks" / "stat_derived_loadout"
_DEFAULT_EXPORT = (
    _REPO_SRC / "configs" / "mod_new_game_loadouts_dogma_stat_derived.ltx"
)


def _in_shop_weapon(
    sec: str,
    entry: dict[str, Any],
    faction: str,
    cat_cfg: dict[str, Any],
    *,
    ammo_map: dict[str, bool] | None = None,
) -> tuple[bool, int]:
    stats = entry.get("stats") or {}
    pts = weapon_pts(
        stats,
        cat_cfg.get("weights") or {},
        float(cat_cfg.get("cost_mult") or 1000),
        cat_cfg.get("ceilings"),
        cat_cfg.get("curves"),
    )
    if pts >= float(cat_cfg.get("max_pts") or 900):
        return False, pts
    if not weapon_name_faction_ok(sec, faction):
        return False, pts
    if not weapon_ammo_allowed(entry.get("ammo_class") or [], ammo_map):
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
        ceilings=cat_cfg.get("ceilings"),
        curves=cat_cfg.get("curves"),
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


def first_good_ammo(
    ammo_class: list[str] | None,
    enabled_map: dict[str, bool] | None = None,
) -> str | None:
    """First enabled ammo for a gun; prefer non-bad, else first enabled."""
    enabled = weapon_enabled_ammos(ammo_class, enabled_map)
    for ammo in enabled:
        if ammo and not is_bad_ammo(ammo):
            return ammo
    return enabled[0] if enabled else None


def build_faction_shop(
    items: dict[str, Any], balance: dict[str, Any], faction: str
) -> dict[str, int]:
    """sec -> pts for purchasable gear (ammo granted via ammo_count, not shop)."""
    shop: dict[str, int] = {}
    wcfg = effective_category(balance, faction, "weapons")
    ocfg = effective_category(balance, faction, "outfits")
    hcfg = effective_category(balance, faction, "helmets")
    ammo_map = ammo_enabled_map(balance, faction)

    for sec, entry in (items.get("weapons") or {}).items():
        ok, pts = _in_shop_weapon(sec, entry, faction, wcfg, ammo_map=ammo_map)
        if ok:
            shop[sec] = ceil_pts_10(pts)

    for sec, entry in (items.get("outfits") or {}).items():
        ok, pts = _in_shop_armor(entry, faction, ocfg, is_helmet=False)
        if ok:
            shop[sec] = ceil_pts_10(pts)

    for sec, entry in (items.get("helmets") or {}).items():
        ok, pts = _in_shop_armor(entry, faction, hcfg, is_helmet=True)
        if ok:
            shop[sec] = ceil_pts_10(pts)

    return shop


def collect_ammo_for_shops(
    items: dict[str, Any],
    shops: dict[str, dict[str, int]],
    balance: dict[str, Any],
) -> dict[str, int]:
    """ammo_sec -> rounds (4 stacks). Only enabled ammos used by shop guns."""
    ammo_box = {
        sec: float((meta or {}).get("box_size") or 50)
        for sec, meta in (items.get("ammo") or {}).items()
    }
    out: dict[str, int] = {}
    weapons = items.get("weapons") or {}
    for faction, shop in shops.items():
        ammo_map = ammo_enabled_map(balance, faction)
        for sec in shop:
            entry = weapons.get(sec)
            if not entry:
                continue
            for ammo in weapon_enabled_ammos(entry.get("ammo_class"), ammo_map):
                if not ammo:
                    continue
                box = ammo_box.get(ammo, 50.0)
                out[ammo] = int(round(4 * box))
    return out


def collect_ammo_type_overrides(
    items: dict[str, Any],
    shops: dict[str, dict[str, int]],
    balance: dict[str, Any],
) -> dict[str, str]:
    """weapon -> first enabled ammo (prefer non-bad)."""
    out: dict[str, str] = {}
    weapons = items.get("weapons") or {}
    for faction, shop in shops.items():
        ammo_map = ammo_enabled_map(balance, faction)
        for sec in shop:
            entry = weapons.get(sec)
            if not entry:
                continue
            ammo = first_good_ammo(entry.get("ammo_class"), ammo_map)
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


def find_stock_loadouts(
    anomaly: Path | None = None, gamma: Path | None = None
) -> Path | None:
    """Locate stock new_game_loadouts.ltx (regenerate-time only)."""
    candidates: list[Path] = []
    if gamma:
        candidates.extend(
            [
                gamma
                / "overwrite"
                / "gamedata"
                / "configs"
                / "items"
                / "settings"
                / "new_game_loadouts.ltx",
                gamma
                / "gamedata"
                / "configs"
                / "items"
                / "settings"
                / "new_game_loadouts.ltx",
            ]
        )
    if anomaly:
        candidates.extend(
            [
                anomaly
                / "tools"
                / "_unpacked"
                / "configs"
                / "items"
                / "settings"
                / "new_game_loadouts.ltx",
                anomaly
                / "gamedata"
                / "configs"
                / "items"
                / "settings"
                / "new_game_loadouts.ltx",
            ]
        )
    for p in candidates:
        if p.is_file():
            return p
    return None


def cache_stock_strip(
    anomaly: Path | None = None, gamma: Path | None = None
) -> dict[str, list[str]]:
    """Parse stock shop keys → cache/stock_strip.yml (call from regenerate)."""
    ensure_dirs()
    stock = find_stock_loadouts(anomaly, gamma)
    strip = (
        parse_purchasable_keys(stock)
        if stock
        else {s: [] for s in _LOADOUT_SECTIONS}
    )
    try:
        STOCK_STRIP_YML.write_text(
            yaml.safe_dump(strip, sort_keys=False), encoding="utf-8"
        )
        n = sum(len(v) for v in strip.values())
        log.info(
            "cached stock strip keys=%d from %s → %s",
            n,
            stock or "(none)",
            STOCK_STRIP_YML,
        )
    except OSError:
        log.exception("failed writing stock strip cache")
    return strip


def load_stock_strip() -> dict[str, list[str]]:
    """Load strip keys from regenerate cache (no Anomaly/GAMMA access)."""
    if not STOCK_STRIP_YML.is_file():
        return {s: [] for s in _LOADOUT_SECTIONS}
    try:
        data = yaml.safe_load(STOCK_STRIP_YML.read_text(encoding="utf-8")) or {}
    except Exception:
        log.exception("stock strip cache load failed")
        return {s: [] for s in _LOADOUT_SECTIONS}
    out: dict[str, list[str]] = {s: [] for s in _LOADOUT_SECTIONS}
    if isinstance(data, dict):
        for sec, keys in data.items():
            if sec in out and isinstance(keys, list):
                out[sec] = [str(k) for k in keys]
    return out


def export_shop_ltx(
    items: dict[str, Any],
    balance: dict[str, Any],
    dest: Path,
    *,
    stock_ltx: Path | None = None,
) -> Path:
    log.info("export begin dest=%s", dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    shops = {f: build_faction_shop(items, balance, f) for f in FACTIONS}
    for fac, shop in shops.items():
        log.debug("shop_%s items=%d", fac, len(shop))
    ammo_counts = collect_ammo_for_shops(items, shops, balance)
    ammo_types = collect_ammo_type_overrides(items, shops, balance)
    log.info(
        "export shops ready ammo_counts=%d ammo_type_overrides=%d",
        len(ammo_counts),
        len(ammo_types),
    )

    if stock_ltx and stock_ltx.is_file():
        strip = parse_purchasable_keys(stock_ltx)
        log.info(
            "stripping %d stock shop keys from explicit %s",
            sum(len(v) for v in strip.values()),
            stock_ltx,
        )
    else:
        strip = load_stock_strip()
        n_strip = sum(len(v) for v in strip.values())
        if n_strip:
            log.info(
                "stripping %d stock shop keys from cache %s",
                n_strip,
                STOCK_STRIP_YML,
            )
        else:
            log.warning(
                "no stock strip cache — regenerate once to capture stock shop keys"
            )

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


def default_export_path(_gamma: Path | None = None) -> Path:
    """Repo feature config path (no Anomaly/GAMMA required at export time)."""
    return _DEFAULT_EXPORT
