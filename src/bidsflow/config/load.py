from __future__ import annotations

from pathlib import Path

from bidsflow.config.models import Config
import tomllib


def _resolve_path(path: Path, base: Path) -> Path:
    if path.is_absolute():
        return path
    return (base / path).resolve()


def load_config(path: Path) -> Config:
    config_path = path.resolve()
    with config_path.open("rb") as stream:
        data = tomllib.load(stream)

    config = Config.model_validate(data)
    config_dir = config_path.parent

    config.project.root = _resolve_path(config.project.root, config_dir)
    project_root = config.project.root

    config.project.sourcedata_root = _resolve_path(config.project.sourcedata_root, project_root)
    config.project.bids_root = _resolve_path(config.project.bids_root, project_root)
    config.project.derivatives_root = _resolve_path(config.project.derivatives_root, project_root)

    config.execution.work_root = _resolve_path(config.execution.work_root, project_root)
    config.execution.logs_root = _resolve_path(config.execution.logs_root, project_root)
    config.execution.state_root = _resolve_path(config.execution.state_root, project_root)

    if config.heudiconv.apptainer_image is not None:
        config.heudiconv.apptainer_image = _resolve_path(config.heudiconv.apptainer_image, project_root)
    if config.heudiconv.heuristic is not None:
        config.heudiconv.heuristic = _resolve_path(config.heudiconv.heuristic, project_root)
    config.heudiconv.outdir = _resolve_path(config.heudiconv.outdir, project_root)
    if config.heudiconv.dcmconfig is not None:
        config.heudiconv.dcmconfig = _resolve_path(config.heudiconv.dcmconfig, project_root)

    return config
