from __future__ import annotations

import sys
from pathlib import Path

import pytest

from helpers import (
    init_project,
    make_source_dirs,
    replace_config,
    set_launcher,
    set_sources_pattern,
    write_failing_run_launcher,
    write_minimal_heuristic,
)


def _ready_status_project(tmp_path: Path, invoke_from, runner, *, name: str) -> Path:
    project_dir = init_project(tmp_path, runner, name=name)
    set_sources_pattern(project_dir / "bidsflow.toml", "SUB{subject}")
    make_source_dirs(project_dir, "SUB001")
    init_heudiconv = invoke_from(project_dir, ["heudiconv", "init"])
    assert init_heudiconv.exit_code == 0, init_heudiconv.output
    return project_dir


@pytest.mark.parametrize(
    "option",
    ["--dry-run", "-n", "--include-failed", "-r", "--keep-execution-view"],
)
def test_status_rejects_run_only_options_before_subcommand(
    tmp_path: Path,
    invoke_from,
    runner,
    option: str,
) -> None:
    project_dir = init_project(tmp_path, runner, name="status-run-option-scope")

    result = invoke_from(project_dir, ["heudiconv", option, "status"])

    assert result.exit_code == 2
    assert "Run options" in result.output
    assert "not with subcommands" in result.output


def test_status_reports_uninitialized_project(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = init_project(tmp_path, runner, name="status-not-ready")

    result = invoke_from(project_dir, ["heudiconv", "status"])

    assert result.exit_code == 0, result.output
    assert "HeuDiConv status." in result.output
    assert "State: not initialized" in result.output
    assert "Heuristic: missing" in result.output
    assert "Latest run state: none" in result.output
    assert "Initialize HeuDiConv: bidsflow heudiconv init" in result.output


def test_status_rejects_invalid_project_config(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = init_project(tmp_path, runner, name="status-invalid-config")
    replace_config(project_dir / "bidsflow.toml", 'source_root = "sourcedata"', "source_root = 1")

    result = invoke_from(project_dir, ["heudiconv", "status"])

    assert result.exit_code == 2
    assert "[paths].source_root must be a string path." in result.output


def test_status_reports_malformed_sources_table(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = init_project(tmp_path, runner, name="status-malformed-sources")
    (project_dir / "state").mkdir()
    (project_dir / "state" / "sources.tsv").write_text(
        "source_name\tsubject_label\tsession_label\tinclude\tstatus\tnotes\n"
        "SUB001\t001\t\tmaybe\tready\tbad include\n",
        encoding="utf-8",
        newline="\n",
    )

    result = invoke_from(project_dir, ["heudiconv", "status"])

    assert result.exit_code == 0, result.output
    assert "Unreadable sources table:" in result.output
    assert "invalid include value" in result.output
    assert "Review sources:" in result.output


def test_status_suggests_draft_when_sources_are_ready(
    tmp_path: Path,
    invoke_from,
    runner,
) -> None:
    project_dir = _ready_status_project(
        tmp_path,
        invoke_from,
        runner,
        name="status-ready-no-heuristic",
    )

    result = invoke_from(project_dir, ["heudiconv", "status"])

    assert result.exit_code == 0, result.output
    assert "- ready: 1" in result.output
    assert "Heuristic: missing" in result.output
    assert "Draft heuristic: bidsflow heudiconv draft <sample-path>" in result.output


def test_status_reports_pending_active_and_completed_units(
    tmp_path: Path,
    invoke_from,
    runner,
) -> None:
    project_dir = _ready_status_project(tmp_path, invoke_from, runner, name="status-unit-states")
    write_minimal_heuristic(project_dir)

    pending = invoke_from(project_dir, ["heudiconv", "status"])
    assert pending.exit_code == 0, pending.output
    assert "- not_run: 1" in pending.output
    assert "Run pending units: bidsflow heudiconv" in pending.output

    claim_path = project_dir / "state" / "heudiconv" / "claims" / "sub-001.running"
    claim_path.parent.mkdir(parents=True)
    claim_path.touch()
    active = invoke_from(project_dir, ["heudiconv", "status"])
    assert active.exit_code == 0, active.output
    assert "- active: 1" in active.output
    assert "Active claims:" in active.output
    assert "Wait for active scheduler/local work to finish" in active.output

    claim_path.unlink()
    status_path = project_dir / "state" / "heudiconv" / "units" / "sub-001.status"
    status_path.parent.mkdir(parents=True)
    status_path.write_text("status=succeeded\n", encoding="utf-8", newline="\n")
    succeeded = invoke_from(project_dir, ["heudiconv", "status"])
    assert succeeded.exit_code == 0, succeeded.output
    assert "- succeeded: 1" in succeeded.output
    assert "No immediate action." in succeeded.output


def test_status_reports_failed_unit_with_retry_guidance(
    tmp_path: Path,
    invoke_from,
    runner,
) -> None:
    project_dir = _ready_status_project(tmp_path, invoke_from, runner, name="status-failed")
    config_path = project_dir / "bidsflow.toml"
    write_minimal_heuristic(project_dir)
    failing_launcher = write_failing_run_launcher(project_dir)
    set_launcher(
        config_path,
        f'launcher = ["{sys.executable}", "{failing_launcher.as_posix()}"]',
    )
    failed = invoke_from(project_dir, ["heudiconv"])
    assert failed.exit_code == 2, failed.output

    result = invoke_from(project_dir, ["heudiconv", "status"])

    assert result.exit_code == 0, result.output
    assert "Latest run state: failed" in result.output
    assert "- failed: 1" in result.output
    assert "Failed units:" in result.output
    assert "sub-001: source=SUB001" in result.output
    assert "log:" in result.output
    assert "Inspect failed unit logs and sources.tsv; retry with:" in result.output
    assert "bidsflow heudiconv --include-failed" in result.output


def test_status_prioritizes_sources_review(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = _ready_status_project(tmp_path, invoke_from, runner, name="status-review")
    sources_path = project_dir / "state" / "sources.tsv"
    sources_path.write_text(
        "\n".join(
            (
                "source_name\tsubject_label\tsession_label\tinclude\tstatus\tnotes",
                "SUB001\t\t\ttrue\tneeds_review\tdata issue",
            )
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    result = invoke_from(project_dir, ["heudiconv", "status"])

    assert result.exit_code == 0, result.output
    assert "Sources needing review:" in result.output
    assert "Review sources:" in result.output


def test_status_limits_long_failed_and_active_details(
    tmp_path: Path,
    invoke_from,
    runner,
) -> None:
    project_dir = init_project(tmp_path, runner, name="status-many-units")
    config_path = project_dir / "bidsflow.toml"
    set_sources_pattern(config_path, "SUB{subject}")
    source_names = tuple(f"SUB{index:03d}" for index in range(1, 13))
    make_source_dirs(project_dir, *source_names)
    init_heudiconv = invoke_from(project_dir, ["heudiconv", "init"])
    assert init_heudiconv.exit_code == 0, init_heudiconv.output
    write_minimal_heuristic(project_dir)

    unit_status_dir = project_dir / "state" / "heudiconv" / "units"
    claim_dir = project_dir / "state" / "heudiconv" / "claims"
    scheduler_log_dir = project_dir / "logs" / "heudiconv" / "sge" / "run-1"
    unit_status_dir.mkdir(parents=True)
    claim_dir.mkdir(parents=True)
    for index in range(1, 13):
        unit_name = f"sub-{index:03d}"
        (unit_status_dir / f"{unit_name}.status").write_text(
            "\n".join(
                (
                    "status=failed",
                    f"unit={unit_name}",
                    f"source=SUB{index:03d}",
                    "backend=sge",
                    f"log_dir={scheduler_log_dir}",
                    "error=data problem",
                )
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        (claim_dir / f"{unit_name}.running").touch()

    result = invoke_from(project_dir, ["heudiconv", "status"])

    assert result.exit_code == 0, result.output
    assert f"logs: {scheduler_log_dir}" in result.output
    assert "additional failed units omitted: 2" in result.output
    assert "additional active claims omitted: 2" in result.output
