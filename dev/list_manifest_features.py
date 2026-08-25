#!/usr/bin/env python3
"""Print DOGMA path-mod paths from config manifests (one per line).

Used by dev/manifest_lib.sh for build.sh / package-fomod.sh.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from manifest import iter_path_features, parse_stage, src_feature_dir, stage_meets

_REPO = Path(__file__).resolve().parent.parent


def main() -> int:
    p = argparse.ArgumentParser(description="List DOGMA path mods from manifest catalog")
    p.add_argument(
        "--manifest",
        default=str(_REPO),
        help="Repo root or path to manifest.yml",
    )
    p.add_argument(
        "--min-stage",
        "--min-level",
        "--min-fomod",
        dest="min_stage",
        default="local",
        help="Include path mods with stage >= this (omit|local|beta|gold)",
    )
    p.add_argument(
        "--check-src",
        action="store_true",
        help="Fail if feature path is missing under src/",
    )
    args = p.parse_args()

    config = Path(args.manifest)
    min_stage = parse_stage(args.min_stage)
    src = _REPO / "src"
    for feat, stage in iter_path_features(config):
        if not stage_meets(stage, min_stage):
            continue
        if args.check_src and not (src / src_feature_dir(feat)).is_dir():
            print(f"manifest: entry missing under src/: {src_feature_dir(feat)} ({feat})", file=sys.stderr)
            return 1
        print(feat)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
