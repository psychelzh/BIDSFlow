"""HeuDiConv compatibility wrappers for shared run-state helpers."""

from __future__ import annotations

from pathlib import Path

from ..run_state import (
    build_subject_session_unit_name,
    read_key_value_status as _read_key_value_status,
)


def build_run_unit_name(subject_label: str, session_label: str | None) -> str:
    """Build the stable unit name used for logs, claims, and status files."""

    return build_subject_session_unit_name(subject_label, session_label)


def read_key_value_status(path: Path) -> dict[str, str]:
    """Read a simple key=value status file."""

    return _read_key_value_status(path)
