#!/usr/bin/env python3
"""DOGMA Setup wizard — pick options from config/mods.yml + feature requires."""

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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import dogma_mo2_lib as lib

_URL_RE = re.compile(r"https?://[^\s\]\)>,;]+")

# Concurrent download jobs (ModDB resolve overlaps; MO2 CLI is locked in lib).
_DOWNLOAD_WORKERS = 4

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
        r"(?i)(?:[A-Za-z]:[/\\](?:[^/\\\s]+[/\\])*?)?(?:downloads[/\\]DOGMA|DOGMA-downloads)",
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
    # Toolbar page/select buttons — ~20% shorter than footer TButton.
    _tb_pad = (12, 4)
    style.configure(
        "Toolbar.TButton",
        background=btn,
        foreground=fg,
        bordercolor=border,
        focuscolor=border,
        lightcolor=btn,
        darkcolor=btn,
        padding=_tb_pad,
    )
    style.map(
        "Toolbar.TButton",
        background=[("active", btn_active), ("pressed", raised)],
        foreground=[("disabled", muted)],
    )
    # Wizard page tabs: selected = white text + 1px white border; idle = grey text.
    style.configure(
        "Page.TButton",
        background=btn,
        foreground=muted,
        bordercolor=border,
        focuscolor=border,
        lightcolor=btn,
        darkcolor=btn,
        borderwidth=1,
        relief="solid",
        padding=_tb_pad,
    )
    style.map(
        "Page.TButton",
        background=[("active", btn_active), ("pressed", raised)],
        foreground=[("active", muted), ("pressed", muted)],
        bordercolor=[("active", border)],
        lightcolor=[("active", btn), ("pressed", btn)],
        darkcolor=[("active", btn), ("pressed", btn)],
    )
    style.configure(
        "PageSelected.TButton",
        background=btn,
        foreground=fg,
        bordercolor="#ffffff",
        focuscolor="#ffffff",
        lightcolor="#ffffff",
        darkcolor="#ffffff",
        borderwidth=1,
        relief="solid",
        padding=_tb_pad,
    )
    style.map(
        "PageSelected.TButton",
        background=[("active", btn_active), ("pressed", raised)],
        foreground=[("active", fg), ("pressed", fg), ("disabled", fg)],
        bordercolor=[("active", "#ffffff"), ("pressed", "#ffffff"), ("disabled", "#ffffff")],
        lightcolor=[("active", "#ffffff"), ("pressed", "#ffffff"), ("disabled", "#ffffff")],
        darkcolor=[("active", "#ffffff"), ("pressed", "#ffffff"), ("disabled", "#ffffff")],
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
        box.tag_configure("alert", foreground=_THEME["alert"])
        first = True
        for label, items, kind in sections:
            if kind == "expected":
                tag = "expected"
            elif kind == "alert":
                tag = "alert"
            else:
                tag = "normal"
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
    image: tk.PhotoImage | None = None,
) -> tk.Label:
    """Filled ⓘ; hover shows what this mod does."""
    bg = bg if bg is not None else _frame_bg(parent)
    img = image or _filled_info_image(parent.winfo_toplevel(), bg=bg)
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


def _icons_dir() -> Path:
    return Path(__file__).resolve().parent / "icons"


def _tint_png_photo(
    master: tk.Misc,
    path: Path,
    rgb: tuple[int, int, int],
) -> tk.PhotoImage | None:
    """Recolor light icon strokes to ``rgb`` (update-available download mark)."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        im = Image.open(path).convert("RGBA")
    except OSError:
        return None
    px = im.load()
    w, h = im.size
    tr, tg, tb = rgb
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a and max(r, g, b) > 40:
                px[x, y] = (tr, tg, tb, a)
    import base64
    import io

    buf = io.BytesIO()
    im.save(buf, format="PNG")
    try:
        return tk.PhotoImage(
            master=master, data=base64.b64encode(buf.getvalue())
        )
    except tk.TclError:
        return None


def _hex_rgb(color: str) -> tuple[int, int, int] | None:
    raw = (color or "").lstrip("#")
    if len(raw) != 6:
        return None
    try:
        return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))
    except ValueError:
        return None


def _load_link_icons(master: tk.Misc) -> dict[str, tk.PhotoImage]:
    """Load link PNGs + download colour states (white/green/blue/grey)."""
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
    dl_path = root_dir / "download.png"
    if dl_path.is_file():
        # white = up to date (download.png); green = newer; blue = unknown
        for key, hex_color in (
            ("download_new", _THEME["accent"]),
            ("download_unknown", _THEME["link"]),
        ):
            rgb = _hex_rgb(hex_color)
            if rgb is None:
                continue
            tinted = _tint_png_photo(master, dl_path, rgb)
            if tinted is not None:
                out[key] = tinted
        # grey "no url" reuses download_off when present; else tint fg_dim
        if "download_off" not in out:
            rgb = _hex_rgb(_THEME["fg_dim"])
            if rgb is not None:
                tinted = _tint_png_photo(master, dl_path, rgb)
                if tinted is not None:
                    out["download_off"] = tinted
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


class _DownloadHub:
    """Worker pool for parallel wizard downloads (one job per pack id)."""

    def __init__(self, root: tk.Tk, *, max_workers: int = _DOWNLOAD_WORKERS) -> None:
        self._root = root
        self._max_workers = max(1, max_workers)
        self._ex = ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix="dogma-dl",
        )
        self._lock = threading.Lock()
        self._inflight: set[str] = set()
        self._fields: list[_ArchiveField] = []

    def register(self, field: "_ArchiveField") -> None:
        self._fields.append(field)

    def at_capacity(self) -> bool:
        with self._lock:
            return len(self._inflight) >= self._max_workers

    def refresh_gates(self) -> None:
        """Disable idle download buttons when all worker slots are busy."""
        blocked = self.at_capacity()
        for field in self._fields:
            field.apply_download_gate(blocked=blocked)

    def submit(
        self,
        dep_id: str,
        work: Callable[[], None],
        on_done: Callable[[BaseException | None], None],
    ) -> bool:
        """Start ``work`` on a free worker. False if in flight or pool full."""
        kid = str(dep_id).strip()
        with self._lock:
            if not kid or kid in self._inflight:
                return False
            if len(self._inflight) >= self._max_workers:
                return False
            self._inflight.add(kid)

        def _run() -> None:
            err: BaseException | None = None
            try:
                work()
            except BaseException as exc:
                err = exc

            def _ui() -> None:
                with self._lock:
                    self._inflight.discard(kid)
                on_done(err)
                self.refresh_gates()

            try:
                self._root.after(0, _ui)
            except tk.TclError:
                with self._lock:
                    self._inflight.discard(kid)

        self._ex.submit(_run)
        try:
            self._root.after(0, self.refresh_gates)
        except tk.TclError:
            pass
        return True

    def shutdown(self) -> None:
        try:
            self._ex.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            # Python < 3.9 has no cancel_futures
            self._ex.shutdown(wait=False)


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
        download_hub: _DownloadHub | None = None,
        width_chars: int | None = None,
    ) -> None:
        self.mo2_root = Path(mo2_root)
        self.dep = dep
        self.on_linked = on_linked
        self.on_cleared = on_cleared
        self.enable_file = enable_file
        self._download_hub = download_hub
        self._busy = False
        self._spin_job: str | None = None
        self._spin_i = 0
        self._has_file = False
        self._progress = 0.0  # 0..1 while downloading; 0 when idle
        self._prog_pending: tuple[int, int] | None = None
        self._prog_job: str | None = None
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
        self._info_lbl: tk.Label | None = None
        self._info_img_normal: tk.PhotoImage | None = None
        self._info_img_alert: tk.PhotoImage | None = None

        if info_sections_fn is not None:
            top = self.frame.winfo_toplevel()
            self._info_img_normal = _filled_info_image(
                top, bg=raised, fill=_THEME["link"]
            )
            self._info_img_alert = _filled_info_image(
                top, bg=raised, fill=_THEME["alert"]
            )
            info = _pack_info_icon(
                inner,
                info_sections_fn,
                bg=raised,
                image=self._info_img_normal,
            )
            info.pack(side="left", padx=(2, 6))
            self._info_lbl = info
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
        # Canvas so a blue L→R fill can sit behind the filename text.
        self.name_canvas = tk.Canvas(
            name_wrap,
            background=sunken,
            highlightthickness=0,
            bd=0,
            height=22,
        )
        self.name_canvas.pack(fill="both", expand=True)
        self._prog_rect = self.name_canvas.create_rectangle(
            0, 0, 0, 22, fill=_THEME["link"], outline="", tags=("prog",)
        )
        self._name_text = self.name_canvas.create_text(
            6,
            11,
            text="",
            anchor="w",
            fill=_THEME["fg_placeholder"],
            font=("Segoe UI", 9),
            tags=("name",),
        )
        # Keep Label API used by tip helpers / older call sites as an alias.
        self.name_lbl = self.name_canvas
        self._drop_hosts.extend([name_wrap, self.name_canvas])
        self.name_var.trace_add("write", lambda *_a: self._paint_name())
        self.name_canvas.bind("<Configure>", lambda _e: self._paint_name(), add="+")
        if enable_file:

            def _browse_click(_event: tk.Event | None = None) -> None:
                self._browse()

            for host in (name_wrap, self.name_canvas):
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
        self._dl_images: dict[str, tk.PhotoImage | None] = {}
        # new | current | unknown | none — see set_download_status
        self._dl_status = "none"
        self._remote_date = ""
        self._remote_probed = False
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
                if kind == "download":
                    self._dl_images = {
                        "new": icons.get("download_new"),
                        "current": icons.get("download"),
                        "unknown": icons.get("download_unknown")
                        or icons.get("download"),
                        "none": icons.get("download_off") or icons.get("download"),
                    }
                    if active and mode == "download":
                        self._can_download = True
                        self._action = "download"
                        self._download_url = url
                        self._dl_status = "unknown"
                        self._dl_image = self._dl_images.get("unknown")
                        img = self._dl_image or img
                        cmd: Callable[[], None] | None = self._start_download
                        st, cur, fg = "normal", "hand2", None
                    else:
                        # No auto-download URL — darker grey, disabled.
                        self._dl_status = "none"
                        self._dl_image = self._dl_images.get("none")
                        img = self._dl_image or img
                        cmd = None
                        st, cur, fg = "disabled", "arrow", _THEME["fg_dim"]
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
                if kind == "download":
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
        if download_hub is not None:
            download_hub.register(self)

        self.refresh()
        if download_hub is not None:
            self.apply_download_gate(blocked=download_hub.at_capacity())

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
        """Red file-box border (+ red ⓘ) when this selected box needs an archive."""
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
        if self._info_lbl is not None:
            img = (
                self._info_img_alert
                if self._alert
                else self._info_img_normal
            )
            if img is not None:
                try:
                    self._info_lbl.configure(image=img)
                    self._info_lbl._dogma_info_img = img  # type: ignore[attr-defined]
                except tk.TclError:
                    pass

    def pack(self, **kwargs: object) -> "_ArchiveField":
        self.frame.pack(**kwargs)  # type: ignore[arg-type]
        return self

    def _name_fg(self) -> str:
        if self._busy:
            return _THEME["fg"]
        if self._has_file:
            return _THEME["link"]
        return _THEME["fg_placeholder"]

    def _paint_name(self) -> None:
        """Redraw name text + optional blue progress fill behind it."""
        try:
            w = max(int(self.name_canvas.winfo_width()), 1)
            h = max(int(self.name_canvas.winfo_height()), 1)
        except tk.TclError:
            return
        frac = max(0.0, min(1.0, self._progress if self._busy else 0.0))
        self.name_canvas.coords(self._prog_rect, 0, 0, int(w * frac), h)
        if frac <= 0:
            self.name_canvas.itemconfigure(self._prog_rect, state="hidden")
        else:
            self.name_canvas.itemconfigure(self._prog_rect, state="normal")
        self.name_canvas.coords(self._name_text, 6, h // 2)
        self.name_canvas.itemconfigure(
            self._name_text,
            text=self.name_var.get(),
            fill=self._name_fg(),
        )
        self.name_canvas.tag_raise(self._name_text)

    def _progress_fraction(self, done: int, total: int) -> float:
        if total and total > 0:
            return max(0.0, min(1.0, done / total))
        # Unknown size: asymptotic fill that approaches ~92%.
        if done <= 0:
            return 0.0
        return max(0.0, min(0.92, done / (done + 8 * 1024 * 1024)))

    def _queue_progress(self, done: int, total: int) -> None:
        """Worker-thread entry: coalesce UI updates onto the Tk thread."""
        self._prog_pending = (int(done), int(total))
        if self._prog_job is not None:
            return
        try:
            self._prog_job = self.frame.after(33, self._flush_progress)
        except tk.TclError:
            self._prog_job = None

    def _flush_progress(self) -> None:
        self._prog_job = None
        pending = self._prog_pending
        if pending is None or not self._busy:
            return
        done, total = pending
        self._progress = self._progress_fraction(done, total)
        self._paint_name()

    def refresh(self) -> None:
        if not self.enable_file:
            self.name_var.set(self.dep.id)
            self._has_file = False
            if not self._busy:
                self._progress = 0.0
            self._paint_name()
            return
        path, _status = lib.resolve_local_archive(
            self.mo2_root,
            self.dep,
            remote_date=self._remote_date,
        )
        if path is not None:
            self.name_var.set(path.name)
            self._has_file = True
        else:
            self.name_var.set(self.dep.id)
            self._has_file = False
        if not self._busy:
            self._progress = 0.0
        self._paint_name()
        if self._can_download:
            self.set_download_status(
                lib.download_version_icon_status(
                    self.mo2_root,
                    self.dep,
                    remote_date=self._remote_date,
                    probed=self._remote_probed,
                )
            )
        if self.clear_btn is not None and not self._busy:
            self.clear_btn.configure(
                state=("normal" if self._has_file else "disabled"),
                foreground=(
                    _THEME["fg_muted"] if self._has_file else _THEME["fg_dim"]
                ),
            )

    def set_download_status(self, status: str) -> None:
        """Colour download icon: new=green, current=white, unknown=blue, none=grey."""
        key = status if status in ("new", "current", "unknown", "none") else "unknown"
        self._dl_status = key
        self._dl_image = self._dl_images.get(key) or self._dl_images.get("unknown")
        if self.dl_btn is None or self._busy:
            return
        if key == "none":
            try:
                if self._dl_image is not None:
                    self.dl_btn.configure(
                        state="disabled",
                        image=self._dl_image,
                        text="",
                        cursor="arrow",
                    )
                else:
                    self.dl_btn.configure(
                        state="disabled",
                        text="↓",
                        cursor="arrow",
                        foreground=_THEME["fg_dim"],
                    )
            except tk.TclError:
                pass
            return
        hub = self._download_hub
        blocked = hub.at_capacity() if hub is not None else False
        self.apply_download_gate(blocked=blocked)

    def apply_download_gate(self, *, blocked: bool) -> None:
        """When the download pool is full, idle cloud buttons stay disabled."""
        if self.dl_btn is None or not self._can_download or self._busy:
            return
        try:
            if blocked:
                self.dl_btn.configure(state="disabled", cursor="arrow")
            elif self._dl_image is not None:
                self.dl_btn.configure(
                    state="normal",
                    image=self._dl_image,
                    text="",
                    cursor="hand2",
                )
            else:
                fg = {
                    "new": _THEME["accent"],
                    "current": _THEME["fg"],
                    "unknown": _THEME["link"],
                    "none": _THEME["fg_dim"],
                }.get(self._dl_status, _THEME["link"])
                self.dl_btn.configure(
                    state="normal",
                    text="↓",
                    cursor="hand2",
                    foreground=fg,
                )
        except tk.TclError:
            pass

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        if self.browse_btn is None:
            return
        if busy:
            self._progress = 0.0
            self._prog_pending = (0, 0)
            self._paint_name()
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
            if self._prog_job is not None:
                try:
                    self.frame.after_cancel(self._prog_job)
                except (tk.TclError, ValueError):
                    pass
                self._prog_job = None
            self._prog_pending = None
            self._progress = 0.0
            self.browse_btn.configure(state="normal")
            for btn in self._link_btns:
                btn.configure(state="normal")
            self.refresh()
            hub = self._download_hub
            self.apply_download_gate(
                blocked=(hub.at_capacity() if hub is not None else False)
            )

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
            lib.associate_archive(
                self.mo2_root,
                self.dep.id,
                src,
                date=self._remote_date,
            )
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
        hub = self._download_hub
        if hub is None:
            return
        lib.info(f"Wizard: download clicked for [{self.dep.id}]")

        def _work() -> None:
            lib.download_and_associate(
                self.mo2_root,
                self.dep,
                on_progress=self._queue_progress,
            )

        def _done(err: BaseException | None) -> None:
            self._set_busy(False)
            if err is not None:
                lib.log_exception(err, where=f"wizard.download[{self.dep.id}]")
                messagebox.showerror("D.O.G.M.A.", str(err))
                return
            self.refresh()
            if self.on_linked is not None:
                self.on_linked(self.dep.id)

        if not hub.submit(self.dep.id, _work, _done):
            # Already downloading this pack, or all worker slots are busy.
            return
        self._set_busy(True)


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
        for dep_id in dep.requires:
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
            "config manifests have no stage:dev|release mods — add stage: "
            "before running the wizard"
        )

    radio_groups = lib.wizard_radio_groups(data, min_stage=min_stage)
    pack_by_id = data.suggested_by_id()
    installed_feats = lib.resolve_installed_features(mo2_root, data)
    pruned = lib.prune_missing_archive_map(mo2_root)
    if pruned:
        lib.info(
            f"Cleared {len(pruned)} missing archive map "
            f"entr{'y' if len(pruned) == 1 else 'ies'}"
        )

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
    download_hub = _DownloadHub(root)
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
    subtitle_var = tk.StringVar(
        value="Choose third-party mods to install"
    )

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
    sel_btns.pack(side="right")
    page_bar = ttk.Frame(toolbar)
    page_bar.pack(side="left", fill="x", expand=True)

    page_tab_btns: dict[int, ttk.Button] = {}

    body = ttk.Frame(root, padding=(16, 8, 16, 8))
    body.pack(fill="both", expand=True)

    ttk.Label(
        body,
        textvariable=subtitle_var,
        font=("Segoe UI", 11),
        foreground=_THEME["fg_muted"],
        wraplength=win_w - 80,
    ).pack(anchor="w", pady=(0, 6))

    scroll_border = tk.Frame(
        body,
        background=_THEME["bg"],
        highlightbackground="#000000",
        highlightcolor="#000000",
        highlightthickness=1,
        bd=0,
    )
    scroll_border.pack(fill="both", expand=True)

    canvas = tk.Canvas(
        scroll_border,
        highlightthickness=0,
        background=_THEME["bg"],
        borderwidth=0,
    )
    scroll = ttk.Scrollbar(scroll_border, orient="vertical", command=canvas.yview)
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

    page1_col = ttk.Frame(inner)
    page2_col = ttk.Frame(inner)
    page3_col = ttk.Frame(inner)
    page1_col.pack(fill="both", expand=True, padx=4, pady=4)

    bool_vars: dict[str, tk.BooleanVar] = {}
    check_btns: dict[str, ttk.Checkbutton] = {}
    # Path-mod ⓘ next to checkbox (features/tweaks): label → alert images.
    path_info_icons: dict[str, tk.Label] = {}
    exclusive_vars: dict[str, tk.StringVar] = {}
    _applying_forced_deps = {"on": False}
    group_defaults: dict[str, str] = {}
    page1_option_ids: list[str] = []
    page2_option_ids: list[str] = []
    page3_option_ids: list[str] = []
    wizard_page = {"n": 1}
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

    # option id → last known "primary archive present" (for edge-triggered select).
    _primary_linked_state: dict[str, bool] = {}

    def _option_primary_leaf_id(oid: str) -> str | None:
        leaves = option_archive_leaves.get(oid) or []
        if not leaves:
            return None
        return oid if oid in leaves else leaves[0]

    def _option_primary_linked(oid: str) -> bool:
        lid = _option_primary_leaf_id(oid)
        if not lid:
            return False
        leaf = pack_by_id.get(lid)
        if leaf is None:
            return False
        path, _st = lib.resolve_local_archive(mo2_root, leaf)
        return path is not None

    def _sync_third_party_checks_from_files(*, on_load: bool = False) -> None:
        """Select page-1 mods when their archive appears; clear when it disappears.

        On load / file-added only (not every poll). Unselect on file loss unless
        the option is a locked dependency of another selected mod.
        """
        if _applying_forced_deps["on"]:
            return
        # Respect a prior InstallerSelection on first open.
        if (
            on_load
            and initial is not None
            and initial.option_ids
        ):
            for oid in page1_option_ids:
                _primary_linked_state[oid] = _option_primary_linked(oid)
            return

        _applying_forced_deps["on"] = True
        try:
            for oid in page1_option_ids:
                linked = _option_primary_linked(oid)
                was = _primary_linked_state.get(oid)
                _primary_linked_state[oid] = linked
                bv = bool_vars.get(oid)
                if bv is None:
                    continue
                if linked and (on_load or was is False):
                    if not bv.get():
                        bv.set(True)
                elif not linked and was is True:
                    # Defer forced check until after selects; uncheck pass below.
                    pass

            # Lock/check requires of newly selected parents.
            forced = _forced_option_ids()
            for oid in forced:
                bv = bool_vars.get(oid)
                if bv is not None and not bv.get():
                    bv.set(True)

            forced = _forced_option_ids()
            for oid in page1_option_ids:
                bv = bool_vars.get(oid)
                if bv is None or not bv.get():
                    continue
                if oid in forced:
                    continue
                if not _primary_linked_state.get(oid, False):
                    bv.set(False)

            forced = _forced_option_ids()
            for oid, chk in check_btns.items():
                try:
                    chk.configure(
                        state=("disabled" if oid in forced else "normal")
                    )
                except tk.TclError:
                    pass
        finally:
            _applying_forced_deps["on"] = False
        _on_selection_changed()

    def _on_archive_linked(leaf_id: str) -> None:
        _ = leaf_id
        _sync_third_party_checks_from_files(on_load=False)

    def _on_archive_cleared(leaf_id: str) -> None:
        _ = leaf_id
        _sync_third_party_checks_from_files(on_load=False)

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

    def _set_path_info_alert(lbl: tk.Label, on: bool) -> None:
        img = (
            getattr(lbl, "_dogma_info_img_alert", None)
            if on
            else getattr(lbl, "_dogma_info_img_normal", None)
        )
        if img is None:
            return
        try:
            lbl.configure(image=img)
            lbl._dogma_info_img = img  # type: ignore[attr-defined]
        except tk.TclError:
            pass

    def _pack_path_info_icon(
        row: ttk.Frame,
        files_col: ttk.Frame,
        option_id: str,
        sections_fn: Callable[[], list[tuple[str, list[str], str]]],
    ) -> None:
        """Blue/red ⓘ between checkbox and path-mod content."""
        top = row.winfo_toplevel()
        bg = _frame_bg(row)
        img_n = _filled_info_image(top, bg=bg)
        img_a = _filled_info_image(top, bg=bg, fill=_THEME["alert"])
        lbl = _pack_info_icon(row, sections_fn, bg=bg, image=img_n)
        lbl._dogma_info_img_normal = img_n  # type: ignore[attr-defined]
        lbl._dogma_info_img_alert = img_a  # type: ignore[attr-defined]
        lbl.pack(
            side="left",
            anchor="n",
            padx=(0, 6),
            pady=(5, 0),
            before=files_col,
        )
        path_info_icons[option_id] = lbl

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
                on_linked=_on_archive_linked,
                on_cleared=_on_archive_cleared,
                info_sections_fn=_leaf_info_sections(leaf),
                drop_registry=drop_registry,
                link_icons=link_icons,
                download_hub=download_hub,
            )
            field.pack(anchor="w", pady=(0, 4))
            archive_fields.setdefault(leaf.id, []).append(field)
        return leaves

    def _require_relation_sections(
        dep: lib.Dependency,
    ) -> list[tuple[str, list[str], str]]:
        """Parents/children for info tips — red ``alert`` kind."""
        children = [d for d in dep.requires if d]
        parents = sorted(
            {
                p.id
                for p in pack_by_id.values()
                if dep.id in p.requires and p.id != dep.id
            },
            key=str.lower,
        )
        out: list[tuple[str, list[str], str]] = []
        if children:
            out.append(("Requires", children, "alert"))
        if parents:
            out.append(("Required by", parents, "alert"))
        return out

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
            sections.extend(_require_relation_sections(path_pack))
            if feature is not None:
                for label, items in lib.preview_feature_effect_sections(
                    feature, mo2_root=mo2_root
                ):
                    sections.append((label, items, "normal"))
            # Require-only packs (skip the path pack itself — covered by feature rows).
            require_deps = [d for d in deps if d.id != path_pack.id]
            for label, items in _effect_deps_for(require_deps):
                if label == "Installs":
                    sections.append(("Installs (requires)", items, "normal"))
                else:
                    sections.append((label, items, "normal"))
        else:
            for label, items in _effect_deps_for(deps):
                sections.append((label, items, "normal"))
            seen_rel: set[str] = set()
            for d in deps:
                if d.id in seen_rel:
                    continue
                seen_rel.add(d.id)
                sections.extend(_require_relation_sections(d))
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
    footer_btns: dict[str, ttk.Button] = {}
    requires_warn_var = tk.StringVar(value="")

    def _selected_seed_ids() -> list[str]:
        """Checkbox option ids + radio picks currently selected."""
        seeds = list(_selected_option_ids())
        for var in exclusive_vars.values():
            pick = (var.get() or "").strip()
            if pick:
                seeds.append(pick)
        return seeds

    def _forced_option_ids() -> set[str]:
        """Checkbox options required by a currently selected pack's requires."""
        forced: set[str] = set()
        for seed in _selected_seed_ids():
            for leaf in lib.archive_leaves_for_pack(pack_by_id, seed):
                if leaf.id in bool_vars and leaf.id != seed:
                    forced.add(leaf.id)
            opt = opt_by_id.get(seed)
            if opt is not None:
                for dep in _deps_for_option(opt):
                    if dep.id in bool_vars and dep.id != seed:
                        forced.add(dep.id)
            pack = pack_by_id.get(seed)
            if pack is not None:
                for dep_id in pack.requires:
                    if dep_id in bool_vars and dep_id != seed:
                        forced.add(dep_id)
        return forced

    def _apply_forced_deps() -> None:
        """Auto-check dependency options and lock their checkboxes."""
        if _applying_forced_deps["on"]:
            return
        _applying_forced_deps["on"] = True
        try:
            forced = _forced_option_ids()
            for oid in forced:
                bv = bool_vars.get(oid)
                if bv is not None and not bv.get():
                    bv.set(True)
            for oid, chk in check_btns.items():
                try:
                    chk.configure(
                        state=("disabled" if oid in forced else "normal")
                    )
                except tk.TclError:
                    pass
        finally:
            _applying_forced_deps["on"] = False

    def _missing_selected_leaf_ids() -> set[str]:
        """Archive leaf ids for the selection that still need a linked file.

        Unified rule: any selected mod's file box without an archive is alerted
        (primary and requires alike). Empty radios contribute nothing.
        """
        missing: set[str] = set()
        seen: set[str] = set()
        for seed in _selected_seed_ids():
            for leaf in lib.archive_leaves_for_pack(pack_by_id, seed):
                if leaf.id in seen:
                    continue
                seen.add(leaf.id)
                path, _st = lib.resolve_local_archive(mo2_root, leaf)
                if path is None:
                    missing.add(leaf.id)
        return missing

    def _on_selection_changed(*_args: object) -> None:
        if _applying_forced_deps["on"]:
            return
        _apply_forced_deps()
        missing = _missing_selected_leaf_ids()
        for lid, fields in archive_fields.items():
            on = lid in missing
            for field in fields:
                field.set_alert(on)
        for oid, lbl in path_info_icons.items():
            on = False
            bv = bool_vars.get(oid)
            if bv is not None and bv.get():
                for leaf in lib.archive_leaves_for_pack(pack_by_id, oid):
                    if leaf.id in missing:
                        on = True
                        break
            _set_path_info_alert(lbl, on)
        blocked = bool(missing)
        btn = footer_btns.get("install")
        if btn is not None:
            try:
                btn.configure(state=("disabled" if blocked else "normal"))
            except tk.TclError:
                pass
        if blocked:
            msg = "Selected mods still need archives linked"
            if wizard_page["n"] >= 2:
                msg += " (page 1)"
            requires_warn_var.set(msg)
        else:
            requires_warn_var.set("")

    def _current_page_option_ids() -> list[str]:
        n = wizard_page["n"]
        if n == 1:
            return page1_option_ids
        if n == 2:
            return page2_option_ids
        return page3_option_ids

    def _select_all() -> None:
        for oid in _current_page_option_ids():
            bv = bool_vars.get(oid)
            if bv is not None:
                bv.set(True)
        if wizard_page["n"] == 1:
            for group, packs in radio_groups.items():
                if not packs:
                    continue
                var = exclusive_vars.get(group)
                if var is not None:
                    var.set(packs[0].id)
        _on_selection_changed()

    def _deselect_all() -> None:
        # Clear this page without re-locking mid-loop; selection-changed
        # re-applies forced deps still required by other pages.
        _applying_forced_deps["on"] = True
        try:
            if wizard_page["n"] == 1:
                for var in exclusive_vars.values():
                    var.set("")
            for oid in _current_page_option_ids():
                bv = bool_vars.get(oid)
                if bv is not None:
                    bv.set(False)
                chk = check_btns.get(oid)
                if chk is not None:
                    try:
                        chk.configure(state="normal")
                    except tk.TclError:
                        pass
        finally:
            _applying_forced_deps["on"] = False
        _on_selection_changed()

    ttk.Button(
        sel_btns, text="Select all", style="Toolbar.TButton", command=_select_all
    ).pack(side="left", padx=(0, 6))
    ttk.Button(
        sel_btns, text="Deselect all", style="Toolbar.TButton", command=_deselect_all
    ).pack(side="left")

    def _add_option_block(
        opt: lib.InstallerOption,
        parent: ttk.Frame,
        id_list: list[str],
        *,
        path_inline_desc: bool = False,
    ) -> None:
        pack = pack_by_id.get(opt.id)
        feat = data.features.get(opt.id)
        if pack is not None and pack.path:
            feat = data.features.get(pack.path) or feat
        title = opt.id
        if pack is not None:
            if pack.path:
                title = lib.with_dogma_prefix(pack.id)
            elif not title:
                title = pack.id
        elif feat is not None:
            title = feat.display_name

        # Plain title — site/download buttons on the file row cover links.
        _title_row(parent, title)
        frame = ttk.Frame(parent, padding=(4, 4))
        frame.pack(fill="x", pady=(0, 8), padx=2)
        prior = set(initial.option_ids) if initial is not None else set()
        if initial is not None and prior:
            checked = opt.id in prior
        else:
            checked = opt.default

        bv = tk.BooleanVar(value=checked)
        bool_vars[opt.id] = bv
        id_list.append(opt.id)

        row, files_col, chk = _pack_mod_select_row(
            frame,
            kind="check",
            variable=bv,
            command=_on_selection_changed,
        )
        if isinstance(chk, ttk.Checkbutton):
            check_btns[opt.id] = chk

        is_path_mod = (pack is not None and bool(pack.path)) or (
            pack is None and feat is not None
        )
        leaves: list[lib.Dependency] = []
        if is_path_mod:
            if pack is not None:
                _pack_path_info_icon(
                    row, files_col, opt.id, _leaf_info_sections(pack)
                )
            elif feat is not None:
                _pack_path_info_icon(
                    row,
                    files_col,
                    opt.id,
                    lambda o=opt, f=feat: _tooltip_sections_for(
                        _deps_for_option(o),
                        feature=f,
                        requires=list(f.requires),
                        path_pack=None,
                    ),
                )
            # Packaged path mods — no archive box.
            # D.O.G.M.A. features page: white desc in the old "Install" slot.
            # Page 1 path rows (if any): keep Install + desc under.
            if path_inline_desc:
                blurb = (
                    _pack_blurb(pack)
                    if pack is not None
                    else (opt.desc or "").strip()
                )
                ttk.Label(
                    files_col,
                    text=blurb or "Install",
                    font=("Segoe UI", 10),
                    foreground=_THEME["fg"],
                    wraplength=max(320, col_wrap - 48),
                    justify="left",
                ).pack(anchor="w", pady=(4, 0))
            else:
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

    def _add_compact_path_row(
        opt: lib.InstallerOption,
        parent: ttk.Frame,
        id_list: list[str],
        *,
        desc_only: bool = False,
    ) -> None:
        """Compact path-mod row with tight gap: ``[✓] Name`` + desc (no Install).

        ``desc_only`` is unused (kept for call-site compat); both pages show the name.
        D.O.G.M.A. features use white desc; Tweaks use muted desc.
        """
        _ = desc_only
        pack = pack_by_id.get(opt.id)
        feat = data.features.get(opt.id)
        if pack is not None and pack.path:
            feat = data.features.get(pack.path) or feat
        if pack is not None:
            title = (
                pack.id
                if lib.is_wizard_tweak_pack(pack)
                else lib.with_dogma_prefix(pack.id)
            )
            desc_fg = (
                _THEME["fg_muted"]
                if lib.is_wizard_tweak_pack(pack)
                else _THEME["fg"]
            )
        elif feat is not None:
            title = feat.display_name
            desc_fg = _THEME["fg"]
        else:
            title = opt.id
            desc_fg = _THEME["fg_muted"]
        desc = _pack_blurb(pack) if pack is not None else (opt.desc or "").strip()

        prior = set(initial.option_ids) if initial is not None else set()
        if initial is not None and prior:
            checked = opt.id in prior
        else:
            checked = opt.default

        bv = tk.BooleanVar(value=checked)
        bool_vars[opt.id] = bv
        id_list.append(opt.id)

        block = ttk.Frame(parent)
        block.pack(anchor="w", fill="x", pady=(0, 8), padx=2)
        row, files_col, chk = _pack_mod_select_row(
            block,
            kind="check",
            variable=bv,
            command=_on_selection_changed,
        )
        if isinstance(chk, ttk.Checkbutton):
            check_btns[opt.id] = chk

        if pack is not None:
            _pack_path_info_icon(
                row, files_col, opt.id, _leaf_info_sections(pack)
            )
        elif feat is not None:
            _pack_path_info_icon(
                row,
                files_col,
                opt.id,
                lambda o=opt, f=feat: _tooltip_sections_for(
                    _deps_for_option(o),
                    feature=f,
                    requires=list(f.requires),
                    path_pack=None,
                ),
            )

        ttk.Label(
            files_col,
            text=title,
            font=("Segoe UI", 10),
            foreground=_THEME["fg"],
        ).pack(anchor="w", pady=(4, 0))
        if desc:
            ttk.Label(
                files_col,
                text=desc,
                foreground=desc_fg,
                font=("Segoe UI", 9),
                wraplength=max(320, col_wrap - 48),
                justify="left",
            ).pack(anchor="w", pady=(1, 0))

    def _add_tweak_row(
        opt: lib.InstallerOption,
        parent: ttk.Frame,
        id_list: list[str],
    ) -> None:
        _add_compact_path_row(opt, parent, id_list, desc_only=False)

    def _add_or_separator(parent: ttk.Frame) -> None:
        # Quiet gap between radio choices (no "OR" label).
        ttk.Frame(parent, height=4).pack(fill="x", pady=(2, 2))

    def _add_radio_block(group: str, parent_col: ttk.Frame) -> None:
        packs = radio_groups[group]
        parent = pack_by_id.get(group)
        title = lib.radio_group_title(group, packs, parent=parent)
        _title_row(parent_col, title)
        frame = ttk.Frame(parent_col, padding=(4, 4))
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

    for kind, sid in lib.wizard_page1_section_order(data, min_stage=min_stage):
        if kind == "radio":
            _add_radio_block(sid, page1_col)
        else:
            opt = opt_by_id.get(sid)
            if opt is not None:
                _add_option_block(opt, page1_col, page1_option_ids)
    _sync_third_party_checks_from_files(on_load=True)

    for kind, sid in lib.wizard_page2_section_order(data, min_stage=min_stage):
        if kind == "option":
            opt = opt_by_id.get(sid)
            if opt is not None:
                _add_option_block(
                    opt,
                    page2_col,
                    page2_option_ids,
                    path_inline_desc=True,
                )

    for kind, sid in lib.wizard_page3_section_order(data, min_stage=min_stage):
        if kind == "option":
            opt = opt_by_id.get(sid)
            if opt is not None:
                _add_tweak_row(opt, page3_col, page3_option_ids)

    def _probe_download_updates() -> None:
        """Background: colour cloud icons from ModDB/GitHub dates vs local date suffix."""
        fields = [f for f in drop_registry.fields if f._can_download]
        if not fields:
            return
        tools = lib.mo2_tools_dir(mo2_root)

        def _work() -> None:
            for field in fields:
                remote = ""
                try:
                    remote = lib.probe_dep_remote_date(field.dep, cache_dir=tools)
                except Exception:
                    remote = ""
                status = lib.download_version_icon_status(
                    mo2_root,
                    field.dep,
                    remote_date=remote,
                    probed=True,
                )

                def _apply(
                    f: _ArchiveField = field,
                    date: str = remote,
                    st: str = status,
                ) -> None:
                    f._remote_date = date
                    f._remote_probed = True
                    f.set_download_status(st)
                    if f.dl_btn is None:
                        return
                    tip = {
                        "new": f"Update available ({date})" if date else "Update available",
                        "current": f"Up to date ({date})" if date else "Up to date",
                        "unknown": "Version unknown",
                    }.get(st, "")
                    if tip:
                        try:
                            _attach_text_tip([f.dl_btn], tip)
                        except tk.TclError:
                            pass

                try:
                    root.after(0, _apply)
                except tk.TclError:
                    return

        threading.Thread(
            target=_work, daemon=True, name="dogma-update-probe"
        ).start()

    root.after(200, _probe_download_updates)

    def collect() -> lib.InstallerSelection:
        picks = {g: v.get() for g, v in exclusive_vars.items()}
        return lib.InstallerSelection(
            option_ids=_selected_option_ids(),
            exclusive_picks=picks,
            features_chosen=True,
        )

    archive_poll: dict[str, object] = {
        "job": None,
        "focus_job": None,
        "alive": True,
    }

    def _sync_archives_from_disk(*_args: object) -> None:
        """Prune missing archives.ini rows and refresh file boxes from disk."""
        if not archive_poll["alive"]:
            return
        try:
            removed = lib.sync_archive_links(mo2_root)
        except Exception as exc:
            lib.log_exception(exc, where="wizard.sync_archives")
            removed = []
        changed = bool(removed)
        for fields in archive_fields.values():
            for field in fields:
                if field._busy:
                    continue
                before = field._has_file
                try:
                    field.refresh()
                except tk.TclError:
                    continue
                if field._has_file != before:
                    changed = True
        if changed:
            _sync_third_party_checks_from_files(on_load=False)

    def _poll_archives() -> None:
        if not archive_poll["alive"]:
            return
        _sync_archives_from_disk()
        try:
            archive_poll["job"] = root.after(1500, _poll_archives)
        except tk.TclError:
            archive_poll["job"] = None

    def _schedule_archive_sync_on_focus(event: tk.Event) -> None:
        if not archive_poll["alive"]:
            return
        try:
            if event.widget.winfo_toplevel() is not root:
                return
        except tk.TclError:
            return
        if archive_poll["focus_job"] is not None:
            return

        def _run() -> None:
            archive_poll["focus_job"] = None
            _sync_archives_from_disk()

        try:
            archive_poll["focus_job"] = root.after(100, _run)
        except tk.TclError:
            archive_poll["focus_job"] = None

    def _cleanup_binds() -> None:
        archive_poll["alive"] = False
        for key in ("job", "focus_job"):
            job = archive_poll.get(key)
            if job is not None:
                try:
                    root.after_cancel(job)  # type: ignore[arg-type]
                except (tk.TclError, ValueError):
                    pass
                archive_poll[key] = None
        try:
            root.unbind_all("<MouseWheel>")
        except tk.TclError:
            pass
        try:
            root.unbind_all("<FocusIn>")
        except tk.TclError:
            pass
        download_hub.shutdown()

    def _refresh_page_tabs() -> None:
        cur = wizard_page["n"]
        for n, btn in page_tab_btns.items():
            try:
                btn.configure(
                    style=(
                        "PageSelected.TButton" if n == cur else "Page.TButton"
                    ),
                    state="normal",
                )
            except tk.TclError:
                pass

    def _show_page(n: int) -> None:
        wizard_page["n"] = n
        page1_col.pack_forget()
        page2_col.pack_forget()
        page3_col.pack_forget()
        next_btn.pack_forget()
        back_btn.pack_forget()
        install_btn.pack_forget()

        if n == 1:
            page1_col.pack(fill="both", expand=True, padx=4, pady=4)
            subtitle_var.set("Choose third-party mods to install")
            next_btn.pack(side="right")
        elif n == 2:
            page2_col.pack(fill="both", expand=True, padx=4, pady=4)
            subtitle_var.set("Choose D.O.G.M.A. features to install")
            next_btn.pack(side="right")
            back_btn.pack(side="right", padx=(0, 8))
        else:
            page3_col.pack(fill="both", expand=True, padx=4, pady=4)
            subtitle_var.set("Choose D.O.G.M.A. tweaks to install")
            install_btn.pack(side="right")
            back_btn.pack(side="right", padx=(0, 8))

        _refresh_page_tabs()
        try:
            canvas.yview_moveto(0)
            canvas.update_idletasks()
            canvas.configure(scrollregion=canvas.bbox("all"))
        except tk.TclError:
            pass
        _on_selection_changed()

    def on_next() -> None:
        n = wizard_page["n"]
        if n < 3:
            _show_page(n + 1)

    def on_back() -> None:
        n = wizard_page["n"]
        if n > 1:
            _show_page(n - 1)

    def on_install() -> None:
        chosen = collect()
        if not chosen.option_ids and not any(chosen.exclusive_picks.values()):
            messagebox.showwarning(
                "D.O.G.M.A.",
                "Select at least one option.",
            )
            return
        missing = sorted(_missing_selected_leaf_ids())
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
            _on_selection_changed()
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

    page_labels = {
        1: "#1 Third party mods",
        2: "#2 D.O.G.M.A. features",
        3: "#3 D.O.G.M.A. tweaks",
    }
    for n, label in page_labels.items():
        btn = ttk.Button(
            page_bar,
            text=label,
            style="Page.TButton",
            command=lambda page=n: _show_page(page),
        )
        btn.pack(side="left", padx=(0, 4))
        page_tab_btns[n] = btn

    footer = ttk.Frame(root, padding=(20, 10, 20, 16))
    footer.pack(fill="x")
    ttk.Label(
        footer,
        textvariable=requires_warn_var,
        foreground=_THEME["alert"],
        font=("Segoe UI", 9),
        anchor="w",
        justify="left",
    ).pack(side="left", fill="x", expand=True, padx=(0, 12))
    next_btn = ttk.Button(footer, text="Next", command=on_next)
    back_btn = ttk.Button(footer, text="Back", command=on_back)
    install_btn = ttk.Button(footer, text="Install", command=on_install)
    footer_btns["install"] = install_btn
    _show_page(1)

    # Detect Explorer deletes in downloads/DOGMA while the wizard is open.
    root.bind_all("<FocusIn>", _schedule_archive_sync_on_focus, add="+")
    try:
        archive_poll["job"] = root.after(1500, _poll_archives)
    except tk.TclError:
        archive_poll["job"] = None

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
