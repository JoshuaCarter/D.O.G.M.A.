# Fix Footsteps SFX

Mutant steps only. Not player. Not stalker NPCs.

Engine lookup for `CBaseMonster` is always pair `(creature_material, default)`. See `reference.md`.

## What this pack writes

1. New mats in `mod_materials_zzzz_dogma_mutants.ltx`: `creatures\fast`, `creatures\lurker`, `creatures\chimera`.
2. Creature `material =` remaps onto those banks plus vanilla `hoof` / `medium` / `large`.
3. Pair file overlays `@default` and the ground surfaces. Another VFS file (`material_pairs_fast.ltx`) creates empty `fast|earth` pairs; those get our `step_sounds`.
4. Larkin `step_params` (overrides Larkin if present) plus missing cycle names the engine plays.
5. OGGs under `sounds/mutant_steps/`.

No empty `step_sounds =`. No reverse pair sections. Collide / break stay vanilla.

## OGG comments

X-Ray v3: `min_dist 1`, `max_dist 300`, `base_volume 1.0`, `max_ai_dist 300` (engine ctor defaults). `game_type` `SOUND_TYPE_MONSTER_STEP`.

All files 44100 Hz mono. After export: `py -3 stamp_ogg_comments.py` (`dir=` in `stamp_ogg_comments.ini`). `--check` to inspect.


## Mutant Categories

Mutants have these sfx categories (stalker = human steps, i.e. we don't handle it)

|mutant type (inc partial match)|foot type|size (baseline)|
|-|-|-|
|baby_yaga|stalker|mid|
|zombie|stalker|mid|
|bloodsucker|foot|mid|
|boar|hoof|mid|
|burer|foot|mid|
|cat|paw|mid|
|chimera|claw|mid|
|dog|paw|mid|
|flesh|hoof|mid|
|fracture|foot|mid|
|gigant|foot|large|
|karlik|foot|mid|
|lurker|claw|mid|
|bibliotekar|claw|large|
|fracture|foot|mid|
|controller|foot|mid|
|psysucker|foot|mid|
|pseudodog|paw|mid|
|psydog|paw|mid|
|psysucker|foot|mid|
|rat|paw|small|
|snork|foot|mid|
|tushkano|claw|small|

### Mutant Overrides

|mutant type (exact match)|foot type|size (override)|
|-|-|-|
|bloodsucker_strong_big|foot|large|
|burer_big|foot|large|
|gigant_very_big|foot|huge|

## OGG files are broken into these categories

|foot type|foot size|surface type|file name|
|-|-|-|-|
|hoof|mid|soft|hoof_{mid}_soft_n|
|hoof|mid|hard|hoof_{mid}_hard_n|
|hoof|mid|hard|hoof_{mid}_wet_n|
|paw|mid|soft|paw_{small|mid}_soft_n|
|paw|mid|hard|paw_{small|mid}_hard_n|
|paw|mid|hard|paw_{small|mid}_wet_n|
|claw|mid|soft|claw_{small|mid|large}_soft_n|
|claw|mid|hard|claw_{small|mid|large}_hard_n|
|claw|mid|hard|claw_{small|mid|large}_wet_n|
|foot|mid|soft|foot_{mid|large|huge}_soft_n|
|foot|mid|hard|foot_{mid|large|huge}_hard_n|
|foot|mid|hard|foot_{mid|large|huge}_wet_n|
