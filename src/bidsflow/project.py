from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import tomllib


@dataclass(frozen=True)
class HeudiconvConfig:
    launcher: tuple[str, ...]
    heuristic: Path
    manifest: "HeudiconvManifestConfig"


@dataclass(frozen=True)
class HeudiconvManifestConfig:
    template: str | None
    command: tuple[str, ...] | None


@dataclass(frozen=True)
class ProjectPaths:
    source_root: Path
    raw_bids_root: Path
    derivatives_root: Path
    work_root: Path
    logs_root: Path
    state_root: Path


@dataclass(frozen=True)
class ProjectContext:
    config_path: Path
    project_root: Path
    paths: ProjectPaths
    heudiconv: HeudiconvConfig


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
    paths = _load_project_paths(project_root, paths_section)
    heudiconv = _load_heudiconv_config(project_root, _require_table(raw_config, "heudiconv"))

    return ProjectContext(
        config_path=config_path,
        project_root=project_root,
        paths=paths,
        heudiconv=heudiconv,
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


def _resolve_config_path(
    project_root: Path,
    section: dict[str, Any],
    section_name: str,
    key: str,
    default: str,
) -> Path:
    value = section.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"[{section_name}].{key} must be a string path.")
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate.resolve()
    return (project_root / candidate).resolve()


def _load_project_paths(project_root: Path, paths_section: dict[str, Any]) -> ProjectPaths:
    return ProjectPaths(
        source_root=_resolve_config_path(project_root, paths_section, "paths", "source_root", "sourcedata"),
        raw_bids_root=_resolve_config_path(project_root, paths_section, "paths", "raw_bids_root", "sourcedata/raw"),
        derivatives_root=_resolve_config_path(project_root, paths_section, "paths", "derivatives_root", "derivatives"),
        work_root=_resolve_config_path(project_root, paths_section, "paths", "work_root", "work"),
        logs_root=_resolve_config_path(project_root, paths_section, "paths", "logs_root", "logs"),
        state_root=_resolve_config_path(project_root, paths_section, "paths", "state_root", "state"),
    )


def _load_heudiconv_config(project_root: Path, heudiconv_section: dict[str, Any]) -> HeudiconvConfig:
    heuristic = _resolve_config_path(
        project_root,
        heudiconv_section,
        "heudiconv",
        "heuristic",
        "code/heudiconv/heuristic.py",
    )
    launcher = heudiconv_section.get("launcher")
    if launcher is None:
        launcher_value = ("heudiconv",)
    elif not isinstance(launcher, list) or not launcher or not all(isinstance(item, str) for item in launcher):
        raise ValueError("[heudiconv].launcher must be a non-empty list of strings.")
    else:
        launcher_value = tuple(launcher)

    manifest = _load_heudiconv_manifest_config(_require_table(heudiconv_section, "manifest"))
    return HeudiconvConfig(
        launcher=launcher_value,
        heuristic=heuristic,
        manifest=manifest,
    )


def _load_heudiconv_manifest_config(
    manifest_section: dict[str, Any],
) -> HeudiconvManifestConfig:
    template = manifest_section.get("template")
    command = manifest_section.get("command")

    if template is not None and not isinstance(template, str):
        raise ValueError("[heudiconv.manifest].template must be a string.")
    if command is not None and (
        not isinstance(command, list)
        or not command
        or not all(isinstance(item, str) for item in command)
    ):
        raise ValueError("[heudiconv.manifest].command must be a non-empty list of strings.")
    if template is not None and command is not None:
        raise ValueError(
            "[heudiconv.manifest] may define template or command, but not both."
        )

    return HeudiconvManifestConfig(
        template=template,
        command=tuple(command) if command is not None else None,
    )
