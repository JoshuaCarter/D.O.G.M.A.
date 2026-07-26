#!/usr/bin/env python3
"""Prepare MO2 for DOGMA: disabled.ini, defaults.ini, MCM keybinds, user.ltx.

Disable/defaults logic lives in src/common/mo2/dogma_mo2_lib.py (also used by
MO2 jobs). This author tool additionally scrubs keybinds and restores user.ltx.

  py -3 tools/disable_blacklisted_mods.py
  python3 tools/disable_blacklisted_mods.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_MO2_LIB = _REPO / "src" / "common" / "mo2"
if str(_MO2_LIB) not in sys.path:
    sys.path.insert(0, str(_MO2_LIB))

import dogma_mo2_lib as lib  # noqa: E402

info = lib.info
ok = lib.ok
warn = lib.warn
err = lib.err
mo2_running = lib.mo2_running
read_mo2_ini_value = lib.read_mo2_ini_value
read_disable_ini = lib.read_disable_ini
update_modlist = lib.update_modlist_disable
apply_initialize = lib.apply_initialize
read_text_lines = lib.read_text_lines
write_text_lines = lib.write_text_lines
stamp_backup = lib.stamp_backup
iter_files = lib.iter_files


def repo_root_from_script() -> Path:
    return _REPO


def game_user_ltx_path(mo2_root: Path) -> Path:
    game_path = Path(read_mo2_ini_value(mo2_root / "ModOrganizer.ini", "gamePath"))
    return game_path / "appdata" / "user.ltx"


def next_bak_path(path: Path) -> Path:
    for n in range(1, 1000):
        candidate = Path(f"{path}.bak{n:03d}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Too many backups for {path} (bak001-bak999 full)")


# ---------------------------------------------------------------------------
# keybinds (author-tool only; not part of Install+)
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Apply config/disabled.ini + defaults.ini, scrub MCM keybinds, restore user.ltx."
    )
    p.add_argument(
        "--mo2-root",
        default=r"C:\GAMMA" if sys.platform == "win32" else os.environ.get("MO2_ROOT", r"C:\GAMMA"),
        help="MO2 / GAMMA install folder (contains ModOrganizer.exe). Default: C:\\GAMMA",
    )
    p.add_argument("--disable", default="", help="Path to disabled.ini (default: <repo>/config/disabled.ini)")
    p.add_argument(
        "--initialize",
        default="",
        help="Path to defaults.ini (default: <repo>/config/defaults.ini)",
    )
    p.add_argument("--user-ltx", default="", help="Template user.ltx (default: <repo>/config/user.ltx)")
    p.add_argument("--profile", default="", help="MO2 profile name (default: selected_profile)")
    p.add_argument("--all-profiles", action="store_true", help="Apply mod disables to every profile")
    only = p.add_mutually_exclusive_group()
    only.add_argument("--disable-only", action="store_true", help="Only apply disabled.ini")
    only.add_argument("--initialize-only", action="store_true", help="Only apply defaults.ini")
    only.add_argument("--keybinds-only", action="store_true", help="Only scrub MCM keybinds")
    only.add_argument("--user-ltx-only", action="store_true", help="Only restore user.ltx")
    p.add_argument("--dry-run", action="store_true", help="Print changes; write nothing")
    p.add_argument("--force", action="store_true", help="Allow edits while ModOrganizer is running")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = repo_root_from_script()
    disable_path = Path(args.disable) if args.disable else root / "config" / "disabled.ini"
    initialize_path = (
        Path(args.initialize) if args.initialize else root / "config" / "manifest.yml"
    )
    user_ltx = Path(args.user_ltx) if args.user_ltx else root / "config" / "user.ltx"
    manifest = root / "config" / "manifest.yml"
    if not manifest.is_file():
        manifest = root / "config" / "manifest.ini"
    mo2_root = Path(args.mo2_root)

    do_disable = not (args.initialize_only or args.keybinds_only or args.user_ltx_only)
    do_initialize = not (args.disable_only or args.keybinds_only or args.user_ltx_only)
    do_keybinds = not (args.disable_only or args.initialize_only or args.user_ltx_only)
    do_user_ltx = not (args.disable_only or args.initialize_only or args.keybinds_only)

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
        if manifest.suffix.lower() in (".yml", ".yaml"):
            data = lib.load_manifest(manifest)
            installed = lib.resolve_installed_features(mo2_root, data)
            feat_rules, active, skipped = lib.feature_disable_rules(
                data, installed=installed
            )
            req_deps = lib.filter_deps(data, "downloads", installed=installed)
        else:
            data = None
            installed = None
            feat_rules, active, skipped = read_disable_ini(disable_path, manifest)
            req_deps = []
        info(f"manifest  : {manifest}")
        if active:
            info(f"  Active features ({len(active)}): {', '.join(active)}")
        if skipped:
            warn(f"  Skipped (omit / not installed) ({len(skipped)}): {', '.join(skipped)}")
        info(f"  Feature disable rules: {len(feat_rules)}")

        if not feat_rules and not req_deps:
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
                rules = list(feat_rules)
                if data is not None and req_deps:
                    rules.extend(
                        lib.gather_dep_disable_rules(mo2_root, req_deps, modlist)
                    )
                if not rules:
                    info("  No disable rules for this profile; skipping.")
                    continue
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
        info("defaults: set MCM options for present mods (from manifest.yml)")
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
