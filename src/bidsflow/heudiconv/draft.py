"""Draft heuristic generation for representative HeuDiConv source samples."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import time

from ..common import (
    _append_log,
    _combine_process_output,
    _format_attempt_label,
    _remove_project_path,
    _utc_now,
    _write_json,
    format_command,
)
from ..project import ProjectContext


@dataclass(frozen=True)
class DraftUnitPlan:
    """One representative sample directory used during heuristic drafting."""

    index: int
    sample_path: Path
    unit_name: str
    initial_command: tuple[str, ...]
    subject_label: str | None
    session_label: str | None
    log_path: Path


@dataclass(frozen=True)
class DraftPlan:
    """Planned draft heuristic generation work for one or more samples."""

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
    """Artifacts produced by one draft unit."""

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
    """Combined draft heuristic and DICOM inventory outputs."""

    heuristic_path: Path
    dicominfo_root: Path
    dicominfo_paths: tuple[Path, ...]
    draft_state_path: Path
    log_dir: Path
    unit_results: tuple[DraftUnitResult, ...]


class HeudiconvDraftError(Exception):
    """Raised when draft heuristic generation cannot complete."""

    pass


def plan_draft(context: ProjectContext, sample_paths: list[Path]) -> DraftPlan:
    """Plan isolated HeuDiConv draft generation for representative samples."""

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
    """Resolve a draft sample path while keeping it inside the source tree."""

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
    """Run the draft plan and merge generated heuristic artifacts."""

    _guard_draft_reset_requirement(plan, reset)

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
    """Write draft metadata that points to generated heuristic and DICOM info."""

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


def _build_draft_command(
    launcher: tuple[str, ...],
    sample_path: Path,
    output_root: Path,
    *,
    subject_label: str | None = None,
    session_label: str | None = None,
) -> tuple[str, ...]:
    """Build the HeuDiConv command used for isolated heuristic drafting."""

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


def _guard_draft_reset_requirement(plan: DraftPlan, reset: bool) -> None:
    """Prevent accidental replacement of existing draft artifacts."""

    if reset:
        return
    if plan.draft_state_path.exists() or plan.heudiconv_state_path.exists():
        raise HeudiconvDraftError(
            "Existing HeuDiConv draft state was found. Use --force to regenerate it."
        )


def _prepare_draft_directories(plan: DraftPlan) -> None:
    """Create directories needed before running draft units."""

    plan.draft_work_root.mkdir(parents=True, exist_ok=True)
    plan.code_root.mkdir(parents=True, exist_ok=True)
    plan.heuristic_path.parent.mkdir(parents=True, exist_ok=True)
    plan.dicominfo_root.mkdir(parents=True, exist_ok=True)
    plan.draft_state_path.parent.mkdir(parents=True, exist_ok=True)
    plan.log_dir.mkdir(parents=True, exist_ok=True)


def _reset_draft_state(project_root: Path, plan: DraftPlan) -> None:
    """Remove prior draft artifacts during forced regeneration."""

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


def _run_draft_unit(
    context: ProjectContext,
    plan: DraftPlan,
    unit: DraftUnitPlan,
) -> DraftUnitResult:
    """Run one draft unit and collect its generated files."""

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
    """Run a draft command and append combined output to the unit log."""

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


def _collect_unit_result(
    *,
    plan: DraftPlan,
    unit: DraftUnitPlan,
    attempted_commands: tuple[tuple[str, ...], ...],
    subject_label: str | None,
    session_label: str | None,
    strategy: str,
    started_at_ns: int,
) -> DraftUnitResult:
    """Collect generated draft files after a successful draft command."""

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
    """Return the newest generated file with the requested name."""

    candidates: list[tuple[Path, int]] = []
    for candidate in root.rglob(filename):
        if not candidate.is_file():  # pragma: no cover
            continue
        mtime = candidate.stat().st_mtime_ns
        if mtime >= started_at_ns:
            candidates.append((candidate, mtime))
    if not candidates:
        raise HeudiconvDraftError(
            f"HeuDiConv draft generation did not produce {filename} under {root}."
        )
    return max(candidates, key=lambda item: item[1])[0]


def _find_generated_dicominfo_files_since(root: Path, started_at_ns: int) -> tuple[Path, ...]:
    """Return generated DICOM inventory files for the current draft unit."""

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
    """Copy or validate the generated heuristic across draft units."""

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
    """Copy generated DICOM inventory files into the project code area."""

    common_parent = Path(os.path.commonpath([str(path.parent) for path in generated_paths]))
    copied_paths: list[Path] = []

    for generated_path in generated_paths:
        relative_path = generated_path.relative_to(common_parent)
        destination = destination_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(generated_path, destination)
        copied_paths.append(destination)

    return tuple(copied_paths)
