# Fix Footsteps SFX

Mutant + stalker NPC steps (and exo servo). Not player (`creatures\actor`). Not collide/break banks.

Every step this pack plays is a pair we wrote plus an OGG under `sounds/dogma_steps/<bank>/`. No vanilla-path copies.

## Comment defaults (all shipped OGGs)

X-Ray v3 blob: `min_dist 1`, `max_dist 40`, `base_volume 1.0`, `max_ai_dist 40`.

All files are 44100 Hz mono. After any comment stamp, rewrite that OGG page CRC. libvorbis rejects a bad checksum (`ov_info` NULL, boot fatal).

Close volume is `base_volume` (clamped while dist <= min_dist). Raising `max_dist` does not make a step louder in your face. It only stretches the fade, so a quiet sample is still audible farther out. That is the point of 40.

Engine ctor default without a comment is 300. That is not a footstep range. 40 is the pack cap.

After Audacity export, restamp (rewrites page CRC):

`py -3 stamp_ogg_comments.py sounds/creature_steps/water`

Edit `stamp_ogg_comments.ini` next to the script. `--check` to inspect. CLI `--max` / `--vol` / `--min` / `--ai` override the ini.

## Hoof (boar / flesh)

These were comment-boosted here (`base_volume 2.2`, `min_dist 8`, `max_dist 70`) and re-encoded larger. Samples are the Solarint / vanilla hoof set (the usual correct ones). Restored from Solarint, then stamped to the defaults above. Live path: `dogma_steps\hoof\`.

## Missing step files

None. Every non-empty `step_sounds` path in this tweak has an OGG here.

Empty `step_sounds` on purpose:

- `creatures\rodent` - silent (rats / tushkano are remapped to medium)

Pairs are `@[` and `step_sounds` only, creature-first (`creatures\X@surface`). No reverse `:inherit` sections: DLTX unions parents across files, so two banks writing opposite `:A@B` / `:B@A` is a fatal cycle. Engine RT table already maps both lookup orders from one pair. Files are named `material_pairs_zzzz_dogma_*.ltx` so include order is last (MO2 last is not enough). Collide / break stay vanilla.

## Surface Meta Buckets For Creature Materials
Engine surface -> meta bucket. For creatures only.

### soft
- `materials\earth`
- `materials\dirt`
- `materials\grass`
- `materials\sand`
- `materials\gravel`
- `materials\earth_death`
- `materials\earth_slide`
- `materials\bush`
- `materials\bush_sux`
- `materials\cloth`
- `objects\clothes`
- `objects\dead_body`
- `objects\monster_body`
- `objects\car_wheel`
- `creatures\human`
- `creatures\human_head`
- `creatures\actor`
- `creatures\phantom`
- `creatures\large`
- `creatures\medium`
- `creatures\small`
- `creatures\hoof`

### hard
- `default`
- `default_object`
- `materials\wood`
- `materials\wooden_board`
- `materials\tree_trunk`
- `materials\fake_ladders_woods`
- `objects\large_furniture`
- `materials\concrete`
- `materials\asphalt`
- `materials\bricks`
- `materials\flooring_tile`
- `materials\stucco`
- `materials\shifer`
- `objects\concrete_box`
- `materials\glass`
- `objects\glass`
- `objects\bottle`
- `materials\fake`
- `materials\fake_slide`
- `materials\occ`
- `materials\death`
- `objects\small_box`
- `objects\large_weapon`
- `objects\small_weapon`
- `objects\bullet`
- `objects\knife`
- `materials\metal`
- `materials\metal_plate`
- `materials\metal_pipe`
- `materials\tin`
- `materials\setka_rabica`
- `materials\fake_ladders`
- `objects\tin_can`
- `objects\car_cabine`
- `objects\barrel`
- `objects\metal_box`
- `objects\small_metal_trash`
- `objects\large_metal_trash`
- `objects\fuel_can`

### water
- `materials\water`
- `materials\water_radiation`

## Creature Materials To Surface Meta Bucket

|Creature|soft|hard|water|
|---|---|---|
|small|hoof_ground|hoof_hard|water_small|
|medium|hoof_ground|hoof_hard|water_small|
|large|hoof_ground|hoof_hard|water_small|
|fast|hoof_ground|hoof_hard|water_small|
|hoof|hoof_ground|hoof_hard|water_small|
|lurker|hoof_ground|hoof_hard|water_small|
|chimera|hoof_ground|hoof_hard|water_small|
|zombie|hoof_ground|hoof_hard|water_small|