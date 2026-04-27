from __future__ import annotations

from pathlib import Path
import tomllib

import click
import pytest
from syrupy.assertion import SnapshotAssertion

import bidsflow.cli as cli
from bidsflow.cli import app
from helpers import assert_lines_in_order


@pytest.fixture(autouse=True)
def _no_scheduler_on_path(monkeypatch) -> None:
    monkeypatch.setattr(cli.shutil, "which", lambda executable: None)


@pytest.mark.parametrize(
    "command_args",
    [
        [],
        ["init"],
        ["heudiconv"],
        ["heudiconv", "init"],
        ["heudiconv", "draft"],
        ["heudiconv", "status"],
    ],
)
@pytest.mark.parametrize("help_option", ["-h", "--help"])
def test_cli_accepts_help_options(
    runner,
    command_args: list[str],
    help_option: str,
) -> None:
    result = runner.invoke(app, [*command_args, help_option])

    assert result.exit_code == 0, result.output
    assert "Usage" in result.output


def test_init_rejects_target_file(tmp_path: Path, runner) -> None:
    target = tmp_path / "not-a-directory"
    target.write_text("not a directory\n", encoding="utf-8")

    result = runner.invoke(app, ["init", str(target)])

    assert result.exit_code == 2
    assert "is a file" in result.output

    with pytest.raises(click.exceptions.Exit) as exc_info:
        cli.init(directory=target, name=None, force=False, make_dirs=False, scheduler="none")
    assert exc_info.value.exit_code == 2


def test_init_writes_config_without_materializing_layout_by_default(
    tmp_path: Path,
    runner,
    snapshot: SnapshotAssertion,
) -> None:
    project_dir = tmp_path / "demo-project"

    result = runner.invoke(app, ["init", str(project_dir)])

    assert result.exit_code == 0, result.output
    assert (project_dir / "bidsflow.toml").is_file()
    assert not (project_dir / "sourcedata").exists()
    assert not (project_dir / "sourcedata" / "raw").exists()
    assert not (project_dir / "derivatives").exists()
    assert not (project_dir / "work").exists()
    assert not (project_dir / "logs").exists()
    assert not (project_dir / "state").exists()

    config_text = (project_dir / "bidsflow.toml").read_text(encoding="utf-8")
    assert 'scheduler = "none"' in config_text
    assert '# scheduler = "sge"' in config_text
    assert '# submit_command = ["qsub", "-terse"]' in config_text
    assert '\nsubmit_command = ["qsub", "-terse"]' not in config_text
    assert_lines_in_order(config_text, ["[paths]", "# [sources]", "[execution]", "[heudiconv]"])
    assert config_text == snapshot(name="init_default_bidsflow_toml")
    assert "Scheduler: none (no supported scheduler detected)" in result.output


def test_init_accepts_explicit_none_scheduler(tmp_path: Path, runner) -> None:
    project_dir = tmp_path / "none-project"

    result = runner.invoke(app, ["init", str(project_dir), "--scheduler", "none"])

    assert result.exit_code == 0, result.output
    assert "Scheduler: none" in result.output
    assert 'scheduler = "none"' in (project_dir / "bidsflow.toml").read_text(encoding="utf-8")


def test_init_scheduler_auto_detects_sge(tmp_path: Path, monkeypatch, runner) -> None:
    project_dir = tmp_path / "sge-project"
    monkeypatch.setattr(
        cli.shutil,
        "which",
        lambda executable: "C:/sge/bin/qsub.exe" if executable == "qsub" else None,
    )

    result = runner.invoke(app, ["init", str(project_dir)])

    assert result.exit_code == 0, result.output
    config_text = (project_dir / "bidsflow.toml").read_text(encoding="utf-8")
    assert 'scheduler = "sge"' in config_text
    assert 'submit_command = ["qsub", "-terse"]' in config_text
    assert "Scheduler: sge (detected qsub)" in result.output


def test_init_explicit_sge_uses_detected_qsub_message(tmp_path: Path, monkeypatch, runner) -> None:
    monkeypatch.setattr(
        cli.shutil,
        "which",
        lambda executable: "/opt/sge/bin/qsub" if executable == "qsub" else None,
    )

    result = runner.invoke(app, ["init", str(tmp_path / "sge-project"), "--scheduler", "sge"])

    assert result.exit_code == 0, result.output
    assert "Scheduler: sge (detected qsub)" in result.output
    assert "Warning:" not in result.output


def test_init_scheduler_sge_writes_config_even_when_qsub_is_missing(tmp_path: Path, runner) -> None:
    project_dir = tmp_path / "sge-project"

    result = runner.invoke(app, ["init", str(project_dir), "--scheduler", "sge"])

    assert result.exit_code == 0, result.output
    config_text = (project_dir / "bidsflow.toml").read_text(encoding="utf-8")
    assert 'scheduler = "sge"' in config_text
    assert 'submit_command = ["qsub", "-terse"]' in config_text
    assert "Scheduler: sge" in result.output
    assert "Warning: qsub was not found on PATH" in result.output


def test_init_rejects_unknown_scheduler(tmp_path: Path, runner) -> None:
    project_dir = tmp_path / "bad-scheduler-project"

    result = runner.invoke(app, ["init", str(project_dir), "--scheduler", "slurm"])

    assert result.exit_code == 2
    assert "Scheduler must be one of: auto, none, sge." in result.output
    assert not project_dir.exists()


def test_init_make_dirs_materializes_default_layout(tmp_path: Path, runner) -> None:
    project_dir = tmp_path / "demo-project"

    result = runner.invoke(app, ["init", str(project_dir), "--make-dirs"])

    assert result.exit_code == 0, result.output
    assert (project_dir / "bidsflow.toml").is_file()
    assert (project_dir / "sourcedata").is_dir()
    assert (project_dir / "sourcedata" / "raw").is_dir()
    assert (project_dir / "derivatives").is_dir()
    assert (project_dir / "work").is_dir()
    assert (project_dir / "logs").is_dir()
    assert (project_dir / "state").is_dir()


def test_init_defaults_to_current_directory(tmp_path: Path, monkeypatch, runner) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "bidsflow.toml").is_file()
    config_text = (tmp_path / "bidsflow.toml").read_text(encoding="utf-8")
    assert f'name = "{tmp_path.resolve().name}"' in config_text


def test_init_respects_custom_name(tmp_path: Path, runner) -> None:
    project_dir = tmp_path / "custom-project"

    result = runner.invoke(
        app,
        [
            "init",
            str(project_dir),
            "--name",
            "TJNU camp project",
        ],
    )

    assert result.exit_code == 0, result.output
    config_path = project_dir / "bidsflow.toml"
    assert config_path.is_file()
    config_text = config_path.read_text(encoding="utf-8")
    assert 'name = "TJNU camp project"' in config_text


def test_init_does_not_reprocess_name_as_template_placeholder(tmp_path: Path, runner) -> None:
    project_dir = tmp_path / "placeholder-name-project"
    project_name = "Project __EXECUTION_SECTION__ marker"

    result = runner.invoke(
        app,
        [
            "init",
            str(project_dir),
            "--name",
            project_name,
        ],
    )

    assert result.exit_code == 0, result.output
    config_text = (project_dir / "bidsflow.toml").read_text(encoding="utf-8")
    assert tomllib.loads(config_text)["project"]["name"] == project_name


def test_init_escapes_control_characters_in_custom_name(tmp_path: Path, runner) -> None:
    project_dir = tmp_path / "control-name-project"

    result = runner.invoke(
        app,
        [
            "init",
            str(project_dir),
            "--name",
            "Line\nTab\tCarriage\rBackspace\bFormfeed\fNull\x00",
        ],
    )

    assert result.exit_code == 0, result.output
    config_text = (project_dir / "bidsflow.toml").read_text(encoding="utf-8")
    expected_name = "Line\nTab\tCarriage\rBackspace\bFormfeed\fNull\x00"
    assert 'name = "Line\\nTab\\tCarriage\\rBackspace\\bFormfeed\\fNull\\u0000"' in config_text
    assert tomllib.loads(config_text)["project"]["name"] == expected_name


def test_init_requires_force_to_overwrite_existing_config(tmp_path: Path, runner) -> None:
    project_dir = tmp_path / "existing-project"
    project_dir.mkdir()
    config_path = project_dir / "bidsflow.toml"
    config_path.write_text("existing = true\n", encoding="utf-8")

    blocked = runner.invoke(app, ["init", str(project_dir)])
    assert blocked.exit_code == 2
    assert "Refusing to overwrite existing config" in blocked.output
    assert config_path.read_text(encoding="utf-8") == "existing = true\n"

    allowed = runner.invoke(app, ["init", str(project_dir), "-f", "--name", "Replacement"])
    assert allowed.exit_code == 0, allowed.output
    assert 'name = "Replacement"' in config_path.read_text(encoding="utf-8")
