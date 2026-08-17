#!/usr/bin/env python3
"""Disable MO2 mods listed in enabled mods' disables.txt."""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

BACKUP = "DOGMA disables"


def pause() -> None:
	if not (sys.stdin.isatty() and sys.stdout.isatty()):
		return
	try:
		input("Press Enter to close...")
	except EOFError:
		pass


def read_lines(path: Path) -> list[str]:
	raw = path.read_bytes()
	if raw.startswith(b"\xef\xbb\xbf"):
		raw = raw[3:]
	text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
	return text.splitlines()


def write_lines(path: Path, lines: list[str]) -> None:
	path.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))


def is_mo2(path: Path) -> bool:
	return (path / "ModOrganizer.exe").is_file() or (path / "ModOrganizer.ini").is_file()


def find_mo2() -> Path:
	env = (os.environ.get("MO2_ROOT") or "").strip()
	if env:
		return Path(env).expanduser()
	cur = Path.cwd().resolve()
	for path in (cur, *cur.parents, Path(__file__).resolve().parent, *Path(__file__).resolve().parents):
		if is_mo2(path):
			return path
	raise FileNotFoundError("Could not find MO2. Run from the DOGMA mod folder or set MO2_ROOT.")


def ini_value(ini: Path, key: str) -> str:
	prefix = re.compile(rf"^\s*{re.escape(key)}\s*=")
	for raw in read_lines(ini):
		if not prefix.match(raw):
			continue
		m = re.search(r"@ByteArray\((.+)\)\s*$", raw)
		value = m.group(1) if m else raw.split("=", 1)[1].strip()
		return value.replace("\\\\", "\\")
	raise ValueError(f"{key} not found in {ini}")


def names_from_disables(path: Path) -> list[str]:
	out: list[str] = []
	for line in read_lines(path):
		text = line.strip()
		if not text or text.startswith("#"):
			continue
		out.append(text)
	return out


def collect_targets(mods_dir: Path, enabled: list[str]) -> list[str]:
	seen: set[str] = set()
	names: list[str] = []
	for mod in enabled:
		path = mods_dir / mod / "disables.txt"
		if not path.is_file():
			continue
		for name in names_from_disables(path):
			key = name.lower()
			if key in seen:
				continue
			seen.add(key)
			names.append(name)
	return names


def next_backup(modlist: Path) -> Path:
	pat = re.compile(rf"^{re.escape(modlist.name)}\.{re.escape(BACKUP)} (\d+)$", re.I)
	n = 0
	for p in modlist.parent.iterdir():
		m = pat.match(p.name) if p.is_file() else None
		if m:
			n = max(n, int(m.group(1)))
	return modlist.parent / f"{modlist.name}.{BACKUP} {n + 1}"


def main() -> int:
	mo2 = find_mo2()
	profile = ini_value(mo2 / "ModOrganizer.ini", "selected_profile")
	modlist = mo2 / "profiles" / profile / "modlist.txt"
	if not modlist.is_file():
		raise FileNotFoundError(f"modlist.txt not found: {modlist}")

	print(f"MO2: {mo2}")
	print(f"profile: {profile}")

	enabled = [line[1:] for line in read_lines(modlist) if line.startswith("+") and line[1:]]
	targets = {n.lower(): n for n in collect_targets(mo2 / "mods", enabled)}
	if not targets:
		print("No disables.txt found on enabled mods.")
		return 0

	lines = read_lines(modlist)
	out: list[str] = []
	will_off: list[str] = []
	already: list[str] = []
	hit: set[str] = set()
	for line in lines:
		m = re.match(r"^([+\-])(.+)$", line)
		if not m:
			out.append(line)
			continue
		flag, name = m.group(1), m.group(2)
		key = name.lower()
		if key not in targets:
			out.append(line)
			continue
		hit.add(key)
		if flag == "-":
			already.append(name)
			out.append(line)
			continue
		will_off.append(name)
		out.append(f"-{name}")

	missing = [targets[k] for k in targets if k not in hit]
	for name in will_off:
		print(f"  disable: {name}")
	for name in already:
		print(f"  already off: {name}")
	for name in missing:
		print(f"  not in modlist: {name}")

	if not will_off:
		print("Nothing to change.")
		return 0

	print(f"Would disable {len(will_off)} mod(s).")
	try:
		ok = input("Apply these changes? [y/N]: ").strip().lower() in ("y", "yes")
	except EOFError:
		ok = False
	if not ok:
		print("Cancelled.")
		return 0

	bak = next_backup(modlist)
	shutil.copy2(modlist, bak)
	write_lines(modlist, out)
	print(f"Done. Disabled {len(will_off)} mod(s).")
	print(f"backup: {bak.name}")
	return 0


if __name__ == "__main__":
	code = 1
	try:
		code = main()
	except (FileNotFoundError, ValueError, OSError) as exc:
		print(exc, file=sys.stderr)
	pause()
	raise SystemExit(code)
