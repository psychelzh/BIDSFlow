from __future__ import annotations

import json
from pathlib import Path

import pytest

import bidsflow.heudiconv as h
from helpers import (
    init_project,
    load_context,
    make_source_dirs,
    set_launcher,
    write_python_script,
)


def test_draft_reports_missing_source_root_and_missing_sample(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = init_project(tmp_path, runner, name="draft-missing-inputs")
    with pytest.raises(h.HeudiconvDraftError, match="At least one sample path"):
        h.plan_draft(load_context(project_dir), [])

    missing_root = invoke_from(project_dir, ["heudiconv", "draft", "sample"])
    assert missing_root.exit_code == 2
    assert "Configured source root does not exist" in missing_root.output

    (project_dir / "sourcedata").mkdir()
    missing_sample = invoke_from(project_dir, ["heudiconv", "draft", "sample"])
    assert missing_sample.exit_code == 2
    assert "Sample path does not exist" in missing_sample.output


def test_draft_rejects_file_source_root_and_project_relative_outside_sample(
    tmp_path: Path,
    invoke_from,
    runner,
) -> None:
    project_dir = init_project(tmp_path, runner, name="draft-bad-source-root")

    (project_dir / "sourcedata").write_text("not a directory\n", encoding="utf-8")
    file_root = invoke_from(project_dir, ["heudiconv", "draft", "sample"])
    assert file_root.exit_code == 2
    assert "Configured source root is not a directory" in file_root.output

    (project_dir / "sourcedata").unlink()
    (project_dir / "sourcedata").mkdir()
    (project_dir / "outside-sample").mkdir()
    outside = invoke_from(project_dir, ["heudiconv", "draft", "outside-sample", "--dry-run"])
    assert outside.exit_code == 2
    assert "Sample path must resolve under the configured source root" in outside.output


@pytest.mark.parametrize(
    ("script_lines", "sample_args", "message"),
    [
        (["raise SystemExit(1)"], ["sample-ses-01"], "draft generation failed for the provided sample path"),
        (
            ["raise SystemExit(1)"],
            ["sample-ses-01", "sample-ses-02"],
            "draft generation failed while processing a representative session directory",
        ),
        (["print('ok')"], ["sample-ses-01"], "did not produce heuristic.py"),
        (
            [
                "from pathlib import Path",
                "import sys",
                "argv = sys.argv[1:]",
                "out_dir = Path(argv[argv.index('-o') + 1])",
                "info_dir = out_dir / '.heudiconv' / 'draft' / 'single'",
                "info_dir.mkdir(parents=True, exist_ok=True)",
                "(info_dir / 'heuristic.py').write_text('def infotodict(seqinfo):\\n    return {}\\n', encoding='utf-8')",
            ],
            ["sample-ses-01"],
            "did not produce dicominfo output",
        ),
    ],
)
def test_draft_failure_paths_write_failed_state(
    tmp_path: Path,
    invoke_from,
    runner,
    python_launcher: str,
    script_lines: list[str],
    sample_args: list[str],
    message: str,
) -> None:
    project_dir = init_project(tmp_path, runner, name="draft-failure-project")
    make_source_dirs(project_dir, *sample_args)

    launcher_script = project_dir / "fake_draft.py"
    write_python_script(launcher_script, script_lines)
    set_launcher(
        project_dir / "bidsflow.toml",
        f'launcher = ["{python_launcher}", "{launcher_script.as_posix()}"]',
    )

    result = invoke_from(project_dir, ["heudiconv", "draft", *sample_args])

    assert result.exit_code == 2
    assert message in result.output
    state = json.loads((project_dir / "state" / "heudiconv" / "draft.json").read_text(encoding="utf-8"))
    assert state["status"] == "failed"
    assert "failed_sample" in state
    assert message in state["error"]


def test_draft_launcher_start_failure_is_logged(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = init_project(tmp_path, runner, name="draft-missing-launcher")
    make_source_dirs(project_dir, "sample-ses-01")
    set_launcher(project_dir / "bidsflow.toml", 'launcher = ["definitely-not-heudiconv"]')

    result = invoke_from(project_dir, ["heudiconv", "draft", "sample-ses-01"])

    assert result.exit_code == 2
    state = json.loads((project_dir / "state" / "heudiconv" / "draft.json").read_text(encoding="utf-8"))
    log_path = Path(state["unit_log_dir"]) / "sample-01.log"
    assert "Failed to start launcher" in log_path.read_text(encoding="utf-8")


def test_draft_rejects_different_heuristics_across_sample_units(
    tmp_path: Path,
    invoke_from,
    runner,
    python_launcher: str,
) -> None:
    project_dir = init_project(tmp_path, runner, name="draft-different-heuristics")
    make_source_dirs(project_dir, "sample-ses-01", "sample-ses-02")

    launcher_script = project_dir / "fake_draft.py"
    write_python_script(
        launcher_script,
        (
            "from pathlib import Path",
            "import sys",
            "argv = sys.argv[1:]",
            "out_dir = Path(argv[argv.index('-o') + 1])",
            "session = argv[argv.index('-ss') + 1]",
            "info_dir = out_dir / '.heudiconv' / 'draft' / session",
            "info_dir.mkdir(parents=True, exist_ok=True)",
            "(info_dir / 'heuristic.py').write_text(f'# {session}\\n', encoding='utf-8')",
            "(info_dir / 'dicominfo.tsv').write_text('series_id\\n1\\n', encoding='utf-8')",
        ),
    )
    set_launcher(
        project_dir / "bidsflow.toml",
        f'launcher = ["{python_launcher}", "{launcher_script.as_posix()}"]',
    )

    result = invoke_from(project_dir, ["heudiconv", "draft", "sample-ses-01", "sample-ses-02"])

    assert result.exit_code == 2
    assert "Generated heuristic drafts differed" in result.output
