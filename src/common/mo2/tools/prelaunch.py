#!/usr/bin/env python3
"""Optionally run pre-launch steps, then launch the game.

Register mods/DOGMA/mo2/tools/DOGMA.bat in MO2 Executables:
  Start in  = instance root (C:\\GAMMA)
  Arguments = --then-launch AnomalyDX11AVX.exe

Default: no pre-launch steps (run DOGMA SFX Prefetch.bat when you want a sound list rebuild).
Optional steps file: mo2/prelaunch.steps — one command per line (#/; comments).
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path


def info(msg: str) -> None:
    print(msg)


def ok(msg: str) -> None:
    print(msg)


def warn(msg: str) -> None:
    print(msg)


def err(msg: str) -> None:
    print(msg, file=sys.stderr)


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


def resolve_mo2_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    cwd = Path.cwd()
    if (cwd / "ModOrganizer.ini").is_file():
        return cwd
    return Path(r"C:\GAMMA") if sys.platform == "win32" else Path(os.environ.get("MO2_ROOT", r"C:\GAMMA"))


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


def read_steps(ini_path: Path) -> list[str]:
    """Optional steps file; missing file → no steps."""
    if not ini_path.is_file():
        return []
    steps: list[str] = []
    for raw in ini_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        steps.append(line)
    return steps


def split_command(line: str) -> list[str]:
    if sys.platform == "win32":
        return shlex.split(line, posix=False)
    return shlex.split(line)


def resolve_command(argv: list[str], mo2_dir: Path, mo2_root: Path) -> list[str]:
    if not argv:
        return argv
    cmd0 = argv[0]
    cand = Path(cmd0)
    if cand.is_file():
        return [str(cand.resolve()), *argv[1:]]
    for base in (mo2_dir, mo2_root, Path.cwd()):
        hit = base / cmd0
        if hit.is_file():
            return [str(hit.resolve()), *argv[1:]]
    return argv


def run_step(line: str, mo2_dir: Path, mo2_root: Path) -> int:
    argv = resolve_command(split_command(line), mo2_dir, mo2_root)
    info(f"prelaunch: {subprocess.list2cmdline(argv) if sys.platform == 'win32' else ' '.join(argv)}")
    use_shell = sys.platform == "win32" and argv and argv[0].lower().endswith((".bat", ".cmd"))
    if use_shell:
        completed = subprocess.run(subprocess.list2cmdline(argv), shell=True, cwd=str(mo2_root))
    else:
        completed = subprocess.run(argv, cwd=str(mo2_root))
    return int(completed.returncode)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Optionally run mo2/prelaunch.steps, then launch the game."
    )
    p.add_argument("--mo2-root", default="", help="MO2 instance root (default: cwd if ModOrganizer.ini present)")
    p.add_argument(
        "--ini",
        "--steps",
        dest="steps_file",
        default="",
        help="Optional steps file (default: mo2/prelaunch.steps if present)",
    )
    p.add_argument("--dry-run", action="store_true", help="Print steps only")
    p.add_argument(
        "--then-launch",
        default="",
        metavar="EXE",
        help="After all steps succeed, launch this game exe",
    )
    p.add_argument("launch_args", nargs="*", help="Args passed to --then-launch")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    here = Path(__file__).resolve().parent
    mo2_dir = here.parent if here.name.lower() == "tools" else here
    mo2_root = resolve_mo2_root(args.mo2_root or None)
    # Prefer new name; fall back to legacy prelaunch.ini if user still has one.
    if args.steps_file:
        steps_path = Path(args.steps_file)
    else:
        steps_path = mo2_dir / "prelaunch.steps"
        if not steps_path.is_file():
            legacy = mo2_dir / "prelaunch.ini"
            if legacy.is_file():
                steps_path = legacy

    if not (mo2_root / "ModOrganizer.exe").is_file():
        err(f"ModOrganizer.exe not found under: {mo2_root}")
        err("Set MO2 Working Directory / Start in to the instance root")
        return 1

    info(f"MO2 root : {mo2_root}")
    steps = read_steps(steps_path)
    if steps:
        info(f"Steps    : {steps_path} ({len(steps)})")
    else:
        info("Steps    : (none)")
    for line in steps:
        if args.dry_run:
            info(f"dry-run: {line}")
            continue
        code = run_step(line, mo2_dir, mo2_root)
        if code != 0:
            err(f"prelaunch step failed ({code}): {line}")
            return code

    if not args.then_launch:
        return 0
    if args.dry_run:
        info(f"dry-run: then-launch {args.then_launch} {' '.join(args.launch_args)}")
        return 0
    exe = resolve_launch_exe(mo2_root, args.then_launch)
    launch = [str(exe), *args.launch_args]
    info(f"Launch: {subprocess.list2cmdline(launch) if sys.platform == 'win32' else ' '.join(launch)}")
    return subprocess.call(launch, cwd=str(mo2_root))


if __name__ == "__main__":
    raise SystemExit(main())
