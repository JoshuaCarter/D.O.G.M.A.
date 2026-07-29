#!/usr/bin/env python3
"""Print FOMOD 'Requires:' blurb for a feature path (from features.yml depends)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO / "src" / "common" / "mo2" / "tools") not in sys.path:
    sys.path.insert(0, str(_REPO / "src" / "common" / "mo2" / "tools"))

import dogma_mo2_lib as lib  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--feature", required=True, help="Feature path e.g. travel/true_fast_travel")
    p.add_argument(
        "--manifest",
        default=str(_REPO / "config"),
        help="Config dir or manifest catalog path",
    )
    args = p.parse_args(argv)
    data = lib.load_manifest(Path(args.manifest))
    feat = data.resolve_feature_path(str(args.feature).strip())
    if not feat:
        return 0
    meta = data.features[feat]
    if not meta.depends:
        return 0
    packs = ", ".join(meta.depends)
    print(
        f"Requires: {packs} - install via DOGMA Setup / Dependencies "
        f"after FOMOD (same ModDB/manual pipeline as other packs)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
