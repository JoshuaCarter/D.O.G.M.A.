# D.O.G.M.A. v0.1.0

*Dorn's Own G.A.M.M.A. Modification Anthology*

D.O.G.M.A. is two things:

1. A `fomod` pick-and-choose collection of my mods, fixes, and tweaks.
2. An opt-in, highly opinionated setup tool to configure G.A.M.M.A according to my preferences.

If you just want to install my 'True Prone' or 'True Fast Travel' mods, you can. But what D.O.G.M.A. **really** exists to do is take a vanilla G.A.M.M.A. install and automatically set it up to be **better** (according to **me**) - it will add mods, disable mods, change default settings, add/remove config options, move files, delete files/dirs, etc. This *massively* simplifies the setup process for getting **my** kind of G.A.M.M.A. up and running.

For example, the opt-in D.O.G.M.A. Setup tool has the option to install *Melancholy Weathers* which will do the following (not an exaustive list):

Melancholy Weathers:

*Due to being a paid/not-public mod, you will be instructed to supply the mod archive file.*

- Download/install Enhanced Shader Color Grading, Screen Space Shaders, Dark Signal Soundscape Overhaul Melancholy Edition, Melancholy Weathers.
- Disable the relevant preexisting SSS, DSSO, Atmospherics mods.
- Disable "persistent weather" in settings
- Delete shaders_cache
- Run `cfg_load melancholy` on first launch


## Which install?

1. **FOMOD** — pick individual D.O.G.M.A. path mods (True Prone, Fast Travel, tooltips, …) like a normal mod. Features that need a third-party pack note that in their description.
2. **DOGMA Setup** in MO2 — wizard pages for third-party packs (`config/manifest-third-party.yml`), D.O.G.M.A. mods (`config/manifest-dogma-mods.yml`), and tweaks (`config/manifest-dogma-tweaks.yml`; `stage: release` or `dev`). Path mods unpack from `mo2/packages/`; url mods download as before. `stage: omit` is hidden from both FOMOD and Setup; `dev` is local-only.

You can use both: install path mods via FOMOD, then run Setup for packs/disables/defaults (already-present path mods default off but still pull their `depends:`).

## Why make an "anthology"?

I got sick of managing a dozen mods, and I knew it would be 2 dozen in no time. I also felt like I was drifting too far from my real goal - making G.A.M.M.A. a better experience for **me**, because the target audience for my mods is **me**. It's so much easier to shove every little change I want to make into this mod because I don't have to scaffold a new repo, maintain another discord thread, etc. If I couldn't share any of this, then nothing would change for me really, but my infinite compassion for others compells me to share my work with whoever may share my tastes.

## Vision / Philosophy

I'm not trying to overhaul G.A.M.M.A., and I'm not trying to make it easier or harder. I'm trying to stay *true* to the vision of G.A.M.M.A. (as I perceive it), while also improving things I think could be better, fixing things that annoy me, reducing tedium, etc.

For example:

- I think it's stupid that crafting x15 AP ammo takes x30 casings, so I changed it to x15, but I also doubled the powder requirement so that it's not strictly easier to craft.
- I added true prone, but I made it so you take extra damage when prone.
- I don't like instant fast travel because it trivialises the world. So I made instant travel crazy expensive and added a facy fast travel mod (WIP) that lets you travel fast, but makes it still take time and doesn't remove the danger.

My ultimate desire, which I may never really achieve, is a single mod who's installation process sets you (**me**) up almost completely for a better experience out of the box. This would includes changing/hiding many mods/config.

## Suggestions

Are welcome, but if it doesn't appeal to **me**, it's probably not happening. In particular, I intentionally don't expose many options that I could, because they would make things easier in a way I don't like - or tempt me to make things easier for myself, and removing that temptation for **me** is something I value. So be ready to hear "no".

## Support

My time is very limited (job/wife/kids/etc). Include your xray log unless confident it's not relevant. And sorry but I probably wont care about conflicts/issues with other mods unless I use them myself.

## Contributions

Are welcome, but check with me first before you do any work. Also you should *know what you're doing* - I don't have time to mentor first-timers on how to use git, nor will I accept PRs that shit the bed and require me to rewrite your code so you don't break everything. Keeping the surface area of your diff small is a good idea.

## Can I copy your homework?

I steal. You steal. We all steal. Go for it.