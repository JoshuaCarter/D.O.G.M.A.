"""Persistent on-disk cache under ``dev/sage/cache/`` (shader-cache style).

Wipe = delete the folder. Missing keys are filled on demand and appended.
DDS logical→path maps live in ``dds_index.json`` (large); other path lists in
``paths.json``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .settings import SETTINGS_PATH

CACHE_FORMAT = 1
KIND_DDS = "dds"
KIND_DESCR_FILES = "descr_files"
KIND_TEXT_FILES = "text_files"


def cache_dir() -> Path:
    return SETTINGS_PATH.parent / "cache"


def clear_cache() -> None:
    """Delete the entire cache directory (full regen next use)."""
    root = cache_dir()
    if root.is_dir():
        shutil.rmtree(root, ignore_errors=True)


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: Path, data: dict[str, Any], *, compact: bool = False) -> None:
    _ensure_dir(path.parent)
    if compact:
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")


def ensure_manifest() -> dict[str, Any]:
    root = _ensure_dir(cache_dir())
    path = root / "manifest.json"
    data = _read_json(path)
    if data is None or int(data.get("cache_format", 0) or 0) != CACHE_FORMAT:
        data = {"cache_format": CACHE_FORMAT}
        _write_json(path, data)
    return data


def roots_fingerprint(roots: list[Path]) -> str:
    parts: list[str] = []
    for r in roots:
        try:
            parts.append(str(r.resolve()).lower())
        except OSError:
            parts.append(str(r).lower())
    return "|".join(sorted(parts))


class PathIndex:
    """Logical asset name / file-list cache under ``cache/``."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        ensure_manifest()
        self._path = cache_dir() / "paths.json"
        self._dds_path = cache_dir() / "dds_index.json"
        self._data: dict[str, Any] = data if data is not None else {"cache_format": CACHE_FORMAT}
        if int(self._data.get("cache_format", 0) or 0) != CACHE_FORMAT:
            self._data = {"cache_format": CACHE_FORMAT}
        self._dirty = False
        self._dds_map: dict[str, str] | None = None
        self._dds_fp: str = ""
        self._dds_dirty = False

    @classmethod
    def load(cls) -> PathIndex:
        ensure_manifest()
        path = cache_dir() / "paths.json"
        data = _read_json(path)
        if data is None or int(data.get("cache_format", 0) or 0) != CACHE_FORMAT:
            return cls({"cache_format": CACHE_FORMAT})
        return cls(data)

    def save(self) -> None:
        if self._dirty:
            self._data["cache_format"] = CACHE_FORMAT
            _write_json(self._path, self._data)
            self._dirty = False
        if self._dds_dirty and self._dds_map is not None:
            _write_json(
                self._dds_path,
                {
                    "cache_format": CACHE_FORMAT,
                    "fingerprint": self._dds_fp,
                    "files": self._dds_map,
                },
                compact=True,
            )
            self._dds_dirty = False

    def invalidate(self) -> None:
        """Drop path + DDS indexes (font glyph meta kept; revalidates via mtime)."""
        self._data = {"cache_format": CACHE_FORMAT}
        self._dirty = False
        self._dds_map = None
        self._dds_fp = ""
        self._dds_dirty = False
        for path in (self._path, self._dds_path):
            if path.is_file():
                try:
                    path.unlink()
                except OSError:
                    pass

    def _bucket(self, kind: str) -> dict[str, Any]:
        bucket = self._data.get(kind)
        if not isinstance(bucket, dict):
            bucket = {}
            self._data[kind] = bucket
            self._dirty = True
        return bucket

    def get_dds(self, logical: str) -> Path | None:
        """Legacy single-key cache (prefer ``get_dds_map`` for bulk lookups)."""
        key = logical.strip().replace("/", "\\").lower()
        if key.endswith(".dds"):
            key = key[:-4]
        # Prefer the full scan map when loaded for this session.
        if self._dds_map is not None and key in self._dds_map:
            path = Path(self._dds_map[key])
            if path.is_file():
                return path
            self._dds_map.pop(key, None)
            self._dds_dirty = True
            return None
        entry = self._bucket(KIND_DDS).get(key)
        if not isinstance(entry, dict):
            return None
        raw = str(entry.get("path") or "")
        if not raw:
            return None
        path = Path(raw)
        if not path.is_file():
            self._bucket(KIND_DDS).pop(key, None)
            self._dirty = True
            return None
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None
        cached_mtime = entry.get("mtime")
        if cached_mtime is not None and abs(float(cached_mtime) - mtime) > 0.5:
            self._bucket(KIND_DDS).pop(key, None)
            self._dirty = True
            return None
        return path

    def drop_dds(self, logical: str) -> None:
        """Remove a stale logical→DDS mapping from memory + pending disk write."""
        key = logical.strip().replace("/", "\\").lower()
        if key.endswith(".dds"):
            key = key[:-4]
        if not key:
            return
        if self._bucket(KIND_DDS).pop(key, None) is not None:
            self._dirty = True
        if self._dds_map is not None and key in self._dds_map:
            self._dds_map.pop(key, None)
            self._dds_dirty = True

    def put_dds(self, logical: str, path: Path) -> None:
        key = logical.strip().replace("/", "\\").lower()
        if key.endswith(".dds"):
            key = key[:-4]
        if not key:
            return
        try:
            resolved = path.resolve()
            mtime = resolved.stat().st_mtime
        except OSError:
            resolved = path
            mtime = 0.0
        self._bucket(KIND_DDS)[key] = {"path": str(resolved), "mtime": mtime}
        self._dirty = True
        if self._dds_map is not None:
            self._dds_map[key] = str(resolved)
            self._dds_dirty = True

    def get_dds_map(self, fingerprint: str) -> dict[str, Path] | None:
        """Full logical→DDS map for ``fingerprint`` (None = miss / rebuild)."""
        if self._dds_map is not None and self._dds_fp == fingerprint:
            return {k: Path(v) for k, v in self._dds_map.items()}
        data = _read_json(self._dds_path)
        if data is None or int(data.get("cache_format", 0) or 0) != CACHE_FORMAT:
            return None
        if str(data.get("fingerprint") or "") != fingerprint:
            return None
        raw = data.get("files")
        if not isinstance(raw, dict) or not raw:
            return None
        self._dds_fp = fingerprint
        self._dds_map = {str(k).lower(): str(v) for k, v in raw.items() if k and v}
        return {k: Path(v) for k, v in self._dds_map.items()}

    def put_dds_map(self, fingerprint: str, files: dict[str, Path]) -> None:
        mapping: dict[str, str] = {}
        for logical, path in files.items():
            key = str(logical).strip().replace("/", "\\").lower()
            if key.endswith(".dds"):
                key = key[:-4]
            if not key:
                continue
            try:
                mapping[key] = str(path.resolve())
            except OSError:
                mapping[key] = str(path)
        self._dds_fp = fingerprint
        self._dds_map = mapping
        self._dds_dirty = True

    def get_file_list(self, kind: str, fingerprint: str) -> list[Path] | None:
        entry = self._bucket(kind).get(fingerprint)
        if not isinstance(entry, dict):
            return None
        raw_list = entry.get("files")
        if not isinstance(raw_list, list):
            return None
        out: list[Path] = []
        for raw in raw_list:
            p = Path(str(raw))
            if p.is_file():
                out.append(p)
        # Empty after wipe of sources → treat as miss so we rescan.
        if not out and raw_list:
            return None
        return out

    def put_file_list(self, kind: str, fingerprint: str, files: list[Path]) -> None:
        paths: list[str] = []
        for f in files:
            try:
                paths.append(str(f.resolve()))
            except OSError:
                paths.append(str(f))
        self._bucket(kind)[fingerprint] = {"files": paths}
        self._dirty = True


def font_cache_dir(atlas_stem: str) -> Path:
    safe = atlas_stem.replace("\\", "_").replace("/", "_").replace(" ", "_")
    return cache_dir() / "fonts" / safe


def load_font_cache(atlas_stem: str) -> dict[str, Any] | None:
    """Return glyph meta if valid vs source DDS/INI mtimes (atlas always from DDS)."""
    ensure_manifest()
    folder = font_cache_dir(atlas_stem)
    meta = _read_json(folder / "meta.json")
    if meta is None:
        return None
    if int(meta.get("cache_format", 0) or 0) != CACHE_FORMAT:
        return None
    for key in ("dds_path", "ini_path"):
        src = Path(str(meta.get(key) or ""))
        if not src.is_file():
            return None
        try:
            mtime = src.stat().st_mtime
        except OSError:
            return None
        cached = meta.get(f"{key}_mtime")
        if cached is None or abs(float(cached) - mtime) > 0.5:
            return None
    return meta


def save_font_cache(atlas_stem: str, *, meta: dict[str, Any]) -> None:
    """Persist parsed glyph coords only — never a re-encoded atlas image."""
    ensure_manifest()
    folder = _ensure_dir(font_cache_dir(atlas_stem))
    meta = dict(meta)
    meta["cache_format"] = CACHE_FORMAT
    _write_json(folder / "meta.json", meta)
    # Drop legacy blurred PNG atlases from older SAGE builds.
    legacy_png = folder / "atlas.png"
    if legacy_png.is_file():
        try:
            legacy_png.unlink()
        except OSError:
            pass
