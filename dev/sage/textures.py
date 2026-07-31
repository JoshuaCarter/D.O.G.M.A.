"""Resolve Stalker UI texture IDs / paths to DDS crops."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import Image

from .model import TextureRef

# Init3tButton / checkbox / radio: XML names the stem; engine appends state.
_STATE_SUFFIXES = ("_e", "_h", "_t", "_d", "_s", "_u")


@dataclass
class AtlasEntry:
    file_name: str  # e.g. ui\dots
    x: float
    y: float
    width: float
    height: float
    resolved_id: str = ""  # actual atlas id used (may be stem+_e)


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
        self._atlas: dict[str, AtlasEntry] = {}
        self._dds_index: dict[str, Path] = {}
        self.rebuild_indexes()

    def rebuild_indexes(self) -> None:
        self._atlas.clear()
        self._dds_index.clear()
        self._load_atlas_index()
        self._load_dds_index()
        self._prefer_resolvable_atlas()
        self._open_dds.cache_clear()
        self._resolve_cached.cache_clear()

    def _prefer_resolvable_atlas(self) -> None:
        """If an atlas id points at a missing DDS, try alternate file names from duplicates.

        Vanilla ui_new_game.xml maps some checkbox UVs onto ui_actor_multiplayer_game_menu;
        that file exists in GAMMA UI so it's fine. This pass only fixes true orphans by
        scanning all descr again for the same id with a resolvable file.
        """
        orphans = [tid for tid, e in self._atlas.items() if self.find_dds(e.file_name) is None]
        if not orphans:
            return
        orphan_set = set(orphans)
        descr_files: list[Path] = []
        for root in list(self.gamedata_descr_roots) + list(self.descr_scan_roots):
            if root.is_dir():
                descr_files.extend(root.rglob("*.xml"))
        for path in descr_files:
            try:
                root = ET.parse(path).getroot()
            except ET.ParseError:
                continue
            for file_el in root.iter("file"):
                file_name = (file_el.get("name") or "").strip()
                if not file_name or self.find_dds(file_name) is None:
                    continue
                for tex in file_el.findall("texture"):
                    tid = (tex.get("id") or "").strip()
                    if tid not in orphan_set:
                        continue
                    try:
                        x = float(tex.get("x", "0"))
                        y = float(tex.get("y", "0"))
                        w = float(tex.get("width", "0"))
                        h = float(tex.get("height", "0"))
                    except ValueError:
                        continue
                    self._atlas[tid] = AtlasEntry(file_name, x, y, w, h, resolved_id=tid)
                    orphan_set.discard(tid)

    def _load_atlas_index(self) -> None:
        descr_files: list[Path] = []
        # Base game / unpacked first, then DOGMA scan (later overrides)
        for root in self.gamedata_descr_roots:
            if root.is_dir():
                descr_files.extend(sorted(root.rglob("*.xml")))
        for root in self.descr_scan_roots:
            if not root.is_dir():
                continue
            descr_files.extend(sorted(root.rglob("**/textures_descr/**/*.xml")))
            descr_files.extend(sorted(root.rglob("textures_descr/*.xml")))
        seen: set[Path] = set()
        for path in descr_files:
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            self._parse_descr(path)

    def _parse_descr(self, path: Path) -> None:
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            return
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
                self._atlas[tid] = AtlasEntry(file_name, x, y, w, h, resolved_id=tid)

    def _load_dds_index(self) -> None:
        # Base packs first; DOGMA src last so it overrides
        for root in self.gamedata_texture_roots:
            if root.is_dir():
                self._index_dds_tree(root)
        for root in self.texture_scan_roots:
            if not root.is_dir():
                continue
            for tex_dir in sorted(root.rglob("textures")):
                if tex_dir.is_dir() and tex_dir.name.lower() == "textures":
                    self._index_dds_tree(tex_dir)

    def _index_dds_tree(self, root: Path) -> None:
        for dds in root.rglob("*.dds"):
            try:
                rel = dds.relative_to(root).with_suffix("")
            except ValueError:
                continue
            key = str(rel).replace("/", "\\").lower()
            self._dds_index[key] = dds

    def find_dds(self, logical: str) -> Path | None:
        key = logical.strip().replace("/", "\\").lower()
        if key.endswith(".dds"):
            key = key[:-4]
        return self._dds_index.get(key)

    def lookup_atlas(self, name: str) -> AtlasEntry | None:
        """Resolve atlas id, including button/checkbox stem → _e/_h/_t/_d."""
        entry = self._atlas.get(name)
        if entry is not None:
            return entry
        # Already has a state suffix?
        for suf in _STATE_SUFFIXES:
            if name.endswith(suf):
                return None
        for suf in _STATE_SUFFIXES:
            entry = self._atlas.get(name + suf)
            if entry is not None:
                return AtlasEntry(
                    entry.file_name,
                    entry.x,
                    entry.y,
                    entry.width,
                    entry.height,
                    resolved_id=name + suf,
                )
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

    # Back-compat alias
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
