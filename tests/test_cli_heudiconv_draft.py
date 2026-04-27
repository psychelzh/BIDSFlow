from __future__ import annotations

import json
import sys
from pathlib import Path

from helpers import (
    init_project,
    make_source_dirs,
    set_launcher,
    write_python_script,
)


def _write_successful_draft_launcher(project_dir: Path) -> Path:
    return write_python_script(
        project_dir / "fake_heudiconv.py",
        (
            "from pathlib import Path",
            "import sys",
            "",
            "argv = sys.argv[1:]",
            "out_dir = Path(argv[argv.index('-o') + 1])",
            "sample_path = argv[argv.index('--files') + 1]",
            "subject = argv[argv.index('-s') + 1] if '-s' in argv else None",
            "session = argv[argv.index('-ss') + 1] if '-ss' in argv else 'single'",
            "info_dir = out_dir / '.heudiconv' / 'draft' / session",
            "info_dir.mkdir(parents=True, exist_ok=True)",
            "(info_dir / 'heuristic.py').write_text('def infotodict(seqinfo):\\n    return {}\\n', encoding='utf-8')",
            "(info_dir / 'dicominfo.tsv').write_text(",
            "    f'series_id\\tprotocol_name\\tsample_path\\tsubject\\tsession\\n1\\tT1w\\t{sample_path}\\t{subject}\\t{session}\\n',",
            "    encoding='utf-8',",
            ")",
            "print('draft ok')",
        ),
    )


def test_heudiconv_draft_single_path_uses_generated_subject(
    tmp_path: Path,
    invoke_from,
    runner,
) -> None:
    project_dir = init_project(tmp_path, runner)
    launcher_script = _write_successful_draft_launcher(project_dir)

    config_path = project_dir / "bidsflow.toml"
    set_launcher(
        config_path,
        f'launcher = ["{sys.executable}", "{launcher_script.as_posix()}"]',
    )

    sample_dir = make_source_dirs(project_dir, "sample-ses-01") / "sample-ses-01"

    result = invoke_from(project_dir, ["heudiconv", "draft", "sample-ses-01"])

    assert result.exit_code == 0, result.output
    assert "Draft samples: 1" in result.output

    heuristic_path = project_dir / "code" / "heudiconv" / "heuristic.py"
    dicominfo_path = project_dir / "code" / "heudiconv" / "dicominfo" / "sample-01" / "dicominfo.tsv"
    state_path = project_dir / "state" / "heudiconv" / "draft.json"
    draft_work_root = project_dir / "work" / "heudiconv" / "draft-work"

    assert heuristic_path.is_file()
    assert dicominfo_path.is_file()
    assert state_path.is_file()
    assert draft_work_root.is_dir()
    assert not (project_dir / "sourcedata" / "raw").exists()

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "succeeded"
    assert state["sample_paths"] == [str(sample_dir.resolve())]
    assert state["artifacts"]["heuristic_template"] == str(heuristic_path)
    assert state["artifacts"]["dicom_inventory_dir"] == str(project_dir / "code" / "heudiconv" / "dicominfo")
    assert state["artifacts"]["draft_work_root"] == str(draft_work_root)
    assert state["artifacts"]["heudiconv_state"] == str(draft_work_root / ".heudiconv")
    assert Path(state["unit_log_dir"]).is_dir()
    assert "units" not in state

    unit_log_path = Path(state["unit_log_dir"]) / "sample-01.log"
    assert unit_log_path.is_file()
    unit_log_text = unit_log_path.read_text(encoding="utf-8")
    assert "-s draft01" in unit_log_text
    assert str(draft_work_root) in unit_log_text


def test_heudiconv_draft_multiple_paths_split_into_session_units(
    tmp_path: Path,
    invoke_from,
    runner,
) -> None:
    project_dir = init_project(tmp_path, runner)
    launcher_script = _write_successful_draft_launcher(project_dir)

    config_path = project_dir / "bidsflow.toml"
    set_launcher(
        config_path,
        f'launcher = ["{sys.executable}", "{launcher_script.as_posix()}"]',
    )

    source_root = make_source_dirs(project_dir, "sample-ses-01", "sample-ses-02")
    sample_dir_one = source_root / "sample-ses-01"
    sample_dir_two = source_root / "sample-ses-02"

    result = invoke_from(
        project_dir,
        ["heudiconv", "draft", "sample-ses-01", "sample-ses-02"],
    )

    assert result.exit_code == 0, result.output
    assert "Draft samples: 2" in result.output

    heuristic_path = project_dir / "code" / "heudiconv" / "heuristic.py"
    dicominfo_root = project_dir / "code" / "heudiconv" / "dicominfo"
    dicominfo_path_one = dicominfo_root / "draft-ses01" / "dicominfo.tsv"
    dicominfo_path_two = dicominfo_root / "draft-ses02" / "dicominfo.tsv"
    state_path = project_dir / "state" / "heudiconv" / "draft.json"
    draft_work_root = project_dir / "work" / "heudiconv" / "draft-work"

    assert heuristic_path.is_file()
    assert dicominfo_path_one.is_file()
    assert dicominfo_path_two.is_file()
    assert state_path.is_file()
    assert draft_work_root.is_dir()
    assert not (project_dir / "sourcedata" / "raw").exists()

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "succeeded"
    assert state["sample_paths"] == [str(sample_dir_one.resolve()), str(sample_dir_two.resolve())]
    assert state["artifacts"]["heuristic_template"] == str(heuristic_path)
    assert state["artifacts"]["dicom_inventory_dir"] == str(dicominfo_root)
    assert state["artifacts"]["draft_work_root"] == str(draft_work_root)
    assert state["artifacts"]["heudiconv_state"] == str(draft_work_root / ".heudiconv")
    assert Path(state["unit_log_dir"]).is_dir()
    assert "units" not in state

    assert (Path(state["unit_log_dir"]) / "draft-ses01.log").is_file()
    assert (Path(state["unit_log_dir"]) / "draft-ses02.log").is_file()


def test_heudiconv_draft_requires_reset_before_regenerating(
    tmp_path: Path,
    invoke_from,
    runner,
) -> None:
    project_dir = init_project(tmp_path, runner)
    launcher_script = _write_successful_draft_launcher(project_dir)

    config_path = project_dir / "bidsflow.toml"
    set_launcher(
        config_path,
        f'launcher = ["{sys.executable}", "{launcher_script.as_posix()}"]',
    )

    make_source_dirs(project_dir, "sample-ses-01")

    first = invoke_from(project_dir, ["heudiconv", "draft", "sample-ses-01"])
    assert first.exit_code == 0, first.output

    blocked = invoke_from(project_dir, ["heudiconv", "draft", "sample-ses-01"])
    assert blocked.exit_code == 2
    assert "Existing HeuDiConv draft state was found" in blocked.output

    allowed = invoke_from(project_dir, ["heudiconv", "draft", "sample-ses-01", "--force"])
    assert allowed.exit_code == 0, allowed.output


def test_heudiconv_draft_rejects_invalid_launcher_config(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = init_project(tmp_path, runner)

    config_path = project_dir / "bidsflow.toml"
    set_launcher(config_path, 'launcher = "heudiconv"')

    make_source_dirs(project_dir, "sample-ses-01")

    result = invoke_from(project_dir, ["heudiconv", "draft", "sample-ses-01"])

    assert result.exit_code == 2
    assert "[heudiconv].launcher must be a non-empty list of strings." in result.output


def test_heudiconv_draft_rejects_sample_outside_configured_source_root(
    tmp_path: Path,
    invoke_from,
    runner,
) -> None:
    project_dir = init_project(tmp_path, runner)
    (project_dir / "sourcedata").mkdir(parents=True)
    outside_sample_dir = project_dir / "other-data" / "sample-ses-01"
    outside_sample_dir.mkdir(parents=True)

    result = invoke_from(project_dir, ["heudiconv", "draft", str(outside_sample_dir)])

    assert result.exit_code == 2
    assert "Sample path must resolve under the configured source root" in result.output
