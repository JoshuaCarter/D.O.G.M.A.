"""Simple undo/redo for widget geometry edits."""

from __future__ import annotations

from dataclasses import dataclass
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


class UndoStack:
    def __init__(self, *, limit: int = 100) -> None:
        self._limit = limit
        self._undo: list[GeoEdit] = []
        self._redo: list[GeoEdit] = []

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()

    def push(self, edit: GeoEdit) -> None:
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

    def recent_undo(self, n: int = 10) -> list[GeoEdit]:
        """Newest-first slice of the undo stack (what Ctrl+Z will hit first)."""
        if n <= 0:
            return []
        return list(reversed(self._undo[-n:]))

    def undo(self) -> GeoEdit | None:
        if not self._undo:
            return None
        edit = self._undo.pop()
        self._redo.append(edit)
        return edit

    def redo(self) -> GeoEdit | None:
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
                self._undo.append(GeoEdit.from_dict(raw))
            except (KeyError, TypeError, ValueError):
                continue
        for raw in redo or []:
            try:
                self._redo.append(GeoEdit.from_dict(raw))
            except (KeyError, TypeError, ValueError):
                continue
        if len(self._undo) > self._limit:
            self._undo = self._undo[-self._limit :]
        if len(self._redo) > self._limit:
            self._redo = self._redo[-self._limit :]
