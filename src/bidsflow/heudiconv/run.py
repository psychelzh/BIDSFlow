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
from ..project import ProjectContext
from ..schedulers import _resolve_scheduler_script_path
from .errors import HeudiconvInitError, HeudiconvRunError
from .sources import (
    SourcesEntry,
    _load_confirmed_sources,
    _resolve_sources_source_path,
    list_sources_review_issues,
)


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


def plan_heudiconv_run(context: ProjectContext) -> RunPlan:
    """Plan a managed HeuDiConv conversion from reviewed sources.tsv rows."""

    sources_path, heuristic_path, entries, ready_entries = _resolve_run_inputs(context)

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
        unit_name = _build_run_unit_name(entry.subject_label, session_label)
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
                    launcher=context.heudiconv.launcher,
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

    return RunPlan(
        attempt_label=attempt_label,
        sources_path=sources_path,
        sources_state_path=context.paths.state_root / "sources.json",
        launcher=context.heudiconv.launcher,
        heuristic_path=heuristic_path,
        raw_bids_root=context.paths.raw_bids_root,
        execution_view_root=execution_view_root,
        state_path=state_path,
        results_path=results_path,
        unit_state_dir=unit_state_dir,
        claim_dir=claim_dir,
        log_dir=log_dir,
        entries=entries,
        units=tuple(units),
        sge=_build_sge_run_plan(
            context,
            execution_view_root=execution_view_root,
            log_dir=log_dir,
        ),
    )


def _resolve_run_inputs(
    context: ProjectContext,
) -> tuple[Path, Path, tuple[SourcesEntry, ...], tuple[SourcesEntry, ...]]:
    """Resolve and validate reviewed sources and heuristic inputs."""

    sources_path = context.paths.state_root / "sources.tsv"
    if not sources_path.exists():
        raise HeudiconvRunError(
            f"BIDSFlow sources table does not exist: {sources_path}. Run `bidsflow heudiconv init` first."
        )

    heuristic_path = context.heudiconv.heuristic
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
    claim_selection = _claim_runnable_units(
        units=plan.units,
        include_failed=include_failed,
    )
    unit_counts = _build_run_unit_counts(plan.units, claim_selection)
    if not claim_selection.runnable_units:
        return RunResult(
            raw_bids_root=plan.raw_bids_root,
            state_path=plan.state_path,
            results_path=plan.results_path,
            log_dir=plan.log_dir,
            unit_results=(),
            status="skipped",
            skipped_units=unit_counts["skipped"],
            skipped_succeeded=unit_counts["skipped_succeeded"],
            skipped_failed=unit_counts["skipped_failed"],
            skipped_active_claim=unit_counts["skipped_active_claim"],
        )

    unit_results: list[RunUnitResult] = []
    current_unit: RunUnitPlan | None = None
    try:
        execution_view_cleared = _cleanup_run_execution_view(
            context.project_root,
            plan.execution_view_root,
        )
        if not execution_view_cleared:
            raise HeudiconvRunError(
                f"Failed to clear prior run execution view: {plan.execution_view_root}"
            )

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

        for unit in claim_selection.runnable_units:
            current_unit = unit
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
                _materialize_run_execution_view(
                    unit.execution_path,
                    unit.source_path,
                )
                completed = _run_heudiconv_command(
                    context,
                    unit.log_path,
                    unit.command,
                    label=unit.source_name,
                )
            except HeudiconvRunError as exc:
                unit_finished_at = _utc_now()
                failed_result = RunUnitResult(
                    index=unit.index,
                    unit_name=unit.unit_name,
                    source_name=unit.source_name,
                    source_path=unit.source_path,
                    subject_label=unit.subject_label,
                    session_label=unit.session_label,
                    execution_path=unit.execution_path,
                    command=unit.command,
                    log_path=unit.log_path,
                    status="failed",
                    started_at=unit_started_at,
                    finished_at=unit_finished_at,
                    exit_code=None,
                )
                _write_unit_status(
                    unit,
                    status="failed",
                    backend="local",
                    attempt_label=plan.attempt_label,
                    started_at=unit_started_at,
                    finished_at=unit_finished_at,
                    log_path=unit.log_path,
                    error=str(exc),
                )
                _append_results_tsv(plan.results_path, (failed_result,))
                _release_unit_claim(unit)
                unit_results.append(failed_result)
                raise
            unit_finished_at = _utc_now()
            status = "succeeded" if completed.returncode == 0 else "failed"
            result = RunUnitResult(
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
                started_at=unit_started_at,
                finished_at=unit_finished_at,
                exit_code=completed.returncode,
            )
            if completed.returncode != 0:
                error_message = (
                    "HeuDiConv execution failed while processing "
                    f"{unit.source_name}. See {unit.log_path} for details."
                )
                _write_unit_status(
                    unit,
                    status="failed",
                    backend="local",
                    attempt_label=plan.attempt_label,
                    started_at=unit_started_at,
                    finished_at=unit_finished_at,
                    exit_code=completed.returncode,
                    log_path=unit.log_path,
                    error=error_message,
                )
                _append_results_tsv(plan.results_path, (result,))
                _release_unit_claim(unit)
                unit_results.append(result)
                raise HeudiconvRunError(
                    error_message
                )

            _write_unit_status(
                unit,
                status="succeeded",
                backend="local",
                attempt_label=plan.attempt_label,
                started_at=unit_started_at,
                finished_at=unit_finished_at,
                exit_code=completed.returncode,
                log_path=unit.log_path,
            )
            _append_results_tsv(plan.results_path, (result,))
            _release_unit_claim(unit)
            unit_results.append(result)
            current_unit = None
    except HeudiconvRunError as exc:
        _release_unfinished_claims(claim_selection.runnable_units, unit_results, current_unit)
        execution_view_cleaned = None
        if cleanup_execution_view:
            execution_view_cleaned = _cleanup_run_execution_view(
                context.project_root,
                plan.execution_view_root,
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
            error=str(exc),
        )
        raise

    execution_view_cleaned = None
    if cleanup_execution_view:
        execution_view_cleaned = _cleanup_run_execution_view(
            context.project_root,
            plan.execution_view_root,
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
    claim_selection = _claim_runnable_units(
        units=plan.units,
        include_failed=include_failed,
    )
    unit_counts = _build_run_unit_counts(plan.units, claim_selection)
    if not claim_selection.runnable_units:
        return RunResult(
            raw_bids_root=plan.raw_bids_root,
            state_path=plan.state_path,
            results_path=plan.results_path,
            log_dir=plan.log_dir,
            unit_results=(),
            backend="sge",
            status="skipped",
            skipped_units=unit_counts["skipped"],
            skipped_succeeded=unit_counts["skipped_succeeded"],
            skipped_failed=unit_counts["skipped_failed"],
            skipped_active_claim=unit_counts["skipped_active_claim"],
            scheduler_script_path=sge.script_path,
            scheduler_log_dir=sge.scheduler_log_dir,
        )

    unit_results: list[RunUnitResult] = []
    current_unit: RunUnitPlan | None = None
    try:
        execution_view_cleared = _cleanup_run_execution_view(
            context.project_root,
            plan.execution_view_root,
        )
        if not execution_view_cleared:
            raise HeudiconvRunError(
                f"Failed to clear prior run execution view: {plan.execution_view_root}"
            )

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

        for task_id, unit in enumerate(claim_selection.runnable_units, start=1):
            current_unit = unit
            _materialize_run_execution_view(
                unit.execution_path,
                unit.source_path,
            )
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
                RunUnitResult(
                    index=unit.index,
                    unit_name=unit.unit_name,
                    source_name=unit.source_name,
                    source_path=unit.source_path,
                    subject_label=unit.subject_label,
                    session_label=unit.session_label,
                    execution_path=unit.execution_path,
                    command=unit.command,
                    log_path=unit.log_path,
                    status="submitted",
                    started_at=started_at,
                    finished_at=None,
                    exit_code=None,
                )
            )
            current_unit = None

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
    except HeudiconvRunError as exc:
        for unit in claim_selection.runnable_units:
            _write_unit_status(
                unit,
                status="submit_failed",
                backend="sge",
                attempt_label=plan.attempt_label,
                started_at=started_at,
                finished_at=_utc_now(),
                log_dir=sge.scheduler_log_dir,
                error=str(exc),
            )
            _release_unit_claim(unit)
        _release_unfinished_claims(
            claim_selection.runnable_units,
            tuple(unit_results),
            current_unit,
        )
        execution_view_cleaned = None
        if cleanup_execution_view:
            execution_view_cleaned = _cleanup_run_execution_view(
                context.project_root,
                plan.execution_view_root,
            )
        scheduler_metadata = _build_sge_run_metadata(plan, units=claim_selection.runnable_units)
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
            error=str(exc),
        )
        raise

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
        unit_results=tuple(unit_results),
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


def _build_run_unit_name(subject_label: str, session_label: str | None) -> str:
    """Build the stable unit name used for logs, claims, and status files."""

    if session_label is None:
        return f"sub-{subject_label}"
    return f"sub-{subject_label}_ses-{session_label}"


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
        runnable_units.append(unit)

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
        return _read_key_value_status(path).get("status", "")
    except OSError:
        return ""


def _read_key_value_status(path: Path) -> dict[str, str]:
    """Read a simple key=value status file."""

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


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
    current_unit: RunUnitPlan | None,
) -> None:
    """Release claims for units that did not finish cleanly."""

    finished_units = {result.unit_name for result in unit_results}
    for unit in units:
        if unit.unit_name in finished_units:
            continue
        if current_unit is not None and unit.unit_name == current_unit.unit_name:
            _release_unit_claim(unit)
            continue
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
        "input_signature": _build_run_input_signature(
            launcher=plan.launcher,
            heuristic_path=plan.heuristic_path,
            raw_bids_root=plan.raw_bids_root,
            source_root=context.paths.source_root,
            ready_entries=tuple(
                entry for entry in plan.entries if entry.include and entry.status == "ready"
            ),
        ),
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


def _cleanup_run_execution_view(project_root: Path, execution_view_root: Path) -> bool:
    """Remove the temporary execution view if it is inside the project."""

    if not execution_view_root.exists():
        return True
    try:
        _remove_project_path(project_root, execution_view_root)
    except ValueError:
        return False
    return not execution_view_root.exists()
