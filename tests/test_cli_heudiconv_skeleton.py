from __future__ import annotations

import csv
import json
from pathlib import Path
import sys

from typer.testing import CliRunner

from bidsflow.cli import app

runner = CliRunner()


def _set_launcher(config_path: Path, launcher_line: str) -> None:
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            '# launcher = ["heudiconv"]',
            launcher_line,
        ),
        encoding="utf-8",
        newline="\n",
    )


def _read_tsv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def test_heudiconv_skeleton_dry_run_single_path_uses_generated_subject(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    sample_dir = project_dir / "sourcedata" / "sample-ses-01"
    sample_dir.mkdir(parents=True)

    result = runner.invoke(
        app,
        [
            "heudiconv",
            "skeleton",
            "sample-ses-01",
            "--config",
            str(project_dir / "bidsflow.toml"),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "single-directory skeleton" in result.output
    assert "Temporary subject for skeleton: skeleton01" in result.output
    assert "heudiconv --files" in result.output
    assert str(sample_dir) in result.output
    assert "-s skeleton01" in result.output
    assert str(project_dir / "work" / "heudiconv" / "skeleton-work") in result.output
    assert str(project_dir / "state" / "heudiconv" / "skeleton.tsv") in result.output


def test_heudiconv_skeleton_dry_run_multiple_paths_shows_session_split(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    sample_dir_one = project_dir / "sourcedata" / "sample-ses-01"
    sample_dir_two = project_dir / "sourcedata" / "sample-ses-02"
    sample_dir_one.mkdir(parents=True)
    sample_dir_two.mkdir(parents=True)

    result = runner.invoke(
        app,
        [
            "heudiconv",
            "skeleton",
            "sample-ses-01",
            "sample-ses-02",
            "--config",
            str(project_dir / "bidsflow.toml"),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "split 2 directories into single-directory session units" in result.output
    assert "skeleton-ses01" in result.output
    assert "skeleton-ses02" in result.output
    assert "-s skeleton01 -ss skeleton-ses01" in result.output
    assert "-s skeleton01 -ss skeleton-ses02" in result.output
    assert str(project_dir / "work" / "heudiconv" / "skeleton-work") in result.output
    assert str(project_dir / "state" / "heudiconv" / "skeleton.tsv") in result.output


def test_heudiconv_skeleton_dry_run_uses_configured_heuristic_path(tmp_path: Path) -> None:
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

    result = runner.invoke(
        app,
        [
            "heudiconv",
            "skeleton",
            "sample-ses-01",
            "--config",
            str(config_path),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert str(project_dir / "code" / "custom" / "heuristic.py") in result.output
    assert str(project_dir / "code" / "heudiconv" / "dicominfo") in result.output


def test_heudiconv_skeleton_single_path_uses_generated_subject(tmp_path: Path) -> None:
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
                "info_dir = out_dir / '.heudiconv' / 'skeleton' / session",
                "info_dir.mkdir(parents=True, exist_ok=True)",
                "(info_dir / 'heuristic.py').write_text('def infotodict(seqinfo):\\n    return {}\\n', encoding='utf-8')",
                "(info_dir / 'dicominfo.tsv').write_text(",
                "    f'series_id\\tprotocol_name\\tsample_path\\tsubject\\n1\\tT1w\\t{sample_path}\\t{subject}\\n',",
                "    encoding='utf-8',",
                ")",
                "print('skeleton ok')",
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

    result = runner.invoke(
        app,
        ["heudiconv", "skeleton", "sample-ses-01", "--config", str(config_path)],
    )

    assert result.exit_code == 0, result.output
    assert "Skeleton units: 1" in result.output

    heuristic_path = project_dir / "code" / "heudiconv" / "heuristic.py"
    dicominfo_path = project_dir / "code" / "heudiconv" / "dicominfo" / "sample-01" / "dicominfo.tsv"
    state_path = project_dir / "state" / "heudiconv" / "skeleton.json"
    units_path = project_dir / "state" / "heudiconv" / "skeleton.tsv"
    skeleton_work_root = project_dir / "work" / "heudiconv" / "skeleton-work"

    assert heuristic_path.is_file()
    assert dicominfo_path.is_file()
    assert state_path.is_file()
    assert units_path.is_file()
    assert skeleton_work_root.is_dir()
    assert not (project_dir / "sourcedata" / "raw").exists()

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "succeeded"
    assert state["sample_paths"] == [str(sample_dir.resolve())]
    assert state["artifacts"]["heuristic_template"] == str(heuristic_path)
    assert state["artifacts"]["dicom_inventory_dir"] == str(project_dir / "code" / "heudiconv" / "dicominfo")
    assert state["artifacts"]["skeleton_work_root"] == str(skeleton_work_root)
    assert state["artifacts"]["heudiconv_state"] == str(skeleton_work_root / ".heudiconv")
    assert state["unit_table_path"] == str(units_path)
    assert Path(state["unit_log_dir"]).is_dir()
    assert "units" not in state

    rows = _read_tsv_rows(units_path)
    assert len(rows) == 1
    assert rows[0]["unit_name"] == "sample-01"
    assert rows[0]["strategy"] == "generated_subject"
    assert rows[0]["subject_label"] == "skeleton01"
    assert rows[0]["session_label"] == ""
    assert rows[0]["status"] == "succeeded"
    assert Path(rows[0]["log_path"]).is_file()
    assert Path(rows[0]["log_path"]).parent == Path(state["unit_log_dir"])

    unit_log_text = Path(rows[0]["log_path"]).read_text(encoding="utf-8")
    assert "-s skeleton01" in unit_log_text
    assert str(skeleton_work_root) in unit_log_text


def test_heudiconv_skeleton_multiple_paths_split_into_session_units(tmp_path: Path) -> None:
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
                "info_dir = out_dir / '.heudiconv' / 'skeleton' / session",
                "info_dir.mkdir(parents=True, exist_ok=True)",
                "(info_dir / 'heuristic.py').write_text('def infotodict(seqinfo):\\n    return {}\\n', encoding='utf-8')",
                "(info_dir / 'dicominfo.tsv').write_text(",
                "    f'series_id\\tprotocol_name\\tsample_path\\tsubject\\tsession\\n1\\tT1w\\t{sample_path}\\t{subject}\\t{session}\\n',",
                "    encoding='utf-8',",
                ")",
                "print('skeleton ok')",
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

    result = runner.invoke(
        app,
        [
            "heudiconv",
            "skeleton",
            "sample-ses-01",
            "sample-ses-02",
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Skeleton units: 2" in result.output

    heuristic_path = project_dir / "code" / "heudiconv" / "heuristic.py"
    dicominfo_root = project_dir / "code" / "heudiconv" / "dicominfo"
    dicominfo_path_one = dicominfo_root / "skeleton-ses01" / "dicominfo.tsv"
    dicominfo_path_two = dicominfo_root / "skeleton-ses02" / "dicominfo.tsv"
    state_path = project_dir / "state" / "heudiconv" / "skeleton.json"
    units_path = project_dir / "state" / "heudiconv" / "skeleton.tsv"
    skeleton_work_root = project_dir / "work" / "heudiconv" / "skeleton-work"

    assert heuristic_path.is_file()
    assert dicominfo_path_one.is_file()
    assert dicominfo_path_two.is_file()
    assert state_path.is_file()
    assert units_path.is_file()
    assert skeleton_work_root.is_dir()
    assert not (project_dir / "sourcedata" / "raw").exists()

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "succeeded"
    assert state["sample_paths"] == [str(sample_dir_one.resolve()), str(sample_dir_two.resolve())]
    assert state["artifacts"]["heuristic_template"] == str(heuristic_path)
    assert state["artifacts"]["dicom_inventory_dir"] == str(dicominfo_root)
    assert state["artifacts"]["skeleton_work_root"] == str(skeleton_work_root)
    assert state["artifacts"]["heudiconv_state"] == str(skeleton_work_root / ".heudiconv")
    assert state["unit_table_path"] == str(units_path)
    assert Path(state["unit_log_dir"]).is_dir()
    assert "units" not in state

    rows = _read_tsv_rows(units_path)
    assert [row["unit_name"] for row in rows] == ["skeleton-ses01", "skeleton-ses02"]
    assert [row["subject_label"] for row in rows] == ["skeleton01", "skeleton01"]
    assert [row["session_label"] for row in rows] == ["skeleton-ses01", "skeleton-ses02"]
    assert all(row["strategy"] == "generated_multi_session" for row in rows)
    assert all(row["status"] == "succeeded" for row in rows)
    assert all(Path(row["log_path"]).is_file() for row in rows)
    assert all(Path(row["log_path"]).parent == Path(state["unit_log_dir"]) for row in rows)


def test_heudiconv_skeleton_requires_reset_before_regenerating(tmp_path: Path) -> None:
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
                "info_dir = out_dir / '.heudiconv' / 'skeleton' / session",
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

    first = runner.invoke(app, ["heudiconv", "skeleton", "sample-ses-01", "--config", str(config_path)])
    assert first.exit_code == 0, first.output

    blocked = runner.invoke(app, ["heudiconv", "skeleton", "sample-ses-01", "--config", str(config_path)])
    assert blocked.exit_code == 2
    assert "Existing HeuDiConv skeleton state was found" in blocked.output

    allowed = runner.invoke(
        app,
        ["heudiconv", "skeleton", "sample-ses-01", "--config", str(config_path), "--reset"],
    )
    assert allowed.exit_code == 0, allowed.output


def test_heudiconv_skeleton_rejects_invalid_launcher_config(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    config_path = project_dir / "bidsflow.toml"
    _set_launcher(config_path, 'launcher = "heudiconv"')

    sample_dir = project_dir / "sourcedata" / "sample-ses-01"
    sample_dir.mkdir(parents=True)

    result = runner.invoke(
        app,
        [
            "heudiconv",
            "skeleton",
            "sample-ses-01",
            "--config",
            str(config_path),
            "--dry-run",
        ],
    )

    assert result.exit_code == 2
    assert "[heudiconv].launcher must be a non-empty list of strings." in result.output


def test_heudiconv_skeleton_rejects_sample_outside_configured_source_root(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    (project_dir / "sourcedata").mkdir(parents=True)
    outside_sample_dir = project_dir / "other-data" / "sample-ses-01"
    outside_sample_dir.mkdir(parents=True)

    result = runner.invoke(
        app,
        [
            "heudiconv",
            "skeleton",
            str(outside_sample_dir),
            "--config",
            str(project_dir / "bidsflow.toml"),
            "--dry-run",
        ],
    )

    assert result.exit_code == 2
    assert "Sample path must resolve under the configured source root" in result.output

