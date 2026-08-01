"""Anomaly bitmap UI fonts (DDS glyph atlas + .ini) for SAGE canvas text."""

from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from PyQt6.QtGui import QColor, QImage, QPixmap

from .cache_store import PathIndex, load_font_cache, save_font_cache
from .model import LayoutNode
from .textures import TextureResolver, open_dds_image

# XML font= → logical DDS under textures/ (1024 variants).
FONT_NAME_MAP: dict[str, str] = {
    "letterica16": r"ui\ui_font_letter_16_1024",
    "letterica18": r"ui\ui_font_letter_18_1024",
    "letterica25": r"ui\ui_font_letter_25_1024",
    "graffiti19": r"ui\ui_font_graff_19_1024",
    "graffiti22": r"ui\ui_font_graff_22_1024",
    "graffiti32": r"ui\ui_font_graff_32_1024",
    "graffiti40": r"ui\ui_font_graff_40_1024",
    "graffiti50": r"ui\ui_font_graff_50_1024",
    "arial14": r"ui\ui_font_arial_14_1024",
    "arial21": r"ui\ui_font_arial_21_1024",
}


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
    height: int
    width_correction: float
    glyphs: dict[int, GlyphRect]
    sheet: Image.Image
    dds_path: Path | None = None
    ini_path: Path | None = None


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
    # configparser needs section headers; file already has them.
    parser = configparser.ConfigParser()
    parser.optionxform = str  # keep case
    try:
        parser.read_string(raw)
    except configparser.Error:
        # Some inis use tabs; retry stripped
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


def _pil_to_qpixmap(img: Image.Image) -> QPixmap:
    img = img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    qimg = QImage(
        data, img.width, img.height, img.width * 4, QImage.Format.Format_RGBA8888
    ).copy()
    return QPixmap.fromImage(qimg)


def _tint_glyph(glyph: Image.Image, color: QColor) -> Image.Image:
    """Multiply glyph RGB by color; keep glyph alpha."""
    g = glyph.convert("RGBA")
    r, gch, b, a = g.split()
    # Use alpha as mask; fill with solid color
    solid = Image.new("RGBA", g.size, (color.red(), color.green(), color.blue(), 255))
    solid.putalpha(a)
    return solid


class FontResolver:
    def __init__(
        self,
        textures: TextureResolver,
        path_index: PathIndex | None = None,
    ) -> None:
        self.textures = textures
        self.path_index = path_index
        self._atlases: dict[str, FontAtlas] = {}
        self._missing: set[str] = set()

    def clear_cache(self) -> None:
        self._atlases.clear()
        self._missing.clear()

    @property
    def count(self) -> int:
        return len(self._atlases)

    def warm_for_document(self, doc: LayoutNode) -> None:
        for name in collect_doc_fonts(doc):
            self.resolve_font(name)

    def resolve_font(self, name: str) -> FontAtlas | None:
        key = (name or "").strip()
        if not key:
            return None
        if key in self._atlases:
            return self._atlases[key]
        if key in self._missing:
            return None
        logical = FONT_NAME_MAP.get(key.lower(), "")
        if not logical:
            # Allow raw logical path
            if "\\" in key or key.startswith("ui"):
                logical = key
            else:
                self._missing.add(key)
                return None
        atlas = self._load_atlas(key, logical)
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

    def _load_atlas(self, name: str, logical: str) -> FontAtlas | None:
        stem = Path(logical.replace("\\", "/")).name
        cached = load_font_cache(stem)
        if cached is not None:
            meta, atlas_png = cached
            try:
                sheet = Image.open(atlas_png)
                sheet.load()
                sheet = sheet.convert("RGBA")
            except OSError:
                sheet = None
            if sheet is not None:
                glyphs: dict[int, GlyphRect] = {}
                raw_glyphs = meta.get("glyphs") or {}
                if isinstance(raw_glyphs, dict):
                    for k, v in raw_glyphs.items():
                        try:
                            code = int(k)
                            if isinstance(v, list) and len(v) >= 4:
                                glyphs[code] = GlyphRect(
                                    int(v[0]), int(v[1]), int(v[2]), int(v[3])
                                )
                        except (TypeError, ValueError):
                            continue
                return FontAtlas(
                    name=name,
                    logical=logical,
                    height=int(meta.get("height") or 16),
                    width_correction=float(meta.get("width_correction") or 0),
                    glyphs=glyphs,
                    sheet=sheet,
                    dds_path=Path(str(meta.get("dds_path") or "")) or None,
                    ini_path=Path(str(meta.get("ini_path") or "")) or None,
                )

        dds, ini = self._find_dds_ini(logical)
        if dds is None or ini is None:
            return None
        try:
            height, width_correction, glyphs = _parse_font_ini(ini)
        except OSError:
            return None
        sheet = open_dds_image(dds)
        if sheet is None:
            # Prefer TextureResolver cache path (same A8 fallback).
            sheet = self.textures._open_dds(str(dds))
        if sheet is None:
            return None

        try:
            dds_mtime = dds.stat().st_mtime
            ini_mtime = ini.stat().st_mtime
        except OSError:
            dds_mtime = 0.0
            ini_mtime = 0.0
        meta = {
            "name": name,
            "logical": logical,
            "height": height,
            "width_correction": width_correction,
            "dds_path": str(dds.resolve()) if dds else "",
            "ini_path": str(ini.resolve()) if ini else "",
            "dds_path_mtime": dds_mtime,
            "ini_path_mtime": ini_mtime,
            "glyphs": {
                str(code): [g.x0, g.y0, g.x1, g.y1] for code, g in glyphs.items()
            },
        }
        save_font_cache(stem, meta=meta, atlas_image=sheet)
        return FontAtlas(
            name=name,
            logical=logical,
            height=height,
            width_correction=width_correction,
            glyphs=glyphs,
            sheet=sheet,
            dds_path=dds,
            ini_path=ini,
        )

    def measure(self, atlas: FontAtlas, text: str) -> tuple[int, int]:
        if not text:
            return 0, atlas.height
        width = 0.0
        for ch in text:
            if ch == "\n":
                continue
            code = _char_code(ch)
            if code is None:
                continue
            g = atlas.glyphs.get(code)
            if g is None or g.width <= 0:
                continue
            width += g.width + atlas.width_correction
        return max(1, int(round(width))), atlas.height

    def render_text(self, atlas: FontAtlas, text: str, color: QColor) -> QPixmap:
        # Single-line for now; expand \n to space for preview.
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n") if "\n" in text else [text]
        line_metrics: list[tuple[str, int]] = []
        max_w = 1
        for line in lines:
            w, _ = self.measure(atlas, line)
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
                out.paste(tinted, (int(round(x)), y), tinted)
                x += g.width + atlas.width_correction
            y += atlas.height
        # Soften like engine bilinear UI upscale (up then down keeps layout size).
        w, h = out.size
        soft = out.resize(
            (max(1, w * 2), max(1, h * 2)), Image.Resampling.BILINEAR
        ).resize((w, h), Image.Resampling.BILINEAR)
        return _pil_to_qpixmap(soft)

    def render_fallback(
        self, text: str, *, point_size: int, color: QColor, max_width: int
    ) -> QPixmap:
        """QFont stand-in when engine atlas is missing."""
        point_size = max(8, min(40, int(point_size) or 16))
        try:
            font = ImageFont.truetype("segoeui.ttf", point_size)
        except OSError:
            font = ImageFont.load_default()
        # Measure
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
