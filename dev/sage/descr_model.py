"""Load / save Anomaly ``textures_descr`` atlas XML (not UI layout XML)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET


def looks_like_textures_descr(
    *,
    path: Path | None = None,
    text: str | None = None,
    root: ET.Element | None = None,
) -> bool:
    """True if path/content is a textures_descr atlas file."""
    if path is not None:
        parts = [p.lower() for p in path.parts]
        if "textures_descr" in parts:
            return True
    el = root
    if el is None and text is not None:
        try:
            el = ET.fromstring(text)
        except ET.ParseError:
            return False
    if el is None:
        return False
    files = list(el.iter("file"))
    if not files:
        return False
    for file_el in files:
        if not (file_el.get("name") or "").strip():
            continue
        for tex in file_el.findall("texture"):
            if (tex.get("id") or "").strip():
                return True
    return False


def _fmt_uv(v: float) -> str:
    """textures_descr UV is pixel coords — always whole numbers."""
    return str(int(round(float(v))))


def _uv_i(v: float) -> float:
    """Store UV as integer-valued float for QGraphics / math APIs."""
    return float(int(round(float(v))))


def _indent(elem: ET.Element, level: int = 0) -> None:
    indent_str = "\n" + ("\t" * level)
    children = list(elem)
    if children:
        if not elem.text or not elem.text.strip():
            elem.text = indent_str + "\t"
        for child in children:
            _indent(child, level + 1)
        last = children[-1]
        if not last.tail or not last.tail.strip():
            last.tail = indent_str
    if level and (not elem.tail or not elem.tail.strip()):
        elem.tail = indent_str
    elif level == 0 and (not elem.tail or not elem.tail.strip()):
        elem.tail = "\n"


@dataclass
class DescrRegion:
    """One ``<texture id …>`` crop on a sheet."""

    element: ET.Element
    atlas_id: str
    x: float = 0.0
    y: float = 0.0
    width: float = 1.0
    height: float = 1.0

    def sync_from_element(self) -> None:
        self.atlas_id = (self.element.get("id") or "").strip()
        try:
            self.x = _uv_i(float(self.element.get("x", "0") or 0))
            self.y = _uv_i(float(self.element.get("y", "0") or 0))
            self.width = _uv_i(float(self.element.get("width", "0") or 0))
            self.height = _uv_i(float(self.element.get("height", "0") or 0))
        except ValueError:
            pass
        self.width = max(1.0, self.width)
        self.height = max(1.0, self.height)

    def apply_to_element(self) -> None:
        self.element.set("id", self.atlas_id)
        self.element.set("x", _fmt_uv(self.x))
        self.element.set("y", _fmt_uv(self.y))
        self.element.set("width", _fmt_uv(self.width))
        self.element.set("height", _fmt_uv(self.height))

    def set_geometry(
        self,
        *,
        x: float | None = None,
        y: float | None = None,
        width: float | None = None,
        height: float | None = None,
    ) -> None:
        if x is not None:
            self.x = _uv_i(x)
        if y is not None:
            self.y = _uv_i(y)
        if width is not None:
            self.width = max(1.0, _uv_i(width))
        if height is not None:
            self.height = max(1.0, _uv_i(height))
        self.apply_to_element()

    def set_id(self, atlas_id: str) -> bool:
        atlas_id = (atlas_id or "").strip()
        if not atlas_id or atlas_id == self.atlas_id:
            return False
        self.atlas_id = atlas_id
        self.element.set("id", atlas_id)
        return True


@dataclass
class DescrSheet:
    """One ``<file name="ui\\…">`` sheet and its regions."""

    element: ET.Element
    file_name: str
    regions: list[DescrRegion] = field(default_factory=list)

    def sync_from_element(self) -> None:
        self.file_name = (self.element.get("name") or "").strip()
        self.regions = []
        for tex in self.element.findall("texture"):
            reg = DescrRegion(element=tex, atlas_id="")
            reg.sync_from_element()
            if reg.atlas_id:
                self.regions.append(reg)

    def find_region(self, atlas_id: str) -> DescrRegion | None:
        for reg in self.regions:
            if reg.atlas_id == atlas_id:
                return reg
        return None

    def all_ids(self) -> set[str]:
        return {r.atlas_id for r in self.regions if r.atlas_id}


class DescrDocument:
    """Writable textures_descr document (no .xml.meta)."""

    def __init__(self) -> None:
        self.path: Path | None = None
        self.root: ET.Element | None = None
        self.tree: ET.ElementTree | None = None
        self.sheets: list[DescrSheet] = []
        self.dirty = False
        self._source_text = ""

    @property
    def source_text(self) -> str:
        return self._source_text

    def clear(self) -> None:
        self.path = None
        self.root = None
        self.tree = None
        self.sheets = []
        self.dirty = False
        self._source_text = ""

    def mark_dirty(self) -> None:
        self.dirty = True

    def load(self, path: Path) -> list[DescrSheet]:
        text = path.read_text(encoding="utf-8-sig")
        self.path = path
        return self.load_text(text, path=path)

    def load_text(
        self,
        text: str,
        *,
        path: Path | None = None,
    ) -> list[DescrSheet]:
        if path is not None:
            self.path = path
        self.root = ET.fromstring(text)
        self.tree = ET.ElementTree(self.root)
        self.sheets = []
        for file_el in self.root.findall("file"):
            # Also accept nested file under wrappers
            sheet = DescrSheet(element=file_el, file_name="")
            sheet.sync_from_element()
            if sheet.file_name:
                self.sheets.append(sheet)
        if not self.sheets:
            for file_el in self.root.iter("file"):
                sheet = DescrSheet(element=file_el, file_name="")
                sheet.sync_from_element()
                if sheet.file_name and sheet not in self.sheets:
                    self.sheets.append(sheet)
        self._source_text = text if text.endswith("\n") or text == "" else text + "\n"
        self.dirty = False
        return self.sheets

    def find_region(self, atlas_id: str) -> DescrRegion | None:
        for sheet in self.sheets:
            hit = sheet.find_region(atlas_id)
            if hit is not None:
                return hit
        return None

    def id_exists(self, atlas_id: str, *, except_region: DescrRegion | None = None) -> bool:
        for sheet in self.sheets:
            for reg in sheet.regions:
                if reg is except_region:
                    continue
                if reg.atlas_id == atlas_id:
                    return True
        return False

    def add_region(
        self,
        sheet: DescrSheet,
        *,
        atlas_id: str,
        x: float,
        y: float,
        width: float,
        height: float,
    ) -> DescrRegion:
        el = ET.Element("texture")
        sheet.element.append(el)
        reg = DescrRegion(element=el, atlas_id=atlas_id)
        reg.set_geometry(x=x, y=y, width=width, height=height)
        reg.set_id(atlas_id)
        sheet.regions.append(reg)
        self.dirty = True
        return reg

    def remove_region(self, sheet: DescrSheet, region: DescrRegion) -> bool:
        if region not in sheet.regions:
            return False
        sheet.regions.remove(region)
        try:
            sheet.element.remove(region.element)
        except ValueError:
            pass
        self.dirty = True
        return True

    def duplicate_region(self, sheet: DescrSheet, region: DescrRegion) -> DescrRegion:
        base = region.atlas_id or "texture"
        n = 2
        new_id = f"{base}_copy"
        while self.id_exists(new_id):
            new_id = f"{base}_copy{n}"
            n += 1
        return self.add_region(
            sheet,
            atlas_id=new_id,
            x=region.x + 8,
            y=region.y + 8,
            width=region.width,
            height=region.height,
        )

    def apply_all_geometry(self) -> None:
        for sheet in self.sheets:
            for reg in sheet.regions:
                reg.apply_to_element()

    def serialize(self) -> str:
        if self.root is None:
            return self._source_text
        self.apply_all_geometry()
        _indent(self.root)
        return ET.tostring(self.root, encoding="unicode") + "\n"

    def save(self, path: Path | None = None) -> Path:
        if self.root is None:
            raise RuntimeError("No document loaded")
        target = path or self.path
        if target is None:
            raise RuntimeError("No save path")
        text = self.serialize()
        target.write_text(text, encoding="utf-8")
        self._source_text = text
        self.path = target
        self.dirty = False
        return target

    def save_raw(self, text: str, path: Path | None = None) -> Path:
        target = path or self.path
        if target is None:
            raise RuntimeError("No save path")
        if not text.endswith("\n") and text != "":
            text = text + "\n"
        target.write_text(text, encoding="utf-8")
        self.path = target
        self.load_text(text, path=target)
        return target
