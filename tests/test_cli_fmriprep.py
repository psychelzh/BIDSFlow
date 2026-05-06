from __future__ import annotations

import json
import sys
from pathlib import Path

from helpers import (
    init_project,
    read_key_value_file,
    read_tsv_rows,
    set_fmriprep_launcher,
    write_python_script,
)


def _make_raw_bids_participants(project_dir: Path, *labels: str) -> None:
    for label in labels:
        (project_dir / "sourcedata" / "raw" / f"sub-{label}").mkdir(parents=True)


def test_fmriprep_init_writes_participants_and_keeps_target_config(
    tmp_path: Path,
    invoke_from,
    runner,
) -> None:
    project_dir = init_project(tmp_path, runner)
    _make_raw_bids_participants(project_dir, "001", "002")

    result = invoke_from(project_dir, ["fmriprep", "init"])

    assert result.exit_code == 0, result.output
    assert "Initialized fMRIPrep support files." in result.output
    assert "Participants discovered: 2" in result.output
    assert "Target config:" in result.output

    rows = read_tsv_rows(project_dir / "state" / "fmriprep" / "participants.tsv")
    assert [row["participant_label"] for row in rows] == ["001", "002"]
    assert [row["include"] for row in rows] == ["true", "true"]
    assert [row["status"] for row in rows] == ["ready", "ready"]

    config_text = (project_dir / "config" / "fmriprep.toml").read_text(encoding="utf-8")
    assert "[options]" in config_text
    assert "nprocs = 8" in config_text
    assert "BIDSFlow manages participant-label" in config_text


def test_fmriprep_init_keeps_reviewed_files_by_default(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = init_project(tmp_path, runner)
    _make_raw_bids_participants(project_dir, "001")
    first = invoke_from(project_dir, ["fmriprep", "init"])
    assert first.exit_code == 0, first.output

    participants_path = project_dir / "state" / "fmriprep" / "participants.tsv"
    participants_path.write_text(
        "\n".join(
            (
                "participant_label\tinclude\tstatus\tnotes",
                "001\tfalse\texcluded\tmanual skip",
            )
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    _make_raw_bids_participants(project_dir, "002")

    second = invoke_from(project_dir, ["fmriprep", "init"])

    assert second.exit_code == 0, second.output
    assert "Existing participants table found; keeping reviewed file." in second.output
    rows = read_tsv_rows(participants_path)
    assert [row["participant_label"] for row in rows] == ["001"]
    assert rows[0]["include"] == "false"


def test_fmriprep_dry_run_shows_example_command(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = init_project(tmp_path, runner)
    _make_raw_bids_participants(project_dir, "001", "002")
    init_result = invoke_from(project_dir, ["fmriprep", "init"])
    assert init_result.exit_code == 0, init_result.output

    result = invoke_from(project_dir, ["fmriprep", "-n"])

    assert result.exit_code == 0, result.output
    assert "Planned `bidsflow fmriprep` execution." in result.output
    assert "Dry run only; no files or directories were created." in result.output
    assert "Ready participants: 2" in result.output
    assert "Runnable participants now: 2" in result.output
    assert "Example runnable participant:" in result.output
    assert "sub-001: participant=001" in result.output
    assert " participant --participant-label 001 -w " in result.output
    assert "--nprocs 8 --omp-nthreads 4 --mem-mb 32000" in result.output
    assert "--output-spaces MNI152NLin2009cAsym:res-2 T1w" in result.output
    assert "--notrack" in result.output
    assert "--skip-bids-validation" not in result.output
    assert not (project_dir / "work").exists()
    assert not (project_dir / "logs").exists()


def test_fmriprep_local_run_records_results_and_cleans_workdir(
    tmp_path: Path,
    invoke_from,
    runner,
) -> None:
    project_dir = init_project(tmp_path, runner)
    _make_raw_bids_participants(project_dir, "001")
    init_result = invoke_from(project_dir, ["fmriprep", "init"])
    assert init_result.exit_code == 0, init_result.output

    fake_launcher = write_python_script(
        project_dir / "fake_fmriprep.py",
        (
            "from pathlib import Path",
            "import sys",
            "",
            "argv = sys.argv[1:]",
            "raw_root = Path(argv[0])",
            "output_root = Path(argv[1])",
            "participant = argv[argv.index('--participant-label') + 1]",
            "work_dir = Path(argv[argv.index('-w') + 1])",
            "assert (raw_root / f'sub-{participant}').is_dir()",
            "work_dir.mkdir(parents=True, exist_ok=True)",
            "(work_dir / 'scratch.txt').write_text('scratch', encoding='utf-8')",
            "(output_root / f'sub-{participant}').mkdir(parents=True, exist_ok=True)",
            "print('fmriprep ok')",
        ),
    )
    set_fmriprep_launcher(
        project_dir / "bidsflow.toml",
        f'launcher = ["{sys.executable}", "{fake_launcher.as_posix()}"]',
    )

    result = invoke_from(project_dir, ["fmriprep"])

    assert result.exit_code == 0, result.output
    assert "Completed `bidsflow fmriprep` execution." in result.output
    assert "Cleanup units: 1" in result.output

    state = json.loads((project_dir / "state" / "fmriprep" / "run.json").read_text(encoding="utf-8"))
    assert state["workflow"] == "fmriprep"
    assert state["record_state"] == "succeeded"
    assert state["backend"] == "local"

    rows = read_tsv_rows(project_dir / "state" / "fmriprep" / "results.tsv")
    assert [row["record_type"] for row in rows] == ["run", "cleanup"]
    assert [row["status"] for row in rows] == ["succeeded", "succeeded"]
    assert [row["participant_label"] for row in rows] == ["001", "001"]
    assert Path(rows[0]["log_path"]).is_file()
    assert not (project_dir / "work" / "fmriprep" / "sub-001").exists()

    status = read_key_value_file(project_dir / "state" / "fmriprep" / "units" / "sub-001.status")
    assert status["status"] == "succeeded"
    assert status["exit_code"] == "0"
    assert status["cleanup_status"] == "succeeded"


def test_fmriprep_rejects_managed_options(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = init_project(tmp_path, runner)
    _make_raw_bids_participants(project_dir, "001")
    init_result = invoke_from(project_dir, ["fmriprep", "init"])
    assert init_result.exit_code == 0, init_result.output

    (project_dir / "config" / "fmriprep.toml").write_text(
        "[options]\nparticipant-label = \"001\"\n",
        encoding="utf-8",
        newline="\n",
    )

    result = invoke_from(project_dir, ["fmriprep", "--dry-run"])

    assert result.exit_code == 2
    assert "managed by BIDSFlow" in result.output
