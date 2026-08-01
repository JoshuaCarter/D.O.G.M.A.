"""Load / save Stalker UI XML with ElementTree + sidecar .xml.meta."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from .meta import MetaDocument, load_meta_document, meta_path_for, save_meta_document
from .model import LayoutNode, apply_meta_positions, build_tree, collect_meta_positions


class UiXmlDocument:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.tree: ET.ElementTree | None = None
        self.root: ET.Element | None = None
        self.doc: LayoutNode | None = None
        self.dirty = False
        self.meta_dirty = False
        self._synthetic_wrapper = False
        self._meta: dict[str, dict[str, float]] = {}
        self._layers: dict[str, bool] = {}
        self._undo: list[dict[str, Any]] = []
        self._redo: list[dict[str, Any]] = []
        self._selection: str = ""
        self._view: dict[str, float] | None = None
        self._source_text = ""

    @property
    def meta_path(self) -> Path | None:
        if self.path is None:
            return None
        return meta_path_for(self.path)

    @property
    def source_text(self) -> str:
        return self._source_text

    @property
    def layer_states(self) -> dict[str, bool]:
        return dict(self._layers)

    @property
    def selection(self) -> str:
        return self._selection

    @property
    def view_state(self) -> dict[str, float] | None:
        return dict(self._view) if self._view else None

    def set_layer_state(self, path: str, visible: bool) -> None:
        if not path:
            return
        if self._layers.get(path) == bool(visible):
            return
        self._layers[path] = bool(visible)
        self.meta_dirty = True

    def replace_layer_states(self, states: dict[str, bool]) -> None:
        """Replace the full layer map without marking dirty (restore / defaults)."""
        self._layers = {str(k): bool(v) for k, v in states.items() if k}

    def set_undo_snapshot(
        self,
        *,
        undo: list[dict[str, Any]],
        redo: list[dict[str, Any]],
    ) -> None:
        self._undo = list(undo)
        self._redo = list(redo)

    def set_selection(self, path: str) -> None:
        path = path or ""
        if self._selection == path:
            return
        self._selection = path
        # Selection is session chrome (written on save) — not an unsaved "change".

    def set_view_state(self, view: dict[str, float] | None) -> None:
        self._view = dict(view) if view else None

    def capture_session(
        self,
        *,
        undo: list[dict[str, Any]],
        redo: list[dict[str, Any]],
        selection: str = "",
        view: dict[str, float] | None = None,
    ) -> None:
        """Update session fields for the next meta write (does not mark dirty)."""
        self._undo = list(undo)
        self._redo = list(redo)
        self._selection = selection or ""
        self._view = dict(view) if view else None

    def undo_snapshot(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        return list(self._undo), list(self._redo)

    def load(self, path: Path) -> LayoutNode:
        # Stalker UI files often omit XML declaration and use tabs
        text = path.read_text(encoding="utf-8-sig")
        self.path = path
        return self.load_text(text, path=path)

    def load_text(
        self,
        text: str,
        *,
        path: Path | None = None,
        keep_meta: bool = False,
    ) -> LayoutNode:
        """Parse XML text into the layout model (keeps path unless overridden)."""
        if path is not None:
            self.path = path
        preserved = self._snapshot_meta() if keep_meta else None
        try:
            self.root = ET.fromstring(text)
            self._synthetic_wrapper = False
        except ET.ParseError:
            self.root = ET.fromstring(f"<w>\n{text}\n</w>")
            self._synthetic_wrapper = True
        self.tree = ET.ElementTree(self.root)
        self.doc = build_tree(self.root)
        if preserved is not None:
            self._apply_meta_doc(preserved)
        elif self.path is not None:
            self._apply_meta_doc(load_meta_document(self.path))
        else:
            self._apply_meta_doc(MetaDocument())
        apply_meta_positions(self.doc, self._meta)
        self._source_text = text if text.endswith("\n") or text == "" else text + "\n"
        self.dirty = False
        self.meta_dirty = False
        return self.doc

    def reload_model(self) -> LayoutNode:
        if self.root is None or self.path is None:
            raise RuntimeError("No document loaded")
        self.doc = build_tree(self.root)
        self._apply_meta_doc(load_meta_document(self.path))
        apply_meta_positions(self.doc, self._meta)
        return self.doc

    def mark_dirty(self) -> None:
        self.dirty = True

    def mark_meta_dirty(self) -> None:
        self.meta_dirty = True

    def sync_meta_from_doc(self) -> None:
        if self.doc is None:
            return
        self._meta = collect_meta_positions(self.doc)

    def serialize(self) -> str:
        """Current tree as XML text (applies preview geometry first)."""
        if self.root is None or self.doc is None:
            return self._source_text
        for node in self.doc.iter_drawables():
            node.apply_geometry_to_element()
        if sanitize_ui_xml_tree(self.root):
            _resync_nodes_from_elements(self.doc)
        if self._synthetic_wrapper:
            parts: list[str] = []
            for child in list(self.root):
                _indent(child, 0)
                parts.append(ET.tostring(child, encoding="unicode"))
            return "\n".join(parts) + "\n"
        _indent(self.root)
        return ET.tostring(self.root, encoding="unicode") + "\n"

    def save(self, path: Path | None = None) -> Path:
        if self.root is None or self.doc is None:
            raise RuntimeError("No document loaded")
        target = path or self.path
        if target is None:
            raise RuntimeError("No save path")
        text = self.serialize()
        target.write_text(text, encoding="utf-8")
        self._source_text = text
        self.path = target
        self.dirty = False
        self.save_meta(target)
        return target

    def save_raw(self, text: str, path: Path | None = None) -> Path:
        """Write editor text (sanitized), then reparse so the model matches."""
        target = path or self.path
        if target is None:
            raise RuntimeError("No save path")
        text, _removed = sanitize_ui_xml_text(text)
        if not text.endswith("\n") and text != "":
            text = text + "\n"
        target.write_text(text, encoding="utf-8")
        self.path = target
        # Keep layers / undo / view / selection - reparse only refreshes the tree.
        self.load_text(text, path=target, keep_meta=True)
        self.save_meta(target)
        return target

    def save_meta(self, path: Path | None = None) -> Path | None:
        """Always write the sidecar .xml.meta next to the XML (every save)."""
        if self.doc is None:
            return None
        target = path or self.path
        if target is None:
            return None
        self.sync_meta_from_doc()
        out = save_meta_document(
            target,
            MetaDocument(
                elements=self._meta,
                layers=self._layers,
                undo=self._undo,
                redo=self._redo,
                selection=self._selection,
                view=self._view,
            ),
        )
        self.meta_dirty = False
        return out

    def _snapshot_meta(self) -> MetaDocument:
        return MetaDocument(
            elements=dict(self._meta),
            layers=dict(self._layers),
            undo=list(self._undo),
            redo=list(self._redo),
            selection=self._selection,
            view=dict(self._view) if self._view else None,
        )

    def _apply_meta_doc(self, meta: MetaDocument) -> None:
        self._meta = dict(meta.elements)
        self._layers = dict(meta.layers)
        self._undo = list(meta.undo)
        self._redo = list(meta.redo)
        self._selection = meta.selection or ""
        self._view = dict(meta.view) if meta.view else None


def _is_junk_chrome(el: ET.Element) -> bool:
    """True for empty / engine-invalid leaf chrome (InitText font, bare texture, …)."""
    tag = el.tag
    if not isinstance(tag, str):
        return False
    body = (el.text or "").strip()
    if tag == "text":
        # CUIXmlInit::InitText asserts pTmpFont — fontless <text> crashes.
        return not (el.get("font") or "").strip()
    if tag == "texture":
        return not body
    if tag == "list_font":
        return not (el.get("font") or "").strip()
    if tag == "window_name":
        return not body
    return False


def sanitize_ui_xml_tree(root: ET.Element) -> int:
    """Remove invalid empty chrome nodes in-place. Returns how many were dropped."""
    removed = 0
    for parent in list(root.iter()):
        for child in list(parent):
            if _is_junk_chrome(child):
                parent.remove(child)
                removed += 1
    return removed


def sanitize_ui_xml_text(text: str) -> tuple[str, int]:
    """Sanitize UI XML source. Rewrites (pretty-printed) only when something was removed."""
    if not (text or "").strip():
        return text, 0
    synthetic = False
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        try:
            root = ET.fromstring(f"<w>\n{text}\n</w>")
        except ET.ParseError:
            return text, 0
        synthetic = True
    removed = sanitize_ui_xml_tree(root)
    if not removed:
        return text, 0
    if synthetic:
        parts: list[str] = []
        for child in list(root):
            _indent(child, 0)
            parts.append(ET.tostring(child, encoding="unicode"))
        out = "\n".join(parts) + ("\n" if parts else "")
    else:
        _indent(root)
        out = ET.tostring(root, encoding="unicode") + "\n"
    return out, removed


def _resync_nodes_from_elements(doc: LayoutNode) -> None:
    """Refresh LayoutNode text/texture caches after tree sanitize."""

    def walk(node: LayoutNode) -> None:
        node.sync_from_element()
        for child in node.children:
            walk(child)

    walk(doc)


def _indent(elem: ET.Element, level: int = 0) -> None:
    """In-place pretty print with tabs (Anomaly-ish)."""
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
