from __future__ import annotations

import json
from pathlib import Path
import sys

from typer.testing import CliRunner

from bidsflow.cli import app

runner = CliRunner()


def test_heudiconv_bootstrap_dry_run_uses_default_launcher(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    sample_dir = project_dir / "incoming" / "sample"
    sample_dir.mkdir(parents=True)

    result = runner.invoke(app, ["heudiconv", "bootstrap", str(sample_dir), "--config", str(project_dir / "bidsflow.toml"), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Planned HeuDiConv bootstrap command:" in result.output
    assert "heudiconv --files" in result.output
    assert str(project_dir / "code" / "heudiconv" / "heuristic.py") in result.output
    assert str(project_dir / "state" / "heudiconv" / "bootstrap.json") in result.output


def test_heudiconv_bootstrap_runs_with_launcher_and_records_outputs(tmp_path: Path) -> None:
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
                "info_dir = out_dir / '.heudiconv' / 'bootstrap' / 'info'",
                "info_dir.mkdir(parents=True, exist_ok=True)",
                "(info_dir / 'heuristic.py').write_text('def infotodict(seqinfo):\\n    return {}\\n', encoding='utf-8')",
                "(info_dir / 'dicominfo.tsv').write_text('series_id\\tprotocol_name\\n1\\tT1w\\n', encoding='utf-8')",
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

    sample_dir = project_dir / "incoming" / "sample"
    sample_dir.mkdir(parents=True)

    result = runner.invoke(app, ["heudiconv", "bootstrap", str(sample_dir), "--config", str(config_path)])

    assert result.exit_code == 0, result.output

    heuristic_path = project_dir / "code" / "heudiconv" / "heuristic.py"
    dicominfo_path = project_dir / "code" / "heudiconv" / "dicominfo.tsv"
    state_path = project_dir / "state" / "heudiconv" / "bootstrap.json"
    log_path = project_dir / "logs" / "heudiconv" / "bootstrap.log"

    assert heuristic_path.is_file()
    assert dicominfo_path.is_file()
    assert state_path.is_file()
    assert log_path.is_file()
    assert "bootstrap ok" in log_path.read_text(encoding="utf-8")

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "succeeded"
    assert Path(state["launcher"][0]).resolve() == Path(sys.executable).resolve()
    assert state["artifacts"]["heuristic_template"] == str(heuristic_path)
    assert state["artifacts"]["dicom_inventory"] == str(dicominfo_path)


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
                "info_dir = out_dir / '.heudiconv' / 'bootstrap' / 'info'",
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

    sample_dir = project_dir / "incoming" / "sample"
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
