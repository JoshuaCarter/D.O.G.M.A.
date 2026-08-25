"""Resolve Anomaly/GAMMA string-table IDs (inv_name) to UI text."""

from __future__ import annotations

import re
from pathlib import Path

from .diaglog import get_logger
from .ltx_merge import iter_config_roots

log = get_logger("strings")

# Engine color / format codes in string bodies, e.g. %c[0,255,255,255]
_ENGINE_MARKUP = re.compile(r"%c\[[^\]]*\]")
# Stalker string ids may include hyphens/dots (ammo-5.45x39-fmj).
_STRING_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]*$")
_STRING_ENTRY = re.compile(
    r'<string\s+id=["\']([^"\']+)["\']\s*>\s*<text>(.*?)</text>',
    re.IGNORECASE | re.DOTALL,
)


def strip_engine_markup(text: str) -> str:
    return _ENGINE_MARKUP.sub("", text).replace("\\n", "\n").strip()


def looks_like_string_id(content: str) -> bool:
    return bool(content) and bool(_STRING_ID.fullmatch(content))


def load_string_table(
    anomaly: Path | None,
    gamma: Path | None,
    *,
    lang: str = "eng",
) -> dict[str, str]:
    """Merge configs/text/<lang>/*.xml in MO2 order (later roots win)."""
    table: dict[str, str] = {}
    n_files = 0
    n_bad = 0
    for root in iter_config_roots(anomaly, gamma):
        text_dir = root / "text" / lang
        if not text_dir.is_dir():
            continue
        try:
            paths = sorted(text_dir.glob("*.xml"))
        except OSError:
            continue
        for path in paths:
            n_files += 1
            try:
                blob = path.read_bytes()
            except OSError:
                n_bad += 1
                continue
            raw: str | None = None
            for enc in ("utf-8-sig", "windows-1251", "cp1251", "latin-1"):
                try:
                    raw = blob.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            if raw is None:
                n_bad += 1
                continue
            # Regex: many stock XMLs are not well-formed for ElementTree.
            for sid, body in _STRING_ENTRY.findall(raw):
                sid = sid.strip()
                if sid:
                    table[sid] = body
    log.info(
        "string table lang=%s files=%d bad=%d ids=%d",
        lang,
        n_files,
        n_bad,
        len(table),
    )
    return table


def resolve_string(raw: str | None, table: dict[str, str]) -> str | None:
    """Return translated body for a string id, or literal text, or None if missing id."""
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    if text in table:
        body = strip_engine_markup(table[text])
        return body or None
    if looks_like_string_id(text):
        return None
    return strip_engine_markup(text) or None


def display_name_for(
    sec: str,
    d: dict[str, str],
    table: dict[str, str],
) -> str:
    """Match inventory/tooltip naming: inv_name, then inv_name_short."""
    for key in ("inv_name", "inv_name_short"):
        hit = resolve_string(d.get(key), table)
        if hit:
            return hit
    for cand in (f"st_{sec}", f"{sec}_name"):
        hit = resolve_string(cand, table)
        if hit:
            return hit
    return sec


def refresh_cached_names(
    items: dict,
    table: dict[str, str],
) -> int:
    """Re-resolve ``name`` on cached items (uses stored inv_name when present)."""
    changed = 0
    for cat in ("weapons", "outfits", "helmets", "ammo"):
        pool = items.get(cat)
        if not isinstance(pool, dict):
            continue
        for sec, entry in pool.items():
            if not isinstance(entry, dict):
                continue
            inv = str(entry.get("inv_name") or "").strip()
            short = str(entry.get("inv_name_short") or "").strip()
            old = str(entry.get("name") or "").strip()
            sec_s = str(sec)
            if inv or short:
                name = display_name_for(
                    sec_s, {"inv_name": inv, "inv_name_short": short}, table
                )
            elif old and old in table:
                name = strip_engine_markup(table[old]) or old
            elif (
                old
                and looks_like_string_id(old)
                and old.lower() != sec_s.lower()
            ):
                # Cached name is a string id (not the section) — resolve it.
                name = display_name_for(
                    sec_s, {"inv_name": old, "inv_name_short": ""}, table
                )
            else:
                # Bare section id: only st_{sec} / {sec}_name heuristics help;
                # real inv_name keys need a regenerate (or stored inv_name).
                name = display_name_for(sec_s, {}, table)
            if name and entry.get("name") != name:
                entry["name"] = name
                changed += 1
    return changed
