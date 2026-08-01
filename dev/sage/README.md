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

Diagnostics: `dev/sage/sage.log` (DEBUG, append-only; includes faulthandler). Check it after crashes — Qt often exits with no console traceback.

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
- Auto-resolves textures / textures_descr / text from the Anomaly + GAMMA roots you set
- If `{Anomaly}/tools/_unpacked` is missing UI descr/text/textures, SAGE unpacks
  `configs.db0` + `textures_ui.db0` there after you confirm in setup/settings
- Zoom (wheel), pan (MMB or Alt+drag), Fit stage (`F`)
- Undo/Redo move, resize, and property geometry (`Ctrl+Z` / `Ctrl+Y` or `Ctrl+Shift+Z`)
- Meta handles for elements without XML `x/y/width/height` (runtime-positioned). Drag them to preview script placement - **children move with the meta origin**; positions save to `file.xml.meta` (not into the XML). `.xml.meta` also stores layer toggles, undo/redo history, selection, and view zoom/pan. ~10×10 diamond markers (move-only); tag caption is display-only.
- Save / Save As (comments/whitespace may change; meta written alongside)

## Texture roots

On first launch (or if roots are missing/invalid), a **SAGE Setup** window asks for
**Anomaly** and **GAMMA** install folders before the editor opens:

- Anomaly must contain `tools\db_unpacker.bat`
- GAMMA must contain `mods\G.A.M.M.A. UI\gamedata\textures`

Optional **extra scan roots** can be added there too (project / `src` and/or deployed
mod under `GAMMA\mods\…` — nothing is auto-added; add the folder itself, not
`textures\ui`). On Continue / Settings Ok, SAGE **scans once** for textures /
`textures_descr` / text folders and **saves those paths** into `settings.json`.
Later launches reuse the saved lists — no rescan until you change roots or use
**Edit → Rescan asset roots**.

**Textures (Anomaly rules):** UI XML `<texture>` is either an **atlas id** (no
`\`, UV from `configs/ui/textures_descr/*.xml`) or a **DDS path** (`ui\foo`,
optional XML `x/y/width/height` crop). The texture picker defaults to atlas ids
(with cropped preview); DDS-path mode is for full-file backgrounds/icons.
Neither writes drive paths — deploy flattens `src/…/textures/` into
`gamedata/textures/` the same way.

**Atlas editor:** Open a `textures_descr` XML (path or `<w>/<file name>/<texture id>`
shape) to edit UV boxes on the DDS sheet — same WYSIWYG / XML / Log tabs plus
sidebars (region list, id + x/y/w/h, Options border/fill/labels, Undo). Labels /
idle borders use shared `box_chrome` (outside the box, same as the UI editor).
Sheet linked by `file name="ui\…"`, not by the descr folder. F2 rename id ·
Ctrl+D duplicate · Del delete · Ctrl+N new region. No `.xml.meta`.

If `Anomaly\tools\_unpacked` is missing (or incomplete), Continue / Ok warns that
SAGE will unpack `configs.db0` + `textures_ui.db0` (may take a minute), runs the
unpack while the setup/settings window is still open, then scans and opens the editor.

Also set **Element label size** (2–40 pt, default **5**) and toggles for **element labels**, box **border** / **fill**
(`label_font_size`, `show_element_labels`, `show_box_border`, `show_box_fill`). Borders are always 1px (cosmetic).
Toggles default **off**. The hovered hit-target still shows border/fill when those are off.
Quick toggles under **View** and the right **Options** panel (also a **Label size** spin box 2–40).
