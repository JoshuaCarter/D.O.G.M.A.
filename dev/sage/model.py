"""Layout tree model: parent-relative Stalker UI widgets."""

from __future__ import annotations

from dataclasses import dataclass, field
from xml.etree.ElementTree import Element

GEO_ATTRS = ("x", "y", "width", "height")
# XPath-style path between tags (also keys in .xml.meta)
PATH_SEP = "/"
# Engine CUILines / InitText (UIXmlInit.cpp) — single-letter enums.
TEXT_ALIGN_VALUES = ("l", "c", "r")
TEXT_VERT_ALIGN_VALUES = ("t", "c", "b")
# ScrollView padding — note engine typo ``left_ident`` / ``right_ident``.
SCROLL_FLOAT_ATTRS = (
    "left_ident",
    "right_ident",
    "top_indent",
    "bottom_indent",
    "vert_interval",
)
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
    # Engine CUILines::m_TextOffset from <text x="" y="">.
    x: float = 0.0
    y: float = 0.0
    complex_mode: bool = False
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

    def ensure_text_element(self) -> Element | None:
        """Return <text>, creating an empty one after <texture> when needed."""
        if self.from_meta:
            return None
        t = self.element.find("text")
        if t is not None:
            return t
        t = Element("text")
        tex = self.element.find("texture")
        if tex is not None:
            idx = list(self.element).index(tex) + 1
            self.element.insert(idx, t)
        else:
            self.element.append(t)
        self.text = _read_text(self.element)
        return t

    def set_text_content(self, content: str) -> bool:
        """Set the <text> element body. Creates <text> when needed. Returns True if changed."""
        if self.from_meta:
            return False
        t = self.element.find("text")
        if t is None:
            if content == "":
                return False
            t = self.ensure_text_element()
            if t is None:
                return False
        old = t.text or ""
        if old == content:
            return False
        t.text = content
        self.text = _read_text(self.element)
        return True

    def prune_invalid_text(self) -> bool:
        """Drop fontless ``<text>`` (engine InitText crash). Returns True if removed."""
        if self.from_meta:
            return False
        t = self.element.find("text")
        if t is None:
            return False
        if (t.get("font") or "").strip():
            return False
        self.element.remove(t)
        self.text = None
        return True

    def apply_text_props(
        self,
        *,
        font: str | None | object = ...,
        align: str | None | object = ...,
        vert_align: str | None | object = ...,
        complex_mode: bool | object = ...,
        r: int | None | object = ...,
        g: int | None | object = ...,
        b: int | None | object = ...,
        a: int | None | object = ...,
        x: float | None | object = ...,
        y: float | None | object = ...,
    ) -> bool:
        """Update <text> attrs. ``...`` = leave; ``None`` = remove attr."""
        if self.from_meta:
            return False
        touched = any(
            v is not ...
            for v in (font, align, vert_align, complex_mode, r, g, b, a, x, y)
        )
        if not touched:
            return False
        t = self.element.find("text")
        if t is None:
            t = self.ensure_text_element()
            if t is None:
                return False
        changed = False
        if font is not ...:
            changed |= _set_attr(t, "font", (font or "").strip() or None)
        if align is not ...:
            val = (align or "").strip().lower() or None
            if val is not None and val not in TEXT_ALIGN_VALUES:
                val = align.strip() if isinstance(align, str) else None
            changed |= _set_attr(t, "align", val)
        if vert_align is not ...:
            val = (vert_align or "").strip().lower() or None
            if val is not None and val not in TEXT_VERT_ALIGN_VALUES:
                val = vert_align.strip() if isinstance(vert_align, str) else None
            changed |= _set_attr(t, "vert_align", val)
        if complex_mode is not ...:
            changed |= _set_bool_attr(t, "complex_mode", bool(complex_mode))
        for ch, val in (("r", r), ("g", g), ("b", b), ("a", a)):
            if val is ...:
                continue
            if val is None:
                changed |= _set_attr(t, ch, None)
            else:
                changed |= _set_attr(t, ch, str(int(val)))
        for attr, val in (("x", x), ("y", y)):
            if val is ...:
                continue
            if val is None:
                changed |= _set_attr(t, attr, None)
            else:
                changed |= _set_attr(t, attr, _fmt(float(val)))
        if self.prune_invalid_text():
            return True
        if changed:
            self.text = _read_text(self.element)
        return changed

    def widget_float_attr(self, name: str) -> float | None:
        raw = self.element.get(name)
        if raw is None or raw == "":
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    def widget_bool_attr(self, name: str, *, default: bool = False) -> bool:
        raw = self.element.get(name)
        if raw is None:
            return default
        return raw.strip().lower() in ("1", "true")

    def is_scroll_view(self) -> bool:
        """Heuristic for InitScrollView paths (almost always ``scroll_*``)."""
        return (self.tag or "").lower().startswith("scroll")

    def is_frame_line(self) -> bool:
        """Heuristic for InitFrameLine (engine asserts ``stretch`` is false)."""
        tag = (self.tag or "").lower()
        return "frame_line" in tag or tag.startswith("frameline")

    def allows_texture_props(self) -> bool:
        """ScrollView is InitWindow-only — no InitTexture unless XML already has one."""
        if self.is_scroll_view() and self.texture is None:
            return False
        return True

    def allows_stretch(self) -> bool:
        """FrameLine asserts stretch==0; ScrollView has no SetStretchTexture."""
        if self.is_frame_line() or self.is_scroll_view():
            return False
        return self.allows_texture_props()

    def allows_text_props(self) -> bool:
        """ScrollView ``<text>`` children are list items, not widget TextItemControl."""
        return not self.is_scroll_view()

    def allows_scroll_props(self) -> bool:
        if self.is_scroll_view():
            return True
        if any(self.element.get(a) is not None for a in SCROLL_FLOAT_ATTRS):
            return True
        return self.element.get("always_show_scroll") is not None

    def has_scroll_attrs(self) -> bool:
        """Back-compat alias — scroll padding / always_show_scroll apply here."""
        return self.allows_scroll_props()

    def apply_widget_props(
        self,
        *,
        stretch: bool | object = ...,
        always_show_scroll: bool | object = ...,
        left_ident: float | None | object = ...,
        right_ident: float | None | object = ...,
        top_indent: float | None | object = ...,
        bottom_indent: float | None | object = ...,
        vert_interval: float | None | object = ...,
    ) -> bool:
        """Update widget-level attrs (stretch, scroll padding)."""
        if self.from_meta:
            return False
        changed = False
        if stretch is not ...:
            self.stretch = bool(stretch)
            # Match apply_geometry_to_element: only write "0" if attr already present.
            if self.stretch:
                changed |= _set_attr(self.element, "stretch", "1")
            elif self.element.get("stretch") is not None:
                changed |= _set_attr(self.element, "stretch", "0")
        if always_show_scroll is not ...:
            changed |= _set_bool_attr(
                self.element, "always_show_scroll", bool(always_show_scroll)
            )
        for name, val in (
            ("left_ident", left_ident),
            ("right_ident", right_ident),
            ("top_indent", top_indent),
            ("bottom_indent", bottom_indent),
            ("vert_interval", vert_interval),
        ):
            if val is ...:
                continue
            if val is None:
                changed |= _set_attr(self.element, name, None)
            else:
                changed |= _set_attr(self.element, name, _fmt(float(val)))
        return changed

    def set_texture_name(self, name: str, *, clear_uv: bool = True) -> bool:
        """Set <texture> body (atlas id or ui\\path). Creates element when needed."""
        if self.from_meta:
            return False
        name = (name or "").strip()
        tex = self.element.find("texture")
        if tex is None:
            if not name:
                return False
            tex = Element("texture")
            self.element.insert(0, tex)
        elif not name:
            self.element.remove(tex)
            self.texture = None
            return True
        old = (tex.text or "").strip()
        uv_changed = False
        if clear_uv:
            for attr in ("x", "y", "width", "height"):
                if attr in tex.attrib:
                    del tex.attrib[attr]
                    uv_changed = True
        if old == name and not uv_changed:
            return False
        tex.text = name
        self.texture = _read_texture(self.element)
        return True

    def capture_prop_state(self):
        """Snapshot props-panel state for undo (imported PropState to avoid cycles)."""
        from .undo import PropState

        el = self.element
        text_el = el.find("text")
        tex_el = el.find("texture")
        return PropState(
            path=self.path,
            stretch=el.get("stretch"),
            always_show_scroll=el.get("always_show_scroll"),
            left_ident=el.get("left_ident"),
            right_ident=el.get("right_ident"),
            top_indent=el.get("top_indent"),
            bottom_indent=el.get("bottom_indent"),
            vert_interval=el.get("vert_interval"),
            has_text=text_el is not None,
            text_content=(text_el.text or "").strip() if text_el is not None else "",
            font=text_el.get("font") if text_el is not None else None,
            align=text_el.get("align") if text_el is not None else None,
            vert_align=text_el.get("vert_align") if text_el is not None else None,
            complex_mode=text_el.get("complex_mode") if text_el is not None else None,
            r=text_el.get("r") if text_el is not None else None,
            g=text_el.get("g") if text_el is not None else None,
            b=text_el.get("b") if text_el is not None else None,
            a=text_el.get("a") if text_el is not None else None,
            text_x=text_el.get("x") if text_el is not None else None,
            text_y=text_el.get("y") if text_el is not None else None,
            has_texture=tex_el is not None,
            texture_name=(tex_el.text or "").strip() if tex_el is not None else "",
            tex_x=tex_el.get("x") if tex_el is not None else None,
            tex_y=tex_el.get("y") if tex_el is not None else None,
            tex_w=tex_el.get("width") if tex_el is not None else None,
            tex_h=tex_el.get("height") if tex_el is not None else None,
            tex_r=tex_el.get("r") if tex_el is not None else None,
            tex_g=tex_el.get("g") if tex_el is not None else None,
            tex_b=tex_el.get("b") if tex_el is not None else None,
            tex_a=tex_el.get("a") if tex_el is not None else None,
        )

    def apply_prop_state(self, state) -> None:
        """Restore a ``PropState`` snapshot onto this node / XML element."""
        from .undo import PropState

        if not isinstance(state, PropState) or self.from_meta:
            return
        el = self.element
        for name in (
            "stretch",
            "always_show_scroll",
            "left_ident",
            "right_ident",
            "top_indent",
            "bottom_indent",
            "vert_interval",
        ):
            _set_attr(el, name, getattr(state, name))
        self.stretch = (el.get("stretch") or "0") in ("1", "true", "True")

        text_el = el.find("text")
        if not state.has_text:
            if text_el is not None:
                el.remove(text_el)
            self.text = None
        else:
            if text_el is None:
                text_el = self.ensure_text_element()
            if text_el is not None:
                text_el.text = state.text_content
                for attr, key in (
                    ("font", "font"),
                    ("align", "align"),
                    ("vert_align", "vert_align"),
                    ("complex_mode", "complex_mode"),
                    ("r", "r"),
                    ("g", "g"),
                    ("b", "b"),
                    ("a", "a"),
                    ("x", "text_x"),
                    ("y", "text_y"),
                ):
                    _set_attr(text_el, attr, getattr(state, key))
            self.text = _read_text(el)

        tex_el = el.find("texture")
        if not state.has_texture:
            if tex_el is not None:
                el.remove(tex_el)
            self.texture = None
            return
        if tex_el is None:
            tex_el = Element("texture")
            el.insert(0, tex_el)
        tex_el.text = state.texture_name
        for attr, key in (
            ("x", "tex_x"),
            ("y", "tex_y"),
            ("width", "tex_w"),
            ("height", "tex_h"),
            ("r", "tex_r"),
            ("g", "tex_g"),
            ("b", "tex_b"),
            ("a", "tex_a"),
        ):
            _set_attr(tex_el, attr, getattr(state, key))
        self.texture = _read_texture(el)

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


def _set_attr(el: Element, name: str, value: str | None) -> bool:
    """Set attr, or delete when ``value`` is None/"". Returns True if changed."""
    old = el.get(name)
    if value is None or value == "":
        if old is None:
            return False
        del el.attrib[name]
        return True
    if old == value:
        return False
    el.set(name, value)
    return True


def _set_bool_attr(el: Element, name: str, value: bool, *, write_zero: bool = True) -> bool:
    if value:
        return _set_attr(el, name, "1")
    if write_zero or el.get(name) is not None:
        return _set_attr(el, name, "0")
    return False


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
    try:
        complex_mode = int(t.get("complex_mode", "0") or "0") != 0
    except ValueError:
        complex_mode = False
    ref = TextRef(
        content=(t.text or "").strip(),
        font=t.get("font", "") or "",
        align=t.get("align", "") or "",
        vert_align=t.get("vert_align", "") or "",
        x=_f(t, "x"),
        y=_f(t, "y"),
        complex_mode=complex_mode,
    )
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
