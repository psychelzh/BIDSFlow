from __future__ import annotations

from pathlib import Path
import subprocess

from typer.testing import CliRunner

from bidsflow.cli import app
from bidsflow.config.load import load_config
from bidsflow.core.stages import StageId
from bidsflow.scheduler.models import SubmittedJob
from bidsflow.scheduler.sge import SGECliScheduler


def test_plan_stage_submission_renders_qsub_command_and_script() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config_path = repo_root / "examples" / "project.toml"
    config = load_config(config_path)

    scheduler = SGECliScheduler(config.scheduler.sge)
    plan = scheduler.plan_stage_submission(
        config_path=config_path,
        config=config,
        stage=StageId.FMRIPREP,
        participant="sub-001",
        hold_jid="1234",
    )

    assert plan.launch.job_name == "bidsflow-fmriprep-sub-001"
    assert plan.script_path.name == "bidsflow-fmriprep-sub-001.sh"
    assert plan.launch.stdout_path.name == "bidsflow-fmriprep-sub-001.out"
    assert plan.launch.stderr_path.name == "bidsflow-fmriprep-sub-001.err"

    assert plan.qsub_command[:4] == ("qsub", "-terse", "-N", "bidsflow-fmriprep-sub-001")
    assert "-cwd" in plan.qsub_command
    assert "-q" in plan.qsub_command
    assert "short.q" in plan.qsub_command
    assert "-P" not in plan.qsub_command
    assert "-pe" not in plan.qsub_command
    assert "-hold_jid" in plan.qsub_command
    assert "1234" in plan.qsub_command

    resources = plan.qsub_command[plan.qsub_command.index("-l") + 1]
    assert "h_rt=00:10:00" in resources
    assert "mem_free=1G" in resources

    assert "#!/bin/sh" in plan.script_text
    assert "set -eu" in plan.script_text
    assert "bidsflow.cli fmriprep --config" in plan.script_text
    assert "--participant sub-001" in plan.script_text
    assert "--scheduler local" in plan.script_text


def test_parse_job_id_uses_first_token() -> None:
    job_id = SGECliScheduler.parse_job_id("12345.cluster.example\n")
    assert job_id == "12345.cluster.example"


def test_parse_qstat_xml_extracts_job_status() -> None:
    xml_text = """
    <job_info>
      <queue_info>
        <job_list state="running">
          <JB_job_number>12345</JB_job_number>
          <JB_name>bidsflow-fmriprep-sub-001</JB_name>
          <JB_owner>liang</JB_owner>
          <state>r</state>
          <queue_name>all.q@node01</queue_name>
          <slots>8</slots>
        </job_list>
      </queue_info>
    </job_info>
    """

    status = SGECliScheduler.parse_qstat_xml(xml_text, job_id="12345")

    assert status is not None
    assert status.job_id == "12345"
    assert status.name == "bidsflow-fmriprep-sub-001"
    assert status.owner == "liang"
    assert status.state == "r"
    assert status.queue_name == "all.q@node01"
    assert status.slots == 8


def test_parse_qacct_output_extracts_accounting() -> None:
    qacct_text = """
    ==============================================================
    qname        all.q
    hostname     node01
    jobname      bidsflow-fmriprep-sub-001
    jobnumber    12345
    failed       0
    exit_status  0
    ru_wallclock 3600
    cpu          120.500
    maxvmem      31.500G
    """

    accounting = SGECliScheduler.parse_qacct_output(qacct_text, job_id="12345")

    assert accounting is not None
    assert accounting.job_id == "12345"
    assert accounting.failed == "0"
    assert accounting.exit_status == "0"
    assert accounting.wallclock == "3600"
    assert accounting.cpu == "120.500"
    assert accounting.maxvmem == "31.500G"


def test_accounting_returns_none_when_site_accounting_is_unavailable(monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config_path = repo_root / "examples" / "project.toml"
    config = load_config(config_path)
    scheduler = SGECliScheduler(config.scheduler.sge)

    monkeypatch.setattr("bidsflow.scheduler.sge.shutil.which", lambda _: "/usr/bin/mock")

    def fake_run(
        command: tuple[str, ...],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(
            1,
            command,
            output="no jobs running since startup\n",
            stderr="/var/lib/gridengine/common/accounting: No such file or directory\n",
        )

    monkeypatch.setattr("bidsflow.scheduler.sge.subprocess.run", fake_run)

    assert scheduler.accounting("12345") is None


def test_submit_and_cancel_use_external_commands(monkeypatch, tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config_path = repo_root / "examples" / "project.toml"
    config = load_config(config_path)
    scheduler = SGECliScheduler(config.scheduler.sge)
    plan = scheduler.plan_stage_submission(
        config_path=config_path,
        config=config,
        stage=StageId.FMRIPREP,
        participant="sub-001",
    )
    plan = type(plan)(
        launch=plan.launch,
        resources=plan.resources,
        script_path=tmp_path / plan.script_path.name,
        script_text=plan.script_text,
        qsub_command=tuple(
            str(tmp_path / plan.script_path.name) if item == str(plan.script_path) else item
            for item in plan.qsub_command
        ),
    )

    monkeypatch.setattr("bidsflow.scheduler.sge.shutil.which", lambda _: "/usr/bin/mock")

    commands: list[tuple[str, ...]] = []

    def fake_run(
        command: tuple[str, ...],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        stdout = "12345.cluster.example\n" if command[0] == "qsub" else ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr("bidsflow.scheduler.sge.subprocess.run", fake_run)

    submitted = scheduler.submit(plan)
    cancel_command = scheduler.cancel("12345.cluster.example")

    assert submitted.job_id == "12345.cluster.example"
    assert plan.script_path.exists()
    assert commands[0][0] == "qsub"
    assert cancel_command == ("qdel", "12345.cluster.example")
    assert commands[1] == cancel_command


def test_config_validate_command_uses_new_namespace() -> None:
    runner = CliRunner()
    repo_root = Path(__file__).resolve().parents[1]
    config_path = repo_root / "examples" / "project.toml"

    result = runner.invoke(app, ["config", "validate", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Validated BIDSFlow config" in result.stdout
    assert "scheduler" in result.stdout


def test_stage_dry_run_uses_configured_sge_scheduler() -> None:
    runner = CliRunner()
    repo_root = Path(__file__).resolve().parents[1]
    config_path = repo_root / "examples" / "project.toml"

    result = runner.invoke(
        app,
        [
            "fmriprep",
            "--config",
            str(config_path),
            "--participant",
            "sub-001",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "SGE stage run preview" in result.stdout
    assert "qsub" in result.stdout
    assert "--scheduler local" in result.stdout


def test_stage_run_submits_sge_job_from_config(monkeypatch) -> None:
    runner = CliRunner()
    repo_root = Path(__file__).resolve().parents[1]
    config_path = repo_root / "examples" / "project.toml"

    monkeypatch.setattr(
        "bidsflow.scheduler.sge.SGECliScheduler.submit",
        lambda self, plan: SubmittedJob(
            job_id="12345.cluster.example",
            script_path=plan.script_path,
            qsub_command=plan.qsub_command,
        ),
    )

    result = runner.invoke(
        app,
        [
            "fmriprep",
            "--config",
            str(config_path),
            "--participant",
            "sub-001",
        ],
    )

    assert result.exit_code == 0
    assert "Submitted SGE job 12345.cluster.example" in result.stdout


def test_stage_run_supports_local_scheduler_override() -> None:
    runner = CliRunner()
    repo_root = Path(__file__).resolve().parents[1]
    config_path = repo_root / "examples" / "project.toml"

    result = runner.invoke(
        app,
        [
            "fmriprep",
            "--config",
            str(config_path),
            "--participant",
            "sub-001",
            "--scheduler",
            "local",
        ],
    )

    assert result.exit_code == 0
    assert "fMRIPrep stage requested" in result.stdout


def test_validate_stage_rejects_participant_argument() -> None:
    runner = CliRunner()
    repo_root = Path(__file__).resolve().parents[1]
    config_path = repo_root / "examples" / "project.toml"

    result = runner.invoke(
        app,
        [
            "validate",
            "--config",
            str(config_path),
            "--participant",
            "sub-001",
        ],
    )

    assert result.exit_code == 2
    assert "does not accept --participant" in result.stdout
