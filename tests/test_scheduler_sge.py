from __future__ import annotations

from pathlib import Path
import subprocess

from typer.testing import CliRunner

from bidsflow.cli import app
from bidsflow.config.load import load_config
from bidsflow.core.stages import StageId
from bidsflow.scheduler.models import SGEAccounting, SGEJobStatus
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

    assert "set -euo pipefail" in plan.script_text
    assert "bidsflow.cli fmriprep --config" in plan.script_text
    assert "--participant sub-001" in plan.script_text


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


def test_scheduler_status_and_accounting_cli_commands(monkeypatch) -> None:
    runner = CliRunner()
    repo_root = Path(__file__).resolve().parents[1]
    config_path = repo_root / "examples" / "project.toml"

    monkeypatch.setattr(
        "bidsflow.scheduler.sge.SGECliScheduler.status",
        lambda self, job_id: SGEJobStatus(
            job_id=job_id,
            name="bidsflow-fmriprep-sub-001",
            state="r",
            owner="liang",
            queue_name="all.q@node01",
            slots=8,
        ),
    )
    monkeypatch.setattr(
        "bidsflow.scheduler.sge.SGECliScheduler.accounting",
        lambda self, job_id: SGEAccounting(
            job_id=job_id,
            exit_status="0",
            failed="0",
            wallclock="3600",
            cpu="120.500",
            maxvmem="31.500G",
        ),
    )
    monkeypatch.setattr(
        "bidsflow.scheduler.sge.SGECliScheduler.cancel",
        lambda self, job_id: ("qdel", job_id),
    )

    status_result = runner.invoke(
        app,
        [
            "scheduler",
            "status-sge",
            "--job-id",
            "12345",
            "--config",
            str(config_path),
        ],
    )
    accounting_result = runner.invoke(
        app,
        [
            "scheduler",
            "accounting-sge",
            "--job-id",
            "12345",
            "--config",
            str(config_path),
        ],
    )
    cancel_result = runner.invoke(
        app,
        [
            "scheduler",
            "cancel-sge",
            "--job-id",
            "12345",
            "--config",
            str(config_path),
        ],
    )

    assert status_result.exit_code == 0
    assert "SGE job status" in status_result.stdout
    assert "bidsflow-fmriprep-sub-001" in status_result.stdout

    assert accounting_result.exit_code == 0
    assert "SGE job accounting" in accounting_result.stdout
    assert "31.500G" in accounting_result.stdout

    assert cancel_result.exit_code == 0
    assert "qdel 12345" in cancel_result.stdout
