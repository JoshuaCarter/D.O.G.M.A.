#!/usr/bin/env python3
"""Run ALAO on local DOGMA src/ (same fix flags as DOGMA Optimize).

  py -3 tools/alao_local.py
  python3 tools/alao_local.py --dry-run

Used by tools/build.sh and the git pre-commit hook. Skip via DOGMA_NO_ALAO=1
in the environment (build only checks that; this CLI always runs when invoked).

Clears *.alao-bak under the target before --fix so authoring src/ is always
re-processed (git is the undo). Full-mod Optimize does not use this path.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_MO2 = _REPO / "src" / "_common" / "mo2" / "tools"
if str(_MO2) not in sys.path:
    sys.path.insert(0, str(_MO2))

import dogma_mo2_lib as lib  # noqa: E402
import dogma_optimize as optimize  # noqa: E402


def _clean_alao_backups(root: Path) -> int:
    """Remove ALAO skip-markers so --fix can touch every script again."""
    deleted = 0
    for bak in root.rglob("*.alao-bak"):
        try:
            bak.unlink()
            deleted += 1
        except OSError as e:
            lib.err(f"Could not delete {bak}: {e}")
    return deleted


def main() -> int:
    p = argparse.ArgumentParser(
        description="ALAO on DOGMA src/ (same flags as Optimize, --direct)",
    )
    p.add_argument(
        "--path",
        type=Path,
        default=_REPO / "src",
        help="Script tree to process (default: repo src/)",
    )
    p.add_argument(
        "--report",
        type=Path,
        default=_REPO / "build" / "alao_report.html",
        help="HTML report path (default: build/alao_report.html)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve ALAO and print target only",
    )
    args = p.parse_args()

    target = args.path.expanduser().resolve()
    if not args.dry_run:
        n = _clean_alao_backups(target)
        if n:
            lib.info(f"Removed {n} stale ALAO backup(s) under {target}")
    return optimize.run_alao(
        target,
        report=args.report.expanduser().resolve(),
        exclude_lines=["VANILLA_SCRIPTS"],
        direct=True,
        dry_run=bool(args.dry_run),
    )


if __name__ == "__main__":
    raise SystemExit(main())
