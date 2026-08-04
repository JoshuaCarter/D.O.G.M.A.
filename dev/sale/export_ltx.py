"""Export SALE-selected items as a minimal new_game_loadouts.ltx patch.

Only writes what SALE controls: per-faction gear shop lines (helmets →
outfits → weapons) plus ammo type/count. No money, no shared/custom,
no template preservation — those belong to the base mod, not SALE.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .balance import (
    ammo_enabled_map,
    item_in_ltx_for_faction,
    item_pts_for,
)
from .diaglog import get_logger
from .score import (
    FACTIONS,
    is_bad_ammo,
    weapon_enabled_ammos,
)

log = get_logger("export")

# Always write under dev/sale/out — never the sobre_loadouts source tree.
_OUT_DIR = Path(__file__).resolve().parent / "out"
_DEFAULT_EXPORT = _OUT_DIR / "new_game_loadouts.ltx"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SOBRE_FORMAT_REF = (
    _REPO_ROOT
    / "src"
    / "tweaks"
    / "sobre_loadouts"
    / "configs"
    / "items"
    / "settings"
    / "new_game_loadouts.ltx"
)

_KEY_WIDTH = 39


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
    """sec → manual pts for checkbox-included gear (ammo via ammo_count, not shop)."""
    shop: dict[str, int] = {}
    for cat in ("weapons", "outfits", "helmets"):
        pool = items.get(cat) or {}
        for sec in pool:
            if not item_in_ltx_for_faction(balance, faction, sec):
                continue
            shop[sec] = item_pts_for(balance, sec, faction)
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


def _fmt_shop_line(sec: str, pts: int) -> str:
    return f"{sec:<{_KEY_WIDTH}} = true,1,{int(pts)}"


def _ordered_shop_lines(shop: dict[str, int], items: dict[str, Any]) -> list[str]:
    """Helmets → outfits → weapons (alpha within each), blank line between groups."""
    lines: list[str] = []
    seen: set[str] = set()
    for cat in ("helmets", "outfits", "weapons"):
        pool = items.get(cat) or {}
        secs = sorted(s for s in shop if s in pool)
        if not secs:
            continue
        if lines:
            lines.append("")
        for sec in secs:
            lines.append(_fmt_shop_line(sec, shop[sec]))
            seen.add(sec)
    extras = sorted(s for s in shop if s not in seen)
    if extras:
        if lines:
            lines.append("")
        for sec in extras:
            lines.append(_fmt_shop_line(sec, shop[sec]))
    return lines


def export_shop_ltx(
    items: dict[str, Any],
    balance: dict[str, Any],
    dest: Path,
    *,
    stock_ltx: Path | None = None,
) -> Path:
    """Write per-faction ``[faction_loadout]`` sections — selected gear only.

    No money / shared / custom / template preservation — SALE only owns the
    gear shop lines + ammo type/count, so that's all this writes.
    """
    del stock_ltx  # unused — kept for call-site compatibility
    if dest.resolve() == _SOBRE_FORMAT_REF.resolve():
        raise ValueError(
            "Refusing to export onto sobre_loadouts source "
            f"({_SOBRE_FORMAT_REF}). Use {default_export_path()}."
        )
    log.info("export begin dest=%s", dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    shops = {f: build_faction_shop(items, balance, f) for f in FACTIONS}
    for fac, shop in shops.items():
        n_h = sum(1 for s in shop if s in (items.get("helmets") or {}))
        n_o = sum(1 for s in shop if s in (items.get("outfits") or {}))
        n_w = sum(1 for s in shop if s in (items.get("weapons") or {}))
        log.debug(
            "shop_%s total=%d helmets=%d outfits=%d weapons=%d",
            fac,
            len(shop),
            n_h,
            n_o,
            n_w,
        )
    ammo_counts = collect_ammo_for_shops(items, shops, balance)
    ammo_types = collect_ammo_type_overrides(items, shops, balance)

    stamp = datetime.now(timezone.utc).isoformat()
    out_lines: list[str] = [
        f"; DOGMA SALE — generated {stamp}",
        "; Selected gear only — merge into new_game_loadouts.ltx faction sections.",
        "",
    ]

    for fac in FACTIONS:
        shop_lines = _ordered_shop_lines(shops.get(fac) or {}, items)
        out_lines.append(f"[{fac}_loadout]")
        out_lines.extend(shop_lines)
        out_lines.append("")

    if ammo_types:
        out_lines.append("[ammo_type_per_wpn]")
        for wpn in sorted(ammo_types.keys()):
            out_lines.append(f"{wpn:<{_KEY_WIDTH}} = {ammo_types[wpn]}")
        out_lines.append("")

    if ammo_counts:
        out_lines.append("[ammo_count]")
        for ammo in sorted(ammo_counts.keys()):
            out_lines.append(f"{ammo:<{_KEY_WIDTH}} = {ammo_counts[ammo]}")
        out_lines.append("")

    text = "\n".join(out_lines)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    if not text.endswith("\n"):
        text += "\n"

    dest.write_text(text, encoding="utf-8")
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
    """Local export under ``dev/sale/out/new_game_loadouts.ltx``."""
    return _DEFAULT_EXPORT
