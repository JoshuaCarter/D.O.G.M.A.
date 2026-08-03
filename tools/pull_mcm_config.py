#!/usr/bin/env python3
"""Pull live MCM diffs into ``config/mcm_config.yml``.

Uses ``dogma_backup.collect_mcm_diff`` unchanged (pristine G.A.M.M.A. + mod
script defaults in load order). Groups by MO2 mod folder when resolvable;
everything else goes under ``other:`` (alphabetical keys).

Does **not** write pack-level ``mcm_set:`` — keep those only for true 1:1
pack↔MCM ownership in the manifests.

  py -3 tools/pull_mcm_config.py
  py -3 tools/pull_mcm_config.py --dry-run
  py -3 tools/pull_mcm_config.py --mo2-root C:/GAMMA
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_MO2_TOOLS = _REPO / "src" / "_common" / "mo2" / "tools"
if str(_MO2_TOOLS) not in sys.path:
    sys.path.insert(0, str(_MO2_TOOLS))

import dogma_backup as backup  # noqa: E402
import dogma_mo2_lib as lib  # noqa: E402

_KEYBIND_LEAF = re.compile(
    r"(?i)^(?:key(?:_.+)?|.+_key|keybind(?:_.+)?|.+_keybind|key_bind(?:_.+)?|"
    r".+_key_bind|hotkey(?:_.+)?|.+_hotkey|bind(?:_.+)?|.+_bind|second_key)$"
)


def info(msg: str) -> None:
    print(msg)


def warn(msg: str) -> None:
    print(msg, file=sys.stderr)


def is_unbound_keybind(key: str, val: str) -> bool:
    leaf = key.split("/")[-1]
    return val.strip() == "-1" and bool(_KEYBIND_LEAF.match(leaf))


def filter_diff(
    groups: dict[str, dict[str, str]],
    *,
    include_dogma: bool,
    keep_unbound_keys: bool,
) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    skipped_dogma = skipped_unbound = 0
    for section, pairs in groups.items():
        kept: dict[str, str] = {}
        for key, val in pairs.items():
            if not include_dogma and key.lower().startswith("dogma/"):
                skipped_dogma += 1
                continue
            if not keep_unbound_keys and is_unbound_keybind(key, val):
                skipped_unbound += 1
                continue
            kept[key] = val
        if kept:
            out[section] = kept
    info(
        f"Filtered: kept {sum(len(v) for v in out.values())} key(s) in "
        f"{len(out)} group(s); skipped dogma={skipped_dogma} "
        f"unbound_keys={skipped_unbound}"
    )
    return out


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _mod_folders(mods_dir: Path) -> list[str]:
    if not mods_dir.is_dir():
        return []
    return sorted(
        (p.name for p in mods_dir.iterdir() if p.is_dir()),
        key=str.lower,
    )


def resolve_mod_group(
    section: str,
    mods_dir: Path,
    folders: list[str],
) -> str | None:
    """Map a diff section to an MO2 mod folder name, or None → ``other``."""
    if not section or section == "other":
        return None
    if (mods_dir / section).is_dir():
        return section

    slow = section.lower()
    for name in folders:
        if name.lower() == slow:
            return name

    slug_sec = _slug(section)
    if len(slug_sec) < 4:
        return None

    hits: list[str] = []
    for name in folders:
        slug_name = _slug(name)
        if not slug_name:
            continue
        if slug_sec == slug_name or (
            len(slug_sec) >= 5
            and (slug_sec in slug_name or slug_name in slug_sec)
        ):
            hits.append(name)
    if len(hits) == 1:
        return hits[0]
    return None


def organize_for_config(
    groups: dict[str, dict[str, str]],
    mo2_root: Path,
) -> dict[str, dict[str, str]]:
    """Bucket diff groups by mod folder; leftovers → ``other``."""
    mods_dir = mo2_root / "mods"
    folders = _mod_folders(mods_dir)
    organized: dict[str, dict[str, str]] = {}
    other: dict[str, str] = {}

    for section, pairs in groups.items():
        mod_name = resolve_mod_group(section, mods_dir, folders)
        if mod_name is None:
            other.update(pairs)
            continue
        organized.setdefault(mod_name, {}).update(pairs)

    if other:
        organized["other"] = other
    return organized


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--mo2-root",
        default="",
        help="MO2 instance root (default: MO2_ROOT / detect)",
    )
    p.add_argument(
        "--config",
        default="",
        help="Config dir for mcm_config.yml (default: <repo>/config)",
    )
    p.add_argument(
        "--include-dogma",
        action="store_true",
        help="Include dogma/* MCM keys (excluded by default)",
    )
    p.add_argument(
        "--keep-unbound-keys",
        action="store_true",
        help="Keep keybind options that are already -1",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned groups; do not write",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg_dir = Path(args.config) if args.config else _REPO / "config"
    if not cfg_dir.is_dir():
        warn(f"Config dir not found: {cfg_dir}")
        return 1

    try:
        mo2_root = lib.resolve_mo2_root(args.mo2_root or None)
    except FileNotFoundError as exc:
        fallback = Path("C:/GAMMA")
        if (fallback / "ModOrganizer.exe").is_file():
            mo2_root = fallback
        else:
            warn(str(exc))
            return 1

    dest = lib.mcm_config_path(cfg_dir)
    info(f"MO2 root : {mo2_root}")
    info(f"Config   : {dest}")

    groups = backup.collect_mcm_diff(mo2_root)
    groups = filter_diff(
        groups,
        include_dogma=args.include_dogma,
        keep_unbound_keys=args.keep_unbound_keys,
    )
    if not groups:
        info("No MCM diffs to write.")
        return 0

    organized = organize_for_config(groups, mo2_root)
    other_n = len(organized.get("other", {}))
    mod_n = len(organized) - (1 if other_n else 0)
    info(
        f"Organized: {sum(len(v) for v in organized.values())} key(s) across "
        f"{mod_n} mod group(s)"
        + (f" + other ({other_n} key(s))" if other_n else "")
    )
    for name in sorted(organized, key=lambda s: (s == "other", s.lower())):
        if name == "other":
            continue
        info(f"  {name}: {len(organized[name])} key(s)")
    if other_n:
        info(f"  other: {other_n} key(s)")

    text = lib.format_mcm_config_yaml(organized)
    if args.dry_run:
        info("Dry run - would write:")
        print(text[:2000] + ("...\n" if len(text) > 2000 else ""))
        return 0

    if dest.is_file():
        bak = dest.with_name(f"{dest.stem}.back{dest.suffix}")
        bak.write_text(dest.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
        info(f"Backup: {bak.name}")

    dest.write_text(text, encoding="utf-8", newline="\n")
    info(f"Wrote {dest}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError, OSError, RuntimeError) as exc:
        warn(str(exc))
        raise SystemExit(1) from exc
