# Fix Footsteps SFX

Mutant steps only. Not player. Not stalker NPCs.

Engine lookup for `CBaseMonster` is always pair `(creature_material, default)`. See `reference.md`.

## What this pack writes

1. New mats in `mod_materials_zzzz_dogma_mutants.ltx`: `creatures\fast`, `creatures\lurker`, `creatures\chimera`.
2. Creature `material =` remaps onto those banks plus vanilla `hoof` / `medium` / `large`.
3. Pair file overlays `@default` and the ground surfaces. Another VFS file (`material_pairs_fast.ltx`) creates empty `fast|earth` pairs; those get our `step_sounds`.
4. `step_params` keys for cycles the engine actually plays (Larkin table + missing AOM/jump names).
5. OGGs under `sounds/test_steps/` (loud beep so a step is obvious).

No empty `step_sounds =`. No reverse pair sections. Collide / break stay vanilla.

## OGG comments

X-Ray v3: `min_dist 1`, `max_dist 300`, `base_volume 1.0`, `max_ai_dist 300` (engine ctor defaults). `game_type` `SOUND_TYPE_MONSTER_STEP`.

All files 44100 Hz mono. After export: `py -3 stamp_ogg_comments.py` (`dir=` in `stamp_ogg_comments.ini`). `--check` to inspect.
