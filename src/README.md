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

## Exception: `mo2/` (MO2 tools dir)

`src/common/mo2/...` and `src/<category>/<feature>/mo2/...` are **not** packed into gamedata. On deploy they land at `<mod>/mo2/...` next to `gamedata/` (e.g. `mods/DOGMA/mo2/DOGMA.bat`). Local `build/mo2/` gets the same layout. Core always-on tools (pre-launch runner) live under `src/common/mo2/`.

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

Release zip: `bash tools/package-fomod.sh` → `build/fomod/`. Wizard is **one page per category**; each page is SelectAny feature checkboxes. Hover a feature for its description and image; an About row shows "Hover each checkbox to see feature information". Descriptions append `Requires:` lines from `features.yml` `depends:` when present. Final page lists third-party recommendations from `src/common/installer/recommendations.txt` (info only). Common is always installed. Root `config/features.yml` gates features: `omit` / `local` / `release`. Pack catalog + Setup wizard: `config/mods.yml`. Feature third-party needs use `depends:` → pack ids. Local `Ctrl+Shift+B` builds `>= local`; packaging ships `>= release`. MO2 entry point: `DOGMA (Setup).bat`.

## Common

Stable globals: `dogma_common`, `dogma_mcm`, `dogma_dbg`, `dogma_sys`, banner `dogma_mcm_banner`.

```lua
local DOGMA_COMMON_VERSION = "dogma_common"
DOGMA = _G[DOGMA_COMMON_VERSION].load(MOD_ID)  -- in on_game_start only
```
