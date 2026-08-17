#!/usr/bin/env python3
"""Read path mods from config/manifest-dogma-*.yml (no Setup / third-party)."""

from __future__ import annotations

from pathlib import Path

STAGE_RANK = {"omit": 0, "dev": 1, "release": 2}
STAGE_ALIASES = {
    "0": "omit",
    "1": "dev",
    "2": "release",
    "omit": "omit",
    "dev": "dev",
    "release": "release",
    "local": "dev",
    "off": "omit",
    "hidden": "omit",
}

FEATURE_FILES = (
    "manifest-dogma-features.yml",
    "manifest-dogma-tweaks.yml",
)


def parse_stage(raw) -> str:
    if raw is False:
        return "omit"
    if raw is True:
        raise ValueError("stage must be omit|dev|release (got boolean true)")
    key = str(raw).strip().lower()
    if key not in STAGE_ALIASES:
        raise ValueError(f"stage must be omit|dev|release (got {raw!r})")
    return STAGE_ALIASES[key]


def stage_meets(stage: str, minimum: str) -> bool:
    return STAGE_RANK[parse_stage(stage)] >= STAGE_RANK[parse_stage(minimum)]


def feature_path_key(feat: str) -> str:
    return feat.strip().replace("\\", "/").replace("/", "_").lower()


def src_feature_dir(feat: str) -> str:
    f = feat.strip().replace("\\", "/").strip("/")
    if f in ("common", "debug"):
        return f"_{f}"
    return f


def _yaml_load(path: Path) -> dict:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to read config/manifest-dogma-*.yml") from exc
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name} root must be a mapping")
    return raw


def iter_path_features(config_dir: Path) -> list[tuple[str, str]]:
    """Return (path, stage) in catalog order. Skips common (always-on)."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name in FEATURE_FILES:
        path = config_dir / name
        if not path.is_file():
            continue
        for key, meta in _yaml_load(path).items():
            title = str(key).strip()
            if not title or title.lower() == "common":
                continue
            if not isinstance(meta, dict):
                continue
            feat = str(meta.get("path") or "").strip().replace("\\", "/")
            if not feat:
                continue
            if feat in seen:
                raise ValueError(f"duplicate path {feat!r} ({title})")
            seen.add(feat)
            stage = parse_stage(meta.get("stage", meta.get("fomod", meta.get("level"))))
            out.append((feat, stage))
    return out
