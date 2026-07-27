#!/usr/bin/env python3
"""Emit FOMOD plugin metadata for a path mod from config/manifest.yml.

Prints shell assignments (eval-safe)::

  FEATURE_NAME='...'
  FEATURE_DESC='...'
  FEATURE_ID='fx_thirst'
  FEATURE_DEFAULT='Recommended'

Name = manifest key, desc = desc:, id = path with / → _ (same as package zip key).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO / "src" / "common" / "mo2" / "tools") not in sys.path:
    sys.path.insert(0, str(_REPO / "src" / "common" / "mo2" / "tools"))

import dogma_mo2_lib as lib  # noqa: E402


def _shell_quote(s: str) -> str:
    """Single-quote for POSIX eval (escape embedded quotes)."""
    return "'" + s.replace("'", "'\"'\"'") + "'"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--feature", required=True, help="Feature path e.g. fx/thirst")
    p.add_argument(
        "--manifest",
        default=str(_REPO / "config" / "manifest.yml"),
        help="manifest.yml path",
    )
    args = p.parse_args(argv)

    data = lib.load_manifest(Path(args.manifest))
    feat = data.resolve_feature_path(str(args.feature).strip())
    if not feat:
        print(f"feature_fomod_meta: unknown feature {args.feature!r}", file=sys.stderr)
        return 1
    meta = data.features[feat]
    dep = next(
        (
            d
            for d in data.mods
            if (d.path or "").replace("\\", "/") == feat
        ),
        None,
    )
    name = meta.display_name
    desc = (dep.desc if dep else "") or f"D.O.G.M.A. feature: {name}"
    fid = lib.feature_path_key(feat)
    default = "Recommended"

    sys.stdout.write(
        f"FEATURE_NAME={_shell_quote(name)}\n"
        f"FEATURE_DESC={_shell_quote(desc)}\n"
        f"FEATURE_ID={_shell_quote(fid)}\n"
        f"FEATURE_DEFAULT={_shell_quote(default)}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
