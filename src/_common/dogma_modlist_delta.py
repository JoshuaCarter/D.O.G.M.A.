#!/usr/bin/env python3
"""Apply installed D.O.G.M.A. modlist_delta.txt files to the current MO2 profile.

Run from the DOGMA mod folder (or pass --mo2-root):

  py -3 dogma_modlist_delta.py
  py -3 dogma_modlist_delta.py --dry-run

Collects +enable / -disable lines from every enabled MO2 mod that ships
gamedata/configs/dogma/modlist_deltas/*.txt (or a root modlist_delta.txt)
and flips matching lines in the selected profile's modlist.txt.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path


DELTA_DIR = Path("gamedata") / "configs" / "dogma" / "modlist_deltas"
ROOT_DELTA_NAME = "modlist_delta.txt"
BACKUP_PREFIX = "DOGMA Modlist Delta"


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
        value = m.group(1) if m else raw.split("=", 1)[1].strip()
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
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))


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


def parse_target(raw: str) -> tuple[str, str]:
    key = raw.strip()
    low = key.lower()
    if low.startswith("substring:") or low.startswith("contains:"):
        return ("substring", key.split(":", 1)[1].strip())
    if low.startswith("exact:"):
        return ("exact", key.split(":", 1)[1].strip())
    return ("exact", key)


def name_matches(name: str, kind: str, pattern: str) -> bool:
    if not pattern:
        return False
    if kind == "substring":
        return pattern.lower() in name.lower()
    return name.lower() == pattern.lower()


def parse_delta_file(path: Path) -> list[tuple[str, str, str]]:
    """Return (flag, kind, pattern) rows. flag is '+' or '-'."""
    rows: list[tuple[str, str, str]] = []
    for line in read_text_lines(path):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if text[0] not in "+-":
            warn(f"  skip {path.name}: not a +/- line: {text}")
            continue
        kind, pattern = parse_target(text[1:])
        if not pattern:
            warn(f"  skip {path.name}: empty name: {text}")
            continue
        rows.append((text[0], kind, pattern))
    return rows


def delta_files_in_mod(mod_dir: Path) -> list[Path]:
    found: list[Path] = []
    root_delta = mod_dir / ROOT_DELTA_NAME
    if root_delta.is_file():
        found.append(root_delta)
    folder = mod_dir / DELTA_DIR
    if folder.is_dir():
        found.extend(sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".txt"))
    return found


def enabled_mod_names(modlist_path: Path) -> list[str]:
    names: list[str] = []
    for line in read_text_lines(modlist_path):
        if line.startswith("+"):
            name = line[1:]
            if name and "separator" not in name.lower():
                names.append(name)
    return names


def next_backup_suffix(modlist: Path) -> str:
    pat = re.compile(
        rf"^{re.escape(modlist.name)}\.{re.escape(BACKUP_PREFIX)} (\d+)$",
        re.IGNORECASE,
    )
    n_max = 0
    for p in modlist.parent.iterdir():
        if p.is_file():
            m = pat.match(p.name)
            if m:
                n_max = max(n_max, int(m.group(1)))
    return f"{BACKUP_PREFIX} {n_max + 1}"


def collect_ops(mods_dir: Path, enabled: list[str]) -> list[tuple[str, str, str, str]]:
    """Low-priority mods first so later / higher-priority deltas win.

    MO2 lists highest priority first in modlist.txt.
    """
    ops: dict[tuple[str, str], tuple[str, str, str, str]] = {}
    for name in reversed(enabled):
        mod_dir = mods_dir / name
        if not mod_dir.is_dir():
            continue
        files = delta_files_in_mod(mod_dir)
        if not files:
            continue
        info(f"{name}")
        for path in files:
            rows = parse_delta_file(path)
            if not rows:
                continue
            info(f"  {path.relative_to(mod_dir)} ({len(rows)})")
            for flag, kind, pattern in rows:
                ops[(kind, pattern.lower())] = (flag, kind, pattern, f"{name}/{path.name}")
    return list(ops.values())


def apply_ops(
    lines: list[str],
    ops: list[tuple[str, str, str, str]],
) -> tuple[list[str], list[str], list[str], list[str]]:
    changed_on: list[str] = []
    changed_off: list[str] = []
    already: list[str] = []
    matched: set[tuple[str, str]] = set()
    out: list[str] = []

    for line in lines:
        m = re.match(r"^([+\-])(.+)$", line)
        if not m:
            out.append(line)
            continue
        flag, name = m.group(1), m.group(2)
        hit: tuple[str, str, str, str] | None = None
        for op in ops:
            if name_matches(name, op[1], op[2]):
                hit = op
                matched.add((op[1], op[2].lower()))
        if hit is None:
            out.append(line)
            continue
        want = hit[0]
        if flag == want:
            already.append(name)
            out.append(line)
            continue
        out.append(f"{want}{name}")
        if want == "+":
            changed_on.append(name)
        else:
            changed_off.append(name)
    unmatched = [f"{flag}{pattern}" for flag, kind, pattern, _src in ops if (kind, pattern.lower()) not in matched]
    return out, changed_on, changed_off, unmatched


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply installed D.O.G.M.A. modlist deltas to the current MO2 profile."
    )
    parser.add_argument("--mo2-root", help="MO2 instance root (default: walk up from cwd / this script)")
    parser.add_argument("--profile", default="", help="Profile name (default: selected_profile)")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing modlist.txt")
    args = parser.parse_args()

    try:
        mo2_root = resolve_mo2_root(args.mo2_root)
    except FileNotFoundError as exc:
        err(str(exc))
        return 1

    profile = args.profile.strip() or read_mo2_ini_value(mo2_root / "ModOrganizer.ini", "selected_profile")
    modlist = mo2_root / "profiles" / profile / "modlist.txt"
    if not modlist.is_file():
        err(f"modlist.txt not found: {modlist}")
        return 1

    info(f"MO2: {mo2_root}")
    info(f"profile: {profile}")
    enabled = enabled_mod_names(modlist)
    ops = collect_ops(mo2_root / "mods", enabled)
    if not ops:
        warn("No modlist_delta.txt files found on enabled mods.")
        return 0

    lines = read_text_lines(modlist)
    out, changed_on, changed_off, unmatched = apply_ops(lines, ops)

    for name in changed_off:
        ok(f"  disable: {name}")
    for name in changed_on:
        ok(f"  enable: {name}")
    for name in unmatched:
        warn(f"  unmatched: {name}")

    if not changed_on and not changed_off:
        info("Nothing to change.")
        return 0

    if args.dry_run:
        info(f"dry-run: would change {len(changed_off) + len(changed_on)} line(s)")
        return 0

    dest = modlist.parent / f"{modlist.name}.{next_backup_suffix(modlist)}"
    shutil.copy2(modlist, dest)
    write_text_lines(modlist, out)
    ok(f"wrote {modlist.name} ({len(changed_off)} off, {len(changed_on)} on)")
    info(f"backup: {dest.name}")
    info("  Restore via MO2 → Restore Backup… on the mod list")
    return 0


if __name__ == "__main__":
    sys.exit(main())
