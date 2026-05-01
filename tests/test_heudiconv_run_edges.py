from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import bidsflow.heudiconv as h
import bidsflow.heudiconv.init as heudiconv_init
import bidsflow.heudiconv.run as heudiconv_run_module
import bidsflow.schedulers as scheduler_module
from helpers import (
    init_project,
    load_context,
    make_source_dirs,
    read_key_value_file,
    read_tsv_rows,
    ready_heudiconv_project,
    set_launcher,
    set_sources_pattern,
    set_submit_command,
    write_minimal_heuristic,
    write_python_script,
    write_successful_run_launcher,
)


def test_run_rejects_missing_sources_missing_heuristic_and_heuristic_directory(
    tmp_path: Path,
    invoke_from,
    runner,
) -> None:
    project_dir = init_project(tmp_path, runner, name="run-missing-inputs")

    missing_sources = invoke_from(project_dir, ["heudiconv"])
    assert missing_sources.exit_code == 2
    assert "sources table does not exist" in missing_sources.output

    set_sources_pattern(project_dir / "bidsflow.toml", "SUB{subject}")
    make_source_dirs(project_dir, "SUB001")
    init_heudiconv = invoke_from(project_dir, ["heudiconv", "init"])
    assert init_heudiconv.exit_code == 0, init_heudiconv.output

    missing_heuristic = invoke_from(project_dir, ["heudiconv"])
    assert missing_heuristic.exit_code == 2
    assert "HeuDiConv heuristic does not exist" in missing_heuristic.output

    heuristic_path = project_dir / "code" / "heudiconv" / "heuristic.py"
    heuristic_path.mkdir(parents=True)
    heuristic_directory = invoke_from(project_dir, ["heudiconv"])
    assert heuristic_directory.exit_code == 2
    assert "HeuDiConv heuristic is not a file" in heuristic_directory.output


def test_run_rejects_sources_table_with_no_ready_included_rows(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = init_project(tmp_path, runner, name="run-no-ready")
    make_source_dirs(project_dir, "SUB001")
    init_heudiconv = invoke_from(project_dir, ["heudiconv", "init"])
    assert init_heudiconv.exit_code == 0, init_heudiconv.output
    write_minimal_heuristic(project_dir)
    (project_dir / "state" / "sources.tsv").write_text(
        "\n".join(
            (
                "source_name\tsubject_label\tsession_label\tinclude\tstatus\tnotes",
                "SUB001\t001\t\tfalse\texcluded\tmanual skip",
            )
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    result = invoke_from(project_dir, ["heudiconv"])

    assert result.exit_code == 2
    assert "does not contain any included ready rows" in result.output


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("", "missing a header row"),
        ("source_name\tinclude\nSUB001\ttrue\n", "missing required columns"),
        (
            "source_name\tsubject_label\tsession_label\tinclude\tstatus\tnotes\n"
            "\t001\t\ttrue\tready\tmissing name\n",
            "missing source_name",
        ),
        (
            "source_name\tsubject_label\tsession_label\tinclude\tstatus\tnotes\n"
            "SUB001\t001\t\tmaybe\tready\tbad include\n",
            "invalid include value",
        ),
    ],
)
def test_run_rejects_malformed_sources_table(
    tmp_path: Path,
    invoke_from,
    runner,
    contents: str,
    message: str,
) -> None:
    project_dir = init_project(tmp_path, runner, name="malformed-sources")
    (project_dir / "sourcedata").mkdir()
    (project_dir / "state").mkdir()
    (project_dir / "state" / "sources.tsv").write_text(contents, encoding="utf-8", newline="\n")
    write_minimal_heuristic(project_dir)

    result = invoke_from(project_dir, ["heudiconv"])

    assert result.exit_code == 2
    assert message in result.output


def test_run_reports_missing_source_issue_from_manual_table(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = init_project(tmp_path, runner, name="missing-source-row")
    (project_dir / "sourcedata").mkdir()
    (project_dir / "state").mkdir()
    (project_dir / "state" / "sources.tsv").write_text(
        "\n".join(
            (
                "source_name\tsubject_label\tsession_label\tinclude\tstatus\tnotes",
                "SUB404\t404\t\ttrue\tready\tmissing source",
            )
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    write_minimal_heuristic(project_dir)

    result = invoke_from(project_dir, ["heudiconv"])

    assert result.exit_code == 2
    assert "missing_source: SUB404" in result.output


def test_local_run_skips_units_with_succeeded_status_or_active_claim(
    tmp_path: Path,
    invoke_from,
    runner,
) -> None:
    project_dir = ready_heudiconv_project(tmp_path, runner, invoke_from)
    config_path = project_dir / "bidsflow.toml"
    fake_launcher = project_dir / "fake_heudiconv.py"
    write_python_script(
        fake_launcher,
        (
            "from pathlib import Path",
            "import sys",
            "argv = sys.argv[1:]",
            "out_dir = Path(argv[argv.index('-o') + 1])",
            "subject = argv[argv.index('-s') + 1]",
            "target = out_dir / f'sub-{subject}'",
            "target.mkdir(parents=True, exist_ok=True)",
        ),
    )
    set_launcher(config_path, f'launcher = ["{sys.executable}", "{fake_launcher.as_posix()}"]')

    first = invoke_from(project_dir, ["heudiconv"])
    assert first.exit_code == 0, first.output
    work_entries = sorted(path.name for path in (project_dir / "work" / "heudiconv").glob("*"))
    log_entries = sorted(path.name for path in (project_dir / "logs" / "heudiconv" / "local").glob("*"))

    skipped_succeeded = invoke_from(project_dir, ["heudiconv"])
    assert skipped_succeeded.exit_code == 0, skipped_succeeded.output
    assert "No runnable `bidsflow heudiconv` units were found." in skipped_succeeded.output
    assert "Skipped units: 1" in skipped_succeeded.output
    assert sorted(path.name for path in (project_dir / "work" / "heudiconv").glob("*")) == work_entries
    assert sorted(path.name for path in (project_dir / "logs" / "heudiconv" / "local").glob("*")) == log_entries
    state = json.loads((project_dir / "state" / "heudiconv" / "run.json").read_text(encoding="utf-8"))
    assert state["record_state"] == "succeeded"

    dry_run_succeeded = invoke_from(project_dir, ["heudiconv", "--dry-run"])
    assert dry_run_succeeded.exit_code == 0, dry_run_succeeded.output
    assert "Runnable units now: 0" in dry_run_succeeded.output
    assert "- already succeeded: 1" in dry_run_succeeded.output

    status_path = next((project_dir / "state" / "heudiconv" / "units").glob("*.status"))
    status_path.unlink()
    claim_path = next((project_dir / "state" / "heudiconv" / "claims").glob("*.running"), None)
    assert claim_path is None
    (project_dir / "state" / "heudiconv" / "claims" / "sub-001_ses-01.running").touch()

    skipped_claimed = invoke_from(project_dir, ["heudiconv"])
    assert skipped_claimed.exit_code == 0, skipped_claimed.output
    assert "Skipped units: 1" in skipped_claimed.output
    assert "- active claim: 1" in skipped_claimed.output
    state = json.loads((project_dir / "state" / "heudiconv" / "run.json").read_text(encoding="utf-8"))
    assert state["record_state"] == "succeeded"

    dry_run_claimed = invoke_from(project_dir, ["heudiconv", "--dry-run"])
    assert dry_run_claimed.exit_code == 0, dry_run_claimed.output
    assert "Runnable units now: 0" in dry_run_claimed.output
    assert "- active claim: 1" in dry_run_claimed.output


def test_local_run_records_launcher_start_failure(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = ready_heudiconv_project(tmp_path, runner, invoke_from)
    set_launcher(project_dir / "bidsflow.toml", 'launcher = ["definitely-not-heudiconv"]')

    result = invoke_from(project_dir, ["heudiconv"])

    assert result.exit_code == 2
    state = json.loads((project_dir / "state" / "heudiconv" / "run.json").read_text(encoding="utf-8"))
    rows = read_tsv_rows(project_dir / "state" / "heudiconv" / "results.tsv")
    status_payload = read_key_value_file(next((project_dir / "state" / "heudiconv" / "units").glob("*.status")))
    assert state["record_state"] == "failed"
    assert rows[0]["status"] == "failed"
    assert rows[0]["exit_code"] == ""
    assert status_payload["status"] == "failed"
    assert "Failed to start HeuDiConv launcher" in status_payload["error"]


def test_sge_submit_failure_releases_claims_and_records_state(
    tmp_path: Path,
    invoke_from,
    runner,
) -> None:
    project_dir = ready_heudiconv_project(tmp_path, runner, invoke_from, scheduler="sge")
    fake_qsub = project_dir / "fake_qsub_fail.py"
    write_python_script(fake_qsub, ("import sys", "print('queue full', file=sys.stderr)", "raise SystemExit(9)"))
    set_submit_command(
        project_dir / "bidsflow.toml",
        f'submit_command = ["{sys.executable}", "{fake_qsub.as_posix()}"]',
    )

    result = invoke_from(project_dir, ["heudiconv"])

    assert result.exit_code == 2
    assert "Failed to submit HeuDiConv SGE array job" in result.output
    state = json.loads((project_dir / "state" / "heudiconv" / "run.json").read_text(encoding="utf-8"))
    assert state["backend"] == "sge"
    assert state["record_state"] == "submit_failed"
    assert list((project_dir / "state" / "heudiconv" / "claims").glob("*.running")) == []
    status_payload = read_key_value_file(next((project_dir / "state" / "heudiconv" / "units").glob("*.status")))
    assert status_payload["status"] == "submit_failed"
    assert "queue full" in status_payload["error"]


def test_sge_submit_command_not_found(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = ready_heudiconv_project(tmp_path, runner, invoke_from, scheduler="sge")
    set_submit_command(project_dir / "bidsflow.toml", 'submit_command = ["definitely-not-qsub"]')

    result = invoke_from(project_dir, ["heudiconv"])

    assert result.exit_code == 2
    assert "Failed to start SGE submit command" in result.output


@pytest.mark.parametrize("scheduler", ["none", "sge"])
def test_run_releases_claim_when_claim_acquisition_fails(
    tmp_path: Path,
    invoke_from,
    runner,
    monkeypatch,
    scheduler: str,
) -> None:
    project_dir = ready_heudiconv_project(tmp_path, runner, invoke_from, scheduler=scheduler)
    context = load_context(project_dir)
    plan = h.plan_heudiconv_run(context)
    original_write_claim = heudiconv_run_module._write_unit_claim

    def _write_then_fail(*, unit: h.RunUnitPlan) -> None:
        original_write_claim(unit=unit)
        raise OSError("claim write failed")

    monkeypatch.setattr(heudiconv_run_module, "_write_unit_claim", _write_then_fail)

    with pytest.raises(h.HeudiconvRunError, match="Failed during HeuDiConv"):
        h.run_heudiconv(context, plan)

    assert list((project_dir / "state" / "heudiconv" / "claims").glob("*.running")) == []


def test_run_state_uses_frozen_plan_input_signature(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = ready_heudiconv_project(tmp_path, runner, invoke_from)
    fake_launcher = write_successful_run_launcher(project_dir)
    set_launcher(
        project_dir / "bidsflow.toml",
        f'launcher = ["{sys.executable}", "{fake_launcher.as_posix()}"]',
    )
    context = load_context(project_dir)
    plan = h.plan_heudiconv_run(context)
    frozen_signature = plan.input_signature

    heuristic_path = project_dir / "code" / "heudiconv" / "heuristic.py"
    heuristic_path.write_text(
        "def infotodict(seqinfo):\n    return {'changed': []}\n",
        encoding="utf-8",
        newline="\n",
    )

    h.run_heudiconv(context, plan)

    state = json.loads((project_dir / "state" / "heudiconv" / "run.json").read_text(encoding="utf-8"))
    assert state["input_signature"] == frozen_signature
    assert state["input_signature"] != heudiconv_run_module._build_run_input_signature(
        launcher=plan.launcher,
        heuristic_path=plan.heuristic_path,
        raw_bids_root=plan.raw_bids_root,
        source_root=context.paths.source_root,
        ready_entries=tuple(entry for entry in plan.entries if entry.include and entry.status == "ready"),
    )


def test_sge_rejects_missing_and_unsupported_scheduler_script_template(
    tmp_path: Path,
    invoke_from,
    runner,
) -> None:
    project_dir = ready_heudiconv_project(tmp_path, runner, invoke_from, scheduler="sge")
    script_path = project_dir / "code" / "bidsflow" / "sge" / "heudiconv.sh"
    script_path.unlink()

    missing = invoke_from(project_dir, ["heudiconv"])
    assert missing.exit_code == 2
    assert "SGE scheduler script does not exist" in missing.output

    script_path.write_text("{{ unsupported }}\n", encoding="utf-8", newline="\n")
    unsupported = invoke_from(project_dir, ["heudiconv"])
    assert unsupported.exit_code == 2
    assert "unsupported placeholders" in unsupported.output

    script_path.write_text(
        "unit_list_path={{ shell_unit_list_path }}\n",
        encoding="utf-8",
        newline="\n",
    )
    outdated = invoke_from(project_dir, ["heudiconv"])
    assert outdated.exit_code == 2
    assert "older BIDSFlow version" in outdated.output


@pytest.mark.parametrize("scheduler", ["none", "sge"])
def test_run_rejects_execution_view_cleanup_failure(
    tmp_path: Path,
    invoke_from,
    runner,
    monkeypatch,
    scheduler: str,
) -> None:
    project_dir = ready_heudiconv_project(tmp_path, runner, invoke_from, scheduler=scheduler)
    context = load_context(project_dir)
    plan = h.plan_heudiconv_run(context)
    monkeypatch.setattr(
        heudiconv_run_module,
        "_cleanup_run_execution_view",
        lambda project_root, execution_view_root: False,
    )

    with pytest.raises(h.HeudiconvRunError, match="Failed to clear prior run execution view"):
        h.run_heudiconv(context, plan)


def test_scheduler_path_and_project_ownership_helpers(tmp_path: Path, runner) -> None:
    project_dir = init_project(tmp_path, runner, name="scheduler-path", scheduler="sge")
    context = load_context(project_dir)

    assert scheduler_module._resolve_scheduler_script_path(context, target="heudiconv") == (
        project_dir / "code" / "bidsflow" / "sge" / "heudiconv.sh"
    ).resolve()

    with pytest.raises(h.HeudiconvInitError, match="outside the project root"):
        heudiconv_init._ensure_project_owned_path(project_dir, tmp_path / "outside.sh")
