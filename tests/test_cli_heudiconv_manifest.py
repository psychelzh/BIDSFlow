from __future__ import annotations

import csv
import json
from pathlib import Path

from typer.testing import CliRunner

from bidsflow.cli import app

runner = CliRunner()


def _read_manifest_rows(manifest_path: Path) -> list[dict[str, str]]:
    with manifest_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return list(reader)


def test_heudiconv_manifest_dry_run_shows_empty_column_behavior(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    source_root = project_dir / "sourcedata"
    (source_root / "SUB001_SES01").mkdir(parents=True)
    (source_root / "SUB001_SES02").mkdir(parents=True)

    result = runner.invoke(
        app,
        [
            "heudiconv",
            "manifest",
            "--config",
            str(project_dir / "bidsflow.toml"),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Planned HeuDiConv manifest generation." in result.output
    assert "Entries discovered: 2" in result.output
    assert "subject/session columns will be left blank" in result.output
    assert "Links: not created by manifest" in result.output
    assert str(project_dir / "code" / "heudiconv" / "manifest.tsv") in result.output
    assert str(project_dir / "state" / "heudiconv" / "manifest.json") in result.output


def test_heudiconv_manifest_writes_blank_review_table_by_default(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    source_root = project_dir / "sourcedata"
    (source_root / "SUB001_SES01").mkdir(parents=True)
    (source_root / "SUB002_SES03").mkdir(parents=True)

    result = runner.invoke(
        app,
        [
            "heudiconv",
            "manifest",
            "--config",
            str(project_dir / "bidsflow.toml"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Wrote HeuDiConv manifest." in result.output
    assert "Entries: 2" in result.output

    manifest_path = project_dir / "code" / "heudiconv" / "manifest.tsv"
    manifest_state_path = project_dir / "state" / "heudiconv" / "manifest.json"

    assert manifest_path.is_file()
    assert manifest_state_path.is_file()

    rows = _read_manifest_rows(manifest_path)
    assert [row["unit_id"] for row in rows] == ["unit-0001", "unit-0002"]
    assert [row["source_name"] for row in rows] == ["SUB001_SES01", "SUB002_SES03"]
    assert all(row["subject_raw"] == "" for row in rows)
    assert all(row["session_raw"] == "" for row in rows)
    assert all(row["subject_label"] == "" for row in rows)
    assert all(row["session_label"] == "" for row in rows)
    assert all(row["include"] == "true" for row in rows)
    assert all(row["status"] == "needs_review" for row in rows)

    state = json.loads(manifest_state_path.read_text(encoding="utf-8"))
    assert state["step"] == "manifest"
    assert state["status"] == "succeeded"
    assert state["source_root"] == str(source_root.resolve())
    assert state["artifacts"]["manifest"] == str(manifest_path)
    assert state["handoff"]["role"] == "truth_source"
    assert state["handoff"]["derived_execution_views"]["links"]["managed_by"] == "convert"
    assert state["handoff"]["derived_execution_views"]["links"]["lifecycle"] == "ephemeral"
    assert state["entry_count"] == 2
    assert state["entries"][0]["subject_raw"] == ""
    assert state["entries"][1]["session_raw"] == ""


def test_heudiconv_manifest_applies_explicit_regex_extraction(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    source_root = project_dir / "sourcedata"
    (source_root / "CAMP_SUB041_VISIT01").mkdir(parents=True)
    (source_root / "CAMP_SUB041_VISIT02").mkdir(parents=True)

    result = runner.invoke(
        app,
        [
            "heudiconv",
            "manifest",
            "--config",
            str(project_dir / "bidsflow.toml"),
            "--subject-regex",
            r"SUB(\d+)",
            "--session-regex",
            r"VISIT(\d+)",
        ],
    )

    assert result.exit_code == 0, result.output

    rows = _read_manifest_rows(project_dir / "code" / "heudiconv" / "manifest.tsv")
    assert [row["subject_raw"] for row in rows] == ["041", "041"]
    assert [row["session_raw"] for row in rows] == ["01", "02"]
    assert all(row["status"] == "needs_review" for row in rows)

    state = json.loads((project_dir / "state" / "heudiconv" / "manifest.json").read_text(encoding="utf-8"))
    assert state["extraction"]["subject_regex"] == r"SUB(\d+)"
    assert state["extraction"]["session_regex"] == r"VISIT(\d+)"
    assert state["entries"][0]["subject_raw"] == "041"
    assert state["entries"][1]["session_raw"] == "02"


def test_heudiconv_manifest_requires_reset_before_regenerating(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    source_root = project_dir / "sourcedata"
    (source_root / "SUB001_SES01").mkdir(parents=True)

    first = runner.invoke(
        app,
        ["heudiconv", "manifest", "--config", str(project_dir / "bidsflow.toml")],
    )
    assert first.exit_code == 0, first.output

    blocked = runner.invoke(
        app,
        ["heudiconv", "manifest", "--config", str(project_dir / "bidsflow.toml")],
    )
    assert blocked.exit_code == 2
    assert "Existing HeuDiConv manifest state was found" in blocked.output

    allowed = runner.invoke(
        app,
        [
            "heudiconv",
            "manifest",
            "--config",
            str(project_dir / "bidsflow.toml"),
            "--reset",
        ],
    )
    assert allowed.exit_code == 0, allowed.output


def test_heudiconv_manifest_uses_configured_source_root(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    config_path = project_dir / "bidsflow.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'source_root = "sourcedata"',
            'source_root = "incoming"',
        ),
        encoding="utf-8",
        newline="\n",
    )

    source_root = project_dir / "incoming"
    (source_root / "SUB010_SES01").mkdir(parents=True)

    result = runner.invoke(
        app,
        ["heudiconv", "manifest", "--config", str(config_path)],
    )

    assert result.exit_code == 0, result.output
    state = json.loads((project_dir / "state" / "heudiconv" / "manifest.json").read_text(encoding="utf-8"))
    assert state["source_root"] == str(source_root.resolve())
