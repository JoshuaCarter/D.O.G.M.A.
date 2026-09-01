# Fix Footsteps SFX

Mutant + stalker NPC steps (and exo servo). Not player (`creatures\actor`). Not collide/break banks.

Every step this pack plays is a pair we wrote plus an OGG under `sounds/dogma_steps/<bank>/`. No vanilla-path copies.

## Comment defaults (all shipped OGGs)

X-Ray v3 blob: `min_dist 1`, `max_dist 40`, `base_volume 1.0`, `max_ai_dist 40`.

All files are 44100 Hz mono.

Close volume is `base_volume` (clamped while dist <= min_dist). Raising `max_dist` does not make a step louder in your face. It only stretches the fade, so a quiet sample is still audible farther out. That is the point of 40.

Engine ctor default without a comment is 300. That is not a footstep range. 40 is the pack cap.

## Hoof (boar / flesh)

These were comment-boosted here (`base_volume 2.2`, `min_dist 8`, `max_dist 70`) and re-encoded larger. Samples are the Solarint / vanilla hoof set (the usual correct ones). Restored from Solarint, then stamped to the defaults above. Live path: `dogma_steps\hoof\`.

## Missing step files

None. Every non-empty `step_sounds` path in this tweak has an OGG here.

Empty `step_sounds` on purpose:

- `creatures\rodent` - silent (rats / tushkano are remapped to medium)

Pairs are `@[` and `step_sounds` only. Files are named `material_pairs_zzzz_dogma_*.ltx` so DLTX include order is last (MO2 last is not enough: `material_pairs_*.ltx` is filename order). Collide / break stay vanilla.
