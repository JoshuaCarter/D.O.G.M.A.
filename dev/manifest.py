#!/usr/bin/env python3
"""Read path mods from manifest.yml."""

from __future__ import annotations

from pathlib import Path

STAGE_RANK = {"omit": 0, "local": 1, "beta": 2, "gold": 3}
STAGE_ALIASES = {
    "0": "omit",
    "1": "local",
    "2": "gold",
    "omit": "omit",
    "local": "local",
    "beta": "beta",
    "gold": "gold",
    "dev": "local",
    "release": "gold",
    "off": "omit",
    "hidden": "omit",
}

MANIFEST_FILE = "manifest.yml"
MANIFEST_REL = Path("fomod") / MANIFEST_FILE
SKIP_KEYS = frozenset({"common"})
FOMOD_STAGES = frozenset({"beta", "gold"})
DEFAULT_RECOMMENDED = {
    "gold": ["dogma", "dogma-beta"],
    "beta": ["dogma-beta"],
}


def parse_stage(raw) -> str:
    if raw is None or raw is False:
        return "omit"
    if raw is True:
        raise ValueError("stage must be omit|local|beta|gold (got boolean true)")
    key = str(raw).strip().lower()
    if not key:
        return "omit"
    if key not in STAGE_ALIASES:
        raise ValueError(f"stage must be omit|local|beta|gold (got {raw!r})")
    return STAGE_ALIASES[key]


def stage_meets(stage: str, minimum: str) -> bool:
    return STAGE_RANK[parse_stage(stage)] >= STAGE_RANK[parse_stage(minimum)]


def feature_path_key(feat: str) -> str:
    return feat.strip().replace("\\", "/").replace("/", "_").lower()


def src_feature_dir(feat: str) -> str:
    f = feat.strip().replace("\\", "/").strip("/")
    if f == "common":
        return "_common"
    return f


def manifest_path(config_dir: Path) -> Path:
    if config_dir.is_file():
        return config_dir
    nested = config_dir / MANIFEST_REL
    if nested.is_file() or not (config_dir / MANIFEST_FILE).is_file():
        return nested
    return config_dir / MANIFEST_FILE


def _yaml_load(path: Path) -> dict:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to read config YAML") from exc
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name} root must be a mapping")
    return raw


def load_manifest(config_dir: Path) -> dict:
    path = manifest_path(config_dir)
    if not path.is_file():
        raise ValueError(f"missing {path}")
    return _yaml_load(path)


def parse_recommended(feat: str, raw) -> list[str] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError(f"{feat}: recommended must be a list")
    return [str(x) for x in raw]


def _feature_entry(feat: str, meta: dict, page: str, seen: set[str]) -> dict:
    feat = feat.strip().replace("\\", "/")
    if not feat:
        raise ValueError("empty feature path")
    if feat in seen:
        raise ValueError(f"duplicate path {feat!r}")
    seen.add(feat)
    title = str(meta.get("name") or "").strip()
    if not title:
        raise ValueError(f"{feat}: missing name")
    page = page.strip()
    if not page:
        raise ValueError(f"{feat}: missing page")
    return {
        "title": title,
        "path": feat,
        "stage": parse_stage(meta.get("stage", "omit")),
        "page": page,
        "desc": str(meta.get("desc") or "").strip(),
        "recommended": parse_recommended(feat, meta.get("recommended")),
    }


def iter_feature_info(config_dir: Path) -> list[dict]:
    """Each: title, path, stage, page, desc, recommended. Skips common.

    mods:
      common: { name: Common }
      Page Name:
        cat/feat: { name, stage, desc? }
    """
    data = load_manifest(config_dir)
    mods = data.get("mods")
    if not isinstance(mods, dict):
        raise ValueError("manifest.yml missing mods")
    out: list[dict] = []
    seen: set[str] = set()
    for key, val in mods.items():
        key = str(key).strip().replace("\\", "/")
        if not key or key.lower() in SKIP_KEYS:
            continue
        if not isinstance(val, dict):
            continue
        if "/" in key:
            out.append(_feature_entry(key, val, str(val.get("page") or ""), seen))
            continue
        for feat, meta in val.items():
            if not isinstance(meta, dict):
                continue
            out.append(_feature_entry(str(feat), meta, key, seen))
    return out


def iter_path_features(config_dir: Path) -> list[tuple[str, str]]:
    """Return (path, stage) in catalog order. Skips common."""
    return [(r["path"], r["stage"]) for r in iter_feature_info(config_dir)]


def fomod_wizard(config_dir: Path) -> dict:
    data = load_manifest(config_dir)
    wizard = data.get("fomod")
    if not isinstance(wizard, dict):
        raise ValueError("manifest.yml missing fomod")
    if not wizard.get("name"):
        raise ValueError("fomod missing name")
    if not wizard.get("presets"):
        raise ValueError("fomod missing presets")
    if not wizard.get("required"):
        raise ValueError("fomod missing required")
    return wizard
