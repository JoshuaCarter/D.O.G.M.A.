"""Resolve Stalker UI texture IDs / paths to DDS crops.

Directory lists come from Setup / Rescan (saved in settings). Full-tree indexes are
not built at launch — atlas/DDS lookups run for the IDs a loaded document needs.
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import Image

from .model import LayoutNode, TextureRef

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


def gamma_relative_path(path: Path) -> str:
    """Path under GAMMA (or gamedata/…); never the full drive path."""
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


def iter_texture_dir_roots(
    *,
    gamedata_texture_roots: list[Path],
    texture_scan_roots: list[Path],
) -> list[Path]:
    """Concrete ``textures`` dirs (and scan roots) used for DDS lookup / picking."""
    roots: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        if not path.is_dir():
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
        _add(scan)
        try:
            for tex_dir in scan.rglob("textures"):
                if tex_dir.is_dir() and tex_dir.name.lower() == "textures":
                    _add(tex_dir)
        except OSError:
            continue
    return roots


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


def path_is_under_roots(path: Path, roots: list[Path]) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return True
        except (OSError, ValueError):
            continue
    return False


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
    ) -> None:
        self.texture_scan_roots = texture_scan_roots
        self.gamedata_texture_roots = gamedata_texture_roots
        self.descr_scan_roots = descr_scan_roots
        self.gamedata_descr_roots = gamedata_descr_roots
        # Lazy caches — filled by warm_* / first lookup, not by scanning everything.
        self._atlas: dict[str, AtlasEntry] = {}
        self._atlas_missing: set[str] = set()
        self._dds_index: dict[str, Path] = {}
        self._dds_missing: set[str] = set()
        self._descr_files: list[Path] | None = None
        self._dds_search_roots: list[Path] | None = None

    def clear_cache(self) -> None:
        self._atlas.clear()
        self._atlas_missing.clear()
        self._dds_index.clear()
        self._dds_missing.clear()
        self._descr_files = None
        self._dds_search_roots = None
        self._open_dds.cache_clear()
        self._resolve_cached.cache_clear()

    def rebuild_indexes(self) -> None:
        """Compatibility no-op — indexes are demand-driven now."""
        self.clear_cache()

    @property
    def atlas_count(self) -> int:
        return len(self._atlas)

    @property
    def dds_count(self) -> int:
        return len(self._dds_index)

    def _iter_descr_files(self) -> list[Path]:
        if self._descr_files is not None:
            return self._descr_files
        files: list[Path] = []
        seen: set[Path] = set()
        for root in self.gamedata_descr_roots:
            if not root.is_dir():
                continue
            try:
                batch = sorted(root.glob("*.xml"))
            except OSError:
                continue
            for path in batch:
                try:
                    key = path.resolve()
                except OSError:
                    continue
                if key in seen:
                    continue
                seen.add(key)
                files.append(path)
        for root in self.descr_scan_roots:
            if not root.is_dir():
                continue
            try:
                batch = sorted(root.rglob("**/textures_descr/*.xml"))
            except OSError:
                continue
            for path in batch:
                try:
                    key = path.resolve()
                except OSError:
                    continue
                if key in seen:
                    continue
                seen.add(key)
                files.append(path)
        self._descr_files = files
        return files

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
                        file_name, x, y, w, h, resolved_id=tid
                    )
        for tid in pending:
            if tid not in self._atlas:
                self._atlas_missing.add(tid)

    def warm_for_document(self, doc: LayoutNode) -> None:
        """Resolve only atlas ids / DDS paths referenced by ``doc``."""
        atlas_ids, path_names = collect_doc_texture_ids(doc)
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
        for tid in atlas_ids:
            entry = self.lookup_atlas(tid)
            if entry is not None:
                needed_files.add(entry.file_name)
        for logical in needed_files:
            self.find_dds(logical)

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
        )
        dds = self.find_dds(entry.file_name)
        if dds is not None:
            self.remember_dds(entry.file_name, dds)
        self._resolve_cached.cache_clear()

    def scan_atlas_catalog(self) -> list[AtlasCatalogEntry]:
        """Load every textures_descr id. Later files override. Add button stems from _e."""
        by_id: dict[str, AtlasCatalogEntry] = {}
        for path in self._iter_descr_files():
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
                        file_name, x, y, w, h, resolved_id=tid
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
            )

        return sorted(by_id.values(), key=lambda e: e.atlas_id.lower())

    def find_dds(self, logical: str) -> Path | None:
        """Resolve logical texture path → DDS. Later roots override (mod load order)."""
        key = logical.strip().replace("/", "\\").lower()
        if key.endswith(".dds"):
            key = key[:-4]
        if key in self._dds_index:
            return self._dds_index[key]
        if key in self._dds_missing:
            return None

        rel = Path(*key.split("\\")).with_suffix(".dds")
        hit: Path | None = None
        for root in self._iter_dds_search_roots():
            candidate = root / rel
            if candidate.is_file():
                hit = candidate
        if hit is not None:
            self._dds_index[key] = hit
            return hit
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
                )
        self._atlas_missing.add(name)
        return None

    @lru_cache(maxsize=96)
    def _open_dds(self, path_str: str) -> Image.Image | None:
        try:
            img = Image.open(path_str)
            img.load()
            return img.convert("RGBA")
        except OSError:
            return None

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
