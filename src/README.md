# DOGMA src

Author here; `Ctrl+Shift+B` smushes into `build/gamedata/`.

## Layout (matches MCM)

```
src/<category>/<feature>/<gamedata-root>/...
src/common/<gamedata-root>/...     # vendored Common, not in MCM
```

Example: `src/mutants/skinning/scripts/...` → MCM `D.O.G.M.A. → Mutants → Skinning`.

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

## Installer (FOMOD)

Per feature: `src/<category>/<feature>/installer/`

| File | Purpose |
|------|---------|
| `name.txt` | Checkbox title |
| `description.txt` | Hover text (short, player-facing) |
| `default.txt` | `recommended` / `optional` |
| `image.png` | optional hover preview |
| `id.txt` | zip module folder name (defaults to feature dir name) |

Release zip: `bash tools/package-fomod.sh` → `build/fomod/`. Wizard is **one page per category**; each page is SelectAny feature checkboxes. Hover a feature for its description and image; an About row shows "Hover each checkbox to see feature information". Final page lists third-party recommendations from `src/common/installer/recommendations.txt` (info only). Common is always installed. Regenerates root `MANIFEST.txt` into the zip. Local `Ctrl+Shift+B` still merges everything.

## Common

Stable globals: `dogma_common`, `dogma_mcm`, `dogma_dbg`, `dogma_sys`, banner `dogma_mcm_banner`.

```lua
local DOGMA_COMMON_VERSION = "dogma_common"
DOGMA = _G[DOGMA_COMMON_VERSION].load(MOD_ID)  -- in on_game_start only
```
