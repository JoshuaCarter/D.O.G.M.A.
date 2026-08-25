"""Weapon kind helpers (display / regenerate). LTX include is checkbox-only."""

from __future__ import annotations

from typing import Any

_KIND_LABELS = {
    "w_pistol": "Pistol",
    "w_smg": "SMG",
    "w_rifle": "Rifle",
    "w_shotgun": "Shotgun",
    "w_sniper": "Sniper",
}


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
