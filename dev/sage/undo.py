"""Undo/redo for widget geometry and property-panel edits."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class GeoState:
    path: str
    x: float
    y: float
    width: float
    height: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GeoState:
        return cls(
            path=str(data["path"]),
            x=float(data["x"]),
            y=float(data["y"]),
            width=float(data["width"]),
            height=float(data["height"]),
        )


@dataclass(frozen=True)
class GeoEdit:
    """One or more geometry changes applied / undone together."""

    parts: tuple[tuple[GeoState, GeoState], ...]

    @classmethod
    def single(cls, before: GeoState, after: GeoState) -> GeoEdit:
        return cls(parts=((before, after),))

    @classmethod
    def multi(cls, pairs: list[tuple[GeoState, GeoState]]) -> GeoEdit:
        return cls(parts=tuple(pairs))

    @property
    def before(self) -> GeoState:
        return self.parts[0][0]

    @property
    def after(self) -> GeoState:
        return self.parts[0][1]

    def changed(self) -> bool:
        return any(b != a for b, a in self.parts)

    def action_tone(self) -> str:
        """List chrome tone: ``move`` | ``change`` (geometry)."""
        if len(self.parts) > 1:
            return "move"
        before, after = self.parts[0]
        size_changed = (before.width, before.height) != (after.width, after.height)
        pos_changed = (before.x, before.y) != (after.x, after.y)
        if size_changed and not pos_changed:
            return "change"
        if pos_changed and not size_changed:
            return "move"
        return "change"

    def describe(self) -> str:
        """Short label for the undo history list."""
        if len(self.parts) > 1:
            return f"Move {len(self.parts)} widgets"
        before, after = self.parts[0]
        name = before.path.rsplit("/", 1)[-1] if before.path else "?"
        size_changed = (before.width, before.height) != (after.width, after.height)
        pos_changed = (before.x, before.y) != (after.x, after.y)
        if size_changed and pos_changed:
            kind = "Edit"
        elif size_changed:
            kind = "Resize"
        elif pos_changed:
            kind = "Move"
        else:
            kind = "Edit"
        return f"{kind} {name}"

    def to_dict(self) -> dict[str, Any]:
        if len(self.parts) == 1:
            before, after = self.parts[0]
            return {"before": before.to_dict(), "after": after.to_dict()}
        return {
            "parts": [
                {"before": b.to_dict(), "after": a.to_dict()} for b, a in self.parts
            ]
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GeoEdit:
        if "parts" in data:
            pairs: list[tuple[GeoState, GeoState]] = []
            for raw in data["parts"]:
                pairs.append(
                    (
                        GeoState.from_dict(raw["before"]),
                        GeoState.from_dict(raw["after"]),
                    )
                )
            if not pairs:
                raise ValueError("empty GeoEdit parts")
            return cls(parts=tuple(pairs))
        return cls.single(
            GeoState.from_dict(data["before"]),
            GeoState.from_dict(data["after"]),
        )


@dataclass(frozen=True)
class PropState:
    """Snapshot of props-panel–editable widget / text / texture state."""

    path: str
    # Widget attrs (None = attribute absent from XML).
    stretch: str | None
    always_show_scroll: str | None
    left_ident: str | None
    right_ident: str | None
    top_indent: str | None
    bottom_indent: str | None
    vert_interval: str | None
    # <text> (has_text=False → no child element).
    has_text: bool
    text_content: str
    font: str | None
    align: str | None
    vert_align: str | None
    complex_mode: str | None
    r: str | None
    g: str | None
    b: str | None
    a: str | None
    text_x: str | None
    text_y: str | None
    # <texture>
    has_texture: bool
    texture_name: str
    tex_x: str | None
    tex_y: str | None
    tex_w: str | None
    tex_h: str | None
    tex_r: str | None
    tex_g: str | None
    tex_b: str | None
    tex_a: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PropState:
        def _s(key: str) -> str | None:
            val = data.get(key)
            if val is None:
                return None
            return str(val)

        return cls(
            path=str(data["path"]),
            stretch=_s("stretch"),
            always_show_scroll=_s("always_show_scroll"),
            left_ident=_s("left_ident"),
            right_ident=_s("right_ident"),
            top_indent=_s("top_indent"),
            bottom_indent=_s("bottom_indent"),
            vert_interval=_s("vert_interval"),
            has_text=bool(data.get("has_text")),
            text_content=str(data.get("text_content") or ""),
            font=_s("font"),
            align=_s("align"),
            vert_align=_s("vert_align"),
            complex_mode=_s("complex_mode"),
            r=_s("r"),
            g=_s("g"),
            b=_s("b"),
            a=_s("a"),
            text_x=_s("text_x"),
            text_y=_s("text_y"),
            has_texture=bool(data.get("has_texture")),
            texture_name=str(data.get("texture_name") or ""),
            tex_x=_s("tex_x"),
            tex_y=_s("tex_y"),
            tex_w=_s("tex_w"),
            tex_h=_s("tex_h"),
            tex_r=_s("tex_r"),
            tex_g=_s("tex_g"),
            tex_b=_s("tex_b"),
            tex_a=_s("tex_a"),
        )


@dataclass(frozen=True)
class PropEdit:
    """One property-panel change (text / stretch / scroll / texture)."""

    before: PropState
    after: PropState

    def changed(self) -> bool:
        return self.before != self.after

    def action_tone(self) -> str:
        """List chrome tone: ``add`` | ``remove`` | ``change``."""
        b, a = self.before, self.after
        if b.has_texture != a.has_texture:
            return "add" if a.has_texture else "remove"
        if b.has_text != a.has_text:
            return "add" if a.has_text else "remove"
        return "change"

    def describe(self) -> str:
        name = self.before.path.rsplit("/", 1)[-1] if self.before.path else "?"
        b, a = self.before, self.after
        if b.has_texture != a.has_texture:
            return f"{'Add' if a.has_texture else 'Remove'} texture {name}"
        if b.texture_name != a.texture_name:
            return f"Texture {name}"
        if b.has_text != a.has_text:
            return f"{'Add' if a.has_text else 'Remove'} text {name}"
        if b.text_content != a.text_content:
            return f"Text {name}"
        if b.stretch != a.stretch:
            return f"Stretch {name}"
        if (
            b.font != a.font
            or b.align != a.align
            or b.vert_align != a.vert_align
            or b.complex_mode != a.complex_mode
            or (b.r, b.g, b.b, b.a) != (a.r, a.g, a.b, a.a)
            or (b.text_x, b.text_y) != (a.text_x, a.text_y)
        ):
            return f"Text style {name}"
        if (
            b.always_show_scroll != a.always_show_scroll
            or b.left_ident != a.left_ident
            or b.right_ident != a.right_ident
            or b.top_indent != a.top_indent
            or b.bottom_indent != a.bottom_indent
            or b.vert_interval != a.vert_interval
        ):
            return f"Scroll {name}"
        return f"Props {name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "props",
            "before": self.before.to_dict(),
            "after": self.after.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PropEdit:
        return cls(
            before=PropState.from_dict(data["before"]),
            after=PropState.from_dict(data["after"]),
        )


@dataclass(frozen=True)
class StructureEdit:
    """XML tree structure change (reparent / paste / delete) via full snapshots."""

    before_xml: str
    after_xml: str
    before_meta: dict[str, dict[str, float]]
    after_meta: dict[str, dict[str, float]]
    before_layers: dict[str, bool]
    after_layers: dict[str, bool]
    summary: str
    select_before: tuple[str, ...] = ()
    select_after: tuple[str, ...] = ()

    def changed(self) -> bool:
        return self.before_xml != self.after_xml

    def action_tone(self) -> str:
        return "move"

    def describe(self) -> str:
        return self.summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "structure",
            "before_xml": self.before_xml,
            "after_xml": self.after_xml,
            "before_meta": self.before_meta,
            "after_meta": self.after_meta,
            "before_layers": self.before_layers,
            "after_layers": self.after_layers,
            "summary": self.summary,
            "select_before": list(self.select_before),
            "select_after": list(self.select_after),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StructureEdit:
        def _meta(raw: Any) -> dict[str, dict[str, float]]:
            out: dict[str, dict[str, float]] = {}
            if not isinstance(raw, dict):
                return out
            for k, v in raw.items():
                if isinstance(v, dict):
                    out[str(k)] = {str(ak): float(av) for ak, av in v.items()}
            return out

        def _layers(raw: Any) -> dict[str, bool]:
            if not isinstance(raw, dict):
                return {}
            return {str(k): bool(v) for k, v in raw.items()}

        return cls(
            before_xml=str(data.get("before_xml") or ""),
            after_xml=str(data.get("after_xml") or ""),
            before_meta=_meta(data.get("before_meta")),
            after_meta=_meta(data.get("after_meta")),
            before_layers=_layers(data.get("before_layers")),
            after_layers=_layers(data.get("after_layers")),
            summary=str(data.get("summary") or "Structure edit"),
            select_before=tuple(str(p) for p in (data.get("select_before") or [])),
            select_after=tuple(str(p) for p in (data.get("select_after") or [])),
        )


Edit = GeoEdit | PropEdit | StructureEdit


def edit_from_dict(data: dict[str, Any]) -> Edit:
    kind = data.get("kind")
    if kind == "props":
        return PropEdit.from_dict(data)
    if kind == "structure":
        return StructureEdit.from_dict(data)
    return GeoEdit.from_dict(data)


class UndoStack:
    def __init__(self, *, limit: int = 100) -> None:
        self._limit = limit
        self._undo: list[Edit] = []
        self._redo: list[Edit] = []

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()

    def push(self, edit: Edit) -> None:
        if not edit.changed():
            return
        self._undo.append(edit)
        if len(self._undo) > self._limit:
            self._undo = self._undo[-self._limit :]
        self._redo.clear()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def recent_undo(self, n: int | None = None) -> list[Edit]:
        """Newest-first undo entries (what Ctrl+Z hits first).

        ``n=None`` → full stack; ``n>0`` → at most that many newest entries.
        """
        return self._recent_side(self._undo, n)

    def recent_redo(self, n: int | None = None) -> list[Edit]:
        """Newest-first redo entries (what Ctrl+Y hits first)."""
        return self._recent_side(self._redo, n)

    @staticmethod
    def _recent_side(stack: list[Edit], n: int | None) -> list[Edit]:
        if not stack:
            return []
        if n is None:
            return list(reversed(stack))
        if n <= 0:
            return []
        return list(reversed(stack[-n:]))

    def undo(self) -> Edit | None:
        if not self._undo:
            return None
        edit = self._undo.pop()
        self._redo.append(edit)
        return edit

    def redo(self) -> Edit | None:
        if not self._redo:
            return None
        edit = self._redo.pop()
        self._undo.append(edit)
        return edit

    def serialize(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "undo": [e.to_dict() for e in self._undo],
            "redo": [e.to_dict() for e in self._redo],
        }

    def restore(
        self,
        *,
        undo: list[dict[str, Any]] | None = None,
        redo: list[dict[str, Any]] | None = None,
    ) -> None:
        self.clear()
        for raw in undo or []:
            try:
                self._undo.append(edit_from_dict(raw))
            except (KeyError, TypeError, ValueError):
                continue
        for raw in redo or []:
            try:
                self._redo.append(edit_from_dict(raw))
            except (KeyError, TypeError, ValueError):
                continue
        if len(self._undo) > self._limit:
            self._undo = self._undo[-self._limit :]
        if len(self._redo) > self._limit:
            self._redo = self._redo[-self._limit :]
