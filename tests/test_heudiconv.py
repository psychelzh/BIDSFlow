from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from bidsflow.cli import app
from bidsflow.config.load import load_config
from bidsflow.core.stages import StageId
from bidsflow.scheduler.sge import SGECliScheduler
from bidsflow.stages.heudiconv import discover_scope_units


def _write_heudiconv_config(tmp_path: Path, *, scheduler: str = "local") -> Path:
    project_root = tmp_path / "project"
    source_root = project_root / "sourcedata" / "dicom" / "sub-001" / "ses-01" / "scanner"
    source_root.mkdir(parents=True, exist_ok=True)
    (source_root / "IM-0001.dcm").write_text("mock", encoding="utf-8")

    heuristic = project_root / "code" / "heuristics" / "heuristic.py"
    heuristic.parent.mkdir(parents=True, exist_ok=True)
    heuristic.write_text("def infotodict(seqinfo):\n    return {}\n", encoding="utf-8")

    config_path = project_root / "bidsflow.toml"
    config_path.write_text(
        "\n".join(
            [
                "[project]",
                'name = "HeuDiConv test project"',
                'root = "."',
                'sourcedata_root = "sourcedata/dicom"',
                'bids_root = "sourcedata/raw"',
                'derivatives_root = "derivatives"',
                "",
                "[execution]",
                'backend = "native"',
                f'scheduler = "{scheduler}"',
                'work_root = "work"',
                'logs_root = "logs"',
                'state_root = "state"',
                "",
                "[heudiconv]",
                'enabled = true',
                'executable = "heudiconv"',
                'dicom_dir_template = "{bids_subject}/{bids_session}/*/*.dcm"',
                'heuristic = "code/heuristics/heuristic.py"',
                'outdir = "sourcedata/raw"',
                'converter = "dcm2niix"',
                'overwrite = true',
                "",
                "[scheduler.sge.default_resources]",
                "slots = 1",
                'walltime = "00:10:00"',
                'memory = "1G"',
                "",
                "[scheduler.sge.resource_map]",
                'walltime = "h_rt"',
                'memory = "mem_free"',
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def test_discover_scope_units_finds_subject_and_session(tmp_path: Path) -> None:
    config_path = _write_heudiconv_config(tmp_path)
    config = load_config(config_path)

    units = discover_scope_units(
        config,
        subject_label=None,
        session_label=None,
        all_units=True,
    )

    assert len(units) == 1
    assert units[0].subject_label == "001"
    assert units[0].session_label == "01"


def test_heudiconv_bootstrap_dry_run_builds_convertall_command(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    config_path = _write_heudiconv_config(tmp_path)

    monkeypatch.setattr("bidsflow.stages.heudiconv.shutil.which", lambda _: "/usr/bin/mock")

    result = runner.invoke(
        app,
        [
            "heudiconv",
            "bootstrap",
            "--config",
            str(config_path),
            "--subject-label",
            "sub-001",
            "--session-label",
            "ses-01",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "HeuDiConv bootstrap preview" in result.stdout
    assert "convertall" in result.stdout
    assert "-c none" in result.stdout


def test_heudiconv_run_dry_run_uses_real_command(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    config_path = _write_heudiconv_config(tmp_path)

    monkeypatch.setattr("bidsflow.stages.heudiconv.shutil.which", lambda _: "/usr/bin/mock")

    result = runner.invoke(
        app,
        [
            "heudiconv",
            "run",
            "--config",
            str(config_path),
            "--subject-label",
            "sub-001",
            "--session-label",
            "ses-01",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "HeuDiConv run preview" in result.stdout
    assert "heudiconv -d" in result.stdout
    assert "-f" in result.stdout
    assert "-s 001" in result.stdout
    assert "-ss 01" in result.stdout
    assert "sub-001/ses-01" in result.stdout


def test_heudiconv_sge_plan_uses_real_command(monkeypatch, tmp_path: Path) -> None:
    config_path = _write_heudiconv_config(tmp_path, scheduler="sge")
    config = load_config(config_path)
    scheduler = SGECliScheduler(config.scheduler.sge)

    monkeypatch.setattr("bidsflow.stages.heudiconv.shutil.which", lambda _: "/usr/bin/mock")

    plan = scheduler.plan_stage_submission(
        config_path=config_path,
        config=config,
        stage=StageId.HEUDICONV,
        subject_label="001",
        session_label="01",
    )

    assert plan.launch.job_name == "bidsflow-heudiconv-sub-001-ses-01"
    assert plan.launch.command[0] == "heudiconv"
    assert "-d" in plan.launch.command
    assert "-s" in plan.launch.command
    assert "-ss" in plan.launch.command
    assert "-b" in plan.launch.command
    assert "mkdir -p" in plan.script_text
