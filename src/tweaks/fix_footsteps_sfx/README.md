# Fix Footsteps SFX

Mutant steps only. Not player. Not stalker NPCs.

Engine lookup for `CBaseMonster` is always pair `(creature_material, default)`. See `reference.md`.

## What this pack writes

1. New mats in `mod_materials_zzzz_dogma_mutants.ltx`: `creatures\fast`, `creatures\lurker`, `creatures\chimera`.
2. Creature `material =` remaps onto those banks plus vanilla `hoof` / `medium` / `large`.
3. One pair file, six sections, `step_sounds` only:

```
@[creatures\fast@default]
@[creatures\hoof@default]
@[creatures\medium@default]
@[creatures\large@default]
@[creatures\lurker@default]
@[creatures\chimera@default]
```

4. `step_params` keys for cycles the engine actually plays (Larkin table + missing AOM/jump names).
5. OGGs under `sounds/test_steps/`. Engine path `test_steps\<file>`.

No ground-surface pairs. No empty `step_sounds =`. No reverse `:inherit`. Collide / break stay vanilla.

## OGG comments

X-Ray v3: `min_dist 1`, `max_dist 300`, `base_volume 1.0`, `max_ai_dist 300` (engine ctor defaults). `game_type` `SOUND_TYPE_MONSTER_STEP`.

All files 44100 Hz mono. After export: `py -3 stamp_ogg_comments.py` (`dir=` in `stamp_ogg_comments.ini`). `--check` to inspect.
