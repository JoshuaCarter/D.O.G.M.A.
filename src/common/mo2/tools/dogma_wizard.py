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
    "border": "#3c3c3c",
    "select_bg": "#3a3a3a",
    "select_fg": "#ffffff",
    "link": "#6cb6ff",
    "accent": "#3ddc84",
    "button_bg": "#333333",
    "button_active": "#404040",
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
        pady=padx[1],
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
    box.pack(anchor="w", fill="x", pady=(4, 0))


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
    """Big title; optional URL on the next line (outside the option box)."""
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

    # Batch console stays open for post-wizard jobs; hide it during the GUI.
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
    finally:
        _set_console_visible(console_hwnd, True)


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
        )

    def _add_meta_lines(
        parent: ttk.Frame,
        sections: list[tuple[str, list[str]]],
    ) -> None:
        for label, items in sections:
            if not items:
                continue
            bullets = "\n".join(f"• {item}" for item in items)
            text = f"{label}:\n{bullets}"
            text = _expand_desc(text, mo2_root=mo2_root, dogma_dl=dogma_dl)
            _pack_link_text(
                parent,
                text,
                dogma_dl=dogma_dl,
                wraplength=col_wrap,
                font_size=8,
                foreground=_THEME["fg_dim"],
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
        )

    def _effect_sections_for(deps: list[lib.Dependency]) -> list[tuple[str, list[str]]]:
        installs = lib.preview_install_packs(deps, pack_by_id)
        sections: list[tuple[str, list[str]]] = []
        if installs:
            sections.append(("Installs", installs))
        sections.extend(lib.preview_effect_sections(deps))
        hit = lib.preview_disables_for_deps(deps, enabled_mods)
        if hit:
            sections.append(("Currently enabled (will disable)", hit))
        return sections

    enabled_mods: list[str] = []
    try:
        modlist = lib.modlist_path(mo2_root)
        enabled_mods = [
            n for f, n in lib.list_modlist_entries(modlist) if f == "+"
        ]
    except (OSError, FileNotFoundError, ValueError):
        enabled_mods = []

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

    option_meta_frames: dict[str, ttk.Frame] = {}
    opt_by_id = {o.id: o for o in options}

    def _refresh_option_meta(opt_id: str) -> None:
        frame = option_meta_frames.get(opt_id)
        if frame is None:
            return
        for child in frame.winfo_children():
            child.destroy()
        opt = opt_by_id.get(opt_id)
        if not opt:
            return
        sections: list[tuple[str, list[str]]] = []
        if opt.requires:
            sections.append(("Requires", list(opt.requires)))
        pack = pack_by_id.get(opt_id)
        if pack is not None and pack.path:
            zname = f"{lib.feature_path_key(pack.path)}.zip"
            sections.append(("Installs", [f"local package {zname} ({pack.path})"]))
            if pack.depends:
                sections.append(("Depends", list(pack.depends)))
            feat = data.features.get(pack.path)
            if feat is not None:
                sections.extend(lib.preview_feature_effect_sections(feat))
            deps = _deps_for_option(opt)
            for label, items in _effect_sections_for(deps):
                if label == "Installs":
                    sections.append(("Installs (depends)", items))
                else:
                    sections.append((label, items))
        else:
            deps = _deps_for_option(opt)
            sections.extend(_effect_sections_for(deps))
        _add_meta_lines(frame, sections)

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
        for oid in option_meta_frames:
            _refresh_option_meta(oid)

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
        title = opt.id
        url = ""
        if pack is not None:
            url = pack.url or pack.buy_url or ""
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
        ttk.Checkbutton(
            frame,
            text="Install",
            style="Dogma.TCheckbutton",
            variable=bv,
            command=_sync_exclusive_requirements,
        ).pack(anchor="w")
        if pack is not None:
            _add_desc(frame, _pack_blurb(pack))
            if pack.path:
                _add_desc(frame, f"Local path mod: {pack.path}")
            _add_instructions(frame, [pack])
        elif feat is not None:
            if opt.desc.strip():
                _add_desc(frame, opt.desc)
            step_deps = [pack_by_id[m] for m in opt.mods if m in pack_by_id]
            _add_instructions(frame, step_deps)
        meta = ttk.Frame(frame)
        meta.pack(anchor="w", fill="x")
        option_meta_frames[opt.id] = meta

    def _add_radio_block(group: str) -> None:
        packs = radio_groups[group]
        parent = pack_by_id.get(group)
        title = lib.radio_group_title(group, packs, parent=parent)
        parent_url = ""
        if parent is not None:
            parent_url = parent.url or parent.buy_url or ""
        _title_row(options_col, title, url=parent_url)
        frame = ttk.LabelFrame(options_col, text="", padding=(12, 10))
        frame.pack(fill="x", pady=(0, 4), padx=2)
        var = exclusive_vars[group]
        var.trace_add("write", lambda *_a: _sync_exclusive_requirements())

        if parent is not None and parent.desc.strip():
            _add_desc(frame, parent.desc)

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
            choice_url = pack.url or pack.buy_url or ""
            head = ttk.Frame(frame)
            head.pack(anchor="w", fill="x", pady=(6, 0))
            ttk.Radiobutton(
                head,
                text=label,
                style="Dogma.TRadiobutton",
                variable=var,
                value=pack.id,
            ).pack(anchor="w")
            if choice_url:
                _inline_link(
                    head, choice_url, lambda u=choice_url: webbrowser.open(u)
                ).pack(anchor="w", padx=(24, 0), pady=(2, 0))

            sub = ttk.Frame(frame)
            sub.pack(anchor="w", fill="x", padx=(24, 0))
            _add_desc(sub, _pack_blurb(pack))
            preview_deps = _preview_deps_for_choice(group, pack)
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
            _add_meta_lines(sub, _effect_sections_for(preview_deps))

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
