"""Participant discovery and review-table handling for fMRIPrep."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
import re

from .errors import FmriprepInitError, FmriprepRunError


PARTICIPANT_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class ParticipantEntry:
    """One row in the editable fMRIPrep participants.tsv table."""

    participant_label: str
    include: bool
    status: str
    notes: str


@dataclass(frozen=True)
class ParticipantsPlan:
    """Planned participant discovery output for fmriprep init."""

    raw_bids_root: Path
    participants_path: Path
    entries: tuple[ParticipantEntry, ...]


def plan_participants(raw_bids_root: Path, participants_path: Path) -> ParticipantsPlan:
    """Build a participants.tsv plan from immediate sub-* raw-BIDS directories."""

    resolved_raw_bids_root = raw_bids_root.resolve()
    if not resolved_raw_bids_root.exists():
        raise FmriprepInitError(
            f"Configured raw BIDS root does not exist: {resolved_raw_bids_root}"
        )
    if not resolved_raw_bids_root.is_dir():
        raise FmriprepInitError(
            f"Configured raw BIDS root is not a directory: {resolved_raw_bids_root}"
        )

    entries = tuple(
        ParticipantEntry(
            participant_label=_validate_participant_label(
                candidate.name.removeprefix("sub-"),
                row_context=candidate.name,
                error_cls=FmriprepInitError,
            ),
            include=True,
            status="ready",
            notes="",
        )
        for candidate in sorted(
            resolved_raw_bids_root.iterdir(),
            key=lambda path: path.name.lower(),
        )
        if candidate.is_dir() and candidate.name.startswith("sub-")
    )

    return ParticipantsPlan(
        raw_bids_root=resolved_raw_bids_root,
        participants_path=participants_path,
        entries=entries,
    )


def write_participants_tsv(
    participants_path: Path,
    entries: tuple[ParticipantEntry, ...],
) -> None:
    """Write the editable participant review table."""

    participants_path.parent.mkdir(parents=True, exist_ok=True)
    with participants_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(("participant_label", "include", "status", "notes"))
        for entry in entries:
            writer.writerow(
                (
                    entry.participant_label,
                    "true" if entry.include else "false",
                    entry.status,
                    entry.notes,
                )
            )


def load_participants(
    raw_bids_root: Path,
    participants_path: Path,
) -> tuple[ParticipantEntry, ...]:
    """Load and validate the user-reviewed participants.tsv table."""

    with participants_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None:
            raise FmriprepRunError(
                f"BIDSFlow fMRIPrep participants table is missing a header row: {participants_path}"
            )

        required_columns = {"participant_label", "include", "status", "notes"}
        missing_columns = sorted(required_columns.difference(reader.fieldnames))
        if missing_columns:
            raise FmriprepRunError(
                "BIDSFlow fMRIPrep participants table is missing required columns: "
                + ", ".join(missing_columns)
            )

        entries: list[ParticipantEntry] = []
        for row_number, row in enumerate(reader, start=2):
            participant_label = _validate_participant_label(
                row.get("participant_label") or "",
                row_context=f"row {row_number}",
                error_cls=FmriprepRunError,
            )
            include = _parse_include(row.get("include"), row_number)
            status = (row.get("status") or "").strip()
            if include and status not in {"ready", "needs_review"}:
                raise FmriprepRunError(
                    "BIDSFlow fMRIPrep participants table row "
                    f"{row_number} has unsupported status: {status!r}."
                )
            if not include:
                status = "excluded"
            entries.append(
                ParticipantEntry(
                    participant_label=participant_label,
                    include=include,
                    status=status,
                    notes=(row.get("notes") or "").strip(),
                )
            )

    return _compute_participant_statuses(raw_bids_root.resolve(), tuple(entries))


def summarize_participants(entries: tuple[ParticipantEntry, ...]) -> dict[str, int]:
    """Return counts by participant review status."""

    summary = {
        "total": len(entries),
        "ready": 0,
        "needs_review": 0,
        "missing_raw": 0,
        "excluded": 0,
    }
    for entry in entries:
        summary[entry.status] = summary.get(entry.status, 0) + 1
    return summary


def list_participants_review_issues(entries: tuple[ParticipantEntry, ...]) -> list[str]:
    """Return human-readable review issues for non-ready participant rows."""

    issues: list[str] = []
    needs_review = [entry.participant_label for entry in entries if entry.status == "needs_review"]
    if needs_review:
        issues.append(
            f"needs_review: {len(needs_review)} row(s) still need participant review"
        )
    missing_raw = [entry.participant_label for entry in entries if entry.status == "missing_raw"]
    if missing_raw:
        issues.append("missing_raw: " + ", ".join(f"sub-{label}" for label in missing_raw))
    return issues


def _compute_participant_statuses(
    raw_bids_root: Path,
    entries: tuple[ParticipantEntry, ...],
) -> tuple[ParticipantEntry, ...]:
    """Refresh statuses that depend on the current raw BIDS filesystem."""

    refreshed: list[ParticipantEntry] = []
    for entry in entries:
        status = entry.status
        if not entry.include:
            status = "excluded"
        elif not (raw_bids_root / f"sub-{entry.participant_label}").is_dir():
            status = "missing_raw"
        elif status not in {"ready", "needs_review"}:
            status = "needs_review"
        refreshed.append(
            ParticipantEntry(
                participant_label=entry.participant_label,
                include=entry.include,
                status=status,
                notes=entry.notes,
            )
        )
    return tuple(refreshed)


def _validate_participant_label(
    value: str,
    *,
    row_context: str,
    error_cls: type[Exception],
) -> str:
    """Validate one BIDS participant label without the sub- prefix."""

    normalized = value.strip().removeprefix("sub-")
    if not normalized:
        raise error_cls(f"Missing participant_label in {row_context}.")
    if not PARTICIPANT_LABEL_PATTERN.fullmatch(normalized):
        raise error_cls(
            f"Invalid participant_label {value!r} in {row_context}. "
            "Use the BIDS label without path separators or the sub- prefix."
        )
    return normalized


def _parse_include(value: str | None, row_number: int) -> bool:
    """Parse the editable include column from participants.tsv."""

    normalized = (value or "").strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise FmriprepRunError(
        f"BIDSFlow fMRIPrep participants table row {row_number} has invalid include value: {value!r}"
    )
