from __future__ import annotations

from pathlib import Path

from bidsflow.config.load import load_config


def test_load_config_resolves_paths_relative_to_project_root() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config = load_config(repo_root / "examples" / "project.toml")

    assert config.execution.scheduler == "sge"
    assert config.project.root == repo_root
    assert config.project.sourcedata_root == repo_root / "sourcedata" / "dicom"
    assert config.execution.logs_root == repo_root / "logs"
    assert config.execution.state_root == repo_root / "state"
    assert config.heudiconv.heuristic == repo_root / "code" / "heuristics" / "heuristic.py"
