from __future__ import annotations

import json
from pathlib import Path
import sys

from typer.testing import CliRunner

from bidsflow.cli import app

runner = CliRunner()


def test_heudiconv_bootstrap_dry_run_single_path_uses_generated_subject(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    sample_dir = project_dir / "incoming" / "sample-ses-01"
    sample_dir.mkdir(parents=True)

    result = runner.invoke(
        app,
        [
            "heudiconv",
            "bootstrap",
            str(sample_dir),
            "--config",
            str(project_dir / "bidsflow.toml"),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "single-directory bootstrap" in result.output
    assert "Temporary subject for bootstrap: bootstrap01" in result.output
    assert "heudiconv --files" in result.output
    assert str(sample_dir) in result.output
    assert "-s bootstrap01" in result.output
    assert str(project_dir / "state" / "heudiconv" / "bootstrap-work") in result.output


def test_heudiconv_bootstrap_dry_run_multiple_paths_shows_session_split(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    sample_dir_one = project_dir / "incoming" / "sample-ses-01"
    sample_dir_two = project_dir / "incoming" / "sample-ses-02"
    sample_dir_one.mkdir(parents=True)
    sample_dir_two.mkdir(parents=True)

    result = runner.invoke(
        app,
        [
            "heudiconv",
            "bootstrap",
            str(sample_dir_one),
            str(sample_dir_two),
            "--config",
            str(project_dir / "bidsflow.toml"),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "split 2 directories into single-directory session units" in result.output
    assert "bootstrap-ses01" in result.output
    assert "bootstrap-ses02" in result.output
    assert "-s bootstrap01 -ss bootstrap-ses01" in result.output
    assert "-s bootstrap01 -ss bootstrap-ses02" in result.output
    assert str(project_dir / "state" / "heudiconv" / "bootstrap-work") in result.output


def test_heudiconv_bootstrap_single_path_uses_generated_subject(tmp_path: Path) -> None:
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
                "info_dir = out_dir / '.heudiconv' / 'bootstrap' / session",
                "info_dir.mkdir(parents=True, exist_ok=True)",
                "(info_dir / 'heuristic.py').write_text('def infotodict(seqinfo):\\n    return {}\\n', encoding='utf-8')",
                "(info_dir / 'dicominfo.tsv').write_text(",
                "    f'series_id\\tprotocol_name\\tsample_path\\tsubject\\n1\\tT1w\\t{sample_path}\\t{subject}\\n',",
                "    encoding='utf-8',",
                ")",
                "print('bootstrap ok')",
            )
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    config_path = project_dir / "bidsflow.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + "\n[heudiconv]\n"
        + f'launcher = ["{sys.executable.replace("\\", "/")}", "{launcher_script.as_posix()}"]\n',
        encoding="utf-8",
        newline="\n",
    )

    sample_dir = project_dir / "incoming" / "sample-ses-01"
    sample_dir.mkdir(parents=True)

    result = runner.invoke(
        app,
        ["heudiconv", "bootstrap", str(sample_dir), "--config", str(config_path)],
    )

    assert result.exit_code == 0, result.output
    assert "Bootstrap units: 1" in result.output

    heuristic_path = project_dir / "code" / "heudiconv" / "heuristic.py"
    dicominfo_path = project_dir / "code" / "heudiconv" / "dicominfo" / "sample-01" / "dicominfo.tsv"
    state_path = project_dir / "state" / "heudiconv" / "bootstrap.json"
    log_path = project_dir / "logs" / "heudiconv" / "bootstrap.log"
    bootstrap_work_root = project_dir / "state" / "heudiconv" / "bootstrap-work"

    assert heuristic_path.is_file()
    assert dicominfo_path.is_file()
    assert state_path.is_file()
    assert log_path.is_file()
    assert bootstrap_work_root.is_dir()
    assert not (project_dir / "sourcedata" / "raw").exists()
    log_text = log_path.read_text(encoding="utf-8")
    assert "-s bootstrap01" in log_text
    assert str(bootstrap_work_root) in log_text

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "succeeded"
    assert state["sample_paths"] == [str(sample_dir.resolve())]
    assert state["artifacts"]["heuristic_template"] == str(heuristic_path)
    assert state["artifacts"]["dicom_inventory_dir"] == str(project_dir / "code" / "heudiconv" / "dicominfo")
    assert state["artifacts"]["dicom_inventories"] == [str(dicominfo_path)]
    assert state["artifacts"]["bootstrap_work_root"] == str(bootstrap_work_root)
    assert state["artifacts"]["heudiconv_state"] == str(bootstrap_work_root / ".heudiconv")
    assert len(state["units"]) == 1
    assert state["units"][0]["strategy"] == "generated_subject"
    assert state["units"][0]["subject_label"] == "bootstrap01"
    assert state["units"][0]["session_label"] is None
    assert len(state["units"][0]["attempted_commands"]) == 1


def test_heudiconv_bootstrap_multiple_paths_split_into_session_units(tmp_path: Path) -> None:
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
                "info_dir = out_dir / '.heudiconv' / 'bootstrap' / session",
                "info_dir.mkdir(parents=True, exist_ok=True)",
                "(info_dir / 'heuristic.py').write_text('def infotodict(seqinfo):\\n    return {}\\n', encoding='utf-8')",
                "(info_dir / 'dicominfo.tsv').write_text(",
                "    f'series_id\\tprotocol_name\\tsample_path\\tsubject\\tsession\\n1\\tT1w\\t{sample_path}\\t{subject}\\t{session}\\n',",
                "    encoding='utf-8',",
                ")",
                "print('bootstrap ok')",
            )
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    config_path = project_dir / "bidsflow.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + "\n[heudiconv]\n"
        + f'launcher = ["{sys.executable.replace("\\", "/")}", "{launcher_script.as_posix()}"]\n',
        encoding="utf-8",
        newline="\n",
    )

    sample_dir_one = project_dir / "incoming" / "sample-ses-01"
    sample_dir_two = project_dir / "incoming" / "sample-ses-02"
    sample_dir_one.mkdir(parents=True)
    sample_dir_two.mkdir(parents=True)

    result = runner.invoke(
        app,
        [
            "heudiconv",
            "bootstrap",
            str(sample_dir_one),
            str(sample_dir_two),
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Bootstrap units: 2" in result.output

    heuristic_path = project_dir / "code" / "heudiconv" / "heuristic.py"
    dicominfo_root = project_dir / "code" / "heudiconv" / "dicominfo"
    dicominfo_path_one = dicominfo_root / "bootstrap-ses01" / "dicominfo.tsv"
    dicominfo_path_two = dicominfo_root / "bootstrap-ses02" / "dicominfo.tsv"
    state_path = project_dir / "state" / "heudiconv" / "bootstrap.json"
    log_path = project_dir / "logs" / "heudiconv" / "bootstrap.log"
    bootstrap_work_root = project_dir / "state" / "heudiconv" / "bootstrap-work"

    assert heuristic_path.is_file()
    assert dicominfo_path_one.is_file()
    assert dicominfo_path_two.is_file()
    assert state_path.is_file()
    assert log_path.is_file()
    assert bootstrap_work_root.is_dir()
    assert not (project_dir / "sourcedata" / "raw").exists()

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "succeeded"
    assert state["sample_paths"] == [str(sample_dir_one.resolve()), str(sample_dir_two.resolve())]
    assert state["artifacts"]["heuristic_template"] == str(heuristic_path)
    assert state["artifacts"]["dicom_inventory_dir"] == str(dicominfo_root)
    assert state["artifacts"]["dicom_inventories"] == [
        str(dicominfo_path_one),
        str(dicominfo_path_two),
    ]
    assert state["artifacts"]["bootstrap_work_root"] == str(bootstrap_work_root)
    assert state["artifacts"]["heudiconv_state"] == str(bootstrap_work_root / ".heudiconv")
    assert len(state["units"]) == 2
    assert state["units"][0]["subject_label"] == "bootstrap01"
    assert state["units"][0]["session_label"] == "bootstrap-ses01"
    assert state["units"][0]["strategy"] == "generated_multi_session"
    assert state["units"][1]["subject_label"] == "bootstrap01"
    assert state["units"][1]["session_label"] == "bootstrap-ses02"
    assert state["units"][1]["strategy"] == "generated_multi_session"


def test_heudiconv_bootstrap_requires_reset_before_regenerating(tmp_path: Path) -> None:
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
                "info_dir = out_dir / '.heudiconv' / 'bootstrap' / session",
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
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + "\n[heudiconv]\n"
        + f'launcher = ["{sys.executable.replace("\\", "/")}", "{launcher_script.as_posix()}"]\n',
        encoding="utf-8",
        newline="\n",
    )

    sample_dir = project_dir / "incoming" / "sample-ses-01"
    sample_dir.mkdir(parents=True)

    first = runner.invoke(app, ["heudiconv", "bootstrap", str(sample_dir), "--config", str(config_path)])
    assert first.exit_code == 0, first.output

    blocked = runner.invoke(app, ["heudiconv", "bootstrap", str(sample_dir), "--config", str(config_path)])
    assert blocked.exit_code == 2
    assert "Existing HeuDiConv bootstrap state was found" in blocked.output

    allowed = runner.invoke(
        app,
        ["heudiconv", "bootstrap", str(sample_dir), "--config", str(config_path), "--reset"],
    )
    assert allowed.exit_code == 0, allowed.output
