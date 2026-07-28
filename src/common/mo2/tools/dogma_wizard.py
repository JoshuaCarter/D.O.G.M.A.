#!/usr/bin/env python3
"""DOGMA Setup wizard — pick options from config/mods.yml + feature depends."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import dogma_mo2_lib as lib

_URL_RE = re.compile(r"https?://[^\s\]\)>,;]+")

# SW_HIDE / SW_SHOW — console is useless while the Tk wizard is up.
_SW_HIDE = 0
_SW_SHOW = 5


def _console_hwnd() -> int:
    if sys.platform != "win32":
        return 0
    try:
        import ctypes

        return int(ctypes.windll.kernel32.GetConsoleWindow() or 0)
    except (AttributeError, OSError, ValueError):
        return 0


def _set_console_visible(hwnd: int, visible: bool) -> None:
    if not hwnd:
        return
    try:
        import ctypes

        ctypes.windll.user32.ShowWindow(hwnd, _SW_SHOW if visible else _SW_HIDE)
    except (AttributeError, OSError):
        pass


# Dark palette (ttk clam + matching tk widgets).
_THEME = {
    "bg": "#1e1e1e",
    "bg_raised": "#2a2a2a",
    "bg_sunken": "#181818",
    "fg": "#e6e6e6",
    "fg_muted": "#9a9a9a",
    # Placeholder mod id in the sunken name well (~20% darker than fg_muted).
    "fg_placeholder": "#7b7b7b",
    "fg_dim": "#777777",
    "fg_expected": "#ffd666",
    "border": "#3c3c3c",
    "select_bg": "#3a3a3a",
    "select_fg": "#ffffff",
    "link": "#6cb6ff",
    "accent": "#3ddc84",
    "button_bg": "#333333",
    "button_active": "#404040",
    "tip_bg": "#2d2d2d",
    "info_fg": "#ffffff",
    "alert": "#e05555",
}


def _win_path(path: Path) -> str:
    """Display path with backslashes (Windows-style)."""
    return str(path.resolve()).replace("/", "\\")


def _expand_desc(text: str, *, mo2_root: Path, dogma_dl: Path) -> str:
    """Replace placeholders; show downloads folder with backslashes only."""
    if not text:
        return text
    folder = _win_path(dogma_dl)
    out = lib.expand_path_placeholders(text, mo2_root)
    out = out.replace("<DOGMA_DOWNLOADS>", folder).replace("<dogma_downloads>", folder)
    out = re.sub(
        r"(?i)(?:[A-Za-z]:[/\\](?:[^/\\\s]+[/\\])*?)?downloads[/\\]DOGMA",
        lambda _m: folder,
        out,
    )
    return _fix_slashes_outside_urls(out)


def _fix_slashes_outside_urls(text: str) -> str:
    """Use backslashes everywhere except inside http(s) URLs."""
    parts: list[str] = []
    pos = 0
    for m in _URL_RE.finditer(text):
        parts.append(text[pos : m.start()].replace("/", "\\"))
        parts.append(m.group(0))
        pos = m.end()
    parts.append(text[pos:].replace("/", "\\"))
    return "".join(parts)


def _open_in_explorer(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    target = str(path.resolve())
    if sys.platform == "win32":
        os.startfile(target)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", target], check=False)
    else:
        subprocess.run(["xdg-open", target], check=False)


def _link_spans(
    text: str, *, dogma_dl: Path
) -> list[tuple[int, int, Callable[[], None]]]:
    """Non-overlapping (start, end, on_click) for URLs and the downloads folder only."""
    spans: list[tuple[int, int, Callable[[], None]]] = []
    used: list[tuple[int, int]] = []

    def _fits(start: int, end: int) -> bool:
        return not any(start < u1 and end > u0 for u0, u1 in used)

    for m in _URL_RE.finditer(text):
        url = m.group(0).rstrip(".,);]")
        end = m.start() + len(url)
        if _fits(m.start(), end):
            used.append((m.start(), end))
            spans.append((m.start(), end, lambda u=url: webbrowser.open(u)))

    folder = _win_path(dogma_dl)
    for m in re.finditer(re.escape(folder), text, re.IGNORECASE):
        if _fits(m.start(), m.end()):
            used.append((m.start(), m.end()))
            spans.append((m.start(), m.end(), lambda: _open_in_explorer(dogma_dl)))

    spans.sort(key=lambda t: t[0])
    return spans


def _frame_bg(widget: tk.Misc) -> str:
    try:
        bg = ttk.Style().lookup("TFrame", "background")
        if bg:
            return bg
    except tk.TclError:
        pass
    try:
        return str(widget.cget("background"))
    except tk.TclError:
        return _THEME["bg"]


def _put_rect(img: tk.PhotoImage, x0: int, y0: int, x1: int, y1: int, color: str) -> None:
    for y in range(y0, y1 + 1):
        img.put(color, to=(x0, y, x1 + 1, y + 1))


def _build_indicator_images(root: tk.Tk) -> dict[str, tk.PhotoImage]:
    """Checkbox / radio images with a green selected mark (20px)."""
    s = 20
    border = "#888888"
    fill = _THEME["bg_raised"]
    green = _THEME["accent"]
    cx = (s - 1) / 2.0

    def blank() -> tk.PhotoImage:
        img = tk.PhotoImage(master=root, width=s, height=s)
        _put_rect(img, 0, 0, s - 1, s - 1, _THEME["bg"])
        return img

    check_off = blank()
    _put_rect(check_off, 1, 1, s - 2, s - 2, border)
    _put_rect(check_off, 2, 2, s - 3, s - 3, fill)

    check_on = blank()
    _put_rect(check_on, 1, 1, s - 2, s - 2, border)
    _put_rect(check_on, 2, 2, s - 3, s - 3, fill)
    # Thicker green tick (scaled up from the old 16px mark).
    tick = [
        (4, 10),
        (5, 11),
        (6, 12),
        (7, 13),
        (8, 14),
        (9, 13),
        (10, 12),
        (11, 11),
        (12, 10),
        (13, 9),
        (14, 8),
        (15, 7),
    ]
    for x, y in tick:
        _put_rect(check_on, x, y, x + 1, y + 1, green)

    ring_outer = 9.0
    ring_inner = 6.6
    dot_r = 4.0

    radio_off = blank()
    for y in range(s):
        for x in range(s):
            dx, dy = x - cx, y - cx
            r2 = dx * dx + dy * dy
            if ring_inner * ring_inner <= r2 <= ring_outer * ring_outer:
                radio_off.put(border, (x, y))
            elif r2 < ring_inner * ring_inner:
                radio_off.put(fill, (x, y))

    radio_on = blank()
    for y in range(s):
        for x in range(s):
            dx, dy = x - cx, y - cx
            r2 = dx * dx + dy * dy
            if ring_inner * ring_inner <= r2 <= ring_outer * ring_outer:
                radio_on.put(border, (x, y))
            elif r2 < ring_inner * ring_inner:
                radio_on.put(fill, (x, y))
            if r2 <= dot_r * dot_r:
                radio_on.put(green, (x, y))

    return {
        "check_off": check_off,
        "check_on": check_on,
        "radio_off": radio_off,
        "radio_on": radio_on,
    }


def _filled_info_image(
    master: tk.Misc, *, bg: str, fill: str | None = None, letter: str | None = None
) -> tk.PhotoImage:
    """Filled blue circle with a white i."""
    fill = fill or _THEME["link"]
    letter = letter or _THEME["info_fg"]
    s = 14
    img = tk.PhotoImage(master=master, width=s, height=s)
    _put_rect(img, 0, 0, s - 1, s - 1, bg)
    cx, cy, r = 6.5, 6.5, 6.2
    for y in range(s):
        for x in range(s):
            if (x - cx) * (x - cx) + (y - cy) * (y - cy) <= r * r:
                img.put(fill, (x, y))
    # Dot
    _put_rect(img, 6, 3, 7, 4, letter)
    # Stem
    _put_rect(img, 6, 6, 7, 10, letter)
    return img


def _apply_dark_theme(root: tk.Tk) -> dict[str, tk.PhotoImage]:
    """Dark ttk clam theme, green indicators, Windows immersive title bar."""
    root.configure(bg=_THEME["bg"])
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    bg = _THEME["bg"]
    raised = _THEME["bg_raised"]
    fg = _THEME["fg"]
    muted = _THEME["fg_muted"]
    border = _THEME["border"]
    btn = _THEME["button_bg"]
    btn_active = _THEME["button_active"]

    style.configure(".", background=bg, foreground=fg, bordercolor=border)
    style.configure("TFrame", background=bg)
    style.configure("TLabel", background=bg, foreground=fg)
    style.configure("TLabelframe", background=bg, foreground=fg, bordercolor=border)
    style.configure("TLabelframe.Label", background=bg, foreground=fg)
    style.configure(
        "TButton",
        background=btn,
        foreground=fg,
        bordercolor=border,
        focuscolor=border,
        lightcolor=btn,
        darkcolor=btn,
        padding=(14, 6),
    )
    style.map(
        "TButton",
        background=[("active", btn_active), ("pressed", raised)],
        foreground=[("disabled", muted)],
    )
    style.configure(
        "Vertical.TScrollbar",
        background=raised,
        troughcolor=bg,
        bordercolor=border,
        arrowcolor=fg,
    )
    style.map("Vertical.TScrollbar", background=[("active", btn_active)])

    imgs = _build_indicator_images(root)
    root._dogma_imgs = imgs  # type: ignore[attr-defined]  # keep refs alive

    style.element_create(
        "Dogma.Checkbutton.indicator",
        "image",
        imgs["check_off"],
        ("selected", imgs["check_on"]),
        ("pressed", imgs["check_on"]),
        border=0,
        sticky="w",
    )
    style.layout(
        "Dogma.TCheckbutton",
        [
            (
                "Checkbutton.padding",
                {
                    "sticky": "nswe",
                    "children": [
                        ("Dogma.Checkbutton.indicator", {"side": "left", "sticky": ""}),
                        (
                            "Checkbutton.focus",
                            {
                                "side": "left",
                                "sticky": "w",
                                "children": [
                                    ("Checkbutton.label", {"sticky": "nswe"})
                                ],
                            },
                        ),
                    ],
                },
            )
        ],
    )
    style.configure(
        "Dogma.TCheckbutton",
        background=bg,
        foreground=fg,
        focuscolor=bg,
        padding=2,
    )
    style.map(
        "Dogma.TCheckbutton",
        background=[("active", bg)],
        foreground=[("disabled", muted)],
    )
    style.layout(
        "Dogma.Box.TCheckbutton",
        style.layout("Dogma.TCheckbutton"),
    )
    style.configure(
        "Dogma.Box.TCheckbutton",
        background=raised,
        foreground=fg,
        focuscolor=raised,
        padding=0,
    )
    style.map(
        "Dogma.Box.TCheckbutton",
        background=[("active", raised), ("pressed", raised)],
        foreground=[("disabled", muted)],
    )

    style.element_create(
        "Dogma.Radiobutton.indicator",
        "image",
        imgs["radio_off"],
        ("selected", imgs["radio_on"]),
        ("pressed", imgs["radio_on"]),
        border=0,
        sticky="w",
    )
    style.layout(
        "Dogma.TRadiobutton",
        [
            (
                "Radiobutton.padding",
                {
                    "sticky": "nswe",
                    "children": [
                        ("Dogma.Radiobutton.indicator", {"side": "left", "sticky": ""}),
                        (
                            "Radiobutton.focus",
                            {
                                "side": "left",
                                "sticky": "w",
                                "children": [
                                    ("Radiobutton.label", {"sticky": "nswe"})
                                ],
                            },
                        ),
                    ],
                },
            )
        ],
    )
    style.configure(
        "Dogma.TRadiobutton",
        background=bg,
        foreground=fg,
        focuscolor=bg,
        padding=2,
    )
    style.map(
        "Dogma.TRadiobutton",
        background=[("active", bg)],
        foreground=[("disabled", muted)],
    )
    style.layout(
        "Dogma.Box.TRadiobutton",
        style.layout("Dogma.TRadiobutton"),
    )
    style.configure(
        "Dogma.Box.TRadiobutton",
        background=raised,
        foreground=fg,
        focuscolor=raised,
        padding=0,
    )
    style.map(
        "Dogma.Box.TRadiobutton",
        background=[("active", raised), ("pressed", raised)],
        foreground=[("disabled", muted)],
    )

    if sys.platform == "win32":
        try:
            import ctypes

            root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
            if not hwnd:
                hwnd = root.winfo_id()
            value = ctypes.c_int(1)
            for attr in (20, 19):
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    attr,
                    ctypes.byref(value),
                    ctypes.sizeof(value),
                )
        except Exception:
            pass

    return imgs


def _pack_link_text(
    parent: tk.Misc,
    text: str,
    *,
    dogma_dl: Path,
    wraplength: int = 900,
    padx: tuple[int, int] = (0, 0),
    pady: tuple[int, int] = (4, 0),
    bold_spans: list[tuple[int, int]] | None = None,
    font_size: int = 10,
    foreground: str | None = None,
) -> None:
    """Read-only wrapped text; http(s) and downloads/DOGMA paths are clickable."""
    body = text.strip()
    if not body:
        return

    bg = _frame_bg(parent)
    fg = foreground or _THEME["fg_muted"]
    box = tk.Text(
        parent,
        wrap="word",
        width=max(8, wraplength // 8),
        height=1,
        padx=padx[0],
        pady=0,
        borderwidth=0,
        highlightthickness=0,
        background=bg,
        foreground=fg,
        insertbackground=_THEME["fg"],
        selectbackground=_THEME["select_bg"],
        selectforeground=_THEME["select_fg"],
        font=("Segoe UI", font_size),
        cursor="arrow",
        relief="flat",
    )
    box.tag_configure("link", foreground=_THEME["link"], underline=True)
    box.tag_configure(
        "bold",
        foreground=_THEME["fg"],
        font=("Segoe UI", font_size, "bold"),
    )
    box.tag_bind("link", "<Enter>", lambda _e: box.configure(cursor="hand2"))
    box.tag_bind("link", "<Leave>", lambda _e: box.configure(cursor="arrow"))

    spans = _link_spans(body, dogma_dl=dogma_dl)
    bold_spans = bold_spans or []
    events: list[tuple[int, int, str, Callable[[], None] | None]] = []
    for start, end, action in spans:
        events.append((start, end, "link", action))
    for start, end in bold_spans:
        events.append((start, end, "bold", None))
    events.sort(key=lambda t: t[0])

    pos = 0
    link_idx = 0
    for start, end, kind, action in events:
        if start < pos:
            continue
        if start > pos:
            box.insert("end", body[pos:start])
        chunk = body[start:end]
        if kind == "link" and action is not None:
            tag = f"link_{link_idx}"
            box.insert("end", chunk, (tag, "link"))
            box.tag_bind(tag, "<Button-1>", lambda _e, fn=action: fn())
            link_idx += 1
        else:
            box.insert("end", chunk, ("bold",))
        pos = end
    if pos < len(body):
        box.insert("end", body[pos:])

    box.configure(state="disabled")
    lines = int(box.index("end-1c").split(".")[0])
    box.configure(height=max(1, lines))
    box.pack(anchor="w", fill="x", pady=pady)

class _HoverTip:
    """Delayed hover tooltip with styled effect-list sections."""

    def __init__(
        self,
        widget: tk.Misc,
        sections_fn: Callable[[], list[tuple[str, list[str], str]]],
        *,
        delay_ms: int = 450,
        hosts: list[tk.Misc] | None = None,
    ) -> None:
        self.widget = widget
        self.hosts = list(hosts) if hosts else [widget]
        self.sections_fn = sections_fn
        self.delay_ms = delay_ms
        self._after: str | None = None
        self._hide_after: str | None = None
        self._tip: tk.Toplevel | None = None
        for w in self.hosts:
            w.bind("<Enter>", self._schedule, add="+")
            w.bind("<Leave>", self._leave, add="+")
            w.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event: tk.Event | None = None) -> None:
        self._cancel_hide()
        self._cancel()
        self._after = self.widget.after(self.delay_ms, self._show)

    def _cancel(self) -> None:
        if self._after is not None:
            try:
                self.widget.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None

    def _cancel_hide(self) -> None:
        if self._hide_after is not None:
            try:
                self.widget.after_cancel(self._hide_after)
            except tk.TclError:
                pass
            self._hide_after = None

    def _widget_under_pointer(self) -> tk.Misc | None:
        try:
            x = self.widget.winfo_pointerx()
            y = self.widget.winfo_pointery()
            return self.widget.winfo_containing(x, y)
        except tk.TclError:
            return None

    def _is_under_tip(self, under: tk.Misc | None) -> bool:
        tip = self._tip
        if tip is None or under is None:
            return False
        cur: tk.Misc | None = under
        while cur is not None:
            if cur is tip:
                return True
            try:
                # Stop at any Toplevel — tip is its own window.
                if cur.winfo_class() == "Toplevel":
                    break
                cur = cur.master
            except tk.TclError:
                break
        return False

    def _is_under_host(self, under: tk.Misc | None) -> bool:
        if under is None:
            return False
        cur: tk.Misc | None = under
        while cur is not None:
            if cur in self.hosts:
                return True
            try:
                # Tip is Toplevel(root); never walk from another toplevel into hosts.
                if cur.winfo_class() == "Toplevel":
                    break
                cur = cur.master
            except tk.TclError:
                break
        return False

    def _pointer_over_host(self) -> bool:
        return self._is_under_host(self._widget_under_pointer())

    def _pointer_over_tip(self) -> bool:
        return self._is_under_tip(self._widget_under_pointer())

    def _leave(self, _event: tk.Event | None = None) -> None:
        self._cancel()
        self._cancel_hide()
        self._hide_after = self.widget.after(60, self._hide_if_left)

    def _hide_if_left(self) -> None:
        self._hide_after = None
        # Stay up while over the control or the tip itself; hide once both are left.
        if self._pointer_over_host() or self._pointer_over_tip():
            return
        self._hide()

    def _hide(self, _event: tk.Event | None = None) -> None:
        self._cancel()
        self._cancel_hide()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None

    def _show(self) -> None:
        self._after = None
        if not self._pointer_over_host():
            return
        sections = [
            (label, items, kind)
            for label, items, kind in self.sections_fn()
            if items
        ]
        if not sections:
            return
        self._hide()
        # Parent to the app root — not the host — so tip isn't in the host master chain.
        tip = tk.Toplevel(self.widget.winfo_toplevel())
        tip.wm_overrideredirect(True)
        tip.attributes("-topmost", True)
        frame = tk.Frame(
            tip,
            background=_THEME["tip_bg"],
            highlightbackground=_THEME["border"],
            highlightthickness=1,
            padx=10,
            pady=8,
        )
        frame.pack()
        box = tk.Text(
            frame,
            wrap="word",
            width=100,
            height=1,
            borderwidth=0,
            highlightthickness=0,
            background=_THEME["tip_bg"],
            foreground=_THEME["fg"],
            font=("Segoe UI", 9),
            cursor="arrow",
            relief="flat",
        )
        box.tag_configure("normal", foreground=_THEME["fg"])
        box.tag_configure("expected", foreground=_THEME["fg_expected"])
        first = True
        for label, items, kind in sections:
            tag = "expected" if kind == "expected" else "normal"
            if not first:
                box.insert("end", "\n\n", (tag,))
            first = False
            if label:
                box.insert("end", f"{label}:\n", (tag,))
                for i, item in enumerate(items):
                    if i:
                        box.insert("end", "\n", (tag,))
                    box.insert("end", f"  • {item}", (tag,))
            else:
                for i, item in enumerate(items):
                    if i:
                        box.insert("end", "\n", (tag,))
                    box.insert("end", item, (tag,))
        box.configure(state="disabled")
        lines = int(box.index("end-1c").split(".")[0])
        box.configure(height=max(1, min(28, lines)))
        box.pack()
        # Moving onto the tip cancels hide; leaving tip hides if not back on host.
        for w in (tip, frame, box):
            w.bind("<Enter>", lambda _e: self._cancel_hide(), add="+")
            w.bind("<Leave>", self._leave, add="+")
        tip.update_idletasks()
        try:
            x = self.widget.winfo_rootx() + 18
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        except tk.TclError:
            tip.destroy()
            return
        tip.wm_geometry(f"+{x}+{y}")
        self._tip = tip

def _attach_hover_tip(
    widgets: list[tk.Misc],
    sections_fn: Callable[[], list[tuple[str, list[str], str]]],
) -> _HoverTip:
    """Bind one tooltip to several widgets (indicator + label)."""
    return _HoverTip(widgets[0], sections_fn, hosts=widgets)


def _attach_text_tip(widgets: list[tk.Misc], text: str) -> _HoverTip:
    """Plain one-line hover tip (full URL / path)."""
    tip_text = text

    def _sections() -> list[tuple[str, list[str], str]]:
        return [("", [tip_text], "normal")] if tip_text else []

    return _HoverTip(widgets[0], _sections, hosts=widgets, delay_ms=350)


def _pack_info_icon(
    parent: tk.Misc,
    sections_fn: Callable[[], list[tuple[str, list[str], str]]],
    *,
    bg: str | None = None,
) -> tk.Label:
    """Filled blue ⓘ; hover shows what this mod does."""
    bg = bg if bg is not None else _frame_bg(parent)
    img = _filled_info_image(parent.winfo_toplevel(), bg=bg)
    lbl = tk.Label(
        parent,
        image=img,
        background=bg,
        borderwidth=0,
        padx=0,
        pady=0,
        cursor="hand2",
    )
    lbl._dogma_info_img = img  # type: ignore[attr-defined]
    _attach_hover_tip([lbl], sections_fn)
    return lbl


def _pack_dotted_link(
    parent: tk.Misc,
    *,
    text: str,
    tip: str,
    command: Callable[[], None],
    font_size: int = 10,
    bold: bool = False,
) -> ttk.Frame:
    """Clickable dotted-underline label with a hover tip showing the full target."""
    col = ttk.Frame(parent)
    bg = _frame_bg(parent)
    font: tuple = ("Segoe UI", font_size, "bold") if bold else ("Segoe UI", font_size)
    lbl = tk.Label(
        col,
        text=text,
        background=bg,
        foreground=_THEME["link"],
        font=font,
        cursor="hand2",
        borderwidth=0,
        padx=0,
        pady=0,
    )
    lbl.pack(anchor="w")
    underline = tk.Canvas(
        col,
        height=3,
        highlightthickness=0,
        background=bg,
        borderwidth=0,
    )
    underline.pack(anchor="w", fill="x")

    def _redraw(_event: tk.Event | None = None) -> None:
        underline.delete("all")
        w = max(int(lbl.winfo_reqwidth()), 8)
        underline.configure(width=w)
        underline.create_line(
            0, 1, w, 1, fill=_THEME["link"], dash=(1, 2), width=1
        )

    lbl.bind("<Configure>", _redraw, add="+")
    col.after_idle(_redraw)
    for w in (lbl, underline):
        w.bind("<Button-1>", lambda _e: command(), add="+")
    _attach_text_tip([lbl, underline, col], tip)
    return col


def _pack_hoverable_choice(
    parent: tk.Misc,
    *,
    kind: str,
    text: str,
    variable: tk.Variable,
    value: object | None = None,
    command: Callable[[], None] | None = None,
    link_url: str = "",
) -> ttk.Frame:
    """Checkbox/radio row. URL tip on link labels when ``link_url`` is set.

    Returns the horizontal row frame so callers can append archive fields.
    """
    row = ttk.Frame(parent)
    row.pack(anchor="w", fill="x")

    if kind == "check":
        btn: ttk.Checkbutton | ttk.Radiobutton = ttk.Checkbutton(
            row,
            text="",
            style="Dogma.TCheckbutton",
            variable=variable,
            command=command,
            width=0,
        )
    else:
        btn = ttk.Radiobutton(
            row,
            text="",
            style="Dogma.TRadiobutton",
            variable=variable,
            value="" if value is None else value,
            command=command,
            width=0,
        )
    btn.pack(side="left", padx=(0, 4))

    bg = _frame_bg(parent)
    url = (link_url or "").strip()
    if url:
        text_col = ttk.Frame(row)
        text_col.pack(side="left", anchor="w")
        lbl = tk.Label(
            text_col,
            text=text,
            background=bg,
            foreground=_THEME["link"],
            font=("Segoe UI", 10),
            cursor="hand2",
            borderwidth=0,
            padx=0,
            pady=0,
        )
        lbl.pack(anchor="w")
        underline = tk.Canvas(
            text_col,
            height=3,
            highlightthickness=0,
            background=bg,
            borderwidth=0,
        )
        underline.pack(anchor="w", fill="x")

        def _redraw_underline(_event: tk.Event | None = None) -> None:
            underline.delete("all")
            w = max(int(lbl.winfo_reqwidth()), 8)
            underline.configure(width=w)
            underline.create_line(
                0, 1, w, 1, fill=_THEME["link"], dash=(1, 2), width=1
            )

        lbl.bind("<Configure>", _redraw_underline, add="+")
        text_col.after_idle(_redraw_underline)

        def _open_url(_event: tk.Event | None = None) -> None:
            webbrowser.open(url)

        for w in (lbl, underline):
            w.bind("<Button-1>", _open_url, add="+")
        _attach_text_tip([lbl, underline, text_col], url)
    else:
        lbl = tk.Label(
            row,
            text=text,
            background=bg,
            foreground=_THEME["fg"],
            font=("Segoe UI", 10),
            cursor="hand2",
            borderwidth=0,
            padx=0,
            pady=0,
        )
        lbl.pack(side="left", anchor="w")

        def _activate(_event: tk.Event | None = None) -> None:
            if kind == "check":
                variable.set(not bool(variable.get()))
            else:
                variable.set("" if value is None else value)
            if command is not None:
                command()

        lbl.bind("<Button-1>", _activate, add="+")

    return row


def _desc_spacer(parent: tk.Misc) -> None:
    """Visible gap after description / instruction text."""
    gap = tk.Frame(parent, height=10, background=_frame_bg(parent), borderwidth=0)
    gap.pack_propagate(False)
    gap.pack(fill="x")


def _inline_link(parent: tk.Misc, text: str, command: Callable[[], None]) -> tk.Label:
    lbl = tk.Label(
        parent,
        text=text,
        background=_frame_bg(parent),
        foreground=_THEME["link"],
        font=("Segoe UI", 9, "underline"),
        cursor="hand2",
    )
    lbl.bind("<Button-1>", lambda _e: command())
    return lbl


def _path_line(
    parent: tk.Misc,
    label: str,
    path: Path,
    *,
    on_open: Callable[[], None],
) -> None:
    row = ttk.Frame(parent)
    row.pack(fill="x", pady=(2, 0))
    ttk.Label(
        row,
        text=label,
        font=("Segoe UI", 10),
        foreground=_THEME["fg_dim"],
    ).pack(side="left")
    _inline_link(row, _win_path(path), on_open).pack(side="left")


def _title_row(
    parent: tk.Misc,
    name: str,
    *,
    url: str = "",
) -> ttk.Frame:
    """Big title; when ``url:`` is set, the name itself is the dotted link."""
    block = ttk.Frame(parent)
    block.pack(fill="x", pady=(10, 2))
    row = ttk.Frame(block)
    row.pack(anchor="w")
    if url:
        _pack_dotted_link(
            row,
            text=name,
            tip=url,
            command=lambda u=url: webbrowser.open(u),
            font_size=14,
            bold=True,
        ).pack(side="left")
    else:
        ttk.Label(
            row,
            text=name,
            font=("Segoe UI", 14, "bold"),
        ).pack(side="left")
    return block


def _url_display_link(dep: lib.Dependency | None) -> str:
    """Inline link under titles: prefer page urls over download/store."""
    if dep is None:
        return ""
    for candidate in (
        dep.url_moddb,
        dep.url_github,
        dep.url_discord,
        dep.url_download,
        dep.url_kofi,
        dep.url_patreon,
        dep.url,
    ):
        u = (candidate or "").strip()
        if u:
            return u
    return ""


def _icons_dir() -> Path:
    return Path(__file__).resolve().parent / "icons"


def _load_link_icons(master: tk.Misc) -> dict[str, tk.PhotoImage]:
    """Load link PNGs + grey ``*_off`` variants (kept alive on master)."""
    out: dict[str, tk.PhotoImage] = {}
    root_dir = _icons_dir()
    for kind in ("download", "moddb", "github", "discord", "kofi", "patreon"):
        for key, fname in (
            (kind, f"{kind}.png"),
            (f"{kind}_off", f"{kind}_off.png"),
        ):
            path = root_dir / fname
            if not path.is_file():
                continue
            try:
                out[key] = tk.PhotoImage(master=master, file=str(path))
            except tk.TclError:
                continue
    setattr(master, "_dogma_link_icons", out)
    return out


def _pack_blurb(dep: lib.Dependency | None) -> str:
    """Short description only (urls / buy / place handled elsewhere)."""
    if dep is None:
        return ""
    return dep.desc.strip()


def _try_hook_windnd(widget: tk.Misc, on_files: Callable[[list[str]], None]) -> bool:
    """Hook Windows file drag-drop if windnd is available."""
    try:
        import windnd
    except ImportError:
        return False

    def _hook(files: list) -> None:
        paths: list[str] = []
        for f in files:
            if isinstance(f, bytes):
                paths.append(f.decode(sys.getfilesystemencoding(), errors="replace"))
            else:
                paths.append(str(f))
        if paths:
            on_files(paths)

    try:
        windnd.hook_dropfiles(widget, func=_hook)
        return True
    except Exception:
        return False


class _ArchiveDropRegistry:
    """One windnd hook on the wizard root; dispatch to the file box under the cursor."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.fields: list[_ArchiveField] = []
        self.ok = _try_hook_windnd(root, self._dispatch)

    def register(self, field: "_ArchiveField") -> None:
        if field.enable_file:
            self.fields.append(field)

    def _dispatch(self, files: list[str]) -> None:
        try:
            under = self.root.winfo_containing(
                self.root.winfo_pointerx(), self.root.winfo_pointery()
            )
        except tk.TclError:
            return
        while under is not None:
            for field in self.fields:
                if field.contains_widget(under):
                    field._on_drop_files(files)
                    return
            try:
                under = under.master  # type: ignore[assignment]
            except tk.TclError:
                break


def _archive_action_btn(
    parent: tk.Misc,
    *,
    text: str = "",
    image: tk.PhotoImage | None = None,
    command: Callable[[], None] | None = None,
    fg: str | None = None,
    state: str = "normal",
    cursor: str = "hand2",
    font: tuple = ("Segoe UI", 10),
    cell_w: int = 28,
    cell_h: int = 26,
) -> tuple[tk.Frame, tk.Button]:
    """Fixed-size archive action cell with the icon centered inside."""
    bg = _THEME["button_bg"]
    cell = tk.Frame(parent, width=cell_w, height=cell_h, background=bg, bd=0)
    cell.pack_propagate(False)
    kwargs: dict[str, object] = {
        "background": bg,
        "foreground": fg or _THEME["fg"],
        "activebackground": _THEME["button_active"],
        "activeforeground": _THEME["fg"],
        "disabledforeground": _THEME["fg_dim"],
        "relief": "flat",
        "bd": 0,
        "highlightthickness": 0,
        "padx": 0,
        "pady": 0,
        "command": command,
        "state": state,
        "cursor": cursor if state == "normal" else "arrow",
    }
    if image is not None:
        kwargs["image"] = image
        kwargs["text"] = ""
    else:
        kwargs["text"] = text
        kwargs["font"] = font
    btn = tk.Button(cell, **kwargs)  # type: ignore[arg-type]
    btn.place(relx=0.5, rely=0.5, anchor="center")
    return cell, btn


def _pack_mod_select_row(
    parent: tk.Misc,
    *,
    kind: str,
    variable: tk.Variable,
    value: object | None = None,
    command: Callable[[], None] | None = None,
) -> tuple[ttk.Frame, ttk.Frame, ttk.Checkbutton | ttk.Radiobutton]:
    """One checkbox/radio left of a column of archive file boxes.

    Returns ``(row, files_col, indicator)``.
    """
    row = ttk.Frame(parent)
    row.pack(anchor="w", fill="x", pady=(0, 2))
    if kind == "check":
        btn: ttk.Checkbutton | ttk.Radiobutton = ttk.Checkbutton(
            row,
            text="",
            style="Dogma.TCheckbutton",
            variable=variable,
            command=command,
            width=0,
        )
    else:
        btn = ttk.Radiobutton(
            row,
            text="",
            style="Dogma.TRadiobutton",
            variable=variable,
            value="" if value is None else value,
            command=command,
            width=0,
        )
    # Align with the first file box’s vertical center-ish.
    btn.pack(side="left", anchor="n", padx=(0, 8), pady=(5, 0))
    files = ttk.Frame(row)
    files.pack(side="left", fill="x", expand=True)
    return row, files, btn


def _pack_path_install_label(parent: tk.Misc) -> None:
    """Path mods ship in the package — no archive/file box, just Install."""
    ttk.Label(parent, text="Install").pack(anchor="w", pady=(4, 0))


class _ArchiveField:
    """Archive file box: [ⓘ|name|links…|📁|✕] — selection lives outside on the left."""

    # Fixed name column (~40% wider than the old ~400px / 56ch look).
    _NAME_PX = 560

    def __init__(
        self,
        parent: tk.Misc,
        *,
        mo2_root: Path,
        dep: lib.Dependency,
        on_linked: Callable[[str], None] | None = None,
        on_cleared: Callable[[str], None] | None = None,
        enable_file: bool = True,
        info_sections_fn: Callable[[], list[tuple[str, list[str], str]]]
        | None = None,
        drop_registry: _ArchiveDropRegistry | None = None,
        link_icons: dict[str, tk.PhotoImage] | None = None,
        width_chars: int | None = None,
    ) -> None:
        self.mo2_root = Path(mo2_root)
        self.dep = dep
        self.on_linked = on_linked
        self.on_cleared = on_cleared
        self.enable_file = enable_file
        self._busy = False
        self._spin_job: str | None = None
        self._spin_i = 0
        self._has_file = False
        _ = width_chars  # kept for call-site compat; width is pixel-based now

        raised = _THEME["bg_raised"]
        sunken = _THEME["bg_sunken"]
        self.frame = ttk.Frame(parent)
        self.box = tk.Frame(
            self.frame,
            background=raised,
            highlightbackground=_THEME["border"],
            highlightthickness=1,
            bd=0,
        )
        self.box.pack(side="left", anchor="w")

        # Tight vertical padding so the outer box hugs the row height.
        inner = tk.Frame(self.box, background=raised, bd=0)
        inner.pack(fill="x", padx=3, pady=1)

        self._drop_hosts: list[tk.Misc] = [self.box, inner]
        self.check: ttk.Checkbutton | None = None
        self.radio: ttk.Radiobutton | None = None
        self._alert = False

        if info_sections_fn is not None:
            info = _pack_info_icon(inner, info_sections_fn, bg=raised)
            info.pack(side="left", padx=(2, 6))
            self._drop_hosts.append(info)

        name_wrap = tk.Frame(
            inner,
            width=self._NAME_PX,
            background=sunken,
            highlightbackground=_THEME["border"],
            highlightthickness=1,
            bd=0,
        )
        name_wrap.pack(side="left", fill="y", pady=0)
        name_wrap.pack_propagate(False)
        self.name_var = tk.StringVar(value="")
        self.name_lbl = tk.Label(
            name_wrap,
            textvariable=self.name_var,
            background=sunken,
            foreground=_THEME["fg_placeholder"],
            font=("Segoe UI", 9),
            anchor="w",
            padx=6,
            pady=1,
        )
        self.name_lbl.pack(fill="both", expand=True)
        self._drop_hosts.extend([name_wrap, self.name_lbl])
        if enable_file:

            def _browse_click(_event: tk.Event | None = None) -> None:
                self._browse()

            for host in (name_wrap, self.name_lbl):
                host.configure(cursor="hand2")
                host.bind("<Button-1>", _browse_click, add="+")

        btns = tk.Frame(inner, background=raised, bd=0)
        btns.pack(side="right")
        self._drop_hosts.append(btns)

        self.dl_btn: tk.Button | None = None
        self.browse_btn: tk.Button | None = None
        self.clear_btn: tk.Button | None = None
        self._link_btns: list[tk.Button] = []
        self._can_download = False
        self._action = "none"
        self._download_url = ""
        self._dl_image: tk.PhotoImage | None = None
        icons = link_icons or {}

        if enable_file:
            by_kind = {
                kind: (url, mode)
                for kind, url, mode in lib.dep_wizard_link_actions(dep)
            }
            # Custom site icons first, then Windows CLOUDDOWNLOAD, then 📁/✕.
            for kind in (
                "moddb",
                "github",
                "discord",
                "kofi",
                "patreon",
                "download",
            ):
                entry = by_kind.get(kind)
                active = entry is not None
                url = entry[0] if entry else ""
                mode = entry[1] if entry else "open"
                img = icons.get(kind if active else f"{kind}_off") or icons.get(
                    kind
                )
                fallback = {
                    "download": "↓",
                    "moddb": "M",
                    "github": "G",
                    "discord": "D",
                    "kofi": "$",
                    "patreon": "P",
                }.get(kind, "↗")
                # Same button/image path for every link icon (incl. download).
                if active and mode == "download":
                    self._can_download = True
                    self._action = "download"
                    self._download_url = url
                    self._dl_image = img
                    cmd: Callable[[], None] | None = self._start_download
                    st, cur, fg = "normal", "hand2", None
                elif active:
                    cmd = lambda u=url, k=kind: self._open_url(u, k)
                    st, cur, fg = "normal", "hand2", None
                else:
                    cmd = None
                    st, cur, fg = "disabled", "arrow", _THEME["fg_dim"]
                cell, btn = _archive_action_btn(
                    btns,
                    text=fallback if img is None else "",
                    image=img,
                    command=cmd,
                    state=st,
                    cursor=cur,
                    fg=fg,
                    cell_h=28,
                )
                if active and mode == "download":
                    self.dl_btn = btn
                cell.pack(side="left", padx=(4, 0))
                self._link_btns.append(btn)
                self._drop_hosts.extend([cell, btn])

            browse_cell, self.browse_btn = _archive_action_btn(
                btns,
                text="📁",
                command=self._browse,
            )
            browse_cell.pack(side="left", padx=(4, 0))
            self._drop_hosts.extend([browse_cell, self.browse_btn])

            clear_cell, self.clear_btn = _archive_action_btn(
                btns,
                text="✕",
                command=self._clear,
                fg=_THEME["fg_muted"],
            )
            clear_cell.pack(side="left", padx=(4, 0))
            self._drop_hosts.extend([clear_cell, self.clear_btn])

        if enable_file and drop_registry is not None:
            drop_registry.register(self)

        self.refresh()

    def contains_widget(self, widget: tk.Misc | None) -> bool:
        """True when ``widget`` is this file box or a child of it."""
        cur: tk.Misc | None = widget
        while cur is not None:
            if cur is self.frame:
                return True
            try:
                cur = cur.master  # type: ignore[assignment]
            except tk.TclError:
                break
        return False

    def set_alert(self, on: bool) -> None:
        """Red border when this box is an unmet dependency target."""
        self._alert = bool(on)
        try:
            self.box.configure(
                highlightbackground=(
                    _THEME["alert"] if self._alert else _THEME["border"]
                ),
                highlightthickness=1,
            )
        except tk.TclError:
            pass

    def pack(self, **kwargs: object) -> "_ArchiveField":
        self.frame.pack(**kwargs)  # type: ignore[arg-type]
        return self

    def refresh(self) -> None:
        if not self.enable_file:
            self.name_var.set(self.dep.id)
            self.name_lbl.configure(foreground=_THEME["fg_placeholder"])
            self._has_file = False
            return
        path, _st = lib.resolve_local_archive(self.mo2_root, self.dep)
        if path is not None:
            self.name_var.set(path.name)
            self.name_lbl.configure(foreground=_THEME["fg"])
            self._has_file = True
        else:
            self.name_var.set(self.dep.id)
            self.name_lbl.configure(foreground=_THEME["fg_placeholder"])
            self._has_file = False
        if self.clear_btn is not None and not self._busy:
            self.clear_btn.configure(
                state=("normal" if self._has_file else "disabled"),
                foreground=(
                    _THEME["fg_muted"] if self._has_file else _THEME["fg_dim"]
                ),
            )

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        if self.browse_btn is None:
            return
        if busy:
            self.browse_btn.configure(state="disabled")
            if self.clear_btn is not None:
                self.clear_btn.configure(state="disabled")
            for btn in self._link_btns:
                btn.configure(state="disabled")
            if self.dl_btn is not None:
                # Drop image while spinning so the glyph is visible.
                self.dl_btn.configure(state="disabled", image="", text="⏳")
            self._spin_i = 0
            self._tick_spin()
        else:
            if self._spin_job is not None:
                try:
                    self.frame.after_cancel(self._spin_job)
                except (tk.TclError, ValueError):
                    pass
                self._spin_job = None
            self.browse_btn.configure(state="normal")
            for btn in self._link_btns:
                btn.configure(state="normal")
            if self.dl_btn is not None:
                if self._dl_image is not None:
                    self.dl_btn.configure(
                        state="normal",
                        image=self._dl_image,
                        text="",
                        cursor="hand2",
                    )
                else:
                    self.dl_btn.configure(
                        state="normal",
                        text="↓",
                        cursor="hand2",
                    )
            self.refresh()

    def _tick_spin(self) -> None:
        if not self._busy or self.dl_btn is None:
            return
        frames = ("◐", "◓", "◑", "◒")
        self.dl_btn.configure(image="", text=frames[self._spin_i % len(frames)])
        self._spin_i += 1
        self._spin_job = self.frame.after(120, self._tick_spin)

    def _on_drop_files(self, files: list[str]) -> None:
        if self._busy or not self.enable_file or not files:
            return
        self._associate_path(Path(files[0]))

    def _browse(self) -> None:
        if self._busy or not self.enable_file:
            return
        path = filedialog.askopenfilename(
            title=f"Archive for {self.dep.id}",
            filetypes=[
                ("Archives", "*.zip *.7z *.rar *.7zip"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self._associate_path(Path(path))

    def _clear(self) -> None:
        if self._busy or not self.enable_file or not self._has_file:
            return
        try:
            lib.clear_archive_map_entry(self.mo2_root, self.dep.id)
            lib.info(f"Wizard: cleared archive link for [{self.dep.id}]")
        except Exception as exc:
            lib.log_exception(exc, where=f"wizard.clear[{self.dep.id}]")
            messagebox.showerror("D.O.G.M.A.", str(exc))
            return
        self.refresh()
        if self.on_cleared is not None:
            self.on_cleared(self.dep.id)

    def _associate_path(self, src: Path) -> None:
        try:
            lib.associate_archive(self.mo2_root, self.dep.id, src)
        except Exception as exc:
            lib.log_exception(exc, where=f"wizard.associate[{self.dep.id}]")
            messagebox.showerror("D.O.G.M.A.", str(exc))
            return
        self.refresh()
        if self.on_linked is not None:
            self.on_linked(self.dep.id)

    def _open_url(self, url: str, kind: str) -> None:
        u = (url or "").strip()
        if not u:
            return
        lib.info(f"Wizard: {kind} clicked for [{self.dep.id}] → {u}")
        webbrowser.open(u)

    def _start_download(self) -> None:
        if self._busy or not self._can_download:
            return
        self._set_busy(True)
        lib.info(f"Wizard: download clicked for [{self.dep.id}]")

        def _work() -> None:
            err_msg: str | None = None
            try:
                lib.download_and_associate(self.mo2_root, self.dep)
            except Exception as exc:
                lib.log_exception(exc, where=f"wizard.download[{self.dep.id}]")
                err_msg = str(exc)

            def _done() -> None:
                self._set_busy(False)
                if err_msg:
                    messagebox.showerror("D.O.G.M.A.", err_msg)
                    return
                self.refresh()
                if self.on_linked is not None:
                    self.on_linked(self.dep.id)

            try:
                self.frame.after(0, _done)
            except tk.TclError:
                pass

        threading.Thread(target=_work, daemon=True).start()


def _expand_deps_unique(
    roots: list[str],
    pack_by_id: dict[str, lib.Dependency],
) -> list[lib.Dependency]:
    out: list[lib.Dependency] = []
    seen: set[str] = set()

    def _add(mid: str) -> None:
        if mid in seen:
            return
        dep = pack_by_id.get(mid)
        if not dep:
            return
        seen.add(mid)
        out.append(dep)
        try:
            leaves = lib.expand_pack_composition(pack_by_id, mid)
        except ValueError:
            leaves = [mid]
        for lid in leaves:
            if lid == mid:
                continue
            _add(lid)
        for dep_id in dep.depends:
            _add(dep_id)

    for mid in roots:
        _add(mid)
    return out


def run_wizard(
    data: lib.ManifestData,
    *,
    mo2_root: Path,
    initial: lib.InstallerSelection | None = None,
) -> lib.InstallerSelection | None:
    """Checkbox options + pack option radios. None if cancelled."""
    mo2_root = Path(mo2_root)

    options = list(data.installer_options)
    dogma_dl = lib.downloads_dir(mo2_root)
    min_stage = lib.catalog_wizard_min_stage(mo2_root)
    data.installer_options = lib.wizard_options_from_deps(
        data.suggested, min_stage=min_stage
    )
    options = list(data.installer_options)
    if not options and not lib.wizard_radio_groups(data, min_stage=min_stage):
        raise ValueError(
            "config/manifest.yml has no stage:dev|release mods — add stage: "
            "before running the wizard"
        )

    radio_groups = lib.wizard_radio_groups(data, min_stage=min_stage)
    pack_by_id = data.suggested_by_id()
    installed_feats = lib.resolve_installed_features(mo2_root, data)

    if initial is None:
        lib.apply_feature_option_defaults(data, installed_feats)
        # Wizard UI starts with no radio picks — re-click clears; requires:
        # gate Install instead of auto-selecting a choice.
        initial = lib.InstallerSelection(
            option_ids=[o.id for o in options if o.default],
            exclusive_picks={},
        )
    else:
        # Still apply feature defaults for ids not in a prior selection
        lib.apply_feature_option_defaults(data, installed_feats)
    initial_picks = dict(initial.exclusive_picks)

    # Hide the launching console while the Tk wizard is up. Leave it hidden when
    # the wizard closes (cancel → process exits; Install → batch jobs keep logging
    # to the hidden console / log file). Failure paths re-show before pause.
    console_hwnd = _console_hwnd()
    _set_console_visible(console_hwnd, False)
    try:
        return _run_wizard_ui(
            data,
            mo2_root=mo2_root,
            options=options,
            dogma_dl=dogma_dl,
            radio_groups=radio_groups,
            pack_by_id=pack_by_id,
            installed_feats=installed_feats,
            initial=initial,
            initial_picks=initial_picks,
            min_stage=min_stage,
        )
    except Exception:
        _set_console_visible(console_hwnd, True)
        raise


def _run_wizard_ui(
    data: lib.ManifestData,
    *,
    mo2_root: Path,
    options: list[lib.InstallerOption],
    dogma_dl: Path,
    radio_groups: dict[str, list[lib.Dependency]],
    pack_by_id: dict[str, lib.Dependency],
    installed_feats: set[str] | None,
    initial: lib.InstallerSelection,
    initial_picks: dict[str, str],
    min_stage: str,
) -> lib.InstallerSelection | None:
    root = tk.Tk()
    root.title("D.O.G.M.A. Setup")
    _apply_dark_theme(root)
    link_icons = _load_link_icons(root)
    drop_registry = _ArchiveDropRegistry(root)
    if not drop_registry.ok:
        lib.warn(
            "windnd not available — drag-drop onto archive boxes is disabled "
            "(folder button still works)"
        )
    root.update_idletasks()
    screen_w = max(800, int(root.winfo_screenwidth()))
    screen_h = max(600, int(root.winfo_screenheight()))
    # Wider file boxes need a bit more horizontal room; keep tall.
    win_w = min(1100, max(640, int((screen_w - 32) * 0.55))) - 100
    win_w = max(540, win_w)
    win_h = min(1080, screen_h - 64)
    root.minsize(min(640, win_w), min(700, win_h))
    pos_x = max(0, (screen_w - win_w) // 2)
    pos_y = max(0, (screen_h - win_h) // 2)
    root.geometry(f"{win_w}x{win_h}+{pos_x}+{pos_y}")
    col_wrap = max(420, win_w - 80)

    result: dict[str, lib.InstallerSelection | None] = {"selected": None}

    header = ttk.Frame(root, padding=(20, 16, 20, 8))
    header.pack(fill="x")
    ttk.Label(
        header,
        text="D.O.G.M.A. Setup",
        font=("Segoe UI", 14, "bold"),
    ).pack(anchor="w")
    ttk.Label(
        header,
        text="Choose mods to install (third-party and D.O.G.M.A. mods)",
        font=("Segoe UI", 11),
        foreground=_THEME["fg_muted"],
        wraplength=win_w - 80,
    ).pack(anchor="w", pady=(6, 0))

    paths = ttk.Frame(header)
    paths.pack(fill="x", pady=(10, 0))
    _path_line(
        paths,
        "G.A.M.M.A. install path: ",
        mo2_root,
        on_open=lambda: _open_in_explorer(mo2_root),
    )
    _path_line(
        paths,
        "D.O.G.M.A. downloaded mods path: ",
        dogma_dl,
        on_open=lambda: _open_in_explorer(dogma_dl),
    )

    toolbar = ttk.Frame(root, padding=(20, 4, 20, 0))
    toolbar.pack(fill="x")
    sel_btns = ttk.Frame(toolbar)
    sel_btns.pack(side="left")

    body = ttk.Frame(root, padding=(16, 8, 16, 8))
    body.pack(fill="both", expand=True)

    canvas = tk.Canvas(
        body,
        highlightthickness=0,
        background=_THEME["bg"],
        borderwidth=0,
    )
    scroll = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
    inner = ttk.Frame(canvas)
    inner.bind(
        "<Configure>",
        lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
    )
    canvas_win = canvas.create_window((0, 0), window=inner, anchor="nw")

    def _stretch_inner(event: tk.Event) -> None:
        canvas.itemconfigure(canvas_win, width=max(1, event.width))

    canvas.bind("<Configure>", _stretch_inner)
    canvas.configure(yscrollcommand=scroll.set)
    canvas.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")

    def _on_mousewheel(event: tk.Event) -> None:
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    root.bind_all("<MouseWheel>", _on_mousewheel)

    options_col = ttk.Frame(inner)
    options_col.pack(fill="both", expand=True, padx=4, pady=4)

    bool_vars: dict[str, tk.BooleanVar] = {}
    exclusive_vars: dict[str, tk.StringVar] = {}
    group_defaults: dict[str, str] = {}
    page_option_ids: list[str] = []
    for group, packs in radio_groups.items():
        # Preferred fallback when a selected option requires this group — not a
        # default selection (radios start empty unless restored / required).
        default_pack = packs[0].id if packs else ""
        parent = pack_by_id.get(group)
        for opt in options:
            for pack_id in opt.requires:
                if parent is not None and pack_id == parent.id and packs:
                    default_pack = packs[0].id
                    break
                pack = next((p for p in packs if p.id == pack_id), None)
                if pack is not None:
                    default_pack = pack_id
                    break
            else:
                continue
            break
        group_defaults[group] = default_pack
        start = initial_picks.get(group, "")
        if start and start not in {p.id for p in packs}:
            start = ""
        # Fresh open: never auto-select a radio choice.
        exclusive_vars[group] = tk.StringVar(value=start)

    def _add_desc(parent: ttk.Frame, desc: str) -> None:
        _pack_link_text(
            parent,
            _expand_desc(desc, mo2_root=mo2_root, dogma_dl=dogma_dl),
            dogma_dl=dogma_dl,
            wraplength=col_wrap,
            pady=(4, 6),
        )

    # leaf id → all archive boxes (same leaf can appear in multiple radio choices)
    archive_fields: dict[str, list[_ArchiveField]] = {}
    # option id → leaf pack ids that need archives
    option_archive_leaves: dict[str, list[str]] = {}

    def _leaves_linked(leaf_ids: list[str]) -> bool:
        if not leaf_ids:
            return True
        for lid in leaf_ids:
            leaf = pack_by_id.get(lid)
            if leaf is None:
                return False
            path, _st = lib.resolve_local_archive(mo2_root, leaf)
            if path is None:
                return False
        return True

    def _auto_check_for_leaf(leaf_id: str) -> None:
        for oid, leaves in option_archive_leaves.items():
            if leaf_id not in leaves:
                continue
            if not _leaves_linked(leaves):
                continue
            bv = bool_vars.get(oid)
            if bv is not None:
                bv.set(True)
        _on_selection_changed()

    def _leaf_info_sections(
        leaf: lib.Dependency,
    ) -> Callable[[], list[tuple[str, list[str], str]]]:
        feat = data.features.get(leaf.path) if leaf.path else None

        def _sections(
            L: lib.Dependency = leaf,
            f: lib.FeatureMeta | None = feat,
        ) -> list[tuple[str, list[str], str]]:
            return _tooltip_sections_for(
                [L],
                feature=f if L.path else None,
                path_pack=L if L.path else None,
            )

        return _sections

    def _add_archive_fields(
        parent: ttk.Frame,
        deps: list[lib.Dependency],
        *,
        option_id: str | None = None,
    ) -> list[lib.Dependency]:
        leaves: list[lib.Dependency] = []
        seen: set[str] = set()
        for dep in deps:
            for leaf in lib.archive_leaves_for_pack(pack_by_id, dep.id):
                if leaf.id in seen:
                    continue
                seen.add(leaf.id)
                leaves.append(leaf)
        if option_id is not None:
            option_archive_leaves[option_id] = [L.id for L in leaves]
        if not leaves:
            return []

        for leaf in leaves:
            field = _ArchiveField(
                parent,
                mo2_root=mo2_root,
                dep=leaf,
                on_linked=_auto_check_for_leaf,
                info_sections_fn=_leaf_info_sections(leaf),
                drop_registry=drop_registry,
                link_icons=link_icons,
            )
            field.pack(anchor="w", pady=(0, 4))
            archive_fields.setdefault(leaf.id, []).append(field)
        return leaves

    def _catalog_sections_for(
        deps: list[lib.Dependency],
        *,
        feature: lib.FeatureMeta | None = None,
        requires: list[str] | None = None,
        path_pack: lib.Dependency | None = None,
    ) -> list[tuple[str, list[str], str]]:
        sections: list[tuple[str, list[str], str]] = []
        if requires:
            sections.append(("Requires", list(requires), "normal"))
        if path_pack is not None and path_pack.path:
            zname = f"{lib.feature_path_key(path_pack.path)}.zip"
            sections.append(
                ("Installs", [f"local package {zname} ({path_pack.path})"], "normal")
            )
            if path_pack.depends:
                sections.append(("Depends", list(path_pack.depends), "normal"))
            if feature is not None:
                for label, items in lib.preview_feature_effect_sections(
                    feature, mo2_root=mo2_root
                ):
                    sections.append((label, items, "normal"))
            # Depends-only packs (skip the path pack itself — covered by feature rows).
            depend_deps = [d for d in deps if d.id != path_pack.id]
            for label, items in _effect_deps_for(depend_deps):
                if label == "Installs":
                    sections.append(("Installs (depends)", items, "normal"))
                else:
                    sections.append((label, items, "normal"))
        else:
            for label, items in _effect_deps_for(deps):
                sections.append((label, items, "normal"))
        return sections

    def _effect_deps_for(
        deps: list[lib.Dependency],
    ) -> list[tuple[str, list[str]]]:
        installs = lib.preview_install_packs(deps, pack_by_id)
        sections: list[tuple[str, list[str]]] = []
        if installs:
            sections.append(("Installs", installs))
        sections.extend(lib.preview_effect_sections(deps, mo2_root=mo2_root))
        return sections

    def _expected_for(
        deps: list[lib.Dependency],
        *,
        feature: lib.FeatureMeta | None = None,
    ) -> list[str]:
        return lib.preview_expected_changes(
            mo2_root,
            deps,
            pack_by_id=pack_by_id,
            enabled_names=enabled_mods,
            disabled_names=disabled_mods,
            feature=feature,
        )

    def _tooltip_sections_for(
        deps: list[lib.Dependency],
        *,
        feature: lib.FeatureMeta | None = None,
        requires: list[str] | None = None,
        path_pack: lib.Dependency | None = None,
    ) -> list[tuple[str, list[str], str]]:
        sections = _catalog_sections_for(
            deps, feature=feature, requires=requires, path_pack=path_pack
        )
        expected = _expected_for(deps, feature=feature)
        if expected:
            sections.append(("Expected changes", expected, "expected"))
        return sections

    enabled_mods: list[str] = []
    disabled_mods: list[str] = []
    try:
        modlist = lib.modlist_path(mo2_root)
        for flag, name in lib.list_modlist_entries(modlist):
            if flag == "+":
                enabled_mods.append(name)
            elif flag == "-":
                disabled_mods.append(name)
    except (OSError, FileNotFoundError, ValueError):
        enabled_mods = []
        disabled_mods = []

    def _deps_for_option(opt: lib.InstallerOption) -> list[lib.Dependency]:
        if opt.id in data.features:
            return data.feature_pack_deps(opt.id)
        return _expand_deps_unique(list(opt.mods), pack_by_id)

    def _preview_deps_for_choice(
        group: str, choice: lib.Dependency
    ) -> list[lib.Dependency]:
        roots = [group, choice.id] if group in pack_by_id else [choice.id]
        return _expand_deps_unique(roots, pack_by_id)

    def _selected_option_ids() -> list[str]:
        return [oid for oid, bv in bool_vars.items() if bv.get()]

    opt_by_id = {o.id: o for o in options}
    install_btn_holder: dict[str, ttk.Button] = {}
    depends_warn_var = tk.StringVar(value="")

    def _unmet_dependency_leaf_ids() -> set[str]:
        """Depend leaf ids missing archives for selected checkbox mods.

        Empty radios are valid (no red). Red only when a selected mod has
        ``depends:`` archives that still need linking.
        """
        unmet: set[str] = set()
        for oid in _selected_option_ids():
            leaves = option_archive_leaves.get(oid) or []
            if not leaves:
                continue
            primary = oid if oid in leaves else leaves[0]
            for lid in leaves:
                if lid == primary:
                    continue
                leaf = pack_by_id.get(lid)
                if leaf is None:
                    unmet.add(lid)
                    continue
                path, _st = lib.resolve_local_archive(mo2_root, leaf)
                if path is None:
                    unmet.add(lid)
        return unmet

    def _on_selection_changed(*_args: object) -> None:
        unmet = _unmet_dependency_leaf_ids()
        for lid, fields in archive_fields.items():
            on = lid in unmet
            for field in fields:
                field.set_alert(on)
        blocked = bool(unmet)
        btn = install_btn_holder.get("btn")
        if btn is not None:
            try:
                btn.configure(state=("disabled" if blocked else "normal"))
            except tk.TclError:
                pass
        depends_warn_var.set(
            "You must also install dependencies for selected mods"
            if blocked
            else ""
        )

    def _select_all() -> None:
        for oid in page_option_ids:
            bv = bool_vars.get(oid)
            if bv is not None:
                bv.set(True)
        _on_selection_changed()

    def _deselect_all() -> None:
        for oid in page_option_ids:
            bv = bool_vars.get(oid)
            if bv is not None:
                bv.set(False)
        for var in exclusive_vars.values():
            var.set("")
        _on_selection_changed()

    ttk.Button(sel_btns, text="Select all", command=_select_all).pack(
        side="left", padx=(0, 6)
    )
    ttk.Button(sel_btns, text="Deselect all", command=_deselect_all).pack(side="left")

    def _add_option_block(opt: lib.InstallerOption) -> None:
        pack = pack_by_id.get(opt.id)
        feat = data.features.get(opt.id)
        if pack is not None and pack.path:
            feat = data.features.get(pack.path) or feat
        title = opt.id
        url = ""
        if pack is not None:
            url = _url_display_link(pack)
            if pack.path:
                title = lib.with_dogma_prefix(pack.id)
            elif not title:
                title = pack.id
        elif feat is not None:
            title = feat.display_name

        _title_row(options_col, title, url=url)
        frame = ttk.Frame(options_col, padding=(4, 4))
        frame.pack(fill="x", pady=(0, 8), padx=2)
        prior = set(initial.option_ids) if initial is not None else set()
        if initial is not None and prior:
            checked = opt.id in prior
        else:
            checked = opt.default

        bv = tk.BooleanVar(value=checked)
        bool_vars[opt.id] = bv
        page_option_ids.append(opt.id)

        _row, files_col, _chk = _pack_mod_select_row(
            frame,
            kind="check",
            variable=bv,
            command=_on_selection_changed,
        )

        is_path_mod = (pack is not None and bool(pack.path)) or (
            pack is None and feat is not None
        )
        leaves: list[lib.Dependency] = []
        if is_path_mod:
            # Packaged path: mods — no links / file box.
            _pack_path_install_label(files_col)
            if pack is not None:
                _add_desc(frame, _pack_blurb(pack))
                _desc_spacer(frame)
            elif opt.desc.strip():
                _add_desc(frame, opt.desc)
                _desc_spacer(frame)
        elif pack is not None:
            leaves = _add_archive_fields(
                files_col,
                [pack],
                option_id=opt.id,
            )

        # No-archive non-path options: checkbox + name-only box.
        if not is_path_mod and not leaves:
            select_dep = pack
            if select_dep is None:
                select_dep = lib.Dependency(
                    id=opt.id, tier="suggested", source="user"
                )
            field = _ArchiveField(
                files_col,
                mo2_root=mo2_root,
                dep=select_dep,
                enable_file=False,
                info_sections_fn=_leaf_info_sections(select_dep)
                if pack is not None
                else (
                    lambda o=opt, f=feat: _tooltip_sections_for(
                        _deps_for_option(o),
                        feature=f,
                        requires=list(o.requires),
                    )
                ),
            )
            field.pack(anchor="w", pady=(0, 4))

        # Fresh wizard: auto-check when archives are already linked.
        linked_leaves = option_archive_leaves.get(opt.id) or []
        if (
            linked_leaves
            and _leaves_linked(linked_leaves)
            and (initial is None or not prior)
        ):
            bv.set(True)

    def _add_or_separator(parent: ttk.Frame) -> None:
        # Quiet gap between radio choices (no "OR" label).
        ttk.Frame(parent, height=4).pack(fill="x", pady=(2, 2))

    def _add_radio_block(group: str) -> None:
        packs = radio_groups[group]
        parent = pack_by_id.get(group)
        title = lib.radio_group_title(group, packs, parent=parent)
        parent_url = _url_display_link(parent)
        _title_row(options_col, title, url=parent_url)
        frame = ttk.Frame(options_col, padding=(4, 4))
        frame.pack(fill="x", pady=(0, 8), padx=2)
        var = exclusive_vars[group]
        var.trace_add("write", lambda *_a: _on_selection_changed())

        if parent is not None and parent.desc.strip():
            _add_desc(frame, parent.desc)
            _desc_spacer(frame)

        def _bind_radio_reclick(
            btn: ttk.Radiobutton, pack_id: str
        ) -> None:
            """Re-clicking the selected radio clears the pick (none)."""

            def _press(_event: tk.Event | None = None) -> str | None:
                if var.get() == pack_id:
                    var.set("")
                    return "break"
                return None

            btn.bind("<Button-1>", _press, add="+")

        for i, pack in enumerate(packs):
            if i > 0:
                _add_or_separator(frame)

            _row, files_col, radio_btn = _pack_mod_select_row(
                frame,
                kind="radio",
                variable=var,
                value=pack.id,
                command=_on_selection_changed,
            )
            _bind_radio_reclick(radio_btn, pack.id)

            step_deps: list[lib.Dependency] = []
            try:
                leaf_ids = lib.expand_pack_composition(pack_by_id, pack.id)
            except ValueError:
                leaf_ids = [pack.id]
            for lid in leaf_ids:
                leaf = pack_by_id.get(lid)
                if leaf is not None:
                    step_deps.append(leaf)

            # Path choices: no file box. File boxes do not toggle the radio.
            if pack.path:
                _pack_path_install_label(files_col)
            else:
                leaves = _add_archive_fields(files_col, step_deps)
                if not leaves:
                    label = pack.choice or pack.id
                    pick_dep = lib.Dependency(
                        id=label, tier="suggested", source="user"
                    )
                    field = _ArchiveField(
                        files_col,
                        mo2_root=mo2_root,
                        dep=pick_dep,
                        enable_file=False,
                        info_sections_fn=lambda g=group, p=pack: _tooltip_sections_for(
                            _preview_deps_for_choice(g, p)
                        ),
                    )
                    field.pack(anchor="w", pady=(0, 4))

    for kind, sid in lib.wizard_section_order(data, min_stage=min_stage):
        if kind == "radio":
            _add_radio_block(sid)
        else:
            opt = opt_by_id.get(sid)
            if opt is not None:
                _add_option_block(opt)

    def collect() -> lib.InstallerSelection:
        picks = {g: v.get() for g, v in exclusive_vars.items()}
        return lib.InstallerSelection(
            option_ids=_selected_option_ids(),
            exclusive_picks=picks,
            features_chosen=True,
        )

    def _cleanup_binds() -> None:
        try:
            root.unbind_all("<MouseWheel>")
        except tk.TclError:
            pass

    def on_install() -> None:
        chosen = collect()
        if not chosen.option_ids and not any(chosen.exclusive_picks.values()):
            messagebox.showwarning(
                "D.O.G.M.A.",
                "Select at least one option.",
            )
            return
        if _unmet_dependency_leaf_ids():
            messagebox.showwarning(
                "D.O.G.M.A.",
                "You must also install dependencies for selected mods.",
            )
            _on_selection_changed()
            return
        missing = lib.missing_archives_for_selection(mo2_root, data, chosen)
        if missing:
            preview = ", ".join(missing[:8])
            extra = f" (+{len(missing) - 8} more)" if len(missing) > 8 else ""
            lib.warn(
                f"Install blocked — missing archives: {', '.join(missing)}"
            )
            messagebox.showwarning(
                "D.O.G.M.A.",
                "These selected mods still need an archive linked "
                f"(download, $, or folder):\n\n{preview}{extra}",
            )
            return
        try:
            lib.resolve_install_order(
                data,
                chosen.option_ids,
                chosen.exclusive_picks,
                installed=installed_feats,
            )
        except ValueError as exc:
            messagebox.showerror("D.O.G.M.A.", str(exc))
            return
        result["selected"] = chosen
        _cleanup_binds()
        root.destroy()

    def on_cancel() -> None:
        result["selected"] = None
        _cleanup_binds()
        root.destroy()

    footer = ttk.Frame(root, padding=(20, 10, 20, 16))
    footer.pack(fill="x")
    ttk.Label(
        footer,
        textvariable=depends_warn_var,
        foreground=_THEME["alert"],
        font=("Segoe UI", 9),
        anchor="w",
        justify="left",
    ).pack(side="left", fill="x", expand=True, padx=(0, 12))
    install_btn = ttk.Button(footer, text="Install", command=on_install)
    install_btn.pack(side="right")
    install_btn_holder["btn"] = install_btn
    _on_selection_changed()

    root.protocol("WM_DELETE_WINDOW", on_cancel)
    root.mainloop()
    _cleanup_binds()
    return result["selected"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="DOGMA suggested-mods wizard")
    p.add_argument("--mo2-root", default="")
    p.add_argument("--config-dir", default="")
    args, _unknown = p.parse_known_args(argv)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    mo2 = lib.resolve_mo2_root(args.mo2_root or None)
    cfg = lib.resolve_config_dir(
        mo2, Path(args.config_dir) if args.config_dir else None
    )
    data = lib.load_manifest(lib.resolve_manifest_path(cfg))
    prev = lib.load_installer_selection(mo2)
    selected = run_wizard(data, mo2_root=mo2, initial=prev)
    if selected is None:
        lib.warn("Wizard cancelled")
        return 2
    lib.save_installer_selection(mo2, selected)
    installed = lib.resolve_installed_features(mo2, data)
    ordered = lib.resolve_install_order(
        data,
        selected.option_ids,
        selected.exclusive_picks,
        installed=installed,
    )
    lib.info(f"Selected: {', '.join(selected.option_ids)}")
    if selected.exclusive_picks:
        lib.info(
            "Exclusive: "
            + ", ".join(
                f"{g}={p or 'None'}" for g, p in selected.exclusive_picks.items()
            )
        )
    lib.info(f"Install order ({len(ordered)}): " + ", ".join(d.id for d in ordered))
    print("\n".join(selected.option_ids))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
