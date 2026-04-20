from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import csv
import hashlib
from importlib import resources
import json
import os
from pathlib import Path
import re
import shutil
import shlex
from string import Formatter
import subprocess
import time

from .project import ProjectContext


@dataclass(frozen=True)
class DraftUnitPlan:
    index: int
    sample_path: Path
    unit_name: str
    initial_command: tuple[str, ...]
    subject_label: str | None
    session_label: str | None
    log_path: Path


@dataclass(frozen=True)
class DraftPlan:
    sample_paths: tuple[Path, ...]
    launcher: tuple[str, ...]
    units: tuple[DraftUnitPlan, ...]
    code_root: Path
    heuristic_path: Path
    dicominfo_root: Path
    draft_work_root: Path
    heudiconv_state_path: Path
    draft_state_path: Path
    log_dir: Path


@dataclass(frozen=True)
class DraftUnitResult:
    index: int
    sample_path: Path
    unit_name: str
    subject_label: str | None
    session_label: str | None
    strategy: str
    attempted_commands: tuple[tuple[str, ...], ...]
    generated_heuristic: Path
    generated_dicominfo_paths: tuple[Path, ...]
    copied_dicominfo_paths: tuple[Path, ...]
    log_path: Path


@dataclass(frozen=True)
class DraftResult:
    heuristic_path: Path
    dicominfo_root: Path
    dicominfo_paths: tuple[Path, ...]
    draft_state_path: Path
    log_dir: Path
    unit_results: tuple[DraftUnitResult, ...]


class HeudiconvDraftError(Exception):
    pass


@dataclass(frozen=True)
class SourcesEntry:
    source_name: str
    subject_label: str
    session_label: str
    include: bool
    status: str
    notes: str


@dataclass(frozen=True)
class SourcesPlan:
    source_root: Path
    sources_path: Path
    sources_state_path: Path
    pattern: str | None
    command: tuple[str, ...] | None
    entries: tuple[SourcesEntry, ...]


@dataclass(frozen=True)
class SourcesResult:
    sources_path: Path
    sources_state_path: Path
    entries: tuple[SourcesEntry, ...]


class SourcesError(Exception):
    pass


@dataclass(frozen=True)
class InitPlan:
    sources_plan: SourcesPlan
    code_root: Path
    heuristic_parent: Path
    scheduler: str
    scheduler_script_path: Path | None
    scheduler_script_content: str | None


@dataclass(frozen=True)
class InitResult:
    sources_path: Path
    sources_state_path: Path
    entries: tuple[SourcesEntry, ...]
    sources_action: str
    code_root: Path
    heuristic_parent: Path
    scheduler: str
    scheduler_script_path: Path | None
    scheduler_script_action: str


class HeudiconvInitError(Exception):
    pass


@dataclass(frozen=True)
class RunUnitPlan:
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
    template_path: Path
    script_path: Path
    unit_list_path: Path
    scheduler_log_dir: Path
    submit_command: tuple[str, ...]


@dataclass(frozen=True)
class RunPlan:
    attempt_label: str
    sources_path: Path
    sources_state_path: Path
    launcher: tuple[str, ...]
    heuristic_path: Path
    raw_bids_root: Path
    execution_view_root: Path
    state_path: Path
    units_path: Path
    unit_state_dir: Path
    claim_dir: Path
    log_dir: Path
    entries: tuple[SourcesEntry, ...]
    units: tuple[RunUnitPlan, ...]
    sge: SgeRunPlan | None


@dataclass(frozen=True)
class RunUnitResult:
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
    raw_bids_root: Path
    state_path: Path
    units_path: Path
    log_dir: Path
    unit_results: tuple[RunUnitResult, ...]
    backend: str = "local"
    status: str = "succeeded"
    skipped_units: int = 0
    scheduler_job_id: str | None = None
    scheduler_script_path: Path | None = None
    scheduler_log_dir: Path | None = None


@dataclass(frozen=True)
class ClaimSelection:
    claimed_units: tuple[RunUnitPlan, ...]
    skipped_succeeded: int
    skipped_active_claim: int


class HeudiconvRunError(Exception):
    pass


def plan_draft(context: ProjectContext, sample_paths: list[Path]) -> DraftPlan:
    if not sample_paths:
        raise HeudiconvDraftError("At least one sample path is required for draft generation.")

    source_root = context.paths.source_root.resolve()
    if not source_root.exists():
        raise HeudiconvDraftError(
            f"Configured source root does not exist: {source_root}"
        )
    if not source_root.is_dir():
        raise HeudiconvDraftError(
            f"Configured source root is not a directory: {source_root}"
        )

    resolved_samples: list[Path] = []
    for sample_path in sample_paths:
        resolved_sample = _resolve_draft_sample_path(
            source_root=source_root,
            project_root=context.project_root,
            sample_path=sample_path,
        )
        if not resolved_sample.exists():
            raise HeudiconvDraftError(f"Sample path does not exist: {resolved_sample}")
        resolved_samples.append(resolved_sample)

    launcher = context.heudiconv.launcher
    code_root = context.project_root / "code" / "heudiconv"
    state_root = context.paths.state_root / "heudiconv"
    log_root = context.paths.logs_root / "heudiconv"
    attempt_label = _format_attempt_label()
    draft_work_root = context.paths.work_root / "heudiconv" / "draft-work"
    unit_log_root = log_root / f"draft-{attempt_label}"

    if len(resolved_samples) == 1:
        units = (
            DraftUnitPlan(
                index=1,
                sample_path=resolved_samples[0],
                unit_name="sample-01",
                initial_command=_build_draft_command(
                    launcher,
                    resolved_samples[0],
                    draft_work_root,
                    subject_label="draft01",
                ),
                subject_label="draft01",
                session_label=None,
                log_path=unit_log_root / "sample-01.log",
            ),
        )
    else:
        generated_subject = "draft01"
        units = tuple(
            DraftUnitPlan(
                index=index,
                sample_path=sample_path,
                unit_name=f"draft-ses{index:02d}",
                initial_command=_build_draft_command(
                    launcher,
                    sample_path,
                    draft_work_root,
                    subject_label=generated_subject,
                    session_label=f"draft-ses{index:02d}",
                ),
                subject_label=generated_subject,
                session_label=f"draft-ses{index:02d}",
                log_path=unit_log_root / f"draft-ses{index:02d}.log",
            )
            for index, sample_path in enumerate(resolved_samples, start=1)
        )

    return DraftPlan(
        sample_paths=tuple(resolved_samples),
        launcher=launcher,
        units=units,
        code_root=code_root,
        heuristic_path=context.heudiconv.heuristic,
        dicominfo_root=code_root / "dicominfo",
        draft_work_root=draft_work_root,
        heudiconv_state_path=draft_work_root / ".heudiconv",
        draft_state_path=state_root / "draft.json",
        log_dir=unit_log_root,
    )


def _resolve_draft_sample_path(
    *,
    source_root: Path,
    project_root: Path,
    sample_path: Path,
) -> Path:
    if sample_path.is_absolute():
        resolved_sample = sample_path.resolve()
    else:
        source_relative = (source_root / sample_path).resolve()
        project_relative = (project_root / sample_path).resolve()
        if source_relative.exists() or not project_relative.exists():
            resolved_sample = source_relative
        else:
            resolved_sample = project_relative

    if not resolved_sample.is_relative_to(source_root):
        raise HeudiconvDraftError(
            f"Sample path must resolve under the configured source root {source_root}: {sample_path}"
        )

    return resolved_sample


def run_draft(context: ProjectContext, plan: DraftPlan, reset: bool) -> DraftResult:
    _guard_draft_reset_requirement(plan, reset)
    _prepare_draft_directories(plan)

    if reset:
        _reset_draft_state(context.project_root, plan)
        _prepare_draft_directories(plan)

    started_at = _utc_now()
    _write_draft_state(
        context=context,
        plan=plan,
        status="running",
        started_at=started_at,
    )

    unit_results: list[DraftUnitResult] = []
    copied_dicominfo_paths: list[Path] = []
    current_unit: DraftUnitPlan | None = None

    try:
        for unit in plan.units:
            current_unit = unit
            unit_result = _run_draft_unit(context, plan, unit)
            _merge_heuristic(plan.heuristic_path, unit_result.generated_heuristic)
            unit_results.append(unit_result)
            copied_dicominfo_paths.extend(unit_result.copied_dicominfo_paths)
            current_unit = None
    except HeudiconvDraftError as exc:
        _write_draft_state(
            context=context,
            plan=plan,
            status="failed",
            started_at=started_at,
            finished_at=_utc_now(),
            failed_unit=current_unit,
            error=str(exc),
        )
        raise

    _write_draft_state(
        context=context,
        plan=plan,
        status="succeeded",
        started_at=started_at,
        finished_at=_utc_now(),
    )

    return DraftResult(
        heuristic_path=plan.heuristic_path,
        dicominfo_root=plan.dicominfo_root,
        dicominfo_paths=tuple(copied_dicominfo_paths),
        draft_state_path=plan.draft_state_path,
        log_dir=plan.log_dir,
        unit_results=tuple(unit_results),
    )


def plan_sources(
    context: ProjectContext,
) -> SourcesPlan:
    resolved_source_root = context.paths.source_root.resolve()
    if not resolved_source_root.exists():
        raise SourcesError(
            f"Configured source root does not exist: {resolved_source_root}"
        )
    if not resolved_source_root.is_dir():
        raise SourcesError(
            f"Configured source root is not a directory: {resolved_source_root}"
        )

    source_units = tuple(
        sorted(
            (
                candidate.resolve()
                for candidate in resolved_source_root.iterdir()
                if candidate.is_dir() and not candidate.name.startswith(".")
            ),
            key=lambda candidate: candidate.name.lower(),
        )
    )

    sources_config = context.sources
    source_name_pattern: re.Pattern[str] | None
    if sources_config.pattern is not None:
        source_name_pattern = _compile_sources_pattern(sources_config.pattern)
    else:
        source_name_pattern = None

    entries: list[SourcesEntry] = []
    for candidate in source_units:
        if source_name_pattern is not None:
            subject_label, session_label, notes = _derive_sources_labels_from_pattern(
                source_name_pattern,
                candidate.name,
            )
        elif sources_config.command is not None:
            subject_label, session_label, notes = _derive_sources_labels_from_command(
                context,
                sources_config.command,
                candidate.name,
            )
        else:
            subject_label, session_label, notes = "", "", ""

        entries.append(
            SourcesEntry(
                source_name=candidate.name,
                subject_label=subject_label,
                session_label=session_label,
                include=True,
                status="",
                notes=notes,
            )
        )

    computed_entries = _compute_sources_statuses(resolved_source_root, tuple(entries))

    return SourcesPlan(
        source_root=resolved_source_root,
        sources_path=context.paths.state_root / "sources.tsv",
        sources_state_path=context.paths.state_root / "sources.json",
        pattern=sources_config.pattern,
        command=sources_config.command,
        entries=computed_entries,
    )


def run_sources(context: ProjectContext, plan: SourcesPlan, reset: bool) -> SourcesResult:
    _guard_sources_reset_requirement(plan, reset)
    _prepare_sources_directories(plan)

    if reset:
        _reset_sources_state(context.project_root, plan)
        _prepare_sources_directories(plan)

    _write_sources_tsv(plan.sources_path, plan.entries)
    _write_sources_state(context, plan)

    return SourcesResult(
        sources_path=plan.sources_path,
        sources_state_path=plan.sources_state_path,
        entries=plan.entries,
    )


def plan_heudiconv_init(context: ProjectContext) -> InitPlan:
    sources_plan = plan_sources(context)
    scheduler = context.execution.scheduler
    scheduler_script_path: Path | None = None
    scheduler_script_content: str | None = None

    if scheduler == "sge":
        scheduler_script_path = _resolve_scheduler_script_path(context, target="heudiconv")
        scheduler_script_content = _render_sge_heudiconv_script()
    elif scheduler != "none":  # pragma: no cover
        raise HeudiconvInitError(f"Unsupported scheduler for HeuDiConv init: {scheduler}")

    return InitPlan(
        sources_plan=sources_plan,
        code_root=context.project_root / "code" / "heudiconv",
        heuristic_parent=context.heudiconv.heuristic.parent,
        scheduler=scheduler,
        scheduler_script_path=scheduler_script_path,
        scheduler_script_content=scheduler_script_content,
    )


def run_heudiconv_init(context: ProjectContext, plan: InitPlan, force: bool) -> InitResult:
    sources_exist = plan.sources_plan.sources_path.exists() or plan.sources_plan.sources_state_path.exists()
    if sources_exist and not force:
        sources_action = "kept"
    else:
        run_sources(context, plan.sources_plan, reset=force)
        sources_action = "overwritten" if sources_exist else "created"

    plan.code_root.mkdir(parents=True, exist_ok=True)
    plan.heuristic_parent.mkdir(parents=True, exist_ok=True)

    scheduler_script_action = "not configured"
    if plan.scheduler_script_path is not None and plan.scheduler_script_content is not None:
        _ensure_project_owned_path(context.project_root, plan.scheduler_script_path)
        scheduler_script_exists = plan.scheduler_script_path.exists()
        if scheduler_script_exists and not force:
            scheduler_script_action = "kept"
        else:
            plan.scheduler_script_path.parent.mkdir(parents=True, exist_ok=True)
            plan.scheduler_script_path.write_text(
                plan.scheduler_script_content,
                encoding="utf-8",
                newline="\n",
            )
            scheduler_script_action = "overwritten" if scheduler_script_exists else "created"

    return InitResult(
        sources_path=plan.sources_plan.sources_path,
        sources_state_path=plan.sources_plan.sources_state_path,
        entries=plan.sources_plan.entries,
        sources_action=sources_action,
        code_root=plan.code_root,
        heuristic_parent=plan.heuristic_parent,
        scheduler=plan.scheduler,
        scheduler_script_path=plan.scheduler_script_path,
        scheduler_script_action=scheduler_script_action,
    )


def plan_heudiconv_run(context: ProjectContext) -> RunPlan:
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
        entry
        for entry in entries
        if entry.include and entry.status == "ready"
    )
    blocking_entries = tuple(
        entry
        for entry in entries
        if entry.include and entry.status != "ready"
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

    state_root = context.paths.state_root / "heudiconv"
    attempt_label = _format_attempt_label()
    execution_view_root = context.paths.work_root / "heudiconv" / f"run-{attempt_label}"
    state_path = state_root / "run.json"
    units_path = state_root / "run.tsv"
    unit_state_dir = state_root / "units"
    claim_dir = state_root / "claims"
    log_root = context.paths.logs_root / "heudiconv"
    scheduler_name = context.execution.scheduler
    log_dir = log_root / ("sge" if scheduler_name == "sge" else "local") / f"run-{attempt_label}"
    sge_plan: SgeRunPlan | None = None

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

    if context.execution.scheduler == "sge":
        if context.execution.submit_command is None:  # pragma: no cover
            raise HeudiconvRunError(
                "[execution].submit_command is required when scheduler is 'sge'."
            )
        try:
            scheduler_template_path = _resolve_scheduler_script_path(
                context,
                target="heudiconv",
            )
        except HeudiconvInitError as exc:
            raise HeudiconvRunError(str(exc)) from exc
        if not scheduler_template_path.is_file():
            raise HeudiconvRunError(
                "SGE scheduler template does not exist: "
                f"{scheduler_template_path}. Run `bidsflow heudiconv init` first."
            )

        scheduler_root = execution_view_root / ".bidsflow" / "scheduler" / "sge"
        sge_plan = SgeRunPlan(
            template_path=scheduler_template_path,
            script_path=scheduler_root / "heudiconv.sh",
            unit_list_path=scheduler_root / "units.tsv",
            scheduler_log_dir=log_dir,
            submit_command=context.execution.submit_command,
        )
    elif context.execution.scheduler != "none":  # pragma: no cover
        raise HeudiconvRunError(
            f"Unsupported scheduler for HeuDiConv run: {context.execution.scheduler}"
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
        units_path=units_path,
        unit_state_dir=unit_state_dir,
        claim_dir=claim_dir,
        log_dir=log_dir,
        entries=entries,
        units=tuple(units),
        sge=sge_plan,
    )


def run_heudiconv(context: ProjectContext, plan: RunPlan) -> RunResult:
    if plan.sge is not None:
        return _submit_sge_heudiconv_run(context, plan)

    execution_view_cleared = _cleanup_run_execution_view(
        context.project_root,
        plan.execution_view_root,
    )
    if not execution_view_cleared:
        raise HeudiconvRunError(
            f"Failed to clear prior run execution view: {plan.execution_view_root}"
        )

    _prepare_run_directories(plan)
    _ensure_run_units_tsv_header(plan.units_path)

    started_at = _utc_now()
    claim_selection = _claim_runnable_units(
        units=plan.units,
    )
    unit_counts = _build_run_unit_counts(plan.units, claim_selection)
    if not claim_selection.claimed_units:
        _write_run_state(
            context=context,
            plan=plan,
            status="skipped",
            started_at=started_at,
            finished_at=_utc_now(),
            unit_counts=unit_counts,
        )
        return RunResult(
            raw_bids_root=plan.raw_bids_root,
            state_path=plan.state_path,
            units_path=plan.units_path,
            log_dir=plan.log_dir,
            unit_results=(),
            status="skipped",
            skipped_units=unit_counts["skipped"],
        )

    _write_run_state(
        context=context,
        plan=plan,
        status="running",
        started_at=started_at,
        unit_counts=unit_counts,
    )

    unit_results: list[RunUnitResult] = []
    current_unit: RunUnitPlan | None = None
    try:
        for unit in claim_selection.claimed_units:
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
                _append_run_units_tsv(plan.units_path, (failed_result,))
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
                _append_run_units_tsv(plan.units_path, (result,))
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
            _append_run_units_tsv(plan.units_path, (result,))
            _release_unit_claim(unit)
            unit_results.append(result)
            current_unit = None
    except HeudiconvRunError as exc:
        _release_unfinished_claims(claim_selection.claimed_units, unit_results, current_unit)
        execution_view_cleaned = _cleanup_run_execution_view(
            context.project_root,
            plan.execution_view_root,
        )
        _write_run_state(
            context=context,
            plan=plan,
            status="failed",
            started_at=started_at,
            finished_at=_utc_now(),
            execution_view_cleaned=execution_view_cleaned,
            unit_counts=unit_counts,
            error=str(exc),
        )
        raise

    execution_view_cleaned = _cleanup_run_execution_view(
        context.project_root,
        plan.execution_view_root,
    )
    _write_run_state(
        context=context,
        plan=plan,
        status="succeeded",
        started_at=started_at,
        finished_at=_utc_now(),
        execution_view_cleaned=execution_view_cleaned,
        unit_counts=unit_counts,
    )

    return RunResult(
        raw_bids_root=plan.raw_bids_root,
        state_path=plan.state_path,
        units_path=plan.units_path,
        log_dir=plan.log_dir,
        unit_results=tuple(unit_results),
        skipped_units=unit_counts["skipped"],
    )


def _submit_sge_heudiconv_run(context: ProjectContext, plan: RunPlan) -> RunResult:
    sge = plan.sge
    if sge is None:  # pragma: no cover
        raise HeudiconvRunError("SGE run plan is missing.")

    execution_view_cleared = _cleanup_run_execution_view(
        context.project_root,
        plan.execution_view_root,
    )
    if not execution_view_cleared:
        raise HeudiconvRunError(
            f"Failed to clear prior run execution view: {plan.execution_view_root}"
    )

    _prepare_run_directories(plan)
    _ensure_run_units_tsv_header(plan.units_path)

    started_at = _utc_now()
    claim_selection = _claim_runnable_units(
        units=plan.units,
    )
    unit_counts = _build_run_unit_counts(plan.units, claim_selection)
    scheduler_metadata = _build_sge_run_metadata(plan, units=claim_selection.claimed_units)
    if not claim_selection.claimed_units:
        _write_run_state(
            context=context,
            plan=plan,
            status="skipped",
            started_at=started_at,
            finished_at=_utc_now(),
            backend="sge",
            scheduler=scheduler_metadata,
            unit_counts=unit_counts,
        )
        return RunResult(
            raw_bids_root=plan.raw_bids_root,
            state_path=plan.state_path,
            units_path=plan.units_path,
            log_dir=plan.log_dir,
            unit_results=(),
            backend="sge",
            status="skipped",
            skipped_units=unit_counts["skipped"],
            scheduler_script_path=sge.script_path,
            scheduler_log_dir=sge.scheduler_log_dir,
        )

    _write_run_state(
        context=context,
        plan=plan,
        status="preparing",
        started_at=started_at,
        backend="sge",
        scheduler=scheduler_metadata,
        unit_counts=unit_counts,
    )

    unit_results: list[RunUnitResult] = []
    current_unit: RunUnitPlan | None = None
    try:
        for unit in claim_selection.claimed_units:
            current_unit = unit
            _materialize_run_execution_view(
                unit.execution_path,
                unit.source_path,
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

        _write_sge_run_files(context, plan, claim_selection.claimed_units)
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
        for unit in claim_selection.claimed_units:
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
            claim_selection.claimed_units,
            tuple(unit_results),
            current_unit,
        )
        _write_run_state(
            context=context,
            plan=plan,
            status="failed",
            started_at=started_at,
            finished_at=_utc_now(),
            backend="sge",
            scheduler=scheduler_metadata,
            unit_counts=unit_counts,
            error=str(exc),
        )
        raise

    scheduler_metadata = _build_sge_run_metadata(
        plan,
        units=claim_selection.claimed_units,
        job_id=scheduler_job_id,
    )
    for task_id, unit in enumerate(claim_selection.claimed_units, start=1):
        _write_unit_status(
            unit,
            status="submitted",
            backend="sge",
            attempt_label=plan.attempt_label,
            started_at=started_at,
            log_dir=sge.scheduler_log_dir,
            scheduler_job_id=scheduler_job_id,
            scheduler_task_id=str(task_id),
        )
    _write_run_state(
        context=context,
        plan=plan,
        status="submitted",
        started_at=started_at,
        finished_at=_utc_now(),
        backend="sge",
        scheduler=scheduler_metadata,
        unit_counts=unit_counts,
    )

    return RunResult(
        raw_bids_root=plan.raw_bids_root,
        state_path=plan.state_path,
        units_path=plan.units_path,
        log_dir=plan.log_dir,
        unit_results=tuple(unit_results),
        backend="sge",
        status="submitted",
        skipped_units=unit_counts["skipped"],
        scheduler_job_id=scheduler_job_id,
        scheduler_script_path=sge.script_path,
        scheduler_log_dir=sge.scheduler_log_dir,
    )


def _write_sge_run_files(
    context: ProjectContext,
    plan: RunPlan,
    units: tuple[RunUnitPlan, ...],
) -> None:
    sge = plan.sge
    if sge is None:  # pragma: no cover
        raise HeudiconvRunError("SGE run plan is missing.")

    _write_sge_unit_list(sge.unit_list_path, units)
    template = sge.template_path.read_text(encoding="utf-8")
    rendered = (
        template.replace("{{ job_name }}", "bidsflow-heudiconv")
        .replace("{{ task_count }}", str(len(units)))
        .replace("{{ attempt_label }}", plan.attempt_label)
        .replace("{{ scheduler_log_dir }}", str(sge.scheduler_log_dir))
        .replace("{{ unit_list_path }}", str(sge.unit_list_path))
        .replace("{{ shell_unit_list_path }}", shlex.quote(str(sge.unit_list_path)))
        .replace("{{ shell_scheduler_log_dir }}", shlex.quote(str(sge.scheduler_log_dir)))
        .replace("{{ shell_project_root }}", shlex.quote(str(context.project_root)))
        .replace("{{ shell_raw_bids_root }}", shlex.quote(str(plan.raw_bids_root)))
        .replace("{{ shell_heuristic_path }}", shlex.quote(str(plan.heuristic_path)))
        .replace("{{ shell_launcher_items }}", _format_shell_array_items(plan.launcher))
    )
    if "{{" in rendered or "}}" in rendered:
        raise HeudiconvRunError(
            "SGE scheduler template contains unsupported placeholders. "
            "Supported run placeholders are {{ job_name }}, {{ task_count }}, "
            "{{ attempt_label }}, {{ scheduler_log_dir }}, {{ unit_list_path }}, "
            "{{ shell_unit_list_path }}, {{ shell_scheduler_log_dir }}, {{ shell_project_root }}, "
            "{{ shell_raw_bids_root }}, {{ shell_heuristic_path }}, and "
            "{{ shell_launcher_items }}."
        )
    sge.script_path.write_text(rendered, encoding="utf-8", newline="\n")
    _make_executable(sge.script_path)


def _write_sge_unit_list(path: Path, units: tuple[RunUnitPlan, ...]) -> None:
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
    return "\n".join(f"    {shlex.quote(value)}" for value in values)


def _submit_sge_script(
    context: ProjectContext,
    sge: SgeRunPlan,
) -> subprocess.CompletedProcess[str]:
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
    sge = plan.sge
    if sge is None:  # pragma: no cover
        raise HeudiconvRunError("SGE run plan is missing.")

    planned_units = plan.units if units is None else units
    metadata: dict[str, object] = {
        "name": "sge",
        "array_range": f"1-{len(planned_units)}" if planned_units else "",
        "template_path": str(sge.template_path),
        "script_path": str(sge.script_path),
        "unit_list_path": str(sge.unit_list_path),
        "scheduler_log_dir": str(sge.scheduler_log_dir),
        "submit_command": list(sge.submit_command),
    }
    if job_id is not None:
        metadata["job_id"] = job_id
    return metadata


def _make_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | 0o111)


def format_command(argv: tuple[str, ...]) -> str:
    return subprocess.list2cmdline(list(argv))


def _resolve_scheduler_script_path(context: ProjectContext, target: str) -> Path:
    scheduler_template = context.execution.scheduler_template
    if scheduler_template is None:  # pragma: no cover
        raise HeudiconvInitError(
            f"[execution].scheduler_template is required when scheduler is {context.execution.scheduler!r}."
        )

    rendered = (
        scheduler_template.replace("{{ scheduler }}", context.execution.scheduler)
        .replace("{{ target }}", target)
    )
    if "{{" in rendered or "}}" in rendered:
        raise HeudiconvInitError(
            "[execution].scheduler_template currently supports only "
            "{{ scheduler }} and {{ target }} placeholders."
        )

    candidate = Path(rendered)
    if candidate.is_absolute():
        return candidate.resolve()
    return (context.project_root / candidate).resolve()


def _render_sge_heudiconv_script() -> str:
    return (
        resources.files("bidsflow")
        .joinpath("templates", "sge", "heudiconv.sh.template")
        .read_text(encoding="utf-8")
    )


def _ensure_project_owned_path(project_root: Path, path: Path) -> None:
    resolved_root = project_root.resolve()
    resolved_path = path.resolve()
    if not resolved_path.is_relative_to(resolved_root):
        raise HeudiconvInitError(
            f"Refusing to write HeuDiConv init file outside the project root: {resolved_path}"
        )


def _compile_sources_pattern(pattern: str) -> re.Pattern[str]:
    pattern_parts: list[str] = ["^"]
    fields: list[str] = []

    for literal_text, field_name, format_spec, conversion in Formatter().parse(pattern):
        pattern_parts.append(re.escape(literal_text))
        if field_name is None:
            continue
        if format_spec or conversion:
            raise SourcesError(
                "BIDSFlow sources pattern does not support format specs or conversions."
            )
        if not field_name.isidentifier():
            raise SourcesError(
                f"BIDSFlow sources pattern field is not a valid identifier: {field_name!r}"
            )
        if field_name not in {"subject", "session"}:
            raise SourcesError(
                "BIDSFlow sources pattern only supports {subject} and optional {session}."
            )
        fields.append(field_name)
        pattern_parts.append(f"(?P<{field_name}>.+?)")

    if "subject" not in fields:
        raise SourcesError(
            "BIDSFlow sources pattern must include a {subject} field."
        )

    pattern_parts.append("$")
    try:
        return re.compile("".join(pattern_parts))
    except re.error as exc:
        raise SourcesError(f"Invalid BIDSFlow sources pattern: {exc}") from exc


def _derive_sources_labels_from_pattern(
    source_name_pattern: re.Pattern[str],
    source_name: str,
) -> tuple[str, str, str]:
    match = source_name_pattern.fullmatch(source_name)
    if match is None:
        return "", "", "sources pattern did not match source_name"

    subject_label = match.groupdict().get("subject", "") or ""
    session_label = match.groupdict().get("session", "") or ""
    return subject_label, session_label, ""


def _derive_sources_labels_from_command(
    context: ProjectContext,
    command: tuple[str, ...],
    source_name: str,
) -> tuple[str, str, str]:
    invocation = [*command, source_name]
    try:
        completed = subprocess.run(
            invocation,
            cwd=context.project_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise SourcesError(
            f"Failed to start sources command while processing {source_name}: {exc}"
        ) from exc

    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()
        details = f" {stderr}" if stderr else ""
        raise SourcesError(
            f"sources command failed for {source_name} with exit code {completed.returncode}.{details}"
        )

    output = (completed.stdout or "").strip()
    if not output:
        raise SourcesError(
            f"sources command returned empty output for {source_name}."
        )

    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if len(lines) > 2:
        raise SourcesError(
            f"sources command returned more than two non-empty output lines for {source_name}."
        )

    subject_label = lines[0]
    session_label = lines[1] if len(lines) == 2 else ""
    return subject_label, session_label, ""


def _compute_sources_statuses(
    source_root: Path,
    entries: tuple[SourcesEntry, ...],
) -> tuple[SourcesEntry, ...]:
    included_entries = [entry for entry in entries if entry.include]
    subject_counts: dict[str, int] = {}
    target_counts: dict[tuple[str, str], int] = {}

    for entry in included_entries:
        subject_label = entry.subject_label.strip()
        session_label = entry.session_label.strip()
        if subject_label:
            subject_counts[subject_label] = subject_counts.get(subject_label, 0) + 1
        if subject_label and session_label:
            key = (subject_label, session_label)
            target_counts[key] = target_counts.get(key, 0) + 1

    updated_entries: list[SourcesEntry] = []
    for entry in entries:
        try:
            source_path = _resolve_sources_source_path(source_root, entry.source_name)
        except HeudiconvRunError:
            source_path = source_root / "__invalid_source_name__"
        subject_label = entry.subject_label.strip()
        session_label = entry.session_label.strip()

        if not entry.include:
            status = "excluded"
        elif not source_path.exists():
            status = "missing_source"
        elif not subject_label:
            status = "needs_review"
        elif session_label and target_counts.get((subject_label, session_label), 0) > 1:
            status = "collision"
        elif not session_label and subject_counts.get(subject_label, 0) > 1:
            status = "needs_review"
        else:
            status = "ready"

        updated_entries.append(
            SourcesEntry(
                source_name=entry.source_name,
                subject_label=subject_label,
                session_label=session_label,
                include=entry.include,
                status=status,
                notes=entry.notes,
            )
        )

    return tuple(updated_entries)


def _load_confirmed_sources(
    source_root: Path,
    sources_path: Path,
) -> tuple[SourcesEntry, ...]:
    with sources_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None:
            raise HeudiconvRunError(
                f"BIDSFlow sources table is missing a header row: {sources_path}"
            )

        required_columns = {
            "source_name",
            "subject_label",
            "session_label",
            "include",
            "status",
            "notes",
        }
        missing_columns = sorted(required_columns.difference(reader.fieldnames))
        if missing_columns:
            raise HeudiconvRunError(
                "BIDSFlow sources table is missing required columns: "
                + ", ".join(missing_columns)
            )

        entries: list[SourcesEntry] = []
        for row_number, row in enumerate(reader, start=2):
            source_name = (row.get("source_name") or "").strip()
            if not source_name:
                raise HeudiconvRunError(
                    f"BIDSFlow sources table row {row_number} is missing source_name."
                )

            include = _parse_sources_include(row.get("include"), row_number)
            entries.append(
                SourcesEntry(
                    source_name=source_name,
                    subject_label=(row.get("subject_label") or "").strip(),
                    session_label=(row.get("session_label") or "").strip(),
                    include=include,
                    status="",
                    notes=(row.get("notes") or "").strip(),
                )
            )

    return _compute_sources_statuses(source_root.resolve(), tuple(entries))


def _parse_sources_include(value: str | None, row_number: int) -> bool:
    normalized = (value or "").strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise HeudiconvRunError(
        f"BIDSFlow sources table row {row_number} has invalid include value: {value!r}"
    )


def _resolve_sources_source_path(source_root: Path, source_name: str) -> Path:
    source_candidate = Path(source_name)
    if source_candidate.name != source_name or source_name in {"", ".", ".."}:
        raise HeudiconvRunError(
            f"BIDSFlow sources table source_name must name an immediate child directory under source_root: {source_name!r}"
        )
    resolved_source_path = (source_root / source_candidate).resolve()
    if not resolved_source_path.is_relative_to(source_root.resolve()):  # pragma: no cover
        raise HeudiconvRunError(
            f"BIDSFlow sources table source_name resolves outside source_root: {source_name!r}"
        )
    return resolved_source_path


def _build_run_execution_path(
    execution_view_root: Path,
    subject_label: str,
    session_label: str | None,
) -> Path:
    if session_label is None:
        return execution_view_root / f"sub-{subject_label}"
    return execution_view_root / f"sub-{subject_label}" / f"ses-{session_label}"


def _build_run_unit_name(subject_label: str, session_label: str | None) -> str:
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
    if session_label is None:
        return f"sub-{subject_label}"
    return f"sub-{subject_label} ses-{session_label}"


def _prepare_run_directories(plan: RunPlan) -> None:
    plan.execution_view_root.mkdir(parents=True, exist_ok=True)
    plan.raw_bids_root.mkdir(parents=True, exist_ok=True)
    plan.state_path.parent.mkdir(parents=True, exist_ok=True)
    plan.units_path.parent.mkdir(parents=True, exist_ok=True)
    plan.unit_state_dir.mkdir(parents=True, exist_ok=True)
    plan.claim_dir.mkdir(parents=True, exist_ok=True)
    plan.log_dir.mkdir(parents=True, exist_ok=True)
    if plan.sge is not None:
        plan.sge.script_path.parent.mkdir(parents=True, exist_ok=True)
        plan.sge.unit_list_path.parent.mkdir(parents=True, exist_ok=True)
        plan.sge.scheduler_log_dir.mkdir(parents=True, exist_ok=True)


def _compute_input_signature(parts: dict[str, str | bytes]) -> str:
    digest = hashlib.sha256()
    for key in sorted(parts):
        digest.update(key.encode("utf-8"))
        digest.update(b"\0")
        value = parts[key]
        digest.update(value.encode("utf-8") if isinstance(value, str) else value)
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def _format_attempt_label() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_key_value_status_atomic(path: Path, lines: list[tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    temp_path.write_text(
        "".join(f"{key}={_format_status_value(value)}\n" for key, value in lines),
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temp_path, path)


def _format_status_value(value: str) -> str:
    return value.replace("\r", " ").replace("\n", " ").strip()


def _write_draft_state(
    *,
    context: ProjectContext,
    plan: DraftPlan,
    status: str,
    started_at: str,
    finished_at: str | None = None,
    failed_unit: DraftUnitPlan | None = None,
    error: str | None = None,
) -> None:
    payload: dict[str, object] = {
        "workflow": "heudiconv",
        "step": "draft",
        "status": status,
        "updated_at": _utc_now(),
        "started_at": started_at,
        "finished_at": finished_at,
        "config_path": str(context.config_path),
        "project_root": str(context.project_root),
        "sample_paths": [str(path) for path in plan.sample_paths],
        "launcher": list(plan.launcher),
        "artifacts": {
            "heuristic_template": str(plan.heuristic_path),
            "dicom_inventory_dir": str(plan.dicominfo_root),
            "draft_work_root": str(plan.draft_work_root),
            "heudiconv_state": str(plan.heudiconv_state_path),
        },
        "unit_log_dir": str(plan.log_dir),
    }
    if failed_unit is not None:
        payload["failed_sample"] = str(failed_unit.sample_path)
    if error is not None:
        payload["error"] = error
    _write_json(plan.draft_state_path, payload)


RUN_UNITS_TSV_COLUMNS = (
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


def _ensure_run_units_tsv_header(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(RUN_UNITS_TSV_COLUMNS)


def _append_run_units_tsv(
    path: Path,
    unit_results: tuple[RunUnitResult, ...],
) -> None:
    _ensure_run_units_tsv_header(path)
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
) -> ClaimSelection:
    claimed_units: list[RunUnitPlan] = []
    skipped_succeeded = 0
    skipped_active_claim = 0

    for unit in units:
        if _unit_status_is_succeeded(unit.status_path):
            skipped_succeeded += 1
            continue
        try:
            _write_unit_claim(
                unit=unit,
            )
        except FileExistsError:
            skipped_active_claim += 1
            continue
        claimed_units.append(unit)

    return ClaimSelection(
        claimed_units=tuple(claimed_units),
        skipped_succeeded=skipped_succeeded,
        skipped_active_claim=skipped_active_claim,
    )


def _build_run_unit_counts(
    planned_units: tuple[RunUnitPlan, ...],
    claim_selection: ClaimSelection,
) -> dict[str, int]:
    skipped = claim_selection.skipped_succeeded + claim_selection.skipped_active_claim
    return {
        "total": len(planned_units),
        "selected": len(claim_selection.claimed_units),
        "skipped": skipped,
        "skipped_succeeded": claim_selection.skipped_succeeded,
        "skipped_active_claim": claim_selection.skipped_active_claim,
    }


def _unit_status_is_succeeded(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        return _read_key_value_status(path).get("status") == "succeeded"
    except OSError:
        return False


def _read_key_value_status(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def _write_unit_claim(*, unit: RunUnitPlan) -> None:
    unit.claim_path.parent.mkdir(parents=True, exist_ok=True)
    with unit.claim_path.open("x", encoding="utf-8"):
        pass


def _release_unit_claim(unit: RunUnitPlan) -> None:
    try:
        unit.claim_path.unlink()
    except FileNotFoundError:
        return


def _release_unfinished_claims(
    units: tuple[RunUnitPlan, ...],
    unit_results: tuple[RunUnitResult, ...] | list[RunUnitResult],
    current_unit: RunUnitPlan | None,
) -> None:
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
    if scheduler_job_id is not None:
        lines.extend(
            (
                ("scheduler", "sge"),
                ("job_id", scheduler_job_id),
                ("task_id", scheduler_task_id or ""),
            )
        )
    if error is not None:
        lines.append(("error", error))
    _write_key_value_status_atomic(unit.status_path, lines)


def _write_run_state(
    *,
    context: ProjectContext,
    plan: RunPlan,
    status: str,
    started_at: str,
    backend: str = "local",
    finished_at: str | None = None,
    execution_view_cleaned: bool | None = None,
    scheduler: dict[str, object] | None = None,
    unit_counts: dict[str, int] | None = None,
    error: str | None = None,
) -> None:
    payload: dict[str, object] = {
        "workflow": "heudiconv",
        "step": "run",
        "backend": backend,
        "status": status,
        "updated_at": _utc_now(),
        "started_at": started_at,
        "finished_at": finished_at,
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
        "artifacts": {
            "raw_bids_dataset": str(plan.raw_bids_root),
            "run_table": str(plan.units_path),
            "unit_status_dir": str(plan.unit_state_dir),
            "claim_dir": str(plan.claim_dir),
            "log_dir": str(plan.log_dir),
        },
        "log_dir": str(plan.log_dir),
        "unit_table_path": str(plan.units_path),
        "unit_status_dir": str(plan.unit_state_dir),
        "claim_dir": str(plan.claim_dir),
    }
    if scheduler is not None:
        payload["scheduler"] = scheduler
    if unit_counts is not None:
        payload["unit_counts"] = unit_counts
    if execution_view_cleaned is not None:
        payload["execution_view_cleaned"] = execution_view_cleaned
    if error is not None:
        payload["error"] = error
    _write_json(plan.state_path, payload)


def _materialize_run_execution_view(execution_path: Path, source_path: Path) -> None:
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
    if not execution_view_root.exists():
        return True
    try:
        _remove_project_path(project_root, execution_view_root)
    except ValueError:
        return False
    return not execution_view_root.exists()


def _build_draft_command(
    launcher: tuple[str, ...],
    sample_path: Path,
    output_root: Path,
    *,
    subject_label: str | None = None,
    session_label: str | None = None,
) -> tuple[str, ...]:
    command: list[str] = [*launcher, "--files", str(sample_path)]
    if subject_label is not None:
        command.extend(["-s", subject_label])
    if session_label is not None:
        command.extend(["-ss", session_label])
    command.extend(
        [
            "-o",
            str(output_root),
            "-f",
            "convertall",
            "-c",
            "none",
        ]
    )
    return tuple(command)


def _guard_sources_reset_requirement(plan: SourcesPlan, reset: bool) -> None:
    if reset:
        return
    if plan.sources_path.exists() or plan.sources_state_path.exists():
        raise SourcesError(
            "Existing BIDSFlow sources state was found. Use --force to regenerate it."
        )


def _prepare_sources_directories(plan: SourcesPlan) -> None:
    plan.sources_path.parent.mkdir(parents=True, exist_ok=True)
    plan.sources_state_path.parent.mkdir(parents=True, exist_ok=True)


def _reset_sources_state(project_root: Path, plan: SourcesPlan) -> None:
    try:
        for path in (plan.sources_path, plan.sources_state_path):
            _remove_project_path(project_root, path)
    except ValueError as exc:
        raise SourcesError(str(exc)) from exc


def _guard_draft_reset_requirement(plan: DraftPlan, reset: bool) -> None:
    if reset:
        return
    if plan.draft_state_path.exists() or plan.heudiconv_state_path.exists():
        raise HeudiconvDraftError(
            "Existing HeuDiConv draft state was found. Use --force to regenerate it."
        )


def _prepare_draft_directories(plan: DraftPlan) -> None:
    plan.draft_work_root.mkdir(parents=True, exist_ok=True)
    plan.code_root.mkdir(parents=True, exist_ok=True)
    plan.heuristic_path.parent.mkdir(parents=True, exist_ok=True)
    plan.dicominfo_root.mkdir(parents=True, exist_ok=True)
    plan.draft_state_path.parent.mkdir(parents=True, exist_ok=True)
    plan.log_dir.mkdir(parents=True, exist_ok=True)


def _reset_draft_state(project_root: Path, plan: DraftPlan) -> None:
    try:
        for path in (
            plan.draft_work_root,
            plan.heudiconv_state_path,
            plan.heuristic_path,
            plan.dicominfo_root,
            plan.draft_state_path,
        ):
            _remove_project_path(project_root, path)
    except ValueError as exc:
        raise HeudiconvDraftError(str(exc)) from exc


def _remove_project_path(project_root: Path, path: Path) -> None:
    resolved_root = project_root.resolve()
    resolved_path = path.resolve()
    if not resolved_path.is_relative_to(resolved_root):
        raise ValueError(f"Refusing to remove path outside the project root: {resolved_path}")
    if not resolved_path.exists():
        return
    if resolved_path.is_dir():
        shutil.rmtree(resolved_path)
        return
    resolved_path.unlink()


def _run_draft_unit(
    context: ProjectContext,
    plan: DraftPlan,
    unit: DraftUnitPlan,
) -> DraftUnitResult:
    completed, _, started_at_ns = _run_command(
        context,
        unit.log_path,
        unit.initial_command,
        label=unit.unit_name,
    )
    if completed.returncode != 0:
        if unit.session_label is None:
            raise HeudiconvDraftError(
                "HeuDiConv draft generation failed for the provided sample path. "
                "BIDSFlow already used a temporary subject id for this draft run; "
                "the directory may not be a clean single-subject, single-session input. "
                f"See {unit.log_path} for details."
            )
        raise HeudiconvDraftError(
            "HeuDiConv draft generation failed while processing a representative session directory. "
            "BIDSFlow treats multiple input directories as separate single-directory draft units; "
            "check whether this directory mixes scans from multiple sessions or incompatible content. "
            f"See {unit.log_path} for details."
        )

    return _collect_unit_result(
        plan=plan,
        unit=unit,
        final_command=unit.initial_command,
        attempted_commands=(unit.initial_command,),
        subject_label=unit.subject_label,
        session_label=unit.session_label,
        strategy="generated_subject" if unit.session_label is None else "generated_multi_session",
        started_at_ns=started_at_ns,
    )


def _run_command(
    context: ProjectContext,
    log_path: Path,
    command: tuple[str, ...],
    *,
    label: str,
) -> tuple[subprocess.CompletedProcess[str], str, int]:
    started_at_ns = time.time_ns()
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
        raise HeudiconvDraftError(
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
    return completed, combined_output, started_at_ns


def _combine_process_output(stdout: str | None, stderr: str | None) -> str:
    parts = [part.strip("\n") for part in (stdout or "", stderr or "") if part]
    return "\n".join(parts)


def _append_log(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", newline="\n") as log_handle:
        if log_handle.tell() > 0:
            log_handle.write("\n")
        log_handle.write(message.rstrip())
        log_handle.write("\n")


def _collect_unit_result(
    *,
    plan: DraftPlan,
    unit: DraftUnitPlan,
    final_command: tuple[str, ...],
    attempted_commands: tuple[tuple[str, ...], ...],
    subject_label: str | None,
    session_label: str | None,
    strategy: str,
    started_at_ns: int,
) -> DraftUnitResult:
    generated_heuristic = _find_latest_generated_file_since(
        plan.heudiconv_state_path,
        "heuristic.py",
        started_at_ns,
    )
    generated_dicominfo_paths = _find_generated_dicominfo_files_since(
        plan.heudiconv_state_path,
        started_at_ns,
    )
    copied_dicominfo_paths = _copy_dicominfo_files(
        plan.dicominfo_root / unit.unit_name,
        generated_dicominfo_paths,
    )

    return DraftUnitResult(
        index=unit.index,
        sample_path=unit.sample_path,
        unit_name=unit.unit_name,
        subject_label=subject_label,
        session_label=session_label,
        strategy=strategy,
        attempted_commands=attempted_commands,
        generated_heuristic=generated_heuristic,
        generated_dicominfo_paths=generated_dicominfo_paths,
        copied_dicominfo_paths=copied_dicominfo_paths,
        log_path=unit.log_path,
    )


def _find_latest_generated_file_since(root: Path, filename: str, started_at_ns: int) -> Path:
    candidates = [
        candidate
        for candidate in root.rglob(filename)
        if candidate.is_file() and candidate.stat().st_mtime_ns >= started_at_ns
    ]
    if not candidates:
        raise HeudiconvDraftError(
            f"HeuDiConv draft generation did not produce {filename} under {root}."
        )
    return max(candidates, key=lambda candidate: candidate.stat().st_mtime_ns)


def _find_generated_dicominfo_files_since(root: Path, started_at_ns: int) -> tuple[Path, ...]:
    candidates = [
        candidate
        for candidate in root.rglob("dicominfo*.tsv")
        if candidate.is_file() and candidate.stat().st_mtime_ns >= started_at_ns
    ]
    if not candidates:
        raise HeudiconvDraftError(
            f"HeuDiConv draft generation did not produce dicominfo output under {root}."
        )
    return tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                str(candidate.parent).lower(),
                candidate.name.lower(),
            ),
        )
    )


def _merge_heuristic(destination: Path, generated_heuristic: Path) -> None:
    if not destination.exists():
        shutil.copy2(generated_heuristic, destination)
        return

    existing_text = destination.read_text(encoding="utf-8")
    generated_text = generated_heuristic.read_text(encoding="utf-8")
    if existing_text != generated_text:
        raise HeudiconvDraftError(
            "Generated heuristic drafts differed across draft units. "
            "Review the sample directories and rerun draft with a narrower input set."
        )


def _copy_dicominfo_files(destination_root: Path, generated_paths: tuple[Path, ...]) -> tuple[Path, ...]:
    common_parent = Path(os.path.commonpath([str(path.parent) for path in generated_paths]))
    copied_paths: list[Path] = []

    for generated_path in generated_paths:
        relative_path = generated_path.relative_to(common_parent)
        destination = destination_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(generated_path, destination)
        copied_paths.append(destination)

    return tuple(copied_paths)


def _write_sources_tsv(sources_path: Path, entries: tuple[SourcesEntry, ...]) -> None:
    with sources_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(
            (
                "source_name",
                "subject_label",
                "session_label",
                "include",
                "status",
                "notes",
            )
        )
        for entry in entries:
            writer.writerow(
                (
                    entry.source_name,
                    entry.subject_label,
                    entry.session_label,
                    "true" if entry.include else "false",
                    entry.status,
                    entry.notes,
                )
            )


def _write_sources_state(context: ProjectContext, plan: SourcesPlan) -> None:
    payload = {
        "step": "sources",
        "status": "succeeded",
        "recorded_at": datetime.now(UTC).isoformat(),
        "config_path": str(context.config_path),
        "project_root": str(context.project_root),
        "source_root": str(plan.source_root),
        "artifacts": {
            "sources": str(plan.sources_path),
        },
        "handoff": {
            "role": "truth_source",
            "derived_execution_views": {
                "links": {
                    "managed_by": "heudiconv",
                    "lifecycle": "ephemeral",
                }
            },
        },
        "label_generation": {
            "pattern": plan.pattern,
            "command": list(plan.command) if plan.command is not None else None,
        },
        "entry_count": len(plan.entries),
        "status_summary": _summarize_sources_entries(plan.entries),
    }

    plan.sources_state_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def summarize_sources_entries(entries: tuple[SourcesEntry, ...]) -> dict[str, int]:
    return _summarize_sources_entries(entries)


def list_sources_review_issues(entries: tuple[SourcesEntry, ...]) -> list[str]:
    issues: list[str] = []

    missing_labels = [entry.source_name for entry in entries if entry.status == "needs_review"]
    if missing_labels:
        issues.append(
            f"needs_review: {len(missing_labels)} row(s) still need final labels or session disambiguation"
        )

    collisions = [entry.source_name for entry in entries if entry.status == "collision"]
    if collisions:
        issues.append(
            "collision: " + ", ".join(collisions)
        )

    missing_sources = [entry.source_name for entry in entries if entry.status == "missing_source"]
    if missing_sources:
        issues.append(
            "missing_source: " + ", ".join(missing_sources)
        )

    return issues


def _summarize_sources_entries(entries: tuple[SourcesEntry, ...]) -> dict[str, int]:
    summary = {
        "total": len(entries),
        "ready": 0,
        "needs_review": 0,
        "collision": 0,
        "missing_source": 0,
        "excluded": 0,
    }
    for entry in entries:
        summary[entry.status] = summary.get(entry.status, 0) + 1
    return summary
