from __future__ import annotations

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


def test_heudiconv_convert_dry_run_shows_manifest_driven_commands(tmp_path: Path) -> None:
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
    assert "Conversion units: 2" in result.output
    assert "-s 001 -ss 01" in result.output
    assert "-s 001 -ss 02" in result.output


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


def test_heudiconv_convert_runs_launcher_and_cleans_execution_view(tmp_path: Path) -> None:
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

    state_root = project_dir / "state" / "heudiconv" / "convert-runs"
    run_dirs = sorted(path for path in state_root.iterdir() if path.is_dir())
    assert len(run_dirs) == 1
    run_root = run_dirs[0]
    assert not (run_root / "links").exists()

    state = json.loads((run_root / "convert.json").read_text(encoding="utf-8"))
    assert state["step"] == "convert"
    assert state["status"] == "succeeded"
    assert state["raw_bids_root"] == str(raw_root)
    assert state["execution_view_cleaned"] is True
    assert len(state["units"]) == 2
    assert state["units"][0]["execution_view_kind"] in {"symlink", "junction"}

    log_root = project_dir / "logs" / "heudiconv"
    log_files = list(log_root.glob("convert-*.log"))
    assert len(log_files) == 1
    log_text = log_files[0].read_text(encoding="utf-8")
    assert "convert ok" in log_text
    assert "--files" in log_text


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
