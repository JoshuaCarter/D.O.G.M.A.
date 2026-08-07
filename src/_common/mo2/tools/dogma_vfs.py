#!/usr/bin/env python3
"""DOGMA VFS — MO2/GAMMA winning files and merged LTX (stdlib only).

Models the loose-file VFS Gamma actually uses at runtime:

  Anomaly gamedata (optional) -> enabled mods low->high -> MO2 overwrite

Same relative path under a layer root = one winner (modlist top / overwrite wins).
Unique paths (e.g. ``mod_system_*.ltx``) all remain and still apply.

Import::

    from dogma_vfs import Mo2Vfs
    vfs = Mo2Vfs.resolve()
    for w in vfs.winning_files("gamedata/sounds", suffix=".ogg"):
        print(w.rel, w.path, w.layer)
    sec = vfs.merged_sections(only={"wpn_ak74"})["wpn_ak74"]

CLI::

    py dogma_vfs.py list --under gamedata/sounds --ext .ogg -o winners.txt
    py dogma_vfs.py resolve gamedata/sounds/weapons/ak74_shoot.ogg
    py dogma_vfs.py merge-ltx --section wpn_ak74 -o wpn_ak74.ltx
    py dogma_vfs.py merge-ltx --match "ammo_*" -o ammo.ltx

Notes:
  - Archives (``.db*`` / BSA) are not enumerated. Use Anomaly ``tools/_unpacked``
    or loose ``gamedata`` for base-game files.
  - LTX merge understands DLTX ``![section]`` patches and ``[sec]:parent`` rebases.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, Sequence


# ---------------------------------------------------------------------------
# MO2 plumbing (self-contained — no dogma_mo2_lib import)
# ---------------------------------------------------------------------------

_SEC_RE = re.compile(
    r"^\s*(\!?\[)([^\]]+)\](?:\s*:\s*(.+))?\s*$",
    re.IGNORECASE,
)
_KV_RE = re.compile(r"^\s*([^=;\s][^=]*?)\s*=\s*(.*)$")


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


def resolve_mo2_root(explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit)
    env = (os.environ.get("MO2_ROOT") or "").strip()
    if env:
        return Path(env).expanduser()
    cwd = Path.cwd()
    if (cwd / "ModOrganizer.exe").is_file() or (cwd / "ModOrganizer.ini").is_file():
        return cwd
    here = Path(__file__).resolve().parent
    # Deployed: mods/DOGMA/mo2/tools → MO2 root
    for up in (here.parent.parent.parent, here.parent.parent.parent.parent):
        if (up / "ModOrganizer.exe").is_file() or (up / "ModOrganizer.ini").is_file():
            return up
    raise FileNotFoundError(
        "Could not find the MO2 instance root. Run from that folder, "
        "pass --mo2-root, or set MO2_ROOT."
    )


def selected_profile(mo2_root: Path, profile: str = "") -> str:
    if profile:
        return profile
    return read_mo2_ini_value(mo2_root / "ModOrganizer.ini", "selected_profile")


def modlist_path(mo2_root: Path, profile: str = "") -> Path:
    name = selected_profile(mo2_root, profile)
    path = mo2_root / "profiles" / name / "modlist.txt"
    if not path.is_file():
        raise FileNotFoundError(f"modlist.txt not found: {path}")
    return path


def game_dir(mo2_root: Path) -> Path:
    return Path(read_mo2_ini_value(mo2_root / "ModOrganizer.ini", "gamePath"))


def _is_separator(name: str) -> bool:
    low = name.lower()
    return "separator" in low or low.endswith("_separator")


def enabled_mods_high_first(modlist: Path) -> list[str]:
    """Enabled mods in modlist order (top of file = highest priority)."""
    names: list[str] = []
    for raw in modlist.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line.startswith("+"):
            continue
        name = line[1:].strip()
        if name and not _is_separator(name):
            names.append(name)
    return names


def rel_key(root: Path, path: Path) -> str:
    """Case-insensitive VFS key relative to a layer root (posix separators)."""
    return path.relative_to(root).as_posix().lower()


def norm_rel(rel: str) -> str:
    """Normalize a user/VFS relative path for lookups."""
    s = rel.strip().replace("\\", "/").lstrip("/")
    while s.startswith("./"):
        s = s[2:]
    return s.lower()


# ---------------------------------------------------------------------------
# Layer model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VfsLayer:
    """One loose-file root in apply order (later layers win on path conflicts)."""

    name: str
    root: Path  # absolute directory that *is* the VFS mount (e.g. .../gamedata)


@dataclass(frozen=True)
class WinningFile:
    """A unique relative path and the disk file that wins for it."""

    rel: str  # relative to layer root, backslash, original casing from winner
    rel_key: str  # lowercased posix
    path: Path  # absolute disk path
    layer: str  # VfsLayer.name


def _split_under(under: str) -> tuple[str, Path]:
    """Return (normalized under string, Path with forward-slash parts)."""
    parts = Path(under.replace("\\", "/"))
    return parts.as_posix().lower(), parts


def _under_relative_to_gamedata(under_parts: Path) -> Path:
    """``gamedata/configs`` → ``configs``; ``configs`` → ``configs``."""
    parts = under_parts.parts
    if parts and parts[0].lower() == "gamedata":
        return Path(*parts[1:]) if len(parts) > 1 else Path()
    return under_parts


def anomaly_layer_root(anomaly: Path, under: str) -> Path | None:
    """Best Anomaly loose root for ``under``, or None.

    Prefers ``tools/_unpacked/<rest>`` (db dump; already gamedata-shaped),
    else ``gamedata/<rest>``.
    """
    _, under_parts = _split_under(under)
    rest = _under_relative_to_gamedata(under_parts)
    candidates = [
        anomaly / "tools" / "_unpacked" / rest,
        anomaly / "gamedata" / rest,
    ]
    for root in candidates:
        if root.is_dir():
            return root.resolve()
    return None


def build_layers(
    mo2_root: Path,
    *,
    under: str = "gamedata",
    profile: str = "",
    include_anomaly: bool = True,
    anomaly: Path | None = None,
    skip_mods: Iterable[str] | None = None,
) -> list[VfsLayer]:
    """Ordered layers low→high for ``under`` (relative to each mod / overwrite / game).

    ``under`` examples: ``gamedata``, ``gamedata/sounds``, ``gamedata/configs``.
    """
    _, under_parts = _split_under(under)
    skip = {s.lower() for s in (skip_mods or ())}
    layers: list[VfsLayer] = []

    if include_anomaly:
        game = anomaly
        if game is None:
            try:
                game = game_dir(mo2_root)
            except (FileNotFoundError, ValueError, OSError):
                game = None
        if game is not None:
            root = anomaly_layer_root(game, under)
            if root is not None:
                layers.append(VfsLayer(name="<Anomaly>", root=root))

    modlist = modlist_path(mo2_root, profile)
    high_first = enabled_mods_high_first(modlist)
    mods_dir = mo2_root / "mods"
    for name in reversed(high_first):
        if name.lower() in skip:
            continue
        root = mods_dir / name / under_parts
        if root.is_dir():
            layers.append(VfsLayer(name=name, root=root.resolve()))

    ow = mo2_root / "overwrite" / under_parts
    if ow.is_dir():
        layers.append(VfsLayer(name="<overwrite>", root=ow.resolve()))

    return layers


def collect_winners(
    layers: Sequence[VfsLayer],
    *,
    suffix: str | None = None,
    suffixes: Iterable[str] | None = None,
    name_glob: str | None = None,
    recursive: bool = True,
) -> list[WinningFile]:
    """Scan layers low→high; return unique path winners (stable sort by rel_key)."""
    ext_set: set[str] = set()
    if suffix:
        ext_set.add(suffix.lower() if suffix.startswith(".") else f".{suffix.lower()}")
    if suffixes:
        for s in suffixes:
            ext_set.add(s.lower() if s.startswith(".") else f".{s.lower()}")

    found: dict[str, WinningFile] = {}
    for layer in layers:
        root = layer.root
        iterator: Iterator[Path]
        if recursive:
            iterator = root.rglob("*")
        else:
            iterator = root.glob("*")
        for path in iterator:
            if not path.is_file():
                continue
            if ext_set and path.suffix.lower() not in ext_set:
                continue
            if name_glob and not fnmatch.fnmatch(path.name.lower(), name_glob.lower()):
                continue
            try:
                key = rel_key(root, path)
            except ValueError:
                continue
            if key.startswith("$"):
                continue
            rel = str(path.relative_to(root)).replace("/", "\\")
            found[key] = WinningFile(
                rel=rel,
                rel_key=key,
                path=path.resolve(),
                layer=layer.name,
            )

    return sorted(found.values(), key=lambda w: w.rel_key)


def resolve_in_layers(layers: Sequence[VfsLayer], rel: str) -> WinningFile | None:
    """Return the winning file for ``rel`` (relative to layer roots), or None."""
    key = norm_rel(rel)
    hit: WinningFile | None = None
    rel_path = Path(rel.replace("\\", "/"))
    for layer in layers:
        candidate = layer.root / rel_path
        if not candidate.is_file():
            continue
        rel_out = str(candidate.relative_to(layer.root)).replace("/", "\\")
        hit = WinningFile(
            rel=rel_out,
            rel_key=key,
            path=candidate.resolve(),
            layer=layer.name,
        )
    if hit is not None:
        return hit
    # Case / alternate spelling: scan winners once
    for w in collect_winners(layers):
        if w.rel_key == key:
            return w
    return None


# ---------------------------------------------------------------------------
# LTX merge (DLTX-aware, VFS path winners only)
# ---------------------------------------------------------------------------


def _parent_list(par: str | None) -> list[str]:
    if not par:
        return []
    return [p.strip() for p in par.split(",") if p.strip()]


def _flatten_parents(
    sections: dict[str, dict[str, str]], parents: list[str]
) -> dict[str, str]:
    base: dict[str, str] = {}
    for p in parents:
        if p in sections:
            base.update(sections[p])
    return base


def _rebase_section(
    sections: dict[str, dict[str, str]],
    owned: dict[str, set[str]],
    sec: str,
    parents: list[str],
) -> None:
    sections[sec] = _flatten_parents(sections, parents)
    owned[sec] = set()


def apply_ltx_file(
    sections: dict[str, dict[str, str]],
    path: Path,
    *,
    section_parents: dict[str, str] | None = None,
    owned: dict[str, set[str]] | None = None,
) -> int:
    """Apply one LTX file into ``sections``. Returns section-header count."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    parents_out = section_parents if section_parents is not None else {}
    owned_keys = owned if owned is not None else {}
    cur: str | None = None
    patch = False
    pending_reparent: tuple[str, list[str]] | None = None
    n = 0

    for raw in text.splitlines():
        line = raw.split(";", 1)[0].rstrip()
        if not line.strip():
            continue
        m = _SEC_RE.match(line)
        if m:
            pending_reparent = None
            bang, name, par = m.group(1), m.group(2).strip(), m.group(3)
            patch = bang.startswith("!")
            cur = name
            parents = _parent_list(par)
            primary = next((p for p in parents if p in sections), None)
            if primary:
                parents_out[cur] = primary
            if cur not in sections:
                sections[cur] = _flatten_parents(sections, parents)
            elif not patch and parents:
                # Header-only ``[sec]:alias`` must not wipe fields until a body key.
                pending_reparent = (cur, parents)
            n += 1
            continue
        if cur is None:
            continue
        km = _KV_RE.match(line)
        if not km:
            continue
        if pending_reparent and pending_reparent[0] == cur:
            _rebase_section(sections, owned_keys, cur, pending_reparent[1])
            pending_reparent = None
        key = km.group(1).strip()
        val = km.group(2).strip()
        sections[cur][key] = val
        owned_keys.setdefault(cur, set()).add(key)
    return n


def merge_ltx_files(
    files: Sequence[Path],
    *,
    progress: Callable[[int, int, Path], None] | None = None,
) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    """Merge LTX files in order. Returns (sections, primary_parent)."""
    sections: dict[str, dict[str, str]] = {}
    parents: dict[str, str] = {}
    owned: dict[str, set[str]] = {}
    total = len(files)
    for i, path in enumerate(files):
        apply_ltx_file(sections, path, section_parents=parents, owned=owned)
        if progress and (i % 50 == 0 or i + 1 == total):
            progress(i + 1, total, path)
    return sections, parents


def winning_ltx_files(layers: Sequence[VfsLayer]) -> list[Path]:
    """Winning ``*.ltx`` paths in layer order (superseded same-rel skipped)."""
    winners: dict[str, Path] = {}
    per_root: list[tuple[VfsLayer, list[Path]]] = []
    for layer in layers:
        found = sorted(p for p in layer.root.rglob("*.ltx") if p.is_file())
        per_root.append((layer, found))
        for path in found:
            winners[rel_key(layer.root, path)] = path

    out: list[Path] = []
    for layer, found in per_root:
        for path in found:
            if winners[rel_key(layer.root, path)] is path:
                out.append(path)
    return out


def format_ltx(
    sections: dict[str, dict[str, str]],
    *,
    names: Sequence[str] | None = None,
    parents: dict[str, str] | None = None,
    header: str = "",
) -> str:
    """Serialize sections to LTX text."""
    lines: list[str] = []
    if header:
        for h in header.splitlines():
            lines.append(f"; {h}" if not h.startswith(";") else h)
    order = list(names) if names is not None else sorted(sections.keys(), key=str.lower)
    for sec in order:
        fields = sections.get(sec)
        if fields is None:
            continue
        parent = (parents or {}).get(sec)
        if parent:
            lines.append(f"[{sec}]:{parent}")
        else:
            lines.append(f"[{sec}]")
        for key, val in fields.items():
            lines.append(f"{key} = {val}")
        lines.append("")
    return "\r\n".join(lines).rstrip() + "\r\n"


def format_txt_list(
    winners: Sequence[WinningFile],
    *,
    with_layer: bool = False,
    with_path: bool = False,
    stem: bool = False,
) -> str:
    lines: list[str] = []
    for w in winners:
        rel = w.rel
        if stem:
            rel = str(Path(rel).with_suffix("")).replace("/", "\\")
        if with_layer or with_path:
            parts = [rel]
            if with_layer:
                parts.append(w.layer)
            if with_path:
                parts.append(str(w.path))
            lines.append("\t".join(parts))
        else:
            lines.append(rel)
    return "\r\n".join(lines) + ("\r\n" if lines else "")


# ---------------------------------------------------------------------------
# High-level façade
# ---------------------------------------------------------------------------


class Mo2Vfs:
    """Resolve winning files / merged LTX for an MO2 (GAMMA) instance."""

    def __init__(
        self,
        mo2_root: Path,
        *,
        profile: str = "",
        include_anomaly: bool = True,
        anomaly: Path | None = None,
        skip_mods: Iterable[str] | None = None,
    ) -> None:
        self.mo2_root = Path(mo2_root).resolve()
        self.profile = selected_profile(self.mo2_root, profile)
        self.include_anomaly = include_anomaly
        self.anomaly = Path(anomaly).resolve() if anomaly else None
        self.skip_mods = list(skip_mods or ())

    @classmethod
    def resolve(
        cls,
        mo2_root: str | Path | None = None,
        **kwargs,
    ) -> Mo2Vfs:
        return cls(resolve_mo2_root(mo2_root), **kwargs)

    def layers(self, under: str = "gamedata") -> list[VfsLayer]:
        return build_layers(
            self.mo2_root,
            under=under,
            profile=self.profile,
            include_anomaly=self.include_anomaly,
            anomaly=self.anomaly,
            skip_mods=self.skip_mods,
        )

    def winning_files(
        self,
        under: str = "gamedata",
        *,
        suffix: str | None = None,
        suffixes: Iterable[str] | None = None,
        name_glob: str | None = None,
        recursive: bool = True,
    ) -> list[WinningFile]:
        return collect_winners(
            self.layers(under),
            suffix=suffix,
            suffixes=suffixes,
            name_glob=name_glob,
            recursive=recursive,
        )

    def resolve_path(self, rel_under: str, *, under: str = "gamedata") -> WinningFile | None:
        """``rel_under`` is relative to ``under`` (e.g. ``sounds\\x.ogg`` with under=gamedata)."""
        rel = rel_under
        under_n = norm_rel(under)
        rel_n = norm_rel(rel_under)
        # Allow callers to pass gamedata/... even when under=gamedata
        if under_n == "gamedata" and rel_n.startswith("gamedata/"):
            rel = rel_under.split("/", 1)[-1] if "/" in rel_under.replace("\\", "/") else rel_under
            # better strip:
            parts = Path(rel_under.replace("\\", "/")).parts
            if parts and parts[0].lower() == "gamedata":
                rel = str(Path(*parts[1:]))
        return resolve_in_layers(self.layers(under), rel)

    def winning_ltx(self, under: str = "gamedata/configs") -> list[Path]:
        return winning_ltx_files(self.layers(under))

    def merge_ltx(
        self,
        *,
        under: str = "gamedata/configs",
        progress: Callable[[int, int, Path], None] | None = None,
    ) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
        """Full VFS LTX merge. Returns (sections, primary_parent)."""
        files = self.winning_ltx(under)
        return merge_ltx_files(files, progress=progress)

    def merged_sections(
        self,
        *,
        under: str = "gamedata/configs",
        only: Iterable[str] | None = None,
        match: str | None = None,
        progress: Callable[[int, int, Path], None] | None = None,
    ) -> dict[str, dict[str, str]]:
        """Full VFS LTX merge, optionally filtered to section names / glob."""
        sections, _parents = self.merge_ltx(under=under, progress=progress)
        if only is None and match is None:
            return sections
        wanted: set[str] = set(only or ())
        out: dict[str, dict[str, str]] = {}
        for name, fields in sections.items():
            if name in wanted or (match and fnmatch.fnmatch(name.lower(), match.lower())):
                out[name] = fields
        return out

    def merged_section(
        self,
        name: str,
        *,
        under: str = "gamedata/configs",
    ) -> dict[str, str] | None:
        return self.merged_sections(under=under, only={name}).get(name)

    def write_txt(
        self,
        dest: Path,
        winners: Sequence[WinningFile],
        **fmt_kwargs,
    ) -> None:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(format_txt_list(winners, **fmt_kwargs).encode("utf-8"))

    def write_ltx(
        self,
        dest: Path,
        sections: dict[str, dict[str, str]],
        *,
        names: Sequence[str] | None = None,
        parents: dict[str, str] | None = None,
        header: str = "",
    ) -> None:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        text = format_ltx(sections, names=names, parents=parents, header=header)
        dest.write_bytes(text.encode("utf-8"))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--mo2-root", default="", help="MO2 instance root")
    p.add_argument("--profile", default="", help="MO2 profile (default: selected)")
    p.add_argument(
        "--no-anomaly",
        action="store_true",
        help="Skip Anomaly base gamedata / _unpacked layer",
    )
    p.add_argument("--anomaly", default="", help="Anomaly root (default: gamePath)")
    p.add_argument(
        "--skip-mod",
        action="append",
        default=[],
        metavar="NAME",
        help="Skip an enabled mod folder (repeatable)",
    )


def _vfs_from_args(args: argparse.Namespace) -> Mo2Vfs:
    return Mo2Vfs.resolve(
        args.mo2_root or None,
        profile=args.profile,
        include_anomaly=not args.no_anomaly,
        anomaly=Path(args.anomaly) if args.anomaly else None,
        skip_mods=args.skip_mod,
    )


def cmd_layers(args: argparse.Namespace) -> int:
    vfs = _vfs_from_args(args)
    layers = vfs.layers(args.under)
    info(f"MO2 root : {vfs.mo2_root}")
    info(f"Profile  : {vfs.profile}")
    info(f"Under    : {args.under}")
    info(f"Layers   : {len(layers)} (low -> high)")
    for i, layer in enumerate(layers, 1):
        print(f"{i:4d}  {layer.name}\t{layer.root}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    vfs = _vfs_from_args(args)
    info(f"MO2 root : {vfs.mo2_root}")
    info(f"Profile  : {vfs.profile}")
    layers = vfs.layers(args.under)
    info(f"Layers   : {len(layers)} under {args.under}")
    winners = vfs.winning_files(
        args.under,
        suffix=args.ext or None,
        name_glob=args.name or None,
    )
    info(f"Winners  : {len(winners)}")
    text = format_txt_list(
        winners,
        with_layer=args.with_layer,
        with_path=args.with_path,
        stem=args.stem,
    )
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(text.encode("utf-8"))
        ok(f"Wrote {out} ({len(winners)} entries)")
    else:
        sys.stdout.write(text)
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    vfs = _vfs_from_args(args)
    hit = vfs.resolve_path(args.rel, under=args.under)
    if hit is None:
        err(f"No winning file for: {args.rel} (under {args.under})")
        return 1
    print(f"rel   : {hit.rel}")
    print(f"layer : {hit.layer}")
    print(f"path  : {hit.path}")
    return 0


def cmd_merge_ltx(args: argparse.Namespace) -> int:
    vfs = _vfs_from_args(args)
    under = args.under
    info(f"MO2 root : {vfs.mo2_root}")
    info(f"Profile  : {vfs.profile}")
    files = vfs.winning_ltx(under)
    info(f"Winning LTX files: {len(files)}")

    def _prog(i: int, total: int, path: Path) -> None:
        if i == total or i % 200 == 0:
            info(f"  merge {i}/{total} ... {path.name}")

    sections, parents = merge_ltx_files(files, progress=_prog if not args.quiet else None)
    info(f"Sections merged: {len(sections)}")

    only = list(args.section or [])
    match = args.match or None
    if only or match:
        filtered: dict[str, dict[str, str]] = {}
        for name, fields in sections.items():
            if name in only or (match and fnmatch.fnmatch(name.lower(), match.lower())):
                filtered[name] = fields
        sections = filtered
        info(f"Sections after filter: {len(sections)}")

    if not sections:
        warn("No sections to write.")
        return 1

    names = sorted(sections.keys(), key=str.lower) if not only else [s for s in only if s in sections]
    # If match-only, keep sorted filtered names
    if match and not only:
        names = sorted(sections.keys(), key=str.lower)

    header = (
        f"dogma_vfs merge-ltx | MO2={vfs.mo2_root.name} | profile={vfs.profile} | under={under}"
    )
    text = format_ltx(sections, names=names, parents=parents, header=header)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(text.encode("utf-8"))
        ok(f"Wrote {out} ({len(names)} sections)")
    else:
        sys.stdout.write(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dogma_vfs",
        description=(
            "Resolve MO2/GAMMA winning files and merge final LTX. "
            "Anomaly (optional) -> mods low->high -> overwrite."
        ),
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    layers_p = sub.add_parser("layers", help="Show VFS layers low->high")
    _add_common(layers_p)
    layers_p.add_argument(
        "--under",
        default="gamedata",
        help="Subpath under each mod/overwrite/game (default: gamedata)",
    )
    layers_p.set_defaults(func=cmd_layers)

    list_p = sub.add_parser("list", help="Write/list winning files as txt")
    _add_common(list_p)
    list_p.add_argument(
        "--under",
        default="gamedata",
        help="Scan root under each layer (e.g. gamedata/sounds)",
    )
    list_p.add_argument("--ext", default="", help="Extension filter (e.g. .ogg)")
    list_p.add_argument("--name", default="", help="Filename glob (e.g. mod_system_*.ltx)")
    list_p.add_argument("-o", "--output", default="", help="Output .txt path")
    list_p.add_argument("--with-layer", action="store_true", help="Include winning layer name")
    list_p.add_argument("--with-path", action="store_true", help="Include absolute disk path")
    list_p.add_argument(
        "--stem",
        action="store_true",
        help="Strip extension (X-Ray sound paths)",
    )
    list_p.set_defaults(func=cmd_list)

    res_p = sub.add_parser("resolve", help="Resolve one relative path to its winner")
    _add_common(res_p)
    res_p.add_argument("rel", help="Path relative to --under")
    res_p.add_argument("--under", default="gamedata")
    res_p.set_defaults(func=cmd_resolve)

    merge_p = sub.add_parser(
        "merge-ltx",
        help="Merge winning LTX under configs into one file / stdout",
    )
    _add_common(merge_p)
    merge_p.add_argument(
        "--under",
        default="gamedata/configs",
        help="Config root (default: gamedata/configs)",
    )
    merge_p.add_argument(
        "--section",
        action="append",
        default=[],
        metavar="NAME",
        help="Include this section (repeatable)",
    )
    merge_p.add_argument(
        "--match",
        default="",
        help="Include sections matching glob (e.g. wpn_ak*)",
    )
    merge_p.add_argument("-o", "--output", default="", help="Output .ltx path")
    merge_p.add_argument("-q", "--quiet", action="store_true")
    merge_p.set_defaults(func=cmd_merge_ltx)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (FileNotFoundError, ValueError, OSError) as exc:
        err(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
