# DOGMA src

Author here; `Ctrl+Shift+B` smushes into `build/gamedata/`.

## Layout (matches MCM)

```
src/<category>/<feature>/<gamedata-root>/...
src/_debug/<gamedata-root>/...     # top-level; manifest path / path_key = debug
src/tweaks/<feature>/...           # simple always-on / no-options patches
src/_common/<gamedata-root>/...    # vendored Common, not in MCM
```

`_common` / `_debug` are underscore-prefixed **on disk only** (sort first / mark reserved). Manifest paths, package ids, and MCM keys stay `common` / `debug`.

Example: `src/gameplay/faster_skinning/scripts/...` → MCM `D.O.G.M.A. → Gameplay → Faster Skinning`.  
Top-level: `src/_debug/...` → MCM `D.O.G.M.A. → Debug` (DEV-only; not a category page).  
Tweaks: `src/tweaks/<feature>/...` → MCM `D.O.G.M.A. → Tweaks` (one shared page; each feature appends a description).

## Build roots merged into gamedata

`scripts` `configs` `textures` `meshes` `anims` `sounds` `spawns`

## Exception: `mo2/` (MO2 tools dir)

`src/_common/mo2/...` and `src/<category>/<feature>/mo2/...` are **not** packed into gamedata. On deploy they land at `<mod>/mo2/...` next to `gamedata/` (e.g. `mods/DOGMA/mo2/DOGMA Setup.bat`). Local `build/mo2/` gets the same layout. Core always-on tools live under `src/_common/mo2/` (`DOGMA Setup.bat` at the mo2 root; internals in `mo2/tools/`).

## Copy vs smush (inside a gamedata root)

| In src | Result |
|--------|--------|
| `foo.script` (file) | copied |
| `foo.script/` (dir) | children concatenated |
| organiser dir (no ext) | walked |

Skip: `README*`, `MOVE_MAP*`, `.gitkeep`, `*.alao-bak`, `_` names.

## Installer (FOMOD)

Path-mod **name**, **description**, and **module id** come from `config/manifest-dogma-features.yml` / `manifest-dogma-tweaks.yml`
(YAML key, `desc:`, `path:` → `category_feature`). Optional hover preview only:

`src/<category>/<feature>/installer/image.png`

Release zip: `bash tools/package-fomod.sh` → `build/fomod/`. Wizard is **one page per category**; each page is SelectAny feature checkboxes. Hover a feature for its description and image; an About row shows "Hover each checkbox to see feature information". Descriptions append `Requires:` lines from `requires:` when present. Common is always installed. Root `config/manifest-dogma-*.yml` gates features: `omit` / `dev` / `release`. Pack catalog: `config/manifest-third-party.yml`. Feature third-party needs use `requires:` → pack ids. Local `Ctrl+Shift+B` builds `>= dev`; packaging ships `>= release`. MO2 entry point: `DOGMA Setup.bat`.

## Common

Runtime API: `dogma.script` (`dogma.mcm`, `dogma.keybinds`, `dogma.dbg`, `dogma.load`, …).
MCM gather: `dogma_mcm.attach` / `append` / `with_header`.
Controls rows: `__dogma_keybinds.claim` / `claim_bind` in feature `_conf.script`.

```lua
function on_game_start()
	dogma.load(MOD_ID)  -- resets per-mod dbg state
	-- live MCM: dogma.mcm.bool(MOD_ID, "x")
	-- debug: dogma.dbg.logging() / dogma.dbg.overlay_enabled()
	-- input: dogma.input.is_key_down(dik)
	-- xlibs (separate MO2 mod): xconst.INVALID_LEVEL_VERTEX_ID
end
```