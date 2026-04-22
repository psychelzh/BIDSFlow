"""Read-only status summaries for the managed HeuDiConv workflow."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import json
from pathlib import Path

from ..project import ProjectContext
from .run import _build_run_unit_name, _read_key_value_status
from .sources import (
    SourcesEntry,
    _load_confirmed_sources,
    list_sources_review_issues,
    summarize_sources_entries,
)


@dataclass(frozen=True)
class HeudiconvUnitStatus:
    """Current status view for one ready HeuDiConv unit."""

    unit_name: str
    source_name: str
    subject_label: str
    session_label: str | None
    status: str
    active_claim: bool
    status_path: Path
    claim_path: Path
    log_path: Path | None
    log_dir: Path | None
    error: str


@dataclass(frozen=True)
class HeudiconvStatus:
    """Project-level HeuDiConv status assembled from state files."""

    sources_path: Path
    sources_exists: bool
    sources_summary: dict[str, int]
    sources_issues: tuple[str, ...]
    heuristic_path: Path
    heuristic_exists: bool
    run_state_path: Path
    run_record_state: str | None
    results_path: Path
    results_count: int
    unit_status_dir: Path
    claim_dir: Path
    units: tuple[HeudiconvUnitStatus, ...]
    unit_counts: dict[str, int]


def get_heudiconv_status(context: ProjectContext) -> HeudiconvStatus:
    """Read HeuDiConv sources, run state, results, unit status, and claims."""

    sources_path = context.paths.state_root / "sources.tsv"
    state_root = context.paths.state_root / "heudiconv"
    run_state_path = state_root / "run.json"
    results_path = state_root / "results.tsv"
    unit_status_dir = state_root / "units"
    claim_dir = state_root / "claims"

    entries: tuple[SourcesEntry, ...] = ()
    if sources_path.exists():
        entries = _load_confirmed_sources(context.paths.source_root, sources_path)

    units = tuple(
        _build_unit_status(
            entry,
            unit_status_dir=unit_status_dir,
            claim_dir=claim_dir,
        )
        for entry in entries
        if entry.include and entry.status == "ready"
    )

    return HeudiconvStatus(
        sources_path=sources_path,
        sources_exists=sources_path.exists(),
        sources_summary=summarize_sources_entries(entries),
        sources_issues=tuple(list_sources_review_issues(entries)),
        heuristic_path=context.heudiconv.heuristic,
        heuristic_exists=context.heudiconv.heuristic.is_file(),
        run_state_path=run_state_path,
        run_record_state=_read_run_record_state(run_state_path),
        results_path=results_path,
        results_count=_count_results_rows(results_path),
        unit_status_dir=unit_status_dir,
        claim_dir=claim_dir,
        units=units,
        unit_counts=_summarize_unit_statuses(units),
    )


def _build_unit_status(
    entry: SourcesEntry,
    *,
    unit_status_dir: Path,
    claim_dir: Path,
) -> HeudiconvUnitStatus:
    session_label = entry.session_label or None
    unit_name = _build_run_unit_name(entry.subject_label, session_label)
    status_path = unit_status_dir / f"{unit_name}.status"
    claim_path = claim_dir / f"{unit_name}.running"
    payload = _read_existing_key_value_status(status_path)

    return HeudiconvUnitStatus(
        unit_name=unit_name,
        source_name=entry.source_name,
        subject_label=entry.subject_label,
        session_label=session_label,
        status=payload.get("status", "not_run"),
        active_claim=claim_path.exists(),
        status_path=status_path,
        claim_path=claim_path,
        log_path=_optional_path(payload.get("log")),
        log_dir=_optional_path(payload.get("log_dir")),
        error=payload.get("error", ""),
    )


def _read_existing_key_value_status(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        return _read_key_value_status(path)
    except OSError:  # pragma: no cover
        return {}


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
    units: tuple[HeudiconvUnitStatus, ...],
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
        if unit.active_claim or unit.status in {"running", "submitted"}:
            summary["active"] += 1
        elif unit.status in {"not_run", "succeeded", "failed"}:
            summary[unit.status] += 1
        else:  # pragma: no cover
            summary["other"] += 1
    return summary
