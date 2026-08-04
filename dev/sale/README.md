# SALE — Stalker Anomaly Loadout Editor

Offline shop balancer for DOGMA Stat Derived Loadout. The shipped tweak is **config-only** — export writes a DLTX patch for stock `new_game_loadouts.ltx`; no Lua.

## Run

```
pip install -r dev/sale/requirements.txt
dev\SALE.pyw
```

Or: `PYTHONPATH=dev py -3 -m sale`

## Diagnostics

- Log file: `dev/sale/sale.log` (always appended; includes thread/func/line)
- In-app **Log** tab mirrors the same stream
- **Open log** button opens the file in the system viewer
- Captures: unhandled exceptions, thread failures, Qt messages, native fatal signals (`faulthandler`)
- On crash, send the tail of `sale.log`

## Flow

1. Set Anomaly + GAMMA (MO2) roots.
2. **Regenerate** — merges enabled-mod LTX in MO2 priority order (modlist top wins), classifies weapons/outfits/helmets (spawner-style filter: drops attachment/kit variants via `parent_section`, `*_cw` / `*_mp` stubs, `tch_`/`mp_`/`_base`, blacklist), computes stats, writes `cache/items.yml`.
3. Tune faction **Default** / overrides + price-scale min/max (Budget; always on). Shop pts = relative 0–1 score mapped through that scale (tile top-left); raw score×1000 is top-right. Per-faction checkbox decides LTX (green = baseline+faction, light green = faction only, yellow = baseline only / off here). **Ctrl+S** refreshes.
4. **Export LTX** → `src/tweaks/stat_derived_loadout/configs/mod_new_game_loadouts_dogma_stat_derived.ltx` (or GAMMA overwrite for live test). Export saves + refreshes first so LTX matches current weights/price scale.

Format is native New Game loadout lines (`sec = true,1,pts`) plus `ammo_type_per_wpn` / `ammo_count` (4 stacks). Stock `UINewGame` reads it; no runtime scripts.
