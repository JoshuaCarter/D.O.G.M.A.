"""Load / save Stalker UI XML with ElementTree + sidecar .xml.meta."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from .meta import MetaDocument, load_meta_document, meta_path_for, save_meta_document
from .model import (
    PATH_SEP,
    SKIP_AS_WIDGET,
    LayoutNode,
    apply_meta_positions,
    build_tree,
    collect_meta_positions,
)

# XML Name / Stalker UI tag (no namespaces, no spaces).
_TAG_RE = re.compile(r"^[A-Za-z_][\w.-]*$")


def _sync_drawable_geometry(node: LayoutNode) -> None:
    """Push preview geometry for ``node`` and drawable descendants into Elements."""
    for n in node.iter_all():
        if n.is_drawable:
            n.apply_geometry_to_element()


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

    def capture_session(
        self,
        *,
        undo: list[dict[str, Any]],
        redo: list[dict[str, Any]],
    ) -> None:
        """Update undo/redo for the next meta write (does not mark dirty)."""
        self._undo = list(undo)
        self._redo = list(redo)

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

    def refresh_model(self) -> LayoutNode:
        """Rebuild LayoutNode tree from the live ElementTree; keep meta/layers."""
        if self.root is None:
            raise RuntimeError("No document loaded")
        self.doc = build_tree(self.root)
        apply_meta_positions(self.doc, self._meta)
        return self.doc

    def subtree_xml(self, node: LayoutNode) -> str:
        """Serialize ``node``'s Element subtree (geometry synced from preview)."""
        _sync_drawable_geometry(node)
        return ET.tostring(node.element, encoding="unicode")

    def paste_sibling_after(
        self, target: LayoutNode, xml: str
    ) -> LayoutNode | None:
        """Insert parsed XML as the next sibling of ``target``; return new node."""
        if self.root is None or self.doc is None:
            return None
        parent = target.parent
        if parent is None or target.element is self.root:
            return None
        try:
            clone = ET.fromstring(xml)
        except ET.ParseError:
            return None
        if not isinstance(clone.tag, str) or not clone.tag:
            return None
        parent_el = parent.element
        try:
            idx = list(parent_el).index(target.element)
        except ValueError:
            return None
        old_by_el = {id(n.element): n.path for n in self.doc.iter_all() if n.path}
        parent_el.insert(idx + 1, clone)
        self.refresh_model()
        assert self.doc is not None
        # Sibling [n] indices may shift — keep meta/layers on the right nodes.
        self._remap_meta_paths(old_by_el)
        self.mark_dirty()
        return self.doc.find_by_element(clone)

    def remove_subtree(self, node: LayoutNode) -> bool:
        """Remove ``node``'s Element from its parent and refresh the model."""
        if self.root is None or self.doc is None:
            return False
        parent = node.parent
        if parent is None or not node.path or node.element is self.root:
            return False
        old_by_el = {id(n.element): n.path for n in self.doc.iter_all() if n.path}
        try:
            parent.element.remove(node.element)
        except ValueError:
            return False
        # Drop meta/layer entries for the subtree (path and descendants).
        prefix = node.path + PATH_SEP
        for store in (self._meta, self._layers):
            stale = [k for k in store if k == node.path or k.startswith(prefix)]
            for k in stale:
                del store[k]
                self.meta_dirty = True
        self.refresh_model()
        assert self.doc is not None
        # Sibling [n] indices may shift — keep meta/layers on the right nodes.
        self._remap_meta_paths(old_by_el)
        self.mark_dirty()
        return True

    def rename_tag(self, node: LayoutNode, new_tag: str) -> str | None:
        """Rename ``node``'s XML tag; remap meta/layer paths. Returns new path."""
        if self.root is None or self.doc is None:
            return None
        if node.element is self.root or not node.path:
            return None
        new_tag = (new_tag or "").strip()
        if not new_tag or new_tag == node.tag:
            return node.path
        if not _TAG_RE.fullmatch(new_tag) or new_tag in SKIP_AS_WIDGET:
            return None
        el = node.element
        old_by_el = {id(n.element): n.path for n in self.doc.iter_all() if n.path}
        el.tag = new_tag
        self.refresh_model()
        assert self.doc is not None
        self._remap_meta_paths(old_by_el)
        self.mark_dirty()
        found = self.doc.find_by_element(el)
        return found.path if found is not None else None

    def move_nodes(
        self,
        nodes: list[LayoutNode],
        target: LayoutNode,
        place: str,
    ) -> list[str] | None:
        """Move selection roots relative to ``target``.

        ``place``: ``"on"`` (last children), ``"before"`` / ``"after"`` (siblings).
        Returns new paths of moved roots, or None if the move is illegal.
        """
        if self.root is None or self.doc is None:
            return None
        if target.element is None or place not in ("on", "before", "after"):
            return None
        # Only move roots among the selection (skip nodes under another selected).
        path_set = {n.path for n in nodes if n.path}
        roots: list[LayoutNode] = []
        for n in nodes:
            if not n.path or n.element is self.root:
                continue
            if n.parent is None:
                continue
            p = n.parent
            under = False
            while p is not None:
                if p.path in path_set:
                    under = True
                    break
                p = p.parent
            if not under:
                roots.append(n)
        if not roots:
            return None
        # Reject drop onto / beside a dragged node or any of its descendants.
        for root in roots:
            if target is root:
                return None
            if target.path and root.path:
                if (
                    target.path == root.path
                    or target.path.startswith(root.path + PATH_SEP)
                ):
                    return None
        if place == "on":
            dest_parent_el = target.element
        else:
            parent = target.parent
            if parent is None or target.element is self.root:
                return None
            dest_parent_el = parent.element
        target_el = target.element
        old_by_el = {id(n.element): n.path for n in self.doc.iter_all() if n.path}
        moved_els: list[ET.Element] = []
        for root in roots:
            parent = root.parent
            if parent is None:
                continue
            try:
                parent.element.remove(root.element)
            except ValueError:
                continue
            moved_els.append(root.element)
        if not moved_els:
            return None
        # Insert after removals so same-parent reorder indices stay correct.
        if place == "on":
            for el in moved_els:
                dest_parent_el.append(el)
        else:
            children = list(dest_parent_el)
            try:
                at = children.index(target_el)
            except ValueError:
                return None
            if place == "after":
                at += 1
            for i, el in enumerate(moved_els):
                dest_parent_el.insert(at + i, el)
        self.refresh_model()
        assert self.doc is not None
        self._remap_meta_paths(old_by_el)
        self.mark_dirty()
        out: list[str] = []
        for el in moved_els:
            found = self.doc.find_by_element(el)
            if found is not None and found.path:
                out.append(found.path)
        return out

    def _remap_meta_paths(self, old_by_el: dict[int, str]) -> None:
        """Rewrite meta/layer keys after structure edits (element identity stable)."""
        if self.doc is None:
            return
        path_map: dict[str, str] = {}
        for n in self.doc.iter_all():
            if not n.path:
                continue
            old = old_by_el.get(id(n.element))
            if old and old != n.path:
                path_map[old] = n.path
        if not path_map:
            return
        self._meta = {path_map.get(k, k): v for k, v in self._meta.items()}
        self._layers = {path_map.get(k, k): v for k, v in self._layers.items()}
        self.meta_dirty = True
        apply_meta_positions(self.doc, self._meta)

    def mark_dirty(self) -> None:
        self.dirty = True

    def mark_meta_dirty(self) -> None:
        self.meta_dirty = True

    def sync_meta_from_doc(self) -> None:
        if self.doc is None:
            return
        self._meta = collect_meta_positions(self.doc)

    def structure_snapshot(
        self,
    ) -> tuple[str, dict[str, dict[str, float]], dict[str, bool]]:
        """XML + meta/layers for undoable structure edits (no sanitize/mutation)."""
        self.sync_meta_from_doc()
        meta = {k: dict(v) for k, v in self._meta.items()}
        layers = dict(self._layers)
        return self.serialize(sanitize=False), meta, layers

    def restore_structure_snapshot(
        self,
        xml: str,
        meta: dict[str, dict[str, float]],
        layers: dict[str, bool],
    ) -> LayoutNode:
        """Replace the live tree from a structure snapshot; mark dirty."""
        try:
            self.root = ET.fromstring(xml)
            self._synthetic_wrapper = False
        except ET.ParseError:
            self.root = ET.fromstring(f"<w>\n{xml}\n</w>")
            self._synthetic_wrapper = True
        self.tree = ET.ElementTree(self.root)
        self.doc = build_tree(self.root)
        self._meta = {str(k): dict(v) for k, v in meta.items()}
        self._layers = {str(k): bool(v) for k, v in layers.items()}
        apply_meta_positions(self.doc, self._meta)
        text = xml if xml.endswith("\n") or xml == "" else xml + "\n"
        self._source_text = text
        self.dirty = True
        self.meta_dirty = True
        return self.doc

    def serialize(self, *, sanitize: bool = True) -> str:
        """Current tree as XML text (applies preview geometry first).

        ``sanitize=False`` for snapshots: no junk-chrome removal, no model resync.
        """
        if self.root is None or self.doc is None:
            return self._source_text
        for node in self.doc.iter_drawables():
            node.apply_geometry_to_element()
        if sanitize and sanitize_ui_xml_tree(self.root):
            _resync_nodes_from_elements(self.doc)
            # sync_from_element cleared from_meta — restore handle geometry.
            apply_meta_positions(self.doc, self._meta)
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
        # Keep layers / undo — reparse only refreshes the tree.
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
        )

    def _apply_meta_doc(self, meta: MetaDocument) -> None:
        self._meta = dict(meta.elements)
        self._layers = dict(meta.layers)
        self._undo = list(meta.undo)
        self._redo = list(meta.redo)


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
