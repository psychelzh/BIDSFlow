from __future__ import annotations

import json
import sys
from pathlib import Path

from bidsflow.cli import app
from helpers import (
    append_config,
    assert_lines_in_order,
    init_project,
    make_source_dirs,
    read_tsv_rows,
    replace_config,
    set_sources_pattern,
    write_python_script,
)


def test_sources_writes_blank_review_table_by_default(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = init_project(tmp_path, runner)
    source_root = make_source_dirs(project_dir, "SUB001_SES01", "SUB002_SES03")

    result = invoke_from(project_dir, ["heudiconv", "init"])

    assert result.exit_code == 0, result.output
    assert "Initialized HeuDiConv support files." in result.output
    assert "Sources discovered: 2" in result.output
    assert "Label generation: manual review" in result.output
    assert "Sources table:" in result.output
    assert str(project_dir / "state" / "sources.tsv") in result.output
    assert str(project_dir / "state" / "sources.json") in result.output
    assert "(created)" in result.output
    assert "- total: 2" in result.output
    assert "- needs_review: 2" in result.output
    assert "Review needed:" in result.output

    sources_path = project_dir / "state" / "sources.tsv"
    sources_state_path = project_dir / "state" / "sources.json"

    assert sources_path.is_file()
    assert sources_state_path.is_file()

    rows = read_tsv_rows(sources_path)
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


def test_sources_applies_configured_pattern(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = init_project(tmp_path, runner)
    set_sources_pattern(project_dir / "bidsflow.toml", "CAMP_SUB{subject}_VISIT{session}")
    make_source_dirs(project_dir, "CAMP_SUB041_VISIT01", "CAMP_SUB041_VISIT02")

    result = invoke_from(project_dir, ["heudiconv", "init"])

    assert result.exit_code == 0, result.output
    assert "- ready: 2" in result.output
    assert "Review needed:" not in result.output

    rows = read_tsv_rows(project_dir / "state" / "sources.tsv")
    assert [row["subject_label"] for row in rows] == ["041", "041"]
    assert [row["session_label"] for row in rows] == ["01", "02"]
    assert all(row["status"] == "ready" for row in rows)

    state = json.loads((project_dir / "state" / "sources.json").read_text(encoding="utf-8"))
    assert state["label_generation"]["pattern"] == "CAMP_SUB{subject}_VISIT{session}"
    assert state["label_generation"]["command"] is None


def test_sources_ignores_raw_bids_output_child(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = tmp_path / "demo-project"
    init_result = runner.invoke(app, ["init", str(project_dir), "--make-dirs", "--scheduler", "none"])
    assert init_result.exit_code == 0, init_result.output

    set_sources_pattern(project_dir / "bidsflow.toml", "SUB{subject}_SES{session}")
    (project_dir / "sourcedata" / "SUB001_SES01").mkdir()

    result = invoke_from(project_dir, ["heudiconv", "init"])

    assert result.exit_code == 0, result.output
    assert "Sources discovered: 1" in result.output

    rows = read_tsv_rows(project_dir / "state" / "sources.tsv")
    assert [row["source_name"] for row in rows] == ["SUB001_SES01"]


def test_heudiconv_init_writes_sge_scheduler_script(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = init_project(tmp_path, runner, scheduler="sge")
    make_source_dirs(project_dir, "SUB001")

    result = invoke_from(project_dir, ["heudiconv", "init"])

    assert result.exit_code == 0, result.output
    scheduler_script = project_dir / "code" / "bidsflow" / "sge" / "heudiconv.sh"
    assert scheduler_script.is_file()
    script_text = scheduler_script.read_text(encoding="utf-8")
    assert_lines_in_order(
        script_text,
        [
            "#$ -S /bin/bash",
            "#$ -cwd",
            "#$ -N {{ job_name }}",
            "#$ -t 1-{{ task_count }}",
            "#$ -j y",
            "#$ -o {{ scheduler_log_dir }}",
        ],
    )
    assert "# #$ -q all.q" in script_text
    assert_lines_in_order(script_text, ["# #$ -q all.q", "set -uo pipefail"])
    assert "Site-specific environment setup goes here." in script_text
    assert 'export BIDSFLOW_SCHEDULER="sge"' in script_text
    assert (
        'export BIDSFLOW_TASK_ID="${SGE_TASK_ID:?This script must run as an SGE array job.}"'
        in script_text
    )
    assert "exec bash {{ runtime_script }}" in script_text
    assert "unit_list_path" not in script_text
    assert "results_table_path" not in script_text
    assert "write_unit_status" not in script_text
    assert "append_final_result" not in script_text
    assert f"Scheduler script: {scheduler_script} (created)" in result.output

    scheduler_script.write_text("custom script\n", encoding="utf-8", newline="\n")
    kept = invoke_from(project_dir, ["heudiconv", "init"])
    assert kept.exit_code == 0, kept.output
    assert f"Scheduler script: {scheduler_script} (kept)" in kept.output
    assert scheduler_script.read_text(encoding="utf-8") == "custom script\n"

    overwritten = invoke_from(project_dir, ["heudiconv", "init", "--force"])
    assert overwritten.exit_code == 0, overwritten.output
    assert f"Scheduler script: {scheduler_script} (overwritten)" in overwritten.output
    assert "#$ -t 1-{{ task_count }}" in scheduler_script.read_text(encoding="utf-8")


def test_heudiconv_init_reports_custom_heuristic_directory(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = init_project(tmp_path, runner, name="custom-heuristic-project")
    replace_config(
        project_dir / "bidsflow.toml",
        'heuristic = "code/heudiconv/heuristic.py"',
        'heuristic = "code/custom/heuristic.py"',
    )
    make_source_dirs(project_dir, "SUB001")

    result = invoke_from(project_dir, ["heudiconv", "init"])

    assert result.exit_code == 0, result.output
    assert f"Heuristic: {project_dir / 'code' / 'custom' / 'heuristic.py'}" in result.output


def test_sources_applies_configured_command(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = init_project(tmp_path, runner)

    command_script = project_dir / "code" / "heudiconv" / "derive_labels.py"
    write_python_script(
        command_script,
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
        ),
    )

    config_path = project_dir / "bidsflow.toml"
    append_config(
        config_path,
        [
            "[sources]",
            f'command = ["{sys.executable}", "code/heudiconv/derive_labels.py"]',
        ],
    )

    make_source_dirs(project_dir, "SITE_SUB001_VISIT01", "SITE_SUB001_VISIT02")

    result = invoke_from(project_dir, ["heudiconv", "init"])

    assert result.exit_code == 0, result.output
    assert "Label generation: command:" in result.output
    assert "Command contract:" not in result.output

    rows = read_tsv_rows(project_dir / "state" / "sources.tsv")
    assert [row["subject_label"] for row in rows] == ["001", "001"]
    assert [row["session_label"] for row in rows] == ["01", "02"]
    assert all(row["status"] == "ready" for row in rows)


def test_sources_reports_collisions(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = init_project(tmp_path, runner)

    config_path = project_dir / "bidsflow.toml"
    command_script = project_dir / "code" / "heudiconv" / "derive_collision_labels.py"
    write_python_script(command_script, ("print('001')", "print('01')"))

    append_config(
        config_path,
        [
            "[sources]",
            f'command = ["{sys.executable}", "code/heudiconv/derive_collision_labels.py"]',
        ],
    )

    make_source_dirs(project_dir, "SITEA_SUB001_VISIT01", "SITEB_SUB001_VISIT01")

    result = invoke_from(project_dir, ["heudiconv", "init"])

    assert result.exit_code == 0, result.output
    assert "- collision: 2" in result.output
    assert "Review needed:" in result.output

    rows = read_tsv_rows(project_dir / "state" / "sources.tsv")
    assert rows[0]["status"] == "collision"
    assert rows[1]["status"] == "collision"


def test_sources_rejects_command_with_more_than_two_lines(
    tmp_path: Path,
    invoke_from,
    runner,
) -> None:
    project_dir = init_project(tmp_path, runner)

    config_path = project_dir / "bidsflow.toml"
    command_script = project_dir / "code" / "heudiconv" / "derive_too_many_lines.py"
    write_python_script(command_script, ("print('001')", "print('01')", "print('extra')"))

    append_config(
        config_path,
        [
            "[sources]",
            f'command = ["{sys.executable}", "code/heudiconv/derive_too_many_lines.py"]',
        ],
    )

    make_source_dirs(project_dir, "SITE_SUB001_VISIT01")

    result = invoke_from(project_dir, ["heudiconv", "init"])

    assert result.exit_code == 2
    assert "returned more than two non-empty output lines" in result.output


def test_heudiconv_init_keeps_existing_sources_without_force(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = init_project(tmp_path, runner)
    source_root = make_source_dirs(project_dir, "SUB001_SES01")

    first = invoke_from(project_dir, ["heudiconv", "init"])
    assert first.exit_code == 0, first.output

    (source_root / "SUB002_SES01").mkdir(parents=True)

    kept = invoke_from(project_dir, ["heudiconv", "init"])
    assert kept.exit_code == 0, kept.output
    assert "(kept)" in kept.output
    rows = read_tsv_rows(project_dir / "state" / "sources.tsv")
    assert [row["source_name"] for row in rows] == ["SUB001_SES01"]

    allowed = invoke_from(project_dir, ["heudiconv", "init", "--force"])
    assert allowed.exit_code == 0, allowed.output
    assert "(overwritten)" in allowed.output
    rows = read_tsv_rows(project_dir / "state" / "sources.tsv")
    assert [row["source_name"] for row in rows] == ["SUB001_SES01", "SUB002_SES01"]


def test_heudiconv_init_refreshes_missing_sources_metadata_without_overwriting_table(
    tmp_path: Path,
    invoke_from,
    runner,
) -> None:
    project_dir = init_project(tmp_path, runner)
    source_root = make_source_dirs(project_dir, "SUB001_SES01")

    first = invoke_from(project_dir, ["heudiconv", "init"])
    assert first.exit_code == 0, first.output

    (project_dir / "state" / "sources.json").unlink()
    (source_root / "SUB002_SES01").mkdir(parents=True)

    refreshed = invoke_from(project_dir, ["heudiconv", "init"])

    assert refreshed.exit_code == 0, refreshed.output
    assert "(metadata refreshed)" in refreshed.output
    assert (project_dir / "state" / "sources.json").is_file()
    rows = read_tsv_rows(project_dir / "state" / "sources.tsv")
    assert [row["source_name"] for row in rows] == ["SUB001_SES01"]


def test_heudiconv_init_rejects_invalid_existing_sources_table(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = init_project(tmp_path, runner)
    make_source_dirs(project_dir, "SUB001_SES01")

    first = invoke_from(project_dir, ["heudiconv", "init"])
    assert first.exit_code == 0, first.output

    sources_path = project_dir / "state" / "sources.tsv"
    sources_path.write_text(
        "\n".join(
            (
                "source_name\tsubject_label\tsession_label\tinclude\tstatus\tnotes",
                "SUB001_SES01\t001\t01\tmaybe\tready\tbad include",
            )
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    result = invoke_from(project_dir, ["heudiconv", "init"])

    assert result.exit_code == 2
    assert "invalid include value" in result.output


def test_sources_uses_configured_source_root(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = init_project(tmp_path, runner)

    config_path = project_dir / "bidsflow.toml"
    replace_config(config_path, 'source_root = "sourcedata"', 'source_root = "incoming"')

    source_root = make_source_dirs(project_dir, "SUB010_SES01", root="incoming")

    result = invoke_from(project_dir, ["heudiconv", "init"])

    assert result.exit_code == 0, result.output
    state = json.loads((project_dir / "state" / "sources.json").read_text(encoding="utf-8"))
    assert state["source_root"] == str(source_root.resolve())


def test_sources_preserves_symlink_child_name(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = init_project(tmp_path, runner)
    source_root = project_dir / "sourcedata"
    source_root.mkdir()
    archive_target = tmp_path / "archive" / "SUB001"
    archive_target.mkdir(parents=True)
    (source_root / "siteA").symlink_to(archive_target, target_is_directory=True)

    result = invoke_from(project_dir, ["heudiconv", "init"])

    assert result.exit_code == 0, result.output
    rows = read_tsv_rows(project_dir / "state" / "sources.tsv")
    assert rows[0]["source_name"] == "siteA"


def test_sources_rejects_mutually_exclusive_generation_config(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = init_project(tmp_path, runner)

    config_path = project_dir / "bidsflow.toml"
    append_config(
        config_path,
        [
            "[sources]",
            'pattern = "SUB{subject}"',
            'command = ["python", "code/heudiconv/derive_labels.py"]',
        ],
    )

    result = invoke_from(project_dir, ["heudiconv", "init"])

    assert result.exit_code == 2
    assert "may define pattern or command, but not both" in result.output
