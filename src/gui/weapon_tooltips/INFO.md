# Weapon Tooltips

## Important Notes

- New Game Loadout: arms tooltip builders at the main menu so the first-session character-creation screen already shows G.A.M.M.A./DOGMA tooltips, class icons, loadout sort (misc → weapons → outfits, small to large), inventory cold-boot order, and point costs on icons.
- Uses ballistic calculations from live CQC grok_bo / ammo (CQC loads above GBOOBS).
- Assumed range is set by MCM (default 30m). For damage numbers, barrel condition is treated as 100% unless you toggle to use the live WPO barrel part.
- Damage numbers also always factor in current difficulty and weapon hit power.
- Silencers (when the matching MCM toggles are on): stalker damage/AP get CQC's x1.07; Burst Size uses cam-recoil _k; Bullet Drop uses muzzle-speed _k. Mutant damage never gets the x1.07. Other attachments, artifacts, medicines, etc. are ignored.
- Ammo rows (damage table, Dmg vs Stalkers, and Dmg vs Mutants) default to the same order as the tooltip ammo icons. MCM can instead sort Dmg desc or AP desc.

## Enhanced weapon stats

New optional stat list items:

$\color{#3cb371}{\textsf{Spread}}$

- Min - max cone in degrees: standing-ADS (weapon fire_dispersion_base) - hip-fire while running (min + movement penalty).
- E.g. "0.35 - 1.20" means up to 0.35 degrees off center when still, up to 1.20 degrees while hip-running.
- Uses normal run speed, not sprint. Aiming mostly cancels the movement add-on (back to the min).

$\color{#3cb371}{\textsf{Dispersion}}$

- Standing Spread (min) angle translated into distance off target at the MCM Dispersion/Drop range (default 200m): range x tan(Spread min).

$\color{#3cb371}{\textsf{Hit Power}}$

- New stat that shows what % of base ammo damage this weapon inflicts.
- Useful for comparing weapons that use the same ammo types.

$\color{#3cb371}{\textsf{Dmg vs Stalkers}}$

- New stat for per-ammo torso damage vs stalkers (does not account for armor).
- Math: (hit_power / range_factor) x ammo_k_hit x torso_mult (0.9) x 1.1 x pellets x barrel_cond x difficulty x npc_ammo_mult x silencer (1.07 if equipped and stats silencer MCM on).
- pellets = buck_shot x (MCM Pellets assumed to hit % / 100), min 1; single-projectile ammo stays 1.
- Per-ammo values are colored like the damage-table Type labels.

$\color{#3cb371}{\textsf{Dmg vs Mutants}}$

- New stat for per-ammo damage vs mutants (before mutant specific modifiers).
- Math: (hit_power / range_factor) x ammo_k_hit x pellets x mut_mult (0.85) x mut_ammo_mult x barrel_cond x difficulty (no silencer; matches CQC).
- pellets = buck_shot x (MCM Pellets assumed to hit % / 100), min 1; single-projectile ammo stays 1.
- Per-ammo values are colored like the damage-table Type labels.

$\color{#3cb371}{\textsf{Bullet Drop}}$

- How far a bullet drops over the MCM Dispersion/Drop range (default 200m).
- With Stat List silencer MCM on and a silencer attached: multiplies muzzle velocity by bullet_speed_k (Boomsticks cans are often ~0.9; some lower), so drop increases with a suppressor.

$\color{#3cb371}{\textsf{Barrel Wear}}$

- Per-shot chance that the barrel specifically loses 1% (shown as e.g. 1.2% @ -1%).
- Independent of other parts: WPO also rolls a separate (higher) chance to ding a random non-barrel part. Barrel chance is not shared or divided by part count.
- Math: (weapon_degradation x condition_shot_dec x 15000) / 10 percent.
- weapon_degradation by economy: Tourist 0.5, Scavenger 0.75, Survivalist 1.0. Silencers do not change this.

$\color{#3cb371}{\textsf{Burst Size}}$

- How many consecutive ADS shots (at max fire rate) it takes for the camera to climb to the MCM Burst Size climb target (default 3 degrees).
- With Stat List silencer MCM on and a silencer attached: multiplies ADS cam kick by zoom_cam_dispersion_k (and inc _k when present), so Burst Size goes up when the suppressor softens recoil.

## Damage table

A table showing how much damage this weapon does with each ammo type vs different body parts and armor values.
(see Useful Info for torso/head armor floats and example outfits)

$\color{#3cb371}{\textsf{Columns}}$:
- $\color{#4aa3ff}{\textsf{Type}}$: Ammo type used for damage calculations on this row (colored by ammo class: FMJ yellow, HP/BUCK blue, AP orange, etc.). Row order defaults to the tooltip ammo icons order (MCM can sort by Dmg or AP instead).
- $\color{#4aa3ff}{\textsf{AP}}$: Armor penetration colored from the MCM low end -> $\color{#ffffff}{\textsf{white}}$ by how well it punches through the listed armor values.
- $\color{#4aa3ff}{\textsf{vs Torso Armor}}$: Damage for a given ammo vs a real torso armor value. Damage colored from the MCM low end -> $\color{#3cb371}{\textsf{green}}$ for how close the value is to max damage for this weapon/ammo/bone.
- $\color{#4aa3ff}{\textsf{vs Head Armor}}$: Same as $\color{#4aa3ff}{\textsf{vs Torso Armor}}$, but vs real head armor values, and with the x3.65 headshot damage mult factored in.

$\color{#3cb371}{\textsf{Table notes}}$:
- Damage numbers are mainly a reflection of your opening salvo, as once an armor segment (head/torso+arms/legs) is broken, you will do near-full damage to that segment regardless of AP.
- Shows up to 4 ammo rows; if a weapon has more, the title shows DAMAGE (+N) for the omitted count (full list still appears in the Dmg stats).
- Column headers can show short armor-class names (Min/Lgt/...), the raw bone-armor floats used in the math, or those floats x100 floored to integers (e.g. 0.075 -> 7).
- avg_hit_fraction uses the average hit_fraction for profiles at that exact armor value only.
- Full armor-value list (including rare tiers skipped in the table) is under Useful Info section.

$\color{#3cb371}{\textsf{Math}}$ (live CQC grok_bo):
- $\color{#4aa3ff}{\textsf{range\_factor}}$ = 1 + (range / 200) x k_air x 0.5 / (1 - k_air + 0.1)
- $\color{#4aa3ff}{\textsf{bone\_mult}}$ = 0.9 (torso) / 3.65 (head)
- $\color{#4aa3ff}{\textsf{silencer}}$ = 1.07 if the section's MCM silencer toggle is on and a silencer is attached (or integrated); else 1
- $\color{#4aa3ff}{\textsf{pellets}}$ = buck_shot x (MCM Pellets assumed to hit % / 100), min 1; single-projectile ammo stays 1 (default 100%)
- $\color{#4aa3ff}{\textsf{power}}$ = (hit_power / $\color{#4aa3ff}{\textsf{range\_factor}}$) x ammo_k_hit x $\color{#4aa3ff}{\textsf{bone\_mult}}$ x tier_ap_scale x 1.1 x $\color{#4aa3ff}{\textsf{pellets}}$ x barrel_cond x difficulty x npc_ammo_mult x $\color{#4aa3ff}{\textsf{silencer}}$ x 100
- $\color{#4aa3ff}{\textsf{combat\_ap}}$ = ((k_ap + sniper_bonus) x 10) x tier_ap_scale x barrel_cond / range_factor x 0.80 x difficulty x $\color{#4aa3ff}{\textsf{silencer}}$
- $\color{#4aa3ff}{\textsf{sniper\_bonus}}$ = 0.05 for CQC sniper parent sections (L96, SV-98, Mosin, Rem 700, DVL, …); else 0. AP column still shows ammo-card k_ap x 1000.
- $\color{#4aa3ff}{\textsf{hp\_ammo\_mult}}$ = 1 except for specific ammos which can be up to 10. Higher = worse damage (see hp_ammo_mult section below).
- IF $\color{#4aa3ff}{\textsf{combat\_ap}}$ >= armor OR armor is 0 THEN  $\color{#e74c3c}{\textsf{damage}}$ = $\color{#4aa3ff}{\textsf{power}}$
- ELSE IF $\color{#4aa3ff}{\textsf{combat\_ap}}$ > armor / 1.6 THEN  $\color{#e74c3c}{\textsf{damage}}$ = $\color{#4aa3ff}{\textsf{power}}$ x avg_hit_fraction
- ELSE $\color{#e74c3c}{\textsf{damage}}$ = 0.0025 x $\color{#4aa3ff}{\textsf{power}}$ x avg_hit_fraction x 62.5 / $\color{#4aa3ff}{\textsf{hp\_ammo\_mult}}$
- AP column shows ini k_ap x 1000 (same as the ammo info card); combat uses k_ap x 10.

## Useful Info

$\color{#3cb371}{\textsf{NPC armor}}$ comes from the visual model they are wearing (the outfit you see on them), not from whatever armor item they happen to drop.
- Loot and appearance usually roughly match (i.e. exo NPCs usually drop exo armor), but many models with higher protection drop armors that are very difficult.

$\color{#3cb371}{\textsf{NPC bone\_mult}}$:
Damage is multiplied according to where the hit lands on an NPC (CQC stalker_damage).
- $\color{#4aa3ff}{\textsf{Head}}$: 365%
- $\color{#4aa3ff}{\textsf{Jaw}}$: 300%
- $\color{#4aa3ff}{\textsf{Neck}}$: 270%
- $\color{#4aa3ff}{\textsf{Torso}}$: 90%
- $\color{#4aa3ff}{\textsf{Upper arms}}$: 70%
- $\color{#4aa3ff}{\textsf{Forearms}}$: 60%
- $\color{#4aa3ff}{\textsf{Thighs}}$: 55%
- $\color{#4aa3ff}{\textsf{Calves}}$: 45%
- $\color{#4aa3ff}{\textsf{Hands}}$: 40%
- $\color{#4aa3ff}{\textsf{Feet}}$: 30%
- $\color{#4aa3ff}{\textsf{Default fallback for unmapped bones}}$: 75%

$\color{#3cb371}{\textsf{NPC bone armor}}$ is tied to their rendered model (86 unique models).
- $\color{#f39c12}{\textsf{Min damage}}$ = is just $\color{#f39c12}{\textsf{avg\_hit\_fraction}}$ as a % of the damage that still applies on a failed pen.
- $\color{#4aa3ff}{\textsf{Tier}}$ = What this tier is called in the damage table (n/a means not shown).
- Model count = number of models with that armor value (ignoring some that are unused).
- Appearances = are example loot outfits that tend to sit in that bone-armor bucket (exact GAMMA DB names; not every NPC look).

$\color{#3cb371}{\textsf{Torso armors}}$ in base GAMMA
| $\color{#4aa3ff}{\textsf{Tier}}$ | $\color{#e74c3c}{\textsf{armor}}$ x model count | $\color{#f39c12}{\textsf{Min damage}}$ | $\color{#f1c40f}{\textsf{\% of all ammos that pen}}$ | appearances (partial, to help visualize) |---------------------------------------------------------------------------------------------------------------------------------------------------
| $\color{#4aa3ff}{\textsf{Min}}$  | $\color{#e74c3c}{\textsf{0.011}}$ x9  | $\color{#f39c12}{\textsf{85}}$% | $\color{#f1c40f}{\textsf{100}}$% | Leather Jacket, Black Leather Jacket, Heavy Brown Overcoat, Overcoat |
| $\color{#4aa3ff}{\textsf{Lgt}}$  | $\color{#e74c3c}{\textsf{0.075}}$ x6  | $\color{#f39c12}{\textsf{60}}$% | $\color{#f1c40f}{\textsf{80}}$%  | Military Service Outfit, SSP-99 Bodysuit, Monolith X-18 Suit |
| $\color{#4aa3ff}{\textsf{Lgt+}}$ | $\color{#e74c3c}{\textsf{0.100}}$ x8  | $\color{#f39c12}{\textsf{50}}$% | $\color{#f1c40f}{\textsf{70}}$%  | "Sunrise" Stalker Suit, CS-1 Body Armor, Wind of Freedom |
| $\color{#9aa0a6}{\textsf{n/a}}$  | $\color{#e74c3c}{\textsf{0.125}}$ x1  | $\color{#f39c12}{\textsf{40}}$% | $\color{#f1c40f}{\textsf{65}}$%  | CS-2a Body Armor |
| $\color{#4aa3ff}{\textsf{Mid}}$  | $\color{#e74c3c}{\textsf{0.150}}$ x24 | $\color{#f39c12}{\textsf{48}}$% | $\color{#f1c40f}{\textsf{60}}$%  | SEVA Bodysuit, Tactical Stalker Suit, Mercenary LC Suit, Sentinel of Freedom |
| $\color{#4aa3ff}{\textsf{Mid+}}$ | $\color{#e74c3c}{\textsf{0.200}}$ x13 | $\color{#f39c12}{\textsf{37}}$% | $\color{#f1c40f}{\textsf{35}}$%  | Berill-5M Armored Suit, CS-3a Body Armor, Guardian of Freedom, "Sunrise" Exoskeleton |
| $\color{#4aa3ff}{\textsf{Hvy}}$  | $\color{#e74c3c}{\textsf{0.250}}$ x10 | $\color{#f39c12}{\textsf{30}}$% | $\color{#f1c40f}{\textsf{25}}$%  | Interceptor Body Armor, Skat-9 Armored Suit |
| $\color{#9aa0a6}{\textsf{n/a}}$  | $\color{#e74c3c}{\textsf{0.300}}$ x1  | $\color{#f39c12}{\textsf{30}}$% | $\color{#f1c40f}{\textsf{20}}$%  | Exosuit |
| $\color{#9aa0a6}{\textsf{n/a}}$  | $\color{#e74c3c}{\textsf{0.350}}$ x2  | $\color{#f39c12}{\textsf{22}}$% | $\color{#f1c40f}{\textsf{10}}$%  | Skat-9 Armored Suit |
| $\color{#4aa3ff}{\textsf{Hvy+}}$ | $\color{#e74c3c}{\textsf{0.400}}$ x4  | $\color{#f39c12}{\textsf{20}}$% | $\color{#f1c40f}{\textsf{10}}$%  | PSZ-7p Shell Armor, Mercenary Exosuit, Monolith Exosuit |
| $\color{#9aa0a6}{\textsf{n/a}}$  | $\color{#e74c3c}{\textsf{0.430}}$ x1  | $\color{#f39c12}{\textsf{15}}$% | $\color{#f1c40f}{\textsf{10}}$%  | Paragon of Freedom |
| $\color{#4aa3ff}{\textsf{Exo}}$  | $\color{#e74c3c}{\textsf{0.550}}$ x5  | $\color{#f39c12}{\textsf{15}}$% | $\color{#f1c40f}{\textsf{5}}$%   | Exoskeleton, Mercenary Exoskeleton, Monolith Exoskeleton |
| $\color{#4aa3ff}{\textsf{Max}}$  | $\color{#e74c3c}{\textsf{0.650}}$ x2  | $\color{#f39c12}{\textsf{15}}$% | $\color{#f1c40f}{\textsf{5}}$%   | Military Stalker Nosorog, PSZ-10d Exoskeleton |

$\color{#3cb371}{\textsf{Head armors}}$ in base GAMMA
| $\color{#4aa3ff}{\textsf{Tier}}$ | $\color{#e74c3c}{\textsf{armor}}$ x model count | $\color{#f39c12}{\textsf{Min damage}}$ | $\color{#f1c40f}{\textsf{\% of all ammos that pen}}$ | appearances (partial, to help visualize) |---------------------------------------------------------------------------------------------------------------------------------------------------
| $\color{#4aa3ff}{\textsf{Min}}$ | $\color{#e74c3c}{\textsf{0.000}}$ x39 | $\color{#9aa0a6}{\textsf{100}}$   | $\color{#f1c40f}{\textsf{100}}$% | Overcoat, Leather Jacket, Tactical Stalker Suit, "Sunrise" Exoskeleton |
| $\color{#4aa3ff}{\textsf{Lgt}}$ | $\color{#e74c3c}{\textsf{0.150}}$ x7  | $\color{#f39c12}{\textsf{41}}$% | $\color{#f1c40f}{\textsf{55}}$%  | SEVA Bodysuit, SSP-99 Bodysuit, PS5-9Md Universal Scientific Suit |
| $\color{#4aa3ff}{\textsf{Mid}}$ | $\color{#e74c3c}{\textsf{0.200}}$ x13 | $\color{#f39c12}{\textsf{44}}$% | $\color{#f1c40f}{\textsf{40}}$%  | CS-3a Body Armor, Mercenary LC Suit, Berill-5M Armored Suit, "Sunrise" Stalker Suit |
| $\color{#4aa3ff}{\textsf{Hvy}}$ | $\color{#e74c3c}{\textsf{0.300}}$ x26 | $\color{#f39c12}{\textsf{33}}$% | $\color{#f1c40f}{\textsf{20}}$%  | Exosuit, Exoskeleton, PSZ-7p Shell Armor, Skat-9 Armored Suit |
| $\color{#4aa3ff}{\textsf{Max}}$ | $\color{#e74c3c}{\textsf{0.350}}$ x1  | $\color{#f39c12}{\textsf{15}}$% | $\color{#f1c40f}{\textsf{10}}$%  | Military Stalker Nosorog |

$\color{#3cb371}{\textsf{NPC health}}$ is one shared pool (not split across limbs).
- Every stalker has the same max HP.
- There are no faction damage resists in live grok_bo.

$\color{#3cb371}{\textsf{mut\_mult}}$ Mutant multiplier (species_mult / weak_spot_mult):
- $\color{#4aa3ff}{\textsf{Fracture}}$: 0.60 / 2.2
- $\color{#4aa3ff}{\textsf{Chimera}}$: 0.75 / 2.0
- $\color{#4aa3ff}{\textsf{Zombie}}$: 0.80 / 2.0
- $\color{#4aa3ff}{\textsf{Pseudogiant}}$: 0.85 / 2.0
- $\color{#4aa3ff}{\textsf{Bloodsucker}}$: 0.85 / 1.4
- $\color{#4aa3ff}{\textsf{Psysucker}}$: 0.85 / 1.6
- $\color{#4aa3ff}{\textsf{Lurker}}$: 0.85 / 1.5
- $\color{#4aa3ff}{\textsf{Boar}}$: 0.95 / none
- $\color{#4aa3ff}{\textsf{Flesh}}$: 0.95 / 1.3
- $\color{#4aa3ff}{\textsf{Dog}}$: 1.0 / none
- $\color{#4aa3ff}{\textsf{Pseudodog}}$: 1.0 / none
- $\color{#4aa3ff}{\textsf{Cat}}$: 1.0 / none
- $\color{#4aa3ff}{\textsf{Burer}}$: 1.0 / none
- $\color{#4aa3ff}{\textsf{Controller}}$: 1.0 / none
- $\color{#4aa3ff}{\textsf{Poltergeist}}$: 1.0 / none
- $\color{#4aa3ff}{\textsf{Tushkano}}$: 1.0 / none
- $\color{#4aa3ff}{\textsf{Crow}}$: 1.0 / none
- $\color{#4aa3ff}{\textsf{Rat}}$: 1.0 / none
- $\color{#4aa3ff}{\textsf{Karlik}}$: 1.0 / none
- $\color{#4aa3ff}{\textsf{Bibliotekar}}$: 1.0 / none
- $\color{#4aa3ff}{\textsf{Rotan}}$: 1.0 / none
- $\color{#4aa3ff}{\textsf{Borya}}$: 1.0 / none
- $\color{#4aa3ff}{\textsf{Snork}}$: 1.10 / 2.0

$\color{#3cb371}{\textsf{mut\_ammo\_mult}}$ Mutant ammo damage multiplier:
- 1.75: 7.62x25 LRNPC / P
- 1.0: 23x75 shrapnel / barrikada, 12x76 zhekan, 9x19 PBP, 9x18 PMM, 5.45 EP, 5.56 SS190, 7.62x25 Pst, .357 Magnum HP, .45 Hydro / FMJ
- 0.90: Buckshot 12x70 / 20x70
- 0.85: Everything else

$\color{#3cb371}{\textsf{hp\_ammo\_mult}}$ Hard-fail residual divisor (CQC hp_rounds; stalker armor only):
Divides residual damage when combat_ap is too low to soft-pen after one armor chip. Soft pens and full pens ignore this. Higher = worse non-pen chip damage.
- 10: .338 Federal
- 3.5: 12x76 Zhekan, 23x75 Barrikada
- 3: Buckshot 12x70
- 2.7: .45 Hydro
- 2: .357 Magnum HP, 9x18 PMM, 9x19 PBP, 23x75 Shrapnel, Buckshot 20x70
- 1.75: 7.62x25 P
- 1.5: 12x76 Dart
- 1.45: 5.45 EP
- 1.33: 5.56 SS190
- 1.0: Everything else (FMJ / AP / Pst / …)

$\color{#3cb371}{\textsf{Silencers}}$:
- Live CQC multiplies stalker combat damage and AP x1.07 when a silencer is attached (or the weapon has an integrated suppressor).
- Damage Table and Stat List each have their own MCM toggle (both off by default). Table toggle affects the damage table and AP color scale; stats toggle affects Dmg vs Stalkers, Burst Size recoil _k, and Bullet Drop muzzle-speed _k.
- Dmg vs Mutants does not get the silencer boost (matches live CQC).
- Recoil: silencers usually set cam_dispersion_k / zoom_cam_dispersion_k around 0.82 (softer kick). Burst Size uses those when the stats silencer toggle is on.
- Velocity: Boomsticks silencers set bullet_speed_k (often ~0.9; some cans 0.7-0.85). Bullet Drop uses that when the stats silencer toggle is on.
- Wear: silencers do not affect barrel wear.

$\color{#3cb371}{\textsf{Overall weapon condition}}$:
- GAMMA locks overall weapon condition to about 83% so that it is always under WPO barrel-wear threshold (85%), so the per-shot barrel wear chance is always in play.
