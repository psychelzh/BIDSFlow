"""Read-only status summaries for the managed fMRIPrep workflow."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import json
from pathlib import Path

from ..project import ProjectContext, target_config_path
from ..run_state import (
    build_subject_session_unit_name,
    read_existing_key_value_status,
)
from .errors import FmriprepRunError
from .participants import (
    ParticipantEntry,
    list_participants_review_issues,
    load_participants,
    summarize_participants,
)


@dataclass(frozen=True)
class FmriprepUnitStatus:
    """Current status view for one ready fMRIPrep participant."""

    unit_name: str
    participant_label: str
    status: str
    cleanup_status: str
    active_run_claim: bool
    active_cleanup_claim: bool
    status_path: Path
    run_claim_path: Path
    cleanup_claim_path: Path
    work_dir: Path
    log_path: Path | None
    error: str
    warning: str


@dataclass(frozen=True)
class FmriprepStatus:
    """Project-level fMRIPrep status assembled from state files."""

    participants_path: Path
    participants_exists: bool
    participants_summary: dict[str, int]
    participants_issues: tuple[str, ...]
    config_path: Path
    config_exists: bool
    run_state_path: Path
    run_record_state: str | None
    results_path: Path
    results_count: int
    unit_status_dir: Path
    run_claim_dir: Path
    cleanup_claim_dir: Path
    units: tuple[FmriprepUnitStatus, ...]
    unit_counts: dict[str, int]
    cleanup_counts: dict[str, int]


def get_fmriprep_status(context: ProjectContext) -> FmriprepStatus:
    """Read fMRIPrep participants, run state, results, unit status, and claims."""

    state_root = context.paths.state_root / "fmriprep"
    participants_path = state_root / "participants.tsv"
    config_path = target_config_path(context, "fmriprep")
    run_state_path = state_root / "run.json"
    results_path = state_root / "results.tsv"
    unit_status_dir = state_root / "units"
    run_claim_dir = state_root / "claims" / "run"
    cleanup_claim_dir = state_root / "claims" / "cleanup"

    entries: tuple[ParticipantEntry, ...] = ()
    participants_issues: list[str] = []
    if participants_path.exists():
        try:
            entries = load_participants(context.paths.raw_bids_root, participants_path)
        except (FmriprepRunError, OSError, ValueError, csv.Error) as exc:
            participants_issues.append(f"Unreadable participants table: {exc}")

    units = tuple(
        _build_unit_status(
            entry,
            unit_status_dir=unit_status_dir,
            run_claim_dir=run_claim_dir,
            cleanup_claim_dir=cleanup_claim_dir,
            default_work_root=context.paths.work_root / "fmriprep",
        )
        for entry in entries
        if entry.include and entry.status == "ready"
    )

    return FmriprepStatus(
        participants_path=participants_path,
        participants_exists=participants_path.exists(),
        participants_summary=summarize_participants(entries),
        participants_issues=tuple(participants_issues + list_participants_review_issues(entries)),
        config_path=config_path,
        config_exists=config_path.is_file(),
        run_state_path=run_state_path,
        run_record_state=_read_run_record_state(run_state_path),
        results_path=results_path,
        results_count=_count_results_rows(results_path),
        unit_status_dir=unit_status_dir,
        run_claim_dir=run_claim_dir,
        cleanup_claim_dir=cleanup_claim_dir,
        units=units,
        unit_counts=_summarize_unit_statuses(units),
        cleanup_counts=_summarize_cleanup_statuses(units),
    )


def _build_unit_status(
    entry: ParticipantEntry,
    *,
    unit_status_dir: Path,
    run_claim_dir: Path,
    cleanup_claim_dir: Path,
    default_work_root: Path,
) -> FmriprepUnitStatus:
    unit_name = build_subject_session_unit_name(entry.participant_label, None)
    status_path = unit_status_dir / f"{unit_name}.status"
    run_claim_path = run_claim_dir / f"{unit_name}.running"
    cleanup_claim_path = cleanup_claim_dir / f"{unit_name}.running"
    payload = read_existing_key_value_status(status_path)
    work_dir = Path(payload.get("work_dir") or default_work_root / unit_name)

    return FmriprepUnitStatus(
        unit_name=unit_name,
        participant_label=entry.participant_label,
        status=payload.get("status", "not_run"),
        cleanup_status=payload.get("cleanup_status", "not_needed"),
        active_run_claim=run_claim_path.exists(),
        active_cleanup_claim=cleanup_claim_path.exists(),
        status_path=status_path,
        run_claim_path=run_claim_path,
        cleanup_claim_path=cleanup_claim_path,
        work_dir=work_dir,
        log_path=_optional_path(payload.get("log")),
        error=payload.get("error", ""),
        warning=payload.get("warning", ""),
    )


def _optional_path(value: str | None) -> Path | None:
    if not value:
        return None
    return Path(value)


def _read_run_record_state(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):  # pragma: no cover
        return "unreadable"
    record_state = payload.get("record_state")
    return record_state if isinstance(record_state, str) else None


def _count_results_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return sum(1 for _ in csv.DictReader(handle, delimiter="\t"))
    except OSError:  # pragma: no cover
        return 0


def _summarize_unit_statuses(
    units: tuple[FmriprepUnitStatus, ...],
) -> dict[str, int]:
    summary = {
        "total": len(units),
        "not_run": 0,
        "succeeded": 0,
        "failed": 0,
        "active": 0,
        "other": 0,
    }
    for unit in units:
        if unit.active_run_claim or unit.status in {"running", "submitted"}:
            summary["active"] += 1
        elif unit.status in {"not_run", "succeeded", "failed"}:
            summary[unit.status] += 1
        else:  # pragma: no cover
            summary["other"] += 1
    return summary


def _summarize_cleanup_statuses(
    units: tuple[FmriprepUnitStatus, ...],
) -> dict[str, int]:
    summary = {
        "pending": 0,
        "succeeded": 0,
        "failed": 0,
        "active": 0,
        "not_needed": 0,
    }
    for unit in units:
        if unit.active_cleanup_claim or unit.cleanup_status in {"running", "submitted"}:
            summary["active"] += 1
        elif unit.cleanup_status in summary:
            summary[unit.cleanup_status] += 1
        else:
            summary["not_needed"] += 1
    return summary
