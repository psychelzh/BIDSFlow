from __future__ import annotations

import csv
import json
from pathlib import Path
import sys

from typer.testing import CliRunner

from bidsflow.cli import app

runner = CliRunner()


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


def test_heudiconv_convert_dry_run_shows_summary_and_one_example(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"
    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    config_path = project_dir / "bidsflow.toml"
    _append_config(
        config_path,
        [
            "[heudiconv.manifest]",
            'template = "SUB{subject}_SES{session}"',
        ],
    )

    (project_dir / "sourcedata" / "SUB001_SES01").mkdir(parents=True)
    (project_dir / "sourcedata" / "SUB001_SES02").mkdir(parents=True)

    manifest_result = runner.invoke(app, ["heudiconv", "manifest", "--config", str(config_path)])
    assert manifest_result.exit_code == 0, manifest_result.output

    heuristic_path = _write_minimal_heuristic(project_dir)

    result = runner.invoke(
        app,
        ["heudiconv", "convert", "--config", str(config_path), "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert "Planned HeuDiConv convert run." in result.output
    assert str(project_dir / "code" / "heudiconv" / "manifest.tsv") in result.output
    assert str(heuristic_path) in result.output
    assert str(project_dir / "sourcedata" / "raw") in result.output
    assert str(project_dir / "state" / "heudiconv" / "convert.json") in result.output
    assert str(project_dir / "state" / "heudiconv" / "convert.tsv") in result.output
    assert "Conversion units: 2" in result.output
    assert "Example unit:" in result.output
    assert "SUB001_SES01: subject=001 session=01" in result.output
    assert "-s 001 -ss 01" in result.output
    assert "Additional units omitted: 1." in result.output


def test_heudiconv_convert_recomputes_manifest_status_from_manual_edits(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"
    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    config_path = project_dir / "bidsflow.toml"
    (project_dir / "sourcedata" / "SUB001").mkdir(parents=True)

    manifest_result = runner.invoke(app, ["heudiconv", "manifest", "--config", str(config_path)])
    assert manifest_result.exit_code == 0, manifest_result.output

    manifest_path = project_dir / "code" / "heudiconv" / "manifest.tsv"
    manifest_path.write_text(
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

    fake_launcher = project_dir / "fake_convert.py"
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

    result = runner.invoke(app, ["heudiconv", "convert", "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert "Completed managed HeuDiConv conversion." in result.output
    assert (project_dir / "sourcedata" / "raw" / "sub-001" / "marker.txt").is_file()


def test_heudiconv_convert_writes_current_state_and_unit_table(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"
    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    config_path = project_dir / "bidsflow.toml"
    _append_config(
        config_path,
        [
            "[heudiconv.manifest]",
            'template = "SUB{subject}_SES{session}"',
        ],
    )

    (project_dir / "sourcedata" / "SUB001_SES01").mkdir(parents=True)
    (project_dir / "sourcedata" / "SUB001_SES02").mkdir(parents=True)
    manifest_result = runner.invoke(app, ["heudiconv", "manifest", "--config", str(config_path)])
    assert manifest_result.exit_code == 0, manifest_result.output

    _write_minimal_heuristic(project_dir)

    fake_launcher = project_dir / "fake_convert.py"
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
                "print('convert ok')",
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

    result = runner.invoke(app, ["heudiconv", "convert", "--config", str(config_path)])
    assert result.exit_code == 0, result.output

    raw_root = project_dir / "sourcedata" / "raw"
    assert (raw_root / "sub-001" / "ses-01" / "marker.txt").is_file()
    assert (raw_root / "sub-001" / "ses-02" / "marker.txt").is_file()

    state_path = project_dir / "state" / "heudiconv" / "convert.json"
    units_path = project_dir / "state" / "heudiconv" / "convert.tsv"
    assert state_path.is_file()
    assert units_path.is_file()

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["workflow"] == "heudiconv"
    assert state["step"] == "convert"
    assert state["backend"] == "local"
    assert state["status"] == "succeeded"
    assert state["input_signature"].startswith("sha256:")
    assert state["started_at"] is not None
    assert state["finished_at"] is not None
    assert state["artifacts"]["raw_bids_dataset"] == str(raw_root)
    assert Path(state["unit_log_dir"]).is_dir()
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
    assert all(Path(row["log_path"]).parent == Path(state["unit_log_dir"]) for row in rows)

    unit_log_text = Path(rows[0]["log_path"]).read_text(encoding="utf-8")
    assert "convert ok" in unit_log_text
    assert "--files" in unit_log_text


def test_heudiconv_convert_rejects_manifest_that_still_needs_review(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"
    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    (project_dir / "sourcedata" / "SUB001_SES01").mkdir(parents=True)
    manifest_result = runner.invoke(
        app,
        ["heudiconv", "manifest", "--config", str(project_dir / "bidsflow.toml")],
    )
    assert manifest_result.exit_code == 0, manifest_result.output

    _write_minimal_heuristic(project_dir)

    result = runner.invoke(
        app,
        ["heudiconv", "convert", "--config", str(project_dir / "bidsflow.toml")],
    )

    assert result.exit_code == 2
    assert "manifest still needs review" in result.output.lower()


def test_heudiconv_convert_overwrites_current_state_and_keeps_unit_logs(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"
    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    config_path = project_dir / "bidsflow.toml"
    _append_config(
        config_path,
        [
            "[heudiconv.manifest]",
            'template = "SUB{subject}_SES{session}"',
        ],
    )

    (project_dir / "sourcedata" / "SUB001_SES01").mkdir(parents=True)
    manifest_result = runner.invoke(app, ["heudiconv", "manifest", "--config", str(config_path)])
    assert manifest_result.exit_code == 0, manifest_result.output
    _write_minimal_heuristic(project_dir)

    flaky_launcher = project_dir / "flaky_convert.py"
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

    first_result = runner.invoke(app, ["heudiconv", "convert", "--config", str(config_path)])
    assert first_result.exit_code == 2
    state_path = project_dir / "state" / "heudiconv" / "convert.json"
    units_path = project_dir / "state" / "heudiconv" / "convert.tsv"
    first_state = json.loads(state_path.read_text(encoding="utf-8"))
    first_rows = _read_tsv_rows(units_path)
    assert first_state["status"] == "failed"
    assert first_rows[0]["status"] == "failed"
    first_log_dir = Path(first_state["unit_log_dir"])

    second_result = runner.invoke(app, ["heudiconv", "convert", "--config", str(config_path)])
    assert second_result.exit_code == 0, second_result.output

    second_state = json.loads(state_path.read_text(encoding="utf-8"))
    second_rows = _read_tsv_rows(units_path)
    assert second_state["status"] == "succeeded"
    assert second_rows[0]["status"] == "succeeded"
    second_log_dir = Path(second_state["unit_log_dir"])

    assert first_log_dir.is_dir()
    assert second_log_dir.is_dir()
    assert first_log_dir != second_log_dir
    assert Path(first_rows[0]["log_path"]).is_file()
    assert Path(second_rows[0]["log_path"]).is_file()
