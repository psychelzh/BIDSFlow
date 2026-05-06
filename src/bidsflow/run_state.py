"""Shared unit naming, status, and claim helpers for managed runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Generic, Protocol, TypeVar


class ClaimableUnit(Protocol):
    """Protocol for planned units that can be claimed before execution."""

    unit_name: str
    status_path: Path
    claim_path: Path


UnitT = TypeVar("UnitT", bound=ClaimableUnit)


@dataclass(frozen=True)
class UnitSelection(Generic[UnitT]):
    """Runnable units plus skip counts for the current attempt."""

    runnable_units: tuple[UnitT, ...]
    skipped_succeeded: int
    skipped_failed: int
    skipped_active_claim: int


def build_subject_session_unit_name(subject_label: str, session_label: str | None) -> str:
    """Build a stable unit name from BIDS subject/session labels."""

    if session_label is None:
        return f"sub-{subject_label}"
    return f"sub-{subject_label}_ses-{session_label}"


def read_key_value_status(path: Path) -> dict[str, str]:
    """Read a simple key=value status file."""

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def read_existing_key_value_status(path: Path) -> dict[str, str]:
    """Read a key=value status file, returning an empty mapping if absent."""

    if not path.is_file():
        return {}
    try:
        return read_key_value_status(path)
    except OSError:  # pragma: no cover
        return {}


def unit_status_value(path: Path) -> str:
    """Read only the status value from a unit status file."""

    return read_existing_key_value_status(path).get("status", "")


def write_unit_claim(claim_path: Path) -> None:
    """Create an exclusive running claim file."""

    claim_path.parent.mkdir(parents=True, exist_ok=True)
    with claim_path.open("x", encoding="utf-8"):
        pass


def release_unit_claim(claim_path: Path) -> None:
    """Remove a unit claim when the protected operation has ended."""

    try:
        claim_path.unlink()
    except FileNotFoundError:
        return


def release_unfinished_claims(
    units: tuple[UnitT, ...],
    finished_unit_names: set[str],
) -> None:
    """Release claims for units that did not finish cleanly."""

    for unit in units:
        if unit.unit_name not in finished_unit_names:
            release_unit_claim(unit.claim_path)


def claim_runnable_units(
    *,
    units: tuple[UnitT, ...],
    include_failed: bool,
) -> UnitSelection[UnitT]:
    """Claim units that are eligible to run now."""

    runnable_units: list[UnitT] = []
    skipped_succeeded = 0
    skipped_failed = 0
    skipped_active_claim = 0

    try:
        for unit in units:
            unit_status = unit_status_value(unit.status_path)
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
                write_unit_claim(unit.claim_path)
            except FileExistsError:  # pragma: no cover
                skipped_active_claim += 1
                continue
            except OSError:
                release_unfinished_claims(tuple(runnable_units), set())
                raise
            runnable_units.append(unit)
    except OSError:
        release_unfinished_claims(tuple(runnable_units), set())
        raise

    return UnitSelection(
        runnable_units=tuple(runnable_units),
        skipped_succeeded=skipped_succeeded,
        skipped_failed=skipped_failed,
        skipped_active_claim=skipped_active_claim,
    )


def preview_runnable_units(
    units: tuple[UnitT, ...],
    *,
    include_failed: bool,
) -> UnitSelection[UnitT]:
    """Select runnable units without writing claim files."""

    runnable_units: list[UnitT] = []
    skipped_succeeded = 0
    skipped_failed = 0
    skipped_active_claim = 0

    for unit in units:
        unit_status = unit_status_value(unit.status_path)
        if unit_status == "succeeded":
            skipped_succeeded += 1
        elif unit.claim_path.exists():
            skipped_active_claim += 1
        elif unit_status == "failed" and not include_failed:
            skipped_failed += 1
        else:
            runnable_units.append(unit)

    return UnitSelection(
        runnable_units=tuple(runnable_units),
        skipped_succeeded=skipped_succeeded,
        skipped_failed=skipped_failed,
        skipped_active_claim=skipped_active_claim,
    )


def build_unit_counts(
    planned_units: tuple[UnitT, ...],
    selection: UnitSelection[UnitT],
) -> dict[str, int]:
    """Summarize selected and skipped units for CLI output and run state."""

    skipped = (
        selection.skipped_succeeded
        + selection.skipped_failed
        + selection.skipped_active_claim
    )
    return {
        "total": len(planned_units),
        "selected": len(selection.runnable_units),
        "skipped": skipped,
        "skipped_succeeded": selection.skipped_succeeded,
        "skipped_failed": selection.skipped_failed,
        "skipped_active_claim": selection.skipped_active_claim,
    }
