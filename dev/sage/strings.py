"""Resolve Stalker UI string-table IDs to localized text.

Directory lists come from Setup / Rescan. String tables are not fully indexed at
launch — lookups search saved roots for IDs referenced by the open document.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .cache_store import KIND_TEXT_FILES, PathIndex, roots_fingerprint
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
        self._strings.clear()
        self._missing.clear()
        self._text_files = None

    def rebuild(self) -> None:
        """Compatibility — clears caches; no full-tree index."""
        self.clear_cache()

    def _iter_text_files(self) -> list[Path]:
        if self._text_files is not None:
            return self._text_files
        fp = roots_fingerprint(
            list(self.gamedata_text_roots) + list(self.text_scan_roots)
        ) + f"|lang={self.lang.lower()}"
        if self.path_index is not None:
            cached = self.path_index.get_file_list(KIND_TEXT_FILES, fp)
            if cached is not None:
                self._text_files = cached
                return cached
        files: list[Path] = []
        seen: set[Path] = set()

        def _add(path: Path) -> None:
            try:
                key = path.resolve()
            except OSError:
                return
            if key in seen:
                return
            seen.add(key)
            files.append(path)

        for root in self.gamedata_text_roots:
            if not root.is_dir():
                continue
            # Root is usually …/text/eng
            if root.name.lower() == self.lang.lower():
                try:
                    for path in sorted(root.glob("*.xml")):
                        _add(path)
                except OSError:
                    continue
            else:
                lang_dir = root / "configs" / "text" / self.lang
                if lang_dir.is_dir():
                    try:
                        for path in sorted(lang_dir.glob("*.xml")):
                            _add(path)
                    except OSError:
                        pass

        for root in self.text_scan_roots:
            if not root.is_dir():
                continue
            try:
                for path in sorted(root.rglob(f"**/configs/text/{self.lang}/*.xml")):
                    _add(path)
                for path in sorted(root.rglob(f"**/text/{self.lang}/*.xml")):
                    _add(path)
            except OSError:
                continue

        self._text_files = files
        if self.path_index is not None:
            self.path_index.put_file_list(KIND_TEXT_FILES, fp, files)
        return files

    def warm_ids(self, ids: set[str]) -> None:
        if not ids:
            return
        # Last file wins so later roots override base game.
        wanted = set(ids)
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
        for sid in ids:
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

    def scan_catalog(self) -> list[StringCatalogEntry]:
        """Load every string id under text roots. Later files override."""
        by_id: dict[str, StringCatalogEntry] = {}
        for path in self._iter_text_files():
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
