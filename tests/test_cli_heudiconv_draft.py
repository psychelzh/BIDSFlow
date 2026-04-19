from __future__ import annotations

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


def _set_launcher(config_path: Path, launcher_line: str) -> None:
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            '# launcher = ["heudiconv"]',
            launcher_line,
        ),
        encoding="utf-8",
        newline="\n",
    )


def test_heudiconv_draft_dry_run_single_path_uses_generated_subject(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    sample_dir = project_dir / "sourcedata" / "sample-ses-01"
    sample_dir.mkdir(parents=True)

    result = _invoke_from(project_dir, ["heudiconv", "draft", "sample-ses-01", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "single-directory draft" in result.output
    assert "Temporary subject for draft: draft01" in result.output
    assert "heudiconv --files" in result.output
    assert str(sample_dir) in result.output
    assert "-s draft01" in result.output
    assert str(project_dir / "work" / "heudiconv" / "draft-work") in result.output
    assert str(project_dir / "state" / "heudiconv" / "draft.json") in result.output


def test_heudiconv_draft_dry_run_multiple_paths_shows_session_split(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    sample_dir_one = project_dir / "sourcedata" / "sample-ses-01"
    sample_dir_two = project_dir / "sourcedata" / "sample-ses-02"
    sample_dir_one.mkdir(parents=True)
    sample_dir_two.mkdir(parents=True)

    result = _invoke_from(
        project_dir,
        ["heudiconv", "draft", "sample-ses-01", "sample-ses-02", "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert "split 2 directories into single-directory sample units" in result.output
    assert "draft-ses01" in result.output
    assert "draft-ses02" in result.output
    assert "-s draft01 -ss draft-ses01" in result.output
    assert "-s draft01 -ss draft-ses02" in result.output
    assert str(project_dir / "work" / "heudiconv" / "draft-work") in result.output
    assert str(project_dir / "state" / "heudiconv" / "draft.json") in result.output


def test_heudiconv_draft_dry_run_uses_configured_heuristic_path(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    config_path = project_dir / "bidsflow.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'heuristic = "code/heudiconv/heuristic.py"',
            'heuristic = "code/custom/heuristic.py"',
        ),
        encoding="utf-8",
        newline="\n",
    )

    sample_dir = project_dir / "sourcedata" / "sample-ses-01"
    sample_dir.mkdir(parents=True)

    result = _invoke_from(project_dir, ["heudiconv", "draft", "sample-ses-01", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert str(project_dir / "code" / "custom" / "heuristic.py") in result.output
    assert str(project_dir / "code" / "heudiconv" / "dicominfo") in result.output


def test_heudiconv_draft_single_path_uses_generated_subject(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    launcher_script = project_dir / "fake_heudiconv.py"
    launcher_script.write_text(
        "\n".join(
            (
                "from pathlib import Path",
                "import sys",
                "",
                "argv = sys.argv[1:]",
                "out_dir = Path(argv[argv.index('-o') + 1])",
                "sample_path = argv[argv.index('--files') + 1]",
                "subject = argv[argv.index('-s') + 1]",
                "session = argv[argv.index('-ss') + 1] if '-ss' in argv else 'single'",
                "info_dir = out_dir / '.heudiconv' / 'draft' / session",
                "info_dir.mkdir(parents=True, exist_ok=True)",
                "(info_dir / 'heuristic.py').write_text('def infotodict(seqinfo):\\n    return {}\\n', encoding='utf-8')",
                "(info_dir / 'dicominfo.tsv').write_text(",
                "    f'series_id\\tprotocol_name\\tsample_path\\tsubject\\n1\\tT1w\\t{sample_path}\\t{subject}\\n',",
                "    encoding='utf-8',",
                ")",
                "print('draft ok')",
            )
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    config_path = project_dir / "bidsflow.toml"
    _set_launcher(
        config_path,
        f'launcher = ["{sys.executable.replace("\\", "/")}", "{launcher_script.as_posix()}"]',
    )

    sample_dir = project_dir / "sourcedata" / "sample-ses-01"
    sample_dir.mkdir(parents=True)

    result = _invoke_from(project_dir, ["heudiconv", "draft", "sample-ses-01"])

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


def test_heudiconv_draft_multiple_paths_split_into_session_units(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    launcher_script = project_dir / "fake_heudiconv.py"
    launcher_script.write_text(
        "\n".join(
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
            )
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    config_path = project_dir / "bidsflow.toml"
    _set_launcher(
        config_path,
        f'launcher = ["{sys.executable.replace("\\", "/")}", "{launcher_script.as_posix()}"]',
    )

    sample_dir_one = project_dir / "sourcedata" / "sample-ses-01"
    sample_dir_two = project_dir / "sourcedata" / "sample-ses-02"
    sample_dir_one.mkdir(parents=True)
    sample_dir_two.mkdir(parents=True)

    result = _invoke_from(
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


def test_heudiconv_draft_requires_reset_before_regenerating(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    launcher_script = project_dir / "fake_heudiconv.py"
    launcher_script.write_text(
        "\n".join(
            (
                "from pathlib import Path",
                "import sys",
                "",
                "argv = sys.argv[1:]",
                "out_dir = Path(argv[argv.index('-o') + 1])",
                "subject = argv[argv.index('-s') + 1]",
                "session = argv[argv.index('-ss') + 1] if '-ss' in argv else 'single'",
                "info_dir = out_dir / '.heudiconv' / 'draft' / session",
                "info_dir.mkdir(parents=True, exist_ok=True)",
                "(info_dir / 'heuristic.py').write_text('def infotodict(seqinfo):\\n    return {}\\n', encoding='utf-8')",
                "(info_dir / 'dicominfo.tsv').write_text('series_id\\tprotocol_name\\n1\\tT1w\\n', encoding='utf-8')",
            )
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    config_path = project_dir / "bidsflow.toml"
    _set_launcher(
        config_path,
        f'launcher = ["{sys.executable.replace("\\", "/")}", "{launcher_script.as_posix()}"]',
    )

    sample_dir = project_dir / "sourcedata" / "sample-ses-01"
    sample_dir.mkdir(parents=True)

    first = _invoke_from(project_dir, ["heudiconv", "draft", "sample-ses-01"])
    assert first.exit_code == 0, first.output

    blocked = _invoke_from(project_dir, ["heudiconv", "draft", "sample-ses-01"])
    assert blocked.exit_code == 2
    assert "Existing HeuDiConv draft state was found" in blocked.output

    allowed = _invoke_from(project_dir, ["heudiconv", "draft", "sample-ses-01", "--force"])
    assert allowed.exit_code == 0, allowed.output


def test_heudiconv_draft_rejects_invalid_launcher_config(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    config_path = project_dir / "bidsflow.toml"
    _set_launcher(config_path, 'launcher = "heudiconv"')

    sample_dir = project_dir / "sourcedata" / "sample-ses-01"
    sample_dir.mkdir(parents=True)

    result = _invoke_from(project_dir, ["heudiconv", "draft", "sample-ses-01", "--dry-run"])

    assert result.exit_code == 2
    assert "[heudiconv].launcher must be a non-empty list of strings." in result.output


def test_heudiconv_draft_rejects_sample_outside_configured_source_root(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    (project_dir / "sourcedata").mkdir(parents=True)
    outside_sample_dir = project_dir / "other-data" / "sample-ses-01"
    outside_sample_dir.mkdir(parents=True)

    result = _invoke_from(project_dir, ["heudiconv", "draft", str(outside_sample_dir), "--dry-run"])

    assert result.exit_code == 2
    assert "Sample path must resolve under the configured source root" in result.output



