from __future__ import annotations

import shlex
import shutil
from pathlib import Path
from typing import Literal

import typer
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from bidsflow.config.load import load_config
from bidsflow.config.models import Config
from bidsflow.core.stages import STAGES, StageId
from bidsflow.scheduler.models import SubmittedJob
from bidsflow.scheduler.sge import SGECliScheduler

SchedulerChoice = Literal["local", "sge"]

app = typer.Typer(
    help="BIDSFlow: a staged Python CLI orchestrator for BIDS Apps.",
    no_args_is_help=True,
)
config_app = typer.Typer(help="Configuration helpers.")
app.add_typer(config_app, name="config")
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
            table.add_row("sge queue", loaded.scheduler.sge.queue or "-")
            table.add_row("project root", str(loaded.project.root))

    console.print(table)


@config_app.command("validate")
def config_validate(config: Path = typer.Option(..., exists=True, help="Path to TOML config.")) -> None:
    """Validate project configuration and execution settings."""
    loaded = load_config(config)
    table = Table(title="Validated BIDSFlow config")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("config", str(config.resolve()))
    table.add_row("project root", str(loaded.project.root))
    table.add_row("backend", loaded.execution.backend)
    table.add_row("scheduler", loaded.execution.scheduler)
    if loaded.execution.scheduler == "sge":
        table.add_row("sge queue", loaded.scheduler.sge.queue or "-")
        table.add_row("sge parallel env", loaded.scheduler.sge.parallel_environment or "-")
    console.print(table)


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


def _resolve_scheduler(config: Config, scheduler: SchedulerChoice | None) -> SchedulerChoice:
    return config.execution.scheduler if scheduler is None else scheduler


def _ensure_stage_scope(stage: StageId, participant: str | None) -> None:
    stage_spec = STAGES[stage]
    if stage_spec.scope == "dataset" and participant is not None:
        console.print(f"Stage '{stage.value}' has dataset scope and does not accept --participant.")
        raise typer.Exit(code=2)


def _build_local_stage_command(
    *,
    stage: StageId,
    config_path: Path,
    participant: str | None,
) -> tuple[str, ...]:
    command = (
        "bidsflow",
        stage.value,
        "--config",
        str(config_path.resolve()),
        "--scheduler",
        "local",
    )
    if participant is None:
        return command
    return (*command, "--participant", participant)


def _print_local_stage_preview(
    *,
    stage: StageId,
    config_path: Path,
    participant: str | None,
    loaded: Config,
) -> None:
    command = _build_local_stage_command(
        stage=stage,
        config_path=config_path,
        participant=participant,
    )
    table = Table(title="Local stage run preview")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("stage", stage.value)
    table.add_row("participant", participant or "-")
    table.add_row("scheduler", "local")
    table.add_row("cwd", str(loaded.project.root))
    table.add_row("command", shlex.join(command))
    console.print(table)


def _run_local_stage(stage: StageId, config_path: Path, participant: str | None) -> None:
    stage_spec = STAGES[stage]
    console.print(
        f"{stage_spec.label} stage requested with config={config_path.resolve()} "
        f"participant={participant or '-'}"
    )


def _load_sge_scheduler(config: Config) -> SGECliScheduler:
    return SGECliScheduler(config.scheduler.sge)


def _print_sge_stage_preview(
    *,
    stage: StageId,
    participant: str | None,
    plan_script: str,
    plan_command: tuple[str, ...],
    launch_command: tuple[str, ...],
    script_path: Path,
    stdout_path: Path,
    stderr_path: Path,
) -> None:
    table = Table(title="SGE stage run preview")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("stage", stage.value)
    table.add_row("participant", participant or "-")
    table.add_row("scheduler", "sge")
    table.add_row("script path", str(script_path))
    table.add_row("stdout", str(stdout_path))
    table.add_row("stderr", str(stderr_path))
    table.add_row("command", shlex.join(launch_command))
    table.add_row("qsub", shlex.join(plan_command))
    console.print(table)
    console.print(Syntax(plan_script, "sh", theme="ansi_dark", line_numbers=True))


def _run_stage(
    *,
    stage: StageId,
    config: Path,
    participant: str | None,
    scheduler: SchedulerChoice | None,
    dry_run: bool,
    hold_jid: str | None,
) -> None:
    loaded = load_config(config)
    _ensure_stage_scope(stage, participant)
    effective_scheduler = _resolve_scheduler(loaded, scheduler)

    if effective_scheduler == "local":
        if hold_jid is not None:
            console.print("--hold-jid is only supported when --scheduler sge is active.")
            raise typer.Exit(code=2)
        if dry_run:
            _print_local_stage_preview(
                stage=stage,
                config_path=config,
                participant=participant,
                loaded=loaded,
            )
            return
        _run_local_stage(stage=stage, config_path=config, participant=participant)
        return

    scheduler_runner = _load_sge_scheduler(loaded)
    plan = scheduler_runner.plan_stage_submission(
        config_path=config,
        config=loaded,
        stage=stage,
        participant=participant,
        hold_jid=hold_jid,
    )
    _print_sge_stage_preview(
        stage=stage,
        participant=participant,
        plan_script=plan.script_text,
        plan_command=plan.qsub_command,
        launch_command=plan.launch.command,
        script_path=plan.script_path,
        stdout_path=plan.launch.stdout_path,
        stderr_path=plan.launch.stderr_path,
    )
    if dry_run:
        return

    submitted: SubmittedJob = scheduler_runner.submit(plan)
    console.print(f"Submitted SGE job {submitted.job_id}")


@app.command()
def curate(
    config: Path = typer.Option(..., exists=True, dir_okay=False, help="Path to TOML config."),
    participant: str | None = typer.Option(None, help="Participant label, e.g. sub-001."),
    scheduler: SchedulerChoice | None = typer.Option(
        None,
        help="Override the scheduler declared in the config for this invocation.",
    ),
    dry_run: bool = typer.Option(False, help="Preview the stage execution without running it."),
    hold_jid: str | None = typer.Option(
        None,
        help="Optional upstream SGE job id dependency when using --scheduler sge.",
    ),
) -> None:
    """Run or preview the HeuDiConv-backed curation stage."""
    _run_stage(
        stage=StageId.CURATE,
        config=config,
        participant=participant,
        scheduler=scheduler,
        dry_run=dry_run,
        hold_jid=hold_jid,
    )


@app.command(name="validate")
def validate_stage(
    config: Path = typer.Option(..., exists=True, dir_okay=False, help="Path to TOML config."),
    participant: str | None = typer.Option(None, help="Participant label, e.g. sub-001."),
    scheduler: SchedulerChoice | None = typer.Option(
        None,
        help="Override the scheduler declared in the config for this invocation.",
    ),
    dry_run: bool = typer.Option(False, help="Preview the stage execution without running it."),
    hold_jid: str | None = typer.Option(
        None,
        help="Optional upstream SGE job id dependency when using --scheduler sge.",
    ),
) -> None:
    """Run or preview the validation stage."""
    _run_stage(
        stage=StageId.VALIDATE,
        config=config,
        participant=participant,
        scheduler=scheduler,
        dry_run=dry_run,
        hold_jid=hold_jid,
    )


@app.command()
def fmriprep(
    config: Path = typer.Option(..., exists=True, dir_okay=False, help="Path to TOML config."),
    participant: str | None = typer.Option(None, help="Participant label, e.g. sub-001."),
    scheduler: SchedulerChoice | None = typer.Option(
        None,
        help="Override the scheduler declared in the config for this invocation.",
    ),
    dry_run: bool = typer.Option(False, help="Preview the stage execution without running it."),
    hold_jid: str | None = typer.Option(
        None,
        help="Optional upstream SGE job id dependency when using --scheduler sge.",
    ),
) -> None:
    """Run or preview the fMRIPrep stage."""
    _run_stage(
        stage=StageId.FMRIPREP,
        config=config,
        participant=participant,
        scheduler=scheduler,
        dry_run=dry_run,
        hold_jid=hold_jid,
    )


@app.command()
def mriqc(
    config: Path = typer.Option(..., exists=True, dir_okay=False, help="Path to TOML config."),
    participant: str | None = typer.Option(None, help="Participant label, e.g. sub-001."),
    scheduler: SchedulerChoice | None = typer.Option(
        None,
        help="Override the scheduler declared in the config for this invocation.",
    ),
    dry_run: bool = typer.Option(False, help="Preview the stage execution without running it."),
    hold_jid: str | None = typer.Option(
        None,
        help="Optional upstream SGE job id dependency when using --scheduler sge.",
    ),
) -> None:
    """Run or preview the MRIQC stage."""
    _run_stage(
        stage=StageId.MRIQC,
        config=config,
        participant=participant,
        scheduler=scheduler,
        dry_run=dry_run,
        hold_jid=hold_jid,
    )


@app.command(name="xcpd")
def xcpd_cmd(
    config: Path = typer.Option(..., exists=True, dir_okay=False, help="Path to TOML config."),
    participant: str | None = typer.Option(None, help="Participant label, e.g. sub-001."),
    scheduler: SchedulerChoice | None = typer.Option(
        None,
        help="Override the scheduler declared in the config for this invocation.",
    ),
    dry_run: bool = typer.Option(False, help="Preview the stage execution without running it."),
    hold_jid: str | None = typer.Option(
        None,
        help="Optional upstream SGE job id dependency when using --scheduler sge.",
    ),
) -> None:
    """Run or preview the XCP-D stage."""
    _run_stage(
        stage=StageId.XCPD,
        config=config,
        participant=participant,
        scheduler=scheduler,
        dry_run=dry_run,
        hold_jid=hold_jid,
    )


@app.command()
def qsiprep(
    config: Path = typer.Option(..., exists=True, dir_okay=False, help="Path to TOML config."),
    participant: str | None = typer.Option(None, help="Participant label, e.g. sub-001."),
    scheduler: SchedulerChoice | None = typer.Option(
        None,
        help="Override the scheduler declared in the config for this invocation.",
    ),
    dry_run: bool = typer.Option(False, help="Preview the stage execution without running it."),
    hold_jid: str | None = typer.Option(
        None,
        help="Optional upstream SGE job id dependency when using --scheduler sge.",
    ),
) -> None:
    """Run or preview the QSIPrep stage."""
    _run_stage(
        stage=StageId.QSIPREP,
        config=config,
        participant=participant,
        scheduler=scheduler,
        dry_run=dry_run,
        hold_jid=hold_jid,
    )


@app.command()
def qsirecon(
    config: Path = typer.Option(..., exists=True, dir_okay=False, help="Path to TOML config."),
    participant: str | None = typer.Option(None, help="Participant label, e.g. sub-001."),
    scheduler: SchedulerChoice | None = typer.Option(
        None,
        help="Override the scheduler declared in the config for this invocation.",
    ),
    dry_run: bool = typer.Option(False, help="Preview the stage execution without running it."),
    hold_jid: str | None = typer.Option(
        None,
        help="Optional upstream SGE job id dependency when using --scheduler sge.",
    ),
) -> None:
    """Run or preview the QSIRecon stage."""
    _run_stage(
        stage=StageId.QSIRECON,
        config=config,
        participant=participant,
        scheduler=scheduler,
        dry_run=dry_run,
        hold_jid=hold_jid,
    )


if __name__ == "__main__":
    app()
