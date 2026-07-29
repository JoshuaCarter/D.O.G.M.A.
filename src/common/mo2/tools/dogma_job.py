#!/usr/bin/env python3
"""DOGMA MO2 job CLI — dependencies, disable, defaults, validate, reset, setup."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import dogma_mo2_lib as lib


def cfg_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    mo2 = lib.resolve_mo2_root(args.mo2_root)
    cfg = lib.resolve_config_dir(mo2, Path(args.config_dir) if args.config_dir else None)
    return mo2, cfg


def resolve_selection(
    args: argparse.Namespace, mo2: Path, data: lib.ManifestData
) -> lib.InstallerSelection | None:
    """Return wizard selection, or None to fall back to defaults.

    --options OptA,OptB wins (exclusive picks = defaults required by those opts).
    --use-selection loads selection.json (missing → None; empty options → error).
    """
    raw = getattr(args, "options", "") or ""
    if raw.strip():
        option_ids = [p.strip() for p in raw.split(",") if p.strip()]
        return lib.InstallerSelection(
            option_ids=option_ids,
            exclusive_picks=lib.default_exclusive_picks(data, option_ids),
        )
    if getattr(args, "use_selection", False):
        sel = lib.load_installer_selection(mo2)
        if sel is None:
            return None
        if not sel.option_ids and not any(sel.exclusive_picks.values()):
            raise ValueError(
                "selection.json is empty — re-run DOGMA Setup or pass "
                "--options OptA,OptB"
            )
        return sel
    return None


def deps_for_args(
    data: lib.ManifestData,
    args: argparse.Namespace,
    mo2: Path,
    *,
    installed: set[str] | None,
) -> list[lib.Dependency]:
    sel = resolve_selection(args, mo2, data)
    tier = getattr(args, "tier", "downloads")
    # Never install the raw full suggested_mods catalog — expand via options.
    if sel is None and data.installer_options and tier in ("suggested", "all"):
        option_ids = lib.default_installer_option_ids(data, installed=installed)
        sel = lib.InstallerSelection(
            option_ids=option_ids,
            exclusive_picks=lib.default_exclusive_picks(data, option_ids),
        )
    if sel is not None:
        sel = lib.sanitize_installer_selection(data, sel)
    if sel is not None and tier in ("suggested", "all"):
        # Selection is authoritative for packs + path mods.
        return lib.resolve_install_order(
            data,
            sel.option_ids,
            sel.exclusive_picks,
            installed=installed,
        )
    return lib.filter_deps(data, tier, installed=installed)


def cmd_wizard(args: argparse.Namespace) -> int:
    import dogma_wizard

    mo2, cfg = cfg_paths(args)
    lib.info(f"Opening Setup wizard (MO2={mo2})")
    data = lib.load_manifest(lib.resolve_manifest_path(cfg))
    prev = lib.load_installer_selection(mo2)
    selected = dogma_wizard.run_wizard(data, mo2_root=mo2, initial=prev)
    if selected is None:
        lib.warn("Wizard cancelled")
        return 2
    lib.save_installer_selection(mo2, selected)
    lib.info(
        f"Wizard saved selection: {len(selected.option_ids)} options, "
        f"{sum(1 for v in selected.exclusive_picks.values() if v)} radio picks"
    )
    installed = lib.resolve_installed_features(mo2, data)
    ordered = lib.resolve_install_order(
        data,
        selected.option_ids,
        selected.exclusive_picks,
        installed=installed,
    )
    lib.ok(f"Selected: {', '.join(selected.option_ids)}")
    if selected.exclusive_picks:
        lib.info(
            "Exclusive: "
            + ", ".join(
                f"{g}={p or 'None'}" for g, p in selected.exclusive_picks.items()
            )
        )
    lib.info("Install order: " + ", ".join(d.id for d in ordered))
    return 0


def cmd_setup(_args: argparse.Namespace) -> int:
    info = lib.info
    ok = lib.ok
    warn = lib.warn
    err = lib.err

    py = sys.executable
    info(f"Python: {py} ({sys.version.split()[0]})")

    # Inline deps (no requirements-mo2.txt) — keep this list tiny.
    required = ("PyYAML>=6.0", "windnd")

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
            [py, "-m", "pip", "install", "--user", *required],
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

    try:
        import windnd  # noqa: F401

        ok("windnd OK (drag-drop)")
    except ImportError:
        warn("windnd not importable — archive drag-drop disabled until Setup Tools succeeds")

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
    # Path mods + url packs share one install list (process_dependency handles both).
    deps = deps_for_args(data, args, mo2, installed=installed)
    modlist = lib.modlist_path(mo2, args.profile)
    lib.info(f"MO2 root: {mo2}")
    lib.info(f"Config: {cfg}")
    lib.info(f"Modlist: {modlist}")
    lib.info(f"Downloads ({args.mode}, tier={args.tier}): {len(deps)} entries")
    if deps:
        lib.info("Order: " + ", ".join(d.id for d in deps))
    if installed is not None:
        lib.info(
            f"Installed DOGMA features: "
            f"{len([f for f in installed if f.lower() != 'common'])}"
        )
    lib.ensure_separator(modlist, args.dry_run)
    for i, dep in enumerate(deps, 1):
        lib.info(f"=== [{i}/{len(deps)}] {dep.id} ===")
        try:
            status = lib.process_dependency(
                mo2, modlist, dep, mode=args.mode, dry_run=args.dry_run
            )
            lib.ok(f"[{dep.id}] done: {status}")
        except Exception as exc:
            lib.log_exception(exc, where=f"dependencies[{dep.id}]")
            return 1
    if not args.dry_run and not args.no_refresh:
        lib.mo2_refresh(mo2)
    lib.ok(f"dependencies finished ({len(deps)} packs)")
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
            f"  Skipped features (omit or not installed): {', '.join(skipped)}"
        )

    deps = deps_for_args(data, args, mo2, installed=installed)
    dep_rules = lib.gather_dep_disable_rules(mo2, deps, modlist)
    lib.info(f"Mod disables: {len(dep_rules)} rules")
    rules = rules + dep_rules

    result = lib.update_modlist_disable(modlist, rules, args.dry_run)
    if result.disabled:
        verb = "Would disable" if args.dry_run else "Disabled"
        lib.ok(f"{verb} ({len(result.disabled)}):")
        for n in sorted(result.disabled):
            lib.ok(f"  disable: {n}")
    else:
        lib.info("Nothing newly disabled.")
    if result.already:
        lib.info(f"Already disabled ({len(result.already)})")
        for n in sorted(result.already):
            lib.info(f"  already off: {n}")
    if result.unmatched:
        lib.warn(f"Unmatched disable rules ({len(result.unmatched)}):")
        for u in result.unmatched:
            lib.warn(f"  unmatched: {u}")

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
                lib.ok(f"  enable: {n}")
        else:
            lib.info("Nothing newly enabled.")
        if en.unmatched:
            lib.warn(f"Unmatched enable rules ({len(en.unmatched)}):")
            for u in en.unmatched:
                lib.warn(f"  unmatched: {u}")
    else:
        en = lib.ModlistResult()

    lib.ok(
        f"disable job done: new_off={len(result.disabled)} "
        f"already_off={len(result.already)} new_on={len(en.enabled)}"
    )
    if not args.dry_run and not args.no_refresh:
        lib.mo2_refresh(mo2)
    return 0


def cmd_defaults(args: argparse.Namespace) -> int:
    mo2, cfg = cfg_paths(args)
    lib.guard_mo2_closed(force=args.force, dry_run=args.dry_run)
    modlist = lib.modlist_path(mo2, args.profile)
    init_path = lib.resolve_manifest_path(cfg)
    data = lib.load_manifest(init_path)
    installed = lib.resolve_installed_features(mo2, data)

    suggested_ids: set[str] | None = None
    sel = resolve_selection(args, mo2, data)
    if sel is None and data.installer_options:
        option_ids = lib.default_installer_option_ids(data, installed=installed)
        sel = lib.InstallerSelection(
            option_ids=option_ids,
            exclusive_picks=lib.default_exclusive_picks(data, option_ids),
        )
    if sel is not None:
        suggested_ids = {
            d.id
            for d in lib.resolve_install_order(
                data,
                sel.option_ids,
                sel.exclusive_picks,
                installed=installed,
            )
        }

    files, values = lib.apply_initialize(
        mo2,
        init_path,
        modlist,
        args.dry_run,
        installed=installed,
        suggested_ids=suggested_ids,
    )
    if values:
        verb = "Would change" if args.dry_run else "Changed"
        lib.ok(f"{verb} {values} setting(s) across {files} file(s)")
    else:
        lib.info("No defaults needed changing.")
    lib.ok(f"defaults job done: values={values} files={files}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    mo2, cfg = cfg_paths(args)
    data = lib.load_manifest(lib.resolve_manifest_path(cfg))
    installed = lib.resolve_installed_features(mo2, data)
    check_deps = deps_for_args(data, args, mo2, installed=installed)
    path = lib.build_report(
        mo2,
        cfg,
        tier=args.tier,
        profile=args.profile,
        deps=check_deps,
    )
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


def cmd_preinstall_backup(args: argparse.Namespace) -> int:
    """MO2-style modlist Create Backup before Setup/Reset changes the list."""
    mo2, _cfg = cfg_paths(args)
    lib.create_preinstall_modlist_backup(
        mo2, args.profile, dry_run=args.dry_run
    )
    return 0


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
    import dogma_optimize as optimize

    mo2 = lib.resolve_mo2_root(args.mo2_root)
    return optimize.run_sfx_prefetch(mo2, force=bool(args.force))


def cmd_optimize(args: argparse.Namespace) -> int:
    import dogma_optimize as optimize

    return optimize.run(args)


def _with_selection(args: argparse.Namespace, **overrides: object) -> argparse.Namespace:
    ns = argparse.Namespace(**vars(args))
    ns.use_selection = True
    for key, value in overrides.items():
        setattr(ns, key, value)
    return ns


def cmd_update(args: argparse.Namespace) -> int:
    """Ensure deps + disable + defaults + validate (uses saved wizard selection)."""
    code = cmd_setup(args)
    if code:
        return code
    sel = _with_selection(args, tier="all", mode="ensure")
    for step in (
        lambda: cmd_dependencies(sel),
        lambda: cmd_disable(sel),
        lambda: cmd_defaults(sel),
        lambda: cmd_validate(sel),
    ):
        code = step()
        if code:
            return code
    lib.ok("update pipeline done")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    """FRESH_INSTALL modlist + MCM, then reinstall pipeline (no wizard)."""
    code = cmd_setup(args)
    if code:
        return code
    sel = _with_selection(args, tier="all", mode="reinstall")
    for step in (
        lambda: cmd_preinstall_backup(args),
        lambda: cmd_reset_base(args),
        lambda: cmd_dependencies(sel),
        lambda: cmd_disable(sel),
        lambda: cmd_defaults(sel),
        lambda: cmd_validate(sel),
    ):
        code = step()
        if code:
            return code
    lib.ok("reset pipeline done")
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    """Full Setup pipeline: tools → wizard → backup → deps → disable → defaults → validate."""
    code = cmd_setup(args)
    if code:
        return code

    no_wizard = bool(getattr(args, "no_wizard", False)) or (
        os.environ.get("DOGMA_NO_WIZARD", "").strip() == "1"
    )
    if no_wizard:
        lib.info("Skipping wizard (DOGMA_NO_WIZARD / --no-wizard)")
        mid = argparse.Namespace(**vars(args))
        mid.tier = "all"
        mid.mode = "reinstall"
        mid.use_selection = False
    else:
        code = cmd_wizard(args)
        if code:
            return code
        mid = _with_selection(args, tier="all", mode="reinstall")

    val = _with_selection(args, tier="all")
    for step in (
        lambda: cmd_preinstall_backup(args),
        lambda: cmd_dependencies(mid),
        lambda: cmd_disable(mid),
        lambda: cmd_defaults(mid),
        lambda: cmd_validate(val),
    ):
        code = step()
        if code:
            return code
    lib.ok("install pipeline done")
    return 0


def cmd_executables(args: argparse.Namespace) -> int:
    import dogma_executables as exes

    mo2 = lib.resolve_mo2_root(args.mo2_root)
    lib.guard_mo2_closed(force=args.force, dry_run=args.dry_run)
    result = exes.register_dogma_executables(mo2, dry_run=args.dry_run)
    if result.missing_bats:
        lib.warn(
            f"{len(result.missing_bats)} bat(s) missing under mods/DOGMA/mo2/tools/ "
            "(deploy DOGMA first)"
        )
    lib.ok(
        f"executables job done: removed={len(result.removed)} "
        f"added={len(result.added)} missing={len(result.missing_bats)}"
    )
    return 0


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
    common.add_argument(
        "--log-reset",
        action="store_true",
        help="Truncate dogma_install.log (parents pass on first step)",
    )

    p = argparse.ArgumentParser(description="DOGMA MO2 jobs", parents=[common])
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup", help="Install Python tooling deps (PyYAML)", parents=[common])
    s.set_defaults(func=cmd_setup)

    inst = sub.add_parser(
        "install",
        help="Full Setup: tools, wizard, deps, disable, defaults, validate",
        parents=[common],
    )
    inst.add_argument(
        "--no-wizard",
        action="store_true",
        help="Skip GUI wizard (same as DOGMA_NO_WIZARD=1)",
    )
    inst.set_defaults(func=cmd_install)

    w = sub.add_parser(
        "wizard",
        help="Pick suggested installer_options (GUI); saves selection.json",
        parents=[common],
    )
    w.set_defaults(func=cmd_wizard)

    sel = argparse.ArgumentParser(add_help=False)
    sel.add_argument(
        "--options",
        default="",
        help="Comma-separated installer_options ids (skips saved selection)",
    )
    sel.add_argument(
        "--use-selection",
        action="store_true",
        help="Only install packs from mo2/config/selection.json (wizard)",
    )

    d = sub.add_parser(
        "dependencies",
        help="Install/ensure feature downloads / suggested mods",
        parents=[common, sel],
    )
    d.add_argument(
        "--tier",
        choices=("downloads", "required", "suggested", "all"),
        default="downloads",
    )
    d.add_argument("--mode", choices=("reinstall", "ensure"), default="ensure")
    d.set_defaults(func=cmd_dependencies)

    z = sub.add_parser(
        "disable",
        help="Apply feature/mod disables from manifest.yml",
        parents=[common, sel],
    )
    z.add_argument(
        "--tier",
        choices=("downloads", "required", "suggested", "all"),
        default="downloads",
    )
    z.set_defaults(func=cmd_disable)

    a = sub.add_parser(
        "defaults",
        help="Apply MCM defaults from manifest.yml",
        parents=[common, sel],
    )
    a.set_defaults(func=cmd_defaults)

    v = sub.add_parser(
        "validate", help="Write fresh dogma_report.log", parents=[common, sel]
    )
    v.add_argument(
        "--tier",
        choices=("downloads", "required", "suggested", "all"),
        default="downloads",
    )
    v.set_defaults(func=cmd_validate)

    r = sub.add_parser(
        "reset-base", help="Restore FRESH_INSTALL modlist + MCM values", parents=[common]
    )
    r.set_defaults(func=cmd_reset_base)

    rst = sub.add_parser(
        "reset",
        help="FRESH_INSTALL + MCM, then deps/disable/defaults/validate",
        parents=[common],
    )
    rst.set_defaults(func=cmd_reset)

    upd = sub.add_parser(
        "update",
        help="Ensure deps + disable + defaults + validate (saved selection)",
        parents=[common],
    )
    upd.set_defaults(func=cmd_update)

    b = sub.add_parser(
        "preinstall-backup",
        help="MO2 Create Backup of modlist (DOGMA Pre Install Backup N)",
        parents=[common],
    )
    b.set_defaults(func=cmd_preinstall_backup)

    x = sub.add_parser("sfx", help="Run sound prefetch builder", parents=[common])
    x.set_defaults(func=cmd_sfx)

    o = sub.add_parser(
        "optimize",
        help="GC settings, SFX prefetch, mods backup, ALAO",
        parents=[common],
    )
    o.add_argument(
        "backup_dir",
        nargs="?",
        default="",
        help="Backup dir for mods.backup.7z (MO2 Arguments; default MO2 root)",
    )
    o.add_argument(
        "--backup-dir",
        dest="backup_dir_flag",
        default="",
        help="Override backup directory (scripting)",
    )
    o.add_argument("--gc", dest="do_gc", action="store_true", help="Ensure Lua GC settings")
    o.add_argument("--no-gc", action="store_true", help="Skip GC step")
    o.add_argument("--sfx", dest="do_sfx", action="store_true", help="Run SFX prefetch")
    o.add_argument("--no-sfx", action="store_true", help="Skip SFX prefetch")
    o.add_argument("--backup", dest="do_backup", action="store_true", help="Create mods.backup.7z")
    o.add_argument("--no-backup", action="store_true", help="Skip mods backup")
    o.add_argument("--alao", dest="do_alao", action="store_true", help="Run ALAO")
    o.add_argument("--no-alao", action="store_true", help="Skip ALAO")
    o.set_defaults(func=cmd_optimize)

    e = sub.add_parser(
        "executables",
        help="Replace DOGMA bats in MO2 Executables (ModOrganizer.ini)",
        parents=[common],
    )
    e.set_defaults(func=cmd_executables)

    return p


def _console_hwnd() -> int:
    if sys.platform != "win32":
        return 0
    try:
        import ctypes

        return int(ctypes.windll.kernel32.GetConsoleWindow() or 0)
    except (AttributeError, OSError, ValueError):
        return 0


def show_console() -> None:
    """Un-hide the process console (e.g. before pause on failure)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = int(ctypes.windll.kernel32.GetConsoleWindow() or 0)
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 5)  # SW_SHOW
    except (AttributeError, OSError, ValueError):
        pass


def main(argv: list[str] | None = None) -> int:
    # Wizard / install may forward extra MO2 args; ignore unknowns on those cmds.
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("wizard", "install"):
        args, _unknown = build_parser().parse_known_args(argv)
    else:
        args = build_parser().parse_args(argv)

    mo2 = lib.resolve_mo2_root(getattr(args, "mo2_root", None) or None)
    tools = lib.mo2_tools_dir(mo2)
    reset = bool(getattr(args, "log_reset", False))
    job = str(getattr(args, "cmd", "") or "")
    log_path = lib.configure_logging(tools, reset=reset, job=job)
    if job == "optimize":
        # Quiet start — Optimizer prints its own prompts; keep detail in the log file only.
        lib.append_action_log(
            tools, f"optimize start mo2={mo2} log={log_path} argv={' '.join(argv)}"
        )
    else:
        lib.info("Installer launched")
        lib.info(f"argv: {' '.join(argv)}")
        lib.info(f"MO2: {mo2}")
        lib.info(f"Log file: {log_path}")
        if reset:
            lib.info("Log reset for this parent run")

    code = 1
    try:
        code = int(args.func(args))
    except Exception as exc:
        lib.log_exception(exc, where=job or "job")
        code = 1
    if job == "optimize":
        if code != 0:
            lib.info(f"Details saved to:\n  {lib.action_log_path(tools)}")
    else:
        lib.info(f"exit={code} job={job}")
        lib.info(f"Log -> {lib.action_log_path(tools)}")
        if lib.report_log_path(tools).is_file():
            lib.info(f"Report -> {lib.report_log_path(tools)}")
    # Cancel (2): keep console hidden so the window goes away with the process.
    # Other errors: show console so :fail pause / messages are readable.
    if code not in (0, 2):
        show_console()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
