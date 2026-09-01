#!/usr/bin/env python3
"""Build dogma_sfx_prefetch.ltx from enabled MO2 mods' loose sounds.

Run from the DOGMA mod folder (or pass --mo2-root). Writes:

  overwrite/gamedata/configs/dogma_sfx_prefetch.ltx

Asks for a max .ogg size (10 / 25 / 50 / 75 / 100 KB, default 50) and fully
replaces overwrite/gamedata/configs/dogma_sfx_prefetch.ltx.

  py -3 dogma_sfx_prefetch.py
  py -3 dogma_sfx_prefetch.py --max-size 50kb
  py -3 dogma_sfx_prefetch.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path


DOGMA_MOD_NAME = "DOGMA"
LEGACY_SEPARATE_MOD = "DOGMA - Sound Prefetch"
# Only .ogg - X-Ray sound attrs live in Vorbis comments (wav/flac are not prefetched).
SOUND_EXTS = {".ogg"}
SECTION = "dogma_sfx_list"
LTX_NAME = "dogma_sfx_prefetch.ltx"
LTX_REL = Path("gamedata") / "configs" / LTX_NAME
# Engine accepts comment versions 1, 2, and OGG_COMMENT_VERSION (3). See SoundRender_Source_loader.
XRAY_OGG_COMMENT_VERSIONS = {1, 2, 3}
XRAY_OGG_COMMENT_MIN_LEN = {
    1: 16,  # vers + min + max + gametype
    2: 20,  # + base volume
    3: 24,  # + max AI dist
}
# Skip files at/above this size (music / long ambience).
SIZE_CHOICES: tuple[tuple[str, int, str], ...] = (
    ("10kb", 10 * 1024, "10 KB"),
    ("25kb", 25 * 1024, "25 KB"),
    ("50kb", 50 * 1024, "50 KB (recommended)"),
    ("75kb", 75 * 1024, "75 KB"),
    ("100kb", 100 * 1024, "100 KB"),
)
SIZE_BY_KEY = {key: (limit, label) for key, limit, label in SIZE_CHOICES}
DEFAULT_SIZE_KEY = "50kb"
DEFAULT_SIZE_IDX = next(i for i, (key, _, _) in enumerate(SIZE_CHOICES, start=1) if key == DEFAULT_SIZE_KEY)


def _use_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            return False
    return sys.stdout.isatty()


_COLOR = _use_color()


def info(msg: str) -> None:
    print(msg, flush=True)


def ok(msg: str) -> None:
    print(f"\033[32m{msg}\033[0m" if _COLOR else msg, flush=True)


def warn(msg: str) -> None:
    print(f"\033[33m{msg}\033[0m" if _COLOR else msg, flush=True)


def err(msg: str) -> None:
    print(f"\033[31m{msg}\033[0m" if _COLOR else msg, file=sys.stderr, flush=True)


def read_mo2_ini_value(ini_path: Path, key: str) -> str:
    if not ini_path.is_file():
        raise FileNotFoundError(f"ModOrganizer.ini not found: {ini_path}")
    prefix = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for raw in ini_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not prefix.match(raw):
            continue
        m = re.search(r"@ByteArray\((.+)\)\s*$", raw)
        if m:
            value = m.group(1)
        else:
            value = raw.split("=", 1)[1].strip()
        return value.replace("\\\\", "\\")
    raise ValueError(f"{key} not found in {ini_path}")


def read_text_lines(path: Path) -> list[str]:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    text = raw.decode("utf-8", errors="replace")
    if text.endswith("\n"):
        text = text[:-1]
        if text.endswith("\r"):
            text = text[:-1]
    if not text:
        return []
    return text.splitlines()


def write_text_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = "\r\n".join(lines) + "\r\n"
    path.write_bytes(data.encode("utf-8"))


def is_mo2_root(path: Path) -> bool:
    return (path / "ModOrganizer.exe").is_file() or (path / "ModOrganizer.ini").is_file()


def walk_up_for_mo2(start: Path) -> Path | None:
    cur = start.resolve()
    for path in (cur, *cur.parents):
        if is_mo2_root(path):
            return path
    return None


def resolve_mo2_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    env = (os.environ.get("MO2_ROOT") or "").strip()
    if env:
        return Path(env).expanduser()
    found = walk_up_for_mo2(Path.cwd()) or walk_up_for_mo2(Path(__file__).resolve().parent)
    if found:
        return found
    raise FileNotFoundError(
        "Could not find the MO2 instance root. Run this script from the DOGMA "
        "mod folder (or the MO2 root), pass --mo2-root, or set MO2_ROOT."
    )


def list_enabled_mod_names(modlist_path: Path) -> list[str]:
    names: list[str] = []
    for line in read_text_lines(modlist_path):
        if line.startswith("+"):
            name = line[1:]
            if name and "separator" not in name.lower():
                names.append(name)
    return names


def find_dogma_mod_dir(mo2_root: Path, enabled_mods: list[str]) -> Path | None:
    here = Path(__file__).resolve().parent
    if here.name.lower() == DOGMA_MOD_NAME.lower() and here.is_dir():
        return here
    mods_dir = mo2_root / "mods"
    exact = mods_dir / DOGMA_MOD_NAME
    if exact.is_dir():
        return exact
    for name in enabled_mods:
        if name.lower() == DOGMA_MOD_NAME.lower():
            candidate = mods_dir / name
            if candidate.is_dir():
                return candidate
    return None


def sound_rel_path(sounds_root: Path, file_path: Path) -> str | None:
    try:
        rel = file_path.relative_to(sounds_root)
    except ValueError:
        return None
    if rel.suffix.lower() not in SOUND_EXTS:
        return None
    stem = rel.with_suffix("")
    return str(stem).replace("/", "\\")


def _vorbis_user_comments(data: bytes) -> list[bytes] | None:
    """Return Vorbis user-comment payloads, or None if no comment packet."""
    idx = 0
    while True:
        idx = data.find(b"vorbis", idx)
        if idx < 0:
            return None
        if idx >= 1 and data[idx - 1] == 3:  # packet type 3 = comment header
            break
        idx += 6
    else:
        return None

    pos = idx + 6
    if pos + 4 > len(data):
        return None
    vend_len = int.from_bytes(data[pos : pos + 4], "little")
    pos += 4
    if vend_len < 0 or pos + vend_len + 4 > len(data):
        return None
    pos += vend_len
    n = int.from_bytes(data[pos : pos + 4], "little")
    pos += 4
    if n < 0 or n > 256:
        return None
    comments: list[bytes] = []
    for _ in range(n):
        if pos + 4 > len(data):
            return None
        cl = int.from_bytes(data[pos : pos + 4], "little")
        pos += 4
        if cl < 0 or pos + cl > len(data):
            return None
        comments.append(data[pos : pos + cl])
        pos += cl
    return comments


def has_valid_xray_ogg_comment(path: Path) -> bool:
    """True if first Vorbis user-comment is an X-Ray sound-attr blob (vers 1/2/3)."""
    try:
        # Comment header is in the first pages; avoid reading multi‑MB ambience whole-file.
        with path.open("rb") as f:
            data = f.read(256 * 1024)
    except OSError:
        return False
    comments = _vorbis_user_comments(data)
    if not comments:
        return False
    first = comments[0]
    if len(first) < 4:
        return False
    vers = int.from_bytes(first[0:4], "little")
    if vers not in XRAY_OGG_COMMENT_VERSIONS:
        return False
    return len(first) >= XRAY_OGG_COMMENT_MIN_LEN[vers]


def drop_large_files(
    entries: list[tuple[str, Path]],
    min_bytes: int | None,
) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    """Keep entries strictly below min_bytes. None keeps all.

    Returns (kept [(rel, size), ...], dropped [(rel, size), ...]).
    """
    kept: list[tuple[str, int]] = []
    dropped: list[tuple[str, int]] = []
    for rel, path in entries:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if min_bytes is not None and size >= min_bytes:
            dropped.append((rel, size))
        else:
            kept.append((rel, size))
    return kept, dropped


def collect_sound_paths(
    mo2_root: Path,
    enabled_mods: list[str],
    *,
    max_bytes: int | None,
) -> tuple[list[tuple[str, int]], int, int, list[tuple[str, int]]]:
    """Return (kept[(rel, bytes)], scanned_files, skipped_invalid_ogg, dropped_large)."""
    # key -> (rel_path, file_path); later (higher priority) wins
    found: dict[str, tuple[str, Path]] = {}
    mods_dir = mo2_root / "mods"
    skip = {LEGACY_SEPARATE_MOD.lower()}
    scanned = 0

    def consider(sounds_root: Path) -> None:
        nonlocal scanned
        if not sounds_root.is_dir():
            return
        for path in sounds_root.rglob("*.ogg"):
            if not path.is_file():
                continue
            scanned += 1
            rel = sound_rel_path(sounds_root, path)
            if not rel or rel.startswith("$"):
                continue
            found[rel.lower()] = (rel, path)

    to_scan = [n for n in reversed(enabled_mods) if n.lower() not in skip]
    info(f"Scanning {len(to_scan)} mods for gamedata/sounds/*.ogg ...")
    for name in to_scan:
        consider(mods_dir / name / "gamedata" / "sounds")
    consider(mo2_root / "overwrite" / "gamedata" / "sounds")
    info(f"Found {len(found)} unique paths ({scanned} .ogg scanned)")

    info(f"Checking X-Ray ogg-comments on {len(found)} unique paths ...")
    valid: list[tuple[str, Path]] = []
    skipped = 0
    for rel, path in found.values():
        if has_valid_xray_ogg_comment(path):
            valid.append((rel, path))
        else:
            skipped += 1
    info(f"Valid X-Ray comment: {len(valid)} (skipped invalid: {skipped})")

    kept, dropped_large = drop_large_files(valid, max_bytes)
    if dropped_large and max_bytes is not None:
        warn(f"Dropping {len(dropped_large)} files >= {max_bytes // 1024} KB")
    kept.sort(key=lambda t: t[0].lower())
    return kept, scanned, skipped, dropped_large


def format_ltx(entries: list[tuple[str, int]], *, size_label: str) -> tuple[list[str], int]:
    """Build a full LTX (replaces any previous list). Returns (lines, total_bytes)."""
    lines = [
        f"; dogma_sfx_prefetch - max size {size_label}",
        f"[{SECTION}]",
    ]
    total_bytes = 0
    for i, (path, size) in enumerate(entries, start=1):
        total_bytes += size
        lines.append(f"t{i} = {path}")
    return lines, total_bytes


def prompt_max_size() -> str:
    info("Max .ogg size to prefetch:")
    for i, (_key, _limit, label) in enumerate(SIZE_CHOICES, start=1):
        info(f"  {i}) {label}")
    while True:
        try:
            raw = input(f"Choice [1-{len(SIZE_CHOICES)}, default {DEFAULT_SIZE_IDX}]: ").strip().lower()
        except EOFError:
            return DEFAULT_SIZE_KEY
        if not raw:
            return DEFAULT_SIZE_KEY
        if raw in SIZE_BY_KEY:
            return raw
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(SIZE_CHOICES):
                return SIZE_CHOICES[idx - 1][0]
        keys = " / ".join(key for key, _limit, _label in SIZE_CHOICES)
        warn(f"Enter 1-{len(SIZE_CHOICES)}, or {keys}.")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Scan enabled MO2 mods for loose sounds and write dogma_sfx_prefetch.ltx "
            "into overwrite/gamedata. Run from the DOGMA mod folder."
        )
    )
    p.add_argument(
        "--mo2-root",
        default="",
        help="MO2 instance folder (default: walk up from cwd / this script)",
    )
    p.add_argument("--profile", default="", help="MO2 profile (default: selected_profile)")
    p.add_argument("--dry-run", action="store_true", help="Scan and report; write nothing")
    p.add_argument(
        "--force",
        action="store_true",
        help="Accepted for D.O.G.M.A. Optimize CLI compatibility (rebuild is always forced)",
    )
    p.add_argument(
        "--max-size",
        choices=[key for key, _limit, _label in SIZE_CHOICES],
        default="",
        help="Max .ogg size to include (default: prompt, or 50kb if not a TTY)",
    )
    return p.parse_args(argv)


def resolve_size_key(args: argparse.Namespace) -> str:
    if args.max_size:
        return args.max_size
    if sys.stdin.isatty() and sys.stdout.isatty():
        return prompt_max_size()
    return DEFAULT_SIZE_KEY


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    mo2_root = resolve_mo2_root(args.mo2_root or None)

    if not is_mo2_root(mo2_root):
        err(f"Not an MO2 instance root: {mo2_root}")
        err("Run from the DOGMA mod folder, or pass --mo2-root")
        return 1

    profile = args.profile or read_mo2_ini_value(mo2_root / "ModOrganizer.ini", "selected_profile")
    modlist_path = mo2_root / "profiles" / profile / "modlist.txt"
    if not modlist_path.is_file():
        err(f"modlist.txt not found: {modlist_path}")
        return 1

    info(f"MO2 root : {mo2_root}")
    info(f"Profile  : {profile}")
    if args.dry_run:
        warn("Dry run - no files will be written.")

    enabled = list_enabled_mod_names(modlist_path)
    info(f"Enabled mods (non-separator): {len(enabled)}")

    dogma_dir = find_dogma_mod_dir(mo2_root, enabled)
    if dogma_dir:
        info(f"DOGMA mod: {dogma_dir}")

    size_key = resolve_size_key(args)
    max_bytes, size_label = SIZE_BY_KEY[size_key]
    info(f"Max size : {size_label}")

    entries, _scanned, _skipped, _dropped = collect_sound_paths(
        mo2_root, enabled, max_bytes=max_bytes
    )
    info(f"Prefetch list: {len(entries)}")

    ltx_path = mo2_root / "overwrite" / LTX_REL

    # Drop older copies (DOGMA mod and previous paths/names).
    stale = [
        (dogma_dir / LTX_REL) if dogma_dir else None,
        mo2_root / "overwrite" / "gamedata" / "configs" / "dogma_snd_prefetch.ltx",
        mo2_root / "overwrite" / "gamedata" / "configs" / "items" / "items" / "dogma_snd_prefetch.ltx",
        mo2_root / "overwrite" / "gamedata" / "configs" / "items" / "items" / LTX_NAME,
        (dogma_dir / "gamedata" / "configs" / "dogma_snd_prefetch.ltx") if dogma_dir else None,
        (dogma_dir / "gamedata" / "configs" / "items" / "items" / "dogma_snd_prefetch.ltx") if dogma_dir else None,
        (dogma_dir / "gamedata" / "configs" / "items" / "items" / LTX_NAME) if dogma_dir else None,
    ]

    lines, total_bytes = format_ltx(entries, size_label=size_label)
    total_mb = total_bytes // (1024 * 1024)
    if not args.dry_run:
        if ltx_path.is_file():
            ltx_path.unlink()
        write_text_lines(ltx_path, lines)
        ok(f"Wrote {ltx_path.relative_to(mo2_root)} ({len(entries)} entries, {total_mb} MB)")
        for old in stale:
            if old and old.is_file() and old.resolve() != ltx_path.resolve():
                old.unlink()
                warn(f"Removed stale {old.relative_to(mo2_root)}")
    else:
        info(f"Would replace {ltx_path.relative_to(mo2_root)} ({len(entries)} entries, {total_mb} MB)")

    ok("Done.")
    return 0


def pause() -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return
    try:
        input("Press Enter to close...")
    except EOFError:
        pass


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    except (FileNotFoundError, ValueError, RuntimeError, OSError) as exc:
        err(str(exc))
    pause()
    raise SystemExit(code)
