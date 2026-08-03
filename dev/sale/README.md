# SALE — Stalker Anomaly Loadout Editor

Offline shop balancer for DOGMA Stat Derived Loadout. The shipped tweak is **config-only** — export writes a DLTX patch for stock `new_game_loadouts.ltx`; no Lua.

## Run

```
pip install -r dev/sale/requirements.txt
dev\SALE.pyw
```

Or: `PYTHONPATH=dev py -3 -m sale`

## Flow

1. Set Anomaly + GAMMA (MO2) roots.
2. **Regenerate** — merges enabled-mod LTX, classifies weapons/outfits/helmets, computes stats, writes `cache/items.yml`.
3. Tune faction **Default** / overrides + thresholds; grid highlights in-shop items.
4. **Export LTX** → `src/tweaks/stat_derived_loadout/configs/mod_new_game_loadouts_dogma_stat_derived.ltx` (or GAMMA overwrite for live test).

Format is native New Game loadout lines (`sec = true,1,pts`) plus `ammo_type_per_wpn` / `ammo_count` (4 stacks). Stock `UINewGame` reads it; no runtime scripts.
