from __future__ import annotations

from pathlib import Path

from .common import _read_template
from .project import ProjectContext


def _resolve_scheduler_script_path(context: ProjectContext, target: str) -> Path:
    return (
        context.project_root
        / "code"
        / "bidsflow"
        / context.execution.scheduler
        / f"{target}.sh"
    ).resolve()


def _render_sge_array_wrapper_script() -> str:
    return _read_template("sge", "array-wrapper.sh.template")
