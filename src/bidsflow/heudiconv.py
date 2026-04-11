from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import csv
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
class SkeletonUnitPlan:
    index: int
    sample_path: Path
    unit_name: str
    initial_command: tuple[str, ...]
    subject_label: str | None
    session_label: str | None


@dataclass(frozen=True)
class SkeletonPlan:
    sample_paths: tuple[Path, ...]
    launcher: tuple[str, ...]
    units: tuple[SkeletonUnitPlan, ...]
    code_root: Path
    heuristic_path: Path
    dicominfo_root: Path
    skeleton_work_root: Path
    heudiconv_state_path: Path
    skeleton_state_path: Path
    log_path: Path


@dataclass(frozen=True)
class SkeletonUnitResult:
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


@dataclass(frozen=True)
class SkeletonResult:
    heuristic_path: Path
    dicominfo_root: Path
    dicominfo_paths: tuple[Path, ...]
    skeleton_state_path: Path
    log_path: Path
    unit_results: tuple[SkeletonUnitResult, ...]


class HeudiconvSkeletonError(Exception):
    pass


@dataclass(frozen=True)
class ManifestEntry:
    source_name: str
    subject_label: str
    session_label: str
    include: bool
    status: str
    notes: str


@dataclass(frozen=True)
class ManifestPlan:
    source_root: Path
    manifest_path: Path
    manifest_state_path: Path
    template: str | None
    command: tuple[str, ...] | None
    entries: tuple[ManifestEntry, ...]


@dataclass(frozen=True)
class ManifestResult:
    manifest_path: Path
    manifest_state_path: Path
    entries: tuple[ManifestEntry, ...]


class HeudiconvManifestError(Exception):
    pass


@dataclass(frozen=True)
class ConvertUnitPlan:
    index: int
    source_name: str
    source_path: Path
    subject_label: str
    session_label: str | None
    execution_path: Path
    command: tuple[str, ...]


@dataclass(frozen=True)
class ConvertPlan:
    manifest_path: Path
    manifest_state_path: Path
    launcher: tuple[str, ...]
    heuristic_path: Path
    raw_bids_root: Path
    execution_view_root: Path
    run_root: Path
    state_path: Path
    log_path: Path
    entries: tuple[ManifestEntry, ...]
    units: tuple[ConvertUnitPlan, ...]


@dataclass(frozen=True)
class ConvertUnitResult:
    index: int
    source_name: str
    source_path: Path
    subject_label: str
    session_label: str | None
    execution_path: Path
    command: tuple[str, ...]
    execution_view_kind: str


@dataclass(frozen=True)
class ConvertResult:
    raw_bids_root: Path
    state_path: Path
    log_path: Path
    unit_results: tuple[ConvertUnitResult, ...]


class HeudiconvConvertError(Exception):
    pass


def plan_skeleton(context: ProjectContext, sample_paths: list[Path]) -> SkeletonPlan:
    if not sample_paths:
        raise HeudiconvSkeletonError("At least one sample path is required for skeleton generation.")

    source_root = context.paths.source_root.resolve()
    if not source_root.exists():
        raise HeudiconvSkeletonError(
            f"Configured source root does not exist: {source_root}"
        )
    if not source_root.is_dir():
        raise HeudiconvSkeletonError(
            f"Configured source root is not a directory: {source_root}"
        )

    resolved_samples: list[Path] = []
    for sample_path in sample_paths:
        resolved_sample = _resolve_skeleton_sample_path(
            source_root=source_root,
            project_root=context.project_root,
            sample_path=sample_path,
        )
        if not resolved_sample.exists():
            raise HeudiconvSkeletonError(f"Sample path does not exist: {resolved_sample}")
        resolved_samples.append(resolved_sample)

    launcher = context.heudiconv.launcher
    code_root = context.project_root / "code" / "heudiconv"
    state_root = context.paths.state_root / "heudiconv"
    log_root = context.paths.logs_root / "heudiconv"
    skeleton_work_root = state_root / "skeleton-work"

    if len(resolved_samples) == 1:
        units = (
            SkeletonUnitPlan(
                index=1,
                sample_path=resolved_samples[0],
                unit_name="sample-01",
                initial_command=_build_skeleton_command(
                    launcher,
                    resolved_samples[0],
                    skeleton_work_root,
                    subject_label="skeleton01",
                ),
                subject_label="skeleton01",
                session_label=None,
            ),
        )
    else:
        generated_subject = "skeleton01"
        units = tuple(
            SkeletonUnitPlan(
                index=index,
                sample_path=sample_path,
                unit_name=f"skeleton-ses{index:02d}",
                initial_command=_build_skeleton_command(
                    launcher,
                    sample_path,
                    skeleton_work_root,
                    subject_label=generated_subject,
                    session_label=f"skeleton-ses{index:02d}",
                ),
                subject_label=generated_subject,
                session_label=f"skeleton-ses{index:02d}",
            )
            for index, sample_path in enumerate(resolved_samples, start=1)
        )

    return SkeletonPlan(
        sample_paths=tuple(resolved_samples),
        launcher=launcher,
        units=units,
        code_root=code_root,
        heuristic_path=context.heudiconv.heuristic,
        dicominfo_root=code_root / "dicominfo",
        skeleton_work_root=skeleton_work_root,
        heudiconv_state_path=skeleton_work_root / ".heudiconv",
        skeleton_state_path=state_root / "skeleton.json",
        log_path=log_root / "skeleton.log",
    )


def _resolve_skeleton_sample_path(
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
        raise HeudiconvSkeletonError(
            f"Sample path must resolve under the configured source root {source_root}: {sample_path}"
        )

    return resolved_sample


def run_skeleton(context: ProjectContext, plan: SkeletonPlan, reset: bool) -> SkeletonResult:
    _guard_skeleton_reset_requirement(plan, reset)
    _prepare_skeleton_directories(plan)

    if reset:
        _reset_skeleton_state(context.project_root, plan)
        _prepare_skeleton_directories(plan)

    plan.log_path.write_text("", encoding="utf-8", newline="\n")

    unit_results: list[SkeletonUnitResult] = []
    copied_dicominfo_paths: list[Path] = []

    try:
        for unit in plan.units:
            unit_result = _run_skeleton_unit(context, plan, unit)
            _merge_heuristic(plan.heuristic_path, unit_result.generated_heuristic)
            unit_results.append(unit_result)
            copied_dicominfo_paths.extend(unit_result.copied_dicominfo_paths)
    except HeudiconvSkeletonError as exc:
        _write_skeleton_record(
            context=context,
            plan=plan,
            status="failed",
            unit_results=tuple(unit_results),
            copied_dicominfo_paths=tuple(copied_dicominfo_paths),
            error=str(exc),
        )
        raise

    _write_skeleton_record(
        context=context,
        plan=plan,
        status="succeeded",
        unit_results=tuple(unit_results),
        copied_dicominfo_paths=tuple(copied_dicominfo_paths),
    )

    return SkeletonResult(
        heuristic_path=plan.heuristic_path,
        dicominfo_root=plan.dicominfo_root,
        dicominfo_paths=tuple(copied_dicominfo_paths),
        skeleton_state_path=plan.skeleton_state_path,
        log_path=plan.log_path,
        unit_results=tuple(unit_results),
    )


def plan_manifest(
    context: ProjectContext,
) -> ManifestPlan:
    resolved_source_root = context.paths.source_root.resolve()
    if not resolved_source_root.exists():
        raise HeudiconvManifestError(
            f"Configured source root does not exist: {resolved_source_root}"
        )
    if not resolved_source_root.is_dir():
        raise HeudiconvManifestError(
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

    manifest_config = context.heudiconv.manifest
    template_pattern: re.Pattern[str] | None
    if manifest_config.template is not None:
        template_pattern = _compile_manifest_template(manifest_config.template)
    else:
        template_pattern = None

    entries: list[ManifestEntry] = []
    for candidate in source_units:
        if template_pattern is not None:
            subject_label, session_label, notes = _derive_manifest_labels_from_template(
                template_pattern,
                candidate.name,
            )
        elif manifest_config.command is not None:
            subject_label, session_label, notes = _derive_manifest_labels_from_command(
                context,
                manifest_config.command,
                candidate.name,
            )
        else:
            subject_label, session_label, notes = "", "", ""

        entries.append(
            ManifestEntry(
                source_name=candidate.name,
                subject_label=subject_label,
                session_label=session_label,
                include=True,
                status="",
                notes=notes,
            )
        )

    computed_entries = _compute_manifest_statuses(resolved_source_root, tuple(entries))
    code_root = context.project_root / "code" / "heudiconv"
    state_root = context.paths.state_root / "heudiconv"

    return ManifestPlan(
        source_root=resolved_source_root,
        manifest_path=code_root / "manifest.tsv",
        manifest_state_path=state_root / "manifest.json",
        template=manifest_config.template,
        command=manifest_config.command,
        entries=computed_entries,
    )


def run_manifest(context: ProjectContext, plan: ManifestPlan, reset: bool) -> ManifestResult:
    _guard_manifest_reset_requirement(plan, reset)
    _prepare_manifest_directories(plan)

    if reset:
        _reset_manifest_state(context.project_root, plan)
        _prepare_manifest_directories(plan)

    _write_manifest_tsv(plan.manifest_path, plan.entries)
    _write_manifest_state(context, plan)

    return ManifestResult(
        manifest_path=plan.manifest_path,
        manifest_state_path=plan.manifest_state_path,
        entries=plan.entries,
    )


def plan_convert(context: ProjectContext) -> ConvertPlan:
    manifest_path = context.project_root / "code" / "heudiconv" / "manifest.tsv"
    if not manifest_path.exists():
        raise HeudiconvConvertError(
            f"HeuDiConv manifest does not exist: {manifest_path}. Run `bidsflow heudiconv manifest` first."
        )

    heuristic_path = context.heudiconv.heuristic
    if not heuristic_path.exists():
        raise HeudiconvConvertError(
            f"HeuDiConv heuristic does not exist: {heuristic_path}. Run `bidsflow heudiconv skeleton` or create the heuristic first."
        )
    if not heuristic_path.is_file():
        raise HeudiconvConvertError(
            f"HeuDiConv heuristic is not a file: {heuristic_path}"
        )

    entries = _load_confirmed_manifest(context.paths.source_root, manifest_path)
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
        issue_summary = "; ".join(list_manifest_review_issues(entries))
        suffix = f" Issues: {issue_summary}" if issue_summary else ""
        raise HeudiconvConvertError(
            "HeuDiConv manifest still needs review before conversion." + suffix
        )
    if not ready_entries:
        raise HeudiconvConvertError(
            "HeuDiConv manifest does not contain any included ready rows to convert."
        )

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_root = context.paths.state_root / "heudiconv" / "convert-runs" / run_id
    execution_view_root = run_root / "links"
    state_path = run_root / "convert.json"
    log_path = context.paths.logs_root / "heudiconv" / f"convert-{run_id}.log"

    units: list[ConvertUnitPlan] = []
    for index, entry in enumerate(ready_entries, start=1):
        session_label = entry.session_label or None
        execution_path = _build_convert_execution_path(
            execution_view_root,
            entry.subject_label,
            session_label,
        )
        units.append(
            ConvertUnitPlan(
                index=index,
                source_name=entry.source_name,
                source_path=_resolve_manifest_source_path(
                    context.paths.source_root,
                    entry.source_name,
                ),
                subject_label=entry.subject_label,
                session_label=session_label,
                execution_path=execution_path,
                command=_build_convert_command(
                    launcher=context.heudiconv.launcher,
                    execution_path=execution_path,
                    raw_bids_root=context.paths.raw_bids_root,
                    heuristic_path=heuristic_path,
                    subject_label=entry.subject_label,
                    session_label=session_label,
                ),
            )
        )

    return ConvertPlan(
        manifest_path=manifest_path,
        manifest_state_path=context.paths.state_root / "heudiconv" / "manifest.json",
        launcher=context.heudiconv.launcher,
        heuristic_path=heuristic_path,
        raw_bids_root=context.paths.raw_bids_root,
        execution_view_root=execution_view_root,
        run_root=run_root,
        state_path=state_path,
        log_path=log_path,
        entries=entries,
        units=tuple(units),
    )


def run_convert(context: ProjectContext, plan: ConvertPlan) -> ConvertResult:
    _prepare_convert_directories(plan)
    plan.log_path.write_text("", encoding="utf-8", newline="\n")

    unit_results: list[ConvertUnitResult] = []
    try:
        for unit in plan.units:
            execution_view_kind = _materialize_convert_execution_view(
                unit.execution_path,
                unit.source_path,
            )
            completed = _run_convert_command(
                context,
                plan.log_path,
                unit.command,
                label=unit.source_name,
            )
            if completed.returncode != 0:
                raise HeudiconvConvertError(
                    "HeuDiConv conversion failed while processing "
                    f"{unit.source_name}. See {plan.log_path} for details."
                )

            unit_results.append(
                ConvertUnitResult(
                    index=unit.index,
                    source_name=unit.source_name,
                    source_path=unit.source_path,
                    subject_label=unit.subject_label,
                    session_label=unit.session_label,
                    execution_path=unit.execution_path,
                    command=unit.command,
                    execution_view_kind=execution_view_kind,
                )
            )
    except HeudiconvConvertError as exc:
        _write_convert_record(
            context=context,
            plan=plan,
            status="failed",
            unit_results=tuple(unit_results),
            execution_view_cleaned=_cleanup_convert_execution_view(
                context.project_root,
                plan.execution_view_root,
            ),
            error=str(exc),
        )
        raise

    execution_view_cleaned = _cleanup_convert_execution_view(
        context.project_root,
        plan.execution_view_root,
    )
    _write_convert_record(
        context=context,
        plan=plan,
        status="succeeded",
        unit_results=tuple(unit_results),
        execution_view_cleaned=execution_view_cleaned,
    )

    return ConvertResult(
        raw_bids_root=plan.raw_bids_root,
        state_path=plan.state_path,
        log_path=plan.log_path,
        unit_results=tuple(unit_results),
    )


def format_command(argv: tuple[str, ...]) -> str:
    return subprocess.list2cmdline(list(argv))


def _compile_manifest_template(template: str) -> re.Pattern[str]:
    pattern_parts: list[str] = ["^"]
    fields: list[str] = []

    for literal_text, field_name, format_spec, conversion in Formatter().parse(template):
        pattern_parts.append(re.escape(literal_text))
        if field_name is None:
            continue
        if format_spec or conversion:
            raise HeudiconvManifestError(
                "HeuDiConv manifest template does not support format specs or conversions."
            )
        if not field_name.isidentifier():
            raise HeudiconvManifestError(
                f"HeuDiConv manifest template field is not a valid identifier: {field_name!r}"
            )
        if field_name not in {"subject", "session"}:
            raise HeudiconvManifestError(
                "HeuDiConv manifest template only supports {subject} and optional {session}."
            )
        fields.append(field_name)
        pattern_parts.append(f"(?P<{field_name}>.+?)")

    if "subject" not in fields:
        raise HeudiconvManifestError(
            "HeuDiConv manifest template must include a {subject} field."
        )

    pattern_parts.append("$")
    try:
        return re.compile("".join(pattern_parts))
    except re.error as exc:
        raise HeudiconvManifestError(f"Invalid HeuDiConv manifest template: {exc}") from exc


def _derive_manifest_labels_from_template(
    template_pattern: re.Pattern[str],
    source_name: str,
) -> tuple[str, str, str]:
    match = template_pattern.fullmatch(source_name)
    if match is None:
        return "", "", "manifest template did not match source_name"

    subject_label = match.groupdict().get("subject", "") or ""
    session_label = match.groupdict().get("session", "") or ""
    return subject_label, session_label, ""


def _derive_manifest_labels_from_command(
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
        raise HeudiconvManifestError(
            f"Failed to start manifest command while processing {source_name}: {exc}"
        ) from exc

    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()
        details = f" {stderr}" if stderr else ""
        raise HeudiconvManifestError(
            f"Manifest command failed for {source_name} with exit code {completed.returncode}.{details}"
        )

    output = (completed.stdout or "").strip()
    if not output:
        raise HeudiconvManifestError(
            f"Manifest command returned empty output for {source_name}."
        )

    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if len(lines) > 2:
        raise HeudiconvManifestError(
            f"Manifest command returned more than two non-empty output lines for {source_name}."
        )

    subject_label = lines[0]
    session_label = lines[1] if len(lines) == 2 else ""
    return subject_label, session_label, ""


def _compute_manifest_statuses(
    source_root: Path,
    entries: tuple[ManifestEntry, ...],
) -> tuple[ManifestEntry, ...]:
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

    updated_entries: list[ManifestEntry] = []
    for entry in entries:
        try:
            source_path = _resolve_manifest_source_path(source_root, entry.source_name)
        except HeudiconvConvertError:
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
            ManifestEntry(
                source_name=entry.source_name,
                subject_label=subject_label,
                session_label=session_label,
                include=entry.include,
                status=status,
                notes=entry.notes,
            )
        )

    return tuple(updated_entries)


def _load_confirmed_manifest(
    source_root: Path,
    manifest_path: Path,
) -> tuple[ManifestEntry, ...]:
    with manifest_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None:
            raise HeudiconvConvertError(
                f"HeuDiConv manifest is missing a header row: {manifest_path}"
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
            raise HeudiconvConvertError(
                "HeuDiConv manifest is missing required columns: "
                + ", ".join(missing_columns)
            )

        entries: list[ManifestEntry] = []
        for row_number, row in enumerate(reader, start=2):
            source_name = (row.get("source_name") or "").strip()
            if not source_name:
                raise HeudiconvConvertError(
                    f"HeuDiConv manifest row {row_number} is missing source_name."
                )

            include = _parse_manifest_include(row.get("include"), row_number)
            entries.append(
                ManifestEntry(
                    source_name=source_name,
                    subject_label=(row.get("subject_label") or "").strip(),
                    session_label=(row.get("session_label") or "").strip(),
                    include=include,
                    status="",
                    notes=(row.get("notes") or "").strip(),
                )
            )

    return _compute_manifest_statuses(source_root.resolve(), tuple(entries))


def _parse_manifest_include(value: str | None, row_number: int) -> bool:
    normalized = (value or "").strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise HeudiconvConvertError(
        f"HeuDiConv manifest row {row_number} has invalid include value: {value!r}"
    )


def _resolve_manifest_source_path(source_root: Path, source_name: str) -> Path:
    source_candidate = Path(source_name)
    if source_candidate.name != source_name or source_name in {"", ".", ".."}:
        raise HeudiconvConvertError(
            f"HeuDiConv manifest source_name must name an immediate child directory under source_root: {source_name!r}"
        )
    resolved_source_path = (source_root / source_candidate).resolve()
    if not resolved_source_path.is_relative_to(source_root.resolve()):
        raise HeudiconvConvertError(
            f"HeuDiConv manifest source_name resolves outside source_root: {source_name!r}"
        )
    return resolved_source_path


def _build_convert_execution_path(
    execution_view_root: Path,
    subject_label: str,
    session_label: str | None,
) -> Path:
    if session_label is None:
        return execution_view_root / f"sub-{subject_label}"
    return execution_view_root / f"sub-{subject_label}" / f"ses-{session_label}"


def _build_convert_command(
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


def _prepare_convert_directories(plan: ConvertPlan) -> None:
    plan.run_root.mkdir(parents=True, exist_ok=True)
    plan.execution_view_root.mkdir(parents=True, exist_ok=True)
    plan.raw_bids_root.mkdir(parents=True, exist_ok=True)
    plan.state_path.parent.mkdir(parents=True, exist_ok=True)
    plan.log_path.parent.mkdir(parents=True, exist_ok=True)


def _materialize_convert_execution_view(execution_path: Path, source_path: Path) -> str:
    execution_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        execution_path.symlink_to(source_path, target_is_directory=True)
        return "symlink"
    except OSError:
        if os.name != "nt":
            raise HeudiconvConvertError(
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
        raise HeudiconvConvertError(
            f"Failed to create temporary execution link: {execution_path} -> {source_path}.{suffix}"
        )
    return "junction"


def _run_convert_command(
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
        raise HeudiconvConvertError(
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


def _cleanup_convert_execution_view(project_root: Path, execution_view_root: Path) -> bool:
    if not execution_view_root.exists():
        return True
    try:
        _remove_project_path(project_root, execution_view_root)
    except ValueError:
        return False
    return not execution_view_root.exists()


def _write_convert_record(
    *,
    context: ProjectContext,
    plan: ConvertPlan,
    status: str,
    unit_results: tuple[ConvertUnitResult, ...],
    execution_view_cleaned: bool,
    error: str | None = None,
) -> None:
    payload = {
        "step": "convert",
        "status": status,
        "recorded_at": datetime.now(UTC).isoformat(),
        "config_path": str(context.config_path),
        "project_root": str(context.project_root),
        "manifest_path": str(plan.manifest_path),
        "manifest_state_path": str(plan.manifest_state_path),
        "launcher": list(plan.launcher),
        "heuristic_path": str(plan.heuristic_path),
        "raw_bids_root": str(plan.raw_bids_root),
        "execution_view_root": str(plan.execution_view_root),
        "execution_view_cleaned": execution_view_cleaned,
        "artifacts": {
            "raw_bids_dataset": str(plan.raw_bids_root),
        },
        "log_path": str(plan.log_path),
        "status_summary": _summarize_manifest_entries(plan.entries),
        "units": [
            {
                "index": result.index,
                "source_name": result.source_name,
                "source_path": str(result.source_path),
                "subject_label": result.subject_label,
                "session_label": result.session_label,
                "execution_path": str(result.execution_path),
                "execution_view_kind": result.execution_view_kind,
                "command": list(result.command),
            }
            for result in unit_results
        ],
    }
    if error is not None:
        payload["error"] = error

    plan.state_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _build_skeleton_command(
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


def _guard_manifest_reset_requirement(plan: ManifestPlan, reset: bool) -> None:
    if reset:
        return
    if plan.manifest_path.exists() or plan.manifest_state_path.exists():
        raise HeudiconvManifestError(
            "Existing HeuDiConv manifest state was found. Use --reset to regenerate it."
        )


def _prepare_manifest_directories(plan: ManifestPlan) -> None:
    plan.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    plan.manifest_state_path.parent.mkdir(parents=True, exist_ok=True)


def _reset_manifest_state(project_root: Path, plan: ManifestPlan) -> None:
    try:
        for path in (plan.manifest_path, plan.manifest_state_path):
            _remove_project_path(project_root, path)
    except ValueError as exc:
        raise HeudiconvManifestError(str(exc)) from exc


def _guard_skeleton_reset_requirement(plan: SkeletonPlan, reset: bool) -> None:
    if reset:
        return
    if plan.skeleton_state_path.exists() or plan.heudiconv_state_path.exists():
        raise HeudiconvSkeletonError(
            "Existing HeuDiConv skeleton state was found. Use --reset to regenerate it."
        )


def _prepare_skeleton_directories(plan: SkeletonPlan) -> None:
    plan.skeleton_work_root.mkdir(parents=True, exist_ok=True)
    plan.code_root.mkdir(parents=True, exist_ok=True)
    plan.heuristic_path.parent.mkdir(parents=True, exist_ok=True)
    plan.dicominfo_root.mkdir(parents=True, exist_ok=True)
    plan.skeleton_state_path.parent.mkdir(parents=True, exist_ok=True)
    plan.log_path.parent.mkdir(parents=True, exist_ok=True)


def _reset_skeleton_state(project_root: Path, plan: SkeletonPlan) -> None:
    try:
        for path in (
            plan.skeleton_work_root,
            plan.heudiconv_state_path,
            plan.heuristic_path,
            plan.dicominfo_root,
            plan.skeleton_state_path,
            plan.log_path,
        ):
            _remove_project_path(project_root, path)
    except ValueError as exc:
        raise HeudiconvSkeletonError(str(exc)) from exc


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


def _run_skeleton_unit(
    context: ProjectContext,
    plan: SkeletonPlan,
    unit: SkeletonUnitPlan,
) -> SkeletonUnitResult:
    completed, _, started_at_ns = _run_command(
        context,
        plan.log_path,
        unit.initial_command,
        label=unit.unit_name,
    )
    if completed.returncode != 0:
        if unit.session_label is None:
            raise HeudiconvSkeletonError(
                "HeuDiConv skeleton generation failed for the provided sample path. "
                "BIDSFlow already used a temporary subject id for this skeleton run; "
                "the directory may not be a clean single-subject, single-session input. "
                f"See {plan.log_path} for details."
            )
        raise HeudiconvSkeletonError(
            "HeuDiConv skeleton generation failed while processing a representative session directory. "
            "BIDSFlow treats multiple input directories as separate single-directory skeleton units; "
            "check whether this directory mixes scans from multiple sessions or incompatible content. "
            f"See {plan.log_path} for details."
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
        raise HeudiconvSkeletonError(
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
    with log_path.open("a", encoding="utf-8", newline="\n") as log_handle:
        if log_handle.tell() > 0:
            log_handle.write("\n")
        log_handle.write(message.rstrip())
        log_handle.write("\n")


def _collect_unit_result(
    *,
    plan: SkeletonPlan,
    unit: SkeletonUnitPlan,
    final_command: tuple[str, ...],
    attempted_commands: tuple[tuple[str, ...], ...],
    subject_label: str | None,
    session_label: str | None,
    strategy: str,
    started_at_ns: int,
) -> SkeletonUnitResult:
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

    return SkeletonUnitResult(
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
    )


def _find_latest_generated_file_since(root: Path, filename: str, started_at_ns: int) -> Path:
    candidates = [
        candidate
        for candidate in root.rglob(filename)
        if candidate.is_file() and candidate.stat().st_mtime_ns >= started_at_ns
    ]
    if not candidates:
        raise HeudiconvSkeletonError(
            f"HeuDiConv skeleton generation did not produce {filename} under {root}."
        )
    return max(candidates, key=lambda candidate: candidate.stat().st_mtime_ns)


def _find_generated_dicominfo_files_since(root: Path, started_at_ns: int) -> tuple[Path, ...]:
    candidates = [
        candidate
        for candidate in root.rglob("dicominfo*.tsv")
        if candidate.is_file() and candidate.stat().st_mtime_ns >= started_at_ns
    ]
    if not candidates:
        raise HeudiconvSkeletonError(
            f"HeuDiConv skeleton generation did not produce dicominfo output under {root}."
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
        raise HeudiconvSkeletonError(
            "Generated heuristic skeletons differed across skeleton units. "
            "Review the sample directories and rerun skeleton with a narrower input set."
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


def _write_manifest_tsv(manifest_path: Path, entries: tuple[ManifestEntry, ...]) -> None:
    with manifest_path.open("w", encoding="utf-8", newline="") as handle:
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


def _write_manifest_state(context: ProjectContext, plan: ManifestPlan) -> None:
    payload = {
        "step": "manifest",
        "status": "succeeded",
        "recorded_at": datetime.now(UTC).isoformat(),
        "config_path": str(context.config_path),
        "project_root": str(context.project_root),
        "source_root": str(plan.source_root),
        "artifacts": {
            "manifest": str(plan.manifest_path),
        },
        "handoff": {
            "role": "truth_source",
            "derived_execution_views": {
                "links": {
                    "managed_by": "convert",
                    "lifecycle": "ephemeral",
                }
            },
        },
        "label_generation": {
            "template": plan.template,
            "command": list(plan.command) if plan.command is not None else None,
        },
        "entry_count": len(plan.entries),
        "status_summary": _summarize_manifest_entries(plan.entries),
    }

    plan.manifest_state_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def summarize_manifest_entries(entries: tuple[ManifestEntry, ...]) -> dict[str, int]:
    return _summarize_manifest_entries(entries)


def list_manifest_review_issues(entries: tuple[ManifestEntry, ...]) -> list[str]:
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


def _summarize_manifest_entries(entries: tuple[ManifestEntry, ...]) -> dict[str, int]:
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


def _write_skeleton_record(
    *,
    context: ProjectContext,
    plan: SkeletonPlan,
    status: str,
    unit_results: tuple[SkeletonUnitResult, ...],
    copied_dicominfo_paths: tuple[Path, ...],
    error: str | None = None,
) -> None:
    plan.skeleton_state_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "step": "skeleton",
        "status": status,
        "recorded_at": datetime.now(UTC).isoformat(),
        "config_path": str(context.config_path),
        "project_root": str(context.project_root),
        "sample_paths": [str(path) for path in plan.sample_paths],
        "launcher": list(plan.launcher),
        "artifacts": {
            "heuristic_template": str(plan.heuristic_path),
            "dicom_inventory_dir": str(plan.dicominfo_root),
            "dicom_inventories": [str(path) for path in copied_dicominfo_paths],
            "skeleton_work_root": str(plan.skeleton_work_root),
            "heudiconv_state": str(plan.heudiconv_state_path),
        },
        "log_path": str(plan.log_path),
        "units": [
            {
                "index": result.index,
                "sample_path": str(result.sample_path),
                "unit_name": result.unit_name,
                "subject_label": result.subject_label,
                "session_label": result.session_label,
                "strategy": result.strategy,
                "attempted_commands": [list(command) for command in result.attempted_commands],
                "generated_heuristic": str(result.generated_heuristic),
                "generated_dicominfo": [str(path) for path in result.generated_dicominfo_paths],
                "copied_dicominfo": [str(path) for path in result.copied_dicominfo_paths],
            }
            for result in unit_results
        ],
    }

    if error is not None:
        payload["error"] = error

    plan.skeleton_state_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

