#!/usr/bin/env python3
"""Build dogma_sfx_prefetch.ltx from enabled MO2 mods' loose sounds.

Part of feature src/tweaks/sound_prefetch. Shipped to mods/DOGMA/mo2/tools/
via the mo2/ build exception. Runtime script is built normally into gamedata/scripts/.

Skips .ogg files >= 100 KB (music / long ambience) so prefetch stays focused
on short SFX that hitch on first play.

  mods/DOGMA/mo2/tools/DOGMA SFX Prefetch.bat   (or D.O.G.M.A. Optimize)
  overwrite/gamedata/configs/dogma_sfx_prefetch.ltx  (generated)

  py -3 build_sound_prefetch.py
  py -3 build_sound_prefetch.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


DOGMA_MOD_NAME = "DOGMA"
LEGACY_SEPARATE_MOD = "DOGMA - Sound Prefetch"
# Only .ogg — X-Ray sound attrs live in Vorbis comments (wav/flac are not prefetched).
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
# Skip files at/above this size (music / long ambience). Prefetching those
# eats RAM and rarely helps short-SFX hitching.
LARGE_FILE_MIN_BYTES = 100 * 1024


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


def resolve_mo2_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    cwd = Path.cwd()
    if (cwd / "ModOrganizer.ini").is_file():
        return cwd
    return Path(r"C:\GAMMA") if sys.platform == "win32" else Path(os.environ.get("MO2_ROOT", r"C:\GAMMA"))


def list_enabled_mod_names(modlist_path: Path) -> list[str]:
    names: list[str] = []
    for line in read_text_lines(modlist_path):
        if line.startswith("+"):
            name = line[1:]
            if name and "separator" not in name.lower():
                names.append(name)
    return names


def find_dogma_mod_dir(mo2_root: Path, enabled_mods: list[str]) -> Path | None:
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
    min_bytes: int = LARGE_FILE_MIN_BYTES,
) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    """Keep entries strictly below min_bytes.

    Returns (kept [(rel, size), ...], dropped [(rel, size), ...]).
    """
    kept: list[tuple[str, int]] = []
    dropped: list[tuple[str, int]] = []
    for rel, path in entries:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if size >= min_bytes:
            dropped.append((rel, size))
        else:
            kept.append((rel, size))
    return kept, dropped


def collect_sound_paths(
    mo2_root: Path, enabled_mods: list[str]
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

    min_kb = LARGE_FILE_MIN_BYTES // 1024
    kept, dropped_large = drop_large_files(valid)
    if dropped_large:
        warn(f"Dropping {len(dropped_large)} files >= {min_kb} KB")
    kept.sort(key=lambda t: t[0].lower())
    return kept, scanned, skipped, dropped_large


def format_ltx(entries: list[tuple[str, int]]) -> tuple[list[str], int]:
    """Build LTX lines. Returns (lines, total_bytes)."""
    lines = [f"[{SECTION}]"]
    total_bytes = 0
    for i, (path, size) in enumerate(entries, start=1):
        total_bytes += size
        lines.append(f"t{i} = {path}")
    return lines, total_bytes


def resolve_launch_exe(mo2_root: Path, then_launch: str) -> Path:
    candidate = Path(then_launch)
    if candidate.is_file():
        return candidate.resolve()
    try:
        game_path = Path(read_mo2_ini_value(mo2_root / "ModOrganizer.ini", "gamePath"))
    except (FileNotFoundError, ValueError):
        game_path = None
    search = []
    if game_path:
        search.append(game_path / then_launch)
    search.append(mo2_root / then_launch)
    search.append(Path.cwd() / then_launch)
    for path in search:
        if path.is_file():
            return path.resolve()
    raise FileNotFoundError(
        f"Launch exe not found: {then_launch} (tried gamePath, mo2-root, cwd)"
    )


def remove_legacy_separate_mod(mo2_root: Path, modlist_path: Path, dry_run: bool) -> None:
    legacy_dir = mo2_root / "mods" / LEGACY_SEPARATE_MOD
    if legacy_dir.is_dir():
        if dry_run:
            warn(f"Dry run: would remove legacy mod {legacy_dir}")
        else:
            shutil.rmtree(legacy_dir)
            ok(f"Removed legacy mod: {legacy_dir.name}")

    lines = read_text_lines(modlist_path)
    target = LEGACY_SEPARATE_MOD.lower()
    kept = []
    removed = False
    for line in lines:
        m = re.match(r"^([+\-])(.+)$", line)
        if m and m.group(2).lower() == target:
            removed = True
            continue
        kept.append(line)
    if removed:
        if dry_run:
            warn(f"Dry run: would remove '{LEGACY_SEPARATE_MOD}' from modlist")
        else:
            write_text_lines(modlist_path, kept)
            ok(f"Removed '{LEGACY_SEPARATE_MOD}' from modlist")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Scan enabled MO2 mods for loose sounds and write dogma_sfx_prefetch.ltx "
            "into overwrite/gamedata. Use DOGMA Optimize / DOGMA SFX Prefetch from "
            "MO2 Executables (or run this script with --mo2-root)."
        )
    )
    p.add_argument(
        "--mo2-root",
        default="",
        help="MO2 instance folder. Default: cwd if ModOrganizer.ini present, else C:\\GAMMA",
    )
    p.add_argument("--profile", default="", help="MO2 profile (default: selected_profile)")
    p.add_argument("--dry-run", action="store_true", help="Scan and report; write nothing")
    p.add_argument(
        "--then-launch",
        default="",
        metavar="EXE",
        help="After a successful write, launch this exe",
    )
    p.add_argument(
        "launch_args",
        nargs="*",
        help="Args passed to --then-launch",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    mo2_root = resolve_mo2_root(args.mo2_root or None)

    if not (mo2_root / "ModOrganizer.exe").is_file():
        err(f"ModOrganizer.exe not found under: {mo2_root}")
        err("Set MO2 Working Directory to the instance root, or pass --mo2-root")
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

    entries, _scanned, _skipped, _dropped = collect_sound_paths(mo2_root, enabled)
    info(f"Prefetch list: {len(entries)}")

    ltx_path = mo2_root / "overwrite" / LTX_REL
    remove_legacy_separate_mod(mo2_root, modlist_path, args.dry_run)

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

    lines, total_bytes = format_ltx(entries)
    total_mb = total_bytes // (1024 * 1024)
    if not args.dry_run:
        write_text_lines(ltx_path, lines)
        ok(f"Wrote {ltx_path.relative_to(mo2_root)} ({len(entries)} entries, {total_mb} MB)")
        for old in stale:
            if old and old.is_file() and old.resolve() != ltx_path.resolve():
                old.unlink()
                warn(f"Removed stale {old.relative_to(mo2_root)}")
    else:
        info(f"Would write {ltx_path.relative_to(mo2_root)} ({len(entries)} entries, {total_mb} MB)")

    if args.then_launch:
        if args.dry_run:
            warn(f"Dry run: skipping launch of {args.then_launch}")
            return 0
        exe = resolve_launch_exe(mo2_root, args.then_launch)
        launch_args = [str(exe), *args.launch_args]
        info(f"Launching: {' '.join(launch_args)}")
        completed = subprocess.run(launch_args, check=False)
        return int(completed.returncode)

    ok("Done.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError, RuntimeError, OSError) as exc:
        err(str(exc))
        raise SystemExit(1) from exc
