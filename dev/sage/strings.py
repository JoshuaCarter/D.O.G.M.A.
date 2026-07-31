"""Resolve Stalker UI string-table IDs to localized text."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

# Engine color / format codes in string bodies, e.g. %c[0,255,255,255]
_ENGINE_MARKUP = re.compile(r"%c\[[^\]]*\]")
_STRING_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class ResolvedString:
    text: str = ""
    source: Path | None = None
    error: str = ""
    is_literal: bool = False


def looks_like_string_id(content: str) -> bool:
    return bool(content) and bool(_STRING_ID.fullmatch(content))


def strip_engine_markup(text: str) -> str:
    return _ENGINE_MARKUP.sub("", text).replace("\\n", "\n")


class StringResolver:
    """Index string_table XML files under configs/text/<lang>/."""

    def __init__(
        self,
        text_scan_roots: list[Path],
        gamedata_text_roots: list[Path],
        lang: str = "eng",
    ) -> None:
        self.text_scan_roots = text_scan_roots
        self.gamedata_text_roots = gamedata_text_roots
        self.lang = lang
        self._strings: dict[str, tuple[str, Path]] = {}
        self.rebuild()

    def rebuild(self) -> None:
        self._strings.clear()
        # Base game / unpacked first; scan roots (e.g. DOGMA src) override last.
        for root in self.gamedata_text_roots:
            self._ingest_root(root)
        for root in self.text_scan_roots:
            self._ingest_root(root)

    @property
    def count(self) -> int:
        return len(self._strings)

    def _ingest_root(self, root: Path) -> None:
        if not root.is_dir():
            return
        files: list[Path] = []
        # Direct eng folder
        if root.name.lower() == self.lang.lower():
            files.extend(sorted(root.glob("*.xml")))
        # …/configs/text/eng
        lang_dir = root / "configs" / "text" / self.lang
        if lang_dir.is_dir():
            files.extend(sorted(lang_dir.glob("*.xml")))
        # …/text/eng
        lang_dir2 = root / "text" / self.lang
        if lang_dir2.is_dir():
            files.extend(sorted(lang_dir2.glob("*.xml")))
        # Recursive under scan roots
        try:
            files.extend(sorted(root.rglob(f"**/configs/text/{self.lang}/**/*.xml")))
            files.extend(sorted(root.rglob(f"**/text/{self.lang}/**/*.xml")))
        except OSError:
            pass
        seen: set[Path] = set()
        for path in files:
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            self._parse_file(path)

    def _parse_file(self, path: Path) -> None:
        raw = None
        for enc in ("utf-8-sig", "windows-1251", "cp1251", "latin-1"):
            try:
                raw = path.read_text(encoding=enc)
                break
            except (OSError, UnicodeDecodeError):
                continue
        if raw is None:
            return
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            return
        tag = (root.tag or "").lower()
        if tag not in ("string_table", "string_table_xml"):
            # Some files wrap differently; still scan for <string id=…>
            pass
        for el in root.iter("string"):
            sid = (el.get("id") or "").strip()
            if not sid:
                continue
            text_el = el.find("text")
            if text_el is None:
                body = (el.text or "").strip()
            else:
                body = (text_el.text or "").strip()
            self._strings[sid] = (body, path)

    def resolve(self, content: str | None) -> ResolvedString:
        if not content:
            return ResolvedString(error="empty text")
        content = content.strip()
        if not content:
            return ResolvedString(error="empty text")
        hit = self._strings.get(content)
        if hit is not None:
            body, src = hit
            return ResolvedString(text=strip_engine_markup(body), source=src, error="")
        if looks_like_string_id(content):
            return ResolvedString(error=f"missing string id: {content}")
        # Treat as literal caption
        return ResolvedString(
            text=strip_engine_markup(content),
            source=None,
            error="",
            is_literal=True,
        )
