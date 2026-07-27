#!/usr/bin/env python3
"""Emit FOMOD plugin metadata for a path mod from config/manifest.yml.

Prints shell assignments (eval-safe)::

  FEATURE_NAME='...'
  FEATURE_DESC='...'
  FEATURE_ID='fx_thirst'
  FEATURE_DEFAULT='Recommended'

Name = manifest key, desc = desc: plus the same effect lists as Setup tooltips
(Installs / Disables / Enables / … / Requires). id = path with / → _.
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


def _fomod_effect_text(data: lib.ManifestData, feat: str, meta: lib.FeatureMeta) -> str:
    sections: list[tuple[str, list[str]]] = []
    zname = f"{lib.feature_path_key(feat)}.zip"
    sections.append(("Installs", [f"local package {zname} ({feat})"]))
    if meta.depends:
        sections.append(("Depends", list(meta.depends)))
    sections.extend(lib.preview_feature_effect_sections(meta))
    try:
        deps = data.feature_pack_deps(feat)
    except Exception:
        deps = []
    if deps:
        pack_by_id = {d.id: d for d in data.mods}
        installs = lib.preview_install_packs(deps, pack_by_id)
        if installs:
            sections.append(("Installs (depends)", installs))
        for label, items in lib.preview_effect_sections(deps):
            sections.append((label, items))
    return lib.format_effect_lists_plain(sections)


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
    effects = _fomod_effect_text(data, feat, meta)
    if effects:
        desc = f"{desc.rstrip()}\n\n{effects}"
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
