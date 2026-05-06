"""Managed HeuDiConv conversion planning, local execution, and SGE submission."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import json
from pathlib import Path
import shlex
import subprocess

from ..common import (
    _append_log,
    _combine_process_output,
    _compute_input_signature,
    _format_attempt_label,
    _make_executable,
    _remove_project_path,
    _read_template,
    _utc_now,
    _write_json,
    _write_key_value_status_atomic,
    format_command,
)
from ..project import HeudiconvConfig, ProjectContext, load_heudiconv_config
from ..schedulers import _resolve_scheduler_script_path
from .errors import HeudiconvInitError, HeudiconvRunError
from .sources import (
    SourcesEntry,
    _load_confirmed_sources,
    _resolve_sources_source_path,
    list_sources_review_issues,
)
from .state import build_run_unit_name, read_key_value_status


def _render_heudiconv_array_runtime_script() -> str:
    """Load the target-specific array runtime script template."""

    return _read_template("heudiconv", "array-runtime.sh.template")


def _render_array_common_runtime_script() -> str:
    """Load the shared array runtime helper template."""

    return _read_template("runtime", "array-common.sh.template")


@dataclass(frozen=True)
class RunUnitPlan:
    """One reviewed source unit ready for HeuDiConv conversion."""

    index: int
    unit_name: str
    source_name: str
    source_path: Path
    subject_label: str
    session_label: str | None
    execution_path: Path
    command: tuple[str, ...]
    log_path: Path
    status_path: Path
    claim_path: Path


@dataclass(frozen=True)
class SgeRunPlan:
    """SGE wrapper, runtime, unit-list, and log paths for a scheduled run."""

    template_path: Path
    script_path: Path
    common_runtime_script_path: Path
    runtime_script_path: Path
    unit_list_path: Path
    scheduler_log_dir: Path
    submit_command: tuple[str, ...]


@dataclass(frozen=True)
class RunPlan:
    """Complete plan for a managed HeuDiConv conversion attempt."""

    attempt_label: str
    sources_path: Path
    sources_state_path: Path
    launcher: tuple[str, ...]
    heuristic_path: Path
    raw_bids_root: Path
    execution_view_root: Path
    state_path: Path
    results_path: Path
    unit_state_dir: Path
    claim_dir: Path
    log_dir: Path
    entries: tuple[SourcesEntry, ...]
    input_signature: str
    units: tuple[RunUnitPlan, ...]
    sge: SgeRunPlan | None


@dataclass(frozen=True)
class RunUnitResult:
    """Final local result or submitted scheduler record for one run unit."""

    index: int
    unit_name: str
    source_name: str
    source_path: Path
    subject_label: str
    session_label: str | None
    execution_path: Path
    command: tuple[str, ...]
    log_path: Path
    status: str
    started_at: str
    finished_at: str | None
    exit_code: int | None


@dataclass(frozen=True)
class RunResult:
    """Overall result returned after local completion or scheduler submission."""

    raw_bids_root: Path
    state_path: Path
    results_path: Path
    log_dir: Path
    unit_results: tuple[RunUnitResult, ...]
    backend: str = "local"
    status: str = "succeeded"
    skipped_units: int = 0
    skipped_succeeded: int = 0
    skipped_failed: int = 0
    skipped_active_claim: int = 0
    scheduler_job_id: str | None = None
    scheduler_script_path: Path | None = None
    scheduler_log_dir: Path | None = None


@dataclass(frozen=True)
class RunUnitSelection:
    """Runnable units plus skip counts for the current attempt."""

    runnable_units: tuple[RunUnitPlan, ...]
    skipped_succeeded: int
    skipped_failed: int
    skipped_active_claim: int


def _build_skipped_run_result(
    plan: RunPlan,
    unit_counts: dict[str, int],
    *,
    backend: str,
) -> RunResult:
    """Return a run result for attempts where every unit was skipped."""

    scheduler_script_path = None
    scheduler_log_dir = None
    if backend == "sge" and plan.sge is not None:
        scheduler_script_path = plan.sge.script_path
        scheduler_log_dir = plan.sge.scheduler_log_dir
    return RunResult(
        raw_bids_root=plan.raw_bids_root,
        state_path=plan.state_path,
        results_path=plan.results_path,
        log_dir=plan.log_dir,
        unit_results=(),
        backend=backend,
        status="skipped",
        skipped_units=unit_counts["skipped"],
        skipped_succeeded=unit_counts["skipped_succeeded"],
        skipped_failed=unit_counts["skipped_failed"],
        skipped_active_claim=unit_counts["skipped_active_claim"],
        scheduler_script_path=scheduler_script_path,
        scheduler_log_dir=scheduler_log_dir,
    )


def _build_run_unit_result(
    unit: RunUnitPlan,
    *,
    status: str,
    started_at: str,
    finished_at: str | None,
    exit_code: int | None,
) -> RunUnitResult:
    """Build the shared per-unit result record used by local and SGE paths."""

    return RunUnitResult(
        index=unit.index,
        unit_name=unit.unit_name,
        source_name=unit.source_name,
        source_path=unit.source_path,
        subject_label=unit.subject_label,
        session_label=unit.session_label,
        execution_path=unit.execution_path,
        command=unit.command,
        log_path=unit.log_path,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        exit_code=exit_code,
    )


def _clear_prior_run_execution_view(context: ProjectContext, plan: RunPlan) -> None:
    """Clear stale temporary source links before a new run writes its view."""

    execution_view_cleared = _cleanup_run_execution_view(
        context.project_root,
        plan.execution_view_root,
    )
    if not execution_view_cleared:
        raise HeudiconvRunError(
            f"Failed to clear prior run execution view: {plan.execution_view_root}"
        )


def _cleanup_execution_view_if_requested(
    context: ProjectContext,
    plan: RunPlan,
    *,
    cleanup_execution_view: bool,
) -> bool | None:
    """Clean the temporary source view when the run option requests it."""

    if not cleanup_execution_view:
        return None
    return _cleanup_run_execution_view(
        context.project_root,
        plan.execution_view_root,
    )


def plan_heudiconv_run(context: ProjectContext) -> RunPlan:
    """Plan a managed HeuDiConv conversion from reviewed sources.tsv rows."""

    heudiconv_config = load_heudiconv_config(context)
    sources_path, heuristic_path, entries, ready_entries = _resolve_run_inputs(
        context,
        heudiconv_config,
    )

    state_root = context.paths.state_root / "heudiconv"
    attempt_label = _format_attempt_label()
    execution_view_root = context.paths.work_root / "heudiconv" / f"run-{attempt_label}"
    state_path = state_root / "run.json"
    results_path = state_root / "results.tsv"
    unit_state_dir = state_root / "units"
    claim_dir = state_root / "claims"
    log_root = context.paths.logs_root / "heudiconv"
    scheduler_name = context.execution.scheduler
    log_dir = log_root / ("sge" if scheduler_name == "sge" else "local") / f"run-{attempt_label}"

    units: list[RunUnitPlan] = []
    for index, entry in enumerate(ready_entries, start=1):
        session_label = entry.session_label or None
        unit_name = build_run_unit_name(entry.subject_label, session_label)
        execution_path = _build_run_execution_path(
            execution_view_root,
            entry.subject_label,
            session_label,
        )
        units.append(
            RunUnitPlan(
                index=index,
                unit_name=unit_name,
                source_name=entry.source_name,
                source_path=_resolve_sources_source_path(
                    context.paths.source_root,
                    entry.source_name,
                ),
                subject_label=entry.subject_label,
                session_label=session_label,
                execution_path=execution_path,
                command=_build_run_command(
                    launcher=heudiconv_config.launcher,
                    execution_path=execution_path,
                    raw_bids_root=context.paths.raw_bids_root,
                    heuristic_path=heuristic_path,
                    subject_label=entry.subject_label,
                    session_label=session_label,
                ),
                log_path=log_dir / f"{unit_name}.log",
                status_path=unit_state_dir / f"{unit_name}.status",
                claim_path=claim_dir / f"{unit_name}.running",
            )
        )

    input_signature = _build_run_input_signature(
        launcher=heudiconv_config.launcher,
        heuristic_path=heuristic_path,
        raw_bids_root=context.paths.raw_bids_root,
        source_root=context.paths.source_root,
        ready_entries=ready_entries,
    )

    return RunPlan(
        attempt_label=attempt_label,
        sources_path=sources_path,
        sources_state_path=context.paths.state_root / "sources.json",
        launcher=heudiconv_config.launcher,
        heuristic_path=heuristic_path,
        raw_bids_root=context.paths.raw_bids_root,
        execution_view_root=execution_view_root,
        state_path=state_path,
        results_path=results_path,
        unit_state_dir=unit_state_dir,
        claim_dir=claim_dir,
        log_dir=log_dir,
        entries=entries,
        input_signature=input_signature,
        units=tuple(units),
        sge=_build_sge_run_plan(
            context,
            execution_view_root=execution_view_root,
            log_dir=log_dir,
        ),
    )


def _resolve_run_inputs(
    context: ProjectContext,
    heudiconv_config: HeudiconvConfig,
) -> tuple[Path, Path, tuple[SourcesEntry, ...], tuple[SourcesEntry, ...]]:
    """Resolve and validate reviewed sources and heuristic inputs."""

    sources_path = context.paths.state_root / "sources.tsv"
    if not sources_path.exists():
        raise HeudiconvRunError(
            f"BIDSFlow sources table does not exist: {sources_path}. Run `bidsflow heudiconv init` first."
        )

    heuristic_path = heudiconv_config.heuristic
    if not heuristic_path.exists():
        raise HeudiconvRunError(
            f"HeuDiConv heuristic does not exist: {heuristic_path}. Run `bidsflow heudiconv draft <sample-path>` or create the heuristic first."
        )
    if not heuristic_path.is_file():
        raise HeudiconvRunError(
            f"HeuDiConv heuristic is not a file: {heuristic_path}"
        )

    entries = _load_confirmed_sources(context.paths.source_root, sources_path)
    ready_entries = tuple(
        entry for entry in entries if entry.include and entry.status == "ready"
    )
    blocking_entries = tuple(
        entry for entry in entries if entry.include and entry.status != "ready"
    )

    if blocking_entries:
        issue_summary = "; ".join(list_sources_review_issues(entries))
        suffix = f" Issues: {issue_summary}" if issue_summary else ""
        raise HeudiconvRunError(
            "BIDSFlow sources table still needs review before HeuDiConv can run." + suffix
        )
    if not ready_entries:
        raise HeudiconvRunError(
            "BIDSFlow sources table does not contain any included ready rows to run."
        )

    return sources_path, heuristic_path, entries, ready_entries


def _build_sge_run_plan(
    context: ProjectContext,
    *,
    execution_view_root: Path,
    log_dir: Path,
) -> SgeRunPlan | None:
    """Build scheduler file paths when SGE execution is configured."""

    if context.execution.scheduler == "none":
        return None
    if context.execution.scheduler != "sge":  # pragma: no cover
        raise HeudiconvRunError(
            f"Unsupported scheduler for HeuDiConv run: {context.execution.scheduler}"
        )
    if context.execution.submit_command is None:  # pragma: no cover
        raise HeudiconvRunError(
            "[execution].submit_command is required when scheduler is 'sge'."
        )

    try:
        scheduler_script_template_path = _resolve_scheduler_script_path(
            context,
            target="heudiconv",
        )
    except HeudiconvInitError as exc:  # pragma: no cover
        raise HeudiconvRunError(str(exc)) from exc
    if not scheduler_script_template_path.is_file():
        raise HeudiconvRunError(
            "SGE scheduler script does not exist: "
            f"{scheduler_script_template_path}. Run `bidsflow heudiconv init` first."
        )

    scheduler_root = execution_view_root / ".bidsflow" / "scheduler" / "sge"
    return SgeRunPlan(
        template_path=scheduler_script_template_path,
        script_path=scheduler_root / "heudiconv.sh",
        common_runtime_script_path=scheduler_root / "array-common.sh",
        runtime_script_path=scheduler_root / "runtime.sh",
        unit_list_path=scheduler_root / "units.tsv",
        scheduler_log_dir=log_dir,
        submit_command=context.execution.submit_command,
    )


def run_heudiconv(
    context: ProjectContext,
    plan: RunPlan,
    *,
    cleanup_execution_view: bool = True,
    include_failed: bool = False,
) -> RunResult:
    """Execute or submit a planned HeuDiConv run."""

    if plan.sge is not None:
        return _submit_sge_heudiconv_run(
            context,
            plan,
            cleanup_execution_view=cleanup_execution_view,
            include_failed=include_failed,
        )

    started_at = _utc_now()
    claim_selection: RunUnitSelection | None = None
    unit_counts: dict[str, int] | None = None
    unit_results: list[RunUnitResult] = []
    try:
        claim_selection = _claim_runnable_units(
            units=plan.units,
            include_failed=include_failed,
        )
        unit_counts = _build_run_unit_counts(plan.units, claim_selection)
        if not claim_selection.runnable_units:
            return _build_skipped_run_result(plan, unit_counts, backend="local")

        _clear_prior_run_execution_view(context, plan)
        _prepare_run_directories(plan)
        _ensure_results_tsv_header(plan.results_path)

        _write_run_state(
            context=context,
            plan=plan,
            record_state="running",
            started_at=started_at,
            cleanup_execution_view=cleanup_execution_view,
            planned_units=unit_counts,
        )

        _run_local_units(
            context,
            plan,
            claim_selection.runnable_units,
            unit_results,
        )
    except (HeudiconvRunError, OSError) as exc:
        run_error = _coerce_run_error(exc, context_message="Failed during HeuDiConv run")
        if claim_selection is not None:
            _release_unfinished_claims(claim_selection.runnable_units, unit_results)
        execution_view_cleaned = _cleanup_execution_view_if_requested(
            context,
            plan,
            cleanup_execution_view=cleanup_execution_view,
        )
        _write_run_state(
            context=context,
            plan=plan,
            record_state="failed",
            started_at=started_at,
            finished_at=_utc_now(),
            cleanup_execution_view=cleanup_execution_view,
            execution_view_cleaned=execution_view_cleaned,
            planned_units=unit_counts,
            error=str(run_error),
        )
        if isinstance(exc, HeudiconvRunError):
            raise
        raise run_error from exc

    execution_view_cleaned = _cleanup_execution_view_if_requested(
        context,
        plan,
        cleanup_execution_view=cleanup_execution_view,
    )
    _write_run_state(
        context=context,
        plan=plan,
        record_state="succeeded",
        started_at=started_at,
        finished_at=_utc_now(),
        cleanup_execution_view=cleanup_execution_view,
        execution_view_cleaned=execution_view_cleaned,
        planned_units=unit_counts,
    )

    return RunResult(
        raw_bids_root=plan.raw_bids_root,
        state_path=plan.state_path,
        results_path=plan.results_path,
        log_dir=plan.log_dir,
        unit_results=tuple(unit_results),
        skipped_units=unit_counts["skipped"],
        skipped_succeeded=unit_counts["skipped_succeeded"],
        skipped_failed=unit_counts["skipped_failed"],
        skipped_active_claim=unit_counts["skipped_active_claim"],
    )


def _run_local_units(
    context: ProjectContext,
    plan: RunPlan,
    units: tuple[RunUnitPlan, ...],
    unit_results: list[RunUnitResult],
) -> None:
    """Run local units in sequence and append completed results."""

    for unit in units:
        unit_results.append(_run_local_unit(context, plan, unit))


def _run_local_unit(
    context: ProjectContext,
    plan: RunPlan,
    unit: RunUnitPlan,
) -> RunUnitResult:
    """Run one local HeuDiConv unit and record its status/result row."""

    unit_started_at = _utc_now()
    _write_unit_status(
        unit,
        status="running",
        backend="local",
        attempt_label=plan.attempt_label,
        started_at=unit_started_at,
        log_path=unit.log_path,
    )

    try:
        _materialize_run_execution_view(unit.execution_path, unit.source_path)
        completed = _run_heudiconv_command(
            context,
            unit.log_path,
            unit.command,
            label=unit.source_name,
        )
    except HeudiconvRunError as exc:
        _record_failed_local_unit(
            plan,
            unit,
            started_at=unit_started_at,
            exit_code=None,
            error=str(exc),
        )
        raise

    if completed.returncode != 0:
        error_message = (
            "HeuDiConv execution failed while processing "
            f"{unit.source_name}. See {unit.log_path} for details."
        )
        _record_failed_local_unit(
            plan,
            unit,
            started_at=unit_started_at,
            exit_code=completed.returncode,
            error=error_message,
        )
        raise HeudiconvRunError(error_message)

    result = _build_run_unit_result(
        unit,
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
    )
    _append_results_tsv(plan.results_path, (result,))
    _release_unit_claim(unit)
    return result


def _record_failed_local_unit(
    plan: RunPlan,
    unit: RunUnitPlan,
    *,
    started_at: str,
    exit_code: int | None,
    error: str,
) -> None:
    """Write failure artifacts for one local unit before raising."""

    result = _build_run_unit_result(
        unit,
        status="failed",
        started_at=started_at,
        finished_at=_utc_now(),
        exit_code=exit_code,
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
        error=error,
    )
    _append_results_tsv(plan.results_path, (result,))
    _release_unit_claim(unit)


def _submit_sge_heudiconv_run(
    context: ProjectContext,
    plan: RunPlan,
    *,
    cleanup_execution_view: bool,
    include_failed: bool,
) -> RunResult:
    """Prepare run state and submit an SGE array job."""

    sge = plan.sge
    if sge is None:  # pragma: no cover
        raise HeudiconvRunError("SGE run plan is missing.")

    started_at = _utc_now()
    claim_selection: RunUnitSelection | None = None
    unit_counts: dict[str, int] | None = None
    unit_results: tuple[RunUnitResult, ...] = ()
    try:
        claim_selection = _claim_runnable_units(
            units=plan.units,
            include_failed=include_failed,
        )
        unit_counts = _build_run_unit_counts(plan.units, claim_selection)
        if not claim_selection.runnable_units:
            return _build_skipped_run_result(plan, unit_counts, backend="sge")

        _clear_prior_run_execution_view(context, plan)
        _prepare_run_directories(plan)
        _ensure_results_tsv_header(plan.results_path)

        scheduler_metadata = _build_sge_run_metadata(plan, units=claim_selection.runnable_units)
        _write_run_state(
            context=context,
            plan=plan,
            record_state="preparing",
            started_at=started_at,
            backend="sge",
            cleanup_execution_view=cleanup_execution_view,
            scheduler=scheduler_metadata,
            planned_units=unit_counts,
        )

        unit_results = _prepare_sge_units_for_submission(
            plan,
            sge,
            claim_selection.runnable_units,
            started_at=started_at,
        )

        _write_sge_run_files(
            context,
            plan,
            claim_selection.runnable_units,
            cleanup_execution_view=cleanup_execution_view,
        )
        completed = _submit_sge_script(context, sge)
        if completed.returncode != 0:
            details = _combine_process_output(completed.stdout, completed.stderr).strip()
            suffix = f" {details}" if details else ""
            raise HeudiconvRunError(
                "Failed to submit HeuDiConv SGE array job with "
                f"{format_command(sge.submit_command)}.{suffix}"
            )
        scheduler_job_id = _parse_sge_job_id(completed.stdout)
    except (HeudiconvRunError, OSError) as exc:
        run_error = _coerce_run_error(exc, context_message="Failed during HeuDiConv SGE run")
        if claim_selection is not None:
            _record_sge_submit_failure(
                plan,
                sge,
                claim_selection.runnable_units,
                started_at=started_at,
                error=str(run_error),
            )
        execution_view_cleaned = _cleanup_execution_view_if_requested(
            context,
            plan,
            cleanup_execution_view=cleanup_execution_view,
        )
        scheduler_metadata = _build_sge_run_metadata(
            plan,
            units=() if claim_selection is None else claim_selection.runnable_units,
        )
        _write_run_state(
            context=context,
            plan=plan,
            record_state="submit_failed",
            started_at=started_at,
            finished_at=_utc_now(),
            backend="sge",
            cleanup_execution_view=cleanup_execution_view,
            execution_view_cleaned=execution_view_cleaned,
            scheduler=scheduler_metadata,
            planned_units=unit_counts,
            error=str(run_error),
        )
        if isinstance(exc, HeudiconvRunError):
            raise
        raise run_error from exc

    scheduler_metadata = _build_sge_run_metadata(
        plan,
        units=claim_selection.runnable_units,
        job_id=scheduler_job_id,
    )
    _write_run_state(
        context=context,
        plan=plan,
        record_state="submitted",
        started_at=started_at,
        finished_at=_utc_now(),
        backend="sge",
        cleanup_execution_view=cleanup_execution_view,
        scheduler=scheduler_metadata,
        planned_units=unit_counts,
    )

    return RunResult(
        raw_bids_root=plan.raw_bids_root,
        state_path=plan.state_path,
        results_path=plan.results_path,
        log_dir=plan.log_dir,
        unit_results=unit_results,
        backend="sge",
        status="submitted",
        skipped_units=unit_counts["skipped"],
        skipped_succeeded=unit_counts["skipped_succeeded"],
        skipped_failed=unit_counts["skipped_failed"],
        skipped_active_claim=unit_counts["skipped_active_claim"],
        scheduler_job_id=scheduler_job_id,
        scheduler_script_path=sge.script_path,
        scheduler_log_dir=sge.scheduler_log_dir,
    )


def _prepare_sge_units_for_submission(
    plan: RunPlan,
    sge: SgeRunPlan,
    units: tuple[RunUnitPlan, ...],
    *,
    started_at: str,
) -> tuple[RunUnitResult, ...]:
    """Create execution links and mark each SGE array task as submitted."""

    unit_results: list[RunUnitResult] = []
    for task_id, unit in enumerate(units, start=1):
        _materialize_run_execution_view(unit.execution_path, unit.source_path)
        _write_unit_status(
            unit,
            status="submitted",
            backend="sge",
            attempt_label=plan.attempt_label,
            started_at=started_at,
            log_dir=sge.scheduler_log_dir,
            scheduler_task_id=str(task_id),
        )
        unit_results.append(
            _build_run_unit_result(
                unit,
                status="submitted",
                started_at=started_at,
                finished_at=None,
                exit_code=None,
            )
        )
    return tuple(unit_results)


def _record_sge_submit_failure(
    plan: RunPlan,
    sge: SgeRunPlan,
    units: tuple[RunUnitPlan, ...],
    *,
    started_at: str,
    error: str,
) -> None:
    """Mark claimed SGE units as submit_failed and release their claims."""

    for unit in units:
        _write_unit_status(
            unit,
            status="submit_failed",
            backend="sge",
            attempt_label=plan.attempt_label,
            started_at=started_at,
            finished_at=_utc_now(),
            log_dir=sge.scheduler_log_dir,
            error=error,
        )
        _release_unit_claim(unit)


def _write_sge_run_files(
    context: ProjectContext,
    plan: RunPlan,
    units: tuple[RunUnitPlan, ...],
    *,
    cleanup_execution_view: bool,
) -> None:
    """Render per-run SGE wrapper, runtime scripts, and unit list."""

    sge = plan.sge
    if sge is None:  # pragma: no cover
        raise HeudiconvRunError("SGE run plan is missing.")

    _write_sge_unit_list(sge.unit_list_path, units)
    common_template = _render_array_common_runtime_script()
    common_rendered = (
        common_template.replace("{{ attempt_label }}", plan.attempt_label)
        .replace("{{ shell_unit_list_path }}", shlex.quote(str(sge.unit_list_path)))
        .replace("{{ shell_results_table_path }}", shlex.quote(str(plan.results_path)))
        .replace("{{ shell_scheduler_log_dir }}", shlex.quote(str(sge.scheduler_log_dir)))
        .replace("{{ shell_project_root }}", shlex.quote(str(context.project_root)))
        .replace(
            "{{ cleanup_execution_view }}",
            "true" if cleanup_execution_view else "false",
        )
    )
    if "{{" in common_rendered or "}}" in common_rendered:  # pragma: no cover
        raise HeudiconvRunError(
            "Bundled array common runtime script contains unsupported placeholders."
        )
    sge.common_runtime_script_path.write_text(
        common_rendered,
        encoding="utf-8",
        newline="\n",
    )
    _make_executable(sge.common_runtime_script_path)

    runtime_template = _render_heudiconv_array_runtime_script()
    runtime_rendered = (
        runtime_template.replace(
            "{{ common_runtime_script }}",
            shlex.quote(str(sge.common_runtime_script_path)),
        )
        .replace("{{ shell_raw_bids_root }}", shlex.quote(str(plan.raw_bids_root)))
        .replace("{{ shell_heuristic_path }}", shlex.quote(str(plan.heuristic_path)))
        .replace("{{ shell_launcher_items }}", _format_shell_array_items(plan.launcher))
    )
    if "{{" in runtime_rendered or "}}" in runtime_rendered:  # pragma: no cover
        raise HeudiconvRunError(
            "Bundled HeuDiConv array runtime script contains unsupported placeholders."
        )
    sge.runtime_script_path.write_text(runtime_rendered, encoding="utf-8", newline="\n")
    _make_executable(sge.runtime_script_path)

    template = sge.template_path.read_text(encoding="utf-8")
    rendered = (
        template.replace("{{ job_name }}", "bidsflow-heudiconv")
        .replace("{{ task_count }}", str(len(units)))
        .replace("{{ attempt_label }}", plan.attempt_label)
        .replace("{{ scheduler_log_dir }}", str(sge.scheduler_log_dir))
        .replace("{{ runtime_script }}", shlex.quote(str(sge.runtime_script_path)))
    )
    if "{{" in rendered or "}}" in rendered:
        raise HeudiconvRunError(
            "SGE scheduler wrapper contains unsupported placeholders. "
            "Supported wrapper placeholders are {{ job_name }}, {{ task_count }}, "
            "{{ attempt_label }}, {{ scheduler_log_dir }}, and {{ runtime_script }}. "
            "If this wrapper was generated by an older BIDSFlow version, run "
            "`bidsflow heudiconv init --force` and reapply site-specific scheduler settings."
        )
    sge.script_path.write_text(rendered, encoding="utf-8", newline="\n")
    _make_executable(sge.script_path)


def _write_sge_unit_list(path: Path, units: tuple[RunUnitPlan, ...]) -> None:
    """Write the array task table consumed by the runtime script."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(
            (
                "unit_name",
                "source_name",
                "subject_label",
                "session_label",
                "execution_path",
                "claim_path",
                "unit_status_path",
            )
        )
        for unit in units:
            writer.writerow(
                (
                    unit.unit_name,
                    unit.source_name,
                    unit.subject_label,
                    unit.session_label or "",
                    str(unit.execution_path),
                    str(unit.claim_path),
                    str(unit.status_path),
                )
            )


def _format_shell_array_items(values: tuple[str, ...]) -> str:
    """Format shell array elements for a generated bash script."""

    return "\n".join(f"    {shlex.quote(value)}" for value in values)


def _submit_sge_script(
    context: ProjectContext,
    sge: SgeRunPlan,
) -> subprocess.CompletedProcess[str]:
    """Run the configured SGE submit command."""

    try:
        return subprocess.run(
            [*sge.submit_command, str(sge.script_path)],
            cwd=context.project_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise HeudiconvRunError(
            f"Failed to start SGE submit command {sge.submit_command[0]!r}: {exc}"
        ) from exc


def _parse_sge_job_id(stdout: str | None) -> str:
    """Extract the scheduler job id from qsub stdout."""

    output = (stdout or "").strip()
    if not output:
        return ""
    return output.splitlines()[0].strip()


def _build_sge_run_metadata(
    plan: RunPlan,
    *,
    units: tuple[RunUnitPlan, ...] | None = None,
    job_id: str | None = None,
) -> dict[str, object]:
    """Build SGE metadata for run.json."""

    sge = plan.sge
    if sge is None:  # pragma: no cover
        raise HeudiconvRunError("SGE run plan is missing.")

    planned_units = plan.units if units is None else units
    metadata: dict[str, object] = {
        "name": "sge",
        "array_range": f"1-{len(planned_units)}" if planned_units else "",
        "template_path": str(sge.template_path),
        "script_path": str(sge.script_path),
        "common_runtime_script_path": str(sge.common_runtime_script_path),
        "runtime_script_path": str(sge.runtime_script_path),
        "unit_list_path": str(sge.unit_list_path),
        "scheduler_log_dir": str(sge.scheduler_log_dir),
        "submit_command": list(sge.submit_command),
    }
    if job_id is not None:
        metadata["job_id"] = job_id
    return metadata


def _build_run_execution_path(
    execution_view_root: Path,
    subject_label: str,
    session_label: str | None,
) -> Path:
    """Build the temporary source view path passed to HeuDiConv."""

    if session_label is None:
        return execution_view_root / f"sub-{subject_label}"
    return execution_view_root / f"sub-{subject_label}" / f"ses-{session_label}"


def _build_run_command(
    *,
    launcher: tuple[str, ...],
    execution_path: Path,
    raw_bids_root: Path,
    heuristic_path: Path,
    subject_label: str,
    session_label: str | None,
) -> tuple[str, ...]:
    """Build the HeuDiConv conversion command for one run unit."""

    command: list[str] = [
        *launcher,
        "--files",
        str(execution_path),
        "-o",
        str(raw_bids_root),
        "-f",
        str(heuristic_path),
        "-c",
        "dcm2niix",
        "-b",
        "-s",
        subject_label,
    ]
    if session_label is not None:
        command.extend(["-ss", session_label])
    return tuple(command)


def _build_run_input_signature(
    *,
    launcher: tuple[str, ...],
    heuristic_path: Path,
    raw_bids_root: Path,
    source_root: Path,
    ready_entries: tuple[SourcesEntry, ...],
) -> str:
    """Hash the inputs that define the current conversion plan."""

    unit_payload = [
        {
            "source_name": entry.source_name,
            "source_path": str(_resolve_sources_source_path(source_root, entry.source_name)),
            "subject_label": entry.subject_label,
            "session_label": entry.session_label,
        }
        for entry in ready_entries
    ]
    return _compute_input_signature(
        {
            "launcher": json.dumps(list(launcher), separators=(",", ":"), sort_keys=True),
            "heuristic_path": str(heuristic_path),
            "heuristic_content": heuristic_path.read_bytes(),
            "raw_bids_root": str(raw_bids_root),
            "source_root": str(source_root),
            "units": json.dumps(unit_payload, separators=(",", ":"), sort_keys=True),
        }
    )


def _build_run_unit_summary(subject_label: str, session_label: str | None) -> str:
    """Return a compact subject/session label for results.tsv."""

    if session_label is None:
        return f"sub-{subject_label}"
    return f"sub-{subject_label} ses-{session_label}"


RESULTS_TSV_COLUMNS = (
    "unit_name",
    "source_name",
    "subject_label",
    "session_label",
    "summary",
    "status",
    "exit_code",
    "started_at",
    "finished_at",
    "log_path",
    "scheduler",
    "scheduler_job_id",
    "scheduler_task_id",
    "notes",
)


def _prepare_run_directories(plan: RunPlan) -> None:
    """Create directories needed before local execution or scheduler submission."""

    plan.execution_view_root.mkdir(parents=True, exist_ok=True)
    plan.raw_bids_root.mkdir(parents=True, exist_ok=True)
    plan.state_path.parent.mkdir(parents=True, exist_ok=True)
    plan.results_path.parent.mkdir(parents=True, exist_ok=True)
    plan.unit_state_dir.mkdir(parents=True, exist_ok=True)
    plan.claim_dir.mkdir(parents=True, exist_ok=True)
    plan.log_dir.mkdir(parents=True, exist_ok=True)
    if plan.sge is not None:
        plan.sge.script_path.parent.mkdir(parents=True, exist_ok=True)
        plan.sge.unit_list_path.parent.mkdir(parents=True, exist_ok=True)
        plan.sge.scheduler_log_dir.mkdir(parents=True, exist_ok=True)


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
    unit_results: tuple[RunUnitResult, ...],
) -> None:
    """Append final local unit results to results.tsv."""

    _ensure_results_tsv_header(path)
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        for result in unit_results:
            writer.writerow(
                (
                    result.unit_name,
                    result.source_name,
                    result.subject_label,
                    result.session_label or "",
                    _build_run_unit_summary(result.subject_label, result.session_label),
                    result.status,
                    "" if result.exit_code is None else str(result.exit_code),
                    result.started_at,
                    result.finished_at or "",
                    str(result.log_path),
                    "",
                    "",
                    "",
                    "",
                )
            )


def _claim_runnable_units(
    *,
    units: tuple[RunUnitPlan, ...],
    include_failed: bool,
) -> RunUnitSelection:
    """Claim units that are eligible to run now."""

    runnable_units: list[RunUnitPlan] = []
    skipped_succeeded = 0
    skipped_failed = 0
    skipped_active_claim = 0

    try:
        for unit in units:
            unit_status = _unit_status_value(unit.status_path)
            if unit_status == "succeeded":
                skipped_succeeded += 1
                continue
            if unit.claim_path.exists():
                skipped_active_claim += 1
                continue
            if unit_status == "failed" and not include_failed:
                skipped_failed += 1
                continue
            try:
                _write_unit_claim(
                    unit=unit,
                )
            except FileExistsError:  # pragma: no cover
                skipped_active_claim += 1
                continue
            except OSError:
                _release_unfinished_claims((*runnable_units, unit), ())
                raise
            runnable_units.append(unit)
    except OSError:
        _release_unfinished_claims(tuple(runnable_units), ())
        raise

    return RunUnitSelection(
        runnable_units=tuple(runnable_units),
        skipped_succeeded=skipped_succeeded,
        skipped_failed=skipped_failed,
        skipped_active_claim=skipped_active_claim,
    )


def preview_run_unit_selection(
    plan: RunPlan,
    *,
    include_failed: bool = False,
) -> tuple[tuple[RunUnitPlan, ...], dict[str, int]]:
    """Return units that would run now without creating claims or files."""

    selection = _preview_runnable_units(plan.units, include_failed=include_failed)
    return selection.runnable_units, _build_run_unit_counts(plan.units, selection)


def _preview_runnable_units(
    units: tuple[RunUnitPlan, ...],
    *,
    include_failed: bool,
) -> RunUnitSelection:
    """Select runnable units without writing claim files."""

    runnable_units: list[RunUnitPlan] = []
    skipped_succeeded = 0
    skipped_failed = 0
    skipped_active_claim = 0

    for unit in units:
        unit_status = _unit_status_value(unit.status_path)
        if unit_status == "succeeded":
            skipped_succeeded += 1
        elif unit.claim_path.exists():
            skipped_active_claim += 1
        elif unit_status == "failed" and not include_failed:
            skipped_failed += 1
        else:
            runnable_units.append(unit)

    return RunUnitSelection(
        runnable_units=tuple(runnable_units),
        skipped_succeeded=skipped_succeeded,
        skipped_failed=skipped_failed,
        skipped_active_claim=skipped_active_claim,
    )


def _build_run_unit_counts(
    planned_units: tuple[RunUnitPlan, ...],
    claim_selection: RunUnitSelection,
) -> dict[str, int]:
    """Summarize selected and skipped units for CLI output and run state."""

    skipped = (
        claim_selection.skipped_succeeded
        + claim_selection.skipped_failed
        + claim_selection.skipped_active_claim
    )
    return {
        "total": len(planned_units),
        "selected": len(claim_selection.runnable_units),
        "skipped": skipped,
        "skipped_succeeded": claim_selection.skipped_succeeded,
        "skipped_failed": claim_selection.skipped_failed,
        "skipped_active_claim": claim_selection.skipped_active_claim,
    }


def _unit_status_value(path: Path) -> str:
    """Read only the status value from a unit status file."""

    if not path.is_file():
        return ""
    try:
        return read_key_value_status(path).get("status", "")
    except OSError:
        return ""


def _write_unit_claim(*, unit: RunUnitPlan) -> None:
    """Create an exclusive running claim for one unit."""

    unit.claim_path.parent.mkdir(parents=True, exist_ok=True)
    with unit.claim_path.open("x", encoding="utf-8"):
        pass


def _release_unit_claim(unit: RunUnitPlan) -> None:
    """Remove a unit's running claim when execution has ended."""

    try:
        unit.claim_path.unlink()
    except FileNotFoundError:
        return


def _release_unfinished_claims(
    units: tuple[RunUnitPlan, ...],
    unit_results: tuple[RunUnitResult, ...] | list[RunUnitResult],
) -> None:
    """Release claims for units that did not finish cleanly."""

    finished_units = {result.unit_name for result in unit_results}
    for unit in units:
        if unit.unit_name not in finished_units:
            _release_unit_claim(unit)


def _write_unit_status(
    unit: RunUnitPlan,
    *,
    status: str,
    backend: str,
    attempt_label: str,
    started_at: str | None = None,
    finished_at: str | None = None,
    exit_code: int | None = None,
    log_path: Path | None = None,
    log_dir: Path | None = None,
    scheduler_job_id: str | None = None,
    scheduler_task_id: str | None = None,
    error: str | None = None,
) -> None:
    """Write the final or submitted state for one unit."""

    lines = [
        ("status", status),
        ("unit", unit.unit_name),
        ("source", unit.source_name),
        ("subject", unit.subject_label),
        ("session", unit.session_label or ""),
        ("backend", backend),
        ("attempt", attempt_label),
        ("started_at", started_at or ""),
        ("finished_at", finished_at or ""),
        ("exit_code", "" if exit_code is None else str(exit_code)),
    ]
    if log_path is not None:
        lines.append(("log", str(log_path)))
    if log_dir is not None:
        lines.append(("log_dir", str(log_dir)))
    if scheduler_job_id is not None or scheduler_task_id is not None:
        lines.extend(
            (
                ("scheduler", "sge"),
                ("job_id", scheduler_job_id or ""),
                ("task_id", scheduler_task_id or ""),
            )
        )
    if error is not None:
        lines.append(("error", error))
    _write_key_value_status_atomic(unit.status_path, lines)


def _build_run_artifacts(
    plan: RunPlan,
    scheduler: dict[str, object] | None,
) -> dict[str, object]:
    """Build artifact pointers recorded in run.json."""

    artifacts: dict[str, object] = {
        "raw_bids_dataset": str(plan.raw_bids_root),
        "results_table": str(plan.results_path),
        "unit_status_dir": str(plan.unit_state_dir),
        "claim_dir": str(plan.claim_dir),
        "log_dir": str(plan.log_dir),
    }
    if scheduler is not None:
        artifacts.update(
            {
                "scheduler_script_template": scheduler["template_path"],
                "scheduler_script": scheduler["script_path"],
                "scheduler_common_runtime_script": scheduler[
                    "common_runtime_script_path"
                ],
                "scheduler_runtime_script": scheduler["runtime_script_path"],
                "scheduler_unit_list": scheduler["unit_list_path"],
                "scheduler_log_dir": scheduler["scheduler_log_dir"],
            }
        )
    return artifacts


def _build_run_execution_metadata(
    *,
    backend: str,
    record_state: str,
    started_at: str,
    updated_at: str,
    finished_at: str | None,
    scheduler: dict[str, object] | None,
) -> dict[str, object]:
    """Build backend-specific execution metadata for run.json."""

    if backend != "sge":
        return {
            "mode": "local",
            "started_at": started_at,
            "finished_at": finished_at,
        }
    if scheduler is None:  # pragma: no cover
        raise HeudiconvRunError("SGE run state requires scheduler metadata.")

    execution: dict[str, object] = {
        "mode": "scheduler",
        "scheduler": scheduler["name"],
        "submit_state": record_state,
        "submit_command": scheduler["submit_command"],
        "array_range": scheduler["array_range"],
        "started_at": started_at,
    }
    if "job_id" in scheduler:
        execution["job_id"] = scheduler["job_id"]
    if record_state == "submitted":
        execution["submitted_at"] = finished_at or updated_at
    elif record_state == "submit_failed":
        execution["failed_at"] = finished_at or updated_at
    return execution


def _write_run_state(
    *,
    context: ProjectContext,
    plan: RunPlan,
    record_state: str,
    started_at: str,
    backend: str = "local",
    finished_at: str | None = None,
    cleanup_execution_view: bool | None = None,
    execution_view_cleaned: bool | None = None,
    scheduler: dict[str, object] | None = None,
    planned_units: dict[str, int] | None = None,
    error: str | None = None,
) -> None:
    """Write run-level metadata without embedding per-unit details."""

    updated_at = _utc_now()

    payload: dict[str, object] = {
        "workflow": "heudiconv",
        "step": "run",
        "backend": backend,
        "record_state": record_state,
        "created_at": started_at,
        "updated_at": updated_at,
        "input_signature": plan.input_signature,
        "config_path": str(context.config_path),
        "project_root": str(context.project_root),
        "sources_path": str(plan.sources_path),
        "sources_state_path": str(plan.sources_state_path),
        "heuristic_path": str(plan.heuristic_path),
        "raw_bids_root": str(plan.raw_bids_root),
        "execution_view_root": str(plan.execution_view_root),
        "execution": _build_run_execution_metadata(
            backend=backend,
            record_state=record_state,
            started_at=started_at,
            updated_at=updated_at,
            finished_at=finished_at,
            scheduler=scheduler,
        ),
        "artifacts": _build_run_artifacts(plan, scheduler),
    }
    if planned_units is not None:
        payload["planned_units"] = planned_units
    if cleanup_execution_view is not None:
        payload["cleanup_execution_view"] = cleanup_execution_view
    if execution_view_cleaned is not None:
        payload["execution_view_cleaned"] = execution_view_cleaned
    if error is not None:
        payload["error"] = error
    _write_json(plan.state_path, payload)


def _materialize_run_execution_view(execution_path: Path, source_path: Path) -> None:
    """Create the temporary source link consumed by HeuDiConv."""

    execution_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        execution_path.symlink_to(source_path, target_is_directory=True)
    except OSError as exc:
        raise HeudiconvRunError(
            f"Failed to create temporary execution link: {execution_path} -> {source_path}"
        ) from exc


def _run_heudiconv_command(
    context: ProjectContext,
    log_path: Path,
    command: tuple[str, ...],
    *,
    label: str,
) -> subprocess.CompletedProcess[str]:
    """Run one local HeuDiConv command and append combined output to its log."""

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
        raise HeudiconvRunError(
            f"Failed to start HeuDiConv launcher. See {log_path} for details."
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


def _coerce_run_error(
    exc: HeudiconvRunError | OSError,
    *,
    context_message: str,
) -> HeudiconvRunError:
    """Normalize raw filesystem errors to the public run exception type."""

    if isinstance(exc, HeudiconvRunError):
        return exc
    return HeudiconvRunError(f"{context_message}: {exc}")


def _cleanup_run_execution_view(project_root: Path, execution_view_root: Path) -> bool:
    """Remove the temporary execution view if it is inside the project."""

    if not execution_view_root.exists():
        return True
    try:
        _remove_project_path(project_root, execution_view_root)
    except ValueError:
        return False
    except OSError as exc:
        raise HeudiconvRunError(
            f"Failed to remove temporary execution view: {execution_view_root}"
        ) from exc
    return not execution_view_root.exists()
