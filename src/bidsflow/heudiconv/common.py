from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from importlib import resources
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from ..project import ProjectContext


class HeudiconvInitError(Exception):
    pass


class HeudiconvRunError(Exception):
    pass


def _make_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | 0o111)


def format_command(argv: tuple[str, ...]) -> str:
    return subprocess.list2cmdline(list(argv))


def _resolve_scheduler_script_path(context: ProjectContext, target: str) -> Path:
    scheduler_template = context.execution.scheduler_template
    if scheduler_template is None:  # pragma: no cover
        raise HeudiconvInitError(
            f"[execution].scheduler_template is required when scheduler is {context.execution.scheduler!r}."
        )

    rendered = (
        scheduler_template.replace("{{ scheduler }}", context.execution.scheduler)
        .replace("{{ target }}", target)
    )
    if "{{" in rendered or "}}" in rendered:
        raise HeudiconvInitError(
            "[execution].scheduler_template currently supports only "
            "{{ scheduler }} and {{ target }} placeholders."
        )

    candidate = Path(rendered)
    if candidate.is_absolute():
        return candidate.resolve()
    return (context.project_root / candidate).resolve()


def _render_sge_heudiconv_script() -> str:
    return (
        resources.files("bidsflow")
        .joinpath("templates", "sge", "heudiconv.sh.template")
        .read_text(encoding="utf-8")
    )


def _ensure_project_owned_path(project_root: Path, path: Path) -> None:
    resolved_root = project_root.resolve()
    resolved_path = path.resolve()
    if not resolved_path.is_relative_to(resolved_root):
        raise HeudiconvInitError(
            f"Refusing to write HeuDiConv init file outside the project root: {resolved_path}"
        )


def _compute_input_signature(parts: dict[str, str | bytes]) -> str:
    digest = hashlib.sha256()
    for key in sorted(parts):
        digest.update(key.encode("utf-8"))
        digest.update(b"\0")
        value = parts[key]
        digest.update(value.encode("utf-8") if isinstance(value, str) else value)
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def _format_attempt_label() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_key_value_status_atomic(path: Path, lines: list[tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    temp_path.write_text(
        "".join(f"{key}={_format_status_value(value)}\n" for key, value in lines),
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temp_path, path)


def _format_status_value(value: str) -> str:
    return value.replace("\r", " ").replace("\n", " ").strip()


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


def _combine_process_output(stdout: str | None, stderr: str | None) -> str:
    parts = [part.strip("\n") for part in (stdout or "", stderr or "") if part]
    return "\n".join(parts)


def _append_log(log_path: Path, message: str) -> None:
    """Append a single-writer log message without explicit cross-process locking.

    BIDSFlow uses _append_log for local per-unit logs where each file has one
    writer. Shared files that require coordination, such as scheduler results
    tables, use their own locking.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", newline="\n") as log_handle:
        if log_handle.tell() > 0:
            log_handle.write("\n")
        log_handle.write(message.rstrip())
        log_handle.write("\n")
