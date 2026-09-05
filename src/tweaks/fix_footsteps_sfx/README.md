# Fix Footsteps SFX

Mutant steps only. Not player. Not stalker NPCs.

Engine lookup for `CBaseMonster` is always pair `(creature_material, default)`. See `reference.md`.

## What this pack writes

1. New mats in `mod_materials_zzzz_dogma_mutants.ltx`: `creatures\fast`, `creatures\lurker`, `creatures\chimera`.
2. Creature `material =` remaps onto those banks plus vanilla `hoof` / `medium` / `large`.
3. Pair file overlays `@default` and the ground surfaces. Another VFS file (`material_pairs_fast.ltx`) creates empty `fast|earth` pairs; those get our `step_sounds`.
4. Larkin `step_params` (overrides Larkin if present) plus missing cycle names the engine plays.
5. OGGs under `sounds/mutants/`.

No empty `step_sounds =`. No reverse pair sections. Collide / break stay vanilla.

## OGG comments

X-Ray v3: `min_dist 1`, `max_dist 300`, `base_volume 1.0`, `max_ai_dist 300` (engine ctor defaults). `game_type` `SOUND_TYPE_MONSTER`.

All files 44100 Hz mono. After export: `py -3 stamp_ogg_comments.py` (`dir=` in `stamp_ogg_comments.ini`). `--check` to inspect.

## SFX Structure

- Mutants will map to ogg files partially based on their foot type: hoof, paw, claw, or foot. Each of those will have unique sounds.
- Mutants will also map to ogg files based on their size.
- Mutants will also map to ogg files based on the surface they stand on, we will break those down into soft, hard.
- Mutants may map to multiple ogg files for any combination of the above.
- Water sounds are shared more broadly across foot types

The result of the above rules is a file naming convention of one of:
- `{hoof|paw|claw|foot}_{small|mid|large|huge}_{soft|hard}_{1-n}.ogg`
- `water_{small|mid|large|huge}_{1-n}.ogg`

|mutant type (inc partial match)|foot type|size (baseline)|
|-|-|-|
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
|baby_yaga|n/a|n/a|
|zombie|n/a|n/a|

|mutant type (exact match)|foot type|size (override)|
|-|-|-|
|bloodsucker_strong_big|foot|large|
|burer_big|foot|large|
|gigant_very_big|foot|huge|

### Resulting OGG files (not including generated step files)

hoof_mid_soft_n
hoof_mid_hard_n
paw_mid_soft_n
paw_mid_hard_n
paw_small_soft_n
paw_small_hard_n
claw_small_soft_n
claw_small_hard_n
claw_mid_soft_n
claw_mid_hard_n
claw_large_soft_n
claw_large_hard_n
foot_mid_soft_n
foot_mid_hard_n
foot_large_soft_n
foot_large_hard_n
foot_huge_soft_n
foot_huge_hard_n

water_small_n
water_mid_n
water_large_n
water_huge_n
