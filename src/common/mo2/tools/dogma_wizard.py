#!/usr/bin/env python3
"""DOGMA Setup wizard — pick options from config/mods.yml + feature depends."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tkinter as tk
import webbrowser
from collections.abc import Callable
from pathlib import Path
from tkinter import messagebox, ttk

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
    "fg": "#e6e6e6",
    "fg_muted": "#9a9a9a",
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
}


def _win_path(path: Path) -> str:
    """Display path with backslashes (Windows-style)."""
    return str(path.resolve()).replace("/", "\\")


def _expand_desc(text: str, *, mo2_root: Path, dogma_dl: Path) -> str:
    """Replace placeholders; show downloads folder with backslashes only."""
    if not text:
        return text
    game = ""
    try:
        gd = lib.game_dir(mo2_root)
        if str(gd).strip():
            game = _win_path(gd)
    except Exception:
        game = ""
    gamma = _win_path(mo2_root)
    folder = _win_path(dogma_dl)
    out = text.replace("<GAMMA>", gamma).replace("<gamma>", gamma)
    out = out.replace("<DOGMA_DOWNLOADS>", folder).replace("<dogma_downloads>", folder)
    if game:
        out = out.replace("<Anomaly>", game).replace("<anomaly>", game)
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
    """Small checkbox / radio images with a green selected mark."""
    s = 16
    border = "#888888"
    fill = _THEME["bg_raised"]
    green = _THEME["accent"]

    def blank() -> tk.PhotoImage:
        img = tk.PhotoImage(master=root, width=s, height=s)
        _put_rect(img, 0, 0, s - 1, s - 1, _THEME["bg"])
        return img

    check_off = blank()
    _put_rect(check_off, 1, 1, 14, 14, border)
    _put_rect(check_off, 2, 2, 13, 13, fill)

    check_on = blank()
    _put_rect(check_on, 1, 1, 14, 14, border)
    _put_rect(check_on, 2, 2, 13, 13, fill)
    # Green tick
    tick = [
        (4, 8),
        (5, 9),
        (6, 10),
        (7, 11),
        (8, 10),
        (9, 9),
        (10, 8),
        (11, 7),
        (12, 6),
    ]
    for x, y in tick:
        _put_rect(check_on, x, y, x + 1, y + 1, green)

    radio_off = blank()
    for y in range(s):
        for x in range(s):
            dx, dy = x - 7.5, y - 7.5
            r2 = dx * dx + dy * dy
            if 5.2 * 5.2 <= r2 <= 7.2 * 7.2:
                radio_off.put(border, (x, y))
            elif r2 < 5.2 * 5.2:
                radio_off.put(fill, (x, y))

    radio_on = blank()
    for y in range(s):
        for x in range(s):
            dx, dy = x - 7.5, y - 7.5
            r2 = dx * dx + dy * dy
            if 5.2 * 5.2 <= r2 <= 7.2 * 7.2:
                radio_on.put(border, (x, y))
            elif r2 < 5.2 * 5.2:
                radio_on.put(fill, (x, y))
            if r2 <= 3.2 * 3.2:
                radio_on.put(green, (x, y))

    return {
        "check_off": check_off,
        "check_on": check_on,
        "radio_off": radio_off,
        "radio_on": radio_on,
    }


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
            box.insert("end", f"{label}:\n", (tag,))
            for i, item in enumerate(items):
                if i:
                    box.insert("end", "\n", (tag,))
                box.insert("end", f"  • {item}", (tag,))
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


def _pack_hoverable_choice(
    parent: tk.Misc,
    *,
    kind: str,
    text: str,
    variable: tk.Variable,
    value: object | None = None,
    command: Callable[[], None] | None = None,
    sections_fn: Callable[[], list[tuple[str, list[str], str]]],
) -> ttk.Checkbutton | ttk.Radiobutton:
    """Checkbox/radio with dotted underline under the label (hover = tooltip)."""
    row = ttk.Frame(parent)
    row.pack(anchor="w")

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

    text_col = ttk.Frame(row)
    text_col.pack(side="left", anchor="w")
    bg = _frame_bg(parent)
    lbl = tk.Label(
        text_col,
        text=text,
        background=bg,
        foreground=_THEME["fg"],
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
        # Dotted rule under the label — marks the control as hoverable.
        underline.create_line(
            0,
            1,
            w,
            1,
            fill=_THEME["fg_muted"],
            dash=(1, 2),
            width=1,
        )

    lbl.bind("<Configure>", _redraw_underline, add="+")
    text_col.after_idle(_redraw_underline)

    def _activate(_event: tk.Event | None = None) -> None:
        if kind == "check":
            variable.set(not bool(variable.get()))
        else:
            variable.set("" if value is None else value)
        if command is not None:
            command()

    for w in (lbl, underline):
        w.bind("<Button-1>", _activate, add="+")

    _attach_hover_tip([btn, lbl, underline, row], sections_fn)
    return btn


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
) -> None:
    """Big title; optional ModDB URL on the next line (outside the option box)."""
    block = ttk.Frame(parent)
    block.pack(fill="x", pady=(10, 2))
    ttk.Label(
        block,
        text=name,
        font=("Segoe UI", 14, "bold"),
    ).pack(anchor="w")
    if url:
        _inline_link(block, url, lambda u=url: webbrowser.open(u)).pack(
            anchor="w", pady=(2, 0)
        )


def _url_display_link(dep: lib.Dependency | None) -> str:
    """Inline link under titles/radios: ``url:`` only (never ``buy_url:``)."""
    if dep is None:
        return ""
    return (dep.url or "").strip()


def _pack_blurb(dep: lib.Dependency | None) -> str:
    """Short description only (urls / buy / place handled elsewhere)."""
    if dep is None:
        return ""
    return dep.desc.strip()


def _manual_instruction_text(
    deps: list[lib.Dependency], *, dogma_dl: Path
) -> tuple[str, list[tuple[int, int]]]:
    """Numbered buy/place/rename steps; returns text + bold spans."""
    folder = _win_path(dogma_dl)
    lines: list[str] = []
    bold: list[tuple[int, int]] = []
    n = 1
    seen_buy: set[str] = set()
    seen_name: set[str] = set()
    need_place = False

    for dep in deps:
        if dep.buy_url and dep.buy_url not in seen_buy:
            seen_buy.add(dep.buy_url)
            lines.append(f"{n}. Buy here: {dep.buy_url}")
            n += 1
            need_place = True
        if dep.buy_url or dep.source == "user" or dep.archive_name:
            need_place = True

    if need_place:
        lines.append(f"{n}. Place archive file in {folder}")
        n += 1
        for dep in deps:
            if not (dep.buy_url or dep.source == "user" or dep.archive_name):
                continue
            name = f"{lib.dep_zip_stem(dep)}.zip"
            if name in seen_name:
                continue
            seen_name.add(name)
            prefix = f"{n}. Rename archive file to "
            line = prefix + name
            start = sum(len(x) + 1 for x in lines) + len(prefix)
            bold.append((start, start + len(name)))
            lines.append(line)
            n += 1

    return "\n".join(lines), bold


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
        initial = lib.InstallerSelection(
            option_ids=[o.id for o in options if o.default],
            exclusive_picks=lib.default_exclusive_picks(data),
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
    root.update_idletasks()
    screen_w = max(800, int(root.winfo_screenwidth()))
    screen_h = max(600, int(root.winfo_screenheight()))
    # ~half previous full-width wizard; keep tall.
    win_w = min(960, max(560, (screen_w - 32) // 2))
    win_h = min(1080, screen_h - 64)
    root.minsize(min(560, win_w), min(700, win_h))
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
        font=("Segoe UI", 18, "bold"),
    ).pack(anchor="w")
    ttk.Label(
        header,
        text="Choose mods to install (third-party and D.O.G.M.A. path mods)",
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
    sel_btns.pack(side="right")

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
    none_radios: dict[str, ttk.Radiobutton] = {}
    group_defaults: dict[str, str] = {}
    page_option_ids: list[str] = []
    for group, packs in radio_groups.items():
        default_pack = ""
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
            if default_pack:
                break
        if not default_pack and packs:
            default_pack = packs[0].id
        group_defaults[group] = default_pack
        start = initial_picks.get(group, "")
        if start and start not in {p.id for p in packs}:
            start = ""
        exclusive_vars[group] = tk.StringVar(value=start)

    def _add_desc(parent: ttk.Frame, desc: str) -> None:
        _pack_link_text(
            parent,
            _expand_desc(desc, mo2_root=mo2_root, dogma_dl=dogma_dl),
            dogma_dl=dogma_dl,
            wraplength=col_wrap,
            pady=(4, 6),
        )

    def _add_instructions(parent: ttk.Frame, deps: list[lib.Dependency]) -> None:
        text, _bold = _manual_instruction_text(deps, dogma_dl=dogma_dl)
        if not text:
            return
        expanded = _expand_desc(text, mo2_root=mo2_root, dogma_dl=dogma_dl)
        _, bold2 = _manual_instruction_text(deps, dogma_dl=dogma_dl)
        _pack_link_text(
            parent,
            expanded,
            dogma_dl=dogma_dl,
            wraplength=col_wrap,
            bold_spans=bold2,
            pady=(4, 6),
        )

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
                for label, items in lib.preview_feature_effect_sections(feature):
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
        sections.extend(lib.preview_effect_sections(deps))
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

    def _sync_exclusive_requirements(*_args: object) -> None:
        required = lib.required_exclusive_groups(data, _selected_option_ids())
        for group, var in exclusive_vars.items():
            none_btn = none_radios.get(group)
            if group in required:
                if none_btn is not None:
                    none_btn.state(["disabled"])
                if not var.get():
                    var.set(required[group] or group_defaults.get(group, ""))
            else:
                if none_btn is not None:
                    none_btn.state(["!disabled"])

    def _select_all() -> None:
        for oid in page_option_ids:
            bv = bool_vars.get(oid)
            if bv is not None:
                bv.set(True)
        _sync_exclusive_requirements()

    def _deselect_all() -> None:
        for oid in page_option_ids:
            bv = bool_vars.get(oid)
            if bv is not None:
                bv.set(False)
        _sync_exclusive_requirements()

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
            if pack.path and not title:
                title = pack.id
        elif feat is not None:
            title = feat.display_name
        _title_row(options_col, title, url=url)
        frame = ttk.LabelFrame(options_col, text="", padding=(12, 10))
        frame.pack(fill="x", pady=(0, 4), padx=2)
        prior = set(initial.option_ids) if initial is not None else set()
        if initial is not None and prior:
            checked = opt.id in prior
        else:
            checked = opt.default

        bv = tk.BooleanVar(value=checked)
        bool_vars[opt.id] = bv
        page_option_ids.append(opt.id)

        def _opt_sections(
            o: lib.InstallerOption = opt,
            p: lib.Dependency | None = pack,
            f: lib.FeatureMeta | None = feat,
        ) -> list[tuple[str, list[str], str]]:
            return _tooltip_sections_for(
                _deps_for_option(o),
                feature=f if p is not None and p.path else None,
                requires=list(o.requires),
                path_pack=p if p is not None and p.path else None,
            )

        _pack_hoverable_choice(
            frame,
            kind="check",
            text="Install",
            variable=bv,
            command=_sync_exclusive_requirements,
            sections_fn=_opt_sections,
        )
        if pack is not None:
            _add_desc(frame, _pack_blurb(pack))
            if pack.path:
                _add_desc(frame, f"Local path mod: {pack.path}")
            _add_instructions(frame, [pack])
            _desc_spacer(frame)
        elif feat is not None:
            if opt.desc.strip():
                _add_desc(frame, opt.desc)
            step_deps = [pack_by_id[m] for m in opt.mods if m in pack_by_id]
            _add_instructions(frame, step_deps)
            _desc_spacer(frame)

    def _add_radio_block(group: str) -> None:
        packs = radio_groups[group]
        parent = pack_by_id.get(group)
        title = lib.radio_group_title(group, packs, parent=parent)
        parent_url = _url_display_link(parent)
        _title_row(options_col, title, url=parent_url)
        frame = ttk.LabelFrame(options_col, text="", padding=(12, 10))
        frame.pack(fill="x", pady=(0, 4), padx=2)
        var = exclusive_vars[group]
        var.trace_add("write", lambda *_a: _sync_exclusive_requirements())

        if parent is not None and parent.desc.strip():
            _add_desc(frame, parent.desc)
            _desc_spacer(frame)

        none_btn = ttk.Radiobutton(
            frame,
            text="None",
            style="Dogma.TRadiobutton",
            variable=var,
            value="",
        )
        none_btn.pack(anchor="w")
        none_radios[group] = none_btn

        for pack in packs:
            label = pack.choice or pack.id
            choice_url = _url_display_link(pack)
            head = ttk.Frame(frame)
            head.pack(anchor="w", fill="x", pady=(6, 0))

            def _radio_sections(
                g: str = group, p: lib.Dependency = pack
            ) -> list[tuple[str, list[str], str]]:
                return _tooltip_sections_for(_preview_deps_for_choice(g, p))

            _pack_hoverable_choice(
                head,
                kind="radio",
                text=label,
                variable=var,
                value=pack.id,
                command=_sync_exclusive_requirements,
                sections_fn=_radio_sections,
            )
            if choice_url:
                _inline_link(
                    head, choice_url, lambda u=choice_url: webbrowser.open(u)
                ).pack(anchor="w", padx=(24, 0), pady=(2, 0))

            sub = ttk.Frame(frame)
            sub.pack(anchor="w", fill="x", padx=(24, 0))
            _add_desc(sub, _pack_blurb(pack))
            step_deps = []
            try:
                leaf_ids = lib.expand_pack_composition(pack_by_id, pack.id)
            except ValueError:
                leaf_ids = [pack.id]
            for lid in leaf_ids:
                leaf = pack_by_id.get(lid)
                if leaf is not None:
                    step_deps.append(leaf)
            _add_instructions(sub, step_deps)
            _desc_spacer(sub)

    for kind, sid in lib.wizard_section_order(data, min_stage=min_stage):
        if kind == "radio":
            _add_radio_block(sid)
        else:
            opt = opt_by_id.get(sid)
            if opt is not None:
                _add_option_block(opt)

    # One-off actions (not mods) — e.g. rebuild SFX prefetch list.
    action_vars: dict[str, tk.BooleanVar] = {}
    prior_actions = set(initial.actions) if initial is not None else set()
    if lib.WIZARD_ACTIONS:
        _title_row(options_col, "Actions")
        for action in lib.WIZARD_ACTIONS:
            frame = ttk.LabelFrame(options_col, text="", padding=(12, 10))
            frame.pack(fill="x", pady=(0, 4), padx=2)
            if initial is not None and prior_actions:
                checked = action.id in prior_actions
            elif initial is not None and initial.option_ids:
                # Returning user: leave one-offs off unless they check them.
                checked = False
            else:
                checked = action.default
            bv = tk.BooleanVar(value=checked)
            action_vars[action.id] = bv
            ttk.Checkbutton(
                frame,
                text=action.title,
                style="Dogma.TCheckbutton",
                variable=bv,
            ).pack(anchor="w")
            if action.desc.strip():
                _add_desc(frame, action.desc)
                _desc_spacer(frame)

    _sync_exclusive_requirements()

    def collect() -> lib.InstallerSelection:
        picks = {g: v.get() for g, v in exclusive_vars.items()}
        actions = [aid for aid, bv in action_vars.items() if bv.get()]
        return lib.InstallerSelection(
            option_ids=_selected_option_ids(),
            exclusive_picks=picks,
            features_chosen=True,
            actions=actions,
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
                "Select at least one option, or Cancel.",
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
    ttk.Button(footer, text="Cancel", command=on_cancel).pack(
        side="right", padx=(8, 0)
    )
    ttk.Button(footer, text="Install", command=on_install).pack(side="right")

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
