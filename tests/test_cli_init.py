from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from bidsflow.cli import app

runner = CliRunner()


def test_init_creates_scaffold_in_target_directory(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    result = runner.invoke(app, ["init", str(project_dir)])

    assert result.exit_code == 0, result.output
    assert (project_dir / "bidsflow.toml").is_file()
    assert (project_dir / "sourcedata").is_dir()
    assert (project_dir / "sourcedata" / "raw").is_dir()
    assert (project_dir / "derivatives").is_dir()
    assert (project_dir / "work").is_dir()
    assert (project_dir / "logs").is_dir()
    assert (project_dir / "state").is_dir()

    config_text = (project_dir / "bidsflow.toml").read_text(encoding="utf-8")
    assert "# Review before first use:" in config_text
    assert "# - adjust [project].name if you want a clearer project label" in config_text
    assert 'name = "demo-project"' in config_text
    assert 'root = "."' in config_text
    assert 'raw_bids_root = "sourcedata/raw"' in config_text


def test_init_defaults_to_current_directory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "bidsflow.toml").is_file()
    config_text = (tmp_path / "bidsflow.toml").read_text(encoding="utf-8")
    assert f'name = "{tmp_path.resolve().name}"' in config_text


def test_init_respects_custom_name_and_config_name(tmp_path: Path) -> None:
    project_dir = tmp_path / "custom-project"

    result = runner.invoke(
        app,
        [
            "init",
            str(project_dir),
            "--name",
            "TJNU camp project",
            "--config-name",
            "bidsflow.toml",
        ],
    )

    assert result.exit_code == 0, result.output
    config_path = project_dir / "bidsflow.toml"
    assert config_path.is_file()
    config_text = config_path.read_text(encoding="utf-8")
    assert 'name = "TJNU camp project"' in config_text


def test_init_requires_force_to_overwrite_existing_config(tmp_path: Path) -> None:
    project_dir = tmp_path / "existing-project"
    project_dir.mkdir()
    config_path = project_dir / "bidsflow.toml"
    config_path.write_text("existing = true\n", encoding="utf-8")

    blocked = runner.invoke(app, ["init", str(project_dir)])
    assert blocked.exit_code == 2
    assert "Refusing to overwrite existing config" in blocked.output
    assert config_path.read_text(encoding="utf-8") == "existing = true\n"

    allowed = runner.invoke(app, ["init", str(project_dir), "--force", "--name", "Replacement"])
    assert allowed.exit_code == 0, allowed.output
    assert 'name = "Replacement"' in config_path.read_text(encoding="utf-8")
