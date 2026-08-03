"""Scan merged configs → items.yml with stats + placeholder thumbs."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from .diaglog import get_logger
from .ltx_merge import merge_configs, resolve_icon_bundle
from .settings import ITEMS_YML, THUMBS_DIR, ensure_dirs
from .spawn_filter import (
    is_explosive_weapon,
    is_gauss_weapon,
    is_spawnable_gear,
    load_spawner_blacklist,
    looks_like_weapon,
    name_blocked,
)
from .export_ltx import cache_stock_strip
from .lua_calc import calc_backend
from .motions import MotionLibrary, weapon_reload_seconds, weapon_use_mag
from .stats_calc import (
    PROT_KEYS,
    armor_calculate,
    build_armor_input,
    build_weapon_input,
    weapon_calculate,
)
from .strings import display_name_for, load_string_table
from .thumbs import iter_texture_roots, make_thumb, set_texture_roots

log = get_logger("regenerate")

ProgressCb = Callable[[str, int, int], None]
_RELOAD_SOURCE = "omf_v1"


def _has(d: dict[str, str], key: str) -> bool:
    return key in d and str(d[key]).strip() != ""


def classify(
    sections: dict[str, dict[str, str]],
    *,
    ignore: set[str] | None = None,
) -> dict[str, list[str]]:
    """Classify gear using debug-spawner style filters (drops attachment/kit/_cw)."""
    ignore = ignore or set()
    weapons: list[str] = []
    outfits: list[str] = []
    helmets: list[str] = []
    skipped_parent = 0
    skipped_stub = 0
    for sec, d in sections.items():
        gear_candidate = (
            looks_like_weapon(d)
            or (d.get("kind") or "").lower() in ("o_light", "o_medium", "o_heavy")
            or (d.get("class") or "").upper()
            in ("EQU_STLK", "E_STLK", "E_HLMET", "EQU_HLMET")
            or "helm" in sec.lower()
            or (
                any(_has(d, k) for k in PROT_KEYS[:3])
                and not _has(d, "ammo_class")
            )
        )
        if not gear_candidate:
            continue
        if not is_spawnable_gear(sec, d, ignore=ignore, require_parent_self=True):
            parent = (d.get("parent_section") or "").strip()
            if parent and parent != sec:
                skipped_parent += 1
            else:
                skipped_stub += 1
            continue
        cls = (d.get("class") or "").upper()
        kind = (d.get("kind") or "").lower()
        if looks_like_weapon(d):
            if is_explosive_weapon(sec, d, sections) or is_gauss_weapon(sec, d):
                continue
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
        if any(_has(d, k) for k in PROT_KEYS[:3]) and not _has(d, "ammo_class"):
            if "helm" in sec.lower():
                helmets.append(sec)
            else:
                outfits.append(sec)
    log.info(
        "classify kept w/o/h=%d/%d/%d dropped attachment/kit=%d stub/name=%d",
        len(set(weapons)),
        len(set(outfits)),
        len(set(helmets)),
        skipped_parent,
        skipped_stub,
    )
    return {
        "weapons": sorted(set(weapons)),
        "outfits": sorted(set(outfits)),
        "helmets": sorted(set(helmets)),
    }


def regenerate(
    anomaly: Path | None,
    gamma: Path | None,
    progress: ProgressCb | None = None,
    out_path: Path | None = None,
) -> Path:
    ensure_dirs()
    out = out_path or ITEMS_YML
    log.info("regenerate begin anomaly=%s gamma=%s out=%s", anomaly, gamma, out)
    set_texture_roots(iter_texture_roots(anomaly, gamma))
    # Force fresh inv_grid crops against current DDS roots.
    for pattern in ("*.inv.png", "*.fallback.png"):
        for stale in THUMBS_DIR.glob(pattern):
            try:
                stale.unlink()
            except OSError:
                pass

    def prog(msg: str, cur: int, total: int) -> None:
        if progress:
            progress(msg, cur, total)

    prog("string tables", 0, 1)
    string_table = load_string_table(anomaly, gamma)

    prog("stock shop strip", 0, 1)
    cache_stock_strip(anomaly, gamma)

    sections, icon_bundles, section_parents = merge_configs(
        anomaly, gamma, progress=prog
    )
    ignore = load_spawner_blacklist(anomaly)
    log.info("classify %d sections (blacklist=%d)", len(sections), len(ignore))
    pools = classify(sections, ignore=ignore)
    log.info(
        "classified weapons=%d outfits=%d helmets=%d",
        len(pools["weapons"]),
        len(pools["outfits"]),
        len(pools["helmets"]),
    )
    items: dict[str, Any] = {
        "meta": {
            "schema": 1,
            "generated": datetime.now(timezone.utc).isoformat(),
            "counts": {k: len(v) for k, v in pools.items()},
            "anomaly": str(anomaly) if anomaly else "",
            "gamma": str(gamma) if gamma else "",
            "reload_source": _RELOAD_SOURCE,
            "stats_calc": calc_backend(),
        },
        "weapons": {},
        "outfits": {},
        "helmets": {},
    }

    log.info("stats calculator backend: %s", calc_backend())
    prog("motion library", 0, 1)
    motion_lib = MotionLibrary(anomaly, gamma)

    wlist = pools["weapons"]
    w_fail = 0
    reload_ok = 0
    for i, sec in enumerate(wlist):
        d = sections[sec]
        inp = build_weapon_input(sec, sections)
        inp["use_mag"] = weapon_use_mag(sec, sections)
        reload_s = weapon_reload_seconds(sec, sections, motion_lib)
        if reload_s and reload_s > 0:
            inp["reload_s"] = float(reload_s)
            reload_ok += 1
        try:
            stats = weapon_calculate(inp)
            # Engine 0–1 → SALE percent for display / scale max / scoring.
            if stats.get("hit_power") is not None:
                from .score import hit_power_pct

                stats["hit_power"] = hit_power_pct(stats["hit_power"])
        except Exception as exc:  # noqa: BLE001
            w_fail += 1
            log.exception("weapon calc failed %s: %s", sec, exc)
            stats = {}
        try:
            own_bundle = icon_bundles.get(sec)
            parent = section_parents.get(sec)
            parent_bundle = (
                resolve_icon_bundle(parent, icon_bundles, section_parents)
                if parent
                else None
            )
            thumb = make_thumb(
                sec,
                d,
                THUMBS_DIR,
                icon_bundle=own_bundle,
                parent_icon_bundle=parent_bundle,
            )
        except Exception:  # noqa: BLE001
            log.exception("weapon thumb failed %s", sec)
            thumb = None
        items["weapons"][sec] = {
            "name": display_name_for(sec, d, string_table),
            "inv_name": (d.get("inv_name") or "").strip(),
            "inv_name_short": (d.get("inv_name_short") or "").strip(),
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
            if i % 200 == 0:
                log.debug("weapons progress %d/%d last=%s", i + 1, len(wlist), sec)
    log.info(
        "weapons done fail=%d/%d reload_from_omf=%d/%d",
        w_fail,
        len(wlist),
        reload_ok,
        len(wlist),
    )

    for cat, is_helm in (("outfits", False), ("helmets", True)):
        clist = pools[cat]
        c_fail = 0
        for i, sec in enumerate(clist):
            d = sections[sec]
            inp = build_armor_input(sec, sections)
            inp["is_helmet"] = is_helm or inp.get("is_helmet")
            try:
                stats = armor_calculate(inp)
            except Exception as exc:  # noqa: BLE001
                c_fail += 1
                log.exception("%s calc failed %s: %s", cat, sec, exc)
                stats = {}
            try:
                own_bundle = icon_bundles.get(sec)
                parent = section_parents.get(sec)
                parent_bundle = (
                    resolve_icon_bundle(parent, icon_bundles, section_parents)
                    if parent
                    else None
                )
                thumb = make_thumb(
                    sec,
                    d,
                    THUMBS_DIR,
                    icon_bundle=own_bundle,
                    parent_icon_bundle=parent_bundle,
                )
            except Exception:  # noqa: BLE001
                log.exception("%s thumb failed %s", cat, sec)
                thumb = None
            items[cat][sec] = {
                "name": display_name_for(sec, d, string_table),
                "inv_name": (d.get("inv_name") or "").strip(),
                "inv_name_short": (d.get("inv_name_short") or "").strip(),
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
        log.info("%s done fail=%d/%d", cat, c_fail, len(clist))

    ammo_secs: set[str] = set()
    for w in items["weapons"].values():
        for a in w.get("ammo_class") or []:
            if a:
                ammo_secs.add(a)
    items["ammo"] = {}
    a_fail = 0
    for i, a in enumerate(sorted(ammo_secs)):
        if "gauss" in a.lower() or name_blocked(a):
            continue
        d = sections.get(a) or {}
        try:
            own_bundle = icon_bundles.get(a)
            parent = section_parents.get(a)
            parent_bundle = (
                resolve_icon_bundle(parent, icon_bundles, section_parents)
                if parent
                else None
            )
            thumb = make_thumb(
                a,
                d,
                THUMBS_DIR,
                icon_bundle=own_bundle,
                parent_icon_bundle=parent_bundle,
            )
        except Exception:  # noqa: BLE001
            a_fail += 1
            log.exception("ammo thumb failed %s", a)
            thumb = None
        items["ammo"][a] = {
            "name": display_name_for(a, d, string_table),
            "inv_name": (d.get("inv_name") or "").strip(),
            "inv_name_short": (d.get("inv_name_short") or "").strip(),
            "box_size": float(d.get("box_size") or 50),
            "inv_grid_x": d.get("inv_grid_x"),
            "inv_grid_y": d.get("inv_grid_y"),
            "inv_grid_width": d.get("inv_grid_width"),
            "inv_grid_height": d.get("inv_grid_height"),
            "icons_texture": d.get("icons_texture") or "",
            "thumb": str(thumb) if thumb else "",
        }
        if progress and (i % 40 == 0 or i + 1 == len(ammo_secs)):
            prog(f"ammo {a}", i + 1, len(ammo_secs))
    log.info("ammo types=%d thumb_fail=%d", len(items["ammo"]), a_fail)

    try:
        text = yaml.safe_dump(items, sort_keys=False)
        out.write_text(text, encoding="utf-8")
        log.info("wrote %s (%d bytes)", out, len(text.encode("utf-8")))
    except Exception:
        log.exception("failed writing %s", out)
        raise
    prog("done", 1, 1)
    return out


def load_items(path: Path | None = None) -> dict[str, Any]:
    """Load cached items.yml only — no Anomaly/GAMMA filesystem access.

    Names, reload times, and thumbs are produced by ``regenerate``.
    """
    p = path or ITEMS_YML
    if not p.is_file():
        log.warning("items missing: %s", p)
        return {}
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        # Drop name-blocked / explosive / gauss from older caches without regenerate.
        # Also rename legacy mut/mid/hvy/max score keys → Min/Lgt/Lgt+/Mid/Mid+.
        _stat_renames = [
            ("mut_dmg", "min_dmg"),
            ("mut_dps", "min_dps"),
            ("mid_dmg", "lgtp_dmg"),
            ("mid_dps", "lgtp_dps"),
            ("hvy_dmg", "mid_dmg"),
            ("hvy_dps", "mid_dps"),
            ("max_dmg", "midp_dmg"),
            ("max_dps", "midp_dps"),
        ]
        renamed_stats = 0
        for cat in ("weapons", "outfits", "helmets"):
            pool = data.get(cat) or {}
            if not isinstance(pool, dict):
                continue
            blocked = [
                sec
                for sec, entry in pool.items()
                if name_blocked(sec)
                or (
                    cat == "weapons"
                    and (
                        is_explosive_weapon(
                            sec, ammo_class=entry.get("ammo_class") or []
                        )
                        or is_gauss_weapon(
                            sec, ammo_class=entry.get("ammo_class") or []
                        )
                    )
                )
            ]
            for sec in blocked:
                pool.pop(sec, None)
            if blocked:
                log.info("load_items dropped %d %s via filters", len(blocked), cat)
            for entry in pool.values():
                stats = entry.get("stats")
                if not isinstance(stats, dict):
                    continue
                if cat == "weapons":
                    for old, new in _stat_renames:
                        if old not in stats:
                            continue
                        if new not in stats:
                            stats[new] = stats.pop(old)
                        else:
                            stats.pop(old)
                        renamed_stats += 1
                    if "rounds_n" in stats:
                        stats.pop("rounds_n", None)
                        renamed_stats += 1
                # Undo bad ballistic rename if present.
                if "br_protection" in stats:
                    if "fire_wound_protection" not in stats:
                        stats["fire_wound_protection"] = stats.pop("br_protection")
                    else:
                        stats.pop("br_protection", None)
                    renamed_stats += 1
                if "is_helmet" in stats:
                    stats.pop("is_helmet", None)
                    renamed_stats += 1
        if renamed_stats:
            log.info("load_items renamed/dropped %d legacy score stat keys", renamed_stats)
        ammo_pool = data.get("ammo") or {}
        if isinstance(ammo_pool, dict):
            drop_ammo = [s for s in ammo_pool if "gauss" in s.lower() or name_blocked(s)]
            for sec in drop_ammo:
                ammo_pool.pop(sec, None)
            if drop_ammo:
                log.info("load_items dropped %d ammo via filters", len(drop_ammo))
        meta = data.get("meta") or {}
        log.info(
            "load_items %s counts=%s",
            p,
            meta.get("counts")
            or {
                "weapons": len(data.get("weapons") or {}),
                "outfits": len(data.get("outfits") or {}),
                "helmets": len(data.get("helmets") or {}),
            },
        )
        return data
    except Exception:
        log.exception("load_items failed: %s", p)
        raise
