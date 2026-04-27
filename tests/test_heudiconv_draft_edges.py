from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

import bidsflow.heudiconv as h
import bidsflow.heudiconv.draft as heudiconv_draft
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
    outside = invoke_from(project_dir, ["heudiconv", "draft", "outside-sample"])
    assert outside.exit_code == 2
    assert "Sample path must resolve under the configured source root" in outside.output


def test_draft_accepts_symlinked_relative_sample(tmp_path: Path, runner) -> None:
    project_dir = init_project(tmp_path, runner, name="draft-symlink-sample")
    source_root = project_dir / "sourcedata"
    archive_target = tmp_path / "archive" / "SUB001"
    archive_target.mkdir(parents=True)
    source_root.mkdir()
    (source_root / "siteA").symlink_to(archive_target, target_is_directory=True)

    plan = h.plan_draft(load_context(project_dir), [Path("siteA")])

    assert plan.sample_paths == (source_root / "siteA",)


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
        f'launcher = ["{sys.executable}", "{launcher_script.as_posix()}"]',
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
    unit_log_dir = Path(state["unit_log_dir"]).resolve()
    assert unit_log_dir.is_relative_to(project_dir.resolve())
    log_path = unit_log_dir / "sample-01.log"
    assert "Failed to start launcher" in log_path.read_text(encoding="utf-8")


def test_draft_detects_new_generated_files_without_timestamp_gate(tmp_path: Path) -> None:
    heudiconv_state = tmp_path / ".heudiconv"
    previous_heuristics = heudiconv_draft._snapshot_generated_files(heudiconv_state, "heuristic.py")
    previous_dicominfo = heudiconv_draft._snapshot_generated_files(heudiconv_state, "dicominfo*.tsv")
    info_dir = heudiconv_state / "draft" / "single"
    info_dir.mkdir(parents=True)
    heuristic_path = info_dir / "heuristic.py"
    dicominfo_path = info_dir / "dicominfo.tsv"
    heuristic_path.write_text("def infotodict(seqinfo):\n    return {}\n", encoding="utf-8")
    dicominfo_path.write_text("series_id\n1\n", encoding="utf-8")
    old_timestamp = 946684800
    os.utime(heuristic_path, (old_timestamp, old_timestamp))
    os.utime(dicominfo_path, (old_timestamp, old_timestamp))

    assert (
        heudiconv_draft._find_latest_generated_file_after_snapshot(
            heudiconv_state,
            "heuristic.py",
            previous_heuristics,
        )
        == heuristic_path
    )
    assert (
        heudiconv_draft._find_generated_dicominfo_files_after_snapshot(
            heudiconv_state,
            previous_dicominfo,
        )
        == (dicominfo_path,)
    )


def test_draft_rejects_different_heuristics_across_sample_units(
    tmp_path: Path,
    invoke_from,
    runner,
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
        f'launcher = ["{sys.executable}", "{launcher_script.as_posix()}"]',
    )

    result = invoke_from(project_dir, ["heudiconv", "draft", "sample-ses-01", "sample-ses-02"])

    assert result.exit_code == 2
    assert "Generated heuristic drafts differed" in result.output
