from __future__ import annotations

from pathlib import Path
import re

import pytest

from bidsflow.project import find_project_config, load_project_context


def _write_config(path: Path, text: str) -> Path:
    path.write_text(text.strip() + "\n", encoding="utf-8", newline="\n")
    return path


def _minimal_config(project_root: str = ".") -> str:
    return f"""
[project]
name = "Demo"
root = "{project_root}"

[paths]
source_root = "sourcedata"
raw_bids_root = "sourcedata/raw"
derivatives_root = "derivatives"
work_root = "work"
logs_root = "logs"
state_root = "state"

[heudiconv]
heuristic = "code/heudiconv/heuristic.py"

[execution]
scheduler = "none"
"""


def test_find_project_config_walks_up_from_descendant(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path / "bidsflow.toml", _minimal_config())
    nested = tmp_path / "work" / "nested"
    nested.mkdir(parents=True)

    assert find_project_config(nested) == config_path


def test_find_project_config_reports_missing_config(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Could not find bidsflow.toml"):
        find_project_config(tmp_path)


def test_load_project_context_resolves_absolute_roots_and_scheduler_defaults(tmp_path: Path) -> None:
    project_root = tmp_path / "absolute-project"
    project_root.mkdir()
    source_root = tmp_path / "absolute-source"
    config_path = _write_config(
        tmp_path / "bidsflow.toml",
        f"""
[project]
name = "Absolute"
root = "{project_root.as_posix()}"

[paths]
source_root = "{source_root.as_posix()}"

[execution]
scheduler = "sge"
""",
    )

    context = load_project_context(config_path)

    assert context.project_root == project_root.resolve()
    assert context.paths.source_root == source_root.resolve()
    assert context.paths.raw_bids_root == project_root / "sourcedata" / "raw"
    assert context.execution.submit_command == ("qsub", "-terse")
    assert context.heudiconv.launcher == ("heudiconv",)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('project = "bad"', "[project] must be a TOML table."),
        ("[project]\nroot = 1\n[paths]\n", "[project].root must be a string path."),
        (
            "[project]\nroot = \".\"\n[paths]\nsource_root = 1\n",
            "[paths].source_root must be a string path.",
        ),
        (_minimal_config() + "\n[sources]\npattern = 1\n", "[sources].pattern must be a string."),
        (
            _minimal_config() + "\n[sources]\ncommand = []\n",
            "[sources].command must be a non-empty list of strings.",
        ),
        (
            _minimal_config()
            + "\n[sources]\npattern = \"SUB{subject}\"\ncommand = [\"python\"]\n",
            "[sources] may define pattern or command, but not both.",
        ),
        (
            _minimal_config().replace('scheduler = "none"', "scheduler = 1"),
            "[execution].scheduler must be a string.",
        ),
        (
            _minimal_config().replace('scheduler = "none"', 'scheduler = "slurm"'),
            "[execution].scheduler must be one of: none, sge.",
        ),
        (
            _minimal_config().replace(
                'scheduler = "none"',
                'scheduler = "none"\nsubmit_command = []',
            ),
            "[execution].submit_command must be a non-empty list of strings.",
        ),
    ],
)
def test_load_project_context_rejects_invalid_config(tmp_path: Path, body: str, message: str) -> None:
    config_path = _write_config(tmp_path / "bidsflow.toml", body)

    with pytest.raises(ValueError, match=re.escape(message)):
        load_project_context(config_path)
