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
    before: GeoState
    after: GeoState

    def changed(self) -> bool:
        return self.before != self.after

    def to_dict(self) -> dict[str, Any]:
        return {"before": self.before.to_dict(), "after": self.after.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GeoEdit:
        return cls(
            before=GeoState.from_dict(data["before"]),
            after=GeoState.from_dict(data["after"]),
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
