"""Merge Anomaly/GAMMA LTX sections (enabled MO2 mods, last wins)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from .diaglog import get_logger

log = get_logger("ltx_merge")

_SEC_RE = re.compile(
    r"^\s*(\!?\[)([^\]]+)\](?:\s*:\s*([^\s]+))?",
    re.IGNORECASE,
)
_KV_RE = re.compile(r"^\s*([^=;\s][^=]*?)\s*=\s*(.*)$")


ProgressCb = Callable[[str, int, int], None]

# Snapshot when a file writes icons_texture — used if merged inv_grid_* is Frankenstein.
ICON_FIELD_KEYS = (
    "icons_texture",
    "inv_grid_x",
    "inv_grid_y",
    "inv_grid_width",
    "inv_grid_height",
)


def parse_modlist(modlist: Path) -> list[str]:
    """Return enabled mod folder names in list order (bottom = lower priority)."""
    return _parse_modlist(modlist)


def _parse_modlist(modlist: Path) -> list[str]:
    """Return enabled mod folder names in list order (bottom = lower priority)."""
    enabled: list[str] = []
    for raw in modlist.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        if line.startswith("+"):
            name = line[1:].strip()
            if name and not name.endswith("_separator"):
                enabled.append(name)
    return enabled


def find_mo2_root(gamma_root: Path | None) -> Path | None:
    if not gamma_root:
        return None
    # GAMMA install often IS the MO2 root.
    if (gamma_root / "ModOrganizer.exe").is_file() or (gamma_root / "mods").is_dir():
        return gamma_root
    return None


def iter_config_roots(anomaly: Path | None, gamma: Path | None) -> list[Path]:
    """Ordered roots: Anomaly unpacked/base first, then enabled mods, then overwrite."""
    roots: list[Path] = []
    if anomaly:
        for cand in (
            anomaly / "tools" / "_unpacked" / "configs",
            anomaly / "gamedata" / "configs",
        ):
            if cand.is_dir():
                roots.append(cand)
                log.info("anomaly configs root: %s", cand)
                break
        else:
            log.warning("no Anomaly configs under %s", anomaly)
    mo2 = find_mo2_root(gamma)
    if mo2 and (mo2 / "mods").is_dir():
        log.info("MO2 root: %s", mo2)
        modlist = mo2 / "profiles"
        profile_lists = sorted(modlist.glob("*/modlist.txt")) if modlist.is_dir() else []
        enabled: list[str] = []
        if profile_lists:
            profile_lists.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            enabled = _parse_modlist(profile_lists[0])
            log.info("modlist %s (%d enabled)", profile_lists[0], len(enabled))
        else:
            log.warning("no modlist.txt under %s", modlist)
        mods_dir = mo2 / "mods"
        added = 0
        # modlist.txt is highest-priority first (MO2 top). Apply low→high so
        # later roots win — matching game deploy order.
        for name in reversed(enabled):
            cfg = mods_dir / name / "gamedata" / "configs"
            if cfg.is_dir():
                roots.append(cfg)
                added += 1
        log.info("enabled mod config roots: %d (priority-corrected)", added)
        ow = mo2 / "overwrite" / "gamedata" / "configs"
        if ow.is_dir():
            roots.append(ow)
            log.info("overwrite configs: %s", ow)
    elif gamma:
        log.warning("GAMMA path is not MO2 root; flat-scanning mods: %s", gamma)
        mods = gamma / "mods"
        if mods.is_dir():
            for cfg in sorted(mods.glob("*/gamedata/configs")):
                if cfg.is_dir():
                    roots.append(cfg)
    else:
        log.warning("no GAMMA/MO2 root provided")
    log.info("total config roots: %d", len(roots))
    return roots


def _flush_icon_bundle(
    sec: str | None,
    block_wrote_icons: bool,
    sections: dict[str, dict[str, str]],
    icon_bundles: dict[str, dict[str, str]],
) -> None:
    if not sec or not block_wrote_icons:
        return
    d = sections.get(sec) or {}
    icon_bundles[sec] = {k: d[k] for k in ICON_FIELD_KEYS if k in d}


def _parent_list(par: str | None) -> list[str]:
    if not par:
        return []
    return [p.strip() for p in par.split(",") if p.strip()]


def _apply_file(
    sections: dict[str, dict[str, str]],
    path: Path,
    icon_bundles: dict[str, dict[str, str]] | None = None,
    section_parents: dict[str, str] | None = None,
) -> int:
    n = 0
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.warning("skip unreadable %s: %s", path, exc)
        return 0
    cur: str | None = None
    parents: list[str] = []
    patch = False
    block_wrote_icons = False
    bundles = icon_bundles if icon_bundles is not None else {}
    parents_out = section_parents if section_parents is not None else {}
    for raw in text.splitlines():
        line = raw.split(";", 1)[0].rstrip()
        if not line.strip():
            continue
        m = _SEC_RE.match(line)
        if m:
            _flush_icon_bundle(cur, block_wrote_icons, sections, bundles)
            bang, name, par = m.group(1), m.group(2).strip(), m.group(3)
            patch = bang.startswith("!")
            cur = name
            parents = _parent_list(par)
            block_wrote_icons = False
            primary = next((p for p in parents if p in sections), None)
            if primary and section_parents is not None:
                parents_out[cur] = primary
            if cur not in sections:
                sections[cur] = dict(sections[primary]) if primary else {}
            elif not patch and primary:
                # Fresh section with parent — start from parent then overwrite
                base = dict(sections[primary])
                base.update(sections[cur])
                sections[cur] = base
            n += 1
            continue
        if cur is None:
            continue
        km = _KV_RE.match(line)
        if not km:
            continue
        key = km.group(1).strip()
        val = km.group(2).strip()
        sections[cur][key] = val
        if key == "icons_texture":
            block_wrote_icons = True
    _flush_icon_bundle(cur, block_wrote_icons, sections, bundles)
    return n


def merge_configs(
    anomaly: Path | None,
    gamma: Path | None,
    progress: ProgressCb | None = None,
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]], dict[str, str]]:
    """Return (section→fields, icon_bundles, section→primary_parent).

    ``icon_bundles`` snapshots inv_grid_* whenever a file writes ``icons_texture``,
    so thumbs can recover when a later mod only patches one grid axis.
    """
    roots = iter_config_roots(anomaly, gamma)
    sections: dict[str, dict[str, str]] = {}
    icon_bundles: dict[str, dict[str, str]] = {}
    section_parents: dict[str, str] = {}
    files: list[Path] = []
    for root in roots:
        found = sorted(root.rglob("*.ltx"))
        log.debug("root %s -> %d ltx", root, len(found))
        files.extend(found)
    total = len(files)
    log.info("merging %d ltx files from %d roots", total, len(roots))
    errors = 0
    for i, path in enumerate(files):
        try:
            _apply_file(sections, path, icon_bundles, section_parents)
        except Exception:  # noqa: BLE001
            errors += 1
            log.exception("merge parse failed: %s", path)
        if progress and (i % 50 == 0 or i + 1 == total):
            progress(f"merge {path.name}", i + 1, total)
        if i > 0 and i % 500 == 0:
            log.debug("merge progress %d/%d sections=%d", i, total, len(sections))
    log.info(
        "merged %d sections (parse_errors=%d icon_bundles=%d)",
        len(sections),
        errors,
        len(icon_bundles),
    )
    return sections, icon_bundles, section_parents


def resolve_icon_bundle(
    sec: str,
    icon_bundles: dict[str, dict[str, str]],
    section_parents: dict[str, str],
) -> dict[str, str] | None:
    """Own bundle, else walk primary-parent chain."""
    seen: set[str] = set()
    cur: str | None = sec
    while cur and cur not in seen:
        hit = icon_bundles.get(cur)
        if hit:
            return hit
        seen.add(cur)
        cur = section_parents.get(cur)
    return None
