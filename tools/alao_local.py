#!/usr/bin/env python3
"""Run ALAO on local DOGMA src/.

  py -3 tools/alao_local.py
  python3 tools/alao_local.py --dry-run

Used by tools/build.sh and the git pre-commit hook. Skip via DOGMA_NO_ALAO=1
in the environment (build only checks that; this CLI always runs when invoked).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
ALAO_URL = (
    "https://www.moddb.com/mods/stalker-anomaly/addons/"
    "alao-anomaly-lua-auto-optimizer-tool"
)


def _info(msg: str) -> None:
    print(msg, flush=True)


def _err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def resolve_alao_root() -> Path | None:
    env = (os.environ.get("ALAO_PATH") or "").strip()
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env))
    candidates.append(Path(r"c:\gamma_dev\ALAO"))
    candidates.append(_REPO.parent / "ALAO")
    for c in candidates:
        if (c / "stalker_lua_lint.py").is_file():
            return c.resolve()
    return None


def ensure_alao_deps(alao_root: Path) -> int:
    try:
        import jinja2  # noqa: F401
        import luaparser  # noqa: F401

        return 0
    except ImportError:
        pass
    req = alao_root / "requirements.txt"
    if not req.is_file():
        _err(f"ALAO is missing its requirements file:\n  {req}")
        return 1
    _info("Installing ALAO dependencies (one-time)…")
    return int(subprocess.call([sys.executable, "-m", "pip", "install", "-r", str(req)]))


def _clean_alao_backups(root: Path) -> int:
    deleted = 0
    for bak in root.rglob("*.alao-bak"):
        try:
            bak.unlink()
            deleted += 1
        except OSError as e:
            _err(f"Could not delete {bak}: {e}")
    return deleted


def run_alao(target: Path, *, report: Path, dry_run: bool) -> int:
    alao = resolve_alao_root()
    if alao is None:
        _err(
            "Could not find ALAO (Anomaly Lua Auto Optimizer).\n"
            f"Download: {ALAO_URL}\n"
            "Set ALAO_PATH or put it at c:\\gamma_dev\\ALAO."
        )
        return 1
    target = target.resolve()
    if not target.exists():
        _err(f"ALAO target does not exist:\n  {target}")
        return 1
    report.parent.mkdir(parents=True, exist_ok=True)
    if dry_run:
        _info(f"Dry run - would run ALAO on:\n  {target}")
        return 0
    code = ensure_alao_deps(alao)
    if code:
        return code
    exclude_path: Path | None = None
    cmd = [
        sys.executable,
        str(alao / "stalker_lua_lint.py"),
        str(target),
        "--fix",
        "--fix-nil",
        "--remove-dead-code",
        "--no-first-time-auto-backup",
        "--direct",
        "--report",
        str(report),
    ]
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".txt",
        prefix="dogma_alao_exclude_",
        delete=False,
    ) as tf:
        tf.write("VANILLA_SCRIPTS\n")
        exclude_path = Path(tf.name)
    cmd.extend(["--exclude", str(exclude_path)])
    _info(f"Running ALAO…\n  {ALAO_URL}")
    try:
        return int(subprocess.call(cmd, cwd=str(alao)))
    finally:
        if exclude_path is not None:
            try:
                exclude_path.unlink(missing_ok=True)
            except OSError:
                pass


def main() -> int:
    p = argparse.ArgumentParser(description="ALAO on DOGMA src/")
    p.add_argument("--path", type=Path, default=_REPO / "src")
    p.add_argument("--report", type=Path, default=_REPO / "build" / "alao_report.html")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    target = args.path.expanduser().resolve()
    if not args.dry_run:
        n = _clean_alao_backups(target)
        if n:
            _info(f"Removed {n} stale ALAO backup(s) under {target}")
    return run_alao(target, report=args.report.expanduser().resolve(), dry_run=bool(args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
