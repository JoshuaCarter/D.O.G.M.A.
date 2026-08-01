#!/usr/bin/env python3
"""DOGMA Dump - collect debug logs/config/hardware into DOGMA/dumps/*.zip."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import dogma_backup as bak
import dogma_mo2_lib as lib

DUMP_DIR_NAME = "dumps"
META_NAME = "dump.yml"
HARDWARE_NAME = "hardware.txt"

# Anomaly appdata/logs - text logs only (skip .mdmp crash dumps; huge/binary).
GAME_LOG_NAMES = (
    "alifeplus.log",
    "alifebalance.log",
    "alifeguard.log",
    "alifetactics.log",
)
DOGMA_LOG_GLOBS = (
    "dogma_*.log",
    "alao_report.html",
)


def dump_root(mo2_root: Path) -> Path:
    """``<MO2>\\DOGMA\\dumps``."""
    dest = lib.dogma_data_dir(mo2_root) / DUMP_DIR_NAME
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def stamp_now() -> str:
    """Same stamp as DOGMA backups / dated artifacts: ``YYYY-MM-DD_HH-MM-SS``."""
    return bak.stamp_now()


def game_logs_dir(mo2_root: Path) -> Path:
    return lib.game_dir(mo2_root) / "appdata" / "logs"


def _copy_if_file(src: Path, dest: Path, *, dry_run: bool) -> bool:
    if not src.is_file():
        return False
    if dry_run:
        lib.info(f"Would copy:\n  {src}\n  -> {dest}")
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return True


def _fmt_bytes(n: int) -> str:
    x = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if x < 1024.0 or unit == "TB":
            return f"{x:.1f} {unit}" if unit != "B" else f"{int(x)} B"
        x /= 1024.0
    return f"{n} B"


def collect_xray_logs(logs_dir: Path) -> list[Path]:
    """Newest-first ``xray_*.log`` (exclude crash .mdmp)."""
    if not logs_dir.is_dir():
        return []
    files = [
        p
        for p in logs_dir.iterdir()
        if p.is_file()
        and p.suffix.lower() == ".log"
        and p.name.lower().startswith("xray_")
    ]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def collect_alife_logs(logs_dir: Path) -> list[Path]:
    if not logs_dir.is_dir():
        return []
    out: list[Path] = []
    for name in GAME_LOG_NAMES:
        p = logs_dir / name
        if p.is_file():
            out.append(p)
    # Any other alife*.log the pack may add later.
    for p in sorted(logs_dir.glob("alife*.log")):
        if p not in out and p.is_file():
            out.append(p)
    return out


def collect_dogma_tool_logs(mo2_root: Path) -> list[Path]:
    logs = lib.logs_dir(mo2_root)
    found: list[Path] = []
    seen: set[str] = set()
    for pattern in DOGMA_LOG_GLOBS:
        for p in sorted(logs.glob(pattern)):
            if not p.is_file():
                continue
            key = p.name.lower()
            if key in seen:
                continue
            seen.add(key)
            found.append(p)
    return found


def _nvidia_smi_gpus() -> list[tuple[str, str, str]]:
    """Return (name, vram, driver) rows from nvidia-smi when available."""
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return []
    rows: list[tuple[str, str, str]] = []
    for line in proc.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        name, mem_mib, driver = parts[0], parts[1], parts[2]
        try:
            vram = f"{float(mem_mib) / 1024.0:.1f} GB"
        except ValueError:
            vram = f"{mem_mib} MiB"
        rows.append((name, vram, driver))
    return rows


def write_hardware_report(dest: Path, *, dry_run: bool) -> bool:
    """Write a short CPU/GPU/RAM/disk summary (no full dxdiag dump)."""
    if dry_run:
        lib.info(f"Would write hardware report:\n  {dest}")
        return True

    ps = r"""
$ErrorActionPreference = 'SilentlyContinue'
function GB([long]$b) {
  if ($null -eq $b -or $b -le 0) { return 'n/a' }
  '{0:N1} GB' -f ($b / 1GB)
}
$os = Get-CimInstance Win32_OperatingSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$sticks = @(Get-CimInstance Win32_PhysicalMemory)
$gpus = @(Get-CimInstance Win32_VideoController)
$disks = @(Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3")
$totalRam = ($sticks | Measure-Object -Property Capacity -Sum).Sum
$speeds = @($sticks | ForEach-Object { $_.Speed } | Where-Object { $_ } | Sort-Object -Unique)

Write-Output '=== DOGMA hardware summary ==='
Write-Output ("Generated: {0}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
Write-Output ''
Write-Output '-- OS --'
Write-Output ("Name:    {0}" -f $os.Caption)
Write-Output ("Version: {0} (build {1})" -f $os.Version, $os.BuildNumber)
Write-Output ("Arch:    {0}" -f $os.OSArchitecture)
Write-Output ''
Write-Output '-- CPU --'
Write-Output ("Name:        {0}" -f $cpu.Name.Trim())
Write-Output ("Cores/Threads: {0} / {1}" -f $cpu.NumberOfCores, $cpu.NumberOfLogicalProcessors)
if ($cpu.MaxClockSpeed) {
  Write-Output ("Max clock:   {0} MHz" -f $cpu.MaxClockSpeed)
}
if ($cpu.CurrentClockSpeed) {
  Write-Output ("Cur clock:   {0} MHz" -f $cpu.CurrentClockSpeed)
}
Write-Output ''
Write-Output '-- RAM --'
Write-Output ("Total:  {0}" -f (GB $totalRam))
if ($speeds.Count -gt 0) {
  Write-Output ("Speed:  {0} MHz" -f ($speeds -join ', '))
}
$i = 1
foreach ($s in $sticks) {
  Write-Output ("  Stick {0}: {1} @ {2} MHz ({3})" -f $i, (GB $s.Capacity), $s.Speed, $s.Manufacturer)
  $i++
}
Write-Output ''
Write-Output '-- GPU --'
foreach ($g in $gpus) {
  if (-not $g.Name) { continue }
  # Skip Microsoft Basic / mirror adapters.
  if ($g.Name -match 'Microsoft Basic|Remote Desktop|Virtual') { continue }
  Write-Output ("Name:  {0}" -f $g.Name)
  if ($g.DriverVersion) {
    Write-Output ("Driver: {0}" -f $g.DriverVersion)
  }
  Write-Output ''
}
Write-Output '-- Disks (fixed) --'
foreach ($d in $disks) {
  Write-Output ("{0}  free {1} / {2}  ({3})" -f $d.DeviceID, (GB $d.FreeSpace), (GB $d.Size), $d.FileSystem)
}
"""
    try:
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                ps,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        dest.write_text(
            f"=== DOGMA hardware summary ===\nUnavailable ({exc})\n",
            encoding="utf-8",
            newline="\n",
        )
        lib.warn(f"Hardware report failed: {exc}")
        return False

    text = (proc.stdout or "").strip()
    if not text:
        err = (proc.stderr or "").strip() or f"exit {proc.returncode}"
        text = f"=== DOGMA hardware summary ===\nUnavailable ({err})\n"
        lib.warn(f"Hardware report empty: {err}")

    # Prefer nvidia-smi for accurate VRAM (WMI AdapterRAM saturates at 4 GB).
    nv = _nvidia_smi_gpus()
    if nv:
        lines = text.splitlines()
        out: list[str] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            out.append(line)
            if line.strip() == "-- GPU --":
                out.pop()  # replace whole GPU section
                out.append("-- GPU --")
                for name, vram, driver in nv:
                    out.append(f"Name:  {name}")
                    out.append(f"VRAM:  {vram}")
                    out.append(f"Driver: {driver}")
                    out.append("")
                # Skip original GPU section until next -- header or EOF.
                i += 1
                while i < len(lines) and not lines[i].startswith("-- "):
                    i += 1
                continue
            i += 1
        text = "\n".join(out).rstrip()

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text + "\n", encoding="utf-8", newline="\n")
    return True


def write_meta(
    dest: Path,
    *,
    mo2_root: Path,
    profile: str,
    collected: dict[str, list[str]],
    dry_run: bool,
) -> None:
    if dry_run:
        lib.info(f"Would write {META_NAME}")
        return
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML required - run DOGMA Setup once") from exc

    game = ""
    try:
        game = str(lib.game_dir(mo2_root))
    except (OSError, FileNotFoundError, ValueError):
        pass

    data = {
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mo2_root": str(mo2_root.resolve()),
        "game_dir": game,
        "profile": profile,
        "python": sys.version.split()[0],
        "collected": collected,
    }
    dest.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
        newline="\n",
    )


def _zip_dir(src_dir: Path, zip_path: Path) -> None:
    if zip_path.is_file():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(src_dir):
            for name in files:
                full = Path(root) / name
                arc = full.relative_to(src_dir).as_posix()
                zf.write(full, arcname=arc)


def run_dump(
    mo2_root: Path,
    *,
    profile: str = "",
    dry_run: bool = False,
    stamp: str | None = None,
) -> tuple[int, Path | None]:
    """Collect debug bundle → ``DOGMA/dumps/dogma_dump_<YYYY-MM-DD_HH-MM-SS>.zip``."""
    mo2_root = Path(mo2_root).resolve()
    profile_name = lib.selected_profile(mo2_root, profile)
    stamp_s = stamp or stamp_now()
    out_root = dump_root(mo2_root)
    zip_path = out_root / f"dogma_dump_{stamp_s}.zip"

    print()
    lib.info(f"Dump archive:\n  {zip_path}")

    collected: dict[str, list[str]] = {
        "logs": [],
        "config": [],
        "other": [],
    }

    with tempfile.TemporaryDirectory(prefix="dogma_dump_") as tmp:
        stage = Path(tmp)
        logs_dest = stage / "logs"
        cfg_dest = stage / "config"
        logs_dest.mkdir(parents=True, exist_ok=True)
        cfg_dest.mkdir(parents=True, exist_ok=True)

        # --- game / AlifePlus / xray logs ---
        g_logs = game_logs_dir(mo2_root)
        xray = collect_xray_logs(g_logs)
        # Keep the newest xray log (current session) + previous if present.
        for src in xray[:2]:
            if _copy_if_file(src, logs_dest / src.name, dry_run=dry_run):
                collected["logs"].append(f"logs/{src.name}")
        for src in collect_alife_logs(g_logs):
            if _copy_if_file(src, logs_dest / src.name, dry_run=dry_run):
                collected["logs"].append(f"logs/{src.name}")
        if not g_logs.is_dir():
            lib.warn(f"Game logs folder missing:\n  {g_logs}")

        # --- DOGMA tool logs ---
        for src in collect_dogma_tool_logs(mo2_root):
            if _copy_if_file(src, logs_dest / src.name, dry_run=dry_run):
                collected["logs"].append(f"logs/{src.name}")

        # --- config: MCM diff, user.ltx, modlist (reuse backup helpers) ---
        if not dry_run:
            code = bak.step_backup_mcm(mo2_root, cfg_dest, dry_run=False)
            if code:
                return code, None
            if (cfg_dest / bak.MCM_DIFF_NAME).is_file():
                collected["config"].append(f"config/{bak.MCM_DIFF_NAME}")

            code = bak.step_backup_user_ltx(mo2_root, cfg_dest, dry_run=False)
            if code:
                return code, None
            if (cfg_dest / bak.USER_LTX_NAME).is_file():
                collected["config"].append(f"config/{bak.USER_LTX_NAME}")

            code = bak.step_backup_modlist(
                mo2_root, cfg_dest, profile=profile_name, dry_run=False
            )
            if code:
                return code, None
            if (cfg_dest / bak.MODLIST_NAME).is_file():
                collected["config"].append(f"config/{bak.MODLIST_NAME}")
            if (cfg_dest / "profile.txt").is_file():
                collected["config"].append("config/profile.txt")
        else:
            bak.step_backup_mcm(mo2_root, cfg_dest, dry_run=True)
            bak.step_backup_user_ltx(mo2_root, cfg_dest, dry_run=True)
            bak.step_backup_modlist(
                mo2_root, cfg_dest, profile=profile_name, dry_run=True
            )
            collected["config"].extend(
                [
                    f"config/{bak.MCM_DIFF_NAME}",
                    f"config/{bak.USER_LTX_NAME}",
                    f"config/{bak.MODLIST_NAME}",
                ]
            )

        # Live axr_options path note + copy (full current MCM file).
        axr = bak.live_axr_options_path(mo2_root)
        if axr is not None and axr.is_file():
            if _copy_if_file(axr, cfg_dest / "axr_options.ltx", dry_run=dry_run):
                collected["config"].append("config/axr_options.ltx")

        # Installer selection (which DOGMA options the user picked).
        sel = lib.mo2_tools_dir(mo2_root) / "config" / "selection.json"
        if _copy_if_file(sel, cfg_dest / "selection.json", dry_run=dry_run):
            collected["config"].append("config/selection.json")

        # Console history (often useful for recent commands / errors).
        hist = lib.game_dir(mo2_root) / "appdata" / "console_history.txt"
        if _copy_if_file(hist, cfg_dest / "console_history.txt", dry_run=dry_run):
            collected["config"].append("config/console_history.txt")

        # --- hardware ---
        hw = stage / HARDWARE_NAME
        if write_hardware_report(hw, dry_run=dry_run):
            collected["other"].append(HARDWARE_NAME)

        if dry_run:
            write_meta(
                stage / META_NAME,
                mo2_root=mo2_root,
                profile=profile_name,
                collected=collected,
                dry_run=True,
            )
            lib.info("Dry run - zip will not be created.")
            return 0, None

        # README for the user uploading the dump.
        readme = stage / "README.txt"
        readme.write_text(
            "\n".join(
                [
                    "D.O.G.M.A. debug dump",
                    "",
                    "Attach this zip when reporting a bug.",
                    "Contains game/xray logs, AlifePlus logs, DOGMA tool logs,",
                    "MO2 modlist, user.ltx, MCM diff, and a short hardware summary.",
                    "",
                    f"Created: {stamp_s}",
                    f"MO2: {mo2_root}",
                    "",
                ]
            ),
            encoding="utf-8",
            newline="\n",
        )
        collected["other"].append("README.txt")

        write_meta(
            stage / META_NAME,
            mo2_root=mo2_root,
            profile=profile_name,
            collected=collected,
            dry_run=False,
        )

        lib.info("Creating zip...")
        _zip_dir(stage, zip_path)

    size = zip_path.stat().st_size if zip_path.is_file() else 0
    lib.ok(f"Dump ready ({_fmt_bytes(size)}):\n  {zip_path}")
    print()
    print("Upload this zip with your bug report:")
    print(f"  {zip_path}")
    return 0, zip_path


def run_dump_cli(args) -> int:
    mo2 = lib.resolve_mo2_root(getattr(args, "mo2_root", None) or None)
    code, _dest = run_dump(
        mo2,
        profile=getattr(args, "profile", "") or "",
        dry_run=bool(getattr(args, "dry_run", False)),
    )
    return code
