#!/usr/bin/env python3
"""Print DOGMA feature paths from config/manifest.yml (one per line).

Used by tools/manifest_lib.sh for build.sh / package-fomod.sh.

  py -3 tools/list_manifest_features.py --min-level dev
  py -3 tools/list_manifest_features.py --min-level release
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_MO2 = _REPO / "src" / "common" / "mo2"
if str(_MO2) not in sys.path:
    sys.path.insert(0, str(_MO2))

import dogma_mo2_lib as lib  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="List DOGMA features from manifest.yml")
    p.add_argument(
        "--manifest",
        default=str(_REPO / "config" / "manifest.yml"),
        help="Path to manifest.yml",
    )
    p.add_argument(
        "--min-level",
        default="dev",
        help="Include features with level >= this (off|dev|release, or 0|1|2)",
    )
    p.add_argument(
        "--check-src",
        action="store_true",
        help="Fail if feature path is missing under src/",
    )
    args = p.parse_args()

    path = Path(args.manifest)
    data = lib.load_manifest(path)
    min_level = lib.parse_level(args.min_level)
    src = _REPO / "src"
    for feat, meta in sorted(data.features.items()):
        if meta.always_on:
            continue
        if not lib.level_meets(meta.level, min_level):
            continue
        if args.check_src and not (src / feat).is_dir():
            print(f"manifest: entry missing under src/: {feat}", file=sys.stderr)
            return 1
        print(feat)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
