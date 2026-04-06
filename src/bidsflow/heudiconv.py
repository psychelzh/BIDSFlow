from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import csv
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any

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
    unit_id: str
    source_path: Path
    source_name: str
    subject_raw: str
    session_raw: str
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
    subject_regex: str | None
    session_regex: str | None
    entries: tuple[ManifestEntry, ...]


@dataclass(frozen=True)
class ManifestResult:
    manifest_path: Path
    manifest_state_path: Path
    entry_count: int


class HeudiconvManifestError(Exception):
    pass


def plan_skeleton(context: ProjectContext, sample_paths: list[Path]) -> SkeletonPlan:
    if not sample_paths:
        raise HeudiconvSkeletonError("At least one sample path is required for skeleton generation.")

    resolved_samples: list[Path] = []
    for sample_path in sample_paths:
        resolved_sample = sample_path.resolve()
        if not resolved_sample.exists():
            raise HeudiconvSkeletonError(f"Sample path does not exist: {resolved_sample}")
        resolved_samples.append(resolved_sample)

    launcher = _load_launcher(context.config)
    code_root = context.project_root / "code" / "heudiconv"
    state_root = context.state_root / "heudiconv"
    log_root = context.logs_root / "heudiconv"
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
        heuristic_path=code_root / "heuristic.py",
        dicominfo_root=code_root / "dicominfo",
        skeleton_work_root=skeleton_work_root,
        heudiconv_state_path=skeleton_work_root / ".heudiconv",
        skeleton_state_path=state_root / "skeleton.json",
        log_path=log_root / "skeleton.log",
    )


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
    source_root: Path,
    *,
    subject_regex: str | None = None,
    session_regex: str | None = None,
) -> ManifestPlan:
    resolved_source_root = source_root.resolve()
    if not resolved_source_root.exists():
        raise HeudiconvManifestError(f"Source root does not exist: {resolved_source_root}")
    if not resolved_source_root.is_dir():
        raise HeudiconvManifestError(f"Source root is not a directory: {resolved_source_root}")

    subject_pattern = _compile_optional_regex(subject_regex, "subject")
    session_pattern = _compile_optional_regex(session_regex, "session")

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

    entries: list[ManifestEntry] = []
    for index, candidate in enumerate(source_units, start=1):
        subject_raw, subject_note = _extract_optional_value(subject_pattern, candidate.name, "subject")
        session_raw, session_note = _extract_optional_value(session_pattern, candidate.name, "session")
        notes = "; ".join(note for note in (subject_note, session_note) if note)

        entries.append(
            ManifestEntry(
                unit_id=f"unit-{index:04d}",
                source_path=candidate,
                source_name=candidate.name,
                subject_raw=subject_raw,
                session_raw=session_raw,
                subject_label="",
                session_label="",
                include=True,
                status="needs_review",
                notes=notes,
            )
        )

    code_root = context.project_root / "code" / "heudiconv"
    state_root = context.state_root / "heudiconv"

    return ManifestPlan(
        source_root=resolved_source_root,
        manifest_path=code_root / "manifest.tsv",
        manifest_state_path=state_root / "manifest.json",
        subject_regex=subject_regex,
        session_regex=session_regex,
        entries=tuple(entries),
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
        entry_count=len(plan.entries),
    )


def format_command(argv: tuple[str, ...]) -> str:
    return subprocess.list2cmdline(list(argv))


def _compile_optional_regex(value: str | None, field_name: str) -> re.Pattern[str] | None:
    if value is None:
        return None
    try:
        return re.compile(value)
    except re.error as exc:
        raise HeudiconvManifestError(f"Invalid {field_name} regex: {exc}") from exc


def _extract_optional_value(
    pattern: re.Pattern[str] | None,
    source_name: str,
    field_name: str,
) -> tuple[str, str]:
    if pattern is None:
        return "", ""

    match = pattern.search(source_name)
    if match is None:
        return "", f"{field_name} regex did not match"

    if field_name in match.groupdict() and match.group(field_name) is not None:
        return match.group(field_name), ""
    if match.lastindex:
        return match.group(1), ""
    return match.group(0), ""


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


def _load_launcher(config: dict[str, Any]) -> tuple[str, ...]:
    heudiconv_section = config.get("heudiconv", {})
    if heudiconv_section == {}:
        return ("heudiconv",)
    if not isinstance(heudiconv_section, dict):
        raise HeudiconvSkeletonError("[heudiconv] must be a TOML table.")

    launcher = heudiconv_section.get("launcher")
    if launcher is None:
        return ("heudiconv",)

    if not isinstance(launcher, list) or not launcher or not all(isinstance(item, str) for item in launcher):
        raise HeudiconvSkeletonError("[heudiconv].launcher must be a non-empty list of strings.")

    return tuple(launcher)


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
                "unit_id",
                "source_path",
                "source_name",
                "subject_raw",
                "session_raw",
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
                    entry.unit_id,
                    str(entry.source_path),
                    entry.source_name,
                    entry.subject_raw,
                    entry.session_raw,
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
        "extraction": {
            "subject_regex": plan.subject_regex,
            "session_regex": plan.session_regex,
        },
        "entry_count": len(plan.entries),
        "entries": [
            {
                "unit_id": entry.unit_id,
                "source_path": str(entry.source_path),
                "source_name": entry.source_name,
                "subject_raw": entry.subject_raw,
                "session_raw": entry.session_raw,
                "subject_label": entry.subject_label,
                "session_label": entry.session_label,
                "include": entry.include,
                "status": entry.status,
                "notes": entry.notes,
            }
            for entry in plan.entries
        ],
    }

    plan.manifest_state_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


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

