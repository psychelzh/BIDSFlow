from __future__ import annotations

import shlex
import shutil
from pathlib import Path

import typer
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from bidsflow.config.load import load_config
from bidsflow.config.models import Config
from bidsflow.core.stages import STAGES, StageId
from bidsflow.scheduler.sge import SGECliScheduler

app = typer.Typer(
    help="BIDSFlow: a staged Python CLI orchestrator for BIDS Apps.",
    no_args_is_help=True,
)
scheduler_app = typer.Typer(help="Scheduler planning and submission helpers.")
app.add_typer(scheduler_app, name="scheduler")
console = Console()


@app.command()
def init(path: Path = typer.Option(Path("."), help="Project root to initialize.")) -> None:
    """Initialize a BIDSFlow project directory."""
    console.print(f"[green]BIDSFlow[/green] project scaffold target: {path}")
    console.print("Project initialization logic is not implemented yet.")


@app.command()
def doctor(
    config: Path | None = typer.Option(
        None,
        exists=True,
        dir_okay=False,
        help="Optional config path to inspect.",
    ),
) -> None:
    """Inspect local execution prerequisites."""
    table = Table(title="BIDSFlow doctor")
    table.add_column("Check")
    table.add_column("Result")

    table.add_row("python", shutil.which("python") or "not found")
    table.add_row("qsub", shutil.which("qsub") or "not found")
    table.add_row("qstat", shutil.which("qstat") or "not found")

    if config is not None:
        loaded = load_config(config)
        table.add_row("config", str(config.resolve()))
        table.add_row("backend", loaded.execution.backend)
        table.add_row("scheduler", loaded.execution.scheduler)
        if loaded.execution.scheduler == "sge":
            table.add_row("sge driver", loaded.scheduler.sge.driver)
            table.add_row("sge queue", loaded.scheduler.sge.queue or "-")
            table.add_row("project root", str(loaded.project.root))

    console.print(table)


@app.command()
def validate(config: Path = typer.Option(..., exists=True, help="Path to TOML config.")) -> None:
    """Validate project configuration and stage inputs."""
    loaded = load_config(config)
    table = Table(title="Validated BIDSFlow config")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("config", str(config.resolve()))
    table.add_row("project root", str(loaded.project.root))
    table.add_row("backend", loaded.execution.backend)
    table.add_row("scheduler", loaded.execution.scheduler)
    if loaded.execution.scheduler == "sge":
        table.add_row("sge driver", loaded.scheduler.sge.driver)
        table.add_row("sge queue", loaded.scheduler.sge.queue or "-")
        table.add_row("sge parallel env", loaded.scheduler.sge.parallel_environment or "-")
    console.print(table)


@app.command()
def curate(
    config: Path = typer.Option(..., exists=True, help="Path to TOML config."),
    participant: str | None = typer.Option(None, help="Participant label, e.g. sub-001."),
) -> None:
    """Run HeuDiConv-backed curation."""
    console.print(f"Curate stage requested with config={config} participant={participant}")


@app.command()
def fmriprep(
    config: Path = typer.Option(..., exists=True, help="Path to TOML config."),
    participant: str | None = typer.Option(None, help="Participant label, e.g. sub-001."),
) -> None:
    """Run fMRIPrep stage."""
    console.print(f"fMRIPrep stage requested with config={config} participant={participant}")


@app.command()
def mriqc(
    config: Path = typer.Option(..., exists=True, help="Path to TOML config."),
    participant: str | None = typer.Option(None, help="Participant label, e.g. sub-001."),
) -> None:
    """Run MRIQC stage."""
    console.print(f"MRIQC stage requested with config={config} participant={participant}")


@app.command(name="xcpd")
def xcpd_cmd(
    config: Path = typer.Option(..., exists=True, help="Path to TOML config."),
    participant: str | None = typer.Option(None, help="Participant label, e.g. sub-001."),
) -> None:
    """Run XCP-D stage."""
    console.print(f"XCP-D stage requested with config={config} participant={participant}")


@app.command()
def qsiprep(
    config: Path = typer.Option(..., exists=True, help="Path to TOML config."),
    participant: str | None = typer.Option(None, help="Participant label, e.g. sub-001."),
) -> None:
    """Run QSIPrep stage."""
    console.print(f"QSIPrep stage requested with config={config} participant={participant}")


@app.command()
def qsirecon(
    config: Path = typer.Option(..., exists=True, help="Path to TOML config."),
    participant: str | None = typer.Option(None, help="Participant label, e.g. sub-001."),
) -> None:
    """Run QSIRecon stage."""
    console.print(f"QSIRecon stage requested with config={config} participant={participant}")


@app.command()
def status() -> None:
    """Display the current stage registry."""
    table = Table(title="BIDSFlow stages")
    table.add_column("Stage")
    table.add_column("Upstream")
    table.add_column("Products")
    for stage in STAGES.values():
        upstream = ", ".join(s.value for s in stage.upstream) or "-"
        products = ", ".join(stage.products)
        table.add_row(stage.id.value, upstream, products)
    console.print(table)


def _load_sge_scheduler(config: Path) -> tuple[Config, SGECliScheduler]:
    loaded = load_config(config)
    if loaded.execution.scheduler != "sge":
        raise typer.BadParameter(
            f"Config scheduler is '{loaded.execution.scheduler}', expected 'sge' for this command."
        )
    return loaded, SGECliScheduler(loaded.scheduler.sge)


@scheduler_app.command("plan-sge")
def scheduler_plan_sge(
    stage: StageId = typer.Argument(..., help="Stage id to schedule."),
    config: Path = typer.Option(..., exists=True, dir_okay=False, help="Path to TOML config."),
    participant: str | None = typer.Option(None, help="Participant label, e.g. sub-001."),
    hold_jid: str | None = typer.Option(None, help="Optional upstream SGE job id dependency."),
    submit: bool = typer.Option(
        False,
        help="Submit with qsub after rendering the plan instead of only printing it.",
    ),
) -> None:
    """Preview or submit an SGE qsub plan for a stage execution unit."""
    loaded, scheduler = _load_sge_scheduler(config)
    plan = scheduler.plan_stage_submission(
        config_path=config,
        config=loaded,
        stage=stage,
        participant=participant,
        hold_jid=hold_jid,
    )

    table = Table(title="SGE submission plan")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("stage", stage.value)
    table.add_row("participant", participant or "-")
    table.add_row("job name", plan.launch.job_name)
    table.add_row("script path", str(plan.script_path))
    table.add_row("stdout", str(plan.launch.stdout_path))
    table.add_row("stderr", str(plan.launch.stderr_path))
    table.add_row("qsub", shlex.join(plan.qsub_command))
    console.print(table)
    console.print(Syntax(plan.script_text, "bash", theme="ansi_dark", line_numbers=True))

    if submit:
        submitted = scheduler.submit(plan)
        console.print(f"Submitted SGE job {submitted.job_id}")


@scheduler_app.command("status-sge")
def scheduler_status_sge(
    job_id: str = typer.Option(..., help="SGE job id to inspect."),
    config: Path = typer.Option(..., exists=True, dir_okay=False, help="Path to TOML config."),
    accounting: bool = typer.Option(
        False,
        help="Fall back to qacct if the job is no longer present in qstat.",
    ),
) -> None:
    """Inspect the live or completed status of an SGE job."""
    _, scheduler = _load_sge_scheduler(config)
    job_status = scheduler.status(job_id)

    if job_status is not None:
        table = Table(title="SGE job status")
        table.add_column("Field")
        table.add_column("Value")
        table.add_row("job id", job_status.job_id)
        table.add_row("name", job_status.name)
        table.add_row("state", job_status.state)
        table.add_row("owner", job_status.owner or "-")
        table.add_row("queue", job_status.queue_name or "-")
        table.add_row("slots", str(job_status.slots) if job_status.slots is not None else "-")
        console.print(table)
        return

    if accounting:
        job_accounting = scheduler.accounting(job_id)
        if job_accounting is not None:
            table = Table(title="SGE job accounting")
            table.add_column("Field")
            table.add_column("Value")
            table.add_row("job id", job_accounting.job_id)
            table.add_row("exit status", job_accounting.exit_status or "-")
            table.add_row("failed", job_accounting.failed or "-")
            table.add_row("wallclock", job_accounting.wallclock or "-")
            table.add_row("cpu", job_accounting.cpu or "-")
            table.add_row("maxvmem", job_accounting.maxvmem or "-")
            console.print(table)
            return

    console.print(f"No SGE status information found for job {job_id}.")
    raise typer.Exit(code=1)


@scheduler_app.command("accounting-sge")
def scheduler_accounting_sge(
    job_id: str = typer.Option(..., help="SGE job id to inspect via qacct."),
    config: Path = typer.Option(..., exists=True, dir_okay=False, help="Path to TOML config."),
) -> None:
    """Inspect qacct information for a completed SGE job."""
    _, scheduler = _load_sge_scheduler(config)
    job_accounting = scheduler.accounting(job_id)
    if job_accounting is None:
        console.print(f"No qacct information found for job {job_id}.")
        raise typer.Exit(code=1)

    table = Table(title="SGE job accounting")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("job id", job_accounting.job_id)
    table.add_row("exit status", job_accounting.exit_status or "-")
    table.add_row("failed", job_accounting.failed or "-")
    table.add_row("wallclock", job_accounting.wallclock or "-")
    table.add_row("cpu", job_accounting.cpu or "-")
    table.add_row("maxvmem", job_accounting.maxvmem or "-")
    console.print(table)


@scheduler_app.command("cancel-sge")
def scheduler_cancel_sge(
    job_id: str = typer.Option(..., help="SGE job id to cancel."),
    config: Path = typer.Option(..., exists=True, dir_okay=False, help="Path to TOML config."),
) -> None:
    """Cancel an SGE job with qdel."""
    _, scheduler = _load_sge_scheduler(config)
    command = scheduler.cancel(job_id)
    console.print(f"Ran {shlex.join(command)}")


if __name__ == "__main__":
    app()
