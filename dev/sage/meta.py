"""Sidecar .xml.meta - editor session state next to the XML."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

META_VERSION = 2
# Selectable marker only; tag caption is drawn beside it.
DEFAULT_HANDLE_SIZE = 10.0


@dataclass
class MetaDocument:
    """Everything SAGE persists beside the XML."""

    elements: dict[str, dict[str, float]] = field(default_factory=dict)
    layers: dict[str, bool] = field(default_factory=dict)
    undo: list[dict[str, Any]] = field(default_factory=list)
    redo: list[dict[str, Any]] = field(default_factory=list)
    selection: str = ""
    view: dict[str, float] | None = None


def meta_path_for(xml_path: Path) -> Path:
    return xml_path.with_name(xml_path.name + ".meta")


def load_meta(xml_path: Path) -> dict[str, dict[str, float]]:
    """Return path -> {x,y,width,height} from sidecar meta file."""
    return load_meta_document(xml_path).elements


def load_meta_layers(xml_path: Path) -> dict[str, bool]:
    """Return layer path -> visible from sidecar meta file."""
    return load_meta_document(xml_path).layers


def load_meta_document(xml_path: Path) -> MetaDocument:
    """Load full editor session meta (elements, layers, undo, view, …)."""
    path = meta_path_for(xml_path)
    if not path.is_file():
        return MetaDocument()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return MetaDocument()
    if not isinstance(data, dict):
        return MetaDocument()
    return _parse_meta_dict(data)


def _parse_meta_dict(data: dict[str, Any]) -> MetaDocument:
    elements: dict[str, dict[str, float]] = {}
    raw_elements = data.get("elements")
    if isinstance(raw_elements, dict):
        for key, val in raw_elements.items():
            if not isinstance(key, str) or not isinstance(val, dict):
                continue
            # Legacy colon paths → slash (XPath-style)
            norm_key = key.replace(":", "/")
            try:
                elements[norm_key] = {
                    "x": float(val.get("x", 0)),
                    "y": float(val.get("y", 0)),
                    "width": float(val.get("width", DEFAULT_HANDLE_SIZE)),
                    "height": float(val.get("height", DEFAULT_HANDLE_SIZE)),
                }
            except (TypeError, ValueError):
                continue

    layers: dict[str, bool] = {}
    raw_layers = data.get("layers")
    if isinstance(raw_layers, dict):
        for key, val in raw_layers.items():
            if not isinstance(key, str):
                continue
            layers[key.replace(":", "/")] = bool(val)

    undo = _parse_edit_list(data.get("undo"))
    redo = _parse_edit_list(data.get("redo"))

    selection = data.get("selection")
    if not isinstance(selection, str):
        selection = ""
    else:
        selection = selection.replace(":", "/")

    view = _parse_view(data.get("view"))
    return MetaDocument(
        elements=elements,
        layers=layers,
        undo=undo,
        redo=redo,
        selection=selection,
        view=view,
    )


def _parse_edit_list(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        edit = _parse_edit(item)
        if edit is not None:
            out.append(edit)
    return out


def _parse_edit(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    # Multi-widget edit: {"parts": [{"before":…, "after":…}, …]}
    if "parts" in raw:
        if not isinstance(raw["parts"], list):
            return None
        parts: list[dict[str, Any]] = []
        for item in raw["parts"]:
            if not isinstance(item, dict):
                continue
            before = _parse_geo(item.get("before"))
            after = _parse_geo(item.get("after"))
            if before is None or after is None:
                continue
            parts.append({"before": before, "after": after})
        if not parts:
            return None
        return {"parts": parts}
    before = _parse_geo(raw.get("before"))
    after = _parse_geo(raw.get("after"))
    if before is None or after is None:
        return None
    return {"before": before, "after": after}


def _parse_geo(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    path = raw.get("path")
    if not isinstance(path, str) or not path:
        return None
    try:
        return {
            "path": path.replace(":", "/"),
            "x": float(raw.get("x", 0)),
            "y": float(raw.get("y", 0)),
            "width": float(raw.get("width", DEFAULT_HANDLE_SIZE)),
            "height": float(raw.get("height", DEFAULT_HANDLE_SIZE)),
        }
    except (TypeError, ValueError):
        return None


def _parse_view(raw: object) -> dict[str, float] | None:
    if not isinstance(raw, dict):
        return None
    try:
        scale = float(raw.get("scale", 0))
        cx = float(raw.get("cx", 0))
        cy = float(raw.get("cy", 0))
    except (TypeError, ValueError):
        return None
    if scale <= 0:
        return None
    return {"scale": scale, "cx": cx, "cy": cy}


def save_meta(
    xml_path: Path,
    elements: dict[str, dict[str, float]],
    *,
    layers: dict[str, bool] | None = None,
    undo: list[dict[str, Any]] | None = None,
    redo: list[dict[str, Any]] | None = None,
    selection: str = "",
    view: dict[str, float] | None = None,
) -> Path:
    path = meta_path_for(xml_path)
    payload: dict[str, Any] = {
        "version": META_VERSION,
        "elements": {
            key: {
                "x": _num(geo["x"]),
                "y": _num(geo["y"]),
                "width": _num(geo["width"]),
                "height": _num(geo["height"]),
            }
            for key, geo in sorted(elements.items())
        },
    }
    if layers is not None:
        payload["layers"] = {key: bool(val) for key, val in sorted(layers.items())}
    if undo is not None:
        payload["undo"] = [_serialize_edit(e) for e in undo]
    if redo is not None:
        payload["redo"] = [_serialize_edit(e) for e in redo]
    if selection:
        payload["selection"] = selection
    if view is not None:
        payload["view"] = {
            "scale": _num(float(view["scale"])),
            "cx": _num(float(view["cx"])),
            "cy": _num(float(view["cy"])),
        }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def save_meta_document(xml_path: Path, meta: MetaDocument) -> Path:
    return save_meta(
        xml_path,
        meta.elements,
        layers=meta.layers,
        undo=meta.undo,
        redo=meta.redo,
        selection=meta.selection,
        view=meta.view,
    )


def _serialize_edit(edit: dict[str, Any]) -> dict[str, Any]:
    # Multi-widget GeoEdit.to_dict() → {"parts": [...]}
    if "parts" in edit and isinstance(edit["parts"], list):
        parts_out: list[dict[str, Any]] = []
        for item in edit["parts"]:
            if not isinstance(item, dict):
                continue
            parts_out.append(
                {
                    "before": _serialize_geo(item["before"]),
                    "after": _serialize_geo(item["after"]),
                }
            )
        if not parts_out:
            raise ValueError("empty GeoEdit parts")
        return {"parts": parts_out}
    return {
        "before": _serialize_geo(edit["before"]),
        "after": _serialize_geo(edit["after"]),
    }


def _serialize_geo(geo: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": str(geo["path"]),
        "x": _num(float(geo["x"])),
        "y": _num(float(geo["y"])),
        "width": _num(float(geo["width"])),
        "height": _num(float(geo["height"])),
    }


def _num(v: float) -> int | float:
    if abs(v - round(v)) < 1e-6:
        return int(round(v))
    return float(f"{v:g}")
