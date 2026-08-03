#!/usr/bin/env python3
"""DOGMA Backup / Restore - MCM diff, user.ltx, modlist, mods archive."""

from __future__ import annotations

import difflib
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import dogma_mo2_lib as lib

BACKUP_DIR_NAME = "backups"
MCM_DIFF_NAME = "dogma_mcm_diff.yml"
USER_LTX_NAME = "user.ltx"
MODLIST_NAME = "modlist.txt"
MODS_ARCHIVE_NAME = "mods.7z"
META_NAME = "backup.yml"

COMPONENTS = ("mcm", "user_ltx", "modlist", "mods")


@dataclass
class BackupComponents:
    mcm: bool = False
    user_ltx: bool = False
    modlist: bool = False
    mods: bool = False

    def any(self) -> bool:
        return self.mcm or self.user_ltx or self.modlist or self.mods

    def as_dict(self) -> dict[str, bool]:
        return {
            "mcm": self.mcm,
            "user_ltx": self.user_ltx,
            "modlist": self.modlist,
            "mods": self.mods,
        }

    @classmethod
    def all(cls) -> BackupComponents:
        return cls(mcm=True, user_ltx=True, modlist=True, mods=True)

    @classmethod
    def config_only(cls) -> BackupComponents:
        """MCM + user.ltx + modlist (no mods archive) - for install safety nets."""
        return cls(mcm=True, user_ltx=True, modlist=True, mods=False)

    @classmethod
    def from_meta(cls, raw: dict | None) -> BackupComponents:
        c = (raw or {}).get("components") or {}
        return cls(
            mcm=bool(c.get("mcm")),
            user_ltx=bool(c.get("user_ltx")),
            modlist=bool(c.get("modlist")),
            mods=bool(c.get("mods")),
        )


def _yn(prompt: str, default_yes: bool = True) -> bool:
    suffix = " [Y/n] " if default_yes else " [y/N] "
    try:
        raw = input(prompt + suffix).strip().lower()
    except EOFError:
        return default_yes
    if not raw:
        return default_yes
    return raw in ("y", "yes")


def _fmt_bytes(n: int) -> str:
    x = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if x < 1024.0 or unit == "TB":
            return f"{x:.1f} {unit}" if unit != "B" else f"{int(x)} B"
        x /= 1024.0
    return f"{n} B"


def dir_tree_size(root: Path) -> int:
    total = 0
    if not root.is_dir():
        return 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                pass
    return total


def backup_root(mo2_root: Path) -> Path:
    """``<MO2>\\DOGMA\\backups`` (outside mods/ so restore can wipe mods)."""
    root = lib.dogma_data_dir(mo2_root)
    dest = root / BACKUP_DIR_NAME
    # One-time rename from the older folder name.
    legacy = root / "config_backups"
    if legacy.is_dir() and not dest.exists():
        try:
            legacy.rename(dest)
        except OSError:
            pass
    return dest


def stamp_now() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def game_user_ltx_path(mo2_root: Path) -> Path:
    return lib.game_dir(mo2_root) / "appdata" / "user.ltx"


def live_axr_options_path(mo2_root: Path) -> Path | None:
    """Prefer overwrite, else installed G.A.M.M.A. MCM values mod."""
    overwrite = mo2_root / "overwrite" / "gamedata" / "configs" / "axr_options.ltx"
    if overwrite.is_file():
        return overwrite
    mods = mo2_root / "mods"
    if not mods.is_dir():
        return None
    for d in sorted(mods.iterdir(), key=lambda p: p.name.lower()):
        if not d.is_dir() or "mcm values" not in d.name.lower():
            continue
        cand = d / "gamedata" / "configs" / "axr_options.ltx"
        if cand.is_file():
            return cand
    # Last resort: any axr_options under mods
    for path in sorted(mods.rglob("axr_options.ltx")):
        return path
    return None


def restore_axr_options_path(mo2_root: Path) -> Path:
    """Write restored MCM into overwrite (does not require wiping MCM values mod)."""
    return mo2_root / "overwrite" / "gamedata" / "configs" / "axr_options.ltx"


def find_pristine_gamma_axr(mo2_root: Path) -> Path | None:
    """Shipped G.A.M.M.A. MCM values from Grok installer (gamma defaults)."""
    roots = (
        mo2_root / ".Grok's Modpack Installer" / "G.A.M.M.A" / "modpack_addons",
        mo2_root
        / ".Grok's Modpack Installer"
        / "resources"
        / "Stalker_GAMMA"
        / "G.A.M.M.A"
        / "modpack_addons",
    )
    for addons in roots:
        if not addons.is_dir():
            continue
        for d in addons.iterdir():
            if not d.is_dir() or "mcm values" not in d.name.lower():
                continue
            cand = d / "gamedata" / "configs" / "axr_options.ltx"
            if cand.is_file():
                return cand
    return None


def parse_axr_section(path: Path, section: str = "mcm") -> dict[str, str]:
    want = f"[{section}]".lower()
    out: dict[str, str] = {}
    in_section = False
    for line in lib.read_text_lines(path):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            in_section = s.lower() == want
            continue
        if not in_section or not s or s.startswith(";"):
            continue
        if "=" not in s:
            continue
        key, val = s.split("=", 1)
        out[key.strip()] = val.strip()
    return out


def _normalize_val(raw: str) -> str:
    raw = (raw or "").strip()
    if raw.lower() in ("true", "false"):
        return raw.lower()
    return raw


def _enabled_mods_low_to_high(mo2_root: Path) -> list[str] | None:
    """Enabled MO2 mods from lowest to highest priority (last wins).

    ``modlist.txt`` lists highest priority first (top of left pane).
    """
    try:
        modlist = lib.modlist_path(mo2_root)
    except FileNotFoundError:
        return None
    high_first = [n for f, n in lib.list_modlist_entries(modlist) if f == "+"]
    return list(reversed(high_first))


def build_mcm_baseline(mo2_root: Path) -> dict[str, str]:
    """Effective MCM defaults: pristine G.A.M.M.A. + mod script defs (load order).

    1. Start from installer G.A.M.M.A. MCM values ``axr_options``.
    2. Overlay enabled mods' script ``def=`` / ``defaults = {…}`` low→high
       priority so later (higher) mods win.
    """
    baseline: dict[str, str] = {}
    pristine = find_pristine_gamma_axr(mo2_root)
    if pristine is not None and pristine.is_file():
        baseline.update(parse_axr_section(pristine, "mcm"))
        lib.info(
            f"MCM baseline: pristine G.A.M.M.A. ({len(baseline)} key(s) from {pristine})"
        )
    else:
        lib.warn("MCM baseline: no pristine G.A.M.M.A. axr_options found")

    mod_order = _enabled_mods_low_to_high(mo2_root)
    if mod_order is None:
        lib.warn("MCM baseline: no modlist - indexing all mods (unordered)")
        script_defs = lib.index_mcm_script_defaults(mo2_root)
    else:
        script_defs = lib.index_mcm_script_defaults(mo2_root, mod_names=mod_order)
        lib.info(
            f"MCM baseline: overlay script defaults from {len(mod_order)} "
            f"enabled mod(s) (load order, last wins) -> {len(script_defs)} key(s)"
        )
    baseline.update(script_defs)
    return baseline


def _lookup_baseline(key: str, baseline: dict[str, str]) -> str | None:
    return lib.lookup_mcm_script_default(key, baseline)


def _collect_root_to_mod(mo2_root: Path) -> dict[str, str]:
    """Map MCM root id -> MO2 mod folder name (from *mcm*.script paths)."""
    root_to_mod: dict[str, str] = {}
    mods = mo2_root / "mods"
    if not mods.is_dir():
        return root_to_mod
    for script in mods.rglob("*.script"):
        if not lib._is_mcm_script_name(script.name):  # noqa: SLF001
            continue
        try:
            text = script.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        mod_folder = script.relative_to(mods).parts[0]
        root = None
        m = lib._ROOT_ID.search(text)  # noqa: SLF001
        if m:
            root = m.group(1)
        else:
            m2 = lib._SIMPLE_ROOT.search(text)  # noqa: SLF001
            if m2:
                root = m2.group(1)
        if root:
            root_to_mod.setdefault(root, mod_folder)
    return root_to_mod


def _section_for_key(key: str, root_to_mod: dict[str, str]) -> str:
    prefix = key.split("/", 1)[0]
    return root_to_mod.get(prefix, prefix)


def collect_mcm_diff(mo2_root: Path) -> dict[str, dict[str, str]]:
    """Non-default MCM keys grouped by MO2 mod name.

    Baseline is pristine G.A.M.M.A. MCM values overlaid with enabled mods'
    script defaults in MO2 load order (last wins). Keys with no known
    baseline are skipped.
    """
    axr = live_axr_options_path(mo2_root)
    if axr is None or not axr.is_file():
        lib.warn("No axr_options.ltx found - MCM diff will be empty.")
        return {}

    current = parse_axr_section(axr, "mcm")
    baseline = build_mcm_baseline(mo2_root)
    root_to_mod = _collect_root_to_mod(mo2_root)

    groups: dict[str, dict[str, str]] = {}
    skipped_unknown = 0
    skipped_default = 0
    for key, val in current.items():
        expected = _lookup_baseline(key, baseline)
        if expected is None:
            skipped_unknown += 1
            continue
        if _normalize_val(val) == _normalize_val(expected):
            skipped_default += 1
            continue
        section = _section_for_key(key, root_to_mod)
        groups.setdefault(section, {})[key] = val

    lib.info(
        f"MCM diff: {sum(len(v) for v in groups.values())} key(s) "
        f"across {len(groups)} mod(s) (from {axr.name}); "
        f"skipped default={skipped_default} unknown={skipped_unknown}"
    )
    return groups


def format_mcm_diff_yaml(groups: dict[str, dict[str, str]]) -> str:
    """Serialize as::

        GAMMA:
          Mod Name:
            - key: value
    """
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML required - run DOGMA Setup once") from exc

    payload: dict[str, dict[str, list[dict[str, str]]]] = {"GAMMA": {}}
    for mod_name in sorted(groups.keys(), key=str.lower):
        pairs = groups[mod_name]
        payload["GAMMA"][mod_name] = [
            {k: v} for k, v in sorted(pairs.items(), key=lambda kv: kv[0].lower())
        ]
    return yaml.safe_dump(
        payload,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )


def load_mcm_diff_yaml(path: Path) -> list[lib.InitSetting]:
    """Parse dogma_mcm_diff.yml -> InitSetting list for [mcm]."""
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML required - run DOGMA Setup once") from exc

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid MCM diff (not a mapping): {path}")

    # Accept {GAMMA: {Mod: [...]}} or flat {Mod: [...]}
    body = raw.get("GAMMA", raw)
    if not isinstance(body, dict):
        raise ValueError(f"Invalid MCM diff body: {path}")

    out: list[lib.InitSetting] = []
    for mod_name, entries in body.items():
        if isinstance(entries, dict):
            items = entries.items()
        elif isinstance(entries, list):
            merged: dict[str, str] = {}
            for item in entries:
                if isinstance(item, dict):
                    merged.update({str(k): str(v) for k, v in item.items()})
            items = merged.items()
        else:
            continue
        for key, val in items:
            out.append(lib.InitSetting(str(mod_name), "mcm", str(key), str(val)))
    return out


def write_meta(
    dest_dir: Path,
    *,
    mo2_root: Path,
    profile: str,
    components: BackupComponents,
    extra: dict | None = None,
) -> None:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML required - run DOGMA Setup once") from exc

    data = {
        "created": dest_dir.name,
        "mo2_root": str(mo2_root.resolve()),
        "profile": profile,
        "components": components.as_dict(),
    }
    if extra:
        data.update(extra)
    (dest_dir / META_NAME).write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
        newline="\n",
    )


def read_meta(dest_dir: Path) -> dict:
    path = dest_dir / META_NAME
    if not path.is_file():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {}


def list_backups(mo2_root: Path) -> list[Path]:
    root = backup_root(mo2_root)
    if not root.is_dir():
        return []
    dirs = [p for p in root.iterdir() if p.is_dir()]
    # Newest first (stamp sorts lexicographically).
    return sorted(dirs, key=lambda p: p.name, reverse=True)


def prompt_components(
    *,
    default: BackupComponents | None = None,
    allow_mods: bool = True,
    label: str = "backup",
) -> BackupComponents:
    d = default or BackupComponents.all()
    print()
    print(f"What should this {label} include?")
    mcm = _yn("  MCM options (diff from defaults)", default_yes=d.mcm)
    user_ltx = _yn("  user.ltx", default_yes=d.user_ltx)
    modlist = _yn("  MO2 mod list (enabled/disabled + order)", default_yes=d.modlist)
    mods = False
    if allow_mods:
        print()
        print("  ! Mods archive can be large and take a long time.")
        mods = _yn("  Full mods/ folder archive", default_yes=d.mods)
    return BackupComponents(mcm=mcm, user_ltx=user_ltx, modlist=modlist, mods=mods)


def components_from_args(args, *, default_all: bool = False) -> BackupComponents | None:
    """Return forced components from CLI, or None to prompt.

    If any --no-* / --* component flag is present, non-interactive selection
    is built from flags (missing yes-flags default False unless --all).
    """
    if getattr(args, "all", False) or default_all:
        return BackupComponents.all()

    flags = (
        ("mcm", "no_mcm"),
        ("user_ltx", "no_user_ltx"),
        ("modlist", "no_modlist"),
        ("mods", "no_mods"),
    )
    any_set = False
    for yes_a, no_a in flags:
        if getattr(args, yes_a, False) or getattr(args, no_a, False):
            any_set = True
            break
    if not any_set:
        return None

    def _pick(yes_a: str, no_a: str) -> bool:
        if getattr(args, no_a, False):
            return False
        if getattr(args, yes_a, False):
            return True
        return False

    return BackupComponents(
        mcm=_pick("mcm", "no_mcm"),
        user_ltx=_pick("user_ltx", "no_user_ltx"),
        modlist=_pick("modlist", "no_modlist"),
        mods=_pick("mods", "no_mods"),
    )


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


def step_backup_mcm(mo2_root: Path, dest_dir: Path, *, dry_run: bool) -> int:
    groups = collect_mcm_diff(mo2_root)
    text = format_mcm_diff_yaml(groups)
    out = dest_dir / MCM_DIFF_NAME
    if dry_run:
        lib.info(f"Would write MCM diff ({sum(len(v) for v in groups.values())} keys):\n  {out}")
        return 0
    out.write_text(text, encoding="utf-8", newline="\n")
    lib.ok(f"MCM diff saved:\n  {out}")
    return 0


def step_backup_user_ltx(mo2_root: Path, dest_dir: Path, *, dry_run: bool) -> int:
    src = game_user_ltx_path(mo2_root)
    dest = dest_dir / USER_LTX_NAME
    if not src.is_file():
        lib.warn(f"No user.ltx at:\n  {src}")
        return 0
    if dry_run:
        lib.info(f"Would copy user.ltx ->\n  {dest}")
        return 0
    shutil.copy2(src, dest)
    lib.ok(f"user.ltx saved:\n  {dest}")
    return 0


def step_backup_modlist(
    mo2_root: Path,
    dest_dir: Path,
    *,
    profile: str,
    dry_run: bool,
) -> int:
    src = lib.modlist_path(mo2_root, profile)
    dest = dest_dir / MODLIST_NAME
    if dry_run:
        lib.info(f"Would copy modlist ->\n  {dest}")
        return 0
    shutil.copy2(src, dest)
    # Also drop profile name for restore.
    (dest_dir / "profile.txt").write_text(src.parent.name + "\n", encoding="utf-8")
    lib.ok(f"Mod list saved:\n  {dest}")
    return 0


def step_backup_mods(mo2_root: Path, dest_dir: Path, *, dry_run: bool) -> int:
    mods = mo2_root / "mods"
    if not mods.is_dir():
        lib.err(f"Could not find mods folder:\n  {mods}")
        return 1

    archive = dest_dir / MODS_ARCHIVE_NAME
    seven = lib.find_7z()
    if seven is None:
        lib.err(
            "7-Zip is required for mods backup, but was not found.\n"
            "Install 7-Zip, or use the copy that ships with G.A.M.M.A. "
            r"(...\.Grok's Modpack Installer\7zip\7z.exe)."
        )
        return 1

    lib.info("Checking how large your mods folder is...")
    mods_size = dir_tree_size(mods)
    need = mods_size // 2
    try:
        free = shutil.disk_usage(
            str(dest_dir if dest_dir.exists() else dest_dir.parent)
        ).free
    except OSError as exc:
        lib.err(f"Could not check free disk space ({exc}).")
        return 1

    lib.info(
        f"Mods folder: {_fmt_bytes(mods_size)}  ·  "
        f"space needed (≈50%): {_fmt_bytes(need)}  ·  "
        f"free: {_fmt_bytes(free)}"
    )
    if free < need:
        lib.err(
            f"Not enough free disk space to back up mods safely.\n"
            f"  Need about {_fmt_bytes(need)}, but only {_fmt_bytes(free)} is free."
        )
        return 1

    if dry_run:
        lib.info(f"Dry run - would create:\n  {archive}")
        return 0

    dest_dir.mkdir(parents=True, exist_ok=True)
    if archive.is_file():
        archive.unlink()

    lib.info(
        "Creating mods archive... this can take a while. Leave this window open."
    )
    # Archive folder contents as ``mods/...`` for predictable extract.
    cmd = [
        str(seven),
        "a",
        "-t7z",
        "-mx=5",
        "-mmt=on",
        str(archive),
        str(mods),
    ]
    code = subprocess.call(cmd)
    if code:
        lib.err("Mods archive failed.")
        return int(code)
    lib.ok(f"Mods archive saved:\n  {archive}")
    return 0


def run_backup(
    mo2_root: Path,
    components: BackupComponents,
    *,
    profile: str = "",
    dry_run: bool = False,
    stamp: str | None = None,
) -> tuple[int, Path | None]:
    """Create a timestamped backup. Returns (exit_code, backup_dir|None)."""
    if not components.any():
        lib.err("Nothing selected to back up.")
        return 1, None

    mo2_root = Path(mo2_root).resolve()
    profile_name = lib.selected_profile(mo2_root, profile)
    root = backup_root(mo2_root)
    dest = root / (stamp or stamp_now())

    print()
    lib.info(f"Backup folder:\n  {dest}")
    lib.info(
        "Components: "
        + ", ".join(k for k, v in components.as_dict().items() if v)
    )

    if dry_run:
        lib.info("Dry run - folder will not be created.")
    else:
        dest.mkdir(parents=True, exist_ok=True)

    if components.mcm:
        code = step_backup_mcm(mo2_root, dest, dry_run=dry_run)
        if code:
            return code, dest if not dry_run else None
    if components.user_ltx:
        code = step_backup_user_ltx(mo2_root, dest, dry_run=dry_run)
        if code:
            return code, dest if not dry_run else None
    if components.modlist:
        code = step_backup_modlist(
            mo2_root, dest, profile=profile_name, dry_run=dry_run
        )
        if code:
            return code, dest if not dry_run else None
    if components.mods:
        code = step_backup_mods(mo2_root, dest, dry_run=dry_run)
        if code:
            return code, dest if not dry_run else None

    if not dry_run:
        write_meta(
            dest,
            mo2_root=mo2_root,
            profile=profile_name,
            components=components,
            extra={
                "axr_options": str(live_axr_options_path(mo2_root) or ""),
                "user_ltx": str(game_user_ltx_path(mo2_root)),
            },
        )
        lib.ok(f"Backup complete:\n  {dest}")
    return 0, None if dry_run else dest


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def _diff_user_ltx(before: str, after: str) -> list[str]:
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    return list(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile="user.ltx (before)",
            tofile="user.ltx (restored)",
            lineterm="",
        )
    )


def step_restore_mcm(mo2_root: Path, src_dir: Path, *, dry_run: bool) -> int:
    diff_path = src_dir / MCM_DIFF_NAME
    if not diff_path.is_file():
        lib.err(f"Missing {MCM_DIFF_NAME} in backup:\n  {src_dir}")
        return 1

    settings = load_mcm_diff_yaml(diff_path)
    if not settings:
        lib.info("MCM diff is empty - nothing to apply.")
        return 0

    dest = restore_axr_options_path(mo2_root)
    live = live_axr_options_path(mo2_root)
    if not dest.is_file():
        # Seed from live MCM values if present so we don't write a tiny fragment-only file.
        if live is not None and live.is_file() and live != dest:
            if dry_run:
                lib.info(f"Would seed overwrite axr_options from:\n  {live}")
                dest = live  # diff against current live file for preview
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(live, dest)
        elif dry_run:
            lib.info(f"Would create axr_options and apply {len(settings)} MCM key(s):\n  {dest}")
            for s in settings:
                print(f"  [{s.axr_section}] {s.key} = {s.value}")
            return 0
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text("[mcm]\n", encoding="utf-8", newline="\r\n")

    if not dest.is_file():
        lib.err(f"No axr_options.ltx to update:\n  {dest}")
        return 1

    # Preview changes first (dry_run compute); write only when not dry_run.
    changes = lib.apply_settings_to_axr_options(dest, settings, dry_run=True)
    print()
    if changes:
        print(f"MCM changes ({len(changes)} of {len(settings)} backed-up keys):")
        for c in changes:
            print(f"  {c}")
    else:
        print("MCM: no changes (already matches backup).")

    if dry_run:
        lib.info(f"Dry run - would apply MCM to:\n  {restore_axr_options_path(mo2_root)}")
        return 0

    if not changes:
        return 0

    write_dest = restore_axr_options_path(mo2_root)
    if write_dest != dest:
        write_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dest, write_dest)
    lib.apply_settings_to_axr_options(write_dest, settings, dry_run=False)
    lib.ok(f"Applied {len(changes)} MCM change(s) ->\n  {write_dest}")
    return 0


def step_restore_user_ltx(mo2_root: Path, src_dir: Path, *, dry_run: bool) -> int:
    src = src_dir / USER_LTX_NAME
    if not src.is_file():
        lib.err(f"Missing {USER_LTX_NAME} in backup:\n  {src_dir}")
        return 1
    dest = game_user_ltx_path(mo2_root)
    new_text = src.read_text(encoding="utf-8", errors="replace")
    old_text = (
        dest.read_text(encoding="utf-8", errors="replace") if dest.is_file() else ""
    )
    diff_lines = _diff_user_ltx(old_text, new_text)
    print()
    if not diff_lines:
        print("user.ltx: no changes.")
    else:
        print("user.ltx changes:")
        # Skip ---/+++ headers noise somewhat; show the hunk lines.
        for line in diff_lines:
            if line.startswith("---") or line.startswith("+++"):
                continue
            print(f"  {line}")

    if dry_run:
        lib.info(f"Dry run - would restore user.ltx ->\n  {dest}")
        return 0

    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file():
        lib.stamp_backup(dest)
    shutil.copy2(src, dest)
    lib.ok(f"Restored user.ltx ->\n  {dest}")
    return 0


def step_restore_modlist(
    mo2_root: Path,
    src_dir: Path,
    *,
    profile: str,
    dry_run: bool,
) -> int:
    src = src_dir / MODLIST_NAME
    if not src.is_file():
        lib.err(f"Missing {MODLIST_NAME} in backup:\n  {src_dir}")
        return 1

    profile_name = profile
    profile_file = src_dir / "profile.txt"
    if not profile_name and profile_file.is_file():
        profile_name = profile_file.read_text(encoding="utf-8").strip()
    meta = read_meta(src_dir)
    if not profile_name:
        profile_name = str(meta.get("profile") or "")
    dest = lib.modlist_path(mo2_root, profile_name)

    if dry_run:
        lib.info(f"Would restore modlist ->\n  {dest}")
        return 0

    lib.stamp_backup(dest)
    shutil.copy2(src, dest)
    lib.ok(f"Restored mod list ->\n  {dest}")
    lib.info("  (+ enabled / - disabled / order). Refresh MO2 if it is open.")
    return 0


def step_restore_mods(mo2_root: Path, src_dir: Path, *, dry_run: bool) -> int:
    archive = src_dir / MODS_ARCHIVE_NAME
    if not archive.is_file():
        lib.err(f"Missing {MODS_ARCHIVE_NAME} in backup:\n  {src_dir}")
        return 1

    seven = lib.find_7z()
    if seven is None:
        lib.err("7-Zip is required to restore the mods archive.")
        return 1

    mods = mo2_root / "mods"
    print()
    print(f"This will DELETE your current mods folder and extract the backup:")
    print(f"  {mods}")
    print(f"  <- {archive}")
    if dry_run:
        lib.info("Dry run - would replace mods/ from archive.")
        return 0

    # Move aside then extract (safer than rm mid-failure).
    trash = mo2_root / f"mods.pre_restore_{stamp_now()}"
    if mods.exists():
        lib.info(f"Moving current mods aside ->\n  {trash}")
        mods.rename(trash)

    lib.info("Extracting mods archive...")
    # Archive contains a top-level ``mods`` folder (7z of the mods path).
    cmd = [str(seven), "x", str(archive), f"-o{mo2_root}", "-y"]
    code = subprocess.call(cmd)
    if code or not mods.is_dir():
        lib.err("Mods extract failed - attempting to put the old folder back.")
        if trash.is_dir() and not mods.exists():
            trash.rename(mods)
        return int(code) if code else 1

    lib.ok(f"Mods restored from archive.")
    # Remove the aside copy after success (large). Offer keep? Default delete.
    try:
        lib.info("Removing temporary pre-restore mods folder...")
        shutil.rmtree(trash)
    except OSError as exc:
        lib.warn(f"Could not remove {trash} ({exc}). Delete it manually if you like.")
    return 0


def pick_backup_dir(mo2_root: Path, explicit: str = "") -> Path | None:
    if explicit.strip():
        p = Path(explicit.strip().strip('"')).expanduser()
        if not p.is_absolute():
            p = backup_root(mo2_root) / p
        p = p.resolve()
        if not p.is_dir():
            lib.err(f"Backup folder not found:\n  {p}")
            return None
        return p

    backups = list_backups(mo2_root)
    if not backups:
        lib.err(f"No backups under:\n  {backup_root(mo2_root)}")
        return None

    print()
    print("Available backups (newest first):")
    for i, b in enumerate(backups, 1):
        meta = read_meta(b)
        comps = BackupComponents.from_meta(meta)
        flags = ",".join(k for k, v in comps.as_dict().items() if v) or "?"
        print(f"  {i}. {b.name}  [{flags}]")
    print("  0. Cancel")
    try:
        raw = input("Pick a backup number: ").strip()
    except EOFError:
        return None
    if not raw or raw == "0":
        return None
    try:
        idx = int(raw)
    except ValueError:
        lib.err("Not a number.")
        return None
    if idx < 1 or idx > len(backups):
        lib.err("Out of range.")
        return None
    return backups[idx - 1]


def available_restore_components(src_dir: Path) -> BackupComponents:
    meta = BackupComponents.from_meta(read_meta(src_dir))
    # Fall back to files present if meta missing/partial.
    return BackupComponents(
        mcm=meta.mcm or (src_dir / MCM_DIFF_NAME).is_file(),
        user_ltx=meta.user_ltx or (src_dir / USER_LTX_NAME).is_file(),
        modlist=meta.modlist or (src_dir / MODLIST_NAME).is_file(),
        mods=meta.mods or (src_dir / MODS_ARCHIVE_NAME).is_file(),
    )


def run_restore(
    mo2_root: Path,
    src_dir: Path,
    components: BackupComponents,
    *,
    profile: str = "",
    dry_run: bool = False,
) -> int:
    if not components.any():
        lib.err("Nothing selected to restore.")
        return 1

    mo2_root = Path(mo2_root).resolve()
    src_dir = Path(src_dir).resolve()
    avail = available_restore_components(src_dir)

    print()
    lib.info(f"Restoring from:\n  {src_dir}")

    if components.mods:
        if not avail.mods:
            lib.err("This backup has no mods archive.")
            return 1
        code = step_restore_mods(mo2_root, src_dir, dry_run=dry_run)
        if code:
            return code

    if components.modlist:
        if not avail.modlist:
            lib.err("This backup has no mod list.")
            return 1
        code = step_restore_modlist(
            mo2_root, src_dir, profile=profile, dry_run=dry_run
        )
        if code:
            return code

    if components.user_ltx:
        if not avail.user_ltx:
            lib.err("This backup has no user.ltx.")
            return 1
        code = step_restore_user_ltx(mo2_root, src_dir, dry_run=dry_run)
        if code:
            return code

    if components.mcm:
        if not avail.mcm:
            lib.err("This backup has no MCM diff.")
            return 1
        code = step_restore_mcm(mo2_root, src_dir, dry_run=dry_run)
        if code:
            return code

    lib.ok("Restore complete.")
    return 0


# ---------------------------------------------------------------------------
# CLI entrypoints (via dogma_job)
# ---------------------------------------------------------------------------


def run_backup_cli(args) -> int:
    mo2 = lib.resolve_mo2_root(getattr(args, "mo2_root", None) or None)
    dry_run = bool(getattr(args, "dry_run", False))
    # Restoring / writing modlist+axr while MO2 open is risky for backup of those
    # files too if MO2 rewrites on exit - warn only when touching live files later.
    comps = components_from_args(args)
    if comps is None:
        comps = prompt_components(default=BackupComponents.all(), allow_mods=True)
    if not comps.any():
        lib.err("Nothing selected.")
        return 1

    code, dest = run_backup(
        mo2,
        comps,
        profile=getattr(args, "profile", "") or "",
        dry_run=dry_run,
    )
    if code == 0 and dest is not None:
        print()
        print(f"Backup path:\n  {dest}")
    return code


def run_restore_cli(args) -> int:
    mo2 = lib.resolve_mo2_root(getattr(args, "mo2_root", None) or None)
    dry_run = bool(getattr(args, "dry_run", False))
    lib.guard_mo2_closed(force=bool(getattr(args, "force", False)), dry_run=dry_run)

    src = pick_backup_dir(mo2, getattr(args, "backup", "") or "")
    if src is None:
        return 2

    avail = available_restore_components(src)
    print()
    print("This backup contains:")
    for name, on in avail.as_dict().items():
        print(f"  {'yes' if on else 'no ':4}  {name}")

    # Safety backup first (user picks whether to include mods).
    print()
    if _yn("Create a safety backup of your current state first?", default_yes=True):
        safety = prompt_components(
            default=BackupComponents.config_only(),
            allow_mods=True,
            label="safety backup",
        )
        if safety.any():
            code, _ = run_backup(
                mo2,
                safety,
                profile=getattr(args, "profile", "") or "",
                dry_run=dry_run,
            )
            if code:
                lib.err("Safety backup failed - aborting restore.")
                return code

    comps = components_from_args(args)
    if comps is None:
        print()
        print("What should be restored?")
        comps = BackupComponents(
            mcm=_yn("  MCM options", default_yes=avail.mcm) if avail.mcm else False,
            user_ltx=_yn("  user.ltx", default_yes=avail.user_ltx)
            if avail.user_ltx
            else False,
            modlist=_yn("  MO2 mod list", default_yes=avail.modlist)
            if avail.modlist
            else False,
            mods=_yn("  Full mods/ folder (DESTRUCTIVE)", default_yes=False)
            if avail.mods
            else False,
        )
    elif getattr(args, "all", False):
        # --all means every component present in this backup.
        comps = BackupComponents(
            mcm=avail.mcm,
            user_ltx=avail.user_ltx,
            modlist=avail.modlist,
            mods=avail.mods,
        )

    return run_restore(
        mo2,
        src,
        comps,
        profile=getattr(args, "profile", "") or "",
        dry_run=dry_run,
    )
