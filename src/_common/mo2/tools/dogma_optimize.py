#!/usr/bin/env python3
"""DOGMA Optimize - GC settings, SFX prefetch, full backup, ALAO."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import dogma_backup as backup
import dogma_mo2_lib as lib

ALAO_URL = (
    "https://www.moddb.com/mods/stalker-anomaly/addons/"
    "alao-anomaly-lua-auto-optimizer-tool"
)
ALAO_MOD_NAME = "ALAO"
GC_SCRIPT_NAME = "zzzz_dogma_lua_gc.script"
GC_SCRIPT_BODY = """--[[
	DOGMA - Lua GC policy (LuaJIT / Lua 5.1).

	Engine already steps via lua_gcstep + parallel GC (user.ltx).
	Here we only tune collector policy:
	  setpause 200  - default; start next cycle at 2x post-GC heap
	  setstepmul 300 - slightly more aggressive than default 200
	Invalid / rejected: collectgarbage("setstep", ...) - not a LuaJIT option.
]]

function on_game_start()
	collectgarbage("setpause", 200)
	collectgarbage("setstepmul", 300)
end
"""

GC_USER_LTX_KEYS: dict[str, str] = {
    "lua_gcstep": "300",
    "lua_parallel_gc": "1",
    "lua_parallel_gc_call_amount": "25",
    "lua_parallel_gcstep": "75",
}

# user.ltx console vars are "key value" (space), not key=value.
_LTX_KEY_RE = re.compile(r"^(\s*)([A-Za-z0-9_]+)\s+(\S.*?)\s*$")


def _yn(prompt: str, default_yes: bool = True) -> bool:
    suffix = " [Y/n] " if default_yes else " [y/N] "
    try:
        raw = input(prompt + suffix).strip().lower()
    except EOFError:
        return default_yes
    if not raw:
        return default_yes
    return raw in ("y", "yes")


def resolve_alao_root(mo2_root: Path | None = None) -> Path | None:
    env = (os.environ.get("ALAO_PATH") or "").strip()
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env))
    if mo2_root is not None:
        # "anomaly_alao" is MO2's default mod name for the ModDB zip.
        for name in (ALAO_MOD_NAME, "anomaly_alao"):
            mod = mo2_root / "mods" / name
            # ModDB zip nests everything under anomaly_alao-main/.
            candidates.append(mod)
            candidates.append(mod / "anomaly_alao-main")
    candidates.append(Path(r"c:\gamma_dev\ALAO"))
    # …/DOGMA/src/_common/mo2/tools → parents[4] = repo root → sibling ALAO
    try:
        repo = Path(__file__).resolve().parents[4]
        candidates.append(repo.parent / "ALAO")
    except IndexError:
        pass
    for c in candidates:
        if (c / "stalker_lua_lint.py").is_file():
            return c.resolve()
    return None


def ensure_alao_deps(alao_root: Path) -> int:
    try:
        import luaparser  # noqa: F401
        import jinja2  # noqa: F401

        return 0
    except ImportError:
        pass
    req = alao_root / "requirements.txt"
    if not req.is_file():
        lib.err(f"ALAO is missing its requirements file:\n  {req}")
        return 1
    lib.info("Installing ALAO dependencies (one-time)…")
    code = subprocess.call(
        [sys.executable, "-m", "pip", "install", "-r", str(req)],
    )
    if code:
        lib.err("Could not install ALAO dependencies.")
    return int(code)


def game_user_ltx_path(mo2_root: Path) -> Path:
    return lib.game_dir(mo2_root) / "appdata" / "user.ltx"


def upsert_user_ltx_keys(
    path: Path,
    keys: dict[str, str],
    *,
    dry_run: bool,
    stamp_backup: bool = True,
) -> list[str]:
    """Upsert key value lines in user.ltx; return change descriptions."""
    if path.is_file():
        lines = lib.read_text_lines(path)
    else:
        lines = []

    found: set[str] = set()
    out: list[str] = []
    changes: list[str] = []
    for line in lines:
        m = _LTX_KEY_RE.match(line.rstrip("\r\n"))
        if not m:
            out.append(line.rstrip("\r\n"))
            continue
        indent, key, val = m.group(1), m.group(2), m.group(3)
        if key in keys:
            found.add(key)
            want = keys[key]
            if val.strip() != want:
                changes.append(f"{key}: {val.strip()!r} -> {want!r}")
                out.append(f"{indent}{key} {want}")
            else:
                out.append(line.rstrip("\r\n"))
        else:
            out.append(line.rstrip("\r\n"))

    for key, want in keys.items():
        if key not in found:
            changes.append(f"{key}: (missing) -> {want!r}")
            out.append(f"{key} {want}")

    if changes and not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        if stamp_backup and path.is_file():
            lib.stamp_backup(path)
        lib.write_text_lines(path, out)
    return changes


def user_back_ltx_path(user_ltx: Path) -> Path:
    return user_ltx.with_name("user.back.ltx")


def backup_user_ltx(user_ltx: Path, *, dry_run: bool, skip_if_exists: bool | None) -> int:
    """Copy user.ltx → user.back.ltx. Returns 0 on success / skip."""
    if not user_ltx.is_file():
        lib.warn("No user.ltx to back up.")
        return 0
    dest = user_back_ltx_path(user_ltx)
    if dest.is_file():
        if skip_if_exists is None:
            print()
            print(f"A backup already exists:\n  {dest}")
            skip_if_exists = _yn("Skip backup and keep the existing one?", default_yes=True)
        if skip_if_exists:
            lib.info("Keeping existing user.back.ltx.")
            return 0
    if dry_run:
        lib.info(f"Would back up user.ltx to:\n  {dest}")
        return 0
    try:
        shutil.copy2(user_ltx, dest)
    except OSError as exc:
        lib.err(f"Could not create user.back.ltx ({exc}).")
        return 1
    lib.ok(f"Backed up user.ltx to:\n  {dest}")
    return 0


def ensure_gc_script(mo2_root: Path, *, dry_run: bool) -> Path:
    dest = lib.dogma_mod_dir(mo2_root) / "gamedata" / "scripts" / GC_SCRIPT_NAME
    if dry_run:
        lib.info(f"Would add Lua memory helper script:\n  {dest}")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.read_text(encoding="utf-8", errors="replace") == GC_SCRIPT_BODY:
        lib.info("Lua memory helper script is already in place.")
    else:
        dest.write_text(GC_SCRIPT_BODY, encoding="utf-8", newline="\n")
        lib.ok("Added Lua memory helper script to DOGMA.")
    return dest


def _gc_user_ltx_display(mo2_root: Path) -> Path | None:
    try:
        return game_user_ltx_path(mo2_root)
    except (OSError, FileNotFoundError, ValueError):
        return None


def read_user_ltx_keys(path: Path | None, keys: dict[str, str]) -> dict[str, str | None]:
    """Return current values for keys (None if missing / unreadable)."""
    out: dict[str, str | None] = {k: None for k in keys}
    if path is None or not path.is_file():
        return out
    for line in lib.read_text_lines(path):
        m = _LTX_KEY_RE.match(line.rstrip("\r\n"))
        if not m:
            continue
        key = m.group(2)
        if key in out:
            out[key] = m.group(3).strip()
    return out


def _gc_script_already_applied(mo2_root: Path) -> bool:
    dest = lib.dogma_mod_dir(mo2_root) / "gamedata" / "scripts" / GC_SCRIPT_NAME
    if not dest.is_file():
        return False
    try:
        return dest.read_text(encoding="utf-8", errors="replace") == GC_SCRIPT_BODY
    except OSError:
        return False


def prompt_gc(mo2_root: Path) -> bool:
    user_ltx = _gc_user_ltx_display(mo2_root)
    current = read_user_ltx_keys(user_ltx, GC_USER_LTX_KEYS)
    script_on = _gc_script_already_applied(mo2_root)

    print()
    print("Current Lua garbage collection values:")
    for key in GC_USER_LTX_KEYS:
        cur = current.get(key)
        print(f"  {key} {cur if cur is not None else '(not set)'}")
    if script_on:
        print("  collectgarbage setpause 200  (already via DOGMA)")
        print("  collectgarbage setstepmul 300  (already via DOGMA)")
    else:
        print("  collectgarbage setpause  (engine default, usually 200)")
        print("  collectgarbage setstepmul  (engine default, usually 200)")

    print()
    print("Change to:")
    for key, val in GC_USER_LTX_KEYS.items():
        cur = current.get(key)
        marker = "" if cur == val else "  ←"
        print(f"  {key} {val}{marker}")
    print("  collectgarbage setpause 200")
    print("  collectgarbage setstepmul 300")
    print()
    if user_ltx is not None:
        print("You can edit these values later in:")
        print(f"  {user_ltx}")
        print("Your user.ltx will be backed up to:")
        print(f"  {user_back_ltx_path(user_ltx)}")
    else:
        print("You can edit these values later in your game’s appdata\\user.ltx.")
    print()
    return _yn("Apply these settings?", default_yes=True)


def step_gc(mo2_root: Path, *, dry_run: bool) -> int:
    user_ltx = _gc_user_ltx_display(mo2_root)
    if user_ltx is None:
        lib.warn("Could not find the game’s user.ltx - skipping that file.")
    else:
        # Back up first (user.back.ltx); offer to keep an existing backup.
        if backup_user_ltx(user_ltx, dry_run=dry_run, skip_if_exists=None):
            return 1
        changes = upsert_user_ltx_keys(
            user_ltx,
            GC_USER_LTX_KEYS,
            dry_run=dry_run,
            stamp_backup=False,
        )
        if changes:
            lib.ok("Updated garbage collection settings.")
        else:
            lib.info("Those settings were already applied.")

    ensure_gc_script(mo2_root, dry_run=dry_run)
    return 0


def resolve_sfx_builder() -> Path | None:
    here = Path(__file__).resolve().parent
    # Deployed / common: mods/DOGMA/mo2/tools/dogma_sfx_prefetch.py (next to this file)
    candidates: list[Path] = [
        here / "dogma_sfx_prefetch.py",
        # Legacy name
        here / "build_sound_prefetch.py",
    ]
    # Legacy flat path from older builds
    if here.name.lower() == "tools":
        candidates.append(here.parent / "dogma_sfx_prefetch.py")
        candidates.append(here.parent / "build_sound_prefetch.py")
    try:
        repo = Path(__file__).resolve().parents[4]
        tools = repo / "src" / "_common" / "mo2" / "tools"
        candidates.append(tools / "dogma_sfx_prefetch.py")
        candidates.append(tools / "build_sound_prefetch.py")
    except IndexError:
        pass
    for py in candidates:
        if py.is_file():
            return py
    return None


def run_sfx_prefetch(mo2_root: Path, *, force: bool = False) -> int:
    """Run sound prefetch builder; tee to action log. Shared with cmd_sfx."""
    py = resolve_sfx_builder()
    if py is None:
        lib.err("Sound prefetch tool is missing from this DOGMA install.")
        return 1
    cmd = [sys.executable, "-u", str(py), "--mo2-root", str(mo2_root)]
    if force:
        cmd.append("--force")
    lib.info("Building sound prefetch list (can take a few minutes)…")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip("\r\n")
        print(line, flush=True)
        lib.append_action_log(mo2_root, line)
    return int(proc.wait())


def _latest_mods_archive(mo2_root: Path) -> Path | None:
    for b in backup.list_backups(mo2_root):
        archive = b / backup.MODS_ARCHIVE_NAME
        if archive.is_file():
            return archive
    return None


def run_alao(
    target: Path,
    *,
    report: Path | None = None,
    exclude_lines: list[str] | None = None,
    direct: bool = False,
    dry_run: bool = False,
    mo2_root: Path | None = None,
) -> int:
    """Run ALAO with the same fix flags Optimize uses.

    Flags: ``--fix --fix-nil --remove-dead-code --no-first-time-auto-backup``
    plus optional ``--direct`` (authoring trees without gamedata/scripts).
    """
    alao = resolve_alao_root(mo2_root)
    if alao is None:
        lib.err(
            "Could not find ALAO (Anomaly Lua Auto Optimizer).\n"
            f"Download: {ALAO_URL}\n"
            "Install the zip in Mod Organizer 2 with the suggested mod name.\n"
            "(MO2 warns that it has no game data - that is fine, and it can\n"
            "stay unchecked.)"
        )
        return 1

    target = target.resolve()
    if not target.exists():
        lib.err(f"ALAO target does not exist:\n  {target}")
        return 1

    if report is not None:
        report.parent.mkdir(parents=True, exist_ok=True)

    if dry_run:
        mode = "direct" if direct else "mods"
        lib.info(f"Dry run - would run ALAO ({mode}) on:\n  {target}")
        return 0

    code = ensure_alao_deps(alao)
    if code:
        return code

    lint = alao / "stalker_lua_lint.py"
    exclude_path: Path | None = None
    cmd = [
        sys.executable,
        str(lint),
        str(target),
        "--fix",
        "--fix-nil",
        "--remove-dead-code",
        "--no-first-time-auto-backup",
    ]
    if direct:
        cmd.append("--direct")
    if exclude_lines:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".txt",
            prefix="dogma_alao_exclude_",
            delete=False,
        ) as tf:
            for line in exclude_lines:
                tf.write(line + "\n")
            exclude_path = Path(tf.name)
        cmd.extend(["--exclude", str(exclude_path)])
    if report is not None:
        cmd.extend(["--report", str(report)])

    lib.info(f"Running ALAO… this can take a long time.\n  {ALAO_URL}")
    try:
        code = subprocess.call(cmd, cwd=str(alao))
    finally:
        if exclude_path is not None:
            try:
                exclude_path.unlink(missing_ok=True)
            except OSError:
                pass

    if code:
        lib.err("ALAO failed. See the console output above for details.")
        return int(code)
    if report is not None:
        lib.ok(f"ALAO finished. Report:\n  {report}")
    else:
        lib.ok("ALAO finished.")
    return 0


def step_alao(
    mo2_root: Path,
    *,
    dry_run: bool,
) -> int:
    archive = _latest_mods_archive(mo2_root)
    if archive is None:
        lib.warn(
            f"ALAO needs a mods backup first "
            f"({backup.MODS_ARCHIVE_NAME} under DOGMA\\backups)."
        )
        return 0

    mods = mo2_root / "mods"
    lib.info(f"Mods backup on disk:\n  {archive}")
    lib.info("ALAO will process all mods (excluding VANILLA_SCRIPTS only).")

    report = lib.logs_dir(mo2_root) / "alao_report.html"

    return run_alao(
        mods,
        report=report,
        exclude_lines=["VANILLA_SCRIPTS"],
        direct=False,
        dry_run=dry_run,
        mo2_root=mo2_root,
    )


def _flag_tri(args, yes_attr: str, no_attr: str) -> bool | None:
    """Return True/False if forced by CLI, else None (prompt)."""
    if getattr(args, yes_attr, False):
        return True
    if getattr(args, no_attr, False):
        return False
    return None


def run(args) -> int:
    mo2 = lib.resolve_mo2_root(getattr(args, "mo2_root", None) or None)
    dry_run = bool(getattr(args, "dry_run", False))

    print()
    print("D.O.G.M.A. Optimizer")

    # --- 1. GC ---
    do_gc = _flag_tri(args, "do_gc", "no_gc")
    if do_gc is None:
        do_gc = prompt_gc(mo2)
    if do_gc:
        code = step_gc(mo2, dry_run=dry_run)
        if code:
            return code
    else:
        lib.info("Skipped.")

    # --- 2. SFX ---
    do_sfx = _flag_tri(args, "do_sfx", "no_sfx")
    if do_sfx is None:
        print()
        do_sfx = _yn(
            "Build a sound prefetch list to reduce audio hitching?",
            default_yes=True,
        )
    if do_sfx:
        if dry_run:
            lib.info("Dry run - would build sound prefetch.")
        else:
            code = run_sfx_prefetch(mo2, force=bool(getattr(args, "force", False)))
            if code:
                return code
    else:
        lib.info("Skipped.")

    # --- 3. Full DOGMA Backup (required before ALAO) ---
    do_backup = _flag_tri(args, "do_backup", "no_backup")
    backup_root = backup.backup_root(mo2)
    if do_backup is None:
        print()
        print("Create a full DOGMA Backup (MCM, user.ltx, modlist, mods archive).")
        print(f"  {backup_root}\\<timestamp>\\")
        print()
        print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        print("!!  WARNING: MODS ARCHIVE CAN TAKE A LONG TIME  !!")
        print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        print()
        do_backup = _yn("Create this backup now?", default_yes=True)
    backup_failed = False
    mods_archive = _latest_mods_archive(mo2)
    if do_backup:
        code, dest = backup.run_backup(
            mo2,
            backup.BackupComponents.all(),
            profile=getattr(args, "profile", "") or "",
            dry_run=dry_run,
        )
        if code:
            backup_failed = True
            lib.warn("Backup did not finish.")
        else:
            mods_archive = _latest_mods_archive(mo2)
            if dest is not None:
                print()
                lib.ok(f"Backup step finished:\n  {dest}")
    else:
        lib.info("Skipped backup.")

    # --- 4. ALAO (only after an explicit second confirmation) ---
    do_alao = _flag_tri(args, "do_alao", "no_alao")
    if mods_archive is None:
        print()
        lib.warn(
            f"No mods archive found ({backup.MODS_ARCHIVE_NAME}). "
            "Skipping ALAO for safety."
        )
        if do_alao is True:
            lib.err("ALAO was requested, but there is no mods backup.")
            return 1
        do_alao = False
    elif do_alao is None:
        print()
        print("-----")
        print("Next step (optional): ALAO - optimize Lua scripts in your mods.")
        print(f"  {ALAO_URL}")
        print(f"This only runs if you say yes. Backup on disk:\n  {mods_archive}")
        # Default N so finishing backup does not chain into ALAO on Enter.
        do_alao = _yn("Run ALAO now?", default_yes=False)

    if do_alao:
        code = step_alao(mo2, dry_run=dry_run)
        if code:
            return code
    elif do_alao is False:
        lib.info("Skipped ALAO.")

    print()
    if backup_failed:
        lib.err("Finished, but the backup failed.")
        return 1
    lib.ok("All done.")
    return 0
