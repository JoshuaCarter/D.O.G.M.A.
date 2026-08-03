"""Per-weapon-kind LTX caps (at most N; lowest-pts fill, with force override)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .balance import get_item_ltx_override

# Stable UI order for kinds we care about; unknown kinds append alphabetically.
_KIND_ORDER = (
    "w_pistol",
    "w_shotgun",
    "w_smg",
    "w_rifle",
    "w_sniper",
)

_KIND_LABELS = {
    "w_pistol": "Pistol",
    "w_smg": "SMG",
    "w_rifle": "Rifle",
    "w_shotgun": "Shotgun",
    "w_sniper": "Sniper",
}

DEFAULT_KIND_LIMIT = 10
MAX_KIND_LIMIT = 10
# Flat pool cap for outfits / helmets (same range as per-kind weapon caps).
DEFAULT_ITEM_LIMIT = 10
MAX_ITEM_LIMIT = 10


def weapon_kind(entry: dict[str, Any] | None, sec: str | None = None) -> str:
    """LTX ``kind``, with a few GAMMA mis-tags corrected (sawn-offs as pistol)."""
    e = entry or {}
    k = str(e.get("kind") or "").strip().lower() or "w_other"
    sid = (sec or "").strip().lower()
    ammos = e.get("ammo_class") or []
    if isinstance(ammos, str):
        ammo_l = ammos.lower()
    else:
        ammo_l = ",".join(str(a) for a in ammos).lower()
    # Sawn-off / shotgun shells sometimes ship as w_pistol in GAMMA packs.
    if k == "w_pistol":
        if any(
            tok in sid
            for tok in (
                "ithaca",
                "toz106",
                "toz_106",
                "shotgun",
                "saiga",
                "mossberg",
                "spas12",
                "protecta",
                "striker",
            )
        ):
            return "w_shotgun"
        if any(
            tok in ammo_l
            for tok in ("12x70", "20x70", "12x76", "buck", "shotgun", "slug")
        ):
            return "w_shotgun"
    return k


def kind_label(kind: str) -> str:
    k = (kind or "").strip().lower()
    if k in _KIND_LABELS:
        return _KIND_LABELS[k]
    if k.startswith("w_"):
        return k[2:].replace("_", " ").title()
    return k.replace("_", " ").title() or "Other"


def present_weapon_kinds(weapons: dict[str, Any] | None) -> list[str]:
    """Kinds that appear in the weapon pool, UI order first."""
    seen: set[str] = set()
    for sec, entry in (weapons or {}).items():
        if isinstance(entry, dict):
            seen.add(weapon_kind(entry, sec))
    ordered = [k for k in _KIND_ORDER if k in seen]
    rest = sorted(k for k in seen if k not in _KIND_ORDER)
    return ordered + rest


def kind_limits_map(cat_cfg: dict[str, Any] | None) -> dict[str, int]:
    raw = (cat_cfg or {}).get("kind_limits") or {}
    out: dict[str, int] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                out[str(k)] = max(0, min(MAX_KIND_LIMIT, int(v)))
            except (TypeError, ValueError):
                out[str(k)] = DEFAULT_KIND_LIMIT
    return out


def kind_limit_for(cat_cfg: dict[str, Any] | None, kind: str) -> int:
    limits = kind_limits_map(cat_cfg)
    if kind in limits:
        return limits[kind]
    return DEFAULT_KIND_LIMIT


def set_kind_limit(balance: dict[str, Any], kind: str, value: int) -> None:
    w = (balance.setdefault("default", {})).setdefault("weapons", {})
    limits = w.setdefault("kind_limits", {})
    if not isinstance(limits, dict):
        limits = {}
        w["kind_limits"] = limits
    limits[str(kind)] = max(0, min(MAX_KIND_LIMIT, int(value)))


def item_limit_for(cat_cfg: dict[str, Any] | None) -> int:
    """At-most-N LTX cap for a flat category (outfits / helmets)."""
    try:
        return max(
            0,
            min(
                MAX_ITEM_LIMIT,
                int((cat_cfg or {}).get("max_items", DEFAULT_ITEM_LIMIT)),
            ),
        )
    except (TypeError, ValueError):
        return DEFAULT_ITEM_LIMIT


def set_item_limit(
    balance: dict[str, Any], category: str, value: int
) -> None:
    cat = str(category or "").strip()
    if cat not in ("outfits", "helmets"):
        return
    cfg = (balance.setdefault("default", {})).setdefault(cat, {})
    cfg["max_items"] = max(0, min(MAX_ITEM_LIMIT, int(value)))


def weapon_cost(entry: dict[str, Any] | None) -> float:
    try:
        return float((entry or {}).get("cost") or 0)
    except (TypeError, ValueError):
        return 0.0


def select_pool_for_ltx(
    pool: dict[str, Any],
    balance: dict[str, Any],
    *,
    eligible: dict[str, Any],
    limit: int,
) -> set[str]:
    """Pick LTX items from a flat pool under an at-most-N cap (lowest pts).

    Force-includes take slots first (may exceed N). Force-exclude never enters.
    ``eligible`` maps section id → pts for filter-ok items.
    """

    def _rank(sec: str) -> tuple[float, float, str]:
        try:
            p = float(eligible.get(sec, 1e18))
        except (TypeError, ValueError):
            p = 1e18
        return (p, weapon_cost(pool.get(sec)), sec)

    force_in: list[str] = []
    auto: list[str] = []
    for sec, entry in (pool or {}).items():
        if not isinstance(entry, dict):
            continue
        sid = str(sec)
        ov = get_item_ltx_override(balance, sid)
        if ov == "exclude":
            continue
        if ov == "include":
            force_in.append(sid)
            continue
        if sid in eligible:
            auto.append(sid)
    force_in.sort(key=_rank)
    auto.sort(key=_rank)
    selected: set[str] = set(force_in)
    slots = max(0, int(limit) - len(force_in))
    selected.update(auto[:slots])
    return selected


def select_weapons_for_ltx(
    weapons: dict[str, Any],
    balance: dict[str, Any],
    *,
    eligible: dict[str, Any],
    cat_cfg: dict[str, Any] | None = None,
) -> set[str]:
    """Pick LTX weapons per kind under an at-most-N cap (not a minimum).

    Among auto (non-forced) guns: keep the lowest-pts eligible until the cap
    (same number shown on tiles / Sort: pts). Tie-break: inventory cost, then id.
    Force-includes take slots first; if they already meet/exceed N, no auto fill.
    Force-include may exceed N (explicit override). Force-exclude never enters.
    ``eligible`` maps section id → pts for filter-ok guns.
    """
    by_kind: dict[str, list[str]] = defaultdict(list)
    for sec, entry in (weapons or {}).items():
        if not isinstance(entry, dict):
            continue
        by_kind[weapon_kind(entry, sec)].append(str(sec))

    selected: set[str] = set()
    for kind, secs in by_kind.items():
        sub = {s: (weapons.get(s) or {}) for s in secs}
        sub_eligible = {s: eligible[s] for s in secs if s in eligible}
        selected |= select_pool_for_ltx(
            sub,
            balance,
            eligible=sub_eligible,
            limit=kind_limit_for(cat_cfg, kind),
        )
    return selected
