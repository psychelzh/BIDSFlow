from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import sys

from typer.testing import CliRunner

from bidsflow.cli import app

runner = CliRunner()


def _invoke_from(project_dir: Path, args: list[str]):
    previous_cwd = Path.cwd()
    try:
        os.chdir(project_dir)
        return runner.invoke(app, args)
    finally:
        os.chdir(previous_cwd)


def _read_sources_rows(sources_path: Path) -> list[dict[str, str]]:
    with sources_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return list(reader)


def _append_sources_config(config_path: Path, lines: list[str]) -> None:
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + "\n" + "\n".join(lines) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def test_sources_dry_run_shows_blank_label_behavior(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    source_root = project_dir / "sourcedata"
    (source_root / "SUB001_SES01").mkdir(parents=True)
    (source_root / "SUB001_SES02").mkdir(parents=True)

    result = _invoke_from(project_dir, ["sources", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Planned BIDSFlow sources scan." in result.output
    assert "Entries discovered: 2" in result.output
    assert "Label generation: no template or command configured" in result.output
    assert "Links: not created by sources" in result.output
    assert str(project_dir / "state" / "sources.tsv") in result.output
    assert str(project_dir / "state" / "sources.json") in result.output


def test_sources_writes_blank_review_table_by_default(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    source_root = project_dir / "sourcedata"
    (source_root / "SUB001_SES01").mkdir(parents=True)
    (source_root / "SUB002_SES03").mkdir(parents=True)

    result = _invoke_from(project_dir, ["sources"])

    assert result.exit_code == 0, result.output
    assert "Wrote BIDSFlow sources table." in result.output
    assert "- total: 2" in result.output
    assert "- needs_review: 2" in result.output
    assert "Review needed:" in result.output

    sources_path = project_dir / "state" / "sources.tsv"
    sources_state_path = project_dir / "state" / "sources.json"

    assert sources_path.is_file()
    assert sources_state_path.is_file()

    rows = _read_sources_rows(sources_path)
    assert [row["source_name"] for row in rows] == ["SUB001_SES01", "SUB002_SES03"]
    assert all(row["subject_label"] == "" for row in rows)
    assert all(row["session_label"] == "" for row in rows)
    assert all(row["include"] == "true" for row in rows)
    assert all(row["status"] == "needs_review" for row in rows)

    state = json.loads(sources_state_path.read_text(encoding="utf-8"))
    assert state["step"] == "sources"
    assert state["status"] == "succeeded"
    assert state["source_root"] == str(source_root.resolve())
    assert state["artifacts"]["sources"] == str(sources_path)
    assert state["handoff"]["role"] == "truth_source"
    assert state["handoff"]["derived_execution_views"]["links"]["managed_by"] == "heudiconv"
    assert state["handoff"]["derived_execution_views"]["links"]["lifecycle"] == "ephemeral"
    assert state["entry_count"] == 2
    assert state["status_summary"]["needs_review"] == 2
    assert "entries" not in state


def test_sources_applies_configured_template(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    config_path = project_dir / "bidsflow.toml"
    _append_sources_config(
        config_path,
        [
            "[sources]",
            'template = "CAMP_SUB{subject}_VISIT{session}"',
        ],
    )

    source_root = project_dir / "sourcedata"
    (source_root / "CAMP_SUB041_VISIT01").mkdir(parents=True)
    (source_root / "CAMP_SUB041_VISIT02").mkdir(parents=True)

    result = _invoke_from(project_dir, ["sources"])

    assert result.exit_code == 0, result.output
    assert "- ready: 2" in result.output
    assert "Review needed:" not in result.output

    rows = _read_sources_rows(project_dir / "state" / "sources.tsv")
    assert [row["subject_label"] for row in rows] == ["041", "041"]
    assert [row["session_label"] for row in rows] == ["01", "02"]
    assert all(row["status"] == "ready" for row in rows)

    state = json.loads((project_dir / "state" / "sources.json").read_text(encoding="utf-8"))
    assert state["label_generation"]["template"] == "CAMP_SUB{subject}_VISIT{session}"
    assert state["label_generation"]["command"] is None


def test_sources_applies_configured_command(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    command_script = project_dir / "code" / "bidsflow" / "derive_labels.py"
    command_script.parent.mkdir(parents=True, exist_ok=True)
    command_script.write_text(
        "\n".join(
            (
                "import re",
                "import sys",
                "",
                "source_name = sys.argv[-1]",
                "match = re.search(r'SUB(\\d+).*?(?:VISIT|SES)(\\d+)', source_name)",
                "if not match:",
                "    raise SystemExit('cannot parse labels')",
                "print(match.group(1))",
                "print(match.group(2))",
            )
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    config_path = project_dir / "bidsflow.toml"
    python_executable = sys.executable.replace("\\", "\\\\")
    _append_sources_config(
        config_path,
        [
            "[sources]",
            f'command = ["{python_executable}", "code/bidsflow/derive_labels.py"]',
        ],
    )

    source_root = project_dir / "sourcedata"
    (source_root / "SITE_SUB001_VISIT01").mkdir(parents=True)
    (source_root / "SITE_SUB001_VISIT02").mkdir(parents=True)

    result = _invoke_from(project_dir, ["sources"])

    assert result.exit_code == 0, result.output
    assert "Label generation used command:" in result.output
    assert (
        "Command contract: source_name is passed as the last argv item; cwd is project_root; "
        "stdout line 1 is subject_label; stdout line 2 is optional session_label."
        in result.output
    )

    rows = _read_sources_rows(project_dir / "state" / "sources.tsv")
    assert [row["subject_label"] for row in rows] == ["001", "001"]
    assert [row["session_label"] for row in rows] == ["01", "02"]
    assert all(row["status"] == "ready" for row in rows)


def test_sources_reports_collisions(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    config_path = project_dir / "bidsflow.toml"
    command_script = project_dir / "code" / "bidsflow" / "derive_collision_labels.py"
    command_script.parent.mkdir(parents=True, exist_ok=True)
    command_script.write_text(
        "\n".join(
            (
                "print('001')",
                "print('01')",
            )
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    python_executable = sys.executable.replace("\\", "\\\\")
    _append_sources_config(
        config_path,
        [
            "[sources]",
            f'command = ["{python_executable}", "code/bidsflow/derive_collision_labels.py"]',
        ],
    )

    source_root = project_dir / "sourcedata"
    (source_root / "SITEA_SUB001_VISIT01").mkdir(parents=True)
    (source_root / "SITEB_SUB001_VISIT01").mkdir(parents=True)

    result = _invoke_from(project_dir, ["sources"])

    assert result.exit_code == 0, result.output
    assert "- collision: 2" in result.output
    assert "Review needed:" in result.output

    rows = _read_sources_rows(project_dir / "state" / "sources.tsv")
    assert rows[0]["status"] == "collision"
    assert rows[1]["status"] == "collision"


def test_sources_rejects_command_with_more_than_two_lines(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    config_path = project_dir / "bidsflow.toml"
    command_script = project_dir / "code" / "bidsflow" / "derive_too_many_lines.py"
    command_script.parent.mkdir(parents=True, exist_ok=True)
    command_script.write_text(
        "\n".join(
            (
                "print('001')",
                "print('01')",
                "print('extra')",
            )
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    python_executable = sys.executable.replace("\\", "\\\\")
    _append_sources_config(
        config_path,
        [
            "[sources]",
            f'command = ["{python_executable}", "code/bidsflow/derive_too_many_lines.py"]',
        ],
    )

    source_root = project_dir / "sourcedata"
    (source_root / "SITE_SUB001_VISIT01").mkdir(parents=True)

    result = _invoke_from(project_dir, ["sources"])

    assert result.exit_code == 2
    assert "returned more than two non-empty output lines" in result.output


def test_sources_requires_reset_before_regenerating(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    source_root = project_dir / "sourcedata"
    (source_root / "SUB001_SES01").mkdir(parents=True)

    first = _invoke_from(project_dir, ["sources"])
    assert first.exit_code == 0, first.output

    blocked = _invoke_from(project_dir, ["sources"])
    assert blocked.exit_code == 2
    assert "Existing BIDSFlow sources state was found" in blocked.output

    allowed = _invoke_from(project_dir, ["sources", "--reset"])
    assert allowed.exit_code == 0, allowed.output


def test_sources_uses_configured_source_root(tmp_path: Path) -> None:
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

    result = _invoke_from(project_dir, ["sources"])

    assert result.exit_code == 0, result.output
    state = json.loads((project_dir / "state" / "sources.json").read_text(encoding="utf-8"))
    assert state["source_root"] == str(source_root.resolve())


def test_sources_rejects_mutually_exclusive_generation_config(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    config_path = project_dir / "bidsflow.toml"
    _append_sources_config(
        config_path,
        [
            "[sources]",
            'template = "SUB{subject}"',
            'command = ["python", "code/bidsflow/derive_labels.py"]',
        ],
    )

    result = _invoke_from(project_dir, ["sources"])

    assert result.exit_code == 2
    assert "may define template or command, but not both" in result.output


