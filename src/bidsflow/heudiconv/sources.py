"""Source discovery and review-table handling for the HeuDiConv workflow."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from string import Formatter
import subprocess

from ..common import _remove_project_path
from ..project import ProjectContext, SourcesConfig, load_heudiconv_config
from .errors import HeudiconvRunError


SOURCES_COMMAND_TIMEOUT_SECONDS = 300.0


@dataclass(frozen=True)
class SourcesEntry:
    """One row in the editable sources.tsv review table."""

    source_name: str
    subject_label: str
    session_label: str
    include: bool
    status: str
    notes: str


@dataclass(frozen=True)
class SourcesPlan:
    """Planned source discovery outputs for heudiconv init."""

    source_root: Path
    sources_path: Path
    sources_state_path: Path
    pattern: str | None
    command: tuple[str, ...] | None
    entries: tuple[SourcesEntry, ...]


@dataclass(frozen=True)
class SourcesResult:
    """Files and entries produced by source discovery."""

    sources_path: Path
    sources_state_path: Path
    entries: tuple[SourcesEntry, ...]


class SourcesError(Exception):
    """Raised when source discovery or review-table refresh fails."""

    pass


SAFE_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def plan_sources(
    context: ProjectContext,
    sources_config: SourcesConfig | None = None,
) -> SourcesPlan:
    """Build the sources.tsv plan from the current project source root."""

    resolved_source_root = context.paths.source_root.resolve()
    resolved_raw_bids_root = context.paths.raw_bids_root.resolve()
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
                candidate
                for candidate in resolved_source_root.iterdir()
                if candidate.is_dir() and not candidate.name.startswith(".")
                and candidate.resolve() != resolved_raw_bids_root
            ),
            key=lambda candidate: candidate.name.lower(),
        )
    )

    sources_config = sources_config or load_heudiconv_config(context).sources
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
        subject_label = _validate_sources_label(
            subject_label,
            field_name="subject_label",
            source_name=candidate.name,
            error_cls=SourcesError,
        )
        session_label = _validate_sources_label(
            session_label,
            field_name="session_label",
            source_name=candidate.name,
            error_cls=SourcesError,
        )

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
    """Write sources.tsv and sources.json for a planned source discovery run."""

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


def _compile_sources_pattern(pattern: str) -> re.Pattern[str]:
    """Compile the configured source-name pattern into a strict regex."""

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
    """Derive subject/session labels from a source directory name."""

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
    """Derive source labels by running the configured project command."""

    invocation = [*command, source_name]
    try:
        completed = subprocess.run(
            invocation,
            cwd=context.project_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=SOURCES_COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        timeout_seconds = int(SOURCES_COMMAND_TIMEOUT_SECONDS)
        raise SourcesError(
            f"sources command timed out after {timeout_seconds} seconds while processing {source_name}."
        ) from exc
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


def _validate_sources_label(
    label: str,
    *,
    field_name: str,
    source_name: str,
    error_cls: type[Exception],
) -> str:
    """Validate one subject or session label from source discovery."""

    normalized = label.strip()
    if not normalized:
        return ""
    if not SAFE_LABEL_PATTERN.fullmatch(normalized):
        raise error_cls(
            "BIDSFlow sources label contains unsafe characters: "
            f"{field_name}={label!r} for source_name={source_name!r}. "
            "Use ASCII letters, numbers, '.', '_' or '-' without path separators."
        )
    return normalized


def _compute_sources_statuses(
    source_root: Path,
    entries: tuple[SourcesEntry, ...],
) -> tuple[SourcesEntry, ...]:
    """Apply review status and notes to discovered source entries."""

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

        updated_entries.append(
            SourcesEntry(
                source_name=entry.source_name,
                subject_label=subject_label,
                session_label=session_label,
                include=entry.include,
                status=_classify_sources_entry(
                    entry=entry,
                    source_path=source_path,
                    subject_label=subject_label,
                    session_label=session_label,
                    subject_counts=subject_counts,
                    target_counts=target_counts,
                ),
                notes=entry.notes,
            )
        )

    return tuple(updated_entries)


def _classify_sources_entry(
    *,
    entry: SourcesEntry,
    source_path: Path,
    subject_label: str,
    session_label: str,
    subject_counts: dict[str, int],
    target_counts: dict[tuple[str, str], int],
) -> str:
    """Classify a source row as ready or needing review."""

    if not entry.include:
        return "excluded"
    if not source_path.is_dir():
        return "missing_source"
    if not subject_label:
        return "needs_review"
    if session_label and target_counts.get((subject_label, session_label), 0) > 1:
        return "collision"
    if not session_label and subject_counts.get(subject_label, 0) > 1:
        return "needs_review"
    return "ready"


def _load_confirmed_sources(
    source_root: Path,
    sources_path: Path,
) -> tuple[SourcesEntry, ...]:
    """Load and validate the user-reviewed sources.tsv table."""

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
                    subject_label=_validate_sources_label(
                        row.get("subject_label") or "",
                        field_name="subject_label",
                        source_name=source_name,
                        error_cls=HeudiconvRunError,
                    ),
                    session_label=_validate_sources_label(
                        row.get("session_label") or "",
                        field_name="session_label",
                        source_name=source_name,
                        error_cls=HeudiconvRunError,
                    ),
                    include=include,
                    status="",
                    notes=(row.get("notes") or "").strip(),
                )
            )

    return _compute_sources_statuses(source_root.resolve(), tuple(entries))


def _parse_sources_include(value: str | None, row_number: int) -> bool:
    """Parse the editable include column from sources.tsv."""

    normalized = (value or "").strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise HeudiconvRunError(
        f"BIDSFlow sources table row {row_number} has invalid include value: {value!r}"
    )


def _resolve_sources_source_path(source_root: Path, source_name: str) -> Path:
    """Resolve a reviewed source name to an immediate child of source_root."""

    source_candidate = Path(source_name)
    if source_candidate.name != source_name or source_name in {"", ".", ".."}:
        raise HeudiconvRunError(
            f"BIDSFlow sources table source_name must name an immediate child directory under source_root: {source_name!r}"
        )
    return source_root.resolve() / source_candidate


def _guard_sources_reset_requirement(plan: SourcesPlan, reset: bool) -> None:
    """Prevent accidental replacement of an existing sources table."""

    if reset:
        return
    if plan.sources_path.exists() or plan.sources_state_path.exists():
        raise SourcesError(
            "Existing BIDSFlow sources state was found. Use --force to regenerate it."
        )


def _prepare_sources_directories(plan: SourcesPlan) -> None:
    """Create parent directories for source review artifacts."""

    plan.sources_path.parent.mkdir(parents=True, exist_ok=True)
    plan.sources_state_path.parent.mkdir(parents=True, exist_ok=True)


def _reset_sources_state(project_root: Path, plan: SourcesPlan) -> None:
    """Remove existing source review artifacts during forced regeneration."""

    try:
        for path in (plan.sources_path, plan.sources_state_path):
            _remove_project_path(project_root, path)
    except ValueError as exc:
        raise SourcesError(str(exc)) from exc


def _write_sources_tsv(sources_path: Path, entries: tuple[SourcesEntry, ...]) -> None:
    """Write the editable source review table."""

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
    """Write source discovery metadata without duplicating row-level state."""

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
    """Return counts by source review status."""

    return _summarize_sources_entries(entries)


def list_sources_review_issues(entries: tuple[SourcesEntry, ...]) -> list[str]:
    """Return human-readable review issues for non-ready source rows."""

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
    """Count source rows by their current review status."""

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
