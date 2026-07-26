#!/usr/bin/env python3
"""Prepare MO2 for DOGMA: disable.ini, initialize.ini, MCM keybinds, user.ltx.

Works on Windows / macOS / Linux with Python 3 (stdlib only).
On Windows, double-click tools/disable_blacklisted_mods.bat or run:

  py -3 tools/disable_blacklisted_mods.py
  python3 tools/disable_blacklisted_mods.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator


# ---------------------------------------------------------------------------
# console
# ---------------------------------------------------------------------------

def _use_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            return False
    return sys.stdout.isatty()


_COLOR = _use_color()


def info(msg: str) -> None:
    print(msg)


def ok(msg: str) -> None:
    print(f"\033[32m{msg}\033[0m" if _COLOR else msg)


def warn(msg: str) -> None:
    print(f"\033[33m{msg}\033[0m" if _COLOR else msg)


def err(msg: str) -> None:
    print(f"\033[31m{msg}\033[0m" if _COLOR else msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# paths / mo2 helpers
# ---------------------------------------------------------------------------

def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parent.parent


def mo2_running() -> bool:
    if sys.platform == "win32":
        try:
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq ModOrganizer.exe", "/NH"],
                capture_output=True,
                text=True,
                check=False,
            )
            return "ModOrganizer.exe" in (out.stdout or "")
        except OSError:
            return False
    try:
        out = subprocess.run(
            ["pgrep", "-x", "ModOrganizer"],
            capture_output=True,
            check=False,
        )
        if out.returncode == 0:
            return True
    except OSError:
        pass
    # fallback: scan /proc names on unix
    proc = Path("/proc")
    if proc.is_dir():
        for cmd in proc.glob("*/comm"):
            try:
                if cmd.read_text(encoding="utf-8", errors="ignore").strip() == "ModOrganizer":
                    return True
            except OSError:
                continue
    return False


def read_mo2_ini_value(ini_path: Path, key: str) -> str:
    if not ini_path.is_file():
        raise FileNotFoundError(f"ModOrganizer.ini not found: {ini_path}")
    prefix = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for raw in ini_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not prefix.match(raw):
            continue
        m = re.search(r"@ByteArray\((.+)\)\s*$", raw)
        if m:
            value = m.group(1)
        else:
            value = raw.split("=", 1)[1].strip()
        # MO2 stores Windows paths with escaped backslashes.
        return value.replace("\\\\", "\\")
    raise ValueError(f"{key} not found in {ini_path}")


def game_user_ltx_path(mo2_root: Path) -> Path:
    game_path = Path(read_mo2_ini_value(mo2_root / "ModOrganizer.ini", "gamePath"))
    return game_path / "appdata" / "user.ltx"


def next_bak_path(path: Path) -> Path:
    for n in range(1, 1000):
        candidate = Path(f"{path}.bak{n:03d}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Too many backups for {path} (bak001-bak999 full)")


def stamp_backup(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = Path(f"{path}.bak.{stamp}")
    shutil.copy2(path, backup)
    return backup


def read_text_lines(path: Path) -> list[str]:
    raw = path.read_bytes()
    # strip UTF-8 BOM if present
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    text = raw.decode("utf-8", errors="replace")
    if text.endswith("\n"):
        text = text[:-1]
        if text.endswith("\r"):
            text = text[:-1]
    if not text:
        return []
    return text.splitlines()


def write_text_lines(path: Path, lines: Iterable[str]) -> None:
    data = "\r\n".join(lines) + "\r\n"
    path.write_bytes(data.encode("utf-8"))


# ---------------------------------------------------------------------------
# disable.ini / modlist
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Rule:
    kind: str  # exact | substring
    pattern: str
    features: tuple[str, ...] = ()

    def label(self) -> str:
        base = f"exact:{self.pattern}" if self.kind == "exact" else self.pattern
        if not self.features:
            return base
        return f"{base} [{', '.join(self.features)}]"


def read_manifest_levels(path: Path) -> dict[str, int]:
    """Parse config/manifest.ini [features] → {feature_path: 0|1|2}."""
    if not path.is_file():
        raise FileNotFoundError(f"Manifest not found: {path}")
    levels: dict[str, int] = {}
    section: str | None = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if section != "features" or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.split(";", 1)[0].strip()
        if not key or key == "common":
            continue
        if val not in ("0", "1", "2"):
            raise ValueError(f"Manifest value must be 0|1|2 (got {key}={val})")
        levels[key.lower()] = int(val)
    return levels


def read_disable_ini(
    path: Path,
    manifest_path: Path,
    *,
    min_level: int = 1,
) -> tuple[list[Rule], list[str], list[str]]:
    """Parse per-feature disable.ini.

    Each [feature/path] section lists MO2 mod names (one per line) that feature
    wants disabled. Only sections whose feature is >= min_level in manifest.ini
    are applied. The same MO2 mod may appear under several features; rules merge.

    Returns (rules, active_features, skipped_features).
    """
    if not path.is_file():
        raise FileNotFoundError(f"disable.ini not found: {path}")

    levels = read_manifest_levels(manifest_path)
    # feature -> list of (kind, pattern)
    by_feature: dict[str, list[tuple[str, str]]] = {}
    section: str | None = None

    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            if not section:
                raise ValueError("disable.ini has an empty [section]")
            by_feature.setdefault(section, [])
            continue
        if section is None:
            raise ValueError(f"disable.ini entry outside a [feature] section: {raw.strip()}")
        # Allow optional legacy name=1; bare name is the normal form.
        key = line.split(";", 1)[0].strip()
        if "=" in key:
            name, val = key.split("=", 1)
            name, val = name.strip(), val.strip()
            if val == "0":
                continue
            if val not in ("", "1"):
                raise ValueError(f"disable.ini entry must be a mod name (got: {raw.strip()})")
            key = name
        if not key:
            continue
        if key.lower().startswith("exact:"):
            by_feature[section].append(("exact", key.split(":", 1)[1].strip()))
        else:
            by_feature[section].append(("substring", key))

    # Merge identical patterns; keep every requesting feature.
    merged: dict[tuple[str, str], list[str]] = {}
    patterns: dict[tuple[str, str], str] = {}
    active: list[str] = []
    skipped: list[str] = []

    for feature, entries in by_feature.items():
        level = levels.get(feature.lower(), 0)
        if level < min_level:
            skipped.append(feature)
            continue
        active.append(feature)
        for kind, pattern in entries:
            key = (kind, pattern.lower())
            patterns.setdefault(key, pattern)
            merged.setdefault(key, [])
            if feature not in merged[key]:
                merged[key].append(feature)

    rules = [
        Rule(kind=kind, pattern=patterns[(kind, pat)], features=tuple(feats))
        for (kind, pat), feats in merged.items()
    ]
    return rules, active, skipped


def mod_matches(name: str, rules: Iterable[Rule]) -> Rule | None:
    lower = name.lower()
    for rule in rules:
        if rule.kind == "exact":
            if name.lower() == rule.pattern.lower():
                return rule
        elif rule.pattern.lower() in lower:
            return rule
    return None


@dataclass
class ModlistResult:
    disabled: list[str]
    already: list[str]
    unmatched: list[str]


def update_modlist(modlist_path: Path, rules: list[Rule], dry_run: bool) -> ModlistResult:
    lines_in = read_text_lines(modlist_path)
    out: list[str] = []
    disabled: list[str] = []
    already: list[str] = []
    matched: set[str] = set()

    for line in lines_in:
        m = re.match(r"^([+\-])(.+)$", line)
        if m:
            flag, name = m.group(1), m.group(2)
            rule = mod_matches(name, rules)
            if rule:
                matched.add(name)
                if flag == "+":
                    disabled.append(name)
                    out.append(f"-{name}")
                    continue
                already.append(name)
        out.append(line)

    unmatched = [r.label() for r in rules if not any(mod_matches(n, [r]) for n in matched)]

    if disabled and not dry_run:
        backup = stamp_backup(modlist_path)
        ok(f"  Backup: {backup}")
        write_text_lines(modlist_path, out)

    return ModlistResult(disabled=disabled, already=already, unmatched=unmatched)


# ---------------------------------------------------------------------------
# keybinds
# ---------------------------------------------------------------------------

_LEAF_BAD = re.compile(
    r"(?i)(mode|modifier|_mod$|style|element|icons|enabled|hijack|keypress|bg_|replace_|flag_|text_pos|bind_text)"
)
_LEAF_OK = re.compile(
    r"(?i)^(?:key(?:_.+)?|.+_key|keybind(?:_.+)?|.+_keybind|key_bind(?:_.+)?|.+_key_bind|"
    r"hotkey(?:_.+)?|.+_hotkey|bind(?:_.+)?|.+_bind|second_key)$"
)
_LTX_KEY = re.compile(r"^(\s*)([^\s=]+)\s*=\s*(-?\d{1,3})\s*(;.*)?$")
_KEY_BIND_LINE = re.compile(r"""(?i)type\s*=\s*['"]key_bind['"]""")
_DEF_ASSIGN = re.compile(r"(?i)def\s*=")
_DEF_VALUE = re.compile(r"(?i)def\s*=\s*[^,}\s]+")


def is_keybind_leaf(leaf: str) -> bool:
    if _LEAF_BAD.search(leaf):
        return False
    return bool(_LEAF_OK.match(leaf))


def update_ltx_keybinds(path: Path, dry_run: bool) -> list[str]:
    lines = read_text_lines(path)
    out: list[str] = []
    changes: list[str] = []
    for line in lines:
        m = _LTX_KEY.match(line)
        if m:
            indent, key, val_s, comment = m.group(1), m.group(2), m.group(3), m.group(4) or ""
            val = int(val_s)
            leaf = key.split("/")[-1]
            if is_keybind_leaf(leaf) and val != -1:
                out.append(f"{indent}{key} = -1{comment}")
                changes.append(f"{key} ({val} -> -1)")
                continue
        out.append(line)
    if changes and not dry_run:
        stamp_backup(path)
        write_text_lines(path, out)
    return changes


def update_script_keybind_defaults(path: Path, dry_run: bool) -> list[str]:
    lines = read_text_lines(path)
    out: list[str] = []
    changes: list[str] = []
    for line in lines:
        if _KEY_BIND_LINE.search(line) and _DEF_ASSIGN.search(line):
            new = _DEF_VALUE.sub("def = -1", line)
            if new != line:
                out.append(new)
                trim = line.strip()
                if len(trim) > 100:
                    trim = trim[:97] + "..."
                changes.append(trim)
                continue
        out.append(line)
    if changes and not dry_run:
        stamp_backup(path)
        write_text_lines(path, out)
    return changes


def iter_files(root: Path, name: str | None = None, suffix: str | None = None) -> Iterator[Path]:
    if not root.is_dir():
        return
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            if name is not None and fn != name:
                continue
            if suffix is not None and not fn.endswith(suffix):
                continue
            yield Path(dirpath) / fn


def reset_mod_keybinds(mo2_root: Path, dry_run: bool) -> tuple[int, int]:
    scan_roots = [p for p in (mo2_root / "mods", mo2_root / "overwrite") if p.is_dir()]
    if not scan_roots:
        raise FileNotFoundError(f"No mods/ or overwrite/ under {mo2_root}")

    file_hits = 0
    value_hits = 0

    info("Scanning axr_options.ltx for keybind values...")
    for scan in scan_roots:
        for path in iter_files(scan, name="axr_options.ltx"):
            changes = update_ltx_keybinds(path, dry_run)
            if not changes:
                continue
            file_hits += 1
            value_hits += len(changes)
            rel = path.relative_to(mo2_root).as_posix()
            ok(f"  {rel} ({len(changes)})")
            for c in changes:
                info(f"    {c}")

    info("Scanning .script for key_bind defaults...")
    for scan in scan_roots:
        for path in iter_files(scan, suffix=".script"):
            changes = update_script_keybind_defaults(path, dry_run)
            if not changes:
                continue
            file_hits += 1
            value_hits += len(changes)
            rel = path.relative_to(mo2_root).as_posix()
            ok(f"  {rel} ({len(changes)})")
            for i, c in enumerate(changes):
                if i < 5:
                    info(f"    {c}")
            if len(changes) > 5:
                info(f"    ... +{len(changes) - 5} more")

    return file_hits, value_hits


# ---------------------------------------------------------------------------
# initialize.ini → axr_options.ltx
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InitSetting:
    mod_pattern: str
    axr_section: str
    key: str
    value: str


def read_initialize_ini(path: Path) -> dict[str, list[InitSetting]]:
    """Parse initialize.ini → {mod_pattern: [settings...]}."""
    if not path.is_file():
        raise FileNotFoundError(f"initialize.ini not found: {path}")

    by_mod: dict[str, list[InitSetting]] = {}
    section: str | None = None

    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            if not section:
                raise ValueError("initialize.ini has an empty [section]")
            by_mod.setdefault(section, [])
            continue
        if section is None:
            raise ValueError(f"initialize.ini entry outside a [mod] section: {raw.strip()}")
        if "=" not in line:
            raise ValueError(f"initialize.ini entry must be key = value (got: {raw.strip()})")
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.split(";", 1)[0].strip()
        if not key:
            raise ValueError(f"initialize.ini entry missing key: {raw.strip()}")
        axr_section = "mcm"
        if key.startswith("@"):
            rest = key[1:]
            if "/" not in rest:
                raise ValueError(
                    f"initialize.ini @entry must be @Section/key (got: {raw.strip()})"
                )
            axr_section, key = rest.split("/", 1)
            axr_section, key = axr_section.strip(), key.strip()
            if not axr_section or not key:
                raise ValueError(f"initialize.ini bad @Section/key: {raw.strip()}")
        by_mod[section].append(InitSetting(section, axr_section, key, val))
    return by_mod


def list_modlist_names(modlist_path: Path) -> list[str]:
    names: list[str] = []
    for line in read_text_lines(modlist_path):
        m = re.match(r"^[+\-](.+)$", line)
        if m:
            names.append(m.group(1))
    return names


def find_present_mod(pattern: str, mod_names: Iterable[str]) -> str | None:
    """Return first modlist name matching pattern (substring or exact:)."""
    if pattern.lower().startswith("exact:"):
        want = pattern.split(":", 1)[1].strip().lower()
        for name in mod_names:
            if name.lower() == want:
                return name
        return None
    needle = pattern.lower()
    for name in mod_names:
        if needle in name.lower():
            return name
    return None


_AXR_ASSIGN = re.compile(r"^(\s*)([^\s=]+)\s*=\s*(.*?)\s*$")


def format_axr_line(indent: str, key: str, value: str, width: int = 40) -> str:
    pad = max(width, len(key) + 1)
    return f"{indent}{key:<{pad}} = {value}"


def apply_settings_to_axr_options(
    path: Path,
    settings: list[InitSetting],
    dry_run: bool,
) -> list[str]:
    """Apply settings to one axr_options.ltx. Returns human-readable change lines."""
    if not settings:
        return []

    lines = read_text_lines(path)
    changes: list[str] = []

    # Group by axr section
    by_section: dict[str, list[InitSetting]] = {}
    for s in settings:
        by_section.setdefault(s.axr_section, []).append(s)

    for axr_section, sect_settings in by_section.items():
        header = f"[{axr_section}]"
        # Find section range [start, end)
        start = None
        for i, line in enumerate(lines):
            if line.strip().lower() == header.lower():
                start = i
                break
        if start is None:
            # Append new section at end
            if dry_run:
                for s in sect_settings:
                    changes.append(f"[{axr_section}] {s.key} = {s.value} (new section)")
                continue
            if lines and lines[-1].strip() != "":
                lines.append("")
            lines.append(header)
            start = len(lines) - 1
            for s in sect_settings:
                lines.append(format_axr_line("        ", s.key, s.value))
                changes.append(f"[{axr_section}] {s.key} = {s.value} (added)")
            continue

        end = len(lines)
        for j in range(start + 1, len(lines)):
            if lines[j].strip().startswith("[") and lines[j].strip().endswith("]"):
                end = j
                break

        # Index existing keys in section
        key_at: dict[str, int] = {}
        indent = "        "
        for i in range(start + 1, end):
            m = _AXR_ASSIGN.match(lines[i])
            if not m:
                continue
            indent = m.group(1) or indent
            key_at[m.group(2).lower()] = i

        for s in sect_settings:
            idx = key_at.get(s.key.lower())
            if idx is None:
                changes.append(f"[{axr_section}] {s.key} = {s.value} (added)")
                if not dry_run:
                    lines.insert(end, format_axr_line(indent, s.key, s.value))
                    end += 1
                    # refresh not needed for subsequent inserts at end
                continue
            m = _AXR_ASSIGN.match(lines[idx])
            assert m is not None
            old = m.group(3)
            if old == s.value:
                continue
            changes.append(f"[{axr_section}] {s.key}: {old} -> {s.value}")
            if not dry_run:
                lines[idx] = format_axr_line(m.group(1), m.group(2), s.value)

    if changes and not dry_run:
        stamp_backup(path)
        write_text_lines(path, lines)
    return changes


def apply_initialize(
    mo2_root: Path,
    initialize_path: Path,
    modlist_path: Path,
    dry_run: bool,
) -> tuple[int, int, list[str]]:
    """Apply initialize.ini against present mods. Returns (files, values, skipped_mods)."""
    by_mod = read_initialize_ini(initialize_path)
    if not by_mod:
        return 0, 0, []

    mod_names = list_modlist_names(modlist_path)
    to_apply: list[InitSetting] = []
    skipped: list[str] = []
    matched_mods: list[str] = []

    for pattern, settings in by_mod.items():
        if not settings:
            continue
        hit = find_present_mod(pattern, mod_names)
        if not hit:
            skipped.append(pattern)
            continue
        matched_mods.append(f"{pattern} -> {hit}")
        to_apply.extend(settings)

    if matched_mods:
        info(f"  Present mods ({len(matched_mods)}):")
        for m in matched_mods:
            info(f"    {m}")

    if not to_apply:
        return 0, 0, skipped

    file_hits = 0
    value_hits = 0
    scan_roots = [p for p in (mo2_root / "mods", mo2_root / "overwrite") if p.is_dir()]
    for scan in scan_roots:
        for path in iter_files(scan, name="axr_options.ltx"):
            changes = apply_settings_to_axr_options(path, to_apply, dry_run)
            if not changes:
                continue
            file_hits += 1
            value_hits += len(changes)
            rel = path.relative_to(mo2_root).as_posix()
            ok(f"  {rel} ({len(changes)})")
            for c in changes[:12]:
                info(f"    {c}")
            if len(changes) > 12:
                info(f"    ... +{len(changes) - 12} more")

    return file_hits, value_hits, skipped


# ---------------------------------------------------------------------------
# user.ltx
# ---------------------------------------------------------------------------

def restore_user_ltx(template: Path, dest: Path, dry_run: bool) -> Path | None:
    if not template.is_file():
        raise FileNotFoundError(f"Template user.ltx not found: {template}")
    if not dest.parent.is_dir():
        raise FileNotFoundError(f"Game appdata folder not found: {dest.parent}")

    backup: Path | None = None
    if dest.is_file():
        backup = next_bak_path(dest)
        if dry_run:
            ok(f"  Would rename existing -> {backup.name}")
        else:
            dest.rename(backup)
            ok(f"  Backup: {backup}")
    else:
        info("  No existing user.ltx to back up.")

    if dry_run:
        ok(f"  Would copy template -> {dest}")
    else:
        shutil.copy2(template, dest)
        ok(f"  Restored: {dest}")
    return backup


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Apply config/disable.ini + initialize.ini, scrub MCM keybinds, restore user.ltx."
    )
    p.add_argument(
        "--mo2-root",
        default=r"C:\GAMMA" if sys.platform == "win32" else os.environ.get("MO2_ROOT", r"C:\GAMMA"),
        help="MO2 / GAMMA install folder (contains ModOrganizer.exe). Default: C:\\GAMMA",
    )
    p.add_argument("--disable", default="", help="Path to disable.ini (default: <repo>/config/disable.ini)")
    p.add_argument(
        "--initialize",
        default="",
        help="Path to initialize.ini (default: <repo>/config/initialize.ini)",
    )
    p.add_argument("--user-ltx", default="", help="Template user.ltx (default: <repo>/config/user.ltx)")
    p.add_argument("--profile", default="", help="MO2 profile name (default: selected_profile)")
    p.add_argument("--all-profiles", action="store_true", help="Apply mod disables to every profile")
    only = p.add_mutually_exclusive_group()
    only.add_argument("--disable-only", action="store_true", help="Only apply disable.ini")
    only.add_argument("--initialize-only", action="store_true", help="Only apply initialize.ini")
    only.add_argument("--keybinds-only", action="store_true", help="Only scrub MCM keybinds")
    only.add_argument("--user-ltx-only", action="store_true", help="Only restore user.ltx")
    p.add_argument("--dry-run", action="store_true", help="Print changes; write nothing")
    p.add_argument("--force", action="store_true", help="Allow edits while ModOrganizer is running")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = repo_root_from_script()
    disable_path = Path(args.disable) if args.disable else root / "config" / "disable.ini"
    initialize_path = (
        Path(args.initialize) if args.initialize else root / "config" / "initialize.ini"
    )
    user_ltx = Path(args.user_ltx) if args.user_ltx else root / "config" / "user.ltx"
    manifest = root / "config" / "manifest.ini"
    mo2_root = Path(args.mo2_root)

    only = (
        args.disable_only,
        args.initialize_only,
        args.keybinds_only,
        args.user_ltx_only,
    )
    do_disable = not (args.initialize_only or args.keybinds_only or args.user_ltx_only)
    do_initialize = not (args.disable_only or args.keybinds_only or args.user_ltx_only)
    do_keybinds = not (args.disable_only or args.initialize_only or args.user_ltx_only)
    do_user_ltx = not (args.disable_only or args.initialize_only or args.keybinds_only)
    if sum(1 for x in only if x) > 1:
        # argparse mutually_exclusive_group already prevents this
        pass

    exe = mo2_root / "ModOrganizer.exe"
    if not exe.is_file():
        err(f"ModOrganizer.exe not found under: {mo2_root}")
        err("Pass --mo2-root path/to/GAMMA")
        return 1

    if mo2_running() and not args.dry_run and not args.force:
        err("ModOrganizer.exe is running. Close MO2, then run again.")
        warn("MO2 keeps the mod list in memory and will overwrite file edits on exit.")
        warn("Preview safely with --dry-run, or override with --force.")
        return 1

    if mo2_running() and args.force and not args.dry_run:
        warn("WARNING: ModOrganizer.exe is running (--force). Edits may be lost when MO2 exits.")

    info(f"MO2 root : {mo2_root}")
    info(f"Repo root: {root}")
    if args.dry_run:
        warn("Dry run - no files will be written.")

    any_change = False
    profiles_dir = mo2_root / "profiles"

    def selected_profiles() -> list[str]:
        if args.all_profiles:
            profiles = sorted(p.name for p in profiles_dir.iterdir() if p.is_dir())
            if not profiles:
                raise FileNotFoundError(f"No profiles under {profiles_dir}")
            return profiles
        profile = args.profile or read_mo2_ini_value(
            mo2_root / "ModOrganizer.ini", "selected_profile"
        )
        return [profile]

    if do_disable:
        rules, active, skipped = read_disable_ini(disable_path, manifest)
        info(f"disable.ini: {disable_path}")
        info(f"Manifest   : {manifest}")
        if active:
            info(f"  Active features ({len(active)}): {', '.join(active)}")
        if skipped:
            warn(f"  Skipped (manifest off / missing) ({len(skipped)}): {', '.join(skipped)}")
        info(f"  Disable rules: {len(rules)}")

        if not rules:
            info("  No disable entries for active features; skipping modlist edits.")
        else:
            for profile in selected_profiles():
                info("")
                info(f"Profile: {profile}")
                modlist = profiles_dir / profile / "modlist.txt"
                if not modlist.is_file():
                    raise FileNotFoundError(
                        f"modlist.txt not found for profile '{profile}': {modlist}"
                    )
                result = update_modlist(modlist, rules, args.dry_run)

                if result.disabled:
                    any_change = True
                    verb = "Would disable" if args.dry_run else "Disabled"
                    ok(f"  {verb} ({len(result.disabled)}):")
                    for name in sorted(result.disabled):
                        ok(f"    - {name}")
                else:
                    info("  Nothing newly disabled.")

                if result.already:
                    info(f"  Already disabled ({len(result.already)}):")
                    for name in sorted(result.already):
                        info(f"    - {name}")

                if result.unmatched:
                    warn(
                        f"  No mod matched these disable entries ({len(result.unmatched)}):"
                    )
                    for label in result.unmatched:
                        warn(f"    - {label}")

    if do_initialize:
        info("")
        info("initialize.ini: set MCM options for present mods")
        info(f"  Config: {initialize_path}")
        for profile in selected_profiles():
            info(f"  Profile: {profile}")
            modlist = profiles_dir / profile / "modlist.txt"
            if not modlist.is_file():
                raise FileNotFoundError(
                    f"modlist.txt not found for profile '{profile}': {modlist}"
                )
            files, values, skipped_mods = apply_initialize(
                mo2_root, initialize_path, modlist, args.dry_run
            )
            if skipped_mods:
                warn(f"  Mods not present ({len(skipped_mods)}): {', '.join(skipped_mods)}")
            if values:
                any_change = True
                verb = "Would change" if args.dry_run else "Changed"
                ok(f"  {verb} {values} setting(s) across {files} axr_options file(s).")
            else:
                info("  No initialize settings needed changing.")

    if do_keybinds:
        info("")
        info("Keybinds: set MCM binds to -1 (unbound)")
        files, values = reset_mod_keybinds(mo2_root, args.dry_run)
        if values:
            any_change = True
            verb = "Would change" if args.dry_run else "Changed"
            ok(f"{verb} {values} keybind value(s) across {files} file(s).")
        else:
            info("No keybind values needed changing.")

    if do_user_ltx:
        info("")
        info("user.ltx: restore template over game appdata")
        dest = game_user_ltx_path(mo2_root)
        info(f"  Template: {user_ltx}")
        info(f"  Dest    : {dest}")
        restore_user_ltx(user_ltx, dest, args.dry_run)
        any_change = True

    info("")
    if args.dry_run:
        warn("Dry run complete.")
    elif any_change:
        ok("Done. Open MO2 / MCM to confirm.")
    else:
        info("Done. Nothing to change.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError, RuntimeError, OSError) as exc:
        err(str(exc))
        raise SystemExit(1) from exc
