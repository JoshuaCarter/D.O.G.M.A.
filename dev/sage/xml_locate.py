"""Locate layout / atlas nodes in raw XML source text."""

from __future__ import annotations

import re

from .model import PATH_SEP, SKIP_AS_WIDGET

# Comments, PIs, then start/end/empty tags. Attribute values rarely contain `>`.
_TAG_RE = re.compile(
    r"<!--.*?-->|"
    r"<\?.*?\?>|"
    r"<!\[CDATA\[.*?\]\]>|"
    r"<(/?)\s*([A-Za-z_][\w:.-]*)([^>]*?)(/?)\s*>",
    re.DOTALL,
)


def parse_layout_path(path: str) -> list[tuple[str, int]]:
    """``main_dialog/btn_icon[1]`` → ``[(\"main_dialog\", 0), (\"btn_icon\", 1)]``."""
    path = (path or "").strip().strip("/")
    if not path:
        return []
    out: list[tuple[str, int]] = []
    for part in path.split(PATH_SEP):
        if not part:
            continue
        m = re.fullmatch(r"(.+?)\[(\d+)\]", part)
        if m:
            out.append((m.group(1), int(m.group(2))))
        else:
            out.append((part, 0))
    return out


def find_layout_path_span(text: str, path: str) -> tuple[int, int] | None:
    """Byte/char span of the opening tag for a layout path (same rules as ``build_tree``)."""
    target = parse_layout_path(path)
    if not target or not text:
        return None

    # stack frames: skip subtree, and widget-sibling counts for non-skip frames
    stack_skip: list[bool] = []
    counts_stack: list[dict[str, int]] = []
    cur: list[tuple[str, int]] = []

    for m in _TAG_RE.finditer(text):
        g = m.groups()
        if g[0] is None:
            # comment / PI / CDATA
            continue
        slash_pre, tag, _attrs, slash_self = g
        if slash_pre == "/":
            if not stack_skip:
                continue
            was_skip = stack_skip.pop()
            counts_stack.pop()
            if not was_skip and cur:
                cur.pop()
            continue

        self_closing = slash_self == "/" or (m.group(0).endswith("/>"))
        if not stack_skip:
            # Document root — not part of layout paths.
            stack_skip.append(False)
            counts_stack.append({})
            if self_closing:
                stack_skip.pop()
                counts_stack.pop()
            continue

        parent_skip = stack_skip[-1]
        skip = parent_skip or tag in SKIP_AS_WIDGET
        if skip:
            stack_skip.append(True)
            counts_stack.append({})
            if self_closing:
                stack_skip.pop()
                counts_stack.pop()
            continue

        idx = counts_stack[-1].get(tag, 0)
        counts_stack[-1][tag] = idx + 1
        stack_skip.append(False)
        counts_stack.append({})
        cur.append((tag, idx))
        if cur == target:
            return m.start(), m.end()
        if self_closing:
            stack_skip.pop()
            counts_stack.pop()
            cur.pop()

    return None


def find_atlas_id_span(text: str, atlas_id: str) -> tuple[int, int] | None:
    """Span of the ``<texture … id="…">`` opening tag for an atlas id."""
    atlas_id = (atlas_id or "").strip()
    if not atlas_id or not text:
        return None
    for quote in ('"', "'"):
        needle = f"id={quote}{atlas_id}{quote}"
        pos = 0
        while True:
            hit = text.find(needle, pos)
            if hit < 0:
                break
            # Prefer a <texture …> that contains this id attr.
            lt = text.rfind("<", 0, hit)
            gt = text.find(">", hit)
            if lt >= 0 and gt > hit:
                open_tag = text[lt : gt + 1]
                if re.match(r"<\s*texture\b", open_tag, re.I):
                    return lt, gt + 1
            pos = hit + len(needle)
    return None
