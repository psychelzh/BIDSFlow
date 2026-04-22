from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from syrupy.assertion import SnapshotAssertion

from helpers import (
    assert_rendered_array_common_script,
    assert_rendered_sge_runtime_script,
    assert_rendered_sge_wrapper,
    init_project,
    make_source_dirs,
    normalize_generated_text,
    read_key_value_file,
    read_tsv_rows,
    set_launcher,
    set_sources_pattern,
    set_submit_command,
    write_failing_run_launcher,
    write_minimal_heuristic,
    write_python_script,
    write_successful_run_launcher,
)


def test_heudiconv_run_dry_run_shows_summary_and_one_example(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = init_project(tmp_path, runner)
    set_sources_pattern(project_dir / "bidsflow.toml", "SUB{subject}_SES{session}")
    make_source_dirs(project_dir, "SUB001_SES01", "SUB001_SES02")

    sources_result = invoke_from(project_dir, ["heudiconv", "init"])
    assert sources_result.exit_code == 0, sources_result.output

    heuristic_path = write_minimal_heuristic(project_dir)

    result = invoke_from(project_dir, ["heudiconv", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Planned `bidsflow heudiconv` execution." in result.output
    assert str(project_dir / "state" / "sources.tsv") in result.output
    assert str(heuristic_path) in result.output
    assert str(project_dir / "sourcedata" / "raw") in result.output
    assert str(project_dir / "state" / "heudiconv" / "run.json") not in result.output
    assert str(project_dir / "state" / "heudiconv" / "results.tsv") not in result.output
    assert "Dry run only; no files or directories were created." in result.output
    assert "Ready units in sources.tsv: 2" in result.output
    assert "Runnable units now: 2" in result.output
    assert "Example runnable unit:" in result.output
    assert "SUB001_SES01: subject=001 session=01" in result.output
    assert "-s 001 -ss 01" in result.output
    assert "Additional runnable units omitted: 1." in result.output
    assert not (project_dir / "state" / "heudiconv" / "run.json").exists()
    assert not (project_dir / "work").exists()
    assert not (project_dir / "logs").exists()


def test_heudiconv_run_sge_dry_run_reports_scheduler_artifacts(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = init_project(tmp_path, runner, name="sge-dry-run-project", scheduler="sge")
    set_sources_pattern(project_dir / "bidsflow.toml", "SUB{subject}_SES{session}")
    make_source_dirs(project_dir, "SUB001_SES01")
    init_heudiconv = invoke_from(project_dir, ["heudiconv", "init"])
    assert init_heudiconv.exit_code == 0, init_heudiconv.output
    write_minimal_heuristic(project_dir)

    result = invoke_from(project_dir, ["heudiconv", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Backend: sge" in result.output
    assert "Scheduler template:" in result.output
    assert "Submit command:" in result.output
    assert "Scheduler artifacts are created only when the job is submitted." in result.output
    assert "Rendered scheduler script:" not in result.output
    assert "Scheduler unit list:" not in result.output
    assert "Scheduler logs:" not in result.output


def test_heudiconv_run_recomputes_sources_status_from_manual_edits(
    tmp_path: Path,
    invoke_from,
    runner,
) -> None:
    project_dir = init_project(tmp_path, runner)

    config_path = project_dir / "bidsflow.toml"
    make_source_dirs(project_dir, "SUB001")

    sources_result = invoke_from(project_dir, ["heudiconv", "init"])
    assert sources_result.exit_code == 0, sources_result.output

    sources_path = project_dir / "state" / "sources.tsv"
    sources_path.write_text(
        "\n".join(
            (
                "source_name\tsubject_label\tsession_label\tinclude\tstatus\tnotes",
                "SUB001\t001\t\ttrue\tneeds_review\tmanual label",
            )
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    write_minimal_heuristic(project_dir)

    fake_launcher = write_successful_run_launcher(project_dir)
    set_launcher(
        config_path,
        f'launcher = ["{sys.executable}", "{fake_launcher.as_posix()}"]',
    )

    result = invoke_from(project_dir, ["heudiconv"])
    assert result.exit_code == 0, result.output
    assert "Completed `bidsflow heudiconv` execution." in result.output
    assert (project_dir / "sourcedata" / "raw" / "sub-001" / "marker.txt").is_file()


def test_heudiconv_run_writes_current_state_and_unit_table(
    tmp_path: Path,
    invoke_from,
    runner,
) -> None:
    project_dir = init_project(tmp_path, runner)
    config_path = project_dir / "bidsflow.toml"
    set_sources_pattern(config_path, "SUB{subject}_SES{session}")
    make_source_dirs(project_dir, "SUB001_SES01", "SUB001_SES02")
    sources_result = invoke_from(project_dir, ["heudiconv", "init"])
    assert sources_result.exit_code == 0, sources_result.output

    write_minimal_heuristic(project_dir)

    fake_launcher = write_successful_run_launcher(project_dir)
    set_launcher(
        config_path,
        f'launcher = ["{sys.executable}", "{fake_launcher.as_posix()}"]',
    )

    result = invoke_from(project_dir, ["heudiconv"])
    assert result.exit_code == 0, result.output

    raw_root = project_dir / "sourcedata" / "raw"
    assert (raw_root / "sub-001" / "ses-01" / "marker.txt").is_file()
    assert (raw_root / "sub-001" / "ses-02" / "marker.txt").is_file()

    state_path = project_dir / "state" / "heudiconv" / "run.json"
    results_path = project_dir / "state" / "heudiconv" / "results.tsv"
    assert state_path.is_file()
    assert results_path.is_file()

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["workflow"] == "heudiconv"
    assert state["step"] == "run"
    assert state["backend"] == "local"
    assert state["record_state"] == "succeeded"
    assert state["execution"]["mode"] == "local"
    assert state["execution"]["finished_at"] is not None
    assert state["input_signature"].startswith("sha256:")
    assert state["created_at"] is not None
    assert state["artifacts"]["raw_bids_dataset"] == str(raw_root)
    assert state["artifacts"]["results_table"] == str(results_path)
    assert state["cleanup_workdir"] is True
    assert Path(state["artifacts"]["log_dir"]).is_dir()
    assert Path(state["artifacts"]["unit_status_dir"]).is_dir()
    assert Path(state["artifacts"]["claim_dir"]).is_dir()
    assert state["execution_view_cleaned"] is True
    assert not Path(state["execution_view_root"]).exists()
    assert "error" not in state

    rows = read_tsv_rows(results_path)
    assert [row["source_name"] for row in rows] == ["SUB001_SES01", "SUB001_SES02"]
    assert [row["status"] for row in rows] == ["succeeded", "succeeded"]
    assert rows[0]["summary"] == "sub-001 ses-01"
    assert rows[1]["summary"] == "sub-001 ses-02"
    assert all(Path(row["log_path"]).is_file() for row in rows)
    assert all(Path(row["log_path"]).parent == Path(state["artifacts"]["log_dir"]) for row in rows)
    assert [row["exit_code"] for row in rows] == ["0", "0"]

    status_files = sorted(Path(state["artifacts"]["unit_status_dir"]).glob("*.status"))
    assert [path.name for path in status_files] == ["sub-001_ses-01.status", "sub-001_ses-02.status"]
    status_payloads = [read_key_value_file(path) for path in status_files]
    assert [payload["status"] for payload in status_payloads] == ["succeeded", "succeeded"]
    assert [payload["exit_code"] for payload in status_payloads] == ["0", "0"]
    assert list(Path(state["artifacts"]["claim_dir"]).glob("*.running")) == []

    unit_log_text = Path(rows[0]["log_path"]).read_text(encoding="utf-8")
    assert "run ok" in unit_log_text
    assert "--files" in unit_log_text


def test_heudiconv_run_can_keep_temporary_execution_view(
    tmp_path: Path,
    invoke_from,
    runner,
) -> None:
    project_dir = init_project(tmp_path, runner, name="keep-workdir-project")
    config_path = project_dir / "bidsflow.toml"
    set_sources_pattern(config_path, "SUB{subject}")
    make_source_dirs(project_dir, "SUB001")
    sources_result = invoke_from(project_dir, ["heudiconv", "init"])
    assert sources_result.exit_code == 0, sources_result.output
    write_minimal_heuristic(project_dir)

    fake_launcher = write_successful_run_launcher(project_dir)
    set_launcher(
        config_path,
        f'launcher = ["{sys.executable}", "{fake_launcher.as_posix()}"]',
    )

    result = invoke_from(project_dir, ["heudiconv", "--keep-workdir"])

    assert result.exit_code == 0, result.output
    assert "Execution view kept:" in result.output

    state = json.loads((project_dir / "state" / "heudiconv" / "run.json").read_text(encoding="utf-8"))
    assert state["cleanup_workdir"] is False
    assert "execution_view_cleaned" not in state
    assert Path(state["execution_view_root"]).exists()


def test_heudiconv_run_reports_partially_skipped_units(
    tmp_path: Path,
    invoke_from,
    runner,
) -> None:
    project_dir = init_project(tmp_path, runner, name="partial-skip-project")
    config_path = project_dir / "bidsflow.toml"
    set_sources_pattern(config_path, "SUB{subject}_SES{session}")
    make_source_dirs(project_dir, "SUB001_SES01", "SUB001_SES02")
    sources_result = invoke_from(project_dir, ["heudiconv", "init"])
    assert sources_result.exit_code == 0, sources_result.output
    write_minimal_heuristic(project_dir)

    fake_launcher = write_successful_run_launcher(project_dir)
    set_launcher(
        config_path,
        f'launcher = ["{sys.executable}", "{fake_launcher.as_posix()}"]',
    )

    unit_status_dir = project_dir / "state" / "heudiconv" / "units"
    unit_status_dir.mkdir(parents=True)
    (unit_status_dir / "sub-001_ses-01.status").write_text(
        "status=succeeded\n",
        encoding="utf-8",
        newline="\n",
    )

    result = invoke_from(project_dir, ["heudiconv"])

    assert result.exit_code == 0, result.output
    assert "Completed `bidsflow heudiconv` execution." in result.output
    assert "Run units: 1" in result.output
    assert "Skipped units: 1" in result.output


def test_heudiconv_run_reports_failed_skips_when_other_units_run(
    tmp_path: Path,
    invoke_from,
    runner,
) -> None:
    project_dir = init_project(tmp_path, runner, name="failed-skip-with-run")
    config_path = project_dir / "bidsflow.toml"
    set_sources_pattern(config_path, "SUB{subject}")
    make_source_dirs(project_dir, "SUB001", "SUB002")
    init_heudiconv = invoke_from(project_dir, ["heudiconv", "init"])
    assert init_heudiconv.exit_code == 0, init_heudiconv.output
    write_minimal_heuristic(project_dir)

    fake_launcher = write_successful_run_launcher(project_dir)
    set_launcher(
        config_path,
        f'launcher = ["{sys.executable}", "{fake_launcher.as_posix()}"]',
    )

    unit_status_dir = project_dir / "state" / "heudiconv" / "units"
    unit_status_dir.mkdir(parents=True)
    (unit_status_dir / "sub-001.status").write_text(
        "status=failed\n",
        encoding="utf-8",
        newline="\n",
    )

    result = invoke_from(project_dir, ["heudiconv"])

    assert result.exit_code == 0, result.output
    assert "Completed `bidsflow heudiconv` execution." in result.output
    assert "Run units: 1" in result.output
    assert "Skipped units: 1" in result.output
    assert "- failed; use --include-failed to retry: 1" in result.output


def test_heudiconv_run_with_sge_generates_array_artifacts_and_submits(
    tmp_path: Path,
    invoke_from,
    runner,
    snapshot: SnapshotAssertion,
) -> None:
    project_dir = init_project(tmp_path, runner, name="sge-project", scheduler="sge")

    config_path = project_dir / "bidsflow.toml"
    set_sources_pattern(config_path, "SUB{subject}_SES{session}")

    fake_qsub = project_dir / "fake_qsub.py"
    write_python_script(
        fake_qsub,
        (
            "from pathlib import Path",
            "import sys",
            "",
            "script = Path(sys.argv[-1])",
            "assert script.is_file()",
            "Path('qsub-args.txt').write_text('\\n'.join(sys.argv[1:]), encoding='utf-8')",
            "print('12345')",
        ),
    )
    set_submit_command(
        config_path,
        f'submit_command = ["{sys.executable}", "{fake_qsub.as_posix()}"]',
    )

    make_source_dirs(project_dir, "SUB001_SES01", "SUB001_SES02")
    sources_result = invoke_from(project_dir, ["heudiconv", "init"])
    assert sources_result.exit_code == 0, sources_result.output
    write_minimal_heuristic(project_dir)
    fake_launcher = write_successful_run_launcher(project_dir)
    set_launcher(
        config_path,
        f'launcher = ["{sys.executable}", "{fake_launcher.as_posix()}"]',
    )

    result = invoke_from(project_dir, ["heudiconv"])
    assert result.exit_code == 0, result.output
    assert "Submitted `bidsflow heudiconv` execution." in result.output
    assert "Scheduler job id: 12345" in result.output
    assert "Scheduler unit list:" in result.output
    assert "Unit status files:" in result.output
    assert "Unit claims:" in result.output

    state_path = project_dir / "state" / "heudiconv" / "run.json"
    results_path = project_dir / "state" / "heudiconv" / "results.tsv"
    state = json.loads(state_path.read_text(encoding="utf-8"))

    assert state["backend"] == "sge"
    assert state["record_state"] == "submitted"
    assert state["cleanup_workdir"] is True
    assert state["execution"]["mode"] == "scheduler"
    assert state["execution"]["scheduler"] == "sge"
    assert state["execution"]["submit_state"] == "submitted"
    assert state["execution"]["job_id"] == "12345"
    assert state["execution"]["array_range"] == "1-2"
    assert "execution_view_cleaned" not in state
    assert state["planned_units"]["selected"] == 2
    assert state["planned_units"]["skipped"] == 0

    scheduler_script = Path(state["artifacts"]["scheduler_script"])
    common_runtime_script = Path(state["artifacts"]["scheduler_common_runtime_script"])
    runtime_script = Path(state["artifacts"]["scheduler_runtime_script"])
    unit_list_path = Path(state["artifacts"]["scheduler_unit_list"])
    scheduler_log_dir = Path(state["artifacts"]["scheduler_log_dir"])
    claim_dir = Path(state["artifacts"]["claim_dir"])
    unit_status_dir = Path(state["artifacts"]["unit_status_dir"])

    assert scheduler_script.is_file()
    assert common_runtime_script.is_file()
    assert runtime_script.is_file()
    assert unit_list_path.is_file()
    assert scheduler_log_dir.is_dir()
    assert claim_dir.is_dir()
    assert unit_status_dir.is_dir()
    assert scheduler_log_dir.is_relative_to(project_dir / "logs")
    assert not (scheduler_script.parent / "tasks.tsv").exists()
    assert not (scheduler_script.parent / "commands").exists()

    script_text = scheduler_script.read_text(encoding="utf-8")
    normalized_script = normalize_generated_text(
        script_text,
        replacements={str(project_dir): "<PROJECT>", sys.executable: "<PYTHON>"},
        drop_blank_lines=True,
    )
    assert normalized_script == snapshot(name="heudiconv_sge_rendered_wrapper")
    assert_rendered_sge_wrapper(
        script_text,
        task_count=2,
        scheduler_log_dir=scheduler_log_dir,
        runtime_script_path=runtime_script,
    )

    runtime_text = runtime_script.read_text(encoding="utf-8")
    normalized_runtime = normalize_generated_text(
        runtime_text,
        replacements={str(project_dir): "<PROJECT>", sys.executable: "<PYTHON>"},
        drop_blank_lines=True,
    )
    assert normalized_runtime == snapshot(name="heudiconv_sge_runtime_script")
    assert_rendered_sge_runtime_script(
        runtime_text,
        common_runtime_script_path=common_runtime_script,
    )

    common_runtime_text = common_runtime_script.read_text(encoding="utf-8")
    normalized_common_runtime = normalize_generated_text(
        common_runtime_text,
        replacements={str(project_dir): "<PROJECT>", sys.executable: "<PYTHON>"},
        drop_blank_lines=True,
    )
    assert normalized_common_runtime == snapshot(name="heudiconv_sge_common_runtime_script")
    assert_rendered_array_common_script(
        common_runtime_text,
        unit_list_path=unit_list_path,
        results_path=results_path,
        cleanup_workdir=True,
    )

    unit_rows = read_tsv_rows(unit_list_path)
    assert [row["unit_name"] for row in unit_rows] == ["sub-001_ses-01", "sub-001_ses-02"]
    assert [row["source_name"] for row in unit_rows] == ["SUB001_SES01", "SUB001_SES02"]
    assert [row["subject_label"] for row in unit_rows] == ["001", "001"]
    assert [row["session_label"] for row in unit_rows] == ["01", "02"]
    assert all(Path(row["execution_path"]).exists() for row in unit_rows)
    assert all(Path(row["claim_path"]).parent == claim_dir for row in unit_rows)
    assert all(Path(row["unit_status_path"]).parent == unit_status_dir for row in unit_rows)
    assert all(Path(row["claim_path"]).suffix == ".running" for row in unit_rows)
    assert all(Path(row["unit_status_path"]).suffix == ".status" for row in unit_rows)

    assert all(Path(row["claim_path"]).is_file() for row in unit_rows)
    assert all(Path(row["claim_path"]).read_text(encoding="utf-8") == "" for row in unit_rows)
    status_payloads = [read_key_value_file(Path(row["unit_status_path"])) for row in unit_rows]
    assert [payload["status"] for payload in status_payloads] == ["submitted", "submitted"]
    assert [payload["job_id"] for payload in status_payloads] == ["", ""]
    assert [payload["task_id"] for payload in status_payloads] == ["1", "2"]
    assert [payload["log_dir"] for payload in status_payloads] == [str(scheduler_log_dir), str(scheduler_log_dir)]

    results_rows = read_tsv_rows(results_path)
    assert results_rows == []

    completed_task = subprocess.run(
        ["bash", str(scheduler_script)],
        cwd=project_dir,
        env={**os.environ, "JOB_ID": "12345", "SGE_TASK_ID": "1"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed_task.returncode == 0, completed_task.stderr
    completed_rows = read_tsv_rows(results_path)
    assert [row["unit_name"] for row in completed_rows] == ["sub-001_ses-01"]
    assert completed_rows[0]["status"] == "succeeded"
    assert completed_rows[0]["scheduler"] == "sge"
    assert completed_rows[0]["scheduler_job_id"] == "12345"
    assert completed_rows[0]["scheduler_task_id"] == "1"
    completed_status = read_key_value_file(Path(unit_rows[0]["unit_status_path"]))
    assert completed_status["status"] == "succeeded"
    assert completed_status["job_id"] == "12345"
    assert not Path(unit_rows[0]["claim_path"]).exists()

    qsub_args = (project_dir / "qsub-args.txt").read_text(encoding="utf-8")
    assert str(scheduler_script) in qsub_args

    second_result = invoke_from(project_dir, ["heudiconv"])
    assert second_result.exit_code == 0, second_result.output
    assert "No runnable `bidsflow heudiconv` units were found." in second_result.output
    assert "Skipped units: 2" in second_result.output
    second_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert second_state["record_state"] == "submitted"
    assert second_state["execution"]["job_id"] == "12345"


def test_sge_array_task_records_command_failure(
    tmp_path: Path,
    invoke_from,
    runner,
) -> None:
    project_dir = init_project(tmp_path, runner, name="sge-command-failure", scheduler="sge")
    config_path = project_dir / "bidsflow.toml"
    set_sources_pattern(config_path, "SUB{subject}_SES{session}")

    fake_qsub = project_dir / "fake_qsub.py"
    write_python_script(fake_qsub, ("print('12345')",))
    set_submit_command(
        config_path,
        f'submit_command = ["{sys.executable}", "{fake_qsub.as_posix()}"]',
    )

    make_source_dirs(project_dir, "SUB001_SES01")
    init_heudiconv = invoke_from(project_dir, ["heudiconv", "init"])
    assert init_heudiconv.exit_code == 0, init_heudiconv.output
    write_minimal_heuristic(project_dir)
    failing_launcher = write_failing_run_launcher(project_dir)
    set_launcher(
        config_path,
        f'launcher = ["{sys.executable}", "{failing_launcher.as_posix()}"]',
    )

    submitted = invoke_from(project_dir, ["heudiconv"])
    assert submitted.exit_code == 0, submitted.output

    state = json.loads((project_dir / "state" / "heudiconv" / "run.json").read_text(encoding="utf-8"))
    scheduler_script = Path(state["artifacts"]["scheduler_script"])
    unit_status_dir = Path(state["artifacts"]["unit_status_dir"])
    claim_dir = Path(state["artifacts"]["claim_dir"])

    completed_task = subprocess.run(
        ["bash", str(scheduler_script)],
        cwd=project_dir,
        env={**os.environ, "JOB_ID": "12345", "SGE_TASK_ID": "1"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed_task.returncode == 42
    assert "Command failed with exit status 42" in completed_task.stdout + completed_task.stderr

    status_payload = read_key_value_file(next(unit_status_dir.glob("*.status")))
    assert status_payload["status"] == "failed"
    assert status_payload["exit_code"] == "42"
    assert status_payload["error"] == "Command failed with exit status 42"
    assert list(claim_dir.glob("*.running")) == []

    rows = read_tsv_rows(project_dir / "state" / "heudiconv" / "results.tsv")
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert rows[0]["exit_code"] == "42"
    assert rows[0]["scheduler"] == "sge"
    assert rows[0]["scheduler_job_id"] == "12345"
    assert rows[0]["scheduler_task_id"] == "1"
    assert rows[0]["notes"] == "Command failed with exit status 42"


def test_heudiconv_run_rejects_sources_that_still_needs_review(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = init_project(tmp_path, runner)
    make_source_dirs(project_dir, "SUB001_SES01")
    sources_result = invoke_from(project_dir, ["heudiconv", "init"])
    assert sources_result.exit_code == 0, sources_result.output

    write_minimal_heuristic(project_dir)

    result = invoke_from(project_dir, ["heudiconv"])

    assert result.exit_code == 2
    assert "sources table still needs review" in result.output.lower()


def test_heudiconv_run_overwrites_current_state_and_keeps_unit_logs(
    tmp_path: Path,
    invoke_from,
    runner,
) -> None:
    project_dir = init_project(tmp_path, runner)
    config_path = project_dir / "bidsflow.toml"
    set_sources_pattern(config_path, "SUB{subject}_SES{session}")
    make_source_dirs(project_dir, "SUB001_SES01")
    sources_result = invoke_from(project_dir, ["heudiconv", "init"])
    assert sources_result.exit_code == 0, sources_result.output
    write_minimal_heuristic(project_dir)

    flaky_launcher = project_dir / "flaky_heudiconv.py"
    write_python_script(
        flaky_launcher,
        (
            "from pathlib import Path",
            "import sys",
            "",
            "argv = sys.argv[1:]",
            "flag = Path(__file__).with_name('flaky_once.flag')",
            "if not flag.exists():",
            "    flag.write_text('1', encoding='utf-8')",
            "    raise SystemExit(1)",
            "files_path = Path(argv[argv.index('--files') + 1])",
            "out_dir = Path(argv[argv.index('-o') + 1])",
            "subject = argv[argv.index('-s') + 1]",
            "assert files_path.exists()",
            "target = out_dir / f'sub-{subject}'",
            "target.mkdir(parents=True, exist_ok=True)",
            "(target / 'marker.txt').write_text(str(files_path), encoding='utf-8')",
        ),
    )
    set_launcher(
        config_path,
        f'launcher = ["{sys.executable}", "{flaky_launcher.as_posix()}"]',
    )

    first_result = invoke_from(project_dir, ["heudiconv"])
    assert first_result.exit_code == 2
    state_path = project_dir / "state" / "heudiconv" / "run.json"
    results_path = project_dir / "state" / "heudiconv" / "results.tsv"
    first_state = json.loads(state_path.read_text(encoding="utf-8"))
    first_rows = read_tsv_rows(results_path)
    assert first_state["record_state"] == "failed"
    assert first_rows[0]["status"] == "failed"
    assert first_rows[0]["exit_code"] == "1"
    first_log_dir = Path(first_state["artifacts"]["log_dir"])

    skipped_failed = invoke_from(project_dir, ["heudiconv"])
    assert skipped_failed.exit_code == 0, skipped_failed.output
    assert "No runnable `bidsflow heudiconv` units were found." in skipped_failed.output
    assert "- failed; use --include-failed to retry: 1" in skipped_failed.output

    dry_run_failed = invoke_from(project_dir, ["heudiconv", "--dry-run"])
    assert dry_run_failed.exit_code == 0, dry_run_failed.output
    assert "Runnable units now: 0" in dry_run_failed.output
    assert "- failed; use --include-failed to retry: 1" in dry_run_failed.output

    dry_run_include_failed = invoke_from(project_dir, ["heudiconv", "--dry-run", "--include-failed"])
    assert dry_run_include_failed.exit_code == 0, dry_run_include_failed.output
    assert "Runnable units now: 1" in dry_run_include_failed.output
    assert "Failed units: included" in dry_run_include_failed.output

    second_result = invoke_from(project_dir, ["heudiconv", "--include-failed"])
    assert second_result.exit_code == 0, second_result.output

    second_state = json.loads(state_path.read_text(encoding="utf-8"))
    second_rows = read_tsv_rows(results_path)
    assert second_state["record_state"] == "succeeded"
    assert [row["status"] for row in second_rows] == ["failed", "succeeded"]
    assert second_rows[-1]["exit_code"] == "0"
    second_log_dir = Path(second_state["artifacts"]["log_dir"])

    assert first_log_dir.is_dir()
    assert second_log_dir.is_dir()
    assert first_log_dir != second_log_dir
    assert Path(first_rows[0]["log_path"]).is_file()
    assert Path(second_rows[-1]["log_path"]).is_file()
