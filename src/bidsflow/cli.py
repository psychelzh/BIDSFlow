"""Command-line interface for BIDSFlow project and HeuDiConv workflows."""

from __future__ import annotations

import os
import shutil
from importlib import resources
from pathlib import Path

import typer

from .heudiconv import (
    HeudiconvDraftError,
    HeudiconvInitError,
    HeudiconvRunError,
    RunPlan,
    RunResult,
    SourcesError,
    format_command,
    get_heudiconv_status,
    list_sources_review_issues,
    plan_draft,
    plan_heudiconv_init,
    plan_heudiconv_run,
    preview_run_unit_selection,
    run_draft,
    run_heudiconv,
    run_heudiconv_init,
    summarize_sources_entries,
)
from .project import find_project_config, load_project_context

app = typer.Typer(
    help="BIDSFlow: a task-first CLI for BIDS workflow logistics.",
    no_args_is_help=True,
)
heudiconv_app = typer.Typer(
    help="Manage HeuDiConv preparation and conversion.",
    invoke_without_command=True,
)
app.add_typer(heudiconv_app, name="heudiconv")
DEFAULT_LAYOUT_DIRECTORIES = (
    Path("sourcedata"),
    Path("sourcedata") / "raw",
    Path("derivatives"),
    Path("work"),
    Path("logs"),
    Path("state"),
)
INIT_SCHEDULERS = ("auto", "none", "sge")
UNSUPPORTED_WINDOWS_MESSAGE = (
    "BIDSFlow currently supports Unix-like environments only. "
    "On Windows, run BIDSFlow inside WSL."
)


def _ensure_supported_platform() -> None:
    if os.name == "nt":  # pragma: no cover
        typer.echo(UNSUPPORTED_WINDOWS_MESSAGE, err=True)
        raise typer.Exit(code=2)


def _toml_string(value: str) -> str:
    escape_map = {
        "\\": "\\\\",
        '"': '\\"',
        "\n": "\\n",
        "\r": "\\r",
        "\t": "\\t",
        "\b": "\\b",
        "\f": "\\f",
    }
    escaped = "".join(
        escape_map.get(character, f"\\u{ord(character):04X}" if ord(character) < 0x20 else character)
        for character in value
    )
    return f'"{escaped}"'


def _render_execution_section(scheduler: str) -> str:
    if scheduler == "sge":
        return """[execution]
scheduler = "sge"
submit_command = ["qsub", "-terse"]
"""
    return """[execution]
scheduler = "none"

# Uncomment and configure these when you want SGE execution.
# scheduler = "sge"
# submit_command = ["qsub", "-terse"]
"""


def _render_project_config(project_name: str, scheduler: str) -> str:
    template = (
        resources.files("bidsflow")
        .joinpath("templates", "bidsflow.toml.template")
        .read_text(encoding="utf-8")
    )
    return (
        template.replace("__PROJECT_NAME__", _toml_string(project_name))
        .replace("__EXECUTION_SECTION__", _render_execution_section(scheduler))
    )


def _select_init_scheduler(requested_scheduler: str) -> tuple[str, str, str | None]:
    requested_scheduler = requested_scheduler.lower()
    if requested_scheduler not in INIT_SCHEDULERS:
        joined = ", ".join(INIT_SCHEDULERS)
        raise typer.BadParameter(f"Scheduler must be one of: {joined}.")

    if requested_scheduler == "none":
        return "none", "Scheduler: none", None

    qsub_path = shutil.which("qsub")
    if requested_scheduler == "auto":
        if qsub_path is not None:
            return "sge", "Scheduler: sge (detected qsub)", None
        return "none", "Scheduler: none (no supported scheduler detected)", None

    if requested_scheduler == "sge":
        if qsub_path is None:
            return (
                "sge",
                "Scheduler: sge",
                "Warning: qsub was not found on PATH; the scaffold still records SGE for this project.",
            )
        return "sge", "Scheduler: sge (detected qsub)", None

    raise AssertionError(f"Unhandled scheduler: {requested_scheduler}")  # pragma: no cover


def _default_project_name(directory: Path) -> str:
    return directory.resolve().name or "BIDSFlow project"


@app.callback()
def main() -> None:
    """BIDSFlow: task-first CLI for BIDS workflow logistics."""
    _ensure_supported_platform()


@app.command()
def init(
    directory: Path = typer.Argument(
        Path("."),
        file_okay=False,
        help="Directory where the project scaffold should be created.",
    ),
    name: str | None = typer.Option(
        None,
        "--name",
        help="Project name to write into the generated config.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite the generated config if it already exists.",
    ),
    make_dirs: bool = typer.Option(
        False,
        "--make-dirs",
        help="Create the default project directories described by the generated config.",
    ),
    scheduler: str = typer.Option(
        "auto",
        "--scheduler",
        help="Scheduler scaffold to write: auto, none, or sge.",
    ),
) -> None:
    """Initialize a minimal BIDSFlow project scaffold."""
    target_directory = directory.resolve()

    if target_directory.exists() and not target_directory.is_dir():
        typer.echo(f"Target path is not a directory: {target_directory}", err=True)
        raise typer.Exit(code=2)

    config_path = target_directory / "bidsflow.toml"
    if config_path.exists() and not force:
        typer.echo(
            f"Refusing to overwrite existing config: {config_path}. Use --force to overwrite it.",
            err=True,
        )
        raise typer.Exit(code=2)

    project_name = name or _default_project_name(target_directory)
    selected_scheduler, scheduler_message, scheduler_warning = _select_init_scheduler(scheduler)

    target_directory.mkdir(parents=True, exist_ok=True)

    config_path.write_text(
        _render_project_config(project_name, selected_scheduler),
        encoding="utf-8",
        newline="\n",
    )

    if make_dirs:
        for relative_path in DEFAULT_LAYOUT_DIRECTORIES:
            (target_directory / relative_path).mkdir(parents=True, exist_ok=True)

    typer.echo("Initialized BIDSFlow project.")
    typer.echo("")
    typer.echo("Project:")
    typer.echo(f"  Root: {target_directory}")
    typer.echo(f"  Config: {config_path}")
    typer.echo(f"  {scheduler_message}")
    directory_message = "created" if make_dirs else "not created (use --make-dirs to create them)"
    typer.echo(f"  Layout directories: {directory_message}")
    if scheduler_warning is not None:
        typer.echo("")
        typer.echo(scheduler_warning)
    typer.echo("")
    typer.echo("Next:")
    typer.echo(f"  Review config: {config_path}")
    typer.echo("  Prepare HeuDiConv: bidsflow heudiconv init")


@heudiconv_app.callback(invoke_without_command=True)
def heudiconv(
    ctx: typer.Context,
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show planned conversion commands and outputs without running HeuDiConv.",
    ),
    include_failed: bool = typer.Option(
        False,
        "--include-failed",
        help="Retry units whose latest status is failed.",
    ),
    clean_workdir: bool = typer.Option(
        True,
        "--clean-workdir/--keep-workdir",
        help="Remove temporary HeuDiConv execution views after units finish.",
    ),
) -> None:
    """Run managed HeuDiConv conversion when no subcommand is provided."""
    if ctx.invoked_subcommand is not None:
        return
    _run_heudiconv_default(
        dry_run=dry_run,
        clean_workdir=clean_workdir,
        include_failed=include_failed,
    )


@heudiconv_app.command("init")
def heudiconv_init(
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite HeuDiConv init files such as sources.tsv and scheduler script.",
    ),
) -> None:
    """Initialize HeuDiConv support files."""
    _run_heudiconv_init(force=force)


@heudiconv_app.command("draft")
def heudiconv_draft(
    sample_paths: list[Path] = typer.Argument(
        ...,
        exists=False,
        help="Representative sample paths used to generate a draft heuristic.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite existing HeuDiConv draft outputs.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show planned draft commands and outputs without running HeuDiConv.",
    ),
) -> None:
    """Generate a draft HeuDiConv heuristic from representative sample paths."""
    _run_heudiconv_draft(sample_paths, force=force, dry_run=dry_run)


@heudiconv_app.command("status")
def heudiconv_status() -> None:
    """Show current HeuDiConv project state without changing files."""
    _run_heudiconv_status()


def _run_heudiconv_init(
    *,
    force: bool,
) -> None:
    try:
        config_path = find_project_config(Path.cwd())
        context = load_project_context(config_path)
        plan = plan_heudiconv_init(context)
        result = run_heudiconv_init(context, plan, force=force)
    except (HeudiconvInitError, SourcesError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    typer.echo("Initialized HeuDiConv support files.")
    typer.echo("")
    typer.echo("Project:")
    typer.echo(f"  Config: {config_path}")
    typer.echo(f"  HeuDiConv code root: {result.code_root}")
    typer.echo(f"  Heuristic: {context.heudiconv.heuristic}")

    typer.echo("")
    typer.echo("Sources:")
    typer.echo(f"  Sources discovered: {len(result.entries)}")
    typer.echo(f"  Sources table: {result.sources_path} ({result.sources_action})")
    typer.echo(f"  Sources metadata: {result.sources_state_path} ({result.sources_action})")

    if plan.sources_plan.pattern is not None:
        typer.echo(f"  Label generation: pattern: {plan.sources_plan.pattern!r}")
    elif plan.sources_plan.command is not None:
        typer.echo(f"  Label generation: command: {format_command(plan.sources_plan.command)}")
    else:
        typer.echo("  Label generation: manual review; labels will be left blank.")

    typer.echo("")
    typer.echo("Scheduler:")
    typer.echo(f"  Scheduler: {result.scheduler}")
    if result.scheduler_script_path is None:
        typer.echo("  Scheduler script: not generated")
    else:
        typer.echo(
            f"  Scheduler script: {result.scheduler_script_path} ({result.scheduler_script_action})"
        )

    typer.echo("")
    typer.echo("Summary:")
    summary = summarize_sources_entries(result.entries)
    for key in ("total", "ready", "needs_review", "collision", "missing_source", "excluded"):
        typer.echo(f"  - {key}: {summary[key]}")
    issues = list_sources_review_issues(result.entries)
    if issues:
        typer.echo("")
        typer.echo("Review needed:")
        for issue in issues:
            typer.echo(f"  - {issue}")

    typer.echo("")
    typer.echo("Next:")
    typer.echo(f"  Review sources: {result.sources_path}")
    typer.echo("  Draft heuristic: bidsflow heudiconv draft <sample-path>")
    typer.echo(f"  Review heuristic: {context.heudiconv.heuristic}")
    if result.scheduler_script_path is not None:
        typer.echo(f"  Check scheduler script: {result.scheduler_script_path}")
        typer.echo("    Look at SGE directives, site environment setup, and launcher/container use.")
    typer.echo("  Preview conversion: bidsflow heudiconv --dry-run")
    typer.echo("  Run conversion: bidsflow heudiconv")


def _run_heudiconv_draft(
    sample_paths: list[Path],
    *,
    force: bool,
    dry_run: bool,
) -> None:
    if not sample_paths:  # pragma: no cover
        typer.echo("`bidsflow heudiconv draft` requires at least one sample path.", err=True)
        raise typer.Exit(code=2)

    try:
        config_path = find_project_config(Path.cwd())
        context = load_project_context(config_path)
        plan = plan_draft(context, sample_paths)
    except (HeudiconvDraftError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    if dry_run:
        if len(plan.units) == 1 and plan.units[0].session_label is None:
            unit = plan.units[0]
            typer.echo("Planned HeuDiConv draft strategy: single-directory draft")
            typer.echo(f"Temporary subject for draft: {unit.subject_label}")
            typer.echo(format_command(unit.initial_command))
        else:
            typer.echo(
                f"Planned HeuDiConv draft strategy: split {len(plan.units)} directories into "
                "single-directory sample units"
            )
            for unit in plan.units:
                typer.echo(
                    f"{unit.unit_name}: sample={unit.sample_path} "
                    f"subject={unit.subject_label} session={unit.session_label}"
                )
                typer.echo(format_command(unit.initial_command))
        typer.echo(f"Config: {config_path}")
        typer.echo(f"Sample paths: {len(plan.sample_paths)}")
        typer.echo(f"Draft work root: {plan.draft_work_root}")
        typer.echo(f"Heuristic: {plan.heuristic_path}")
        typer.echo(f"DICOM inventories: {plan.dicominfo_root}")
        typer.echo(f"State: {plan.draft_state_path}")
        typer.echo(f"Unit logs: {plan.log_dir}")
        return

    try:
        result = run_draft(context, plan, reset=force)
    except HeudiconvDraftError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    typer.echo("Prepared HeuDiConv draft outputs.")
    typer.echo(f"Draft samples: {len(result.unit_results)}")
    typer.echo(f"Draft work root: {plan.draft_work_root}")
    typer.echo(f"Heuristic: {result.heuristic_path}")
    typer.echo(f"DICOM inventories: {result.dicominfo_root} ({len(result.dicominfo_paths)} files)")
    typer.echo(f"State: {result.draft_state_path}")
    typer.echo(f"Unit logs: {result.log_dir}")
    typer.echo(
        "Next: review and edit the heuristic, review sources.tsv, then run `bidsflow heudiconv`."
    )


def _run_heudiconv_default(
    *,
    dry_run: bool,
    clean_workdir: bool,
    include_failed: bool,
) -> None:
    try:
        config_path = find_project_config(Path.cwd())
        context = load_project_context(config_path)
        plan = plan_heudiconv_run(context)
    except (HeudiconvRunError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    if dry_run:
        _echo_heudiconv_dry_run(
            config_path,
            plan,
            clean_workdir=clean_workdir,
            include_failed=include_failed,
        )
        return

    try:
        result = run_heudiconv(
            context,
            plan,
            cleanup_workdir=clean_workdir,
            include_failed=include_failed,
        )
    except HeudiconvRunError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    _echo_heudiconv_run_result(result, plan, clean_workdir=clean_workdir)


def _run_heudiconv_status() -> None:
    try:
        config_path = find_project_config(Path.cwd())
        context = load_project_context(config_path)
        status = get_heudiconv_status(context)
    except (HeudiconvRunError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    typer.echo("HeuDiConv status.")
    typer.echo("")
    typer.echo("Project:")
    typer.echo(f"  Config: {config_path}")
    typer.echo(f"  Sources table: {status.sources_path}")
    typer.echo(f"  Heuristic: {status.heuristic_path}")

    typer.echo("")
    typer.echo("Sources:")
    if not status.sources_exists:
        typer.echo("  State: not initialized")
    else:
        for key in ("total", "ready", "needs_review", "collision", "missing_source", "excluded"):
            typer.echo(f"  - {key}: {status.sources_summary[key]}")
    if not status.heuristic_exists:
        typer.echo("  Heuristic: missing")

    typer.echo("")
    typer.echo("Run:")
    typer.echo(f"  Latest run state: {status.run_record_state or 'none'}")
    typer.echo(f"  Results rows: {status.results_count}")
    for key in ("total", "not_run", "succeeded", "failed", "active", "other"):
        typer.echo(f"  - {key}: {status.unit_counts[key]}")

    if status.sources_issues:
        typer.echo("")
        typer.echo("Sources needing review:")
        for issue in status.sources_issues:
            typer.echo(f"  - {issue}")

    failed_units = tuple(unit for unit in status.units if unit.status == "failed")
    if failed_units:
        typer.echo("")
        typer.echo("Failed units:")
        for unit in failed_units[:10]:
            suffix = f" ({unit.error})" if unit.error else ""
            typer.echo(f"  - {unit.unit_name}: source={unit.source_name}{suffix}")
            if unit.log_path is not None:
                typer.echo(f"    log: {unit.log_path}")
            elif unit.log_dir is not None:
                typer.echo(f"    logs: {unit.log_dir}")
        if len(failed_units) > 10:
            typer.echo(f"  - additional failed units omitted: {len(failed_units) - 10}")

    active_units = tuple(unit for unit in status.units if unit.active_claim)
    if active_units:
        typer.echo("")
        typer.echo("Active claims:")
        for unit in active_units[:10]:
            typer.echo(f"  - {unit.unit_name}: {unit.claim_path}")
        if len(active_units) > 10:
            typer.echo(f"  - additional active claims omitted: {len(active_units) - 10}")

    typer.echo("")
    typer.echo("Next:")
    if not status.sources_exists:
        typer.echo("  Initialize HeuDiConv: bidsflow heudiconv init")
    elif status.sources_issues:
        typer.echo(f"  Review sources: {status.sources_path}")
    elif not status.heuristic_exists:
        typer.echo("  Draft heuristic: bidsflow heudiconv draft <sample-path>")
    elif failed_units:
        typer.echo("  Inspect failed unit logs and sources.tsv; retry with:")
        typer.echo("    bidsflow heudiconv --include-failed")
    elif status.unit_counts["not_run"]:
        typer.echo("  Run pending units: bidsflow heudiconv")
    elif status.unit_counts["active"]:
        typer.echo("  Wait for active scheduler/local work to finish, then run status again.")
    else:
        typer.echo("  No immediate action.")


def _echo_heudiconv_dry_run(
    config_path: Path,
    plan: RunPlan,
    *,
    clean_workdir: bool,
    include_failed: bool,
) -> None:
    runnable_units, unit_counts = preview_run_unit_selection(
        plan,
        include_failed=include_failed,
    )
    typer.echo("Planned `bidsflow heudiconv` execution.")
    typer.echo("Dry run only; no files or directories were created.")
    typer.echo(f"Config: {config_path}")
    typer.echo(f"Sources table: {plan.sources_path}")
    typer.echo(f"Heuristic: {plan.heuristic_path}")
    typer.echo(f"Raw BIDS output: {plan.raw_bids_root}")
    if plan.sge is not None:
        typer.echo("Backend: sge")
        typer.echo(f"Scheduler template: {plan.sge.template_path}")
        typer.echo(f"Submit command: {format_command(plan.sge.submit_command)}")
        typer.echo("Scheduler artifacts are created only when the job is submitted.")
    else:
        typer.echo("Backend: local")
    cleanup_summary = (
        "enabled; use --keep-workdir to inspect temporary execution views."
        if clean_workdir
        else "disabled; temporary execution views will be kept."
    )
    typer.echo(f"Cleanup: {cleanup_summary}")
    typer.echo(f"Ready units in sources.tsv: {unit_counts['total']}")
    typer.echo(f"Runnable units now: {unit_counts['selected']}")
    if include_failed:
        typer.echo("Failed units: included")
    if unit_counts["skipped"]:
        typer.echo(f"Skipped units: {unit_counts['skipped']}")
    if unit_counts["skipped_succeeded"]:
        typer.echo(f"- already succeeded: {unit_counts['skipped_succeeded']}")
    if unit_counts["skipped_failed"]:
        typer.echo(f"- failed; use --include-failed to retry: {unit_counts['skipped_failed']}")
    if unit_counts["skipped_active_claim"]:
        typer.echo(f"- active claim: {unit_counts['skipped_active_claim']}")
    if not runnable_units:
        return

    unit = runnable_units[0]
    typer.echo("Example runnable unit:")
    typer.echo(
        f"{unit.source_name}: subject={unit.subject_label} "
        f"session={unit.session_label or '-'}"
    )
    typer.echo(format_command(unit.command))
    if len(runnable_units) > 1:
        typer.echo(
            f"Additional runnable units omitted: {len(runnable_units) - 1}. "
            "HeuDiConv will apply the same sources-driven pattern to each ready row."
        )


def _echo_heudiconv_run_result(
    result: RunResult,
    plan: RunPlan,
    *,
    clean_workdir: bool,
) -> None:
    if result.status == "submitted":
        typer.echo("Submitted `bidsflow heudiconv` execution.")
    elif result.status == "skipped":
        typer.echo("No runnable `bidsflow heudiconv` units were found.")
        typer.echo(f"Skipped units: {result.skipped_units}")
        if result.skipped_succeeded:
            typer.echo(f"- already succeeded: {result.skipped_succeeded}")
        if result.skipped_failed:
            typer.echo(f"- failed; use --include-failed to retry: {result.skipped_failed}")
        if result.skipped_active_claim:
            typer.echo(f"- active claim: {result.skipped_active_claim}")
        return
    else:
        typer.echo("Completed `bidsflow heudiconv` execution.")
    typer.echo(f"Run units: {len(result.unit_results)}")
    if result.skipped_units:
        typer.echo(f"Skipped units: {result.skipped_units}")
    if result.skipped_failed:
        typer.echo(f"- failed; use --include-failed to retry: {result.skipped_failed}")
    typer.echo(f"Raw BIDS root: {result.raw_bids_root}")
    typer.echo(f"State: {result.state_path}")
    typer.echo(f"Results table: {result.results_path}")
    typer.echo(f"Logs: {result.log_dir}")
    typer.echo(f"Unit status files: {plan.unit_state_dir}")
    typer.echo(f"Unit claims: {plan.claim_dir}")
    if not clean_workdir:
        typer.echo(f"Execution view kept: {plan.execution_view_root}")
    if result.backend == "sge":
        typer.echo("Scheduler: sge")
        typer.echo(f"Scheduler job id: {result.scheduler_job_id or '-'}")
        typer.echo(f"Scheduler script: {result.scheduler_script_path}")
        if plan.sge is not None:
            typer.echo(f"Scheduler unit list: {plan.sge.unit_list_path}")
        typer.echo(f"Scheduler logs: {result.scheduler_log_dir}")


if __name__ == "__main__":  # pragma: no cover
    app()
