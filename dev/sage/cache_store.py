"""Persistent on-disk cache under ``dev/sage/cache/`` (shader-cache style).

Bump ``CACHE_VERSION`` when the on-disk layout changes — next open wipes the
folder and regenerates. Path indexes are one JSON per install name
(``anomaly`` / ``gamma`` / ``custom``), not per mod folder.

Layout:
  version                 — single integer matching CACHE_VERSION
  meta.json               — font name list
  dds/{anomaly,gamma,custom}.json     — logical→.dds (winning paths)
  descr/{anomaly,gamma,custom}.json   — basename→textures_descr xml
  text/{anomaly,gamma,custom}_{lang}.json — basename→text xml
  dds_rgba/               — decoded PNG sheets (mtime-keyed)
  fonts/*.json            — glyph meta per atlas stem
  thumbs/                 — low-res sheet proxies for the texture picker
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from .settings import SETTINGS_PATH

# Bump when cache file shapes change; mismatched/missing → wipe + regen.
CACHE_VERSION = 6
KIND_DESCR = "descr"
KIND_TEXT = "text"
KIND_DDS = "dds"

INSTALL_ANOMALY = "anomaly"
INSTALL_GAMMA = "gamma"
INSTALL_CUSTOM = "custom"
INSTALL_ORDER = (INSTALL_ANOMALY, INSTALL_GAMMA, INSTALL_CUSTOM)

# Sheet-proxy rule version (in path hash). Bump when downscale rule changes.
SHEET_PROXY_RULE_VER = 3


def cache_dir() -> Path:
    return SETTINGS_PATH.parent / "cache"


def version_path() -> Path:
    return cache_dir() / "version"


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


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """Pretty-print JSON (one entry per line for maps/arrays)."""
    _ensure_dir(path.parent)
    text = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    path.write_text(text, encoding="utf-8")


def _read_cache_version() -> int | None:
    path = version_path()
    if not path.is_file():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def ensure_cache_version() -> None:
    """If on-disk version ≠ code, delete the cache and stamp ``CACHE_VERSION``."""
    root = cache_dir()
    if _read_cache_version() == CACHE_VERSION:
        # Drop obsolete monolithic indexes from older SAGE builds.
        for legacy in ("manifest.json", "paths.json", "dds_index.json"):
            path = root / legacy
            if path.is_file():
                try:
                    path.unlink()
                except OSError:
                    pass
        return
    clear_cache()
    _ensure_dir(cache_dir())
    version_path().write_text(f"{CACHE_VERSION}\n", encoding="utf-8")


def normalize_roots(roots: list[Path]) -> list[str]:
    """Stable absolute paths for cache invalidation (scan order preserved)."""
    out: list[str] = []
    for r in roots:
        try:
            out.append(str(r.resolve()))
        except OSError:
            out.append(str(r))
    return out


def normalize_root(root: Path) -> str:
    return normalize_roots([root])[0]


def roots_match(cached: Any, roots: list[Path]) -> bool:
    if not isinstance(cached, list):
        return False
    want = [p.lower() for p in normalize_roots(roots)]
    have = [str(p).lower() for p in cached]
    return have == want


def _path_under(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except (ValueError, OSError):
        try:
            path.relative_to(base)
            return True
        except ValueError:
            return False


def classify_install(
    root: Path,
    *,
    anomaly_root: Path | None,
    gamma_root: Path | None,
) -> str:
    """Map a concrete asset folder to anomaly / gamma / custom."""
    if anomaly_root is not None and anomaly_root.is_dir() and _path_under(root, anomaly_root):
        return INSTALL_ANOMALY
    if gamma_root is not None and gamma_root.is_dir() and _path_under(root, gamma_root):
        return INSTALL_GAMMA
    return INSTALL_CUSTOM


def group_roots_by_install(
    roots: list[Path],
    *,
    anomaly_root: Path | None,
    gamma_root: Path | None,
) -> dict[str, list[Path]]:
    """Partition concrete roots into install buckets (scan order preserved)."""
    groups: dict[str, list[Path]] = {k: [] for k in INSTALL_ORDER}
    for root in roots:
        groups[
            classify_install(
                root, anomaly_root=anomaly_root, gamma_root=gamma_root
            )
        ].append(root)
    return groups


def install_shard_path(
    kind: str, install: str, *, lang: str | None = None
) -> Path:
    """``cache/dds/gamma.json`` or ``cache/text/anomaly_eng.json``."""
    name = install.lower().strip() or INSTALL_CUSTOM
    if lang:
        name = f"{name}_{lang.lower().strip()}"
    return cache_dir() / kind / f"{name}.json"


def asset_index_scan_needed(
    *,
    texture_scan_roots: list[Path],
    gamedata_texture_roots: list[Path],
    descr_scan_roots: list[Path],
    gamedata_descr_roots: list[Path],
    text_scan_roots: list[Path] | None = None,
    gamedata_text_roots: list[Path] | None = None,
    anomaly_root: str | Path = "",
    gamma_root: str | Path = "",
    text_lang: str = "eng",
) -> bool:
    """True when any install index file is missing or its root list is stale."""
    ensure_cache_version()
    from .textures import iter_texture_dir_roots

    anom = Path(str(anomaly_root).strip()) if str(anomaly_root).strip() else None
    gam = Path(str(gamma_root).strip()) if str(gamma_root).strip() else None
    if anom is not None and not anom.is_dir():
        anom = None
    if gam is not None and not gam.is_dir():
        gam = None

    def _stale(kind: str, roots: list[Path], *, lang: str | None = None) -> bool:
        groups = group_roots_by_install(
            roots, anomaly_root=anom, gamma_root=gam
        )
        for install in INSTALL_ORDER:
            ir = groups[install]
            if not ir:
                continue
            path = install_shard_path(kind, install, lang=lang)
            data = _read_json(path)
            if data is None:
                return True
            if not roots_match(data.get("roots"), ir):
                return True
        return False

    dds_roots = iter_texture_dir_roots(
        gamedata_texture_roots=gamedata_texture_roots,
        texture_scan_roots=texture_scan_roots,
    )
    if _stale(KIND_DDS, dds_roots):
        return True
    if _stale(KIND_DESCR, list(gamedata_descr_roots) + list(descr_scan_roots)):
        return True
    text_roots = list(gamedata_text_roots or []) + list(text_scan_roots or [])
    if text_roots and _stale(KIND_TEXT, text_roots, lang=text_lang or "eng"):
        return True
    return False


def _basename_key(path: Path | str) -> str:
    return Path(path).name.lower()


def _logical_dds_key(logical: str) -> str:
    key = logical.strip().replace("/", "\\").lower()
    if key.endswith(".dds"):
        key = key[:-4]
    return key


def _files_to_str_map(files: dict[str, Path], *, logical: bool) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for name, path in files.items():
        key = _logical_dds_key(str(name)) if logical else _basename_key(name)
        if not key:
            continue
        try:
            cleaned[key] = str(Path(path).resolve())
        except OSError:
            cleaned[key] = str(path)
    return {k: cleaned[k] for k in sorted(cleaned)}


def _files_from_raw(raw: Any, *, logical: bool) -> dict[str, Path] | None:
    if not isinstance(raw, dict) or not raw:
        return None
    out: dict[str, Path] = {}
    for name, path_s in raw.items():
        key = _logical_dds_key(str(name)) if logical else str(name).lower()
        if not key:
            continue
        p = Path(str(path_s))
        if p.is_file():
            out[key] = p
    if not out and raw:
        return None
    return out


class PathIndex:
    """Install-level path caches under ``cache/dds|descr|text/`` + ``meta.json`` fonts."""

    def __init__(self) -> None:
        ensure_cache_version()
        self._meta_path = cache_dir() / "meta.json"
        self._meta: dict[str, Any] = {}
        self._meta_dirty = False
        self._anomaly_root: Path | None = None
        self._gamma_root: Path | None = None
        # Merged DDS view (later installs/roots win); rebuilt from shards as needed.
        self._dds_map: dict[str, str] | None = None
        self._dds_roots: list[str] = []

    @classmethod
    def load(cls) -> PathIndex:
        ensure_cache_version()
        idx = cls()
        data = _read_json(idx._meta_path)
        if data is not None:
            idx._meta = data
        return idx

    def configure_installs(
        self,
        anomaly_root: str | Path = "",
        gamma_root: str | Path = "",
    ) -> None:
        """Set Anomaly / GAMMA bases used to classify concrete asset folders."""
        anom = Path(str(anomaly_root).strip()) if str(anomaly_root).strip() else None
        gam = Path(str(gamma_root).strip()) if str(gamma_root).strip() else None
        self._anomaly_root = anom if anom is not None and anom.is_dir() else None
        self._gamma_root = gam if gam is not None and gam.is_dir() else None

    def classify(self, root: Path) -> str:
        return classify_install(
            root,
            anomaly_root=self._anomaly_root,
            gamma_root=self._gamma_root,
        )

    def group_roots(self, roots: list[Path]) -> dict[str, list[Path]]:
        return group_roots_by_install(
            roots,
            anomaly_root=self._anomaly_root,
            gamma_root=self._gamma_root,
        )

    def save(self) -> None:
        if self._meta_dirty:
            _write_json(self._meta_path, self._meta)
            self._meta_dirty = False

    def invalidate(self) -> None:
        """Drop all path/DDS/descr/text shards (fonts glyph meta kept)."""
        self._dds_map = None
        self._dds_roots = []
        self._meta = {}
        self._meta_dirty = False
        if self._meta_path.is_file():
            try:
                self._meta_path.unlink()
            except OSError:
                pass
        for kind in (KIND_DDS, KIND_DESCR, KIND_TEXT):
            folder = cache_dir() / kind
            if folder.is_dir():
                shutil.rmtree(folder, ignore_errors=True)

    def invalidate_roots(self, roots: list[Path]) -> None:
        """Delete install index files touched by the given concrete roots."""
        installs = {self.classify(root) for root in roots}
        for install in installs:
            for kind in (KIND_DDS, KIND_DESCR, KIND_TEXT):
                folder = cache_dir() / kind
                if not folder.is_dir():
                    continue
                for path in folder.glob(f"{install}*.json"):
                    try:
                        path.unlink()
                    except OSError:
                        pass
        self._dds_map = None
        self._dds_roots = []

    # --- fonts (global meta) ---

    def get_fonts(self) -> list[str] | None:
        raw = self._meta.get("fonts")
        if not isinstance(raw, list) or not raw:
            return None
        return [str(x) for x in raw if x]

    def put_fonts(self, names: list[str]) -> None:
        cleaned = sorted({str(n).strip() for n in names if str(n).strip()}, key=str.lower)
        if self._meta.get("fonts") == cleaned:
            return
        self._meta["fonts"] = cleaned
        self._meta_dirty = True

    # --- install shards ---

    def get_install_dds(
        self, install: str, roots: list[Path]
    ) -> dict[str, Path] | None:
        if not roots:
            return {}
        data = _read_json(install_shard_path(KIND_DDS, install))
        if data is None:
            return None
        if not roots_match(data.get("roots"), roots):
            return None
        return _files_from_raw(data.get("files"), logical=True)

    def put_install_dds(
        self,
        install: str,
        roots: list[Path],
        files: dict[str, Path],
        *,
        bust_merge: bool = True,
    ) -> None:
        _write_json(
            install_shard_path(KIND_DDS, install),
            {
                "install": install,
                "roots": normalize_roots(roots),
                "files": _files_to_str_map(files, logical=True),
            },
        )
        if bust_merge:
            self._dds_map = None

    def get_install_names(
        self,
        kind: str,
        install: str,
        roots: list[Path],
        *,
        lang: str | None = None,
    ) -> dict[str, Path] | None:
        if kind not in (KIND_DESCR, KIND_TEXT):
            return None
        if not roots:
            return {}
        lang_key = (lang or "eng").lower() if kind == KIND_TEXT else None
        data = _read_json(install_shard_path(kind, install, lang=lang_key))
        if data is None:
            return None
        if not roots_match(data.get("roots"), roots):
            return None
        if kind == KIND_TEXT and str(data.get("lang") or "").lower() != lang_key:
            return None
        return _files_from_raw(data.get("files"), logical=False)

    def put_install_names(
        self,
        kind: str,
        install: str,
        roots: list[Path],
        files: dict[str, Path],
        *,
        lang: str | None = None,
    ) -> None:
        if kind not in (KIND_DESCR, KIND_TEXT):
            return
        payload: dict[str, Any] = {
            "install": install,
            "roots": normalize_roots(roots),
            "files": _files_to_str_map(files, logical=False),
        }
        lang_key = None
        if kind == KIND_TEXT:
            lang_key = (lang or "eng").lower()
            payload["lang"] = lang_key
        _write_json(install_shard_path(kind, install, lang=lang_key), payload)

    # Back-compat names used by older call sites (install-scoped).
    def get_dds_shard(self, root: Path) -> dict[str, Path] | None:
        install = self.classify(root)
        roots = [
            Path(r)
            for r in self._dds_roots
            if self.classify(Path(r)) == install
        ]
        if not roots:
            roots = [root]
        return self.get_install_dds(install, roots)

    def put_dds_shard(
        self, root: Path, files: dict[str, Path], *, bust_merge: bool = True
    ) -> None:
        install = self.classify(root)
        roots = [
            Path(r)
            for r in self._dds_roots
            if self.classify(Path(r)) == install
        ]
        if not roots:
            roots = [root]
        # Merge into existing install map when updating a single root's worth.
        existing = self.get_install_dds(install, roots) or {}
        existing.update(files)
        self.put_install_dds(install, roots, existing, bust_merge=bust_merge)

    def get_name_shard(
        self,
        kind: str,
        root: Path,
        *,
        lang: str | None = None,
    ) -> dict[str, Path] | None:
        install = self.classify(root)
        return self.get_install_names(kind, install, [root], lang=lang)

    def put_name_shard(
        self,
        kind: str,
        root: Path,
        files: dict[str, Path],
        *,
        lang: str | None = None,
    ) -> None:
        install = self.classify(root)
        self.put_install_names(kind, install, [root], files, lang=lang)

    def merge_dds_shards(self, roots: list[Path]) -> dict[str, Path] | None:
        """Merged logical→path if every non-empty install index is present."""
        groups = self.group_roots(roots)
        merged: dict[str, Path] = {}
        for install in INSTALL_ORDER:
            ir = groups[install]
            if not ir:
                continue
            shard = self.get_install_dds(install, ir)
            if shard is None:
                return None
            merged.update(shard)
        self._dds_roots = normalize_roots(roots)
        self._dds_map = {k: str(v) for k, v in merged.items()}
        return dict(merged)

    def merge_name_shards(
        self,
        kind: str,
        roots: list[Path],
        *,
        lang: str | None = None,
    ) -> dict[str, Path] | None:
        """Merged basename→path if every non-empty install index is present."""
        groups = self.group_roots(roots)
        merged: dict[str, Path] = {}
        for install in INSTALL_ORDER:
            ir = groups[install]
            if not ir:
                continue
            shard = self.get_install_names(kind, install, ir, lang=lang)
            if shard is None:
                return None
            merged.update(shard)
        return merged

    def get_dds_map(self, roots: list[Path]) -> dict[str, Path] | None:
        if (
            self._dds_map is not None
            and roots_match(self._dds_roots, roots)
        ):
            return {k: Path(v) for k, v in self._dds_map.items()}
        return self.merge_dds_shards(roots)

    def put_dds_map(self, roots: list[Path], files: dict[str, Path]) -> None:
        """Split a combined map into install shards."""
        groups = self.group_roots(roots)
        buckets: dict[str, dict[str, Path]] = {k: {} for k in INSTALL_ORDER}
        for logical, path in files.items():
            owner_install = INSTALL_CUSTOM
            for root in reversed(roots):
                try:
                    path.resolve().relative_to(root.resolve())
                    owner_install = self.classify(root)
                    break
                except (ValueError, OSError):
                    try:
                        path.relative_to(root)
                        owner_install = self.classify(root)
                        break
                    except ValueError:
                        continue
            buckets[owner_install][_logical_dds_key(str(logical))] = path
        for install in INSTALL_ORDER:
            ir = groups[install]
            if not ir:
                continue
            self.put_install_dds(install, ir, buckets[install])
        self._dds_roots = normalize_roots(roots)
        self._dds_map = {
            _logical_dds_key(str(k)): str(v) for k, v in files.items() if k and v
        }

    def get_name_map(
        self,
        kind: str,
        roots: list[Path],
        *,
        lang: str | None = None,
    ) -> dict[str, Path] | None:
        return self.merge_name_shards(kind, roots, lang=lang)

    def put_name_map(
        self,
        kind: str,
        roots: list[Path],
        files: dict[str, Path],
        *,
        lang: str | None = None,
    ) -> None:
        groups = self.group_roots(roots)
        buckets: dict[str, dict[str, Path]] = {k: {} for k in INSTALL_ORDER}
        for name, path in files.items():
            owner_install = INSTALL_CUSTOM
            for root in reversed(roots):
                try:
                    path.resolve().relative_to(root.resolve())
                    owner_install = self.classify(root)
                    break
                except (ValueError, OSError):
                    try:
                        path.relative_to(root)
                        owner_install = self.classify(root)
                        break
                    except ValueError:
                        continue
            buckets[owner_install][_basename_key(name)] = path
        for install in INSTALL_ORDER:
            ir = groups[install]
            if not ir:
                continue
            self.put_install_names(
                kind, install, ir, buckets[install], lang=lang
            )

    def name_map_paths(
        self,
        kind: str,
        roots: list[Path],
        *,
        lang: str | None = None,
    ) -> list[Path] | None:
        mapping = self.get_name_map(kind, roots, lang=lang)
        if mapping is None:
            return None
        return [mapping[k] for k in sorted(mapping)]

    def get_dds(self, logical: str) -> Path | None:
        key = _logical_dds_key(logical)
        if not key:
            return None
        if self._dds_map is None:
            return None
        raw = self._dds_map.get(key)
        if not raw:
            return None
        path = Path(raw)
        if path.is_file():
            return path
        self._dds_map.pop(key, None)
        return None

    def drop_dds(self, logical: str) -> None:
        key = _logical_dds_key(logical)
        if not key:
            return
        if self._dds_map is not None:
            self._dds_map.pop(key, None)
        folder = cache_dir() / KIND_DDS
        if not folder.is_dir():
            return
        for path in folder.glob("*.json"):
            data = _read_json(path)
            if data is None:
                continue
            files = data.get("files")
            if not isinstance(files, dict) or key not in files:
                continue
            files.pop(key, None)
            data["files"] = {k: files[k] for k in sorted(files)}
            _write_json(path, data)

    def put_dds(self, logical: str, path: Path) -> None:
        key = _logical_dds_key(logical)
        if not key:
            return
        try:
            resolved = str(path.resolve())
        except OSError:
            resolved = str(path)
        if self._dds_map is None:
            self._dds_map = {}
        self._dds_map[key] = resolved
        owner: Path | None = None
        for root_s in reversed(self._dds_roots):
            root = Path(root_s)
            try:
                path.resolve().relative_to(root.resolve())
                owner = root
                break
            except (ValueError, OSError):
                try:
                    path.relative_to(root)
                    owner = root
                    break
                except ValueError:
                    continue
        if owner is None:
            return
        install = self.classify(owner)
        roots = [
            Path(r)
            for r in self._dds_roots
            if self.classify(Path(r)) == install
        ]
        shard = self.get_install_dds(install, roots) or {}
        shard[key] = path
        self.put_install_dds(install, roots, shard, bust_merge=False)


def sheet_proxy_divisor(_max_edge: int = 0) -> int:
    """Fixed 1/4 downscale for every sheet proxy (e.g. 128→32, 2048→512)."""
    return 4


def sheet_proxy_scale(max_edge: int = 0) -> float:
    """Downscale factor ``1/divisor``."""
    return 1.0 / sheet_proxy_divisor(max_edge)


def sheet_proxy_cache_path(dds_path: Path) -> Path | None:
    """Return cache path for a low-res sheet proxy PNG, or None if unreadable."""
    try:
        resolved = dds_path.resolve()
        mtime_ns = resolved.stat().st_mtime_ns
    except OSError:
        return None
    key = hashlib.sha1(
        f"{resolved}|{mtime_ns}|sheetproxy{SHEET_PROXY_RULE_VER}".encode("utf-8")
    ).hexdigest()[:20]
    stem = resolved.stem.replace(" ", "_")[:32]
    return cache_dir() / "thumbs" / f"{stem}_{key}.png"


def load_sheet_proxy(dds_path: Path):
    """Load ``(proxy_rgba, orig_w, orig_h)`` or None.

    Original DDS size is stored in PNG ``tEXt`` so UV crops can be scaled.
    """
    from PIL import Image

    dest = sheet_proxy_cache_path(dds_path)
    if dest is None or not dest.is_file():
        return None
    try:
        img = Image.open(dest)
        img.load()
        rgba = img.convert("RGBA")
        meta = getattr(img, "text", None) or {}
        try:
            ow = int(meta.get("sage_ow") or rgba.width)
            oh = int(meta.get("sage_oh") or rgba.height)
        except ValueError:
            ow, oh = rgba.width, rgba.height
        if ow <= 0 or oh <= 0:
            ow, oh = rgba.width, rgba.height
        return rgba, ow, oh
    except OSError:
        return None


def save_sheet_proxy(
    dds_path: Path,
    image,
    *,
    orig_w: int,
    orig_h: int,
) -> None:
    """Persist a low-res sheet proxy (fast zlib; embeds original WxH)."""
    from PIL import PngImagePlugin

    dest = sheet_proxy_cache_path(dds_path)
    if dest is None:
        return
    try:
        _ensure_dir(dest.parent)
        meta = PngImagePlugin.PngInfo()
        meta.add_text("sage_ow", str(int(orig_w)))
        meta.add_text("sage_oh", str(int(orig_h)))
        image.save(
            dest,
            format="PNG",
            compress_level=1,
            optimize=False,
            pnginfo=meta,
        )
    except OSError:
        pass


def dds_rgba_cache_path(dds_path: Path) -> Path | None:
    """Return cache file path for a decoded DDS, or None if source unreadable."""
    try:
        resolved = dds_path.resolve()
        mtime_ns = resolved.stat().st_mtime_ns
        key = hashlib.sha1(f"{resolved}|{mtime_ns}".encode("utf-8")).hexdigest()[:20]
    except OSError:
        return None
    stem = resolved.stem.replace(" ", "_")[:40]
    return cache_dir() / "dds_rgba" / f"{stem}_{key}.png"


def load_dds_rgba_cache(dds_path: Path):
    """Load cached RGBA PIL image for ``dds_path``, or None."""
    from PIL import Image

    dest = dds_rgba_cache_path(dds_path)
    if dest is None or not dest.is_file():
        return None
    try:
        img = Image.open(dest)
        img.load()
        return img.convert("RGBA")
    except OSError:
        return None


def save_dds_rgba_cache(dds_path: Path, image) -> None:
    """Persist decoded RGBA so later opens skip slow DDS decompress."""
    dest = dds_rgba_cache_path(dds_path)
    if dest is None:
        return
    try:
        _ensure_dir(dest.parent)
        image.save(dest, format="PNG", optimize=False)
    except OSError:
        pass


def _font_stem_safe(atlas_stem: str) -> str:
    return atlas_stem.replace("\\", "_").replace("/", "_").replace(" ", "_")


def font_cache_path(atlas_stem: str) -> Path:
    """Flat glyph-meta file: ``cache/fonts/<stem>.json``."""
    return cache_dir() / "fonts" / f"{_font_stem_safe(atlas_stem)}.json"


def _legacy_font_cache_dir(atlas_stem: str) -> Path:
    return cache_dir() / "fonts" / _font_stem_safe(atlas_stem)


def _font_meta_valid(meta: dict[str, Any] | None) -> dict[str, Any] | None:
    if meta is None:
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


def load_font_cache(atlas_stem: str) -> dict[str, Any] | None:
    """Return glyph meta if valid vs source DDS/INI mtimes (atlas always from DDS)."""
    ensure_cache_version()
    meta = _font_meta_valid(_read_json(font_cache_path(atlas_stem)))
    if meta is not None:
        return meta
    # Migrate older per-atlas directories (…/fonts/<stem>/meta.json).
    legacy = _read_json(_legacy_font_cache_dir(atlas_stem) / "meta.json")
    return _font_meta_valid(legacy)


def save_font_cache(atlas_stem: str, *, meta: dict[str, Any]) -> None:
    """Persist parsed glyph coords only — never a re-encoded atlas image."""
    ensure_cache_version()
    dest = font_cache_path(atlas_stem)
    meta = dict(meta)
    meta.pop("cache_format", None)
    _write_json(dest, meta)
    # Drop legacy per-atlas directories from older SAGE builds.
    legacy_dir = _legacy_font_cache_dir(atlas_stem)
    if legacy_dir.is_dir():
        shutil.rmtree(legacy_dir, ignore_errors=True)
