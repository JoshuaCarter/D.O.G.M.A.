#!/usr/bin/env python3
"""Pull live MCM diffs into config/manifest-*.yml ``mcm_set:`` blocks.

Uses the same baseline as DOGMA Backup (pristine G.A.M.M.A. MCM values, else
script ``def=``). Matched packs get their ``mcm_set:`` merged/updated in place
so surrounding comments and key order stay intact.

  py -3 tools/pull_mcm_to_manifest.py
  py -3 tools/pull_mcm_to_manifest.py --dry-run
  py -3 tools/pull_mcm_to_manifest.py --mo2-root C:/GAMMA
"""

from __future__ import annotations

import argparse
import re
import sys
from difflib import SequenceMatcher
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

_MANIFEST_NAMES = (
    "manifest-third-party.yml",
    "manifest-dogma-features.yml",
    "manifest-dogma-tweaks.yml",
)


def info(msg: str) -> None:
    print(msg)


def warn(msg: str) -> None:
    print(msg, file=sys.stderr)


def is_unbound_keybind(key: str, val: str) -> bool:
    leaf = key.split("/")[-1]
    return val.strip() == "-1" and bool(_KEYBIND_LEAF.match(leaf))


def _kv_list_to_map(raw) -> dict[str, str]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    if isinstance(raw, list):
        out: dict[str, str] = {}
        for item in raw:
            if isinstance(item, dict):
                out.update({str(k): str(v) for k, v in item.items()})
        return out
    return {}


def _yaml_quote(val: str) -> str:
    """Quote like existing manifest mcm_set values."""
    s = str(val)
    esc = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{esc}"'


def _format_mcm_set_block(mapping: dict[str, str], *, indent: str = "  ") -> str:
    lines = [f"{indent}mcm_set:"]
    for key in sorted(mapping.keys(), key=str.lower):
        lines.append(f"{indent}  - {key}: {_yaml_quote(mapping[key])}")
    return "\n".join(lines)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _pack_needles(pack_id: str, entry: dict) -> tuple[set[str], set[str]]:
    """Return (titles/folders, mcm_roots) used for matching."""
    titles: set[str] = {pack_id.strip().lower()}
    raw_en = entry.get("enables")
    if isinstance(raw_en, list):
        for item in raw_en:
            s = str(item).strip().lower()
            if s:
                titles.add(s)
    path = str(entry.get("path") or "").strip().lower()
    if path:
        titles.add(path)
        titles.add(path.rsplit("/", 1)[-1])
    roots: set[str] = set()
    for k in _kv_list_to_map(entry.get("mcm_set")):
        root = k.split("/", 1)[0].strip().lower()
        if root:
            roots.add(root)
    titles.discard("")
    roots.discard("")
    return titles, roots


def _best_pack_for_section(
    section: str,
    packs: dict[str, dict],
    *,
    key_roots: set[str],
) -> str | None:
    """Pick a manifest pack id for an MCM diff section (MO2 folder or root)."""
    slow = section.strip().lower()
    if not slow:
        return None
    # Never map the DOGMA mod folder via fuzzy title match.
    if slow in {"dogma", "d.o.g.m.a."}:
        return None

    slug_sec = _slug(section)
    exact: list[str] = []
    scored: list[tuple[int, str]] = []

    for pack_id, entry in packs.items():
        titles, roots = _pack_needles(pack_id, entry)
        pack_l = pack_id.strip().lower()
        slug_pack = _slug(pack_id)

        if slow == pack_l or slow in titles:
            exact.append(pack_id)
            continue

        # MCM root id (medik, idiots, painterofthezone, …) → pack that owns it.
        if slow in roots or (key_roots and key_roots <= roots):
            scored.append((100 + len(slow), pack_id))
            continue
        if len(key_roots) == 1:
            root = next(iter(key_roots))
            if root in roots:
                scored.append((90 + len(root), pack_id))
                continue

        # Slug / long substring (MO2 folder vs pack title).
        score = 0
        if slug_sec and slug_pack and len(slug_sec) >= 6:
            if slug_sec == slug_pack:
                score = 80
            elif slug_sec in slug_pack or slug_pack in slug_sec:
                score = 60 + min(len(slug_sec), len(slug_pack)) // 4
        if score == 0 and len(slow) >= 8:
            for title in titles:
                if len(title) < 8:
                    continue
                if slow in title or title in slow:
                    score = max(score, 50 + min(len(slow), len(title)) // 4)
        # Lead token (tarkov_transitions ↔ Tarkov-like …).
        if score == 0:
            tok_a = re.findall(r"[a-z0-9]+", slow)
            tok_b = re.findall(r"[a-z0-9]+", pack_l)
            if tok_a and tok_b and tok_a[0] == tok_b[0] and len(tok_a[0]) >= 5:
                score = 55
        # Camel/title initials (at → AlifeTactics, ap → AlifePlus).
        if score == 0 and 2 <= len(slow) <= 4 and slow.isalpha():
            camel = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", pack_id)
            initials = "".join(p[0].lower() for p in camel if p)
            if slow == initials:
                score = 70
                # Prefer CamelCase leaf ids over spaced radio parents.
                if " " not in pack_id and re.search(r"[a-z][A-Z]", pack_id):
                    score += 15
        # Near-equal slugs (Task vs Tasks).
        if score == 0 and slug_sec and slug_pack and min(len(slug_sec), len(slug_pack)) >= 12:
            ratio = SequenceMatcher(None, slug_sec, slug_pack).ratio()
            if ratio >= 0.86:
                score = int(70 * ratio)
        # Shared keyword tokens (quick_action_wheel ↔ Quick Action Wheels …).
        # Skip numbered MO2 folders (329- Fair Fast Travel …) — those are often
        # conflicts listed under disables, not the pack that owns the MCM.
        if score == 0 and not re.match(r"^\d+-", slow):
            stop = {
                "the", "and", "for", "with", "mod", "mcm", "by", "a", "of",
                "fast", "travel",  # too generic across FT mods
            }
            ta = {t for t in re.findall(r"[a-z0-9]{4,}", slow) if t not in stop}
            tb = {t for t in re.findall(r"[a-z0-9]{4,}", pack_l) if t not in stop}
            common = ta & tb
            if len(common) >= 2:
                score = 40 + 10 * len(common)
        if score:
            scored.append((score, pack_id))

    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        for pack_id in exact:
            if pack_id.strip().lower() == slow:
                return pack_id
        return sorted(exact, key=lambda s: (len(s), s.lower()))[0]
    if scored:
        scored.sort(key=lambda t: (-t[0], len(t[1]), t[1].lower()))
        # Require a clear winner (avoid near-ties across unrelated packs).
        if len(scored) == 1 or scored[0][0] >= scored[1][0] + 8:
            return scored[0][1]
    return None


def _top_level_key_spans(text: str) -> list[tuple[str, int, int]]:
    """Return (key, start, end) for each top-level YAML mapping entry."""
    lines = text.splitlines(keepends=True)
    # Offset of each line start.
    starts: list[int] = []
    pos = 0
    for line in lines:
        starts.append(pos)
        pos += len(line)

    keys: list[tuple[str, int]] = []  # key, line_index
    key_re = re.compile(r"^([^:\s#][^:]*)\s*:\s*(#.*)?$")
    for i, line in enumerate(lines):
        if line.startswith(" ") or line.startswith("\t"):
            continue
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = key_re.match(line.rstrip("\n\r"))
        if not m:
            continue
        keys.append((m.group(1).strip(), i))

    spans: list[tuple[str, int, int]] = []
    for idx, (key, line_i) in enumerate(keys):
        start = starts[line_i]
        if idx + 1 < len(keys):
            end = starts[keys[idx + 1][1]]
        else:
            end = len(text)
        spans.append((key, start, end))
    return spans


_MCM_SET_RE = re.compile(
    r"(?m)^(?P<indent>[ \t]*)mcm_set:\s*(?:#.*)?\n"
    r"(?P<body>(?:(?P=indent)[ \t]+.*\n)*)"
)


def _upsert_mcm_set_in_block(block: str, mapping: dict[str, str]) -> str:
    """Replace or insert ``mcm_set:`` inside one pack's YAML block."""
    if not mapping:
        return block
    new_block_body = _format_mcm_set_block(mapping, indent="  ") + "\n"
    m = _MCM_SET_RE.search(block)
    if m:
        return block[: m.start()] + new_block_body + block[m.end() :]

    # Insert after default:/stage: when present; else before trailing blank lines.
    lines = block.splitlines(keepends=True)
    if not lines:
        return block
    insert_at = 1  # after the pack key line
    for i, line in enumerate(lines):
        if re.match(r"^[ \t]+(?:stage|default):\s*", line):
            insert_at = i + 1
    # Prefer sitting with other effect fields: before enables/disables if those
    # appear first after stage/default — still fine after default.
    # Keep a blank line separation if the next line is blank.
    out = lines[:insert_at]
    # Ensure previous line ends with newline
    if out and not out[-1].endswith("\n"):
        out[-1] += "\n"
    out.append(new_block_body)
    out.extend(lines[insert_at:])
    return "".join(out)


def update_manifest_text(
    text: str,
    pack_updates: dict[str, dict[str, str]],
) -> tuple[str, list[str]]:
    """Apply mcm_set merges for pack ids. Returns (new_text, updated_ids)."""
    if not pack_updates:
        return text, []
    spans = _top_level_key_spans(text)
    # Rebuild from end so offsets stay valid.
    updated: list[str] = []
    new_text = text
    for key, start, end in reversed(spans):
        if key not in pack_updates:
            continue
        block = new_text[start:end]
        # Merge onto existing mcm_set in this block.
        existing: dict[str, str] = {}
        m = _MCM_SET_RE.search(block)
        if m:
            for line in m.group("body").splitlines():
                item = re.match(
                    r"^[ \t]+-\s*([^:]+?)\s*:\s*(.*?)\s*$", line
                )
                if not item:
                    continue
                k = item.group(1).strip()
                raw_v = item.group(2).strip()
                if (raw_v.startswith('"') and raw_v.endswith('"')) or (
                    raw_v.startswith("'") and raw_v.endswith("'")
                ):
                    raw_v = raw_v[1:-1]
                existing[k] = raw_v
        merged = dict(existing)
        merged.update(pack_updates[key])
        new_block = _upsert_mcm_set_in_block(block, merged)
        if new_block != block:
            new_text = new_text[:start] + new_block + new_text[end:]
            updated.append(key)
    updated.reverse()
    return new_text, updated


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


def load_pack_entries(cfg_dir: Path) -> dict[str, tuple[Path, dict]]:
    """pack_id → (manifest_path, entry dict)."""
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML required - run DOGMA Setup once") from exc

    packs: dict[str, tuple[Path, dict]] = {}
    for name in _MANIFEST_NAMES:
        path = cfg_dir / name
        if not path.is_file():
            continue
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            continue
        for key, meta in raw.items():
            if not isinstance(meta, dict):
                continue
            pack_id = str(key).strip()
            if not pack_id or pack_id in packs:
                continue
            packs[pack_id] = (path, meta)
    return packs


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
        help="Config dir with manifest-*.yml (default: <repo>/config)",
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
        help="Print planned merges; do not write",
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
        # Dev convenience: common GAMMA install path.
        fallback = Path("C:/GAMMA")
        if (fallback / "ModOrganizer.exe").is_file():
            mo2_root = fallback
        else:
            warn(str(exc))
            return 1

    info(f"MO2 root : {mo2_root}")
    info(f"Config   : {cfg_dir}")

    groups = backup.collect_mcm_diff(mo2_root)
    groups = filter_diff(
        groups,
        include_dogma=args.include_dogma,
        keep_unbound_keys=args.keep_unbound_keys,
    )
    if not groups:
        info("No MCM diffs to merge.")
        return 0

    packs = load_pack_entries(cfg_dir)
    if not packs:
        warn("No pack entries found in split manifests.")
        return 1

    # section → pack_id
    assignments: dict[str, str] = {}
    pack_merges: dict[str, dict[str, str]] = {}
    orphans: list[str] = []

    for section, pairs in sorted(groups.items(), key=lambda kv: kv[0].lower()):
        key_roots = {k.split("/", 1)[0].lower() for k in pairs}
        pack_id = _best_pack_for_section(
            section, {k: v for k, (_, v) in packs.items()}, key_roots=key_roots
        )
        if pack_id is None:
            orphans.append(section)
            continue
        assignments[section] = pack_id
        pack_merges.setdefault(pack_id, {}).update(pairs)

    for section, pack_id in assignments.items():
        n = len(groups[section])
        info(f"  {section!r} -> {pack_id!r} ({n} key(s))")
    if orphans:
        warn(f"Unmatched MCM groups ({len(orphans)}) - not written:")
        for section in orphans:
            warn(f"  - {section} ({len(groups[section])} key(s))")

    # Group updates by manifest file.
    by_file: dict[Path, dict[str, dict[str, str]]] = {}
    for pack_id, mapping in pack_merges.items():
        path, _entry = packs[pack_id]
        by_file.setdefault(path, {})[pack_id] = mapping

    if args.dry_run:
        info(f"Dry run - would update {len(pack_merges)} pack(s) in {len(by_file)} file(s).")
        return 0

    wrote = 0
    for path, updates in sorted(by_file.items(), key=lambda kv: kv[0].name.lower()):
        original = path.read_text(encoding="utf-8")
        new_text, changed = update_manifest_text(original, updates)
        if not changed:
            info(f"No text change: {path.name}")
            continue
        bak = path.with_suffix(path.suffix + ".bak")
        bak.write_text(original, encoding="utf-8", newline="\n")
        path.write_text(new_text, encoding="utf-8", newline="\n")
        info(f"Updated {path.name}: {', '.join(changed)}")
        info(f"  Backup: {bak.name}")
        wrote += 1

    info(f"Done ({wrote} file(s)).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError, OSError, RuntimeError) as exc:
        warn(str(exc))
        raise SystemExit(1) from exc
