# MCM Runtime Read Cache

Wraps `ui_mcm.get` with an in-Lua table so mods that spam option reads every
update pay one `axr_options:r_value` per key until the value changes (or the
cache is cleared on Accept / menu / key `set`).

### Worst MCM readers

| Offender | Calls in capture | Per update | ~Per sec @90 FPS | Notes |
|---|---:|---:|---:|---|
| **`ui_mcm.get`** (all callers) | **~98k** | **~25/frame** | **~2,250/s** | Shared sink; ~264ms total |
| **`stealth_mcm.get_config`** | **~31.7k** | **~8/frame** | **~720/s** | → `ui_mcm.get("stealth/…")`; ~195ms |
| **`zz_priler_nda_mcm.get_config`** | **~15.0k** | **~3.8/frame** | **~340/s** | N.D.A. |
| **`zzz_player_injuries_mcm.get_config`** | **~12.2k** | **~3.1/frame** | **~280/s** | Body Health System |
| **`tarkov_transitions_mcm.get_activation_dist`** | **~3.9k** | **1/frame** | **~90/s** | Every actor update |
| **`short_range_blood_profiler.is_enabled`** | **~3.9k** | **1/frame** | **~90/s** | Painter of the Zone OVERHAUL |

### Microbench (`scripts/bench.script`)

**10M** calls in loop per path (axr in-mem file read vs lua in-mem cache).

| Path | Total | Per call |
|---|---:|---:|
| axr | **4.896 s** | **0.490 µs** |
| lua | **0.002 s** | **~0.0002 µs** |
| **Speedup** | | **~2448×** (~100% of that get-time) |