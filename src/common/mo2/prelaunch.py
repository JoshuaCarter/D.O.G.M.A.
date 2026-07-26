#!/usr/bin/env python3
"""Run mods/DOGMA/mo2/prelaunch.ini steps, optionally then launch the game.

Register mods/DOGMA/mo2/DOGMA.bat in MO2 Executables:
  Start in  = instance root (C:\\GAMMA)
  Arguments = --then-launch AnomalyDX11AVX.exe
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
    if not ini_path.is_file():
        raise FileNotFoundError(f"prelaunch.ini not found: {ini_path}")
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
        raise ValueError("empty command")
    prog = argv[0]
    p = Path(prog)
    candidates: list[Path] = []
    if p.is_absolute():
        candidates.append(p)
    else:
        candidates.append(mo2_dir / prog)
        candidates.append(mo2_root / prog)
        candidates.append(Path.cwd() / prog)
    resolved = None
    for c in candidates:
        if c.is_file():
            resolved = c.resolve()
            break
    if resolved is None:
        # Allow bare exe names on PATH (py, python, …)
        return argv
    out = [str(resolved), *argv[1:]]
    return out


def run_step(line: str, mo2_dir: Path, mo2_root: Path) -> int:
    argv = split_command(line)
    argv = resolve_command(argv, mo2_dir, mo2_root)
    info(f"prelaunch: {' '.join(argv)}")
    # .bat/.cmd need a shell on Windows
    use_shell = sys.platform == "win32" and Path(argv[0]).suffix.lower() in {".bat", ".cmd"}
    if use_shell:
        completed = subprocess.run(subprocess.list2cmdline(argv), shell=True, cwd=str(mo2_root))
    else:
        completed = subprocess.run(argv, cwd=str(mo2_root))
    return int(completed.returncode)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run DOGMA mo2/prelaunch.ini then optionally launch the game.")
    p.add_argument("--mo2-root", default="", help="MO2 instance root (default: cwd if ModOrganizer.ini present)")
    p.add_argument(
        "--ini",
        default="",
        help="Path to prelaunch.ini (default: next to this script)",
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
    mo2_dir = Path(__file__).resolve().parent
    mo2_root = resolve_mo2_root(args.mo2_root or None)
    ini_path = Path(args.ini) if args.ini else mo2_dir / "prelaunch.ini"

    if not (mo2_root / "ModOrganizer.exe").is_file():
        err(f"ModOrganizer.exe not found under: {mo2_root}")
        err("Set MO2 Working Directory / Start in to the instance root")
        return 1

    info(f"MO2 root : {mo2_root}")
    info(f"Ini      : {ini_path}")
    steps = read_steps(ini_path)
    if not steps:
        warn("prelaunch.ini has no steps")
    for line in steps:
        if args.dry_run:
            info(f"dry-run: {line}")
            continue
        code = run_step(line, mo2_dir, mo2_root)
        if code != 0:
            err(f"prelaunch step failed ({code}): {line}")
            return code

    if args.then_launch:
        if args.dry_run:
            warn(f"Dry run: skipping launch of {args.then_launch}")
            return 0
        exe = resolve_launch_exe(mo2_root, args.then_launch)
        launch_args = [str(exe), *args.launch_args]
        info(f"Launching: {' '.join(launch_args)}")
        completed = subprocess.run(launch_args, check=False)
        return int(completed.returncode)

    ok("prelaunch done.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError, RuntimeError, OSError) as exc:
        err(str(exc))
        raise SystemExit(1) from exc
