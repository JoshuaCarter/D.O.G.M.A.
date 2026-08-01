"""Resolve Stalker UI string-table IDs to localized text.

Directory lists come from Setup / Rescan. String tables are not fully indexed at
launch — lookups search saved roots for IDs referenced by the open document.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .cache_store import INSTALL_ORDER, KIND_TEXT, PathIndex
from .model import LayoutNode

# Engine color / format codes in string bodies, e.g. %c[0,255,255,255]
_ENGINE_MARKUP = re.compile(r"%c\[[^\]]*\]")
_STRING_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class ResolvedString:
    text: str = ""
    source: Path | None = None
    error: str = ""
    is_literal: bool = False


@dataclass(frozen=True)
class StringCatalogEntry:
    """One pickable string-table id + winning body / source file."""

    string_id: str
    text: str
    source: Path


@dataclass(frozen=True)
class StringRootGroup:
    """One string-picker scope: label + text dirs."""

    label: str
    text_roots: tuple[Path, ...]


def looks_like_string_id(content: str) -> bool:
    return bool(content) and bool(_STRING_ID.fullmatch(content))


def strip_engine_markup(text: str) -> str:
    return _ENGINE_MARKUP.sub("", text).replace("\\n", "\n")


def collect_doc_string_ids(doc: LayoutNode) -> set[str]:
    out: set[str] = set()
    for node in doc.iter_all():
        if node.text is None or not node.text.content:
            continue
        content = node.text.content.strip()
        if looks_like_string_id(content):
            out.add(content)
    return out


class StringResolver:
    """Resolve string ids from configs/text/<lang>/ under saved roots."""

    def __init__(
        self,
        text_scan_roots: list[Path],
        gamedata_text_roots: list[Path],
        lang: str = "eng",
        path_index: PathIndex | None = None,
    ) -> None:
        self.text_scan_roots = text_scan_roots
        self.gamedata_text_roots = gamedata_text_roots
        self.lang = lang
        self.path_index = path_index
        self._strings: dict[str, tuple[str, Path]] = {}
        self._missing: set[str] = set()
        self._text_files: list[Path] | None = None

    @property
    def count(self) -> int:
        return len(self._strings)

    def clear_cache(self) -> None:
        """Full clear including text-file list (rescan / rebind)."""
        self._strings.clear()
        self._missing.clear()
        self._text_files = None

    def clear_document_cache(self) -> None:
        """No-op for strings — resolved ids stay warm across file opens."""
        return

    def rebuild(self) -> None:
        """Compatibility — clears caches; no full-tree index."""
        self.clear_cache()

    def _text_roots(self) -> list[Path]:
        return list(self.gamedata_text_roots) + list(self.text_scan_roots)

    def _scan_text_root(self, root: Path, *, deep: bool) -> dict[str, Path]:
        by_name: dict[str, Path] = {}

        def _add(path: Path) -> None:
            by_name[path.name.lower()] = path

        if not root.is_dir():
            return by_name
        if deep:
            try:
                for path in sorted(root.rglob(f"**/configs/text/{self.lang}/*.xml")):
                    _add(path)
                for path in sorted(root.rglob(f"**/text/{self.lang}/*.xml")):
                    _add(path)
            except OSError:
                return by_name
            return by_name
        # Gamedata root is usually …/text/eng
        if root.name.lower() == self.lang.lower():
            try:
                for path in sorted(root.glob("*.xml")):
                    _add(path)
            except OSError:
                return by_name
        else:
            lang_dir = root / "configs" / "text" / self.lang
            if lang_dir.is_dir():
                try:
                    for path in sorted(lang_dir.glob("*.xml")):
                        _add(path)
                except OSError:
                    pass
        return by_name

    def _iter_text_files(self, *, force: bool = False) -> list[Path]:
        if self._text_files is not None and not force:
            return self._text_files
        roots = self._text_roots()
        scan_keys: set[str] = set()
        for p in self.text_scan_roots:
            try:
                scan_keys.add(str(p.resolve()).lower())
            except OSError:
                scan_keys.add(str(p).lower())

        if not force and self.path_index is not None:
            cached = self.path_index.merge_name_shards(
                KIND_TEXT, roots, lang=self.lang
            )
            if cached is not None:
                self._text_files = [cached[k] for k in sorted(cached)]
                return self._text_files

        by_name: dict[str, Path] = {}
        groups = (
            self.path_index.group_roots(roots)
            if self.path_index is not None
            else {INSTALL_ORDER[-1]: roots}
        )
        for install in INSTALL_ORDER:
            ir = groups.get(install) or []
            if not ir:
                continue
            shard: dict[str, Path] | None = None
            if not force and self.path_index is not None:
                shard = self.path_index.get_install_names(
                    KIND_TEXT, install, ir, lang=self.lang
                )
            if shard is None:
                shard = {}
                for root in ir:
                    try:
                        key = str(root.resolve()).lower()
                    except OSError:
                        key = str(root).lower()
                    deep = key in scan_keys
                    shard.update(self._scan_text_root(root, deep=deep))
                if self.path_index is not None:
                    self.path_index.put_install_names(
                        KIND_TEXT, install, ir, shard, lang=self.lang
                    )
            by_name.update(shard)
        self._text_files = [by_name[k] for k in sorted(by_name)]
        if self.path_index is not None:
            self.path_index.save()
        return self._text_files

    def ensure_indexes(self, *, force: bool = False) -> dict[str, int]:
        """Load or build the text XML file list cache."""
        if force:
            self._text_files = None
        return {"text_files": len(self._iter_text_files(force=force))}

    def warm_ids(self, ids: set[str]) -> None:
        if not ids:
            return
        # Skip ids already resolved (or known missing) so reopen stays cheap.
        wanted = {
            sid
            for sid in ids
            if sid not in self._strings and sid not in self._missing
        }
        if not wanted:
            return
        # Last file wins so later roots override base game.
        needles = [f'id="{sid}"'.encode("ascii", "ignore") for sid in wanted]
        needles += [f"id='{sid}'".encode("ascii", "ignore") for sid in wanted]
        for path in self._iter_text_files():
            try:
                blob = path.read_bytes()
            except OSError:
                continue
            if not any(n in blob for n in needles):
                continue
            self._parse_bytes_allow_override(blob, path, wanted)
        for sid in wanted:
            if sid not in self._strings:
                self._missing.add(sid)

    def _parse_bytes_allow_override(
        self, blob: bytes, path: Path, wanted: set[str]
    ) -> None:
        raw = None
        for enc in ("utf-8-sig", "windows-1251", "cp1251", "latin-1"):
            try:
                raw = blob.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if raw is None:
            return
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            return
        for el in root.iter("string"):
            sid = (el.get("id") or "").strip()
            if not sid or sid not in wanted:
                continue
            text_el = el.find("text")
            if text_el is None:
                body = (el.text or "").strip()
            else:
                body = (text_el.text or "").strip()
            self._strings[sid] = (body, path)

    def warm_for_document(self, doc: LayoutNode) -> None:
        self.warm_ids(collect_doc_string_ids(doc))

    def _text_files_for_roots(self, roots: list[Path]) -> list[Path]:
        """Collect text XMLs under ``roots`` (scan roots deep, gamedata shallow)."""
        scan_keys: set[str] = set()
        for p in self.text_scan_roots:
            try:
                scan_keys.add(str(p.resolve()).lower())
            except OSError:
                scan_keys.add(str(p).lower())
        by_name: dict[str, Path] = {}
        for root in roots:
            try:
                key = str(root.resolve()).lower()
            except OSError:
                key = str(root).lower()
            by_name.update(self._scan_text_root(root, deep=key in scan_keys))
        return [by_name[k] for k in sorted(by_name)]

    def scan_catalog(
        self, source_roots: list[Path] | None = None
    ) -> list[StringCatalogEntry]:
        """Load string ids under text roots (or ``source_roots``). Later files win."""
        by_id: dict[str, StringCatalogEntry] = {}
        files = (
            self._iter_text_files()
            if source_roots is None
            else self._text_files_for_roots(source_roots)
        )
        for path in files:
            try:
                blob = path.read_bytes()
            except OSError:
                continue
            raw = None
            for enc in ("utf-8-sig", "windows-1251", "cp1251", "latin-1"):
                try:
                    raw = blob.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            if raw is None:
                continue
            try:
                root = ET.fromstring(raw)
            except ET.ParseError:
                continue
            for el in root.iter("string"):
                sid = (el.get("id") or "").strip()
                if not sid:
                    continue
                text_el = el.find("text")
                if text_el is None:
                    body = (el.text or "").strip()
                else:
                    body = (text_el.text or "").strip()
                by_id[sid] = StringCatalogEntry(
                    string_id=sid, text=body, source=path
                )
                self._strings[sid] = (body, path)
                self._missing.discard(sid)
        return sorted(by_id.values(), key=lambda e: e.string_id.lower())

    def remember_string(self, entry: StringCatalogEntry) -> None:
        self._strings[entry.string_id] = (entry.text, entry.source)
        self._missing.discard(entry.string_id)

    def resolve(self, content: str | None) -> ResolvedString:
        if not content:
            return ResolvedString(error="empty text")
        content = content.strip()
        if not content:
            return ResolvedString(error="empty text")
        if content not in self._strings and content not in self._missing:
            if looks_like_string_id(content):
                self.warm_ids({content})
        hit = self._strings.get(content)
        if hit is not None:
            body, src = hit
            return ResolvedString(text=strip_engine_markup(body), source=src, error="")
        if looks_like_string_id(content):
            return ResolvedString(error=f"missing string id: {content}")
        return ResolvedString(
            text=strip_engine_markup(content),
            source=None,
            error="",
            is_literal=True,
        )


def build_string_picker_root_groups(
    resolver: StringResolver,
    *,
    anomaly_root: str | Path = "",
    gamma_root: str | Path = "",
) -> list[StringRootGroup]:
    """Custom first (default), then Anomaly / GAMMA / All."""
    custom = tuple(p for p in resolver.text_scan_roots if p.is_dir())
    anom = Path(str(anomaly_root).strip()).expanduser() if str(anomaly_root).strip() else None
    gam = Path(str(gamma_root).strip()).expanduser() if str(gamma_root).strip() else None

    def _under(paths: list[Path], root: Path | None) -> tuple[Path, ...]:
        if root is None or not root.is_dir():
            return ()
        try:
            root_r = root.resolve()
        except OSError:
            root_r = root
        out: list[Path] = []
        for p in paths:
            try:
                p.resolve().relative_to(root_r)
            except (OSError, ValueError):
                continue
            out.append(p)
        return tuple(out)

    all_roots = tuple(
        p
        for p in list(resolver.gamedata_text_roots) + list(resolver.text_scan_roots)
        if p.is_dir()
    )
    anom_roots = _under(list(resolver.gamedata_text_roots), anom)
    gam_roots = _under(list(resolver.gamedata_text_roots), gam)

    groups: list[StringRootGroup] = []
    if custom:
        groups.append(StringRootGroup("Custom", custom))
    if anom_roots:
        groups.append(StringRootGroup("Anomaly", anom_roots))
    if gam_roots:
        groups.append(StringRootGroup("GAMMA", gam_roots))
    groups.append(StringRootGroup("All", all_roots))
    return groups
