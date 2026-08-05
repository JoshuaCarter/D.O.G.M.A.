"""Export SALE-selected items as a minimal new_game_loadouts.ltx patch.

Only writes what SALE controls: per-faction gear shop lines (helmets →
outfits → weapons). No ammo sections, money, shared/custom, or template
preservation — those belong to the base mod, not SALE.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .balance import (
    item_in_ltx_for_faction,
    item_pts_for,
)
from .diaglog import get_logger
from .score import FACTIONS

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


def build_faction_shop(
    items: dict[str, Any], balance: dict[str, Any], faction: str
) -> dict[str, int]:
    """sec → manual pts for checkbox-included gear."""
    shop: dict[str, int] = {}
    for cat in ("weapons", "outfits", "helmets"):
        pool = items.get(cat) or {}
        for sec in pool:
            if not item_in_ltx_for_faction(balance, faction, sec):
                continue
            shop[sec] = item_pts_for(balance, sec, faction)
    return shop


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

    No money / shared / custom / ammo / template preservation — SALE only owns
    the gear shop lines, so that's all this writes.
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

    text = "\n".join(out_lines)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    if not text.endswith("\n"):
        text += "\n"

    dest.write_text(text, encoding="utf-8")
    n_items = sum(len(s) for s in shops.values())
    log.info(
        "exported %s (%d faction shops, %d item rows, no ammo sections)",
        dest,
        len(FACTIONS),
        n_items,
    )
    return dest


def default_export_path(_gamma: Path | None = None) -> Path:
    """Local export under ``dev/sale/out/new_game_loadouts.ltx``."""
    return _DEFAULT_EXPORT
