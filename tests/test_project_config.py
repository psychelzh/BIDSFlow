from __future__ import annotations

from pathlib import Path
import re

import pytest

from bidsflow.project import (
    find_project_config,
    load_fmriprep_config,
    load_heudiconv_config,
    load_project_context,
    target_config_path,
)


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
    assert context.resources.fs_license_file is None
    assert target_config_path(context, "heudiconv") == project_root / "config" / "heudiconv.toml"
    assert target_config_path(context, "fmriprep") == project_root / "config" / "fmriprep.toml"


def test_target_config_loaders_read_target_configs(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path / "bidsflow.toml", _minimal_config())
    target_config_root = tmp_path / "config"
    target_config_root.mkdir()
    _write_config(
        target_config_root / "heudiconv.toml",
        """
heuristic = "code/custom/heuristic.py"
launcher = ["custom-heudiconv"]

[sources]
pattern = "NEW{subject}"
""",
    )
    _write_config(
        target_config_root / "fmriprep.toml",
        """
launcher = ["custom-fmriprep"]
""",
    )

    context = load_project_context(config_path)
    heudiconv_config = load_heudiconv_config(context)
    fmriprep_config = load_fmriprep_config(context)

    assert heudiconv_config.config_path == (target_config_root / "heudiconv.toml").resolve()
    assert heudiconv_config.launcher == ("custom-heudiconv",)
    assert heudiconv_config.heuristic == (tmp_path / "code" / "custom" / "heuristic.py").resolve()
    assert heudiconv_config.sources.pattern == "NEW{subject}"
    assert fmriprep_config.config_path == (target_config_root / "fmriprep.toml").resolve()
    assert fmriprep_config.launcher == ("custom-fmriprep",)


def test_load_project_context_does_not_parse_target_configs(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path / "bidsflow.toml", _minimal_config())
    target_config_root = tmp_path / "config"
    target_config_root.mkdir()
    _write_config(target_config_root / "heudiconv.toml", "launcher = [")
    _write_config(target_config_root / "fmriprep.toml", "launcher = [")

    context = load_project_context(config_path)

    assert context.project_root == tmp_path.resolve()


def test_target_config_loaders_are_isolated(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path / "bidsflow.toml", _minimal_config())
    target_config_root = tmp_path / "config"
    target_config_root.mkdir()
    _write_config(
        target_config_root / "heudiconv.toml",
        """
launcher = ["custom-heudiconv"]
""",
    )
    _write_config(target_config_root / "fmriprep.toml", "launcher = []")
    context = load_project_context(config_path)

    assert load_heudiconv_config(context).launcher == ("custom-heudiconv",)
    with pytest.raises(
        ValueError,
        match=re.escape("[fmriprep].launcher must be a non-empty list of strings."),
    ):
        load_fmriprep_config(context)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('project = "bad"', "[project] must be a TOML table."),
        ("[project]\nroot = 1\n[paths]\n", "[project].root must be a string path."),
        (
            "[project]\nroot = \".\"\n[paths]\nsource_root = 1\n",
            "[paths].source_root must be a string path.",
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
        (
            _minimal_config().replace(
                'scheduler = "none"',
                'scheduler = "none"\nsubmit_command = [" "]',
            ),
            "[execution].submit_command must be a non-empty list of strings.",
        ),
        (
            _minimal_config() + "\n[resources]\nfs_license_file = 1\n",
            "[resources].fs_license_file must be a string path.",
        ),
    ],
)
def test_load_project_context_rejects_invalid_config(tmp_path: Path, body: str, message: str) -> None:
    config_path = _write_config(tmp_path / "bidsflow.toml", body)

    with pytest.raises(ValueError, match=re.escape(message)):
        load_project_context(config_path)


@pytest.mark.parametrize(
    ("target", "body", "message"),
    [
        ("heudiconv", "launcher = [\"\"]\n", "[heudiconv].launcher must be a non-empty list of strings."),
        ("heudiconv", "[sources]\npattern = 1\n", "[sources].pattern must be a string."),
        (
            "heudiconv",
            "[sources]\ncommand = []\n",
            "[sources].command must be a non-empty list of strings.",
        ),
        (
            "heudiconv",
            "[sources]\ncommand = [\"\"]\n",
            "[sources].command must be a non-empty list of strings.",
        ),
        (
            "heudiconv",
            "[sources]\npattern = \"SUB{subject}\"\ncommand = [\"python\"]\n",
            "[sources] may define pattern or command, but not both.",
        ),
        ("fmriprep", "launcher = []\n", "[fmriprep].launcher must be a non-empty list of strings."),
    ],
)
def test_target_config_loaders_reject_invalid_target_config(
    tmp_path: Path,
    target: str,
    body: str,
    message: str,
) -> None:
    config_path = _write_config(tmp_path / "bidsflow.toml", _minimal_config())
    target_config_root = tmp_path / "config"
    target_config_root.mkdir()
    _write_config(target_config_root / f"{target}.toml", body)
    context = load_project_context(config_path)
    loader = load_heudiconv_config if target == "heudiconv" else load_fmriprep_config

    with pytest.raises(ValueError, match=re.escape(message)):
        loader(context)


@pytest.mark.parametrize(
    ("key", "default_value"),
    [
        ("work_root", "work"),
        ("logs_root", "logs"),
        ("state_root", "state"),
    ],
)
def test_load_project_context_rejects_external_managed_roots(
    tmp_path: Path,
    key: str,
    default_value: str,
) -> None:
    outside_root = tmp_path.parent / f"external-{key}"
    body = _minimal_config().replace(
        f'{key} = "{default_value}"',
        f'{key} = "{outside_root.as_posix()}"',
    )
    config_path = _write_config(tmp_path / "bidsflow.toml", body)

    with pytest.raises(
        ValueError,
        match=re.escape(f"[paths].{key} must resolve under [project].root."),
    ):
        load_project_context(config_path)
