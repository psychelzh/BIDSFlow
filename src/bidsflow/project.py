"""Project configuration loading and path resolution for BIDSFlow."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import tomllib


@dataclass(frozen=True)
class HeudiconvConfig:
    """Resolved HeuDiConv command and heuristic settings for a project."""

    launcher: tuple[str, ...]
    heuristic: Path


@dataclass(frozen=True)
class SourcesConfig:
    """Source-label derivation settings from the project config."""

    pattern: str | None
    command: tuple[str, ...] | None


@dataclass(frozen=True)
class ExecutionConfig:
    """Execution backend settings resolved from the project config."""

    scheduler: str
    submit_command: tuple[str, ...] | None


@dataclass(frozen=True)
class ProjectPaths:
    """Resolved project paths used by BIDSFlow workflows."""

    source_root: Path
    raw_bids_root: Path
    derivatives_root: Path
    work_root: Path
    logs_root: Path
    state_root: Path


@dataclass(frozen=True)
class ProjectContext:
    """Fully resolved project configuration passed to workflow planners."""

    config_path: Path
    project_root: Path
    paths: ProjectPaths
    sources: SourcesConfig
    heudiconv: HeudiconvConfig
    execution: ExecutionConfig


def find_project_config(start_dir: Path) -> Path:
    """Return the nearest bidsflow.toml at or above start_dir."""

    current = start_dir.resolve()
    for directory in (current, *current.parents):
        candidate = directory / "bidsflow.toml"
        if candidate.is_file():
            return candidate

    raise ValueError(
        "Could not find bidsflow.toml in the current directory or its parents. "
        "Run the command from a BIDSFlow project directory or one of its descendants."
    )


def load_project_context(config_path: Path) -> ProjectContext:
    """Load bidsflow.toml into a resolved project context."""

    raw_config = tomllib.loads(config_path.read_text(encoding="utf-8"))

    project_section = _require_table(raw_config, "project")
    paths_section = _require_table(raw_config, "paths")

    project_root_value = project_section.get("root", ".")
    if not isinstance(project_root_value, str):
        raise ValueError("[project].root must be a string path.")

    project_root = _resolve_from_config_dir(config_path, Path(project_root_value))
    paths = _load_project_paths(project_root, paths_section)
    sources = _load_sources_config(_require_table(raw_config, "sources"))
    heudiconv = _load_heudiconv_config(project_root, _require_table(raw_config, "heudiconv"))
    execution = _load_execution_config(_require_table(raw_config, "execution"))

    return ProjectContext(
        config_path=config_path,
        project_root=project_root,
        paths=paths,
        sources=sources,
        heudiconv=heudiconv,
        execution=execution,
    )


def _require_table(config: dict[str, Any], key: str) -> dict[str, Any]:
    """Return a TOML table value or an empty table when the key is absent."""

    value = config.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"[{key}] must be a TOML table.")
    return value


def _resolve_from_config_dir(config_path: Path, candidate: Path) -> Path:
    """Resolve a path relative to the directory that contains bidsflow.toml."""

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
    """Resolve a path-valued config key relative to the project root."""

    value = section.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"[{section_name}].{key} must be a string path.")
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate.resolve()
    return (project_root / candidate).resolve()


def _require_project_owned_path(
    project_root: Path,
    path: Path,
    *,
    section_name: str,
    key: str,
) -> Path:
    """Require a managed workflow path to stay under the project root."""

    if not path.is_relative_to(project_root):
        raise ValueError(f"[{section_name}].{key} must resolve under [project].root.")
    return path


def _load_non_empty_string_list(value: Any, field_name: str) -> tuple[str, ...]:
    """Load a non-empty TOML string list with no blank elements."""

    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item.strip() for item in value)
    ):
        raise ValueError(f"{field_name} must be a non-empty list of strings.")
    return tuple(value)


def _load_project_paths(project_root: Path, paths_section: dict[str, Any]) -> ProjectPaths:
    """Load the configured project directory layout."""

    source_root = _resolve_config_path(
        project_root,
        paths_section,
        "paths",
        "source_root",
        "sourcedata",
    )
    raw_bids_root = _resolve_config_path(
        project_root,
        paths_section,
        "paths",
        "raw_bids_root",
        "sourcedata/raw",
    )
    derivatives_root = _resolve_config_path(
        project_root,
        paths_section,
        "paths",
        "derivatives_root",
        "derivatives",
    )
    work_root = _require_project_owned_path(
        project_root,
        _resolve_config_path(project_root, paths_section, "paths", "work_root", "work"),
        section_name="paths",
        key="work_root",
    )
    logs_root = _require_project_owned_path(
        project_root,
        _resolve_config_path(project_root, paths_section, "paths", "logs_root", "logs"),
        section_name="paths",
        key="logs_root",
    )
    state_root = _require_project_owned_path(
        project_root,
        _resolve_config_path(project_root, paths_section, "paths", "state_root", "state"),
        section_name="paths",
        key="state_root",
    )

    return ProjectPaths(
        source_root=source_root,
        raw_bids_root=raw_bids_root,
        derivatives_root=derivatives_root,
        work_root=work_root,
        logs_root=logs_root,
        state_root=state_root,
    )


def _load_heudiconv_config(project_root: Path, heudiconv_section: dict[str, Any]) -> HeudiconvConfig:
    """Load HeuDiConv launcher and heuristic configuration."""

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
    else:
        launcher_value = _load_non_empty_string_list(
            launcher,
            "[heudiconv].launcher",
        )

    return HeudiconvConfig(
        launcher=launcher_value,
        heuristic=heuristic,
    )


def _load_sources_config(
    sources_section: dict[str, Any],
) -> SourcesConfig:
    """Load source label derivation configuration."""

    pattern = sources_section.get("pattern")
    command = sources_section.get("command")

    if pattern is not None and not isinstance(pattern, str):
        raise ValueError("[sources].pattern must be a string.")
    if command is not None:
        command = _load_non_empty_string_list(command, "[sources].command")
    if pattern is not None and command is not None:
        raise ValueError(
            "[sources] may define pattern or command, but not both."
        )

    return SourcesConfig(
        pattern=pattern,
        command=command,
    )


def _load_execution_config(
    execution_section: dict[str, Any],
) -> ExecutionConfig:
    """Load scheduler selection and submit command configuration."""

    scheduler = execution_section.get("scheduler", "none")
    submit_command = execution_section.get("submit_command")

    if not isinstance(scheduler, str):
        raise ValueError("[execution].scheduler must be a string.")
    if scheduler not in {"none", "sge"}:
        raise ValueError("[execution].scheduler must be one of: none, sge.")
    if submit_command is not None:
        submit_command = _load_non_empty_string_list(
            submit_command,
            "[execution].submit_command",
        )

    if scheduler == "sge":
        submit_command = submit_command or ["qsub", "-terse"]
    else:
        submit_command = None

    return ExecutionConfig(
        scheduler=scheduler,
        submit_command=tuple(submit_command) if submit_command is not None else None,
    )
