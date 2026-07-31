"""Layout tree model: parent-relative Stalker UI widgets."""

from __future__ import annotations

from dataclasses import dataclass, field
from xml.etree.ElementTree import Element

GEO_ATTRS = ("x", "y", "width", "height")
# XPath-style path between tags (also keys in .xml.meta)
PATH_SEP = "/"
# Child tags that are never drawable widgets themselves
SKIP_AS_WIDGET = frozenset(
    {
        "texture",
        "text",
        "list_font",
        "text_color",
        "e",
        "d",
        "window_name",
        "progress",
        "level_map",
        "on",
        "off",
    }
)


def _f(el: Element, name: str, default: float = 0.0) -> float:
    raw = el.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def has_geometry(el: Element) -> bool:
    return any(el.get(a) is not None for a in GEO_ATTRS)


def default_meta_xy(node: LayoutNode) -> tuple[float, float]:
    """Guess a runtime placement origin when .xml.meta has no entry.

    Engine ``InitWindow`` auto-attaches ``auto_static`` children at their XML
    x/y (missing attrs → 0 via ReadAttribFlt). Script-built containers with no
    XML geometry (``options``, ``popup_*``, …) need a SAGE meta handle instead.

    Prefer a sibling ``scroll_<tag>`` / ``templ_<tag>`` that scripts parent into
    (e.g. ``options`` → ``scroll_options`` at the scroll view's x/y).
    """
    parent = node.parent
    if parent is None:
        return 0.0, 0.0
    for want in (f"scroll_{node.tag}", f"templ_{node.tag}"):
        for sib in parent.children:
            if sib is node or sib.tag != want:
                continue
            if has_geometry(sib.element):
                return sib.x, sib.y
    return 0.0, 0.0


@dataclass
class TextureRef:
    """Resolved or unresolved texture reference from a <texture> child."""

    name: str = ""
    # UV in DDS pixels (0 means "use full / unknown" when all zero and no attrs)
    uv_x: float = 0.0
    uv_y: float = 0.0
    uv_w: float = 0.0
    uv_h: float = 0.0
    has_uv: bool = False
    tint_r: int | None = None
    tint_g: int | None = None
    tint_b: int | None = None
    tint_a: int | None = None

    @property
    def is_path(self) -> bool:
        return "\\" in self.name or "/" in self.name


@dataclass
class TextRef:
    content: str = ""
    font: str = ""
    align: str = ""
    vert_align: str = ""
    r: int | None = None
    g: int | None = None
    b: int | None = None
    a: int | None = None


@dataclass
class LayoutNode:
    element: Element
    tag: str
    path: str  # slash path from root child, e.g. main_dialog/btn_back
    parent: LayoutNode | None = None
    children: list[LayoutNode] = field(default_factory=list)
    # Local geometry (parent-relative)
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    stretch: bool = False
    is_drawable: bool = False
    # Geometry comes from sidecar .xml.meta (not written into XML)
    from_meta: bool = False
    visible: bool = True
    texture: TextureRef | None = None
    text: TextRef | None = None
    # Absolute canvas position (updated by recompute)
    abs_x: float = 0.0
    abs_y: float = 0.0

    def recompute_absolute(self, ox: float = 0.0, oy: float = 0.0) -> None:
        if self.is_drawable:
            self.abs_x = ox + self.x
            self.abs_y = oy + self.y
            # Meta handles are runtime placement origins (script-parented roots with
            # no XML x/y). Children preview relative to them; XML is unchanged.
            next_ox, next_oy = self.abs_x, self.abs_y
        else:
            self.abs_x = ox
            self.abs_y = oy
            next_ox, next_oy = ox, oy

        # Script Init* parent often differs from XML nesting (Anomaly new-game dialog):
        # - main_dialog siblings → parented to frame_back
        # - popup_* content (not frame/frame_black) → parented to popup frame
        anchor_back = next((c for c in self.children if c.tag == "frame_back"), None)
        anchor_frame = next((c for c in self.children if c.tag == "frame"), None)

        for child in self.children:
            if anchor_back is not None and child is not anchor_back:
                child.recompute_absolute(next_ox + anchor_back.x, next_oy + anchor_back.y)
            elif (
                anchor_frame is not None
                and child is not anchor_frame
                and child.tag not in ("frame", "frame_black")
            ):
                child.recompute_absolute(next_ox + anchor_frame.x, next_oy + anchor_frame.y)
            else:
                child.recompute_absolute(next_ox, next_oy)

    def coord_origin(self) -> tuple[float, float]:
        """Canvas origin that this node's local x/y are relative to."""
        parent = self.parent
        if parent is None:
            return 0.0, 0.0
        # Drawable parents (including meta roots) contribute their absolute position.
        if parent.is_drawable:
            base_x, base_y = parent.abs_x, parent.abs_y
        else:
            base_x, base_y = parent.coord_origin()
        anchor_back = next((c for c in parent.children if c.tag == "frame_back"), None)
        if anchor_back is not None and self is not anchor_back:
            return base_x + anchor_back.x, base_y + anchor_back.y
        anchor_frame = next((c for c in parent.children if c.tag == "frame"), None)
        if (
            anchor_frame is not None
            and self is not anchor_frame
            and self.tag not in ("frame", "frame_black")
        ):
            return base_x + anchor_frame.x, base_y + anchor_frame.y
        return base_x, base_y

    def sync_from_element(self) -> None:
        self.x = _f(self.element, "x")
        self.y = _f(self.element, "y")
        self.width = _f(self.element, "width")
        self.height = _f(self.element, "height")
        self.stretch = self.element.get("stretch", "0") in ("1", "true", "True")
        self.is_drawable = has_geometry(self.element)
        self.from_meta = False
        self.texture = _read_texture(self.element)
        self.text = _read_text(self.element)

    def apply_geometry_to_element(self) -> None:
        if not self.is_drawable or self.from_meta:
            return
        self.element.set("x", _fmt(self.x))
        self.element.set("y", _fmt(self.y))
        self.element.set("width", _fmt(self.width))
        self.element.set("height", _fmt(self.height))
        if self.stretch:
            self.element.set("stretch", "1")
        elif self.element.get("stretch") is not None:
            self.element.set("stretch", "0")

    def set_text_content(self, content: str) -> bool:
        """Set the <text> element body. Creates <text> when needed. Returns True if changed."""
        if self.from_meta:
            return False
        t = self.element.find("text")
        if t is None:
            if content == "":
                return False
            t = Element("text")
            # Keep texture-first order when present
            tex = self.element.find("texture")
            if tex is not None:
                idx = list(self.element).index(tex) + 1
                self.element.insert(idx, t)
            else:
                self.element.append(t)
        old = t.text or ""
        if old == content:
            return False
        t.text = content
        self.text = _read_text(self.element)
        return True

    def set_geometry(
        self,
        *,
        x: float | None = None,
        y: float | None = None,
        width: float | None = None,
        height: float | None = None,
    ) -> None:
        if x is not None:
            self.x = x
        if y is not None:
            self.y = y
        if width is not None:
            self.width = max(1.0, width)
        if height is not None:
            self.height = max(1.0, height)
        self.is_drawable = True
        self.apply_geometry_to_element()

    def apply_meta_geometry(
        self,
        *,
        x: float,
        y: float,
        width: float,
        height: float,
    ) -> None:
        self.x = x
        self.y = y
        self.width = max(1.0, width)
        self.height = max(1.0, height)
        self.is_drawable = True
        self.from_meta = True

    def hierarchy_depth(self) -> int:
        """Distance from the document root (root = 0)."""
        depth = 0
        node = self.parent
        while node is not None:
            depth += 1
            node = node.parent
        return depth

    def iter_drawables(self) -> list[LayoutNode]:
        out: list[LayoutNode] = []
        if self.is_drawable:
            out.append(self)
        for child in self.children:
            out.extend(child.iter_drawables())
        return out

    def iter_all(self) -> list[LayoutNode]:
        out = [self]
        for child in self.children:
            out.extend(child.iter_all())
        return out

    def find_by_path(self, path: str) -> LayoutNode | None:
        if self.path == path:
            return self
        for child in self.children:
            found = child.find_by_path(path)
            if found is not None:
                return found
        return None


def _fmt(v: float) -> str:
    if abs(v - round(v)) < 1e-6:
        return str(int(round(v)))
    return f"{v:g}"


def _read_texture(el: Element) -> TextureRef | None:
    tex = el.find("texture")
    if tex is None:
        return None
    name = (tex.text or "").strip()
    ref = TextureRef(name=name)
    if any(tex.get(a) is not None for a in GEO_ATTRS):
        ref.has_uv = True
        ref.uv_x = _f(tex, "x")
        ref.uv_y = _f(tex, "y")
        ref.uv_w = _f(tex, "width")
        ref.uv_h = _f(tex, "height")
    for ch, attr in (("r", "tint_r"), ("g", "tint_g"), ("b", "tint_b"), ("a", "tint_a")):
        raw = tex.get(ch)
        if raw is not None:
            try:
                setattr(ref, attr, int(raw))
            except ValueError:
                pass
    return ref


def _read_text(el: Element) -> TextRef | None:
    t = el.find("text")
    if t is None:
        return None
    ref = TextRef(content=(t.text or "").strip(), font=t.get("font", ""), align=t.get("align", ""), vert_align=t.get("vert_align", ""))
    for ch, attr in (("r", "r"), ("g", "g"), ("b", "b"), ("a", "a")):
        raw = t.get(ch)
        if raw is not None:
            try:
                setattr(ref, attr, int(raw))
            except ValueError:
                pass
    return ref


def build_tree(root: Element) -> LayoutNode:
    """Build a LayoutNode forest under a synthetic root for the document element."""

    def walk(el: Element, parent: LayoutNode | None, path: str) -> LayoutNode:
        node = LayoutNode(element=el, tag=el.tag, path=path, parent=parent)
        node.sync_from_element()
        for child_el in list(el):
            if not isinstance(child_el.tag, str):
                continue
            # texture/text UV/color attrs are not widget layout
            if child_el.tag in SKIP_AS_WIDGET:
                continue
            child_path = child_el.tag if not path else f"{path}{PATH_SEP}{child_el.tag}"
            # Disambiguate duplicate sibling tags
            siblings = [c for c in node.children if c.tag == child_el.tag]
            if siblings:
                child_path = f"{child_path}[{len(siblings)}]"
            child = walk(child_el, node, child_path)
            node.children.append(child)
        return node

    # Document may have a wrapper with no geometry; use empty path for root
    doc = LayoutNode(element=root, tag=root.tag, path="", parent=None)
    doc.sync_from_element()
    for child_el in list(root):
        if not isinstance(child_el.tag, str):
            continue
        if child_el.tag in SKIP_AS_WIDGET:
            continue
        child_path = child_el.tag
        siblings = [c for c in doc.children if c.tag == child_el.tag]
        if siblings:
            child_path = f"{child_path}[{len(siblings)}]"
        doc.children.append(walk(child_el, doc, child_path))
    doc.recompute_absolute(0.0, 0.0)
    return doc


def apply_meta_positions(
    doc: LayoutNode,
    meta: dict[str, dict[str, float]],
    *,
    default_size: float | None = None,
) -> None:
    """Attach editor handles for nodes without XML geometry.

    Positions come from the sidecar meta map when present; otherwise
    ``default_meta_xy`` (sibling scroll_/templ_, else 0,0) is used.
    Size is always the fixed diamond marker (width/height in meta are ignored).
    """
    from .meta import DEFAULT_HANDLE_SIZE

    size = default_size if default_size is not None else DEFAULT_HANDLE_SIZE
    for node in doc.iter_all():
        if not node.path:
            continue  # skip document root
        if has_geometry(node.element):
            continue
        geo = meta.get(node.path)
        if geo is not None:
            node.apply_meta_geometry(
                x=geo["x"],
                y=geo["y"],
                width=size,
                height=size,
            )
        else:
            dx, dy = default_meta_xy(node)
            node.apply_meta_geometry(x=dx, y=dy, width=size, height=size)
    doc.recompute_absolute(0.0, 0.0)


def collect_meta_positions(doc: LayoutNode) -> dict[str, dict[str, float]]:
    from .meta import DEFAULT_HANDLE_SIZE

    out: dict[str, dict[str, float]] = {}
    for node in doc.iter_drawables():
        if not node.from_meta or not node.path:
            continue
        out[node.path] = {
            "x": node.x,
            "y": node.y,
            "width": DEFAULT_HANDLE_SIZE,
            "height": DEFAULT_HANDLE_SIZE,
        }
    return out


def top_level_sections(doc: LayoutNode) -> list[LayoutNode]:
    """Immediate children of the document root (background, main_dialog, ...)."""
    return list(doc.children)


def layer_sections(doc: LayoutNode) -> list[LayoutNode]:
    """Sections useful as visibility toggles: top-level + nested dialog panels."""
    layers: list[LayoutNode] = []
    for child in doc.children:
        layers.append(child)
        # Under main_dialog-like containers, expose options / popup_* as layers
        for grand in child.children:
            tag = grand.tag.lower()
            if tag.startswith("popup") or tag in {"options", "background"}:
                layers.append(grand)
    return layers


def default_layer_visible(node: LayoutNode) -> bool:
    """Hide overlays/templates by default so the main layout is readable."""
    tag = node.tag.lower()
    if tag.startswith("popup"):
        return False
    # options is a scroll-content template (coords relative to 0,0, not screen)
    if tag == "options":
        return False
    return True
