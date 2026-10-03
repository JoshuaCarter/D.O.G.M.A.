# Mutant step override: engine proof

Source tree: `c:\gamma_dev\xray-monolith-2026.7.13\xray-monolith-2026.7.13`

## 0. Where the last proof was wrong

22:47 boot still silent. These claims did not prove what they said:

1. **"Pack ships one pair file."** Src does. The running VFS did not. Same log `Adding new material pair creatures\fast | materials\earth` (and default_object, grass, ...). Those sections are not in `mod_material_pairs_zzzz_dogma_mutants.ltx`. Root `#include "material_pairs_*.ltx"` pulls leftover `material_pairs_zzzz_*.ltx` from **any** mod / stale deploy. Overlay proof was about src, not what `GameMtlLib` walked.

2. **"Lua play of `mutant_steps\\test` proves the OGG path."** Wrong file. Wrong if no position (`xsound` uses `s2d`). `s3d` is flags `0` (same as `play_next`) but that still is not `CStepManager` calling `play_next`. `hoof_ground_1` / `step_fast-01` / `test` are the same 5065-byte beep. Hearing `test` does not prove a step event fired.

3. **"LTX `stand_run_0` present means steps arm."** `SStepInfo.disable` defaults **true** (`step_manager_defs.h:38`). Map fill is `ID_Cycle_Safe` at `reload`. Release miss = no log. LTX dump is not `m_steps_map`.

4. **"max_dist 50 is correct because the step gate is 50 m."** Different checks. Step gate: camera-to-monster, then `play_next`. 3D cull: listener-to-emitter vs **OGG** `max_distance` (`SoundRender_Emitter_FSM.cpp:352-356`). Ctor default is **300** (`SoundRender_Source.cpp:9-12`). Attenuation is `(max-dist)/(max-min)`. 50 was never compared to a vanilla working step OGG (Anomaly `sounds.db0` packed; not unpacked here).

5. **"Global != torso so `on_animation_start` fires."** Vanilla: torso stays invalid (`SAnimationPart::init`), so yes. Not dumped. AOM setting torso == global would skip (`control_animation.cpp:149`). `disable` stays true.

6. **"Always `(creature, default)`."** Engine: `SetPLastMaterialIDX` skipped for `CBaseMonster`, `get_current_pair` reads `m_last_material_idx` (stays default). **Never dumped at runtime.** Leftover earth pairs in VFS made the experiment invalid. If last is actually ground and leftovers die, new mats (`fast`) have **null** pair -> silent.

23:16 / 23:28 boots:

- VFS has `material_pairs_fast.ltx` (not DOGMA). That file added `fast|earth` and every other surface.
- Probe logged `engine-play 2d+3d mutant_steps\hoof_ground_1` at first_update. User heard nothing.
- That call is **not** proof the file is silent. Decoded PCM of `mutant_steps\hoof_ground_1.ogg`: 44100, ~57 ms, peak full-scale (32768). Same sha on every `mutant_steps` file.
- `play_no_feedback` does **not** die on Lua GC. `i_destroy_source` is a no-op (`SoundRender_Core_SourceManager.cpp:29-32`). Emitter keeps `owner_data`.
- 57 ms at `actor_on_first_update` is load-fade. Easy to miss. Not a walk proof. Not a mute proof.

23:42 boot: `held 2d control=1 beep=2` at log 9887, then `intro_start game_loaded` at 9902. Timer ran **during load intro**. User heard long `tinnitus3a` (caught the tail). 57 ms beep was already over. Sitting on the load screen: both finished before enter.

Play works. File path works (handle 2). Timing was the probe hole.

23:56: dismiss ran. `beep_ms=0 tinnitus_ms=6000 mutant_ms=0`. Length is not "too short". Vanilla plays. Ours do not. Both our banks.

Stamp was packing **one packet per page**. Pre-stamp `mutant_steps` is 3 pages: ident (BOS) | comment+setup | audio (EOS). Vorbis I / X-Ray layout. After stamp: 40 pages. Engine `ov_pcm_total` 0. miniaudio still decodes.

`mutant_steps` also used libVorbis 20200704 / ffmpeg Lavf61. Engine decoder is the 2005-era one (`Xiph.Org libVorbis I 20050304` on working mutant files).

Stamp now writes the 3-page layout. Restored pre-stamp mutant (2005) + original test beep, restamped. Need `mutant_ms` / `beep_ms` > 0.

What 22:47 did prove: `fast|default` added, `hoof|default` changed, no `Can't find sound` / bad rate for `mutant_steps`, live `material` / `step_params` strings. That is overlay + file exist. Not play.

---

Not a guess from the **pointer wire**: `CStepManager` reads pair `(m_my_material_idx, m_last_material_idx)`. For `CBaseMonster` that second index is not hooked to the foot ray.

---

## 1. Boot order

`CGamePersistent::OnAppStart` calls `GMLib.Load()` (`xrGame/GamePersistent.cpp:156-159`).

Inside `CGameMtlLibrary::Load` (`xrEngine/GameMtlLib.cpp`):

1. Open `gamemtl.xr`. Missing file: log + return. Pairs never overlay.
2. Load materials from xr chunk `GAMEMTLS_CHUNK_MTLS`.
3. Optional overlay `materials\materials.ltx` (`CInifile`, DLTX mods apply).
4. Load pairs from xr chunk `GAMEMTLS_CHUNK_MTLS_PAIR`. Each pair `SGameMtlPair::Load` calls `CreateSounds(StepSounds, ...)` (`xrEngine/GameMtlLib_Engine.cpp:123-126`).
5. Optional overlay `materials\material_pairs.ltx` (`GameMtlLib.cpp:236-243`).
6. Build `material_pairs_rt` (`GameMtlLib.cpp:346-356`).

Materials must exist before pairs. New creature mat `creatures\fast` is added in step 3. Pair `creatures\fast@default` is added in step 5.

Boot log for this pack:

```
[materials.ltx] Adding new material creatures\fast, id 103
[material_pairs.ltx] Adding new material pair creatures\fast | default, id 1788
[material_pairs.ltx] Changing existing material pair creatures\hoof | default, id 996
```

Those two lines are the `@default` overlays. 22:47 also added `fast|earth` etc. from leftover VFS files. Do not treat src as the loaded set.

---

## 2. How DLTX finds pair files

Root file name is `material_pairs`. Constructor: `CInifile(path, TRUE)` (`GameMtlLib.cpp:243`).

After the root file is parsed, DLTX scans the **same directory** (`xrCore/Xr_ini.cpp:551-612`):

| Mask | Role |
|---|---|
| `mod_material_pairs_*.ltx` | loaded as DLTX mods |
| `material_pairs_*.ltx` | NOT auto-loaded. Only pulled if root `#include`s them |

Root `gamedata/materials/material_pairs.ltx:34` has `#include "material_pairs_*.ltx"`. That include is depth `+1`. It does **not** match `mod_material_pairs_*.ltx`.

Depth (`xr_ini.h:20-21`, `Xr_ini.cpp:569-611, 1321-1327`):

| Source | depth |
|---|---|
| root `material_pairs.ltx` | 0 |
| its `#include`s | 1 |
| 1st `mod_` file (alpha order) | -200 |
| 2nd `mod_` file | -400 |
| nth | `-200 * n` |

Lower depth wins (`Xr_ini.cpp:416-427`). Later `mod_` filename (alpha) wins conflicts.

Ambiguous skip (`Xr_ini.cpp:577-593`): if `material_pairs_foo.ltx` exists, skip mods matching `mod_material_pairs_foo_.+.ltx`. `mod_material_pairs_zzzz_dogma_fast.ltx` does **not** match that (needs extra `_...` before `.ltx`). It loads.

This pack ships one file: `mod_material_pairs_zzzz_dogma_mutants.ltx`. Correct name.

Same rule for materials: `mod_materials_*.ltx` against root `materials.ltx`. Pack file: `mod_materials_zzzz_dogma_mutants.ltx`. Boot log added `creatures\fast`.

---

## 3. `@[` vs `![` vs `[`

Parsed in `Xr_ini.cpp:655-769`.

| Prefix | If no LTX `[section]` exists yet | Use for xr-only pairs |
|---|---|---|
| `[name]` | creates base. Duplicate `[name]` = fatal (`Xr_ini.cpp:374-377`) | no (fatal if some include already defined it) |
| `![name]` | override only. No base created. Dropped + warning (`Xr_ini.cpp:1383-1396`) | **no. Section never reaches GameMtlLib** |
| `@[name]` | override + empty base created (`Xr_ini.cpp:762-769, 892-904`) | **yes** |

`gamemtl.xr` pairs are not LTX bases. DLTX only merges LTX. GameMtlLib then walks the merged `CInifile` and patches the xr vector (`GameMtlLib.cpp:244-321`).

So: `@[creatures\hoof@default]` creates an LTX section, GameMtlLib finds xr pair `(hoof, default)`, logs `Changing existing`, applies keys.

`![creatures\hoof@default]` with no LTX `[creatures\hoof@default]` anywhere: DLTX drops it. GameMtlLib never sees it. xr sounds stay.

New pair (no xr row): `@[creatures\fast@default]` -> GameMtlLib `Adding new` + `SetPair(GetMaterialID(a), GetMaterialID(b))`.

LTX match is **ordered** (`GameMtlLib.cpp:266-268`): `m1==mtl0 && m2==mtl1`. Runtime lookup is **symmetric** (section 5). Write creature-first: `creatures\X@default`. Do not also write `default@creatures\X` (duplicate pair object; last RT write wins).

Engine comment in `material_pairs.ltx:25` says define swapped `:inherit` pairs. That is editor/docs leftover. RT table already writes both orientations. DLTX parent union across files can fatal-cycle if two banks inherit each other. This pack does not write reverse sections.

---

## 4. What GameMtlLib applies from a pair section

Only keys that exist after DLTX merge (`GameMtlLib.cpp:291-320`):

- `step_sounds` -> `CreateSoundsImpl` (CLEAR then parse)
- `breaking_sounds`, `collide_sounds`, `collide_particles`, `collide_marks` same pattern

Omitted key: xr value stays.

`step_sounds =` (empty, key present): `line_exist` true (`Xr_ini.cpp:1572-1578`) -> `CreateSoundsImpl` clears -> empty vector. **Wipes vanilla steps.**

`!step_sounds` (DLTX delete): key gone -> overlay skipped -> xr steps stay.

`CreateSounds` (`GameMtlLib_Engine.cpp:27-60`): split on comma, `snd.create(name, st_Effect, sg_SourceType)`, always `push_back`. Cap `GAMEMTL_SUBITEM_COUNT+2` = 22 (`GameMtlLib.h:33`). Wildcard `*` expands via `FS.file_list`.

Missing ogg: `Can't find sound` + fallback `$no_sound.ogg` (`SoundRender_Source_loader.cpp:173-178`). Still a handle.

Not 44100: `Invalid source rate` + `LoadWave` false (`SoundRender_Source_loader.cpp:79-85`). Handle still exists. Play is not skipped on rate fail (`SoundRender_Core.cpp:353-355` checks handle only).

This boot: no `Can't find sound` / `Invalid source rate` for `mutant_steps\`. Pair overlay ran. Sounds were created.

---

## 5. Runtime pair lookup

IDs vs indices (`GameMtlLib.h:318-390`):

- `SGameMtl::ID` = auto number from xr / overlay (`++biggestId`). Not a vector slot.
- `GetMaterialID(name)` -> that field.
- `GetMaterialIdx(name)` / `GetMaterialIdx(ID)` -> `it - materials.begin()` (0..N-1).
- Overlay stores IDs in `mtl0/mtl1` (`SetPair`).
- RT fill: `GetMaterialIdx(mtl0/mtl1)` then writes both `[i][j]` and `[j][i]` (`GameMtlLib.cpp:352-355`).
- `GetMaterialPair(idx0, idx1)` reads `rt[idx1 * N + idx0]`.

Consistent. Both orders hit the same `SGameMtlPair*`.

`CMaterialManager::Load`: `m_my_material_idx = GetMaterialIdx(section.material)` (`material_manager.cpp:40`).

`CMaterialManager::reinit` (`material_manager.cpp:58-69`):

```
m_last_material_idx = GetMaterialIdx("default");
if (entity is NOT CBaseMonster)
    SetPLastMaterialIDX(&m_last_material_idx);
```

Comment in source: mobs do not get the real (ground) material.

`get_current_pair` (`material_manager_inline.h:21-24`):

```
update_last_material();   // writes physics lastMaterialIDX
return GetMaterialPair(m_my_material_idx, m_last_material_idx);
```

For `CBaseMonster`, `SetPLastMaterialIDX` was skipped. Raycast writes `CPHCharacter::lastMaterialIDX`. `m_last_material_idx` stays `"default"`.

**Mutant steps = pair `(material, default)`.**

Actor and stalker also use `CStepManager::update` (`Actor.cpp:1272-1273`, `ai_stalker.cpp:1098-1099`). `CMaterialManager::update` has no callers. The split is the pointer wire: non-monsters get ground in `m_last_material_idx`, `CBaseMonster` does not. Ground pairs (`creatures\fast@materials\earth` etc.) are unused by mutant `CStepManager`. This pack is mutant-only.

`default` is a real material in xr. Boot log shows both `fast|default` (new) and `hoof|default` (changed).

---

## 6. When a step actually plays

Call chain (all `CBaseMonster`):

1. `select_animation` builds cycle name `prefix + index` (`control_animation_base.cpp:244-245`). Example: `"stand_run_" + "0"` -> `stand_run_0`.
2. `UpdateCL` -> `CStepManager::update(false)` then `control().update_frame()` (`base_monster.cpp:351, 356`).
3. `CControlAnimation::play_part` -> `LL_PlayCycle` -> `on_animation_start(motion, blend)` if the part is not the torso motion (`control_animation.cpp:149-150`). Global anim on snork/boar is not torso. It fires.
4. `on_animation_start` (`step_manager.cpp:117-146`): `m_steps_map.find(motion_id)`. Miss -> `disable=true`. **No log in release** (`#ifdef DEBUG` only).
5. Later frames: `update` (`step_manager.cpp:159-218`). Early out if `disable` or `!m_blend`.
6. Dist gate: `distance_to(camera) < 50` (`step_manager.cpp:165-166`). Farther: no sound, timing still advances.
7. `is_on_ground()` default true. `CBaseMonster` does not override (`step_manager.h:54`).
8. `get_current_pair()`. Null pair: `break` (`step_manager.cpp:197-198`).
9. Non-actor: `play_next` (`step_manager.cpp:216-218`).
10. `play_next` (`step_manager.cpp:333-364`): empty `StepSounds` -> return. Else `play_no_feedback(object, 0, 0, pos+0.5y, &power)`.

`CStepManager::reload` (`step_manager.cpp:35-115`) builds `m_steps_map`:

- Read `step_params` section name from the creature section.
- Every line: key = full cycle name, value = `cycles, (time, power) * LegsCount`.
- `ID_Cycle_Safe(key)` exact string (`SkeletonAnimated.cpp:199-212`). No auto `_0`.
- Missing motion: skip, release silent.
- Called from `CBaseMonster::reload` (`base_monster_startup.cpp:220-225`).

`step_params` key must be the played cycle (`stand_run_0`), not the `AddAnim` prefix (`stand_run_`).

`@[section]` / `![section]` on an existing system.ltx section **merges**. Keys we do not list stay. `![` on a section that has no LTX base is dropped (same as pairs).

Snork engine prefixes (`snork.cpp:68-71, 117-118`):

- walk `stand_walk_fwd_` -> `stand_walk_fwd_0`
- run `stand_run_` -> `stand_run_0` (ACT_RUN)
- jump `stand_attack_2_0`, `stand_attack_2_1`, `stand_somersault_0`

Boar engine prefixes (`boar.cpp:65-71`):

- walk `stand_walk_fwd_` -> `stand_walk_fwd_0`
- run `stand_run_fwd_` -> `stand_run_fwd_0` (not `stand_run_0`)
- damaged run `stand_run_dmg_`

Lua probe on 22:17 boot:

```
near boar_normal mat=creatures\hoof step_params=m_boar_step_params
m_boar_step_params has no stand_run_0
near snork_normal mat=creatures\fast step_params=m_snork_step_params
m_snork_step_params stand_run_0 = 1,0.01,1.0,0.05,1.0,0.50,1.0,0.55,1.0
```

`stand_run_0` missing on boar is expected (boar does not play that name). Vanilla `m_boar_step_params` still supplies walk/run keys via DLTX merge. Our `@[m_boar_step_params]` only adds AOM attack-run keys.

Reload map miss is the only release-silent path that can kill a moving mutant that already has a loaded pair.

---

## 7. Play vs lua probe

`play_no_feedback` (`SoundRender_Core.cpp:353-380`): 3D unless `sm_2D` or stereo file. Sets pos + volume.

3D cull (`SoundRender_Emitter_FSM.cpp:339-387`):

- dist > `max_distance` (from ogg comment) -> volume 0, no play
- `smooth_volume = base_volume * att * psSoundVEffects * psSoundVFactor * occluder`
- if `< psSoundCull` (0.01) -> no play

`SOUND_TYPE_MONSTER_STEP` (`0x20008000`) is not a listener mute. Used for AI events. `g_type==0` skips AI notify only.

Lua `xsound.play` / `sound_object` 2D: no max_dist cull, no occlusion. Same ogg can beep from script and stay silent from 3D `play_no_feedback` if 3D cull hits.

Stamp **ctor defaults**: `min_dist=1`, `max_dist=300`, `base_volume=1`, `max_ai_dist=300`. `game_type` `SOUND_TYPE_MONSTER_STEP` (`CreateSounds` uses `sg_SourceType` so the OGG blob is `g_type`). `g_type` is AI notify, not a listener mute.

Lua `sound_object` ctor uses `SOUND_TYPE_NO_SOUND`, not `sg_SourceType`. Playback still 3D. Do not treat lua hear as `play_next`.

`i_destroy_source` is empty. Unheld `play_no_feedback` keeps playing. 23:42 2D ran before `intro_start game_loaded`. See section 0.

---

## 8. Proven recipe (do this, nothing else)

To make a mutant play a new step bank:

1. **Material**
   - File: `gamedata/materials/mod_materials_<id>.ltx`
   - `@[creatures\<name>]` plus whatever flags you need (copy an existing creature mat).
   - Creature section: `material = creatures\<name>`
2. **The one pair `CStepManager` reads**
   - File: `gamedata/materials/mod_material_pairs_<id>.ltx`
   - `@[creatures\<name>@default]`
   - `step_sounds = dir\file1, dir\file2, ...` (paths under `$game_sounds$`, no `.ogg`)
   - Do not write empty `step_sounds =`
   - Extra surfaces (`@materials\earth` etc.) do not change mutant `CStepManager` playback
3. **OGGs**
   - 44100 Hz
   - Present at boot (pairs load at `OnAppStart`, not on spawn)
   - X-Ray v3 comment: match ctor (`min 1`, `max 300`, `vol 1`). Do not stamp 50 to "match" the step gate
4. **step_params**
   - Creature `step_params = <section>`
   - Every played cycle name listed, exact (`stand_run_0`, `stand_run_fwd_0`, jump names)
   - `@[` to add keys to an existing section. `![` only if that section already exists as LTX base (vanilla snork section does)
   - First number = cycles (>=1). Then `(time, power)` per leg. Power is the play volume. `0` = that foot silent
   - `LegsCount` on the creature must match how many pairs you write
5. **Distance**
   - Listener < 50 m (`step_manager.cpp:166`)
   - Listener < ogg `max_dist` for 3D

To replace vanilla hoof/boar steps: step 2 on `@[creatures\hoof@default]` is sufficient. Boot already did that (`Changing existing ... id 996`).

---

## 9. What this pack already proved at boot

| Check | Result |
|---|---|
| `creatures\fast` material created | yes, id 103 |
| pair `fast\|default` created | yes, id 1788 |
| pair `hoof\|default` overlaid | yes, id 996 |
| `step_sounds` keys present on those `@[` sections | yes (`mutant_steps\...`) |
| `mutant_steps` missing / bad rate at boot | no log lines |
| snork `material` / `step_params` live | `creatures\fast`, `m_snork_step_params`, `stand_run_0` present |
| boar `material` / `step_params` live | `creatures\hoof`, `m_boar_step_params` |
| lua `mutant_steps\test` | **not a proof** (wrong file / not `play_next`) |

Overlay + file-exist is all the boot log can say. Stamp 3-page layout is what made engine duration > 0. Walk proof is `CStepManager::play_next`, not a lua 2D hold.

---

## 10. README

`README.md` matches the `@default` recipe. That recipe is only as good as last-material == default **and** a clean VFS (no leftover `material_pairs_zzzz_*.ltx`). Actor / stalker steps are the same `CStepManager` function with last-material wired to ground. Out of scope for this pack.
