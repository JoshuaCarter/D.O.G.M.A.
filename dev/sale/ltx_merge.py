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
                break
    mo2 = find_mo2_root(gamma)
    if mo2 and (mo2 / "mods").is_dir():
        modlist = mo2 / "profiles"
        # Prefer first profile with modlist.txt
        profile_lists = sorted(modlist.glob("*/modlist.txt")) if modlist.is_dir() else []
        enabled: list[str] = []
        if profile_lists:
            # Active profile often in ModOrganizer.ini — use newest modlist as heuristic.
            profile_lists.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            enabled = _parse_modlist(profile_lists[0])
            log.info("modlist %s (%d enabled)", profile_lists[0], len(enabled))
        mods_dir = mo2 / "mods"
        for name in enabled:
            cfg = mods_dir / name / "gamedata" / "configs"
            if cfg.is_dir():
                roots.append(cfg)
        ow = mo2 / "overwrite" / "gamedata" / "configs"
        if ow.is_dir():
            roots.append(ow)
    elif gamma:
        # Flat scan of gamma/mods/*/gamedata/configs (unordered fallback)
        mods = gamma / "mods"
        if mods.is_dir():
            for cfg in sorted(mods.glob("*/gamedata/configs")):
                if cfg.is_dir():
                    roots.append(cfg)
    return roots


def _apply_file(sections: dict[str, dict[str, str]], path: Path) -> int:
    n = 0
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    cur: str | None = None
    parent: str | None = None
    patch = False
    for raw in text.splitlines():
        line = raw.split(";", 1)[0].rstrip()
        if not line.strip():
            continue
        m = _SEC_RE.match(line)
        if m:
            bang, name, par = m.group(1), m.group(2).strip(), m.group(3)
            patch = bang.startswith("!")
            cur = name
            parent = par.strip() if par else None
            if cur not in sections:
                sections[cur] = {}
                if parent and parent in sections:
                    sections[cur] = dict(sections[parent])
            elif not patch and parent and parent in sections:
                # Fresh section with parent — start from parent then overwrite
                base = dict(sections[parent])
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
    return n


def merge_configs(
    anomaly: Path | None,
    gamma: Path | None,
    progress: ProgressCb | None = None,
) -> dict[str, dict[str, str]]:
    """Return section_name → {key: value}."""
    roots = iter_config_roots(anomaly, gamma)
    sections: dict[str, dict[str, str]] = {}
    files: list[Path] = []
    for root in roots:
        files.extend(sorted(root.rglob("*.ltx")))
    total = len(files)
    log.info("merging %d ltx files from %d roots", total, len(roots))
    for i, path in enumerate(files):
        _apply_file(sections, path)
        if progress and (i % 50 == 0 or i + 1 == total):
            progress(f"merge {path.name}", i + 1, total)
    log.info("merged %d sections", len(sections))
    return sections
