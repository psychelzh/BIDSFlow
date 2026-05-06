"""Typer commands for the managed fMRIPrep workflow."""

from __future__ import annotations

from pathlib import Path

import typer

from ..common import format_command
from ..project import find_project_config, load_project_context
from . import (
    FmriprepInitError,
    FmriprepInitResult,
    FmriprepRunError,
    FmriprepRunPlan,
    FmriprepRunResult,
    get_fmriprep_status,
    list_participants_review_issues,
    plan_fmriprep_init,
    plan_fmriprep_run,
    preview_fmriprep_unit_selection,
    run_fmriprep,
    run_fmriprep_init,
    summarize_participants,
)

HELP_CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}
RUN_UNIT_STATUS_KEYS = ("total", "not_run", "succeeded", "failed", "active", "other")
PARTICIPANT_SUMMARY_KEYS = (
    "total",
    "ready",
    "needs_review",
    "missing_raw",
    "excluded",
)
CLEANUP_STATUS_KEYS = ("pending", "succeeded", "failed", "active", "not_needed")

fmriprep_app = typer.Typer(
    help="Manage fMRIPrep participant preprocessing.",
    invoke_without_command=True,
    context_settings=HELP_CONTEXT_SETTINGS,
)


@fmriprep_app.callback(
    invoke_without_command=True,
    context_settings=HELP_CONTEXT_SETTINGS,
)
def fmriprep(
    ctx: typer.Context,
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Show planned fMRIPrep commands and preflight messages without running.",
    ),
    include_failed: bool = typer.Option(
        False,
        "--include-failed",
        "-r",
        help="Retry participant units whose latest status is failed.",
    ),
) -> None:
    """Run managed fMRIPrep preprocessing when no subcommand is provided."""
    if ctx.invoked_subcommand is not None:
        if dry_run or include_failed:
            typer.echo(
                "Run options apply only to fMRIPrep runs. Use them with "
                "`bidsflow fmriprep`, not with subcommands.",
                err=True,
            )
            raise typer.Exit(code=2)
        return
    _run_fmriprep_default(dry_run=dry_run, include_failed=include_failed)


@fmriprep_app.command("init", context_settings=HELP_CONTEXT_SETTINGS)
def fmriprep_init(
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite fMRIPrep init files such as participants.tsv and target config.",
    ),
    build_raw_layout: bool = typer.Option(
        False,
        "--build-raw-layout",
        "-l",
        help="Build or refresh the PyBIDS raw BIDS layout database.",
    ),
) -> None:
    """Initialize fMRIPrep support files."""
    _run_fmriprep_init(force=force, build_raw_layout=build_raw_layout)


@fmriprep_app.command("status", context_settings=HELP_CONTEXT_SETTINGS)
def fmriprep_status() -> None:
    """Show current fMRIPrep project state without changing files."""
    _run_fmriprep_status()


def _run_fmriprep_init(
    *,
    force: bool,
    build_raw_layout: bool,
) -> None:
    try:
        config_path = find_project_config(Path.cwd())
        context = load_project_context(config_path)
        plan = plan_fmriprep_init(context, build_raw_layout=build_raw_layout)
        result = run_fmriprep_init(context, plan, force=force)
    except (FmriprepInitError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    typer.echo("Initialized fMRIPrep support files.")
    typer.echo("")
    typer.echo("Project:")
    typer.echo(f"  Config: {config_path}")
    typer.echo(f"  Target config: {result.config_path} ({result.config_action})")

    typer.echo("")
    typer.echo("Participants:")
    typer.echo(f"  Participants discovered: {len(plan.participants_plan.entries)}")
    typer.echo(f"  Participants table: {result.participants_path} ({result.participants_action})")
    _echo_participant_summary(summarize_participants(result.participants))

    typer.echo("")
    typer.echo("Raw BIDS layout:")
    typer.echo(f"  Layout database: {result.raw_layout_dir} ({result.raw_layout_action})")

    typer.echo("")
    typer.echo("Scheduler:")
    typer.echo(f"  Scheduler: {result.scheduler}")
    if result.scheduler_script_path is None:
        typer.echo("  Scheduler script: not generated")
    else:
        typer.echo(
            f"  Scheduler script: {result.scheduler_script_path} "
            f"({result.scheduler_script_action})"
        )
        typer.echo(
            f"  Cleanup scheduler script: {result.cleanup_scheduler_script_path} "
            f"({result.cleanup_scheduler_script_action})"
        )

    _echo_init_notices(result, discovered_participants=len(plan.participants_plan.entries))

    issues = list_participants_review_issues(result.participants)
    if issues:
        typer.echo("")
        typer.echo("Review needed:")
        for issue in issues:
            typer.echo(f"  - {issue}")

    typer.echo("")
    typer.echo("Next:")
    typer.echo(f"  Review participants: {result.participants_path}")
    typer.echo(f"  Review fMRIPrep config: {result.config_path}")
    if result.raw_layout_action == "not requested":
        typer.echo("  Optional raw layout cache: bidsflow fmriprep init -l")
    typer.echo("  Preview fMRIPrep: bidsflow fmriprep --dry-run")
    typer.echo("  Run fMRIPrep: bidsflow fmriprep")


def _run_fmriprep_default(
    *,
    dry_run: bool,
    include_failed: bool,
) -> None:
    try:
        config_path = find_project_config(Path.cwd())
        context = load_project_context(config_path)
        plan = plan_fmriprep_run(context)
    except (FmriprepRunError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    if dry_run:
        _echo_dry_run(config_path, plan, include_failed=include_failed)
        return

    if plan.warnings:
        typer.secho("Warnings:", fg=typer.colors.YELLOW, bold=True)
        for warning in plan.warnings:
            typer.echo(f"  - {warning}")

    try:
        result = run_fmriprep(context, plan, include_failed=include_failed)
    except FmriprepRunError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    _echo_run_result(result)


def _run_fmriprep_status() -> None:
    try:
        config_path = find_project_config(Path.cwd())
        context = load_project_context(config_path)
        status = get_fmriprep_status(context)
    except (FmriprepRunError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    typer.echo("fMRIPrep status.")
    typer.echo("")
    typer.echo("Project:")
    typer.echo(f"  Config: {config_path}")
    typer.echo(f"  Participants table: {status.participants_path}")
    typer.echo(f"  Target config: {status.config_path}")

    typer.echo("")
    typer.echo("Participants:")
    if not status.participants_exists:
        typer.echo("  State: not initialized")
    else:
        _echo_participant_summary(status.participants_summary)
    if not status.config_exists:
        typer.echo("  Target config: missing")

    typer.echo("")
    typer.echo("Run:")
    typer.echo(f"  Latest run state: {status.run_record_state or 'none'}")
    typer.echo(f"  Results rows: {status.results_count}")
    for key in RUN_UNIT_STATUS_KEYS:
        typer.echo(f"  - {key}: {status.unit_counts[key]}")

    typer.echo("")
    typer.echo("Cleanup:")
    for key in CLEANUP_STATUS_KEYS:
        typer.echo(f"  - {key}: {status.cleanup_counts[key]}")

    if status.participants_issues:
        typer.echo("")
        typer.echo("Participants needing review:")
        for issue in status.participants_issues:
            typer.echo(f"  - {issue}")

    failed_units = tuple(unit for unit in status.units if unit.status == "failed")
    if failed_units:
        typer.echo("")
        typer.echo("Failed units:")
        for unit in failed_units[:10]:
            suffix = f" ({unit.error})" if unit.error else ""
            typer.echo(f"  - {unit.unit_name}{suffix}")
            if unit.log_path is not None:
                typer.echo(f"    log: {unit.log_path}")
        if len(failed_units) > 10:
            typer.echo(f"  - additional failed units omitted: {len(failed_units) - 10}")

    cleanup_failed = tuple(unit for unit in status.units if unit.cleanup_status == "failed")
    if cleanup_failed:
        typer.echo("")
        typer.echo("Cleanup warnings:")
        for unit in cleanup_failed[:10]:
            suffix = f" ({unit.warning})" if unit.warning else ""
            typer.echo(f"  - {unit.unit_name}{suffix}")

    typer.echo("")
    typer.echo("Next:")
    if not status.participants_exists:
        typer.echo("  Initialize fMRIPrep: bidsflow fmriprep init")
    elif status.participants_issues:
        typer.echo(f"  Review participants: {status.participants_path}")
    elif not status.config_exists:
        typer.echo("  Regenerate target config: bidsflow fmriprep init")
    elif failed_units:
        typer.echo("  Inspect failed unit logs and retry with:")
        typer.echo("    bidsflow fmriprep --include-failed")
    elif status.unit_counts["not_run"]:
        typer.echo("  Run pending participants: bidsflow fmriprep")
    elif status.cleanup_counts["pending"] or status.cleanup_counts["failed"]:
        typer.echo("  Run cleanup-only pass: bidsflow fmriprep")
    elif status.unit_counts["active"] or status.cleanup_counts["active"]:
        typer.echo("  Wait for active scheduler/local work to finish, then run status again.")
    else:
        typer.echo("  No immediate action.")


def _echo_dry_run(
    config_path: Path,
    plan: FmriprepRunPlan,
    *,
    include_failed: bool,
) -> None:
    runnable_units, unit_counts = preview_fmriprep_unit_selection(
        plan,
        include_failed=include_failed,
    )
    typer.echo("Planned `bidsflow fmriprep` execution.")
    typer.echo("Dry run only; no files or directories were created.")
    typer.echo(f"Config: {config_path}")
    typer.echo(f"Participants table: {plan.participants_path}")
    typer.echo(f"Target config: {plan.config_path}")
    typer.echo(f"Raw BIDS input: {plan.raw_bids_root}")
    typer.echo(f"fMRIPrep output: {plan.output_root}")
    typer.echo("Backend: local")
    typer.echo(f"Ready participants: {unit_counts['total']}")
    typer.echo(f"Runnable participants now: {unit_counts['selected']}")
    typer.echo(f"Cleanup-pending participants: {len(plan.cleanup_units)}")
    if include_failed:
        typer.echo("Failed units: included")
    _echo_skipped_units(unit_counts)
    if plan.warnings:
        typer.echo("")
        typer.secho("Warnings:", fg=typer.colors.YELLOW, bold=True)
        for warning in plan.warnings:
            typer.echo(f"  - {warning}")
    if not runnable_units:
        return

    unit = runnable_units[0]
    typer.echo("")
    typer.echo("Example runnable participant:")
    typer.echo(f"{unit.unit_name}: participant={unit.participant_label}")
    typer.echo(format_command(unit.command))
    if len(runnable_units) > 1:
        typer.echo(f"Additional runnable participants omitted: {len(runnable_units) - 1}.")


def _echo_run_result(result: FmriprepRunResult) -> None:
    if result.status == "skipped":
        typer.echo("No runnable `bidsflow fmriprep` participants were found.")
        _echo_skipped_units(_run_result_skip_counts(result))
        return
    if result.status == "cleanup-only":
        typer.echo("Completed fMRIPrep cleanup-only pass.")
    else:
        typer.echo("Completed `bidsflow fmriprep` execution.")
    typer.echo(f"Run units: {len(result.unit_results)}")
    typer.echo(f"Cleanup units: {len(result.cleanup_results)}")
    _echo_skipped_units(
        _run_result_skip_counts(result),
        show_succeeded=False,
        show_active_claim=False,
    )
    typer.echo(f"fMRIPrep output: {result.output_root}")
    typer.echo(f"State: {result.state_path}")
    typer.echo(f"Results table: {result.results_path}")
    typer.echo(f"Logs: {result.log_dir}")


def _echo_participant_summary(summary: dict[str, int]) -> None:
    """Print participant review counts in a stable CLI order."""

    for key in PARTICIPANT_SUMMARY_KEYS:
        typer.echo(f"  - {key}: {summary[key]}")


def _echo_init_notices(
    result: FmriprepInitResult,
    *,
    discovered_participants: int,
) -> None:
    """Explain preserved fMRIPrep init artifacts so reruns are not silent."""

    notices: list[str] = []
    if result.participants_action == "kept":
        notices.append("Existing participants table found; keeping reviewed file.")
        if discovered_participants != len(result.participants):
            notices.append(
                f"Current raw-BIDS scan found {discovered_participants} participant row(s); "
                f"the kept participants table has {len(result.participants)} row(s)."
            )
        notices.append(
            "Current raw-BIDS discovery is not applied to participants.tsv unless you regenerate."
        )
        notices.append(
            "To rebuild participants.tsv from raw BIDS, run: bidsflow fmriprep init --force"
        )
    if result.scheduler_script_action == "kept":
        notices.append("Existing scheduler script kept; use --force to regenerate it.")
    if result.cleanup_scheduler_script_action == "kept":
        notices.append("Existing cleanup scheduler script kept; use --force to regenerate it.")

    if not notices:
        return
    typer.echo("")
    typer.secho("Notice:", fg=typer.colors.YELLOW, bold=True)
    for notice in notices:
        typer.echo(f"  - {notice}")


def _run_result_skip_counts(result: FmriprepRunResult) -> dict[str, int]:
    """Adapt an fMRIPrep RunResult to the shared skip-count printer shape."""

    return {
        "skipped": result.skipped_units,
        "skipped_succeeded": result.skipped_succeeded,
        "skipped_failed": result.skipped_failed,
        "skipped_active_claim": result.skipped_active_claim,
    }


def _echo_skipped_units(
    unit_counts: dict[str, int],
    *,
    show_succeeded: bool = True,
    show_active_claim: bool = True,
) -> None:
    """Print skipped-unit totals with retry guidance."""

    if unit_counts["skipped"]:
        typer.echo(f"Skipped units: {unit_counts['skipped']}")
    if show_succeeded and unit_counts["skipped_succeeded"]:
        typer.echo(f"- already succeeded: {unit_counts['skipped_succeeded']}")
    if unit_counts["skipped_failed"]:
        typer.echo(
            f"- failed; use --include-failed to retry: {unit_counts['skipped_failed']}"
        )
    if show_active_claim and unit_counts["skipped_active_claim"]:
        typer.echo(f"- active claim: {unit_counts['skipped_active_claim']}")
