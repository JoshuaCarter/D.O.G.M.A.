# SAGE

Stalker Anomaly GUI Editor - visual layout editor for S.T.A.L.K.E.R. / Anomaly UI XML files.

## Coordinate space

Virtual **1024×768** HUD pixels (engine scales to the display). Child `x`/`y` are parent-relative.

**Script parenting:** Anomaly often parents `main_dialog` siblings to `frame_back` even when they are XML siblings (see `ui_mm_faction_select.script`). The editor applies that heuristic so the preview matches in-game placement.

## Run

Double-click (no console):

```
dev\SAGE.pyw
```

App icon art: `dev/sage/assets/sage.png` (64×64).

Or from a terminal:

```bash
pip install -r dev/sage/requirements.txt
py -3 dev/sage/__main__.py path/to/ui.xml
```

Headless preview dump:

```bash
py -3 dev/sage/render_preview.py
# → build/sage_preview.png
```

## Features

- WYSIWYG / XML / Log tabs (canvas + monospace XML editor + load-failure log)
- Load UI XML → boxes for every element with `x`/`y`/`width`/`height`
- **File → Open Recent** - last 10 unique files (persisted in settings)
- Tree + canvas selection, drag move, corner resize, property panel
- Layer toggles (`background`, `main_dialog`, `options`, `popup_*`); popups/options hidden by default
- Texture preview: path+UV and atlas IDs (`ui_inGame2_button` → `_e` state)
- String-table resolve for `<text>` ids via `configs/text/eng` (shown in Properties)
- **Log** tab - append-only editor log (opens, saves, audits, texture/string errors)
- Auto-detects local G.A.M.M.A. UI textures + Anomaly/`_db_unpacked` textures_descr + text tables
- Zoom (wheel), pan (MMB or Alt+drag), Fit stage (`F`)
- Undo/Redo move, resize, and property geometry (`Ctrl+Z` / `Ctrl+Y`)
- Meta handles for elements without XML `x/y/width/height` (runtime-positioned). Drag them to preview script placement - **children move with the meta origin**; positions save to `file.xml.meta` (not into the XML). `.xml.meta` also stores layer toggles, undo/redo history, selection, and view zoom/pan. ~10×10 diamond markers (move-only); tag caption is display-only.
- Save / Save As (comments/whitespace may change; meta written alongside)

## Texture roots

**Edit → Settings…** or edit `dev/sage/settings.json`.

Defaults scan repo `src/` plus common installs:

- `C:/GAMMA/mods/G.A.M.M.A. UI/gamedata/textures`
- `C:/gamma_dev/_db_unpacked/configs/ui/textures_descr`
- `C:/Anomaly/tools/_unpacked/configs/ui/textures_descr`

Also set **Element label size** (2–40 pt) and toggles for **element labels**, box **border** / **fill**
(`label_font_size`, `show_element_labels`, `show_box_border`, `show_box_fill`). Borders are always 1px (cosmetic).
Toggles default **off** for borders/fill; labels default **on**. The hovered hit-target still shows border/fill when those are off.
Quick toggles under **View** and the right **Options** panel (also a **Label size** spin box 2–40). Settings dialog has the same options.
