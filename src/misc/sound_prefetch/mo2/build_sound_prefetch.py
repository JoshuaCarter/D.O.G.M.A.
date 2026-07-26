#!/usr/bin/env python3
"""Build dogma_sfx_prefetch.ltx from enabled MO2 mods' loose sounds.

Part of feature src/misc/sound_prefetch. Shipped to mods/DOGMA/mo2/ via the
mo2/ build exception. Runtime script is built normally into gamedata/scripts/.

  mods/DOGMA/mo2/build_sound_prefetch.bat   (MO2 Executable; Start in = instance root)
  overwrite/gamedata/configs/dogma_sfx_prefetch.ltx  (generated)

  py -3 build_sound_prefetch.py
  py -3 build_sound_prefetch.py --dry-run
  py -3 build_sound_prefetch.py --then-launch AnomalyDX11AVX.exe
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


DOGMA_MOD_NAME = "DOGMA"
LEGACY_SEPARATE_MOD = "DOGMA - Sound Prefetch"
SOUND_EXTS = {".ogg", ".wav", ".flac"}
SECTION = "dogma_sfx_list"
LTX_NAME = "dogma_sfx_prefetch.ltx"
LTX_REL = Path("gamedata") / "configs" / LTX_NAME


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
        return value.replace("\\\\", "\\")
    raise ValueError(f"{key} not found in {ini_path}")


def read_text_lines(path: Path) -> list[str]:
    raw = path.read_bytes()
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


def write_text_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = "\r\n".join(lines) + "\r\n"
    path.write_bytes(data.encode("utf-8"))


def resolve_mo2_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    cwd = Path.cwd()
    if (cwd / "ModOrganizer.ini").is_file():
        return cwd
    return Path(r"C:\GAMMA") if sys.platform == "win32" else Path(os.environ.get("MO2_ROOT", r"C:\GAMMA"))


def list_enabled_mod_names(modlist_path: Path) -> list[str]:
    names: list[str] = []
    for line in read_text_lines(modlist_path):
        if line.startswith("+"):
            name = line[1:]
            if name and "separator" not in name.lower():
                names.append(name)
    return names


def find_dogma_mod_dir(mo2_root: Path, enabled_mods: list[str]) -> Path | None:
    mods_dir = mo2_root / "mods"
    exact = mods_dir / DOGMA_MOD_NAME
    if exact.is_dir():
        return exact
    for name in enabled_mods:
        if name.lower() == DOGMA_MOD_NAME.lower():
            candidate = mods_dir / name
            if candidate.is_dir():
                return candidate
    return None


def sound_rel_path(sounds_root: Path, file_path: Path) -> str | None:
    try:
        rel = file_path.relative_to(sounds_root)
    except ValueError:
        return None
    if rel.suffix.lower() not in SOUND_EXTS:
        return None
    stem = rel.with_suffix("")
    return str(stem).replace("/", "\\")


def collect_sound_paths(mo2_root: Path, enabled_mods: list[str]) -> list[str]:
    found: dict[str, str] = {}
    mods_dir = mo2_root / "mods"
    skip = {LEGACY_SEPARATE_MOD.lower()}

    for name in reversed(enabled_mods):
        if name.lower() in skip:
            continue
        sounds = mods_dir / name / "gamedata" / "sounds"
        if not sounds.is_dir():
            continue
        for path in sounds.rglob("*"):
            if not path.is_file():
                continue
            rel = sound_rel_path(sounds, path)
            if rel and not rel.startswith("$"):
                found[rel.lower()] = rel

    overwrite = mo2_root / "overwrite" / "gamedata" / "sounds"
    if overwrite.is_dir():
        for path in overwrite.rglob("*"):
            if not path.is_file():
                continue
            rel = sound_rel_path(overwrite, path)
            if rel and not rel.startswith("$"):
                found[rel.lower()] = rel

    return sorted(found.values(), key=lambda s: s.lower())


def format_ltx(paths: list[str]) -> list[str]:
    lines = [f"[{SECTION}]"]
    for i, path in enumerate(paths, start=1):
        lines.append(f"t{i} = {path}")
    return lines


def resolve_launch_exe(mo2_root: Path, then_launch: str) -> Path:
    candidate = Path(then_launch)
    if candidate.is_file():
        return candidate.resolve()
    try:
        game_path = Path(read_mo2_ini_value(mo2_root / "ModOrganizer.ini", "gamePath"))
    except (FileNotFoundError, ValueError):
        game_path = None
    search = []
    if game_path:
        search.append(game_path / then_launch)
    search.append(mo2_root / then_launch)
    search.append(Path.cwd() / then_launch)
    for path in search:
        if path.is_file():
            return path.resolve()
    raise FileNotFoundError(
        f"Launch exe not found: {then_launch} (tried gamePath, mo2-root, cwd)"
    )


def remove_legacy_separate_mod(mo2_root: Path, modlist_path: Path, dry_run: bool) -> None:
    legacy_dir = mo2_root / "mods" / LEGACY_SEPARATE_MOD
    if legacy_dir.is_dir():
        if dry_run:
            warn(f"Dry run: would remove legacy mod {legacy_dir}")
        else:
            shutil.rmtree(legacy_dir)
            ok(f"Removed legacy mod: {legacy_dir.name}")

    lines = read_text_lines(modlist_path)
    target = LEGACY_SEPARATE_MOD.lower()
    kept = []
    removed = False
    for line in lines:
        m = re.match(r"^([+\-])(.+)$", line)
        if m and m.group(2).lower() == target:
            removed = True
            continue
        kept.append(line)
    if removed:
        if dry_run:
            warn(f"Dry run: would remove '{LEGACY_SEPARATE_MOD}' from modlist")
        else:
            write_text_lines(modlist_path, kept)
            ok(f"Removed '{LEGACY_SEPARATE_MOD}' from modlist")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Scan enabled MO2 mods for loose sounds and write dogma_sfx_prefetch.ltx "
            "into overwrite/gamedata. Register mods/DOGMA/mo2/build_sound_prefetch.bat "
            "in MO2 Executables with Start in = instance root."
        )
    )
    p.add_argument(
        "--mo2-root",
        default="",
        help="MO2 instance folder. Default: cwd if ModOrganizer.ini present, else C:\\GAMMA",
    )
    p.add_argument("--profile", default="", help="MO2 profile (default: selected_profile)")
    p.add_argument("--dry-run", action="store_true", help="Scan and report; write nothing")
    p.add_argument(
        "--then-launch",
        default="",
        metavar="EXE",
        help="After a successful write, launch this exe",
    )
    p.add_argument(
        "launch_args",
        nargs="*",
        help="Args passed to --then-launch",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    mo2_root = resolve_mo2_root(args.mo2_root or None)

    if not (mo2_root / "ModOrganizer.exe").is_file():
        err(f"ModOrganizer.exe not found under: {mo2_root}")
        err("Set MO2 Working Directory to the instance root, or pass --mo2-root")
        return 1

    profile = args.profile or read_mo2_ini_value(mo2_root / "ModOrganizer.ini", "selected_profile")
    modlist_path = mo2_root / "profiles" / profile / "modlist.txt"
    if not modlist_path.is_file():
        err(f"modlist.txt not found: {modlist_path}")
        return 1

    info(f"MO2 root : {mo2_root}")
    info(f"Profile  : {profile}")
    if args.dry_run:
        warn("Dry run - no files will be written.")

    enabled = list_enabled_mod_names(modlist_path)
    info(f"Enabled mods (non-separator): {len(enabled)}")

    dogma_dir = find_dogma_mod_dir(mo2_root, enabled)
    if dogma_dir:
        info(f"DOGMA mod: {dogma_dir}")

    paths = collect_sound_paths(mo2_root, enabled)
    info(f"Unique sound paths: {len(paths)}")
    if paths:
        sample = [p for p in paths if p.lower().startswith("weapons\\")]
        show = (sample or paths)[:8]
        info("  Sample:")
        for s in show:
            info(f"    {s}")

    ltx_path = mo2_root / "overwrite" / LTX_REL
    remove_legacy_separate_mod(mo2_root, modlist_path, args.dry_run)

    # Drop older copies (DOGMA mod and previous paths/names).
    stale = [
        (dogma_dir / LTX_REL) if dogma_dir else None,
        mo2_root / "overwrite" / "gamedata" / "configs" / "dogma_snd_prefetch.ltx",
        mo2_root / "overwrite" / "gamedata" / "configs" / "items" / "items" / "dogma_snd_prefetch.ltx",
        mo2_root / "overwrite" / "gamedata" / "configs" / "items" / "items" / LTX_NAME,
        (dogma_dir / "gamedata" / "configs" / "dogma_snd_prefetch.ltx") if dogma_dir else None,
        (dogma_dir / "gamedata" / "configs" / "items" / "items" / "dogma_snd_prefetch.ltx") if dogma_dir else None,
        (dogma_dir / "gamedata" / "configs" / "items" / "items" / LTX_NAME) if dogma_dir else None,
    ]

    if not args.dry_run:
        write_text_lines(ltx_path, format_ltx(paths))
        ok(f"Wrote {ltx_path.relative_to(mo2_root)} ({len(paths)} entries)")
        for old in stale:
            if old and old.is_file() and old.resolve() != ltx_path.resolve():
                old.unlink()
                warn(f"Removed stale {old.relative_to(mo2_root)}")
    else:
        info(f"Would write {ltx_path}")

    if args.then_launch:
        if args.dry_run:
            warn(f"Dry run: skipping launch of {args.then_launch}")
            return 0
        exe = resolve_launch_exe(mo2_root, args.then_launch)
        launch_args = [str(exe), *args.launch_args]
        info(f"Launching: {' '.join(launch_args)}")
        completed = subprocess.run(launch_args, check=False)
        return int(completed.returncode)

    ok("Done.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError, RuntimeError, OSError) as exc:
        err(str(exc))
        raise SystemExit(1) from exc
