#!/usr/bin/env python3
"""DOGMA MO2 job CLI — dependencies, disable, defaults, validate, reset, setup."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import dogma_mo2_lib as lib


def cfg_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    mo2 = lib.resolve_mo2_root(args.mo2_root)
    cfg = lib.resolve_config_dir(mo2, Path(args.config_dir) if args.config_dir else None)
    return mo2, cfg


def cmd_setup(_args: argparse.Namespace) -> int:
    req = Path(__file__).resolve().parent / "requirements-mo2.txt"
    info = lib.info
    ok = lib.ok
    warn = lib.warn
    err = lib.err

    py = sys.executable
    info(f"Python: {py} ({sys.version.split()[0]})")

    def yaml_ok_fresh() -> bool:
        probe = subprocess.run(
            [py, "-c", "import yaml; print(yaml.__version__)"],
            capture_output=True,
            text=True,
            check=False,
        )
        return probe.returncode == 0

    if not yaml_ok_fresh():
        info("Installing requirements…")
        proc = subprocess.run(
            [py, "-m", "pip", "install", "--user", "-r", str(req)],
            check=False,
        )
        if proc.returncode != 0:
            err("pip install failed")
            return proc.returncode
    else:
        ok("PyYAML already available")

    if not yaml_ok_fresh():
        err("PyYAML still not importable after install")
        return 1
    ok("PyYAML OK")

    seven = lib.find_7z()
    if seven:
        ok(f"7-Zip OK: {seven}")
    else:
        warn("7-Zip not found — .7z/.rar dependency archives will fail until installed")

    return 0


def cmd_dependencies(args: argparse.Namespace) -> int:
    mo2, cfg = cfg_paths(args)
    lib.guard_mo2_closed(force=args.force, dry_run=args.dry_run)
    data = lib.load_manifest(lib.resolve_manifest_path(cfg))
    installed = lib.resolve_installed_features(mo2, data)
    deps = lib.filter_deps(data, args.tier, installed=installed)
    modlist = lib.modlist_path(mo2, args.profile)
    tools = lib.mo2_tools_dir(mo2)
    lib.info(f"Requirements ({args.mode}, tier={args.tier}): {len(deps)} entries")
    if installed is not None:
        lib.info(
            f"Installed DOGMA features: "
            f"{len([f for f in installed if f.lower() != 'common'])}"
        )
    lib.ensure_separator(modlist, args.dry_run)
    for dep in deps:
        try:
            status = lib.process_dependency(
                mo2, modlist, dep, mode=args.mode, dry_run=args.dry_run
            )
            lib.append_action_log(tools, f"mod {dep.id}: {status}")
        except (FileNotFoundError, RuntimeError, OSError) as exc:
            lib.err(str(exc))
            lib.append_action_log(tools, f"mod {dep.id}: ERROR {exc}")
            return 1
    if not args.dry_run and not args.no_refresh:
        lib.mo2_refresh(mo2)
    return 0


def cmd_disable(args: argparse.Namespace) -> int:
    mo2, cfg = cfg_paths(args)
    lib.guard_mo2_closed(force=args.force, dry_run=args.dry_run)
    modlist = lib.modlist_path(mo2, args.profile)
    data = lib.load_manifest(lib.resolve_manifest_path(cfg))
    installed = lib.resolve_installed_features(mo2, data)
    rules, active, skipped = lib.feature_disable_rules(data, installed=installed)
    lib.info(f"Feature disables: {len(rules)} rules from {len(active)} features")
    if skipped:
        lib.warn(
            f"  Skipped features (off or not installed): {', '.join(skipped)}"
        )

    deps = lib.filter_deps(data, args.tier, installed=installed)
    dep_rules = lib.gather_dep_disable_rules(mo2, deps, modlist)
    lib.info(f"Mod disables: {len(dep_rules)} rules")
    rules = rules + dep_rules

    result = lib.update_modlist_disable(modlist, rules, args.dry_run)
    if result.disabled:
        verb = "Would disable" if args.dry_run else "Disabled"
        lib.ok(f"{verb} ({len(result.disabled)}):")
        for n in sorted(result.disabled):
            lib.ok(f"  - {n}")
    else:
        lib.info("Nothing newly disabled.")
    if result.already:
        lib.info(f"Already disabled ({len(result.already)})")
    if result.unmatched:
        lib.warn(f"Unmatched disable rules ({len(result.unmatched)}):")
        for u in result.unmatched:
            lib.warn(f"  - {u}")

    enable_rules, enable_active = lib.feature_enable_rules(data, installed=installed)
    dep_enable = lib.gather_dep_enable_rules(mo2, deps, modlist)
    if dep_enable:
        enable_rules = enable_rules + dep_enable
    if enable_rules:
        lib.info(
            f"Feature/mod enables: {len(enable_rules)} rules"
            + (f" from {len(enable_active)} features" if enable_active else "")
        )
        en = lib.update_modlist_enable_by_rules(modlist, enable_rules, args.dry_run)
        if en.enabled:
            verb = "Would enable" if args.dry_run else "Enabled"
            lib.ok(f"{verb} ({len(en.enabled)}):")
            for n in sorted(en.enabled):
                lib.ok(f"  + {n}")
        else:
            lib.info("Nothing newly enabled.")
        if en.unmatched:
            lib.warn(f"Unmatched enable rules ({len(en.unmatched)}):")
            for u in en.unmatched:
                lib.warn(f"  - {u}")
    else:
        en = lib.ModlistResult()

    lib.append_action_log(
        lib.mo2_tools_dir(mo2),
        f"disable: new={len(result.disabled)} already={len(result.already)}; "
        f"enable: new={len(en.enabled)}",
    )
    if not args.dry_run and not args.no_refresh:
        lib.mo2_refresh(mo2)
    return 0


def cmd_defaults(args: argparse.Namespace) -> int:
    mo2, cfg = cfg_paths(args)
    lib.guard_mo2_closed(force=args.force, dry_run=args.dry_run)
    modlist = lib.modlist_path(mo2, args.profile)
    init_path = lib.resolve_manifest_path(cfg)
    tools = lib.mo2_tools_dir(mo2)
    data = lib.load_manifest(init_path)
    installed = lib.resolve_installed_features(mo2, data)
    only: set[str] | None = None
    if args.fingerprint:
        current = lib.defaults_fingerprint(init_path)
        previous = lib.load_fingerprint(tools)
        only = {k for k, v in current.items() if previous.get(k) != v}
        if not only and previous:
            lib.info("Defaults fingerprint unchanged — nothing to apply.")
            return 0
        if only:
            lib.info(f"Applying defaults for {len(only)} new/changed mod section(s)")
        if not previous:
            only = None

    files, values, skipped = lib.apply_initialize(
        mo2,
        init_path,
        modlist,
        args.dry_run,
        only_patterns=only,
        installed=installed,
    )
    if skipped:
        lib.warn(f"Mods not present ({len(skipped)}): {', '.join(skipped)}")
    if values:
        verb = "Would change" if args.dry_run else "Changed"
        lib.ok(f"{verb} {values} setting(s) across {files} file(s)")
    else:
        lib.info("No defaults needed changing.")
    if not args.dry_run:
        lib.save_fingerprint(tools, lib.defaults_fingerprint(init_path))
        lib.append_action_log(tools, f"defaults: values={values} files={files}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    mo2, cfg = cfg_paths(args)
    path = lib.build_report(mo2, cfg, tier=args.tier, profile=args.profile)
    lib.ok(f"Report written: {path}")
    # Echo WARNs to console
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("WARN:"):
            lib.warn(line)
        elif line.startswith("OK:"):
            lib.ok(line)
        elif line.startswith("Summary:"):
            lib.info(line)
    return 1 if "WARN:" in text else 0


def cmd_reset_base(args: argparse.Namespace) -> int:
    mo2, _cfg = cfg_paths(args)
    lib.guard_mo2_closed(force=args.force, dry_run=args.dry_run)
    profile = lib.selected_profile(mo2, args.profile)
    modlist = mo2 / "profiles" / profile / "modlist.txt"
    fresh = mo2 / "profiles" / profile / "modlist.txt.FRESH_INSTALL.txt"
    if not fresh.is_file():
        raise FileNotFoundError(f"FRESH_INSTALL modlist not found: {fresh}")
    lib.info(f"Restoring modlist from {fresh.name}")
    if not args.dry_run:
        lib.stamp_backup(modlist)
        shutil.copy2(fresh, modlist)

    mcm_mod = None
    mods = mo2 / "mods"
    for d in mods.iterdir() if mods.is_dir() else []:
        if d.is_dir() and "mcm values" in d.name.lower():
            mcm_mod = d
            break
    if mcm_mod:
        src = mcm_mod / "gamedata" / "configs" / "axr_options.ltx"
        dest = mo2 / "overwrite" / "gamedata" / "configs" / "axr_options.ltx"
        if src.is_file():
            lib.info(f"Restoring MCM values axr_options from {mcm_mod.name}")
            if not args.dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.is_file():
                    lib.stamp_backup(dest)
                shutil.copy2(src, dest)
        else:
            lib.warn(f"No axr_options.ltx in {mcm_mod}")
    else:
        lib.warn("G.A.M.M.A. MCM values mod not found — skipped axr restore")

    lib.append_action_log(lib.mo2_tools_dir(mo2), "reset-base complete")
    return 0


def cmd_sfx(args: argparse.Namespace) -> int:
    mo2 = lib.resolve_mo2_root(args.mo2_root)
    # Prefer deployed sound_prefetch next to this folder (same mo2/)
    bat = Path(__file__).resolve().parent / "build_sound_prefetch.bat"
    py = Path(__file__).resolve().parent / "build_sound_prefetch.py"
    if not py.is_file():
        # Feature path when running from repo
        repo = Path(__file__).resolve().parents[3]
        py = repo / "src" / "misc" / "sound_prefetch" / "mo2" / "build_sound_prefetch.py"
    if not py.is_file():
        lib.err("build_sound_prefetch.py not found")
        return 1
    cmd = [sys.executable, str(py), "--mo2-root", str(mo2)]
    if args.force:
        cmd.append("--force")
    lib.info(f"SFX prefetch: {py}")
    return subprocess.run(cmd, check=False).returncode


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--mo2-root",
        default="",
        help="MO2 instance root (default: cwd / C:\\GAMMA)",
    )
    common.add_argument("--config-dir", default="", help="Override config dir")
    common.add_argument("--profile", default="", help="MO2 profile name")
    common.add_argument("--dry-run", action="store_true")
    common.add_argument("--force", action="store_true", help="Allow edits while MO2 is open")
    common.add_argument("--no-refresh", action="store_true")

    p = argparse.ArgumentParser(description="DOGMA MO2 jobs", parents=[common])
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup", help="Install Python tooling deps (PyYAML)", parents=[common])
    s.set_defaults(func=cmd_setup)

    d = sub.add_parser(
        "dependencies",
        help="Install/ensure feature requirements / suggested mods",
        parents=[common],
    )
    d.add_argument("--tier", choices=("required", "suggested", "all"), default="required")
    d.add_argument("--mode", choices=("reinstall", "ensure"), default="ensure")
    d.set_defaults(func=cmd_dependencies)

    z = sub.add_parser(
        "disable", help="Apply feature/mod disable lists from manifest.yml", parents=[common]
    )
    z.add_argument("--tier", choices=("required", "suggested", "all"), default="required")
    z.set_defaults(func=cmd_disable)

    a = sub.add_parser("defaults", help="Apply MCM defaults from manifest.yml", parents=[common])
    a.add_argument(
        "--fingerprint",
        action="store_true",
        help="Only apply new/changed defaults sections (Update)",
    )
    a.set_defaults(func=cmd_defaults)

    v = sub.add_parser(
        "validate", help="Write fresh dogma_mo2_report.log", parents=[common]
    )
    v.add_argument("--tier", choices=("required", "suggested", "all"), default="required")
    v.set_defaults(func=cmd_validate)

    r = sub.add_parser(
        "reset-base", help="Restore FRESH_INSTALL modlist + MCM values", parents=[common]
    )
    r.set_defaults(func=cmd_reset_base)

    x = sub.add_parser("sfx", help="Run sound prefetch builder", parents=[common])
    x.set_defaults(func=cmd_sfx)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (FileNotFoundError, ValueError, RuntimeError, OSError) as exc:
        lib.err(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
