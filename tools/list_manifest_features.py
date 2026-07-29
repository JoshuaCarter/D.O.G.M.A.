#!/usr/bin/env python3
"""Print DOGMA path-mod paths from config manifests (one per line).

Used by tools/manifest_lib.sh for build.sh / package-fomod.sh.

  py -3 tools/list_manifest_features.py --min-stage local
  py -3 tools/list_manifest_features.py --min-stage release
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_MO2 = _REPO / "src" / "common" / "mo2" / "tools"
if str(_MO2) not in sys.path:
    sys.path.insert(0, str(_MO2))

import dogma_mo2_lib as lib  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(
        description="List DOGMA path mods (fomod gate) from manifest catalog"
    )
    p.add_argument(
        "--manifest",
        default=str(_REPO / "config"),
        help="Config dir or manifest-*.yml / manifest.yml / features.yml",
    )
    p.add_argument(
        "--min-stage",
        "--min-level",
        "--min-fomod",
        dest="min_stage",
        default="local",
        help="Include path mods with stage >= this (omit|dev|release; local=dev)",
    )
    p.add_argument(
        "--check-src",
        action="store_true",
        help="Fail if feature path is missing under src/",
    )
    args = p.parse_args()

    path = Path(args.manifest)
    data = lib.load_manifest(path)
    min_stage = lib.parse_stage(args.min_stage)
    src = _REPO / "src"
    for feat, meta in sorted(data.features.items()):
        if meta.always_on:
            continue
        if not lib.stage_meets(meta.stage, min_stage):
            continue
        if args.check_src and not (src / feat).is_dir():
            print(f"manifest: entry missing under src/: {feat}", file=sys.stderr)
            return 1
        print(feat)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
