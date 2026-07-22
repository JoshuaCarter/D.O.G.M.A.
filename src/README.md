# DOGMA src

Author here; `Ctrl+Shift+B` smushes into `build/gamedata/`.

## Layout (matches MCM)

```
src/<category>/<feature>/<gamedata-root>/...
src/common/<gamedata-root>/...     # vendored Common, not in MCM
```

Example: `src/mutants/skinning/scripts/...` → MCM `DOGMA → Mutants → Skinning`.

See `MOVE_MAP.md` for the full map.

## Build roots merged into gamedata

`scripts` `configs` `textures` `meshes` `anims` `sounds` `spawns`

## Copy vs smush (inside a gamedata root)

| In src | Result |
|--------|--------|
| `foo.script` (file) | copied |
| `foo.script/` (dir) | children concatenated |
| organiser dir (no ext) | walked |

Skip: `README*`, `MOVE_MAP*`, `.gitkeep`, `*.alao-bak`, `_` names.

## Common

Stable globals: `dorn_common`, `dorn_mcm`, `dorn_dbg`, `dorn_sys`, banner `dorn_mcm_banner`.

```lua
local DORN_COMMON_VERSION = "dorn_common"
DORN = _G[DORN_COMMON_VERSION].load(MOD_ID)  -- in on_game_start only
```
