"""Managed fMRIPrep planning, local execution, and cleanup."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess

from ..common import (
    _append_log,
    _combine_process_output,
    _compute_input_signature,
    _format_attempt_label,
    _remove_project_path,
    _utc_now,
    _write_json,
    _write_key_value_status_atomic,
    format_command,
)
from ..project import ProjectContext, load_fmriprep_config
from ..run_state import (
    UnitSelection,
    build_subject_session_unit_name,
    build_unit_counts,
    claim_runnable_units,
    preview_runnable_units,
    read_existing_key_value_status,
    release_unit_claim,
    release_unfinished_claims,
)
from .errors import FmriprepRunError
from .options import FmriprepOptions, load_fmriprep_options
from .participants import (
    ParticipantEntry,
    list_participants_review_issues,
    load_participants,
)


@dataclass(frozen=True)
class FmriprepRunUnitPlan:
    """One reviewed participant unit ready for fMRIPrep."""

    index: int
    unit_name: str
    participant_label: str
    command: tuple[str, ...]
    work_dir: Path
    log_path: Path
    status_path: Path
    claim_path: Path
    cleanup_claim_path: Path


@dataclass(frozen=True)
class FmriprepRunPlan:
    """Complete plan for a managed fMRIPrep attempt."""

    attempt_label: str
    participants_path: Path
    config_path: Path
    options: FmriprepOptions
    raw_bids_root: Path
    output_root: Path
    state_path: Path
    results_path: Path
    unit_state_dir: Path
    run_claim_dir: Path
    cleanup_claim_dir: Path
    log_dir: Path
    entries: tuple[ParticipantEntry, ...]
    input_signature: str
    units: tuple[FmriprepRunUnitPlan, ...]
    cleanup_units: tuple[FmriprepRunUnitPlan, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class FmriprepRunUnitResult:
    """Final local result or submitted scheduler record for one fMRIPrep unit."""

    record_type: str
    unit_name: str
    participant_label: str
    status: str
    started_at: str
    finished_at: str | None
    exit_code: int | None
    log_path: Path
    work_dir: Path
    notes: str = ""


@dataclass(frozen=True)
class FmriprepRunResult:
    """Overall result returned after fMRIPrep execution or cleanup."""

    output_root: Path
    state_path: Path
    results_path: Path
    log_dir: Path
    unit_results: tuple[FmriprepRunUnitResult, ...]
    cleanup_results: tuple[FmriprepRunUnitResult, ...] = ()
    backend: str = "local"
    status: str = "succeeded"
    skipped_units: int = 0
    skipped_succeeded: int = 0
    skipped_failed: int = 0
    skipped_active_claim: int = 0
    cleanup_selected: int = 0


RESULTS_TSV_COLUMNS = (
    "record_type",
    "unit_name",
    "participant_label",
    "status",
    "exit_code",
    "started_at",
    "finished_at",
    "log_path",
    "scheduler",
    "scheduler_job_id",
    "scheduler_task_id",
    "work_dir",
    "notes",
)


def plan_fmriprep_run(context: ProjectContext) -> FmriprepRunPlan:
    """Plan fMRIPrep execution from participants.tsv and target config."""

    participants_path, entries, ready_entries = _resolve_run_participants(context)
    fmriprep_config = load_fmriprep_config(context)
    config_path = fmriprep_config.config_path
    if not config_path.exists():
        raise FmriprepRunError(
            f"fMRIPrep target config does not exist: {config_path}. "
            "Run `bidsflow fmriprep init` first."
        )
    options = load_fmriprep_options(config_path)

    attempt_label = _format_attempt_label()
    state_root = context.paths.state_root / "fmriprep"
    state_path = state_root / "run.json"
    results_path = state_root / "results.tsv"
    unit_state_dir = state_root / "units"
    run_claim_dir = state_root / "claims" / "run"
    cleanup_claim_dir = state_root / "claims" / "cleanup"
    log_dir = context.paths.logs_root / "fmriprep" / "local" / f"run-{attempt_label}"
    output_root = context.paths.derivatives_root / "fmriprep"
    warnings = _build_preflight_warnings(context)
    managed_args = _build_managed_args(context, output_root)

    units: list[FmriprepRunUnitPlan] = []
    for index, entry in enumerate(ready_entries, start=1):
        unit_name = build_subject_session_unit_name(entry.participant_label, None)
        work_dir = context.paths.work_root / "fmriprep" / unit_name
        command = _build_run_command(
            launcher=fmriprep_config.launcher,
            raw_bids_root=context.paths.raw_bids_root,
            output_root=output_root,
            participant_label=entry.participant_label,
            work_dir=work_dir,
            managed_args=managed_args,
            options_args=options.argv,
        )
        units.append(
            FmriprepRunUnitPlan(
                index=index,
                unit_name=unit_name,
                participant_label=entry.participant_label,
                command=command,
                work_dir=work_dir,
                log_path=log_dir / f"{unit_name}.log",
                status_path=unit_state_dir / f"{unit_name}.status",
                claim_path=run_claim_dir / f"{unit_name}.running",
                cleanup_claim_path=cleanup_claim_dir / f"{unit_name}.running",
            )
        )

    unit_tuple = tuple(units)
    return FmriprepRunPlan(
        attempt_label=attempt_label,
        participants_path=participants_path,
        config_path=config_path,
        options=options,
        raw_bids_root=context.paths.raw_bids_root,
        output_root=output_root,
        state_path=state_path,
        results_path=results_path,
        unit_state_dir=unit_state_dir,
        run_claim_dir=run_claim_dir,
        cleanup_claim_dir=cleanup_claim_dir,
        log_dir=log_dir,
        entries=entries,
        input_signature=_build_run_input_signature(
            launcher=fmriprep_config.launcher,
            raw_bids_root=context.paths.raw_bids_root,
            output_root=output_root,
            options=options,
            managed_args=managed_args,
            ready_entries=ready_entries,
        ),
        units=unit_tuple,
        cleanup_units=_select_cleanup_pending_units(unit_tuple),
        warnings=warnings,
    )


def preview_fmriprep_unit_selection(
    plan: FmriprepRunPlan,
    *,
    include_failed: bool = False,
) -> tuple[tuple[FmriprepRunUnitPlan, ...], dict[str, int]]:
    """Return units that would run now without creating claims or files."""

    selection = preview_runnable_units(plan.units, include_failed=include_failed)
    return selection.runnable_units, build_unit_counts(plan.units, selection)


def run_fmriprep(
    context: ProjectContext,
    plan: FmriprepRunPlan,
    *,
    include_failed: bool = False,
) -> FmriprepRunResult:
    """Execute a planned fMRIPrep run locally."""

    if context.execution.scheduler != "none":
        raise FmriprepRunError(
            "fMRIPrep scheduler execution is not implemented yet on this branch. "
            "Set [execution].scheduler = \"none\" for local execution."
        )

    started_at = _utc_now()
    selection: UnitSelection[FmriprepRunUnitPlan] | None = None
    unit_counts: dict[str, int] | None = None
    unit_results: list[FmriprepRunUnitResult] = []
    cleanup_results: list[FmriprepRunUnitResult] = []
    try:
        selection = claim_runnable_units(units=plan.units, include_failed=include_failed)
        unit_counts = build_unit_counts(plan.units, selection)
        if not selection.runnable_units:
            if plan.cleanup_units:
                _prepare_run_directories(plan)
                _ensure_results_tsv_header(plan.results_path)
                _write_run_state(
                    context=context,
                    plan=plan,
                    record_state="running",
                    started_at=started_at,
                    planned_units=unit_counts,
                )
            cleanup_results.extend(_run_cleanup_pending_units(context, plan))
            if cleanup_results:
                _write_run_state(
                    context=context,
                    plan=plan,
                    record_state="succeeded",
                    started_at=started_at,
                    finished_at=_utc_now(),
                    planned_units=unit_counts,
                )
            return _build_skipped_run_result(
                plan,
                unit_counts,
                cleanup_results=tuple(cleanup_results),
            )

        _prepare_run_directories(plan)
        _ensure_results_tsv_header(plan.results_path)
        _write_run_state(
            context=context,
            plan=plan,
            record_state="running",
            started_at=started_at,
            planned_units=unit_counts,
        )

        for unit in selection.runnable_units:
            unit_result = _run_local_unit(context, plan, unit)
            unit_results.append(unit_result)
            if unit_result.status == "succeeded":
                cleanup_results.append(_cleanup_unit_workdir(context, plan, unit))
    except (FmriprepRunError, OSError) as exc:
        run_error = _coerce_run_error(exc, context_message="Failed during fMRIPrep run")
        if selection is not None:
            release_unfinished_claims(
                selection.runnable_units,
                {result.unit_name for result in unit_results},
            )
        _write_run_state(
            context=context,
            plan=plan,
            record_state="failed",
            started_at=started_at,
            finished_at=_utc_now(),
            planned_units=unit_counts,
            error=str(run_error),
        )
        if isinstance(exc, FmriprepRunError):
            raise
        raise run_error from exc

    _write_run_state(
        context=context,
        plan=plan,
        record_state="succeeded",
        started_at=started_at,
        finished_at=_utc_now(),
        planned_units=unit_counts,
    )

    return FmriprepRunResult(
        output_root=plan.output_root,
        state_path=plan.state_path,
        results_path=plan.results_path,
        log_dir=plan.log_dir,
        unit_results=tuple(unit_results),
        cleanup_results=tuple(cleanup_results),
        skipped_units=unit_counts["skipped"],
        skipped_succeeded=unit_counts["skipped_succeeded"],
        skipped_failed=unit_counts["skipped_failed"],
        skipped_active_claim=unit_counts["skipped_active_claim"],
        cleanup_selected=len(cleanup_results),
    )


def _resolve_run_participants(
    context: ProjectContext,
) -> tuple[Path, tuple[ParticipantEntry, ...], tuple[ParticipantEntry, ...]]:
    """Resolve and validate participants.tsv for a run."""

    participants_path = context.paths.state_root / "fmriprep" / "participants.tsv"
    if not participants_path.exists():
        raise FmriprepRunError(
            f"BIDSFlow fMRIPrep participants table does not exist: {participants_path}. "
            "Run `bidsflow fmriprep init` first."
        )
    if not context.paths.raw_bids_root.is_dir():
        raise FmriprepRunError(f"Raw BIDS root is not a directory: {context.paths.raw_bids_root}")

    entries = load_participants(context.paths.raw_bids_root, participants_path)
    ready_entries = tuple(entry for entry in entries if entry.include and entry.status == "ready")
    blocking_entries = tuple(
        entry for entry in entries if entry.include and entry.status != "ready"
    )
    if blocking_entries:
        issue_summary = "; ".join(list_participants_review_issues(entries))
        suffix = f" Issues: {issue_summary}" if issue_summary else ""
        raise FmriprepRunError(
            "BIDSFlow fMRIPrep participants table still needs review before fMRIPrep can run."
            + suffix
        )
    if not ready_entries:
        raise FmriprepRunError(
            "BIDSFlow fMRIPrep participants table does not contain any included ready rows to run."
        )
    return participants_path, entries, ready_entries


def _build_managed_args(context: ProjectContext, output_root: Path) -> tuple[str, ...]:
    """Build BIDSFlow-managed fMRIPrep options."""

    args: list[str] = []
    raw_layout_dir = context.paths.state_root / "layouts" / "raw"
    if raw_layout_dir.is_dir():
        args.extend(("--bids-database-dir", str(raw_layout_dir)))
    if context.resources.fs_license_file is not None:
        args.extend(("--fs-license-file", str(context.resources.fs_license_file)))
    return tuple(args)


def _build_preflight_warnings(context: ProjectContext) -> tuple[str, ...]:
    """Collect non-fatal warnings that should be visible before execution."""

    warnings: list[str] = []
    raw_layout_dir = context.paths.state_root / "layouts" / "raw"
    if raw_layout_dir.is_dir():
        warnings.append(
            "Using raw BIDS layout database; rebuild with `bidsflow fmriprep init -l -f` "
            "if raw BIDS changed."
        )
    else:
        warnings.append(
            "Raw BIDS layout database not found; fMRIPrep will index raw BIDS itself. "
            "Build one with `bidsflow fmriprep init -l` for repeated runs."
        )
    if context.resources.fs_license_file is None:
        warnings.append(
            "No [resources].fs_license_file is configured; fMRIPrep may fail when "
            "FreeSurfer-dependent outputs are requested."
        )
    elif not context.resources.fs_license_file.exists():
        warnings.append(
            f"Configured FreeSurfer license file does not exist: {context.resources.fs_license_file}"
        )
    return tuple(warnings)


def _build_run_command(
    *,
    launcher: tuple[str, ...],
    raw_bids_root: Path,
    output_root: Path,
    participant_label: str,
    work_dir: Path,
    managed_args: tuple[str, ...],
    options_args: tuple[str, ...],
) -> tuple[str, ...]:
    """Build one fMRIPrep participant command."""

    return (
        *launcher,
        str(raw_bids_root),
        str(output_root),
        "participant",
        "--participant-label",
        participant_label,
        "-w",
        str(work_dir),
        *managed_args,
        *options_args,
    )


def _build_run_input_signature(
    *,
    launcher: tuple[str, ...],
    raw_bids_root: Path,
    output_root: Path,
    options: FmriprepOptions,
    managed_args: tuple[str, ...],
    ready_entries: tuple[ParticipantEntry, ...],
) -> str:
    """Hash the inputs that define the current fMRIPrep plan."""

    return _compute_input_signature(
        {
            "launcher": json.dumps(list(launcher), separators=(",", ":"), sort_keys=True),
            "raw_bids_root": str(raw_bids_root),
            "output_root": str(output_root),
            "managed_args": json.dumps(list(managed_args), separators=(",", ":")),
            "target_config_path": str(options.path),
            "target_config_content": options.path.read_bytes(),
            "participants": json.dumps(
                [
                    {
                        "participant_label": entry.participant_label,
                        "include": entry.include,
                        "status": entry.status,
                    }
                    for entry in ready_entries
                ],
                separators=(",", ":"),
                sort_keys=True,
            ),
        }
    )


def _prepare_run_directories(plan: FmriprepRunPlan) -> None:
    """Create directories needed before local fMRIPrep execution."""

    plan.output_root.mkdir(parents=True, exist_ok=True)
    plan.state_path.parent.mkdir(parents=True, exist_ok=True)
    plan.results_path.parent.mkdir(parents=True, exist_ok=True)
    plan.unit_state_dir.mkdir(parents=True, exist_ok=True)
    plan.run_claim_dir.mkdir(parents=True, exist_ok=True)
    plan.cleanup_claim_dir.mkdir(parents=True, exist_ok=True)
    plan.log_dir.mkdir(parents=True, exist_ok=True)


def _run_local_unit(
    context: ProjectContext,
    plan: FmriprepRunPlan,
    unit: FmriprepRunUnitPlan,
) -> FmriprepRunUnitResult:
    """Run one local fMRIPrep participant and record its status/result row."""

    unit_started_at = _utc_now()
    _write_unit_status(
        unit,
        status="running",
        backend="local",
        attempt_label=plan.attempt_label,
        started_at=unit_started_at,
        log_path=unit.log_path,
    )

    completed = _run_fmriprep_command(context, unit.log_path, unit.command, label=unit.unit_name)
    if completed.returncode != 0:
        error_message = (
            f"fMRIPrep failed while processing {unit.unit_name}. "
            f"See {unit.log_path} for details."
        )
        result = _build_unit_result(
            unit,
            record_type="run",
            status="failed",
            started_at=unit_started_at,
            finished_at=_utc_now(),
            exit_code=completed.returncode,
            notes=error_message,
        )
        _write_unit_status(
            unit,
            status="failed",
            backend="local",
            attempt_label=plan.attempt_label,
            started_at=result.started_at,
            finished_at=result.finished_at,
            exit_code=result.exit_code,
            log_path=result.log_path,
            error=error_message,
        )
        _append_results_tsv(plan.results_path, (result,))
        release_unit_claim(unit.claim_path)
        raise FmriprepRunError(error_message)

    result = _build_unit_result(
        unit,
        record_type="run",
        status="succeeded",
        started_at=unit_started_at,
        finished_at=_utc_now(),
        exit_code=completed.returncode,
    )
    _write_unit_status(
        unit,
        status="succeeded",
        backend="local",
        attempt_label=plan.attempt_label,
        started_at=result.started_at,
        finished_at=result.finished_at,
        exit_code=result.exit_code,
        log_path=result.log_path,
        cleanup_status="pending" if unit.work_dir.exists() else "succeeded",
    )
    _append_results_tsv(plan.results_path, (result,))
    release_unit_claim(unit.claim_path)
    return result


def _run_fmriprep_command(
    context: ProjectContext,
    log_path: Path,
    command: tuple[str, ...],
    *,
    label: str,
) -> subprocess.CompletedProcess[str]:
    """Run one local fMRIPrep command and append combined output to its log."""

    try:
        completed = subprocess.run(
            list(command),
            cwd=context.project_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        _append_log(log_path, f"[{label}] Failed to start launcher: {exc}")
        raise FmriprepRunError(
            f"Failed to start fMRIPrep launcher. See {log_path} for details."
        ) from exc

    combined_output = _combine_process_output(completed.stdout, completed.stderr)
    _append_log(
        log_path,
        "\n".join(
            (
                f"[{label}] Command: {format_command(command)}",
                combined_output.rstrip(),
            )
        ).rstrip(),
    )
    return completed


def _run_cleanup_pending_units(
    context: ProjectContext,
    plan: FmriprepRunPlan,
) -> tuple[FmriprepRunUnitResult, ...]:
    """Run cleanup for previously succeeded units with leftover work dirs."""

    cleanup_results: list[FmriprepRunUnitResult] = []
    for unit in plan.cleanup_units:
        if unit.cleanup_claim_path.exists():
            continue
        try:
            unit.cleanup_claim_path.parent.mkdir(parents=True, exist_ok=True)
            with unit.cleanup_claim_path.open("x", encoding="utf-8"):
                pass
        except FileExistsError:
            continue
        cleanup_results.append(_cleanup_unit_workdir(context, plan, unit))
    return tuple(cleanup_results)


def _cleanup_unit_workdir(
    context: ProjectContext,
    plan: FmriprepRunPlan,
    unit: FmriprepRunUnitPlan,
) -> FmriprepRunUnitResult:
    """Remove one successful unit's work dir and record cleanup as a warning-only step."""

    cleanup_started_at = _utc_now()
    existing_status = read_existing_key_value_status(unit.status_path)
    cleanup_log_path = Path(existing_status["log"]) if existing_status.get("log") else unit.log_path
    if not unit.cleanup_claim_path.exists():
        unit.cleanup_claim_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with unit.cleanup_claim_path.open("x", encoding="utf-8"):
                pass
        except FileExistsError:
            pass

    cleanup_status = "succeeded"
    notes = ""
    exit_code: int | None = 0
    try:
        if unit.work_dir.exists():
            _remove_project_path(context.project_root, unit.work_dir)
    except (OSError, ValueError) as exc:
        cleanup_status = "failed"
        exit_code = None
        notes = f"Cleanup failed: {exc}"
        _append_log(cleanup_log_path, f"[{unit.unit_name}] {notes}")

    cleanup_result = _build_unit_result(
        unit,
        record_type="cleanup",
        status=cleanup_status,
        started_at=cleanup_started_at,
        finished_at=_utc_now(),
        exit_code=exit_code,
        log_path=cleanup_log_path,
        notes=notes,
    )
    _write_unit_status(
        unit,
        status="succeeded",
        backend="local",
        attempt_label=plan.attempt_label,
        log_path=cleanup_log_path,
        cleanup_status=cleanup_status,
        cleanup_started_at=cleanup_result.started_at,
        cleanup_finished_at=cleanup_result.finished_at,
        warning=notes if cleanup_status == "failed" else None,
    )
    _append_results_tsv(plan.results_path, (cleanup_result,))
    release_unit_claim(unit.cleanup_claim_path)
    return cleanup_result


def _select_cleanup_pending_units(
    units: tuple[FmriprepRunUnitPlan, ...],
) -> tuple[FmriprepRunUnitPlan, ...]:
    """Find succeeded units that still have cleanup work to do."""

    cleanup_units: list[FmriprepRunUnitPlan] = []
    for unit in units:
        payload = read_existing_key_value_status(unit.status_path)
        if payload.get("status") != "succeeded":
            continue
        if payload.get("cleanup_status") == "succeeded":
            continue
        if not unit.work_dir.exists():
            continue
        cleanup_units.append(unit)
    return tuple(cleanup_units)


def _build_unit_result(
    unit: FmriprepRunUnitPlan,
    *,
    record_type: str,
    status: str,
    started_at: str,
    finished_at: str | None,
    exit_code: int | None,
    log_path: Path | None = None,
    notes: str = "",
) -> FmriprepRunUnitResult:
    """Build a per-unit run or cleanup result."""

    return FmriprepRunUnitResult(
        record_type=record_type,
        unit_name=unit.unit_name,
        participant_label=unit.participant_label,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        exit_code=exit_code,
        log_path=log_path or unit.log_path,
        work_dir=unit.work_dir,
        notes=notes,
    )


def _build_skipped_run_result(
    plan: FmriprepRunPlan,
    unit_counts: dict[str, int],
    *,
    cleanup_results: tuple[FmriprepRunUnitResult, ...],
) -> FmriprepRunResult:
    """Return a result for attempts where every run unit was skipped."""

    return FmriprepRunResult(
        output_root=plan.output_root,
        state_path=plan.state_path,
        results_path=plan.results_path,
        log_dir=plan.log_dir,
        unit_results=(),
        cleanup_results=cleanup_results,
        status="cleanup-only" if cleanup_results else "skipped",
        skipped_units=unit_counts["skipped"],
        skipped_succeeded=unit_counts["skipped_succeeded"],
        skipped_failed=unit_counts["skipped_failed"],
        skipped_active_claim=unit_counts["skipped_active_claim"],
        cleanup_selected=len(cleanup_results),
    )


def _ensure_results_tsv_header(path: Path) -> None:
    """Create results.tsv with its header if it does not exist."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(RESULTS_TSV_COLUMNS)


def _append_results_tsv(
    path: Path,
    unit_results: tuple[FmriprepRunUnitResult, ...],
) -> None:
    """Append fMRIPrep run or cleanup rows to results.tsv."""

    _ensure_results_tsv_header(path)
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        for result in unit_results:
            writer.writerow(
                (
                    result.record_type,
                    result.unit_name,
                    result.participant_label,
                    result.status,
                    "" if result.exit_code is None else str(result.exit_code),
                    result.started_at,
                    result.finished_at or "",
                    str(result.log_path),
                    "",
                    "",
                    "",
                    str(result.work_dir),
                    result.notes,
                )
            )


def _write_unit_status(
    unit: FmriprepRunUnitPlan,
    *,
    status: str,
    backend: str,
    attempt_label: str,
    started_at: str | None = None,
    finished_at: str | None = None,
    exit_code: int | None = None,
    log_path: Path | None = None,
    scheduler_job_id: str | None = None,
    scheduler_task_id: str | None = None,
    cleanup_status: str | None = None,
    cleanup_started_at: str | None = None,
    cleanup_finished_at: str | None = None,
    warning: str | None = None,
    error: str | None = None,
) -> None:
    """Write final or in-progress state for one fMRIPrep unit."""

    existing = read_existing_key_value_status(unit.status_path)
    exit_code_value = "" if exit_code is None else str(exit_code)
    if exit_code is None and status == "succeeded" and existing.get("status") == "succeeded":
        exit_code_value = existing.get("exit_code", "")
    lines = [
        ("status", status),
        ("unit", unit.unit_name),
        ("participant", unit.participant_label),
        ("backend", backend),
        ("attempt", attempt_label),
        ("started_at", started_at or existing.get("started_at", "")),
        ("finished_at", finished_at or existing.get("finished_at", "")),
        ("exit_code", exit_code_value),
        ("work_dir", str(unit.work_dir)),
    ]
    if log_path is not None:
        lines.append(("log", str(log_path)))
    elif existing.get("log"):
        lines.append(("log", existing["log"]))
    if scheduler_job_id is not None or scheduler_task_id is not None:
        lines.extend(
            (
                ("scheduler", "sge"),
                ("job_id", scheduler_job_id or ""),
                ("task_id", scheduler_task_id or ""),
            )
        )
    for key in ("cleanup_status", "cleanup_started_at", "cleanup_finished_at"):
        if key in existing:
            lines.append((key, existing[key]))
    _replace_or_append(lines, "cleanup_status", cleanup_status)
    _replace_or_append(lines, "cleanup_started_at", cleanup_started_at)
    _replace_or_append(lines, "cleanup_finished_at", cleanup_finished_at)
    _replace_or_append(lines, "warning", warning)
    _replace_or_append(lines, "error", error)
    _write_key_value_status_atomic(unit.status_path, lines)


def _replace_or_append(lines: list[tuple[str, str]], key: str, value: str | None) -> None:
    """Replace an existing status field or append it when a value is provided."""

    if value is None:
        return
    for index, (existing_key, _) in enumerate(lines):
        if existing_key == key:
            lines[index] = (key, value)
            return
    lines.append((key, value))


def _write_run_state(
    *,
    context: ProjectContext,
    plan: FmriprepRunPlan,
    record_state: str,
    started_at: str,
    finished_at: str | None = None,
    planned_units: dict[str, int] | None = None,
    error: str | None = None,
) -> None:
    """Write fMRIPrep run-level metadata without embedding per-unit details."""

    updated_at = _utc_now()
    payload: dict[str, object] = {
        "workflow": "fmriprep",
        "step": "run",
        "backend": "local",
        "record_state": record_state,
        "created_at": started_at,
        "updated_at": updated_at,
        "input_signature": plan.input_signature,
        "config_path": str(context.config_path),
        "project_root": str(context.project_root),
        "participants_path": str(plan.participants_path),
        "target_config_path": str(plan.config_path),
        "raw_bids_root": str(plan.raw_bids_root),
        "output_root": str(plan.output_root),
        "execution": {
            "mode": "local",
            "started_at": started_at,
            "finished_at": finished_at,
        },
        "artifacts": {
            "results_table": str(plan.results_path),
            "unit_status_dir": str(plan.unit_state_dir),
            "run_claim_dir": str(plan.run_claim_dir),
            "cleanup_claim_dir": str(plan.cleanup_claim_dir),
            "log_dir": str(plan.log_dir),
        },
        "warnings": list(plan.warnings),
    }
    if planned_units is not None:
        payload["planned_units"] = planned_units
    if error is not None:
        payload["error"] = error
    _write_json(plan.state_path, payload)


def _coerce_run_error(
    exc: FmriprepRunError | OSError,
    *,
    context_message: str,
) -> FmriprepRunError:
    """Normalize raw filesystem errors to the public run exception type."""

    if isinstance(exc, FmriprepRunError):
        return exc
    return FmriprepRunError(f"{context_message}: {exc}")
