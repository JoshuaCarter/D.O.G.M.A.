"""Ensure Anomaly ``tools/_unpacked`` exists for SAGE assets.

Uses ``Anomaly/tools/converter.exe`` like ``db_unpacker.bat``, but only the
archives SAGE needs: ``configs.db0`` (textures_descr + text) and
``textures_ui.db0`` (UI DDS).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .diaglog import get_logger

_log = get_logger("db_unpack")


@dataclass
class UnpackResult:
    ran: bool = False
    anomaly_root: Path | None = None
    out_root: Path | None = None
    unpacked: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped_reason: str = ""


def find_anomaly_root(preferred: str | Path | None = None) -> Path | None:
    """Resolve the user-supplied Anomaly root (no guessed drive letters)."""
    if not preferred or not str(preferred).strip():
        return None
    root = Path(str(preferred)).expanduser()
    conv = root / "tools" / "converter.exe"
    db = root / "db"
    if conv.is_file() and db.is_dir():
        return root
    return None


def preferred_unpack_root(anomaly: Path) -> Path:
    """Unpack target: ``{Anomaly}/tools/_unpacked`` (same as db_unpacker.bat)."""
    return anomaly / "tools" / "_unpacked"


@dataclass
class UnpackNeed:
    needed: bool
    anomaly_root: Path | None = None
    out_root: Path | None = None
    missing: list[str] = field(default_factory=list)
    present_root: Path | None = None


def check_anomaly_unpack_needed(
    anomaly_root: str | Path | None = None,
) -> UnpackNeed:
    """True when Anomaly root is valid but UI assets are not unpacked yet."""
    anomaly = find_anomaly_root(anomaly_root)
    if anomaly is None:
        return UnpackNeed(needed=False, missing=["Anomaly install not found"])
    out = preferred_unpack_root(anomaly)
    if out.is_dir() and not missing_markers(out):
        return UnpackNeed(
            needed=False,
            anomaly_root=anomaly,
            out_root=out,
            present_root=out,
        )
    miss = missing_markers(out)
    if not out.is_dir():
        miss = ["tools/_unpacked"] + [m for m in miss if m not in ("tools/_unpacked",)]
    return UnpackNeed(
        needed=True,
        anomaly_root=anomaly,
        out_root=out,
        missing=miss,
    )


def _has_descr(out: Path) -> bool:
    d = out / "configs" / "ui" / "textures_descr"
    try:
        return d.is_dir() and any(d.glob("*.xml"))
    except OSError:
        return False


def _has_text(out: Path) -> bool:
    d = out / "configs" / "text" / "eng"
    try:
        return d.is_dir() and any(d.iterdir())
    except OSError:
        return False


def _has_ui_textures(out: Path) -> bool:
    d = out / "textures"
    try:
        if not d.is_dir():
            return False
        # UI packs usually drop ui_*.dds (possibly nested).
        return any(d.rglob("ui_*.dds")) or any(d.glob("*.dds"))
    except OSError:
        return False


def missing_markers(out: Path) -> list[str]:
    miss: list[str] = []
    if not _has_descr(out):
        miss.append("configs/ui/textures_descr")
    if not _has_text(out):
        miss.append("configs/text/eng")
    if not _has_ui_textures(out):
        miss.append("textures (ui)")
    return miss


def _run_converter(
    *,
    converter: Path,
    db_file: Path,
    out_dir: Path,
) -> tuple[bool, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(converter),
        "-unpack",
        "-xdb",
        str(db_file),
        "-dir",
        str(out_dir),
    ]
    _log.info("unpack: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(converter.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        return False, str(exc)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        _log.error("converter failed (%s): %s", db_file.name, err)
        return False, err
    if proc.stdout:
        _log.debug("converter stdout: %s", proc.stdout.strip()[:500])
    return True, ""


def ensure_anomaly_db_unpacked(
    *,
    force: bool = False,
    anomaly_root: str | Path | None = None,
) -> UnpackResult:
    """Unpack Anomaly configs + UI textures into ``tools/_unpacked`` when missing."""
    result = UnpackResult()
    need = check_anomaly_unpack_needed(anomaly_root)
    anomaly = need.anomaly_root or find_anomaly_root(anomaly_root)
    if anomaly is None:
        result.skipped_reason = "Anomaly install not found (converter.exe / db/)"
        _log.info("db_unpack skip: %s", result.skipped_reason)
        return result
    result.anomaly_root = anomaly
    out = preferred_unpack_root(anomaly)
    result.out_root = out
    if not need.needed and not force:
        result.skipped_reason = f"already present under {need.present_root or out}"
        _log.debug("db_unpack skip: %s", result.skipped_reason)
        return result

    miss = missing_markers(out) if out.is_dir() else [
        "configs/ui/textures_descr",
        "configs/text/eng",
        "textures (ui)",
    ]
    converter = anomaly / "tools" / "converter.exe"
    configs_db = anomaly / "db" / "configs" / "configs.db0"
    # Prefer dedicated UI pack; fall back to first textures.db0 if needed.
    ui_db = anomaly / "db" / "textures" / "textures_ui.db0"
    if not ui_db.is_file():
        ui_db = anomaly / "db" / "textures" / "textures.db0"

    jobs: list[tuple[str, Path]] = []
    need_configs = force or "configs/ui/textures_descr" in miss or "configs/text/eng" in miss
    need_ui = force or "textures (ui)" in miss
    # Dir missing entirely → unpack both.
    if not out.is_dir():
        need_configs = True
        need_ui = True
    if need_configs:
        if configs_db.is_file():
            jobs.append(("configs.db0", configs_db))
        else:
            result.errors.append(f"missing {configs_db}")
    if need_ui:
        if ui_db.is_file():
            jobs.append((ui_db.name, ui_db))
        else:
            result.errors.append(f"missing UI textures db under {anomaly / 'db' / 'textures'}")

    if not jobs:
        result.skipped_reason = "nothing to unpack (archives missing)"
        _log.warning("db_unpack: %s", result.skipped_reason)
        return result

    _log.info(
        "db_unpack → %s (missing=%s force=%s)",
        out,
        miss or "forced",
        force,
    )
    result.ran = True
    for label, db_path in jobs:
        ok, err = _run_converter(converter=converter, db_file=db_path, out_dir=out)
        if ok:
            result.unpacked.append(label)
        else:
            result.errors.append(f"{label}: {err}")

    still = missing_markers(out)
    if still:
        result.errors.append(f"still missing after unpack: {', '.join(still)}")
        _log.warning("db_unpack incomplete: %s", still)
    else:
        _log.info("db_unpack ok: %s", result.unpacked)
    return result
