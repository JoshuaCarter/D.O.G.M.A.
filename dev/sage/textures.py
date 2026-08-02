"""Resolve Stalker UI texture IDs / paths to DDS crops.

Directory lists come from Setup / Rescan (saved in settings). Path indexes build
on first bind (per-root shards); picker thumbnails are generated then too.
Document open warms only the atlas/DDS/string/font ids that file references.
"""

from __future__ import annotations

import codecs
import os
import struct
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import Image

from .cache_store import (
    INSTALL_ORDER,
    KIND_DESCR,
    PathIndex,
    load_dds_rgba_cache,
    load_sheet_proxy,
    save_dds_rgba_cache,
    save_sheet_proxy,
    sheet_proxy_cache_path,
    sheet_proxy_divisor,
)
from .diaglog import get_logger
from .model import LayoutNode, TextureRef

_log = get_logger("textures")

# Texture picker icon size (fit from low-res sheet proxy).
PICKER_THUMB_SIZE = 192
# In-memory sheet proxies so many atlas UVs sharing one DDS don't re-read PNG/DDS.
_PROXY_MEM_MAX = 96
_proxy_mem: OrderedDict[str, tuple[Image.Image, int, int]] = OrderedDict()
_proxy_mem_lock = threading.Lock()


def _decode_dds_raw(path: Path) -> Image.Image | None:
    """Decode DDS bytes without consulting the RGBA disk cache."""
    path_str = str(path)
    try:
        img = Image.open(path_str)
        img.load()
        return img.convert("RGBA")
    except (OSError, NotImplementedError, ValueError):
        pass
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) < 128 or raw[:4] != b"DDS ":
        return None
    # Standard DDS_HEADER: height @ +12, width @ +16 (after magic).
    try:
        height, width = struct.unpack_from("<2I", raw, 12)
    except struct.error:
        return None
    if width <= 0 or height <= 0 or width > 8192 or height > 8192:
        return None
    payload = raw[128:]
    need = width * height
    if len(payload) < need:
        return None
    # A8 / L8 font sheets: 1 byte/pixel → white RGB + alpha.
    alpha = payload[:need]
    rgba = bytearray(need * 4)
    for i, a in enumerate(alpha):
        o = i * 4
        rgba[o] = 255
        rgba[o + 1] = 255
        rgba[o + 2] = 255
        rgba[o + 3] = a
    return Image.frombytes("RGBA", (width, height), bytes(rgba))


def open_dds_image(
    path: Path | str, *, persist: bool = True
) -> Image.Image | None:
    """Open a DDS as RGBA. When ``persist``, write the decoded sheet to disk cache."""
    p = Path(path)
    cached = load_dds_rgba_cache(p)
    if cached is not None:
        _log.debug(
            "dds cache-hit %s (%sx%s)", abs_log_path(p), cached.width, cached.height
        )
        return cached
    img = _decode_dds_raw(p)
    if img is None:
        _log.warning("dds decode failed %s", abs_log_path(p))
        return None
    _log.info(
        "dds decoded %s (%sx%s) persist=%s",
        abs_log_path(p),
        img.width,
        img.height,
        persist,
    )
    if persist:
        save_dds_rgba_cache(p, img)
    return img


def _crop_uv(
    img: Image.Image, uv: tuple[float, float, float, float]
) -> Image.Image:
    x, y, w, h = uv
    if w <= 0 or h <= 0:
        return img
    left = max(0, int(x))
    top = max(0, int(y))
    right = min(img.width, int(x + w))
    bottom = min(img.height, int(y + h))
    if right <= left or bottom <= top:
        return img
    return img.crop((left, top, right, bottom))


def letterbox_thumb(
    img: Image.Image, size: int = PICKER_THUMB_SIZE
) -> Image.Image:
    """Fit ``img`` inside a square picker swatch (scale up/down, no crop)."""
    img = img.convert("RGBA")
    w, h = img.size
    if w <= 0 or h <= 0:
        return Image.new("RGBA", (size, size), (42, 42, 48, 255))
    # Contain: largest size that still fits; letterbox leftover with panel grey.
    scale = min(size / float(w), size / float(h))
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    if (nw, nh) != (w, h):
        resample = (
            Image.Resampling.BOX if scale <= 1.0 else Image.Resampling.BILINEAR
        )
        img = img.resize((nw, nh), resample)
    bg = Image.new("RGBA", (size, size), (42, 42, 48, 255))
    bg.paste(img, ((size - nw) // 2, (size - nh) // 2), img)
    return bg


def make_sheet_proxy(sheet: Image.Image) -> tuple[Image.Image, int, int]:
    """Quarter-res sheet proxy; returns (proxy, orig_w, orig_h)."""
    sheet = sheet.convert("RGBA")
    ow, oh = sheet.size
    div = sheet_proxy_divisor()
    nw = max(1, ow // div)
    nh = max(1, oh // div)
    proxy = sheet.resize((nw, nh), Image.Resampling.BOX)
    return proxy, ow, oh


def _proxy_mem_key(path: Path) -> str:
    try:
        resolved = path.resolve()
        return f"{resolved}|{resolved.stat().st_mtime_ns}"
    except OSError:
        return str(path)


def ensure_sheet_proxy(dds_path: Path | str) -> tuple[Image.Image, int, int] | None:
    """Load or build the low-res sheet proxy for ``dds_path`` (process LRU)."""
    path = Path(dds_path)
    key = _proxy_mem_key(path)
    with _proxy_mem_lock:
        hit = _proxy_mem.get(key)
        if hit is not None:
            _proxy_mem.move_to_end(key)
            return hit
    disk = load_sheet_proxy(path)
    if disk is not None:
        proxy, ow, oh = disk
    else:
        sheet = open_dds_image(path, persist=False)
        if sheet is None:
            return None
        proxy, ow, oh = make_sheet_proxy(sheet)
        save_sheet_proxy(path, proxy, orig_w=ow, orig_h=oh)
    with _proxy_mem_lock:
        _proxy_mem[key] = (proxy, ow, oh)
        _proxy_mem.move_to_end(key)
        while len(_proxy_mem) > _PROXY_MEM_MAX:
            _proxy_mem.popitem(last=False)
    return proxy, ow, oh


def build_picker_thumb_image(
    dds_path: Path | str,
    *,
    uv: tuple[float, float, float, float] | None = None,
    size: int = PICKER_THUMB_SIZE,
) -> Image.Image | None:
    """Picker thumb from the sheet proxy (scaled UV crop, fill preview box)."""
    loaded = ensure_sheet_proxy(dds_path)
    if loaded is None:
        return None
    proxy, ow, oh = loaded
    if uv is None:
        src = proxy
    else:
        sx = proxy.width / float(ow)
        sy = proxy.height / float(oh)
        x, y, w, h = uv
        src = _crop_uv(proxy, (x * sx, y * sy, w * sx, h * sy))
    return letterbox_thumb(src, size)


# Init3tButton / checkbox / radio: XML names the stem; engine appends state.
_STATE_SUFFIXES = ("_e", "_h", "_t", "_d", "_s", "_u")


def _register_stalker_codecs() -> None:
    """Map engine encodings (e.g. st_windows-1251) onto cp1251."""

    def _search(name: str):  # noqa: ANN202
        key = name.lower().replace("-", "_")
        if key in ("st_windows_1251", "windows_1251"):
            return codecs.lookup("cp1251")
        return None

    codecs.register(_search)


_register_stalker_codecs()


def _parse_xml_root(path: Path) -> ET.Element | None:
    """Parse descr XML; tolerate Stalker encodings / broken declarations."""
    try:
        return ET.parse(path).getroot()
    except ET.ParseError:
        return None
    except LookupError:
        pass
    except OSError:
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    try:
        text = raw.decode("cp1251")
    except UnicodeDecodeError:
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return None
    if text.lstrip().startswith("<?xml"):
        end = text.find("?>")
        if end > 0:
            text = '<?xml version="1.0"?>' + text[end + 2 :]
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        return None


@dataclass
class AtlasEntry:
    file_name: str  # e.g. ui\dots
    x: float
    y: float
    width: float
    height: float
    resolved_id: str = ""  # actual atlas id used (may be stem+_e)
    source: Path | None = None  # textures_descr XML that defined this id


@dataclass(frozen=True)
class AtlasCatalogEntry:
    """One textures_descr atlas id (or button stem) for picking."""

    atlas_id: str
    file_name: str
    x: float
    y: float
    width: float
    height: float
    source: Path
    # When XML uses a stem (ui_inGame2_button) but descr only has _e/_h/…
    is_stem: bool = False
    state_id: str = ""  # e.g. ui_inGame2_button_e used for preview


@dataclass(frozen=True)
class TexturePick:
    """What to write into UI XML <texture> — Anomaly atlas id or DDS path."""

    name: str
    kind: str  # "atlas" | "path"
    dds_path: Path | None = None


@dataclass
class ResolvedTexture:
    path: Path | None
    image: Image.Image | None
    error: str = ""
    atlas_id: str = ""


def abs_log_path(path: Path | str | None) -> str:
    """Full filesystem path for load/diagnostic logs (resolved when possible)."""
    if path is None:
        return "?"
    p = Path(path)
    try:
        return str(p.resolve())
    except OSError:
        return str(p)


def gamma_relative_path(path: Path) -> str:
    """Short path under GAMMA (or gamedata/…) for UI chrome — not for load logs."""
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    text = str(resolved).replace("\\", "/")
    lower = text.lower()
    marker = "/gamma/"
    idx = lower.find(marker)
    if idx >= 0:
        return text[idx + len(marker) :]
    gidx = lower.find("/gamedata/")
    if gidx >= 0:
        return text[gidx + 1 :]
    return path.name


def _path_has_part(path: Path, name: str) -> bool:
    needle = name.lower()
    return any(part.lower() == needle for part in path.parts)


def iter_texture_dir_roots(
    *,
    gamedata_texture_roots: list[Path],
    texture_scan_roots: list[Path],
) -> list[Path]:
    """Concrete ``textures`` dirs used for DDS lookup / picking.

    Custom/project scan roots are not scanned as a flat dump — only directories
    named ``textures`` underneath them (e.g. ``…/gamedata/textures``,
    ``…/tweaks/foo/textures``). That keeps ``assets/`` reference dumps out of
    the index.
    """
    roots: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        if not path.is_dir():
            return
        if _path_has_part(path, "assets"):
            return
        try:
            key = str(path.resolve()).lower()
        except OSError:
            key = str(path).lower()
        if key in seen:
            return
        seen.add(key)
        roots.append(path)

    for root in gamedata_texture_roots:
        _add(root)
    for scan in texture_scan_roots:
        if not scan.is_dir():
            continue
        # If the custom root *is* a textures folder, use it; never treat a
        # project root (e.g. DOGMA/src) as one giant DDS root.
        if scan.name.lower() == "textures":
            _add(scan)
            continue
        try:
            for tex_dir in scan.rglob("textures"):
                if tex_dir.is_dir() and tex_dir.name.lower() == "textures":
                    _add(tex_dir)
        except OSError:
            continue
    return roots


def _scan_dds_under_roots(roots: list[Path], mapping: dict[str, Path]) -> None:
    """Rglob ``*.dds`` into ``mapping`` (later roots override)."""
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for path in root.rglob("*.dds"):
                if not path.is_file():
                    continue
                if _path_has_part(path, "assets"):
                    continue
                try:
                    rel = path.relative_to(root)
                except ValueError:
                    continue
                logical = str(rel.with_suffix("")).replace("/", "\\").lower()
                if not logical:
                    continue
                mapping[logical] = path
        except OSError:
            continue


def logical_dds_name(dds_path: Path, texture_roots: list[Path]) -> str | None:
    """Map a DDS under a texture root → game logical name (``ui\\foo``, no .dds)."""
    best: tuple[int, str] | None = None
    for root in texture_roots:
        logical = _logical_under_root(dds_path, root)
        if not logical:
            continue
        try:
            depth = len(root.resolve().parts)
        except OSError:
            depth = len(root.parts)
        if best is None or depth > best[0]:
            best = (depth, logical)
    return best[1] if best else None


def _norm_dir_prefix(root: Path) -> str:
    """Normalized directory prefix for fast ``startswith`` root checks."""
    try:
        s = os.path.normcase(str(root.resolve()))
    except OSError:
        s = os.path.normcase(str(root))
    if s and s[-1] not in "\\/":
        s += os.sep
    return s


def root_dir_prefixes(roots: list[Path]) -> list[str]:
    """Precompute normalized root prefixes (call once per catalog filter)."""
    return [_norm_dir_prefix(r) for r in roots]


def path_is_under_prefixes(path: Path, prefixes: list[str]) -> bool:
    try:
        s = os.path.normcase(str(path.resolve()))
    except OSError:
        return False
    return any(s.startswith(p) for p in prefixes)


def path_is_under_roots(path: Path, roots: list[Path]) -> bool:
    return path_is_under_prefixes(path, root_dir_prefixes(roots))


@dataclass(frozen=True)
class DdsCatalogEntry:
    """One pickable DDS: game logical name + winning file on disk."""

    logical: str
    path: Path


def _logical_under_root(dds: Path, root: Path) -> str | None:
    """Logical name relative to a search root (strip a nested ``textures\\`` if needed)."""
    try:
        resolved = dds.resolve()
        root_r = root.resolve()
        rel = resolved.relative_to(root_r)
    except (OSError, ValueError):
        return None
    if resolved.suffix.lower() != ".dds":
        return None
    parts = list(rel.parts)
    if root_r.name.lower() != "textures":
        lower = [p.lower() for p in parts]
        if "textures" in lower:
            parts = parts[lower.index("textures") + 1 :]
    if not parts:
        return None
    return str(Path(*parts).with_suffix("")).replace("/", "\\")


def scan_dds_catalog(roots: list[Path]) -> list[DdsCatalogEntry]:
    """Enumerate ``*.dds`` under roots. Later roots win on the same logical name."""
    by_key: dict[str, DdsCatalogEntry] = {}
    for root in roots:
        if not root.is_dir():
            continue
        try:
            batch = root.rglob("*.dds")
        except OSError:
            continue
        for dds in batch:
            try:
                if not dds.is_file():
                    continue
            except OSError:
                continue
            logical = _logical_under_root(dds, root)
            if not logical:
                continue
            by_key[logical.lower()] = DdsCatalogEntry(logical=logical, path=dds)
    return sorted(by_key.values(), key=lambda e: e.logical.lower())


@dataclass(frozen=True)
class PickerRootGroup:
    """One texture-picker scope: label + texture dirs + descr dirs."""

    label: str
    texture_roots: tuple[Path, ...]
    descr_roots: tuple[Path, ...]


def build_picker_root_groups(
    resolver: TextureResolver,
    *,
    anomaly_root: str | Path = "",
    gamma_root: str | Path = "",
) -> list[PickerRootGroup]:
    """Custom first (default), then Anomaly / GAMMA / All."""
    custom_tex = tuple(p for p in resolver.texture_scan_roots if p.is_dir())
    custom_descr = tuple(p for p in resolver.descr_scan_roots if p.is_dir())
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

    all_tex = tuple(resolver.dds_search_roots())
    all_descr = tuple(
        p
        for p in list(resolver.gamedata_descr_roots) + list(resolver.descr_scan_roots)
        if p.is_dir()
    )
    anom_tex = _under(list(resolver.gamedata_texture_roots), anom)
    anom_descr = _under(list(resolver.gamedata_descr_roots), anom)
    gam_tex = _under(list(resolver.gamedata_texture_roots), gam)
    gam_descr = _under(list(resolver.gamedata_descr_roots), gam)

    groups: list[PickerRootGroup] = []
    if custom_tex or custom_descr:
        groups.append(
            PickerRootGroup("Custom", custom_tex, custom_descr)
        )
    if anom_tex or anom_descr:
        groups.append(PickerRootGroup("Anomaly", anom_tex, anom_descr))
    if gam_tex or gam_descr:
        groups.append(PickerRootGroup("GAMMA", gam_tex, gam_descr))
    groups.append(PickerRootGroup("All", all_tex, all_descr))
    return groups


def collect_doc_texture_ids(doc: LayoutNode) -> tuple[set[str], set[str]]:
    """Atlas ids and direct texture paths referenced by a layout tree."""
    atlas: set[str] = set()
    paths: set[str] = set()
    for node in doc.iter_all():
        ref = node.texture
        if ref is None or not ref.name:
            continue
        if ref.is_path:
            paths.add(ref.name)
        else:
            atlas.add(ref.name)
    return atlas, paths


class TextureResolver:
    def __init__(
        self,
        *,
        texture_scan_roots: list[Path],
        gamedata_texture_roots: list[Path],
        descr_scan_roots: list[Path],
        gamedata_descr_roots: list[Path],
        path_index: PathIndex | None = None,
    ) -> None:
        self.texture_scan_roots = texture_scan_roots
        self.gamedata_texture_roots = gamedata_texture_roots
        self.descr_scan_roots = descr_scan_roots
        self.gamedata_descr_roots = gamedata_descr_roots
        self.path_index = path_index
        # Lazy caches — filled by warm_* / ensure_indexes / first lookup.
        self._atlas: dict[str, AtlasEntry] = {}
        self._atlas_missing: set[str] = set()
        self._dds_index: dict[str, Path] = {}
        self._dds_missing: set[str] = set()
        self._descr_files: list[Path] | None = None
        self._dds_search_roots: list[Path] | None = None
        self._dds_map_ready = False
        self._thumb_abort = False
        self._thumb_pool: ThreadPoolExecutor | None = None
        self._atlas_catalog_cache: dict[tuple[str, ...], list[AtlasCatalogEntry]] = {}
        self._dds_catalog_cache: dict[tuple[str, ...], list[DdsCatalogEntry]] = {}

    def abort_picker_thumbs(self) -> None:
        """Cancel in-flight thumb warm (close / quit). Releases file locks ASAP."""
        self._thumb_abort = True
        pool = self._thumb_pool
        if pool is not None:
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass

    def clear_cache(self) -> None:
        """Full memory clear (rescan / rebind). Drops decoded DDS images too."""
        self._atlas.clear()
        self._atlas_missing.clear()
        self._dds_index.clear()
        self._dds_missing.clear()
        self._descr_files = None
        self._dds_search_roots = None
        self._dds_map_ready = False
        self._atlas_catalog_cache.clear()
        self._dds_catalog_cache.clear()
        self._open_dds.cache_clear()
        self._resolve_cached.cache_clear()
        with _proxy_mem_lock:
            _proxy_mem.clear()

    def clear_document_cache(self) -> None:
        """Drop per-document atlas/UV resolve state; keep DDS map + decoded images."""
        self._atlas.clear()
        self._atlas_missing.clear()
        self._dds_missing.clear()
        self._resolve_cached.cache_clear()

    def invalidate_for_document(self, doc: LayoutNode) -> None:
        """Drop decoded sheets / atlas UV for ids this document uses (re-read on warm)."""
        atlas_ids, path_names = collect_doc_texture_ids(doc)
        drop_ids = set(atlas_ids)
        for tid in atlas_ids:
            if any(tid.endswith(s) for s in _STATE_SUFFIXES):
                continue
            for suf in _STATE_SUFFIXES:
                drop_ids.add(tid + suf)
        for tid in drop_ids:
            self._atlas.pop(tid, None)
            self._atlas_missing.discard(tid)
        # Decoded PIL sheets are path-keyed without mtime — must clear to pick up edits.
        self._open_dds.cache_clear()
        self._resolve_cached.cache_clear()
        with _proxy_mem_lock:
            _proxy_mem.clear()
        # Forget missing flags for path textures so find_dds retries.
        for name in path_names:
            key = name.strip().replace("/", "\\").lower()
            if key.endswith(".dds"):
                key = key[:-4]
            self._dds_missing.discard(key)
        _log.info(
            "invalidate textures: dropped %d atlas id(s), %d path name(s); "
            "cleared decoded DDS + resolve caches",
            len(drop_ids),
            len(path_names),
        )

    def rebuild_indexes(self) -> None:
        """Clear memory + disk path index (fonts revalidate via mtime)."""
        self.clear_cache()
        if self.path_index is not None:
            self.path_index.invalidate()

    def ensure_indexes(self, *, force: bool = False) -> dict[str, int]:
        """Load or build descr file list + full DDS map (fast loads after first scan)."""
        if force:
            self._descr_files = None
            self._dds_map_ready = False
            self._dds_index.clear()
            self._dds_missing.clear()
            self._dds_search_roots = None
        n_descr = len(self._iter_descr_files(force=force))
        n_dds = self._ensure_dds_map(force=force)
        if self.path_index is not None:
            self.path_index.save()
        return {"descr_files": n_descr, "dds": n_dds}

    def _ensure_dds_map(self, *, force: bool = False) -> int:
        if self._dds_map_ready and not force:
            return len(self._dds_index)
        roots = self._iter_dds_search_roots()
        if not force and self.path_index is not None:
            cached = self.path_index.get_dds_map(roots)
            if cached is not None:
                self._dds_index = dict(cached)
                self._dds_map_ready = True
                self._dds_catalog_cache.clear()
                return len(self._dds_index)
        mapping: dict[str, Path] = {}
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
                shard = self.path_index.get_install_dds(install, ir)
            if shard is None:
                shard = {}
                _scan_dds_under_roots(ir, shard)
                if self.path_index is not None:
                    self.path_index.put_install_dds(install, ir, shard)
            mapping.update(shard)
        self._dds_index = mapping
        self._dds_map_ready = True
        self._dds_catalog_cache.clear()
        if self.path_index is not None:
            self.path_index.get_dds_map(roots)
        return len(mapping)

    @property
    def atlas_count(self) -> int:
        return len(self._atlas)

    @property
    def dds_count(self) -> int:
        return len(self._dds_index)

    def _descr_roots(self) -> list[Path]:
        return list(self.gamedata_descr_roots) + list(self.descr_scan_roots)

    def _scan_descr_root(self, root: Path, *, deep: bool) -> dict[str, Path]:
        by_name: dict[str, Path] = {}
        if not root.is_dir():
            return by_name
        try:
            batch = (
                sorted(root.rglob("**/textures_descr/*.xml"))
                if deep
                else sorted(root.glob("*.xml"))
            )
        except OSError:
            return by_name
        for path in batch:
            if _path_has_part(path, "assets"):
                continue
            by_name[path.name.lower()] = path
        return by_name

    def _iter_descr_files(self, *, force: bool = False) -> list[Path]:
        if self._descr_files is not None and not force:
            return self._descr_files
        roots = self._descr_roots()
        scan_keys: set[str] = set()
        for p in self.descr_scan_roots:
            try:
                scan_keys.add(str(p.resolve()).lower())
            except OSError:
                scan_keys.add(str(p).lower())

        if not force and self.path_index is not None:
            cached = self.path_index.merge_name_shards(KIND_DESCR, roots)
            if cached is not None:
                self._descr_files = [cached[k] for k in sorted(cached)]
                return self._descr_files

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
                shard = self.path_index.get_install_names(KIND_DESCR, install, ir)
            if shard is None:
                shard = {}
                for root in ir:
                    try:
                        key = str(root.resolve()).lower()
                    except OSError:
                        key = str(root).lower()
                    deep = key in scan_keys
                    shard.update(self._scan_descr_root(root, deep=deep))
                if self.path_index is not None:
                    self.path_index.put_install_names(
                        KIND_DESCR, install, ir, shard
                    )
            by_name.update(shard)
        self._descr_files = [by_name[k] for k in sorted(by_name)]
        if self.path_index is not None:
            self.path_index.save()
        return self._descr_files

    def _ingest_descr_for_ids(self, wanted: set[str]) -> None:
        """Scan descr XMLs for ``wanted`` atlas ids (later roots override)."""
        pending = {
            tid
            for tid in wanted
            if tid not in self._atlas and tid not in self._atlas_missing
        }
        if not pending:
            return
        # Also accept state-suffixed matches for bare stems.
        search = set(pending)
        for tid in list(pending):
            if any(tid.endswith(s) for s in _STATE_SUFFIXES):
                continue
            for suf in _STATE_SUFFIXES:
                search.add(tid + suf)

        for path in self._iter_descr_files():
            try:
                blob = path.read_bytes()
            except OSError:
                continue
            if not any(
                f'id="{tid}"'.encode("ascii", "ignore") in blob
                or f"id='{tid}'".encode("ascii", "ignore") in blob
                for tid in search
            ):
                continue
            root = _parse_xml_root(path)
            if root is None:
                # Prefer parse from bytes we already have when ET.parse fails encodings.
                try:
                    text = blob.decode("cp1251")
                except UnicodeDecodeError:
                    text = blob.decode("utf-8", errors="replace")
                if text.lstrip().startswith("<?xml"):
                    end = text.find("?>")
                    if end > 0:
                        text = '<?xml version="1.0"?>' + text[end + 2 :]
                try:
                    root = ET.fromstring(text)
                except ET.ParseError:
                    continue
            for file_el in root.iter("file"):
                file_name = (file_el.get("name") or "").strip()
                if not file_name:
                    continue
                for tex in file_el.findall("texture"):
                    tid = (tex.get("id") or "").strip()
                    if not tid or tid not in search:
                        continue
                    try:
                        x = float(tex.get("x", "0"))
                        y = float(tex.get("y", "0"))
                        w = float(tex.get("width", "0"))
                        h = float(tex.get("height", "0"))
                    except ValueError:
                        continue
                    # Last matching descr wins (later roots override).
                    self._atlas[tid] = AtlasEntry(
                        file_name,
                        x,
                        y,
                        w,
                        h,
                        resolved_id=tid,
                        source=path,
                    )
        for tid in pending:
            if tid not in self._atlas:
                self._atlas_missing.add(tid)
                _log.debug("atlas missing after descr scan: %s", tid)

    def warm_for_document(self, doc: LayoutNode) -> None:
        """Resolve only atlas ids / DDS paths referenced by ``doc``."""
        self._ensure_dds_map()
        atlas_ids, path_names = collect_doc_texture_ids(doc)
        _log.info(
            "warm textures: %d atlas id(s), %d path texture(s)",
            len(atlas_ids),
            len(path_names),
        )
        self._ingest_descr_for_ids(set(atlas_ids))
        # Ensure state-suffix variants used by lookup_atlas are covered.
        expanded = set(atlas_ids)
        for tid in atlas_ids:
            if any(tid.endswith(s) for s in _STATE_SUFFIXES):
                continue
            for suf in _STATE_SUFFIXES:
                expanded.add(tid + suf)
        self._ingest_descr_for_ids(expanded)
        needed_files: set[str] = set(path_names)
        for tid in sorted(atlas_ids):
            entry = self.lookup_atlas(tid)
            if entry is None:
                _log.warning("texture atlas unresolved: %s", tid)
                continue
            needed_files.add(entry.file_name)
            dds = self.find_dds(entry.file_name)
            descr = abs_log_path(entry.source) if entry.source else "(no descr path)"
            dds_s = abs_log_path(dds) if dds else "MISSING"
            resolved = entry.resolved_id or tid
            _log.info(
                "texture atlas %s -> %s UV %.0f,%.0f %.0fx%.0f | descr %s | dds %s",
                tid if tid == resolved else f"{tid}->{resolved}",
                entry.file_name,
                entry.x,
                entry.y,
                entry.width,
                entry.height,
                descr,
                dds_s,
            )
        for name in sorted(path_names):
            dds = self.find_dds(name)
            if dds is None:
                _log.warning("texture path unresolved: %s", name)
            else:
                _log.info("texture path %s -> dds %s", name, abs_log_path(dds))
        dds_paths: list[Path] = []
        seen_dds: set[str] = set()
        for logical in needed_files:
            dds = self.find_dds(logical)
            if dds is None:
                continue
            try:
                key = str(dds.resolve())
            except OSError:
                key = str(dds)
            if key in seen_dds:
                continue
            seen_dds.add(key)
            dds_paths.append(dds)
        # Prefetch unique sheets (disk RGBA cache + in-memory LRU).
        _log.info("prefetch %d unique DDS sheet(s)", len(dds_paths))
        self._prefetch_dds(dds_paths)
        if self.path_index is not None:
            self.path_index.save()

    def _prefetch_dds(self, paths: list[Path]) -> None:
        if not paths:
            return

        def _load(path: Path) -> str:
            open_dds_image(path)
            return str(path)

        workers = min(4, len(paths))
        if workers == 1:
            for path in paths:
                self._open_dds(str(path))
            return
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(_load, paths))
        for path in paths:
            self._open_dds(str(path))

    def warm_picker_thumbs(
        self,
        *,
        progress: Callable[[int, int, int], None] | None = None,
    ) -> dict[str, int]:
        """Build one low-res sheet proxy per textures_descr atlas sheet.

        Not every DDS under the texture roots (~10k+) — only sheets named by
        atlas XML (a few hundred). Path-mode / other DDS build on demand via
        ``ensure_sheet_proxy``. ``progress(done, total, written)`` is optional.
        """
        self.ensure_indexes()
        # Unique DDS paths referenced by atlas file names only.
        by_key: dict[str, Path] = {}
        for entry in self.scan_atlas_catalog():
            dds = self.find_dds(entry.file_name)
            if dds is None:
                continue
            try:
                key = str(dds.resolve())
            except OSError:
                key = str(dds)
            by_key.setdefault(key, dds)

        todo: list[Path] = []
        skipped = 0
        for path in by_key.values():
            dest = sheet_proxy_cache_path(path)
            if dest is not None and dest.is_file():
                skipped += 1
                continue
            todo.append(path)

        total = len(todo)
        written = 0
        failed = 0
        if total == 0:
            return {
                "sheets": 0,
                "written": 0,
                "skipped": skipped,
                "failed": 0,
            }

        def _warm_one(path: Path) -> tuple[int, int]:
            if self._thumb_abort:
                return 0, 0
            if ensure_sheet_proxy(path) is None:
                return 0, 1
            return 1, 0

        cpu = os.cpu_count() or 4
        workers = min(32, max(8, cpu * 2), total)
        done = 0
        last_ui = 0.0
        aborted = False
        self._thumb_abort = False
        pool = ThreadPoolExecutor(max_workers=workers)
        self._thumb_pool = pool
        try:
            futures = [pool.submit(_warm_one, path) for path in todo]
            for fut in as_completed(futures):
                if self._thumb_abort:
                    aborted = True
                    for pending in futures:
                        pending.cancel()
                    break
                try:
                    if fut.cancelled():
                        continue
                    w, f = fut.result()
                except Exception:
                    w, f = 0, 1
                written += w
                failed += f
                done += 1
                if progress is not None:
                    now = time.perf_counter()
                    if done == total or now - last_ui >= 0.25:
                        last_ui = now
                        progress(done, total, written)
                        if self._thumb_abort:
                            aborted = True
                            for pending in futures:
                                pending.cancel()
                            break
        finally:
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
            self._thumb_pool = None

        return {
            "sheets": total,
            "written": written,
            "skipped": skipped,
            "failed": failed,
            "workers": workers,
            "aborted": int(aborted),
        }

    def _iter_dds_search_roots(self) -> list[Path]:
        """Saved texture dirs (+ textures/ under scan roots). Built once per cache."""
        if self._dds_search_roots is not None:
            return self._dds_search_roots
        self._dds_search_roots = iter_texture_dir_roots(
            gamedata_texture_roots=self.gamedata_texture_roots,
            texture_scan_roots=self.texture_scan_roots,
        )
        return self._dds_search_roots

    def dds_search_roots(self) -> list[Path]:
        """Concrete dirs a DDS may be picked from (scanned / gamedata texture roots)."""
        return list(self._iter_dds_search_roots())

    def remember_dds(self, logical: str, path: Path) -> None:
        """Seed the DDS index after a successful file pick."""
        key = logical.strip().replace("/", "\\").lower()
        if key.endswith(".dds"):
            key = key[:-4]
        if not key:
            return
        self._dds_missing.discard(key)
        try:
            self._dds_index[key] = path.resolve()
        except OSError:
            self._dds_index[key] = path
        if self.path_index is not None:
            self.path_index.put_dds(key, path)
        self._resolve_cached.cache_clear()

    def remember_atlas(self, entry: AtlasCatalogEntry) -> None:
        """Seed atlas + DDS caches after an atlas pick."""
        self._atlas_missing.discard(entry.atlas_id)
        self._atlas[entry.atlas_id] = AtlasEntry(
            entry.file_name,
            entry.x,
            entry.y,
            entry.width,
            entry.height,
            resolved_id=entry.state_id or entry.atlas_id,
            source=entry.source,
        )
        dds = self.find_dds(entry.file_name)
        if dds is not None:
            self.remember_dds(entry.file_name, dds)
        self._resolve_cached.cache_clear()

    def dds_catalog_for_roots(self, roots: list[Path] | None = None) -> list[DdsCatalogEntry]:
        """DDS entries from the indexed map.

        ``roots is None`` → all indexed. ``roots == []`` → empty. Otherwise filter.
        """
        self._ensure_dds_map()
        if roots is None:
            cache_key = ("__all__",)
        elif not roots:
            return []
        else:
            cache_key = tuple(
                sorted(
                    str(p.resolve()) if p.exists() else str(p) for p in roots
                )
            )
        cached = self._dds_catalog_cache.get(cache_key)
        if cached is not None:
            return cached
        if roots is None:
            out = [
                DdsCatalogEntry(logical=k, path=p)
                for k, p in sorted(self._dds_index.items(), key=lambda kv: kv[0])
            ]
        else:
            prefixes = root_dir_prefixes(list(roots))
            out = [
                DdsCatalogEntry(logical=k, path=p)
                for k, p in self._dds_index.items()
                if path_is_under_prefixes(p, prefixes)
            ]
            out.sort(key=lambda e: e.logical.lower())
        self._dds_catalog_cache[cache_key] = out
        return out

    def scan_atlas_catalog(
        self, *, source_roots: list[Path] | None = None
    ) -> list[AtlasCatalogEntry]:
        """Load textures_descr ids. Optional ``source_roots`` limits which XMLs are parsed."""
        if source_roots is None:
            cache_key = ("__all__",)
        else:
            cache_key = tuple(
                sorted(
                    str(p.resolve()) if p.exists() else str(p) for p in source_roots
                )
            )
        cached = self._atlas_catalog_cache.get(cache_key)
        if cached is not None:
            return cached

        by_id: dict[str, AtlasCatalogEntry] = {}
        files = self._iter_descr_files()
        if source_roots:
            prefixes = root_dir_prefixes(list(source_roots))
            files = [p for p in files if path_is_under_prefixes(p, prefixes)]
        for path in files:
            root = _parse_xml_root(path)
            if root is None:
                continue
            for file_el in root.iter("file"):
                file_name = (file_el.get("name") or "").strip()
                if not file_name:
                    continue
                for tex in file_el.findall("texture"):
                    tid = (tex.get("id") or "").strip()
                    if not tid:
                        continue
                    try:
                        x = float(tex.get("x", "0"))
                        y = float(tex.get("y", "0"))
                        w = float(tex.get("width", "0"))
                        h = float(tex.get("height", "0"))
                    except ValueError:
                        continue
                    by_id[tid] = AtlasCatalogEntry(
                        atlas_id=tid,
                        file_name=file_name,
                        x=x,
                        y=y,
                        width=w,
                        height=h,
                        source=path,
                    )
                    self._atlas[tid] = AtlasEntry(
                        file_name,
                        x,
                        y,
                        w,
                        h,
                        resolved_id=tid,
                        source=path,
                    )
                    self._atlas_missing.discard(tid)

        # XML often names the stem; engine appends _e/_h/_t/_d. Expose stems too.
        for tid, entry in list(by_id.items()):
            if not tid.endswith("_e"):
                continue
            stem = tid[:-2]
            if not stem or stem in by_id:
                continue
            by_id[stem] = AtlasCatalogEntry(
                atlas_id=stem,
                file_name=entry.file_name,
                x=entry.x,
                y=entry.y,
                width=entry.width,
                height=entry.height,
                source=entry.source,
                is_stem=True,
                state_id=tid,
            )
            self._atlas[stem] = AtlasEntry(
                entry.file_name,
                entry.x,
                entry.y,
                entry.width,
                entry.height,
                resolved_id=tid,
                source=entry.source,
            )

        out = sorted(by_id.values(), key=lambda e: e.atlas_id.lower())
        self._atlas_catalog_cache[cache_key] = out
        return out

    def find_dds(self, logical: str) -> Path | None:
        """Resolve logical texture path → DDS. Later roots override (mod load order)."""
        key = logical.strip().replace("/", "\\").lower()
        if key.endswith(".dds"):
            key = key[:-4]
        if not key:
            return None
        if key in self._dds_missing:
            return None

        # Prefer full scan map (O(1)); build/load once per texture roots.
        self._ensure_dds_map()
        hit = self._dds_index.get(key)
        if hit is not None:
            if hit.is_file():
                return hit
            self._dds_index.pop(key, None)
            if self.path_index is not None:
                self.path_index.drop_dds(key)

        # Fallback probe (map miss / stale) — still respects later-root wins.
        rel = Path(*key.split("\\")).with_suffix(".dds")
        probed: Path | None = None
        for root in self._iter_dds_search_roots():
            candidate = root / rel
            if candidate.is_file():
                probed = candidate
        if probed is not None:
            self.remember_dds(key, probed)
            if self.path_index is not None:
                self.path_index.save()
            return probed
        self._dds_missing.add(key)
        return None

    def lookup_atlas(self, name: str) -> AtlasEntry | None:
        """Resolve atlas id, including button/checkbox stem → _e/_h/_t/_d."""
        if name in self._atlas:
            return self._atlas[name]
        if name not in self._atlas_missing:
            self._ingest_descr_for_ids({name})
        entry = self._atlas.get(name)
        if entry is not None:
            return entry
        for suf in _STATE_SUFFIXES:
            if name.endswith(suf):
                self._atlas_missing.add(name)
                return None
        for suf in _STATE_SUFFIXES:
            alt = name + suf
            if alt not in self._atlas and alt not in self._atlas_missing:
                self._ingest_descr_for_ids({alt})
            entry = self._atlas.get(alt)
            if entry is not None:
                return AtlasEntry(
                    entry.file_name,
                    entry.x,
                    entry.y,
                    entry.width,
                    entry.height,
                    resolved_id=alt,
                    source=entry.source,
                )
        self._atlas_missing.add(name)
        return None

    @lru_cache(maxsize=128)
    def _open_dds(self, path_str: str) -> Image.Image | None:
        return open_dds_image(path_str)

    def probe_ref(self, ref: TextureRef | None) -> tuple[bool, str]:
        """Check atlas/DDS presence without decoding or cropping the image."""
        if ref is None or not ref.name:
            return False, "no texture"
        name = ref.name
        file_name = name
        if not ref.is_path:
            entry = self.lookup_atlas(name)
            if entry is None:
                return False, f"unknown atlas id: {name}"
            file_name = entry.file_name
        dds = self.find_dds(file_name)
        if dds is None:
            return False, f"missing dds: {file_name}"
        return True, ""

    def resolve_ref(self, ref: TextureRef | None) -> ResolvedTexture:
        if ref is None or not ref.name:
            return ResolvedTexture(None, None, "no texture")
        return self._resolve_cached(
            ref.name,
            ref.uv_x,
            ref.uv_y,
            ref.uv_w,
            ref.uv_h,
            ref.has_uv,
            ref.is_path,
            ref.tint_r,
            ref.tint_g,
            ref.tint_b,
            ref.tint_a,
        )

    def resolve(self, ref: TextureRef | None) -> ResolvedTexture:
        return self.resolve_ref(ref)

    @lru_cache(maxsize=512)
    def _resolve_cached(
        self,
        name: str,
        uv_x: float,
        uv_y: float,
        uv_w: float,
        uv_h: float,
        has_uv: bool,
        is_path: bool,
        tint_r: int | None,
        tint_g: int | None,
        tint_b: int | None,
        tint_a: int | None,
    ) -> ResolvedTexture:
        file_name = name
        atlas_id = ""
        use_uv = has_uv
        rx, ry, rw, rh = uv_x, uv_y, uv_w, uv_h

        if not is_path:
            entry = self.lookup_atlas(name)
            if entry is None:
                return ResolvedTexture(None, None, f"unknown atlas id: {name}")
            file_name = entry.file_name
            atlas_id = entry.resolved_id or name
            if not use_uv:
                rx, ry, rw, rh = entry.x, entry.y, entry.width, entry.height
                use_uv = True

        dds = self.find_dds(file_name)
        if dds is None:
            return ResolvedTexture(None, None, f"missing dds: {file_name}", atlas_id)

        full = self._open_dds(str(dds))
        if full is None:
            return ResolvedTexture(dds, None, f"failed to open: {dds.name}", atlas_id)

        if use_uv and rw > 0 and rh > 0:
            left = int(rx)
            top = int(ry)
            right = int(rx + rw)
            bottom = int(ry + rh)
            if left >= full.width or top >= full.height:
                return ResolvedTexture(
                    dds,
                    None,
                    f"UV out of bounds on {dds.name} ({full.width}x{full.height}): "
                    f"{left},{top} {rw}x{rh}",
                    atlas_id,
                )
            left = max(0, min(left, full.width - 1))
            top = max(0, min(top, full.height - 1))
            right = max(left + 1, min(right, full.width))
            bottom = max(top + 1, min(bottom, full.height))
            crop = full.crop((left, top, right, bottom))
        else:
            crop = full.copy()

        ref_proxy = TextureRef(
            name=name,
            tint_r=tint_r,
            tint_g=tint_g,
            tint_b=tint_b,
            tint_a=tint_a,
        )
        if any(v is not None for v in (tint_r, tint_g, tint_b, tint_a)):
            crop = _apply_tint(crop, ref_proxy)

        return ResolvedTexture(dds, crop, "", atlas_id)


def _apply_tint(img: Image.Image, ref: TextureRef) -> Image.Image:
    r = ref.tint_r if ref.tint_r is not None else 255
    g = ref.tint_g if ref.tint_g is not None else 255
    b = ref.tint_b if ref.tint_b is not None else 255
    a = ref.tint_a if ref.tint_a is not None else 255
    if r == 255 and g == 255 and b == 255 and a == 255:
        return img
    bands = list(img.split())
    factors = (r, g, b, a)
    out_bands = []
    for band, factor in zip(bands, factors, strict=True):
        if factor == 255:
            out_bands.append(band)
        else:
            out_bands.append(band.point(lambda p, f=factor: (p * f) // 255))
    return Image.merge("RGBA", out_bands)
