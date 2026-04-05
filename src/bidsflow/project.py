from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import tomllib


@dataclass(frozen=True)
class ProjectContext:
    config_path: Path
    project_root: Path
    raw_bids_root: Path
    logs_root: Path
    state_root: Path
    config: dict[str, Any]


def find_project_config(explicit_config: Path | None, start_dir: Path) -> Path:
    if explicit_config is not None:
        config_path = explicit_config.resolve()
        if not config_path.is_file():
            raise ValueError(f"Config file does not exist: {config_path}")
        return config_path

    current = start_dir.resolve()
    for directory in (current, *current.parents):
        candidate = directory / "bidsflow.toml"
        if candidate.is_file():
            return candidate

    raise ValueError(
        "Could not find bidsflow.toml in the current directory or its parents. "
        "Use --config to point at a project config."
    )


def load_project_context(config_path: Path) -> ProjectContext:
    raw_config = tomllib.loads(config_path.read_text(encoding="utf-8"))

    project_section = _require_table(raw_config, "project")
    paths_section = _require_table(raw_config, "paths")

    project_root_value = project_section.get("root", ".")
    if not isinstance(project_root_value, str):
        raise ValueError("[project].root must be a string path.")

    project_root = _resolve_from_config_dir(config_path, Path(project_root_value))
    raw_bids_root = _resolve_within_project(project_root, paths_section, "raw_bids_root", "sourcedata/raw")
    logs_root = _resolve_within_project(project_root, paths_section, "logs_root", "logs")
    state_root = _resolve_within_project(project_root, paths_section, "state_root", "state")

    return ProjectContext(
        config_path=config_path,
        project_root=project_root,
        raw_bids_root=raw_bids_root,
        logs_root=logs_root,
        state_root=state_root,
        config=raw_config,
    )


def _require_table(config: dict[str, Any], key: str) -> dict[str, Any]:
    value = config.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"[{key}] must be a TOML table.")
    return value


def _resolve_from_config_dir(config_path: Path, candidate: Path) -> Path:
    if candidate.is_absolute():
        return candidate.resolve()
    return (config_path.parent / candidate).resolve()


def _resolve_within_project(
    project_root: Path,
    section: dict[str, Any],
    key: str,
    default: str,
) -> Path:
    value = section.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"[paths].{key} must be a string path.")
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate.resolve()
    return (project_root / candidate).resolve()
