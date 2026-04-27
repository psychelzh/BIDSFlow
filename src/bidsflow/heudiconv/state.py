"""Shared HeuDiConv state-file helpers."""

from __future__ import annotations

from pathlib import Path


def build_run_unit_name(subject_label: str, session_label: str | None) -> str:
    """Build the stable unit name used for logs, claims, and status files."""

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
