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

Example: `src/game/faster_skinning/scripts/...` → MCM `D.O.G.M.A. → Game → Faster Skinning`.  
Top-level: `src/_debug/...` → MCM `D.O.G.M.A. → Debug` (local stage; not a category page).  
Tweaks: `src/tweaks/<feature>/...` → MCM `D.O.G.M.A. → Tweaks` (one shared page; each feature appends a description).

## Build roots merged into gamedata

`scripts` `configs` `textures` `meshes` `anims` `sounds` `spawns`

`src/<category>/<feature>/*.py` and `src/_common/*.py` (file sitting in that folder, not under a gamedata/`db` bucket) ship to the mod root (`mods/DOGMA/dogma_sfx_prefetch.py`, `dogma_modlist_delta.py`). Run those from the mod folder; they walk up to find the MO2 instance.

`src/<category>/<feature>/disables.txt` (one MO2 folder name per line) ships to the mod root. `dogma_modlist_delta.py` turns those mods off in the current profile.

## Copy vs smush (inside a gamedata root)

| In src | Result |
|--------|--------|
| `foo.script` (file) | copied |
| `foo.script/` (dir) | children concatenated |
| organiser dir (no ext) | walked |

Skip: `README*`, `MOVE_MAP*`, `.gitkeep`, `*.alao-bak`, `_` names.

## Installer (FOMOD)

Catalog: `config/manifest.yml` (path, name, stage). Wizard pages: `config/fomod.yml` (lists those paths).

`BETA`/`GOLD` only. Required: `src/<path>/fomod/desc.txt`. Optional: `image.png`.

`Ctrl+Shift+B` also writes `build/DOGMA.zip` and copies it to MO2 `downloads/` (Reinstall). `stage: LOCAL` is the fat merge only.

## Common

Runtime API: `dogma.script` (`dogma.mcm`, `dogma.input`, `dogma.dbg`, `dogma.status`, `dogma.load`, …).
MCM gather: `dogma_mcm.attach` / `append` / `with_header`.
Feature keybinds: MCM `key_bind` via `dogma.mcm.key()` + `dogma.input` (mod-only keys),
or engine mirrors via `dogma.mcm.key_mirror` / `mirror_spec` (Settings → Controls ↔ MCM),
or vanilla `on_before_key_press` + `key_bindings`.

```lua
function on_game_start()
	dogma.load(MOD_ID)  -- resets per-mod dbg state
	-- live MCM: dogma.mcm.bool(MOD_ID, "x")
	-- debug: dogma.dbg.logging() / dogma.dbg.overlay_enabled()
	-- input: dogma.input.is_key_down(dik)
	-- status: dogma.status.is_in_combat() / is_inv_open() / is_eating() / is_drinking()
	--         / is_weapon_lowered() / is_sprinting() / is_talking() / health() / limbs() / bhs()
	-- xlibs (separate MO2 mod): xconst.INVALID_LEVEL_VERTEX_ID
end
```