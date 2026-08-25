"""Anomaly bitmap UI fonts (DDS glyph atlas + .ini) for SAGE canvas text.

Matches xray-monolith ``CGameFont`` / ``CUILines``:

- Resolve XML ``font=`` via ``fonts.ltx`` sections.
- Pick atlas by **device height** thresholds (``texture800`` / ``texture`` /
  ``texture1600`` / ``texture2160``), walking down if a key is missing.
- Draw height in 1024×768 HUD space = ``ini_height * 768 / device_height``
  (engine draws ``ini_height`` screen pixels; UI scale is ``device/768``).
- ``width_correction`` in the INI is ignored (commented out in GameFont.cpp).
- Letterica sections have no ``size`` / ``interval`` in fonts.ltx.
"""

from __future__ import annotations

import configparser
import re
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from PyQt6.QtGui import QColor, QImage, QPixmap

from .cache_store import PathIndex, load_font_cache, save_font_cache
from .diaglog import get_logger
from .model import LayoutNode
from .settings import UI_HEIGHT
from .textures import TextureResolver, abs_log_path, open_dds_image

_log = get_logger("fonts")

# XML font= → fonts.ltx section (engine names).
FONT_SECTION_MAP: dict[str, str] = {
    "letterica16": "ui_font_letterica16_russian",
    "letterica18": "ui_font_letterica18_russian",
    "letterica25": "ui_font_letter_25",
    "graffiti19": "ui_font_graffiti19_russian",
    "graffiti22": "ui_font_graffiti22_russian",
    "graffiti32": "ui_font_graff_32",
    "graffiti40": "ui_font_graff_40",
    "graffiti50": "ui_font_graff_50",
    # Engine InitFont uses arial_14; arial14 accepted as alias.
    "arial_14": "ui_font_arial_14",
    "arial14": "ui_font_arial_14",
    "arial21": "ui_font_arial_21",
}

# CUIXmlInit::InitFont hard-coded XML names (UIXmlInit.cpp).
ENGINE_FONT_NAMES: tuple[str, ...] = (
    "letterica16",
    "letterica18",
    "letterica25",
    "graffiti19",
    "graffiti22",
    "graffiti32",
    "graffiti50",
    "arial_14",
    "medium",
    "small",
    "di",
)

# GameFont.cpp FindTextureName order (index by Device.dwHeight).
_TEXTURE_VARIANT_BY_IDX = ("texture800", "texture", "texture1600", "texture2160")


def variant_index_for_height(device_height: int) -> int:
    """Match GameFont.cpp: h<=600→800, h<1024→1024, h<1440→1600, else→2160."""
    h = int(device_height)
    if h <= 600:
        return 0
    if h < 1024:
        return 1
    if h < 1440:
        return 2
    return 3


def ui_scale_for_device(device_height: int) -> float:
    """Screen→HUD: engine draws ini_height screen px; widgets use ×(h/768)."""
    h = max(1, int(device_height))
    return float(UI_HEIGHT) / float(h)


@dataclass
class GlyphRect:
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return max(0, self.x1 - self.x0)

    @property
    def height(self) -> int:
        return max(0, self.y1 - self.y0)


@dataclass
class FontAtlas:
    name: str
    logical: str
    height: int  # atlas line height in texture pixels (symbol_coords height)
    width_correction: float  # stored from INI; not applied (engine ignores)
    glyphs: dict[int, GlyphRect]
    sheet: Image.Image
    dds_path: Path | None = None
    ini_path: Path | None = None
    variant_key: str = "texture"
    device_height: int = 1080
    # HUD scale: height_ui = height * ui_scale
    ui_scale: float = 1.0

    @property
    def ui_height(self) -> float:
        return float(self.height) * self.ui_scale


@dataclass
class _FontLtxEntry:
    section: str
    variants: dict[str, str] = field(default_factory=dict)
    # Optional; letterica has neither. Applied if present (stat_font interval).
    size: float | None = None
    interval: tuple[float, float] | None = None


def collect_doc_fonts(doc: LayoutNode) -> set[str]:
    out: set[str] = set()
    for node in doc.iter_all():
        if node.text is None:
            continue
        font = (node.text.font or "").strip()
        if font:
            out.add(font)
    return out


def _char_code(ch: str) -> int | None:
    """Map Unicode char → Stalker glyph key (CP1251 / Latin-1 byte)."""
    if not ch:
        return None
    try:
        return ch.encode("cp1251")[0]
    except UnicodeEncodeError:
        try:
            return ch.encode("latin-1")[0]
        except UnicodeEncodeError:
            return None


def _parse_font_ini(path: Path) -> tuple[int, float, dict[int, GlyphRect]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    parser = configparser.ConfigParser()
    parser.optionxform = str  # keep case
    try:
        parser.read_string(raw)
    except configparser.Error:
        parser = configparser.ConfigParser()
        parser.optionxform = str
        parser.read_string(raw.replace("\t", " "))
    height = 16
    width_correction = 0.0
    glyphs: dict[int, GlyphRect] = {}
    if parser.has_section("width_correction"):
        try:
            width_correction = float(parser.get("width_correction", "value", fallback="0"))
        except ValueError:
            width_correction = 0.0
    if parser.has_section("symbol_coords"):
        try:
            height = int(float(parser.get("symbol_coords", "height", fallback="16")))
        except ValueError:
            height = 16
        for key, val in parser.items("symbol_coords"):
            if key == "height":
                continue
            try:
                code = int(key)
            except ValueError:
                continue
            parts = [p.strip() for p in val.replace("\t", " ").split(",") if p.strip()]
            if len(parts) < 4:
                continue
            try:
                x0, y0, x1, y1 = (int(float(parts[i])) for i in range(4))
            except ValueError:
                continue
            glyphs[code] = GlyphRect(x0, y0, x1, y1)
    return height, width_correction, glyphs


def _parse_fonts_ltx(path: Path) -> dict[str, _FontLtxEntry]:
    """Parse fonts.ltx sections → texture variants (+ optional size/interval)."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    out: dict[str, _FontLtxEntry] = {}
    section = ""
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith(";") or s.startswith("//"):
            continue
        if s.startswith("[") and s.endswith("]"):
            body = s[1:-1].split(":", 1)[0].strip()
            section = body
            if section and section not in out:
                out[section] = _FontLtxEntry(section=section)
            continue
        if not section or "=" not in s:
            continue
        key, _, val = s.partition("=")
        key = key.strip().lower()
        val = val.split(";")[0].strip()
        entry = out[section]
        if key.startswith("texture") and val:
            entry.variants[key] = val.replace("/", "\\")
        elif key == "size" and val:
            try:
                entry.size = float(val.split(",")[0].strip())
            except ValueError:
                pass
        elif key == "interval" and val:
            parts = [p.strip() for p in val.split(",")]
            if len(parts) >= 2:
                try:
                    entry.interval = (float(parts[0]), float(parts[1]))
                except ValueError:
                    pass
    return out


def _pil_to_qpixmap(img: Image.Image) -> QPixmap:
    """1:1 RGBA → QPixmap (no rescale, no devicePixelRatio bump)."""
    img = img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    qimg = QImage(
        data, img.width, img.height, img.width * 4, QImage.Format.Format_RGBA8888
    ).copy()
    pix = QPixmap.fromImage(qimg)
    pix.setDevicePixelRatio(1.0)
    return pix


def _tint_glyph(glyph: Image.Image, color: QColor) -> Image.Image:
    """Solid-color glyph using atlas alpha (A8 fonts are white RGB + alpha)."""
    g = glyph.convert("RGBA")
    _r, _g, _b, a = g.split()
    solid = Image.new("RGBA", g.size, (color.red(), color.green(), color.blue(), 255))
    solid.putalpha(a)
    return solid


def _glyphs_from_meta(meta: dict) -> dict[int, GlyphRect]:
    glyphs: dict[int, GlyphRect] = {}
    raw_glyphs = meta.get("glyphs") or {}
    if not isinstance(raw_glyphs, dict):
        return glyphs
    for k, v in raw_glyphs.items():
        try:
            code = int(k)
            if isinstance(v, list) and len(v) >= 4:
                glyphs[code] = GlyphRect(
                    int(v[0]), int(v[1]), int(v[2]), int(v[3])
                )
        except (TypeError, ValueError):
            continue
    return glyphs


class FontResolver:
    def __init__(
        self,
        textures: TextureResolver,
        path_index: PathIndex | None = None,
        *,
        device_height: int = 1080,
    ) -> None:
        self.textures = textures
        self.path_index = path_index
        self.device_height = max(1, int(device_height))
        self._atlases: dict[str, FontAtlas] = {}
        self._missing: set[str] = set()
        self._ltx_sections: dict[str, _FontLtxEntry] | None = None

    def clear_cache(self) -> None:
        self._atlases.clear()
        self._missing.clear()
        self._ltx_sections = None

    def known_font_names(self) -> list[str]:
        """XML ``font=`` values: engine InitFont list + fonts.ltx reverse map."""
        names: set[str] = set(ENGINE_FONT_NAMES)
        names.update(FONT_SECTION_MAP.keys())
        rev = {section: xml for xml, section in FONT_SECTION_MAP.items()}
        for section in self._load_fonts_ltx():
            xml = rev.get(section)
            if xml:
                names.add(xml)
            # Prefer engine-facing arial_14 over arial14 alias in the list.
            if section == "ui_font_arial_14":
                names.add("arial_14")
        if self.path_index is not None:
            cached = self.path_index.get_fonts()
            if cached:
                names.update(cached)
        # Prefer canonical engine spellings in the dropdown.
        names.discard("arial14")
        return sorted(names, key=str.lower)

    def ensure_font_index(self) -> list[str]:
        """Scan fonts.ltx, persist name list to path cache (last wins via ltx merge)."""
        names = self.known_font_names()
        if self.path_index is not None:
            self.path_index.put_fonts(names)
        return names

    def set_device_height(self, height: int) -> None:
        h = max(1, int(height))
        if h == self.device_height:
            return
        self.device_height = h
        self.clear_cache()

    @property
    def count(self) -> int:
        return len(self._atlases)

    def invalidate_for_document(self, doc: LayoutNode) -> None:
        """Drop loaded font atlases for this document so DDS/INI edits re-load."""
        names = collect_doc_fonts(doc)
        for name in names:
            self._atlases.pop(name, None)
            self._missing.discard(name)
        _log.info("invalidate fonts: dropped %d name(s)", len(names))

    def warm_for_document(self, doc: LayoutNode) -> None:
        names = collect_doc_fonts(doc)
        _log.info("warm fonts: %d name(s) device_h=%s", len(names), self.device_height)
        for name in sorted(names):
            atlas = self.resolve_font(name)
            if atlas is None:
                _log.warning("font unresolved: %s", name)
                continue
            dds_s = abs_log_path(atlas.dds_path) if atlas.dds_path else "?"
            ini_s = abs_log_path(atlas.ini_path) if atlas.ini_path else "?"
            _log.info(
                "font %s -> logical %s variant=%s glyphs=%d | dds %s | ini %s | "
                "h=%s ui_scale=%.4f",
                name,
                atlas.logical,
                atlas.variant_key,
                len(atlas.glyphs),
                dds_s,
                ini_s,
                atlas.height,
                atlas.ui_scale,
            )

    def _iter_fonts_ltx_paths(self) -> list[Path]:
        """Discover fonts.ltx beside texture roots (Anomaly + GAMMA packs)."""
        found: list[Path] = []
        seen: set[str] = set()

        def add(path: Path) -> None:
            try:
                key = str(path.resolve()).lower()
            except OSError:
                key = str(path).lower()
            if key in seen or not path.is_file():
                return
            seen.add(key)
            found.append(path)

        for root in self.textures.gamedata_texture_roots:
            # …/gamedata/textures → …/gamedata/configs/fonts.ltx
            # …/tools/_unpacked/textures → …/tools/_unpacked/configs/fonts.ltx
            add(root.parent / "configs" / "fonts.ltx")
        for root in self.textures.texture_scan_roots:
            add(root / "gamedata" / "configs" / "fonts.ltx")
            add(root / "configs" / "fonts.ltx")
        return found

    def _load_fonts_ltx(self) -> dict[str, _FontLtxEntry]:
        if self._ltx_sections is not None:
            return self._ltx_sections
        merged: dict[str, _FontLtxEntry] = {}
        # Later files override (MO2 priority — same as texture roots).
        for path in self._iter_fonts_ltx_paths():
            for name, entry in _parse_fonts_ltx(path).items():
                prev = merged.get(name)
                if prev is None:
                    merged[name] = entry
                else:
                    variants = dict(prev.variants)
                    variants.update(entry.variants)
                    merged[name] = _FontLtxEntry(
                        section=name,
                        variants=variants,
                        size=entry.size if entry.size is not None else prev.size,
                        interval=(
                            entry.interval
                            if entry.interval is not None
                            else prev.interval
                        ),
                    )
        self._ltx_sections = merged
        return merged

    def _pick_variant(
        self, entry: _FontLtxEntry
    ) -> tuple[str | None, str]:
        """Engine FindTextureName: start at height bucket, walk down."""
        idx = variant_index_for_height(self.device_height)
        while idx >= 0:
            vkey = _TEXTURE_VARIANT_BY_IDX[idx]
            logical = entry.variants.get(vkey)
            if logical and self.textures.find_dds(logical) is not None:
                return logical, vkey
            idx -= 1
        # Declared but missing — return base texture for error path.
        for vkey in ("texture", "texture1600", "texture800", "texture2160"):
            logical = entry.variants.get(vkey)
            if logical:
                return logical, vkey
        return None, "texture"

    def _resolve_logical_dds(self, font_name: str) -> tuple[str | None, str]:
        """Pick texture path from fonts.ltx using engine height rules."""
        key = font_name.strip().lower()
        if key == "arial_14":
            key = "arial14"
        section_name = FONT_SECTION_MAP.get(key, "") or FONT_SECTION_MAP.get(
            font_name.strip().lower(), ""
        )
        sections = self._load_fonts_ltx()
        entry = sections.get(section_name) if section_name else None
        if entry is None:
            for name, ent in sections.items():
                if key in name.lower():
                    entry = ent
                    break
        if entry is not None:
            return self._pick_variant(entry)

        legacy = {
            "letterica16": r"ui\ui_font_letter_16_1024",
            "letterica18": r"ui\ui_font_letter_18_1024",
            "letterica25": r"ui\ui_font_letter_25_1024",
            "graffiti19": r"ui\ui_font_graff_19_1024",
            "graffiti22": r"ui\ui_font_graff_22_1024",
            "graffiti32": r"ui\ui_font_graff_32_1024",
            "graffiti40": r"ui\ui_font_graff_40_1024",
            "graffiti50": r"ui\ui_font_graff_50_1024",
            "arial14": r"ui\ui_font_arial_14_1024",
            "arial_14": r"ui\ui_font_arial_14_1024",
            "arial21": r"ui\ui_font_arial_21_1024",
        }.get(key)
        if legacy is None:
            return None, "texture"

        # Emulate variant walk without fonts.ltx.
        idx = variant_index_for_height(self.device_height)
        suffix_by_idx = ("_800", "_1024", "_1600", "_2160")
        while idx >= 0:
            cand = re.sub(r"_1024$", suffix_by_idx[idx], legacy)
            if self.textures.find_dds(cand) is not None:
                return cand, _TEXTURE_VARIANT_BY_IDX[idx]
            idx -= 1
        if self.textures.find_dds(legacy) is not None:
            return legacy, "texture"
        hi = re.sub(r"_1024$", "_1600", legacy)
        if hi != legacy and self.textures.find_dds(hi) is not None:
            return hi, "texture1600"
        return legacy, "texture"

    def resolve_font(self, name: str) -> FontAtlas | None:
        key = (name or "").strip()
        if not key:
            return None
        if key in self._atlases:
            return self._atlases[key]
        if key in self._missing:
            return None
        logical, variant_key = self._resolve_logical_dds(key)
        if not logical:
            if "\\" in key or key.startswith("ui"):
                logical = key
                variant_key = "texture"
            else:
                self._missing.add(key)
                return None
        atlas = self._load_atlas(key, logical, variant_key)
        if atlas is None:
            self._missing.add(key)
            return None
        self._atlases[key] = atlas
        return atlas

    def _find_dds_ini(self, logical: str) -> tuple[Path | None, Path | None]:
        dds = None
        if self.path_index is not None:
            dds = self.path_index.get_dds(logical)
        if dds is None:
            dds = self.textures.find_dds(logical)
            if dds is not None and self.path_index is not None:
                self.path_index.put_dds(logical, dds)
        if dds is None:
            return None, None
        ini = dds.with_suffix(".ini")
        if not ini.is_file():
            return dds, None
        return dds, ini

    def _load_atlas(
        self, name: str, logical: str, variant_key: str
    ) -> FontAtlas | None:
        stem = Path(logical.replace("\\", "/")).name
        meta = load_font_cache(stem)
        dds: Path | None = None
        ini: Path | None = None
        height = 16
        width_correction = 0.0
        glyphs: dict[int, GlyphRect] = {}

        if meta is not None:
            dds_s = str(meta.get("dds_path") or "")
            ini_s = str(meta.get("ini_path") or "")
            dds = Path(dds_s) if dds_s else None
            ini = Path(ini_s) if ini_s else None
            height = int(meta.get("height") or 16)
            width_correction = float(meta.get("width_correction") or 0)
            glyphs = _glyphs_from_meta(meta)

        if dds is None or not dds.is_file() or ini is None or not ini.is_file():
            dds, ini = self._find_dds_ini(logical)
        if dds is None or ini is None:
            return None

        if not glyphs:
            try:
                height, width_correction, glyphs = _parse_font_ini(ini)
            except OSError:
                return None
            try:
                dds_mtime = dds.stat().st_mtime
                ini_mtime = ini.stat().st_mtime
            except OSError:
                dds_mtime = 0.0
                ini_mtime = 0.0
            save_font_cache(
                stem,
                meta={
                    "name": name,
                    "logical": logical,
                    "height": height,
                    "width_correction": width_correction,
                    "dds_path": str(dds.resolve()) if dds else "",
                    "ini_path": str(ini.resolve()) if ini else "",
                    "dds_path_mtime": dds_mtime,
                    "ini_path_mtime": ini_mtime,
                    "glyphs": {
                        str(code): [g.x0, g.y0, g.x1, g.y1]
                        for code, g in glyphs.items()
                    },
                },
            )

        sheet = open_dds_image(dds)
        if sheet is None:
            sheet = self.textures._open_dds(str(dds))
        if sheet is None:
            return None

        # Engine: fCurrentHeight = symbol_coords height; fonts.ltx `size` can
        # override (letterica has none). Screen px → HUD: × 768/device_height.
        scale = ui_scale_for_device(self.device_height)
        section_name = FONT_SECTION_MAP.get(name.strip().lower(), "")
        entry = self._load_fonts_ltx().get(section_name) if section_name else None
        if entry is not None and entry.size is not None and entry.size > 0:
            # size replaces fCurrentHeight; still sample full glyph cells.
            scale *= float(entry.size) / float(height) if height > 0 else 1.0

        return FontAtlas(
            name=name,
            logical=logical,
            height=height,
            width_correction=width_correction,
            glyphs=glyphs,
            sheet=sheet,
            dds_path=dds,
            ini_path=ini,
            variant_key=variant_key,
            device_height=self.device_height,
            ui_scale=scale,
        )

    def render_text(self, atlas: FontAtlas, text: str, color: QColor) -> QPixmap:
        """Compose at native atlas pixels (caller applies atlas.ui_scale)."""
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n") if "\n" in text else [text]
        line_metrics: list[tuple[str, int]] = []
        max_w = 1
        for line in lines:
            width = 0.0
            for ch in line:
                code = _char_code(ch)
                if code is None:
                    continue
                g = atlas.glyphs.get(code)
                if g is None or g.width <= 0:
                    continue
                width += g.width
            w = max(1, int(round(width)))
            line_metrics.append((line, w))
            max_w = max(max_w, w)
        total_h = max(1, atlas.height * len(lines))
        out = Image.new("RGBA", (max_w, total_h), (0, 0, 0, 0))
        y = 0
        for line, _lw in line_metrics:
            x = 0.0
            for ch in line:
                code = _char_code(ch)
                if code is None:
                    continue
                g = atlas.glyphs.get(code)
                if g is None or g.width <= 0:
                    continue
                crop = atlas.sheet.crop((g.x0, g.y0, g.x1, g.y1))
                tinted = _tint_glyph(crop, color)
                out.paste(tinted, (int(x), y), tinted)
                x += g.width
            y += atlas.height
        return _pil_to_qpixmap(out)

    def render_fallback(
        self, text: str, *, point_size: int, color: QColor, max_width: int
    ) -> QPixmap:
        """QFont stand-in when engine atlas is missing."""
        point_size = max(8, min(40, int(point_size) or 16))
        try:
            font = ImageFont.truetype("segoeui.ttf", point_size)
        except OSError:
            font = ImageFont.load_default()
        dummy = Image.new("RGBA", (1, 1))
        draw = ImageDraw.Draw(dummy)
        bbox = draw.textbbox((0, 0), text or " ", font=font)
        w = max(1, min(max_width, bbox[2] - bbox[0] + 2))
        h = max(1, bbox[3] - bbox[1] + 2)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.text(
            (0, 0),
            text,
            font=font,
            fill=(color.red(), color.green(), color.blue(), color.alpha()),
        )
        return _pil_to_qpixmap(img)
