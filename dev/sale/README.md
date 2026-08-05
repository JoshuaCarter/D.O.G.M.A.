# SALE — Stalker Anomaly Loadout Editor

Offline shop balancer for New Game loadouts. Export writes a DLTX patch for stock `new_game_loadouts.ltx`; no Lua.

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
2. **Regenerate** — merges enabled-mod LTX in MO2 priority order (modlist top wins; same relative path = VFS file winner only, so replaced weapon files cannot leave ghost sections), classifies weapons/outfits/helmets (spawner-style filter: drops attachment/kit variants via `parent_section`, `*_cw` / `*_mp` stubs, `tch_`/`mp_`/`_base`, blacklist), computes stats, writes `cache/items.yml`.
3. Tune faction **Default** / overrides (Budget toggles + weights). Select an item and set **Pts** (0–1000) under Budget — baseline applies to all factions; faction view can override (**x** clears). Tile top-left = pts; top-right = raw score×1000. Per-faction checkbox decides LTX. **Ctrl+S** refreshes scores.
4. **Export LTX** → `dev/sale/out/mod_new_game_loadouts_dogma_stat_derived.ltx` (or GAMMA overwrite via Deploy). Export saves + refreshes first so LTX matches current weights + manual pts.

Format is native New Game loadout lines (`sec = true,1,pts`) only — no `ammo_type_per_wpn` / `ammo_count` (stock defaults apply). Stock `UINewGame` reads it; no runtime scripts.
