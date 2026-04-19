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


def test_heudiconv_init_shows_blank_label_behavior(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    source_root = project_dir / "sourcedata"
    (source_root / "SUB001_SES01").mkdir(parents=True)
    (source_root / "SUB001_SES02").mkdir(parents=True)

    result = _invoke_from(project_dir, ["heudiconv", "init"])

    assert result.exit_code == 0, result.output
    assert "Initialized HeuDiConv support files." in result.output
    assert "Sources discovered: 2" in result.output
    assert "Label generation: no pattern or command configured" in result.output
    assert str(project_dir / "state" / "sources.tsv") in result.output
    assert str(project_dir / "state" / "sources.json") in result.output


def test_sources_writes_blank_review_table_by_default(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    source_root = project_dir / "sourcedata"
    (source_root / "SUB001_SES01").mkdir(parents=True)
    (source_root / "SUB002_SES03").mkdir(parents=True)

    result = _invoke_from(project_dir, ["heudiconv", "init"])

    assert result.exit_code == 0, result.output
    assert "Initialized HeuDiConv support files." in result.output
    assert "Sources table:" in result.output
    assert "(created)" in result.output
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


def test_sources_applies_configured_pattern(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    config_path = project_dir / "bidsflow.toml"
    _append_sources_config(
        config_path,
        [
            "[sources]",
            'pattern = "CAMP_SUB{subject}_VISIT{session}"',
        ],
    )

    source_root = project_dir / "sourcedata"
    (source_root / "CAMP_SUB041_VISIT01").mkdir(parents=True)
    (source_root / "CAMP_SUB041_VISIT02").mkdir(parents=True)

    result = _invoke_from(project_dir, ["heudiconv", "init"])

    assert result.exit_code == 0, result.output
    assert "- ready: 2" in result.output
    assert "Review needed:" not in result.output

    rows = _read_sources_rows(project_dir / "state" / "sources.tsv")
    assert [row["subject_label"] for row in rows] == ["041", "041"]
    assert [row["session_label"] for row in rows] == ["01", "02"]
    assert all(row["status"] == "ready" for row in rows)

    state = json.loads((project_dir / "state" / "sources.json").read_text(encoding="utf-8"))
    assert state["label_generation"]["pattern"] == "CAMP_SUB{subject}_VISIT{session}"
    assert state["label_generation"]["command"] is None


def test_heudiconv_init_writes_sge_scheduler_script(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir), "--scheduler", "sge"])
    assert init_result.exit_code == 0, init_result.output

    source_root = project_dir / "sourcedata"
    (source_root / "SUB001").mkdir(parents=True)

    result = _invoke_from(project_dir, ["heudiconv", "init"])

    assert result.exit_code == 0, result.output
    scheduler_script = project_dir / "code" / "bidsflow" / "sge" / "heudiconv.sh"
    assert scheduler_script.is_file()
    script_text = scheduler_script.read_text(encoding="utf-8")
    assert "#$ -N {{ job_name }}" in script_text
    assert "Site-specific environment setup goes here." in script_text
    assert "{{ command }}" in script_text
    assert f"Scheduler script: {scheduler_script} (created)" in result.output

    scheduler_script.write_text("custom script\n", encoding="utf-8", newline="\n")
    kept = _invoke_from(project_dir, ["heudiconv", "init"])
    assert kept.exit_code == 0, kept.output
    assert f"Scheduler script: {scheduler_script} (kept)" in kept.output
    assert scheduler_script.read_text(encoding="utf-8") == "custom script\n"

    overwritten = _invoke_from(project_dir, ["heudiconv", "init", "--force"])
    assert overwritten.exit_code == 0, overwritten.output
    assert f"Scheduler script: {scheduler_script} (overwritten)" in overwritten.output
    assert "{{ command }}" in scheduler_script.read_text(encoding="utf-8")


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

    result = _invoke_from(project_dir, ["heudiconv", "init"])

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

    result = _invoke_from(project_dir, ["heudiconv", "init"])

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

    result = _invoke_from(project_dir, ["heudiconv", "init"])

    assert result.exit_code == 2
    assert "returned more than two non-empty output lines" in result.output


def test_heudiconv_init_keeps_existing_sources_without_force(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    source_root = project_dir / "sourcedata"
    (source_root / "SUB001_SES01").mkdir(parents=True)

    first = _invoke_from(project_dir, ["heudiconv", "init"])
    assert first.exit_code == 0, first.output

    (source_root / "SUB002_SES01").mkdir(parents=True)

    kept = _invoke_from(project_dir, ["heudiconv", "init"])
    assert kept.exit_code == 0, kept.output
    assert "(kept)" in kept.output
    rows = _read_sources_rows(project_dir / "state" / "sources.tsv")
    assert [row["source_name"] for row in rows] == ["SUB001_SES01"]

    allowed = _invoke_from(project_dir, ["heudiconv", "init", "--force"])
    assert allowed.exit_code == 0, allowed.output
    assert "(overwritten)" in allowed.output
    rows = _read_sources_rows(project_dir / "state" / "sources.tsv")
    assert [row["source_name"] for row in rows] == ["SUB001_SES01", "SUB002_SES01"]


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

    result = _invoke_from(project_dir, ["heudiconv", "init"])

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
            'pattern = "SUB{subject}"',
            'command = ["python", "code/bidsflow/derive_labels.py"]',
        ],
    )

    result = _invoke_from(project_dir, ["heudiconv", "init"])

    assert result.exit_code == 2
    assert "may define pattern or command, but not both" in result.output
