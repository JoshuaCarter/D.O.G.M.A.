#!/usr/bin/env python3
"""Register DOGMA bats in MO2 Executables (ModOrganizer.ini [customExecutables])."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import dogma_mo2_lib as lib

# MO2 Executables titles we own (no parentheses). Legacy aliases are stripped on re-run.
DOGMA_EXECUTABLE_TITLES: tuple[str, ...] = (
    "D.O.G.M.A. Setup",
    "D.O.G.M.A. Backup",
    "D.O.G.M.A. Restore",
    "D.O.G.M.A. Optimize",
)
_DOGMA_EXECUTABLE_BATS: tuple[str, ...] = (
    "DOGMA Setup.bat",
    "DOGMA Backup.bat",
    "DOGMA Restore.bat",
    "DOGMA Optimize.bat",
)
_DOGMA_EXECUTABLE_ARGUMENTS: dict[str, str] = {}
_DOGMA_EXECUTABLE_TITLE_ALIASES: frozenset[str] = frozenset(
    t.lower()
    for t in (
        *DOGMA_EXECUTABLE_TITLES,
        "DOGMA Setup",
        "DOGMA (Setup)",
        "DOGMA Backup",
        "DOGMA (Backup)",
        "DOGMA Restore",
        "DOGMA (Restore)",
        "DOGMA Optimize",
        "DOGMA (Optimize)",
        # Legacy: stripped on re-run (Apply Defaults → Setup/Update; SFX → Optimize)
        "D.O.G.M.A. Apply Defaults",
        "DOGMA Apply Defaults",
        "DOGMA (Apply Defaults)",
        "D.O.G.M.A. SFX Prefetch",
        "DOGMA SFX Prefetch",
        "DOGMA (SFX Prefetch)",
    )
)


def _mo2_arguments_for_bat(bat_name: str, mo2_root: Path) -> str:
    raw = _DOGMA_EXECUTABLE_ARGUMENTS.get(bat_name, "")
    if raw == "__MO2_ROOT__":
        # Qt ini: \ is an escape — write C:\\Instance so MO2 stores/passes C:\Instance
        # (same as workingDirectory). Bare C:\Instance becomes C:nstance.
        path = str(mo2_root.resolve()).replace("\\", "\\\\")
        return f'"{path}"' if " " in path else path
    return raw


def dogma_mo2_executable_entries(mo2_root: Path) -> list[dict[str, str]]:
    """User-facing DOGMA bats to register under MO2 Executables."""
    tools = lib.mo2_tools_dir(mo2_root) / "tools"
    wd = str(mo2_root.resolve()).replace("\\", "\\\\")

    def entry(title: str, bat_name: str) -> dict[str, str]:
        binary = (tools / bat_name).resolve()
        return {
            "title": title,
            "binary": str(binary).replace("\\", "/"),
            "workingDirectory": wd,
            "arguments": _mo2_arguments_for_bat(bat_name, mo2_root),
            "hide": "false",
            "ownicon": "false",
            "steamAppID": "",
            "toolbar": "false",
        }

    return [
        entry(title, bat)
        for title, bat in zip(DOGMA_EXECUTABLE_TITLES, _DOGMA_EXECUTABLE_BATS, strict=True)
    ]


def _custom_executables_section_bounds(lines: list[str]) -> tuple[int, int]:
    """Return (section_start, section_end_exclusive) for ``[customExecutables]``."""
    start = -1
    for i, raw in enumerate(lines):
        if raw.strip().lower() == "[customexecutables]":
            start = i
            break
    if start < 0:
        raise ValueError("ModOrganizer.ini has no [customExecutables] section")

    end = len(lines)
    for i in range(start + 1, len(lines)):
        s = lines[i].strip()
        if s.startswith("[") and s.endswith("]"):
            end = i
            break
    return start, end


def _parse_custom_executable_entries(section_body: list[str]) -> list[dict[str, str]]:
    """Parse ``N\\key=value`` rows into ordered entry dicts (1..size)."""
    size = 0
    by_index: dict[int, dict[str, str]] = {}
    key_re = re.compile(r"^(\d+)\\([A-Za-z]+)=(.*)$")
    size_re = re.compile(r"^size\s*=\s*(\d+)\s*$", re.IGNORECASE)

    for raw in section_body:
        stripped = raw.strip()
        if not stripped:
            continue
        m = size_re.match(stripped)
        if m:
            size = int(m.group(1))
            continue
        m = key_re.match(stripped)
        if not m:
            continue
        idx = int(m.group(1))
        by_index.setdefault(idx, {})[m.group(2)] = m.group(3)

    if size <= 0 and by_index:
        size = max(by_index)

    ordered: list[dict[str, str]] = []
    for idx in range(1, size + 1):
        entry = by_index.get(idx)
        if entry is None:
            continue
        ordered.append(dict(entry))
    return ordered


def _is_dogma_managed_executable(entry: dict[str, str], mo2_root: Path) -> bool:
    title = (entry.get("title") or "").strip().lower()
    if title in _DOGMA_EXECUTABLE_TITLE_ALIASES:
        return True
    binary = (entry.get("binary") or "").strip()
    if not binary:
        return False
    norm = binary.replace("\\", "/").lower()
    if "/mods/dogma/mo2/" in norm:
        return True
    try:
        bundle = lib.mo2_tools_dir(mo2_root).resolve()
        path = Path(binary)
        resolved = path.resolve() if path.is_absolute() else (mo2_root / path).resolve()
        return bundle == resolved or bundle in resolved.parents
    except OSError:
        return False


def _format_executable_block(index: int, entry: dict[str, str]) -> list[str]:
    n = str(index)
    return [
        f"{n}\\arguments={entry.get('arguments', '')}",
        f"{n}\\binary={entry.get('binary', '')}",
        f"{n}\\hide={entry.get('hide', 'false')}",
        f"{n}\\ownicon={entry.get('ownicon', 'false')}",
        f"{n}\\steamAppID={entry.get('steamAppID', '')}",
        f"{n}\\title={entry.get('title', '')}",
        f"{n}\\toolbar={entry.get('toolbar', 'false')}",
        f"{n}\\workingDirectory={entry.get('workingDirectory', '')}",
    ]


@dataclass
class RegisterExecutablesResult:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    missing_bats: list[str] = field(default_factory=list)


def register_dogma_executables(
    mo2_root: Path,
    *,
    dry_run: bool = False,
) -> RegisterExecutablesResult:
    """Replace DOGMA executable rows in ModOrganizer.ini; leave all other rows alone."""
    ini_path = mo2_root / "ModOrganizer.ini"
    if not ini_path.is_file():
        raise FileNotFoundError(f"ModOrganizer.ini not found: {ini_path}")

    text = ini_path.read_text(encoding="utf-8", errors="replace")
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()

    start, end = _custom_executables_section_bounds(lines)
    existing = _parse_custom_executable_entries(lines[start + 1 : end])
    result = RegisterExecutablesResult()

    kept: list[dict[str, str]] = []
    for entry in existing:
        if _is_dogma_managed_executable(entry, mo2_root):
            title = (entry.get("title") or entry.get("binary") or "?").strip()
            result.removed.append(title)
            lib.ok(f"{'Would remove' if dry_run else 'Remove'}: {title}")
        else:
            kept.append(entry)

    final = list(kept)
    for entry in dogma_mo2_executable_entries(mo2_root):
        title = entry["title"]
        bat = Path(entry["binary"])
        if not bat.is_file():
            result.missing_bats.append(str(bat))
            lib.warn(f"Missing bat (skip): {bat}")
            continue
        final.append(entry)
        result.added.append(title)
        lib.ok(f"{'Would add' if dry_run else 'Add'}: {title}")

    if not result.removed and not result.added:
        lib.info("No DOGMA executable changes.")
        return result

    body: list[str] = [f"size={len(final)}"]
    for i, entry in enumerate(final, start=1):
        body.extend(_format_executable_block(i, entry))

    out: list[str] = []
    out.extend(lines[: start + 1])
    out.extend(body)
    out.extend(lines[end:])

    if dry_run:
        lib.info(
            f"Dry-run: would remove {len(result.removed)}, "
            f"add {len(result.added)} -> {ini_path}"
        )
        return result

    ini_path.write_text(newline.join(out) + newline, encoding="utf-8")
    lib.ok(f"Updated {ini_path} (size={len(final)})")
    return result
