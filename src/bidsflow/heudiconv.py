from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
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
    template: str | None
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
class RunUnitPlan:
    index: int
    source_name: str
    source_path: Path
    subject_label: str
    session_label: str | None
    execution_path: Path
    command: tuple[str, ...]
    log_path: Path


@dataclass(frozen=True)
class RunPlan:
    sources_path: Path
    sources_state_path: Path
    launcher: tuple[str, ...]
    heuristic_path: Path
    raw_bids_root: Path
    execution_view_root: Path
    state_path: Path
    units_path: Path
    log_dir: Path
    entries: tuple[SourcesEntry, ...]
    units: tuple[RunUnitPlan, ...]


@dataclass(frozen=True)
class RunUnitResult:
    index: int
    source_name: str
    source_path: Path
    subject_label: str
    session_label: str | None
    execution_path: Path
    command: tuple[str, ...]
    execution_view_kind: str
    log_path: Path


@dataclass(frozen=True)
class RunResult:
    raw_bids_root: Path
    state_path: Path
    units_path: Path
    log_dir: Path
    unit_results: tuple[RunUnitResult, ...]


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
    template_pattern: re.Pattern[str] | None
    if sources_config.template is not None:
        template_pattern = _compile_sources_template(sources_config.template)
    else:
        template_pattern = None

    entries: list[SourcesEntry] = []
    for candidate in source_units:
        if template_pattern is not None:
            subject_label, session_label, notes = _derive_sources_labels_from_template(
                template_pattern,
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
        template=sources_config.template,
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


def plan_heudiconv_run(context: ProjectContext) -> RunPlan:
    sources_path = context.paths.state_root / "sources.tsv"
    if not sources_path.exists():
        raise HeudiconvRunError(
            f"BIDSFlow sources table does not exist: {sources_path}. Run `bidsflow sources` first."
        )

    heuristic_path = context.heudiconv.heuristic
    if not heuristic_path.exists():
        raise HeudiconvRunError(
            f"HeuDiConv heuristic does not exist: {heuristic_path}. Run `bidsflow heudiconv --draft <sample-path>` or create the heuristic first."
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
    log_root = context.paths.logs_root / "heudiconv"
    unit_log_root = log_root / f"run-{attempt_label}"

    units: list[RunUnitPlan] = []
    for index, entry in enumerate(ready_entries, start=1):
        session_label = entry.session_label or None
        execution_path = _build_run_execution_path(
            execution_view_root,
            entry.subject_label,
            session_label,
        )
        units.append(
            RunUnitPlan(
                index=index,
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
                log_path=unit_log_root / f"{entry.source_name}.log",
            )
        )

    return RunPlan(
        sources_path=sources_path,
        sources_state_path=context.paths.state_root / "sources.json",
        launcher=context.heudiconv.launcher,
        heuristic_path=heuristic_path,
        raw_bids_root=context.paths.raw_bids_root,
        execution_view_root=execution_view_root,
        state_path=state_path,
        units_path=units_path,
        log_dir=unit_log_root,
        entries=entries,
        units=tuple(units),
    )


def run_heudiconv(context: ProjectContext, plan: RunPlan) -> RunResult:
    execution_view_cleared = _cleanup_run_execution_view(
        context.project_root,
        plan.execution_view_root,
    )
    if not execution_view_cleared:
        raise HeudiconvRunError(
            f"Failed to clear prior run execution view: {plan.execution_view_root}"
        )

    _prepare_run_directories(plan)
    _remove_state_file(plan.units_path)

    started_at = _utc_now()
    _write_run_state(
        context=context,
        plan=plan,
        status="running",
        started_at=started_at,
    )

    unit_results: list[RunUnitResult] = []
    current_unit: RunUnitPlan | None = None
    try:
        for unit in plan.units:
            current_unit = unit
            execution_view_kind = _materialize_run_execution_view(
                unit.execution_path,
                unit.source_path,
            )
            completed = _run_heudiconv_command(
                context,
                unit.log_path,
                unit.command,
                label=unit.source_name,
            )
            if completed.returncode != 0:
                raise HeudiconvRunError(
                    "HeuDiConv execution failed while processing "
                    f"{unit.source_name}. See {unit.log_path} for details."
                )

            unit_results.append(
                RunUnitResult(
                    index=unit.index,
                    source_name=unit.source_name,
                    source_path=unit.source_path,
                    subject_label=unit.subject_label,
                    session_label=unit.session_label,
                    execution_path=unit.execution_path,
                    command=unit.command,
                    execution_view_kind=execution_view_kind,
                    log_path=unit.log_path,
                )
            )
            current_unit = None
    except HeudiconvRunError as exc:
        execution_view_cleaned = _cleanup_run_execution_view(
            context.project_root,
            plan.execution_view_root,
        )
        _write_run_units_tsv(
            plan.units_path,
            plan.units,
            tuple(unit_results),
            failed_unit=current_unit,
            error=str(exc),
        )
        _write_run_state(
            context=context,
            plan=plan,
            status="failed",
            started_at=started_at,
            finished_at=_utc_now(),
            execution_view_cleaned=execution_view_cleaned,
            error=str(exc),
        )
        raise

    execution_view_cleaned = _cleanup_run_execution_view(
        context.project_root,
        plan.execution_view_root,
    )
    _write_run_units_tsv(
        plan.units_path,
        plan.units,
        tuple(unit_results),
    )
    _write_run_state(
        context=context,
        plan=plan,
        status="succeeded",
        started_at=started_at,
        finished_at=_utc_now(),
        execution_view_cleaned=execution_view_cleaned,
    )

    return RunResult(
        raw_bids_root=plan.raw_bids_root,
        state_path=plan.state_path,
        units_path=plan.units_path,
        log_dir=plan.log_dir,
        unit_results=tuple(unit_results),
    )


def format_command(argv: tuple[str, ...]) -> str:
    return subprocess.list2cmdline(list(argv))


def _compile_sources_template(template: str) -> re.Pattern[str]:
    pattern_parts: list[str] = ["^"]
    fields: list[str] = []

    for literal_text, field_name, format_spec, conversion in Formatter().parse(template):
        pattern_parts.append(re.escape(literal_text))
        if field_name is None:
            continue
        if format_spec or conversion:
            raise SourcesError(
                "BIDSFlow sources table template does not support format specs or conversions."
            )
        if not field_name.isidentifier():
            raise SourcesError(
                f"BIDSFlow sources table template field is not a valid identifier: {field_name!r}"
            )
        if field_name not in {"subject", "session"}:
            raise SourcesError(
                "BIDSFlow sources table template only supports {subject} and optional {session}."
            )
        fields.append(field_name)
        pattern_parts.append(f"(?P<{field_name}>.+?)")

    if "subject" not in fields:
        raise SourcesError(
            "BIDSFlow sources table template must include a {subject} field."
        )

    pattern_parts.append("$")
    try:
        return re.compile("".join(pattern_parts))
    except re.error as exc:
        raise SourcesError(f"Invalid BIDSFlow sources table template: {exc}") from exc


def _derive_sources_labels_from_template(
    template_pattern: re.Pattern[str],
    source_name: str,
) -> tuple[str, str, str]:
    match = template_pattern.fullmatch(source_name)
    if match is None:
        return "", "", "sources template did not match source_name"

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
    except FileNotFoundError as exc:
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
    if not resolved_source_path.is_relative_to(source_root.resolve()):
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
    plan.log_dir.mkdir(parents=True, exist_ok=True)


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


def _remove_state_file(path: Path) -> None:
    if path.exists():
        path.unlink()


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


def _build_run_unit_rows(
    planned_units: tuple[RunUnitPlan, ...],
    unit_results: tuple[RunUnitResult, ...],
    *,
    failed_unit: RunUnitPlan | None = None,
    error: str | None = None,
) -> tuple[dict[str, str], ...]:
    completed_by_source = {result.source_name: result for result in unit_results}
    rows: list[dict[str, str]] = []

    for unit in planned_units:
        if unit.source_name in completed_by_source:
            status = "succeeded"
            notes = ""
        elif failed_unit is not None and unit.source_name == failed_unit.source_name:
            status = "failed"
            notes = error or ""
        else:
            status = "not_run"
            notes = ""

        rows.append(
            {
                "source_name": unit.source_name,
                "subject_label": unit.subject_label,
                "session_label": unit.session_label or "",
                "summary": _build_run_unit_summary(unit.subject_label, unit.session_label),
                "status": status,
                "log_path": str(unit.log_path),
                "notes": notes,
            }
        )

    return tuple(rows)


def _write_run_units_tsv(
    path: Path,
    planned_units: tuple[RunUnitPlan, ...],
    unit_results: tuple[RunUnitResult, ...],
    *,
    failed_unit: RunUnitPlan | None = None,
    error: str | None = None,
) -> None:
    rows = _build_run_unit_rows(
        planned_units,
        unit_results,
        failed_unit=failed_unit,
        error=error,
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(
            (
                "source_name",
                "subject_label",
                "session_label",
                "summary",
                "status",
                "log_path",
                "notes",
            )
        )
        for row in rows:
            writer.writerow(
                (
                    row["source_name"],
                    row["subject_label"],
                    row["session_label"],
                    row["summary"],
                    row["status"],
                    row["log_path"],
                    row["notes"],
                )
            )


def _write_run_state(
    *,
    context: ProjectContext,
    plan: RunPlan,
    status: str,
    started_at: str,
    finished_at: str | None = None,
    execution_view_cleaned: bool | None = None,
    error: str | None = None,
) -> None:
    payload: dict[str, object] = {
        "workflow": "heudiconv",
        "step": "run",
        "backend": "local",
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
        },
        "unit_log_dir": str(plan.log_dir),
        "unit_table_path": str(plan.units_path),
    }
    if execution_view_cleaned is not None:
        payload["execution_view_cleaned"] = execution_view_cleaned
    if error is not None:
        payload["error"] = error
    _write_json(plan.state_path, payload)


def _materialize_run_execution_view(execution_path: Path, source_path: Path) -> str:
    execution_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        execution_path.symlink_to(source_path, target_is_directory=True)
        return "symlink"
    except OSError:
        if os.name != "nt":
            raise HeudiconvRunError(
                f"Failed to create temporary execution link: {execution_path} -> {source_path}"
            ) from None

    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(execution_path), str(source_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not execution_path.exists():
        details = (completed.stderr or completed.stdout or "").strip()
        suffix = f" {details}" if details else ""
        raise HeudiconvRunError(
            f"Failed to create temporary execution link: {execution_path} -> {source_path}.{suffix}"
        )
    return "junction"


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
    except FileNotFoundError as exc:
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
            "Existing BIDSFlow sources state was found. Use --reset to regenerate it."
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
            "Existing HeuDiConv draft state was found. Use --reset to regenerate it."
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
    except FileNotFoundError as exc:
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
            "template": plan.template,
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

