"""Scan merged configs → items.yml with stats + placeholder thumbs."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from .diaglog import get_logger
from .ltx_merge import merge_configs
from .settings import ITEMS_YML, THUMBS_DIR, ensure_dirs
from .stats_calc import (
    PROT_KEYS,
    armor_calculate,
    build_armor_input,
    build_weapon_input,
    weapon_calculate,
)
from .thumbs import make_thumb

log = get_logger("regenerate")

ProgressCb = Callable[[str, int, int], None]


def _has(d: dict[str, str], key: str) -> bool:
    return key in d and str(d[key]).strip() != ""


def classify(sections: dict[str, dict[str, str]]) -> dict[str, list[str]]:
    weapons: list[str] = []
    outfits: list[str] = []
    helmets: list[str] = []
    for sec, d in sections.items():
        if sec.startswith("!") or " " in sec:
            continue
        if not _has(d, "inv_grid_x"):
            continue
        cls = (d.get("class") or "").upper()
        kind = (d.get("kind") or "").lower()
        if _has(d, "ammo_class") and cls.startswith("WP_"):
            weapons.append(sec)
            continue
        if _has(d, "ammo_class") and any(
            k in d for k in ("rpm", "ammo_mag_size", "hit_power")
        ):
            weapons.append(sec)
            continue
        if kind in ("o_light", "o_medium", "o_heavy") or cls in (
            "EQU_STLK",
            "E_STLK",
        ):
            outfits.append(sec)
            continue
        if "helm" in sec.lower() or cls in ("E_HLMET", "EQU_HLMET"):
            helmets.append(sec)
            continue
        # Protection-heavy sections without ammo → outfit-ish
        if any(_has(d, k) for k in PROT_KEYS[:3]) and not _has(d, "ammo_class"):
            if "helm" in sec.lower():
                helmets.append(sec)
            else:
                outfits.append(sec)
    return {
        "weapons": sorted(set(weapons)),
        "outfits": sorted(set(outfits)),
        "helmets": sorted(set(helmets)),
    }


def _display_name(sec: str, d: dict[str, str]) -> str:
    return (d.get("inv_name") or d.get("inv_name_short") or sec).strip()


def regenerate(
    anomaly: Path | None,
    gamma: Path | None,
    progress: ProgressCb | None = None,
    out_path: Path | None = None,
) -> Path:
    ensure_dirs()
    out = out_path or ITEMS_YML

    def prog(msg: str, cur: int, total: int) -> None:
        if progress:
            progress(msg, cur, total)

    sections = merge_configs(anomaly, gamma, progress=prog)
    pools = classify(sections)
    items: dict[str, Any] = {
        "meta": {
            "schema": 1,
            "generated": datetime.now(timezone.utc).isoformat(),
            "counts": {k: len(v) for k, v in pools.items()},
        },
        "weapons": {},
        "outfits": {},
        "helmets": {},
    }

    # Weapons
    wlist = pools["weapons"]
    for i, sec in enumerate(wlist):
        d = sections[sec]
        inp = build_weapon_input(sec, sections)
        try:
            stats = weapon_calculate(inp)
        except Exception as exc:  # noqa: BLE001
            log.exception("weapon %s: %s", sec, exc)
            stats = {}
        thumb = make_thumb(sec, d, THUMBS_DIR)
        items["weapons"][sec] = {
            "name": _display_name(sec, d),
            "cost": float(d.get("cost") or 0),
            "ammo_class": [
                p.strip() for p in str(d.get("ammo_class") or "").split(",") if p.strip()
            ],
            "community": d.get("community") or "",
            "inv_grid_x": d.get("inv_grid_x"),
            "inv_grid_y": d.get("inv_grid_y"),
            "inv_grid_width": d.get("inv_grid_width"),
            "inv_grid_height": d.get("inv_grid_height"),
            "icons_texture": d.get("icons_texture") or "",
            "stats": stats,
            "thumb": str(thumb) if thumb else "",
        }
        if progress and (i % 20 == 0 or i + 1 == len(wlist)):
            prog(f"weapons {sec}", i + 1, len(wlist))

    for cat, is_helm in (("outfits", False), ("helmets", True)):
        clist = pools[cat]
        for i, sec in enumerate(clist):
            d = sections[sec]
            inp = build_armor_input(sec, sections)
            inp["is_helmet"] = is_helm or inp.get("is_helmet")
            try:
                stats = armor_calculate(inp)
            except Exception as exc:  # noqa: BLE001
                log.exception("%s %s: %s", cat, sec, exc)
                stats = {}
            thumb = make_thumb(sec, d, THUMBS_DIR)
            items[cat][sec] = {
                "name": _display_name(sec, d),
                "cost": float(d.get("cost") or 0),
                "community": d.get("community") or "",
                "inv_grid_x": d.get("inv_grid_x"),
                "inv_grid_y": d.get("inv_grid_y"),
                "inv_grid_width": d.get("inv_grid_width"),
                "inv_grid_height": d.get("inv_grid_height"),
                "icons_texture": d.get("icons_texture") or "",
                "stats": stats,
                "thumb": str(thumb) if thumb else "",
            }
            if progress and (i % 40 == 0 or i + 1 == len(clist)):
                prog(f"{cat} {sec}", i + 1, len(clist))

    ammo_secs: set[str] = set()
    for w in items["weapons"].values():
        for a in w.get("ammo_class") or []:
            if a:
                ammo_secs.add(a)
    items["ammo"] = {}
    for a in sorted(ammo_secs):
        d = sections.get(a) or {}
        items["ammo"][a] = {"box_size": float(d.get("box_size") or 50)}

    out.write_text(yaml.safe_dump(items, sort_keys=False), encoding="utf-8")
    log.info("wrote %s", out)
    prog("done", 1, 1)
    return out


def load_items(path: Path | None = None) -> dict[str, Any]:
    p = path or ITEMS_YML
    if not p.is_file():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
