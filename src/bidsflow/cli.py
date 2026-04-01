from __future__ import annotations

import os
import shlex
import shutil
import subprocess
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
from bidsflow.stages.heudiconv import (
    CommandSpec,
    ReadinessResult,
    ScopeUnit,
    build_bootstrap_command,
    build_run_command,
    check_readiness,
    find_bootstrap_dicominfo,
    find_bootstrap_heuristic,
    format_session_label,
    format_subject_label,
)

SchedulerChoice = Literal["local", "sge"]
BackendChoice = Literal["docker", "apptainer", "native"]

app = typer.Typer(
    help="BIDSFlow: a staged Python CLI orchestrator for BIDS Apps.",
    no_args_is_help=True,
)
config_app = typer.Typer(help="Configuration helpers.")
heudiconv_app = typer.Typer(help="HeuDiConv stage helpers.")
app.add_typer(config_app, name="config")
app.add_typer(heudiconv_app, name="heudiconv")
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
    table.add_row("heudiconv", shutil.which("heudiconv") or "not found")
    table.add_row("apptainer", shutil.which("apptainer") or "not found")
    table.add_row("docker", shutil.which("docker") or "not found")
    table.add_row("qsub", shutil.which("qsub") or "not found")
    table.add_row("qstat", shutil.which("qstat") or "not found")

    if config is not None:
        loaded = load_config(config)
        table.add_row("config", str(config.resolve()))
        table.add_row("backend", loaded.execution.backend)
        table.add_row("scheduler", loaded.execution.scheduler)
        table.add_row("project root", str(loaded.project.root))
        table.add_row("sourcedata root", str(loaded.project.sourcedata_root))
        if loaded.execution.scheduler == "sge":
            table.add_row("sge queue", loaded.scheduler.sge.queue or "-")

    console.print(table)


@config_app.command("validate")
def config_validate(
    config: Path = typer.Option(
        ...,
        exists=True,
        dir_okay=False,
        help="Path to TOML config.",
    ),
) -> None:
    """Validate project configuration and execution settings."""
    loaded = load_config(config)
    table = Table(title="Validated BIDSFlow config")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("config", str(config.resolve()))
    table.add_row("project root", str(loaded.project.root))
    table.add_row("sourcedata root", str(loaded.project.sourcedata_root))
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


def _apply_execution_overrides(
    config: Config,
    *,
    backend: BackendChoice | None = None,
    scheduler: SchedulerChoice | None = None,
) -> Config:
    if backend is not None:
        config.execution.backend = backend
    if scheduler is not None:
        config.execution.scheduler = scheduler
    return config


def _ensure_stage_scope(stage: StageId, subject_label: str | None) -> None:
    stage_spec = STAGES[stage]
    if stage_spec.scope == "dataset" and subject_label is not None:
        console.print(f"Stage '{stage.value}' has dataset scope and does not accept --subject-label.")
        raise typer.Exit(code=2)


def _build_local_stage_command(
    *,
    stage: StageId,
    config_path: Path,
    subject_label: str | None,
) -> tuple[str, ...]:
    command = (
        "bidsflow",
        stage.value,
        "--config",
        str(config_path.resolve()),
        "--scheduler",
        "local",
    )
    if subject_label is None:
        return command
    return (*command, "--subject-label", subject_label)


def _format_scope(subject_label: str | None, session_label: str | None = None) -> str:
    if subject_label is None:
        return "-"
    if session_label is None:
        return format_subject_label(subject_label)
    return f"{format_subject_label(subject_label)} / {format_session_label(session_label)}"


def _print_local_stage_preview(
    *,
    stage: StageId,
    config_path: Path,
    subject_label: str | None,
    loaded: Config,
) -> None:
    command = _build_local_stage_command(
        stage=stage,
        config_path=config_path,
        subject_label=subject_label,
    )
    table = Table(title="Local stage run preview")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("stage", stage.value)
    table.add_row("scope", _format_scope(subject_label))
    table.add_row("scheduler", "local")
    table.add_row("cwd", str(loaded.project.root))
    table.add_row("command", shlex.join(command))
    console.print(table)


def _run_local_stage(stage: StageId, config_path: Path, subject_label: str | None) -> None:
    stage_spec = STAGES[stage]
    console.print(
        f"{stage_spec.label} stage requested with config={config_path.resolve()} "
        f"scope={_format_scope(subject_label)}"
    )


def _load_sge_scheduler(config: Config) -> SGECliScheduler:
    return SGECliScheduler(config.scheduler.sge)


def _print_sge_stage_preview(
    *,
    stage: StageId,
    subject_label: str | None,
    session_label: str | None,
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
    table.add_row("scope", _format_scope(subject_label, session_label))
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
    subject_label: str | None,
    scheduler: SchedulerChoice | None,
    dry_run: bool,
    hold_jid: str | None,
) -> None:
    loaded = load_config(config)
    _ensure_stage_scope(stage, subject_label)
    effective_scheduler = _resolve_scheduler(loaded, scheduler)

    if effective_scheduler == "local":
        if hold_jid is not None:
            console.print("--hold-jid is only supported when --scheduler sge is active.")
            raise typer.Exit(code=2)
        if dry_run:
            _print_local_stage_preview(
                stage=stage,
                config_path=config,
                subject_label=subject_label,
                loaded=loaded,
            )
            return
        _run_local_stage(stage=stage, config_path=config, subject_label=subject_label)
        return

    scheduler_runner = _load_sge_scheduler(loaded)
    plan = scheduler_runner.plan_stage_submission(
        config_path=config,
        config=loaded,
        stage=stage,
        subject_label=subject_label,
        session_label=None,
        hold_jid=hold_jid,
    )
    _print_sge_stage_preview(
        stage=stage,
        subject_label=subject_label,
        session_label=None,
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


def _load_bootstrap_config(
    *,
    config_path: Path | None,
    backend: BackendChoice | None,
    source_root: Path | None,
    heuristic: Path | None,
    outdir: Path | None,
    dicom_dir_template: str | None,
) -> Config:
    if config_path is None:
        loaded = Config()
        cwd = Path.cwd().resolve()
        loaded.project.root = cwd
        loaded.project.sourcedata_root = (source_root or cwd).resolve()
        loaded.project.bids_root = (outdir or cwd / "sourcedata" / "raw").resolve()
        loaded.project.derivatives_root = (cwd / "derivatives").resolve()
        loaded.execution.work_root = (cwd / "work").resolve()
        loaded.execution.logs_root = (cwd / "logs").resolve()
        loaded.execution.state_root = (cwd / "state").resolve()
        loaded.execution.backend = backend or "native"
        loaded.execution.scheduler = "local"
        loaded.heudiconv.outdir = (outdir or loaded.project.bids_root).resolve()
    else:
        loaded = load_config(config_path)

    if backend is not None:
        loaded.execution.backend = backend
    if source_root is not None:
        loaded.project.sourcedata_root = source_root.resolve()
    if heuristic is not None:
        loaded.heudiconv.heuristic = heuristic.resolve()
    if outdir is not None:
        loaded.heudiconv.outdir = outdir.resolve()
    if dicom_dir_template is not None:
        loaded.heudiconv.dicom_dir_template = dicom_dir_template
    return loaded


def _print_heudiconv_check_result(readiness: ReadinessResult, *, loaded: Config) -> None:
    table = Table(title="HeuDiConv check")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("status", readiness.status)
    table.add_row("backend", loaded.execution.backend)
    table.add_row("units", ", ".join(unit.display_name for unit in readiness.units) or "-")
    for index, message in enumerate(readiness.messages, start=1):
        table.add_row(f"message {index}", message)
    console.print(table)


def _print_heudiconv_local_preview(
    *,
    mode: Literal["bootstrap", "run"],
    loaded: Config,
    unit: ScopeUnit,
    command_spec: CommandSpec,
) -> None:
    table = Table(title=f"HeuDiConv {mode} preview")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("stage", StageId.HEUDICONV.value)
    table.add_row("mode", mode)
    table.add_row("scope", unit.display_name)
    table.add_row("backend", loaded.execution.backend)
    table.add_row("cwd", str(command_spec.cwd))
    table.add_row("command", shlex.join(command_spec.command))
    table.add_row("outdir", str(loaded.heudiconv.outdir))
    console.print(table)


def _execute_command(command_spec: CommandSpec, *, prepare_paths: tuple[Path, ...] = ()) -> None:
    for path in prepare_paths:
        path.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update(command_spec.env)
    subprocess.run(
        command_spec.command,
        cwd=command_spec.cwd,
        env=env,
        check=True,
    )


def _copy_bootstrap_heuristic(
    *,
    generated_heuristic: Path | None,
    target_heuristic: Path | None,
    overwrite: bool,
) -> Path | None:
    if generated_heuristic is None or target_heuristic is None:
        return None
    target_heuristic.parent.mkdir(parents=True, exist_ok=True)
    if target_heuristic.exists() and not overwrite:
        return target_heuristic
    shutil.copy2(generated_heuristic, target_heuristic)
    return target_heuristic


def _heudiconv_run_impl(
    *,
    config: Path,
    subject_label: str | None,
    session_label: str | None,
    all_units: bool,
    files: tuple[Path, ...],
    backend: BackendChoice | None,
    scheduler: SchedulerChoice | None,
    dry_run: bool,
    hold_jid: str | None,
) -> None:
    loaded = load_config(config)
    _apply_execution_overrides(loaded, backend=backend, scheduler=scheduler)
    effective_scheduler = _resolve_scheduler(loaded, scheduler)

    readiness = check_readiness(
        loaded,
        mode="run",
        subject_label=subject_label,
        session_label=session_label,
        all_units=all_units,
        files=files,
    )
    _print_heudiconv_check_result(readiness, loaded=loaded)
    if readiness.status == "blocked":
        raise typer.Exit(code=1)

    if files and effective_scheduler == "sge":
        console.print("Explicit --files inputs are not yet supported with --scheduler sge.")
        raise typer.Exit(code=2)

    if effective_scheduler == "local":
        if hold_jid is not None:
            console.print("--hold-jid is only supported when --scheduler sge is active.")
            raise typer.Exit(code=2)
        for unit in readiness.units:
            command_spec = build_run_command(
                loaded,
                subject_label=unit.subject_label,
                session_label=unit.session_label,
                files=files,
            )
            _print_heudiconv_local_preview(
                mode="run",
                loaded=loaded,
                unit=unit,
                command_spec=command_spec,
            )
            if dry_run:
                continue
            try:
                _execute_command(command_spec, prepare_paths=(loaded.heudiconv.outdir.parent,))
            except subprocess.CalledProcessError as error:
                console.print(f"HeuDiConv run failed for {unit.display_name} with exit code {error.returncode}.")
                raise typer.Exit(code=1) from error
        return

    scheduler_runner = _load_sge_scheduler(loaded)
    for unit in readiness.units:
        plan = scheduler_runner.plan_stage_submission(
            config_path=config,
            config=loaded,
            stage=StageId.HEUDICONV,
            subject_label=unit.subject_label,
            session_label=unit.session_label,
            hold_jid=hold_jid,
        )
        _print_sge_stage_preview(
            stage=StageId.HEUDICONV,
            subject_label=unit.subject_label,
            session_label=unit.session_label,
            plan_script=plan.script_text,
            plan_command=plan.qsub_command,
            launch_command=plan.launch.command,
            script_path=plan.script_path,
            stdout_path=plan.launch.stdout_path,
            stderr_path=plan.launch.stderr_path,
        )
        if dry_run:
            continue
        submitted = scheduler_runner.submit(plan)
        console.print(f"Submitted SGE job {submitted.job_id} for {unit.display_name}")


@heudiconv_app.command("bootstrap")
def heudiconv_bootstrap(
    config: Path | None = typer.Option(
        None,
        exists=True,
        dir_okay=False,
        help="Optional path to a TOML config.",
    ),
    subject_label: str = typer.Option(
        ...,
        "--subject-label",
        "--participant-label",
        help="Subject label, e.g. sub-001 or 001.",
    ),
    session_label: str | None = typer.Option(
        None,
        "--session-label",
        help="Optional session label, e.g. ses-01 or 01.",
    ),
    source_root: Path | None = typer.Option(
        None,
        help="Override the sourcedata root used for DICOM discovery.",
    ),
    dicom_dir_template: str | None = typer.Option(
        None,
        help="Override the HeuDiConv DICOM template. Required when no config provides one.",
    ),
    files: list[Path] | None = typer.Option(
        None,
        "--files",
        help="Explicit input files or directories. Repeat --files to add more inputs.",
    ),
    heuristic: Path | None = typer.Option(
        None,
        help="Destination path for the copied bootstrap heuristic.py file.",
    ),
    outdir: Path | None = typer.Option(
        None,
        help="Override the HeuDiConv output root.",
    ),
    backend: BackendChoice | None = typer.Option(
        None,
        help="Override the execution backend for bootstrap.",
    ),
    dry_run: bool = typer.Option(False, help="Preview the bootstrap command without running it."),
) -> None:
    """Generate heuristic bootstrap materials from representative DICOM inputs."""
    loaded = _load_bootstrap_config(
        config_path=config,
        backend=backend,
        source_root=source_root,
        heuristic=heuristic,
        outdir=outdir,
        dicom_dir_template=dicom_dir_template,
    )
    file_inputs = tuple(path.resolve() for path in (files or []))

    readiness = check_readiness(
        loaded,
        mode="bootstrap",
        subject_label=subject_label,
        session_label=session_label,
        all_units=False,
        files=file_inputs,
    )
    _print_heudiconv_check_result(readiness, loaded=loaded)
    if readiness.status == "blocked":
        raise typer.Exit(code=1)

    unit = readiness.units[0]
    command_spec = build_bootstrap_command(
        loaded,
        subject_label=unit.subject_label,
        session_label=unit.session_label,
        files=file_inputs,
    )
    _print_heudiconv_local_preview(
        mode="bootstrap",
        loaded=loaded,
        unit=unit,
        command_spec=command_spec,
    )
    if dry_run:
        return

    try:
        _execute_command(command_spec, prepare_paths=(loaded.heudiconv.outdir.parent,))
    except subprocess.CalledProcessError as error:
        console.print(f"HeuDiConv bootstrap failed with exit code {error.returncode}.")
        raise typer.Exit(code=1) from error

    generated_heuristic = find_bootstrap_heuristic(
        loaded.heudiconv.outdir,
        subject_label=unit.subject_label,
        session_label=unit.session_label,
    )
    copied_heuristic = _copy_bootstrap_heuristic(
        generated_heuristic=generated_heuristic,
        target_heuristic=loaded.heudiconv.heuristic,
        overwrite=loaded.heudiconv.overwrite,
    )
    dicominfo = find_bootstrap_dicominfo(
        loaded.heudiconv.outdir,
        subject_label=unit.subject_label,
        session_label=unit.session_label,
    )

    table = Table(title="HeuDiConv bootstrap results")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("scope", unit.display_name)
    table.add_row("generated heuristic", str(generated_heuristic) if generated_heuristic else "not found")
    table.add_row("copied heuristic", str(copied_heuristic) if copied_heuristic else "-")
    table.add_row("dicominfo.tsv", str(dicominfo) if dicominfo else "not found")
    console.print(table)


@heudiconv_app.command("check")
def heudiconv_check(
    config: Path = typer.Option(..., exists=True, dir_okay=False, help="Path to TOML config."),
    subject_label: str | None = typer.Option(
        None,
        "--subject-label",
        "--participant-label",
        help="Subject label, e.g. sub-001 or 001.",
    ),
    session_label: str | None = typer.Option(
        None,
        "--session-label",
        help="Optional session label, e.g. ses-01 or 01.",
    ),
    all_units: bool = typer.Option(False, "--all", help="Check all discoverable HeuDiConv scope units."),
    files: list[Path] | None = typer.Option(
        None,
        "--files",
        help="Explicit input files or directories. Repeat --files to add more inputs.",
    ),
    backend: BackendChoice | None = typer.Option(
        None,
        help="Override the backend declared in the config for this check.",
    ),
) -> None:
    """Check whether the HeuDiConv stage is ready to run."""
    loaded = load_config(config)
    _apply_execution_overrides(loaded, backend=backend)
    readiness = check_readiness(
        loaded,
        mode="run",
        subject_label=subject_label,
        session_label=session_label,
        all_units=all_units,
        files=tuple(path.resolve() for path in (files or [])),
    )
    _print_heudiconv_check_result(readiness, loaded=loaded)
    if readiness.status == "blocked":
        raise typer.Exit(code=1)


@heudiconv_app.command("run")
def heudiconv_run(
    config: Path = typer.Option(..., exists=True, dir_okay=False, help="Path to TOML config."),
    subject_label: str | None = typer.Option(
        None,
        "--subject-label",
        "--participant-label",
        help="Subject label, e.g. sub-001 or 001.",
    ),
    session_label: str | None = typer.Option(
        None,
        "--session-label",
        help="Optional session label, e.g. ses-01 or 01.",
    ),
    all_units: bool = typer.Option(False, "--all", help="Run all discoverable HeuDiConv scope units."),
    files: list[Path] | None = typer.Option(
        None,
        "--files",
        help="Explicit input files or directories. Repeat --files to add more inputs.",
    ),
    backend: BackendChoice | None = typer.Option(
        None,
        help="Override the backend declared in the config for this invocation.",
    ),
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
    """Run or preview the HeuDiConv stage."""
    _heudiconv_run_impl(
        config=config,
        subject_label=subject_label,
        session_label=session_label,
        all_units=all_units,
        files=tuple(path.resolve() for path in (files or [])),
        backend=backend,
        scheduler=scheduler,
        dry_run=dry_run,
        hold_jid=hold_jid,
    )


@app.command(name="validate")
def validate_stage(
    config: Path = typer.Option(..., exists=True, dir_okay=False, help="Path to TOML config."),
    subject_label: str | None = typer.Option(
        None,
        "--subject-label",
        "--participant-label",
        help="Subject label, e.g. sub-001 or 001.",
    ),
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
        subject_label=subject_label,
        scheduler=scheduler,
        dry_run=dry_run,
        hold_jid=hold_jid,
    )


@app.command()
def fmriprep(
    config: Path = typer.Option(..., exists=True, dir_okay=False, help="Path to TOML config."),
    subject_label: str | None = typer.Option(
        None,
        "--subject-label",
        "--participant-label",
        help="Subject label, e.g. sub-001 or 001.",
    ),
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
        subject_label=subject_label,
        scheduler=scheduler,
        dry_run=dry_run,
        hold_jid=hold_jid,
    )


@app.command()
def mriqc(
    config: Path = typer.Option(..., exists=True, dir_okay=False, help="Path to TOML config."),
    subject_label: str | None = typer.Option(
        None,
        "--subject-label",
        "--participant-label",
        help="Subject label, e.g. sub-001 or 001.",
    ),
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
        subject_label=subject_label,
        scheduler=scheduler,
        dry_run=dry_run,
        hold_jid=hold_jid,
    )


@app.command(name="xcpd")
def xcpd_cmd(
    config: Path = typer.Option(..., exists=True, dir_okay=False, help="Path to TOML config."),
    subject_label: str | None = typer.Option(
        None,
        "--subject-label",
        "--participant-label",
        help="Subject label, e.g. sub-001 or 001.",
    ),
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
        subject_label=subject_label,
        scheduler=scheduler,
        dry_run=dry_run,
        hold_jid=hold_jid,
    )


@app.command()
def qsiprep(
    config: Path = typer.Option(..., exists=True, dir_okay=False, help="Path to TOML config."),
    subject_label: str | None = typer.Option(
        None,
        "--subject-label",
        "--participant-label",
        help="Subject label, e.g. sub-001 or 001.",
    ),
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
        subject_label=subject_label,
        scheduler=scheduler,
        dry_run=dry_run,
        hold_jid=hold_jid,
    )


@app.command()
def qsirecon(
    config: Path = typer.Option(..., exists=True, dir_okay=False, help="Path to TOML config."),
    subject_label: str | None = typer.Option(
        None,
        "--subject-label",
        "--participant-label",
        help="Subject label, e.g. sub-001 or 001.",
    ),
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
        subject_label=subject_label,
        scheduler=scheduler,
        dry_run=dry_run,
        hold_jid=hold_jid,
    )


if __name__ == "__main__":
    app()
