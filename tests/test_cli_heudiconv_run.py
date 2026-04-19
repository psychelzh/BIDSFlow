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


def _append_config(config_path: Path, lines: list[str]) -> None:
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + "\n" + "\n".join(lines) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _set_launcher(config_path: Path, launcher_line: str) -> None:
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            '# launcher = ["heudiconv"]',
            launcher_line,
        ),
        encoding="utf-8",
        newline="\n",
    )


def _set_submit_command(config_path: Path, submit_command_line: str) -> None:
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'submit_command = ["qsub", "-terse"]',
            submit_command_line,
        ),
        encoding="utf-8",
        newline="\n",
    )


def _write_minimal_heuristic(project_dir: Path) -> Path:
    heuristic_path = project_dir / "code" / "heudiconv" / "heuristic.py"
    heuristic_path.parent.mkdir(parents=True, exist_ok=True)
    heuristic_path.write_text(
        "def infotodict(seqinfo):\n    return {}\n",
        encoding="utf-8",
        newline="\n",
    )
    return heuristic_path


def _read_tsv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _read_key_value_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def test_heudiconv_run_dry_run_shows_summary_and_one_example(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"
    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    config_path = project_dir / "bidsflow.toml"
    _append_config(
        config_path,
        [
            "[sources]",
            'pattern = "SUB{subject}_SES{session}"',
        ],
    )

    (project_dir / "sourcedata" / "SUB001_SES01").mkdir(parents=True)
    (project_dir / "sourcedata" / "SUB001_SES02").mkdir(parents=True)

    sources_result = _invoke_from(project_dir, ["heudiconv", "init"])
    assert sources_result.exit_code == 0, sources_result.output

    heuristic_path = _write_minimal_heuristic(project_dir)

    result = _invoke_from(project_dir, ["heudiconv", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Planned `bidsflow heudiconv` execution." in result.output
    assert str(project_dir / "state" / "sources.tsv") in result.output
    assert str(heuristic_path) in result.output
    assert str(project_dir / "sourcedata" / "raw") in result.output
    assert str(project_dir / "state" / "heudiconv" / "run.json") in result.output
    assert str(project_dir / "state" / "heudiconv" / "run.tsv") in result.output
    assert "Run units: 2" in result.output
    assert "Example unit:" in result.output
    assert "SUB001_SES01: subject=001 session=01" in result.output
    assert "-s 001 -ss 01" in result.output
    assert "Additional units omitted: 1." in result.output


def test_heudiconv_run_recomputes_sources_status_from_manual_edits(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"
    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    config_path = project_dir / "bidsflow.toml"
    (project_dir / "sourcedata" / "SUB001").mkdir(parents=True)

    sources_result = _invoke_from(project_dir, ["heudiconv", "init"])
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

    _write_minimal_heuristic(project_dir)

    fake_launcher = project_dir / "fake_heudiconv.py"
    fake_launcher.write_text(
        "\n".join(
            (
                "from pathlib import Path",
                "import sys",
                "",
                "argv = sys.argv[1:]",
                "files_path = Path(argv[argv.index('--files') + 1])",
                "out_dir = Path(argv[argv.index('-o') + 1])",
                "subject = argv[argv.index('-s') + 1]",
                "session = argv[argv.index('-ss') + 1] if '-ss' in argv else None",
                "assert files_path.exists()",
                "target = out_dir / f'sub-{subject}'",
                "if session is not None:",
                "    target = target / f'ses-{session}'",
                "target.mkdir(parents=True, exist_ok=True)",
                "(target / 'marker.txt').write_text(str(files_path), encoding='utf-8')",
            )
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    _set_launcher(
        config_path,
        f'launcher = ["{sys.executable.replace("\\", "/")}", "{fake_launcher.as_posix()}"]',
    )

    result = _invoke_from(project_dir, ["heudiconv"])
    assert result.exit_code == 0, result.output
    assert "Completed `bidsflow heudiconv` execution." in result.output
    assert (project_dir / "sourcedata" / "raw" / "sub-001" / "marker.txt").is_file()


def test_heudiconv_run_writes_current_state_and_unit_table(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"
    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    config_path = project_dir / "bidsflow.toml"
    _append_config(
        config_path,
        [
            "[sources]",
            'pattern = "SUB{subject}_SES{session}"',
        ],
    )

    (project_dir / "sourcedata" / "SUB001_SES01").mkdir(parents=True)
    (project_dir / "sourcedata" / "SUB001_SES02").mkdir(parents=True)
    sources_result = _invoke_from(project_dir, ["heudiconv", "init"])
    assert sources_result.exit_code == 0, sources_result.output

    _write_minimal_heuristic(project_dir)

    fake_launcher = project_dir / "fake_heudiconv.py"
    fake_launcher.write_text(
        "\n".join(
            (
                "from pathlib import Path",
                "import sys",
                "",
                "argv = sys.argv[1:]",
                "files_path = Path(argv[argv.index('--files') + 1])",
                "out_dir = Path(argv[argv.index('-o') + 1])",
                "subject = argv[argv.index('-s') + 1]",
                "session = argv[argv.index('-ss') + 1]",
                "assert files_path.exists()",
                "target = out_dir / f'sub-{subject}' / f'ses-{session}'",
                "target.mkdir(parents=True, exist_ok=True)",
                "(target / 'marker.txt').write_text(str(files_path), encoding='utf-8')",
                "print('run ok')",
            )
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    _set_launcher(
        config_path,
        f'launcher = ["{sys.executable.replace("\\", "/")}", "{fake_launcher.as_posix()}"]',
    )

    result = _invoke_from(project_dir, ["heudiconv"])
    assert result.exit_code == 0, result.output

    raw_root = project_dir / "sourcedata" / "raw"
    assert (raw_root / "sub-001" / "ses-01" / "marker.txt").is_file()
    assert (raw_root / "sub-001" / "ses-02" / "marker.txt").is_file()

    state_path = project_dir / "state" / "heudiconv" / "run.json"
    units_path = project_dir / "state" / "heudiconv" / "run.tsv"
    assert state_path.is_file()
    assert units_path.is_file()

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["workflow"] == "heudiconv"
    assert state["step"] == "run"
    assert state["backend"] == "local"
    assert state["status"] == "succeeded"
    assert state["input_signature"].startswith("sha256:")
    assert state["started_at"] is not None
    assert state["finished_at"] is not None
    assert state["artifacts"]["raw_bids_dataset"] == str(raw_root)
    assert Path(state["log_dir"]).is_dir()
    assert Path(state["unit_status_dir"]).is_dir()
    assert Path(state["claim_dir"]).is_dir()
    assert state["unit_table_path"] == str(units_path)
    assert state["execution_view_cleaned"] is True
    assert not Path(state["execution_view_root"]).exists()
    assert "error" not in state

    rows = _read_tsv_rows(units_path)
    assert [row["source_name"] for row in rows] == ["SUB001_SES01", "SUB001_SES02"]
    assert [row["status"] for row in rows] == ["succeeded", "succeeded"]
    assert rows[0]["summary"] == "sub-001 ses-01"
    assert rows[1]["summary"] == "sub-001 ses-02"
    assert all(Path(row["log_path"]).is_file() for row in rows)
    assert all(Path(row["log_path"]).parent == Path(state["log_dir"]) for row in rows)
    assert [row["exit_code"] for row in rows] == ["0", "0"]

    status_files = sorted(Path(state["unit_status_dir"]).glob("*.status"))
    assert [path.name for path in status_files] == ["sub-001_ses-01.status", "sub-001_ses-02.status"]
    status_payloads = [_read_key_value_file(path) for path in status_files]
    assert [payload["status"] for payload in status_payloads] == ["succeeded", "succeeded"]
    assert [payload["exit_code"] for payload in status_payloads] == ["0", "0"]
    assert list(Path(state["claim_dir"]).glob("*.running")) == []

    unit_log_text = Path(rows[0]["log_path"]).read_text(encoding="utf-8")
    assert "run ok" in unit_log_text
    assert "--files" in unit_log_text


def test_heudiconv_run_with_sge_generates_array_artifacts_and_submits(tmp_path: Path) -> None:
    project_dir = tmp_path / "sge-project"
    init_result = runner.invoke(app, ["init", str(project_dir), "--scheduler", "sge"])
    assert init_result.exit_code == 0, init_result.output

    config_path = project_dir / "bidsflow.toml"
    _append_config(
        config_path,
        [
            "[sources]",
            'pattern = "SUB{subject}_SES{session}"',
        ],
    )

    fake_qsub = project_dir / "fake_qsub.py"
    fake_qsub.write_text(
        "\n".join(
            (
                "from pathlib import Path",
                "import sys",
                "",
                "script = Path(sys.argv[-1])",
                "assert script.is_file()",
                "Path('qsub-args.txt').write_text('\\n'.join(sys.argv[1:]), encoding='utf-8')",
                "print('12345')",
            )
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    _set_submit_command(
        config_path,
        f'submit_command = ["{sys.executable.replace("\\", "/")}", "{fake_qsub.as_posix()}"]',
    )

    (project_dir / "sourcedata" / "SUB001_SES01").mkdir(parents=True)
    (project_dir / "sourcedata" / "SUB001_SES02").mkdir(parents=True)
    sources_result = _invoke_from(project_dir, ["heudiconv", "init"])
    assert sources_result.exit_code == 0, sources_result.output
    _write_minimal_heuristic(project_dir)

    result = _invoke_from(project_dir, ["heudiconv"])
    assert result.exit_code == 0, result.output
    assert "Submitted `bidsflow heudiconv` execution." in result.output
    assert "Scheduler job id: 12345" in result.output
    assert "Scheduler unit list:" in result.output
    assert "Unit status files:" in result.output
    assert "Unit claims:" in result.output

    state_path = project_dir / "state" / "heudiconv" / "run.json"
    units_path = project_dir / "state" / "heudiconv" / "run.tsv"
    state = json.loads(state_path.read_text(encoding="utf-8"))

    assert state["backend"] == "sge"
    assert state["status"] == "submitted"
    assert state["scheduler"]["name"] == "sge"
    assert state["scheduler"]["job_id"] == "12345"
    assert state["scheduler"]["array_range"] == "1-2"
    assert "task_table_path" not in state["scheduler"]
    assert "command_dir" not in state["scheduler"]
    assert "execution_view_cleaned" not in state
    assert state["unit_counts"]["selected"] == 2
    assert state["unit_counts"]["skipped"] == 0

    scheduler_script = Path(state["scheduler"]["script_path"])
    unit_list_path = Path(state["scheduler"]["unit_list_path"])
    scheduler_log_dir = Path(state["scheduler"]["scheduler_log_dir"])
    claim_dir = Path(state["claim_dir"])
    unit_status_dir = Path(state["unit_status_dir"])

    assert scheduler_script.is_file()
    assert unit_list_path.is_file()
    assert scheduler_log_dir.is_dir()
    assert claim_dir.is_dir()
    assert unit_status_dir.is_dir()
    assert scheduler_log_dir.is_relative_to(project_dir / "logs")
    assert not (scheduler_script.parent / "tasks.tsv").exists()
    assert not (scheduler_script.parent / "commands").exists()

    script_text = scheduler_script.read_text(encoding="utf-8")
    assert "#$ -t 1-2" in script_text
    assert f"#$ -o {scheduler_log_dir}" in script_text
    assert "unit_list_path=" in script_text
    assert str(unit_list_path) in script_text
    assert "launcher=(" in script_text
    assert "unit_row=" in script_text
    assert "write_unit_status" in script_text
    assert 'rm -f -- "$claim_path"' in script_text
    assert "Log directory:" not in script_text
    assert "Unit list:" not in script_text
    assert "Execution path:" not in script_text
    assert "SGE job/task:" in script_text
    assert "unit_log_path" not in script_text
    assert "{{" not in script_text
    assert "task_table_path" not in script_text

    unit_rows = _read_tsv_rows(unit_list_path)
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
    status_payloads = [_read_key_value_file(Path(row["unit_status_path"])) for row in unit_rows]
    assert [payload["status"] for payload in status_payloads] == ["submitted", "submitted"]
    assert [payload["job_id"] for payload in status_payloads] == ["12345", "12345"]
    assert [payload["log_dir"] for payload in status_payloads] == [str(scheduler_log_dir), str(scheduler_log_dir)]

    run_rows = _read_tsv_rows(units_path)
    assert run_rows == []

    qsub_args = (project_dir / "qsub-args.txt").read_text(encoding="utf-8")
    assert str(scheduler_script) in qsub_args

    second_result = _invoke_from(project_dir, ["heudiconv"])
    assert second_result.exit_code == 0, second_result.output
    assert "No runnable `bidsflow heudiconv` units were found." in second_result.output
    assert "Skipped units: 2" in second_result.output


def test_heudiconv_run_rejects_sources_that_still_needs_review(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"
    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    (project_dir / "sourcedata" / "SUB001_SES01").mkdir(parents=True)
    sources_result = _invoke_from(project_dir, ["heudiconv", "init"])
    assert sources_result.exit_code == 0, sources_result.output

    _write_minimal_heuristic(project_dir)

    result = _invoke_from(project_dir, ["heudiconv"])

    assert result.exit_code == 2
    assert "sources table still needs review" in result.output.lower()


def test_heudiconv_run_overwrites_current_state_and_keeps_unit_logs(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"
    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    config_path = project_dir / "bidsflow.toml"
    _append_config(
        config_path,
        [
            "[sources]",
            'pattern = "SUB{subject}_SES{session}"',
        ],
    )

    (project_dir / "sourcedata" / "SUB001_SES01").mkdir(parents=True)
    sources_result = _invoke_from(project_dir, ["heudiconv", "init"])
    assert sources_result.exit_code == 0, sources_result.output
    _write_minimal_heuristic(project_dir)

    flaky_launcher = project_dir / "flaky_heudiconv.py"
    flaky_launcher.write_text(
        "\n".join(
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
            )
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    _set_launcher(
        config_path,
        f'launcher = ["{sys.executable.replace("\\", "/")}", "{flaky_launcher.as_posix()}"]',
    )

    first_result = _invoke_from(project_dir, ["heudiconv"])
    assert first_result.exit_code == 2
    state_path = project_dir / "state" / "heudiconv" / "run.json"
    units_path = project_dir / "state" / "heudiconv" / "run.tsv"
    first_state = json.loads(state_path.read_text(encoding="utf-8"))
    first_rows = _read_tsv_rows(units_path)
    assert first_state["status"] == "failed"
    assert first_rows[0]["status"] == "failed"
    assert first_rows[0]["exit_code"] == "1"
    first_log_dir = Path(first_state["log_dir"])

    second_result = _invoke_from(project_dir, ["heudiconv"])
    assert second_result.exit_code == 0, second_result.output

    second_state = json.loads(state_path.read_text(encoding="utf-8"))
    second_rows = _read_tsv_rows(units_path)
    assert second_state["status"] == "succeeded"
    assert [row["status"] for row in second_rows] == ["failed", "succeeded"]
    assert second_rows[-1]["exit_code"] == "0"
    second_log_dir = Path(second_state["log_dir"])

    assert first_log_dir.is_dir()
    assert second_log_dir.is_dir()
    assert first_log_dir != second_log_dir
    assert Path(first_rows[0]["log_path"]).is_file()
    assert Path(second_rows[-1]["log_path"]).is_file()
