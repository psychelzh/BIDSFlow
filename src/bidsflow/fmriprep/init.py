"""fMRIPrep target initialization planning and file generation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys

from ..common import _read_template, _remove_project_path
from ..project import ProjectContext, target_config_path
from ..schedulers import (
    _render_sge_array_wrapper_script,
    _resolve_scheduler_script_path,
)
from .errors import FmriprepInitError, FmriprepRunError
from .participants import (
    ParticipantEntry,
    ParticipantsPlan,
    load_participants,
    plan_participants,
    write_participants_tsv,
)


@dataclass(frozen=True)
class FmriprepInitPlan:
    """Planned fMRIPrep initialization artifacts for a project."""

    participants_plan: ParticipantsPlan
    config_path: Path
    config_content: str
    raw_layout_dir: Path
    build_raw_layout: bool
    scheduler: str
    scheduler_script_path: Path | None
    cleanup_scheduler_script_path: Path | None
    scheduler_script_content: str | None


@dataclass(frozen=True)
class FmriprepInitResult:
    """Files and actions produced by fmriprep init."""

    participants_path: Path
    participants: tuple[ParticipantEntry, ...]
    participants_action: str
    config_path: Path
    config_action: str
    raw_layout_dir: Path
    raw_layout_action: str
    scheduler: str
    scheduler_script_path: Path | None
    scheduler_script_action: str
    cleanup_scheduler_script_path: Path | None
    cleanup_scheduler_script_action: str


def plan_fmriprep_init(
    context: ProjectContext,
    *,
    build_raw_layout: bool,
) -> FmriprepInitPlan:
    """Plan participant discovery, options, layout cache, and scheduler wrappers."""

    participants_path = context.paths.state_root / "fmriprep" / "participants.tsv"
    scheduler = context.execution.scheduler
    scheduler_script_path: Path | None = None
    cleanup_scheduler_script_path: Path | None = None
    scheduler_script_content: str | None = None

    if scheduler == "sge":
        scheduler_script_path = _resolve_scheduler_script_path(context, target="fmriprep")
        cleanup_scheduler_script_path = _resolve_scheduler_script_path(
            context,
            target="fmriprep-cleanup",
        )
        scheduler_script_content = _render_sge_array_wrapper_script()
    elif scheduler != "none":  # pragma: no cover
        raise FmriprepInitError(f"Unsupported scheduler for fMRIPrep init: {scheduler}")

    return FmriprepInitPlan(
        participants_plan=plan_participants(context.paths.raw_bids_root, participants_path),
        config_path=target_config_path(context, "fmriprep"),
        config_content=_render_target_config_template(),
        raw_layout_dir=context.paths.state_root / "layouts" / "raw",
        build_raw_layout=build_raw_layout,
        scheduler=scheduler,
        scheduler_script_path=scheduler_script_path,
        cleanup_scheduler_script_path=cleanup_scheduler_script_path,
        scheduler_script_content=scheduler_script_content,
    )


def run_fmriprep_init(
    context: ProjectContext,
    plan: FmriprepInitPlan,
    *,
    force: bool,
) -> FmriprepInitResult:
    """Materialize fMRIPrep init artifacts while preserving user edits by default."""

    participants_exists = plan.participants_plan.participants_path.exists()
    if participants_exists and not force:
        participants = _load_existing_participants(plan.participants_plan)
        participants_action = "kept"
    else:
        participants = plan.participants_plan.entries
        write_participants_tsv(plan.participants_plan.participants_path, participants)
        participants_action = "overwritten" if participants_exists else "created"

    config_exists = plan.config_path.exists()
    if config_exists and not force:
        config_action = "kept"
    else:
        _ensure_project_owned_path(context.project_root, plan.config_path)
        plan.config_path.parent.mkdir(parents=True, exist_ok=True)
        plan.config_path.write_text(plan.config_content, encoding="utf-8", newline="\n")
        config_action = "overwritten" if config_exists else "created"

    raw_layout_action = "not requested"
    if plan.build_raw_layout:
        raw_layout_action = _build_raw_layout(context, plan.raw_layout_dir, force=force)

    scheduler_script_action = "not configured"
    if plan.scheduler_script_path is not None and plan.scheduler_script_content is not None:
        scheduler_script_action = _write_scheduler_template(
            context,
            plan.scheduler_script_path,
            plan.scheduler_script_content,
            force=force,
        )

    cleanup_scheduler_script_action = "not configured"
    if plan.cleanup_scheduler_script_path is not None and plan.scheduler_script_content is not None:
        cleanup_scheduler_script_action = _write_scheduler_template(
            context,
            plan.cleanup_scheduler_script_path,
            plan.scheduler_script_content,
            force=force,
        )

    return FmriprepInitResult(
        participants_path=plan.participants_plan.participants_path,
        participants=participants,
        participants_action=participants_action,
        config_path=plan.config_path,
        config_action=config_action,
        raw_layout_dir=plan.raw_layout_dir,
        raw_layout_action=raw_layout_action,
        scheduler=plan.scheduler,
        scheduler_script_path=plan.scheduler_script_path,
        scheduler_script_action=scheduler_script_action,
        cleanup_scheduler_script_path=plan.cleanup_scheduler_script_path,
        cleanup_scheduler_script_action=cleanup_scheduler_script_action,
    )


def _render_target_config_template() -> str:
    """Load the fMRIPrep target config template."""

    return _read_template("fmriprep", "config.toml.template")


def _load_existing_participants(plan: ParticipantsPlan) -> tuple[ParticipantEntry, ...]:
    """Reload reviewed participant rows when init preserves an existing table."""

    try:
        return load_participants(plan.raw_bids_root, plan.participants_path)
    except FmriprepRunError as exc:
        raise FmriprepInitError(str(exc)) from exc


def _write_scheduler_template(
    context: ProjectContext,
    path: Path,
    content: str,
    *,
    force: bool,
) -> str:
    """Write one project-local scheduler template if needed."""

    _ensure_project_owned_path(context.project_root, path)
    exists = path.exists()
    if exists and not force:
        return "kept"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return "overwritten" if exists else "created"


def _build_raw_layout(context: ProjectContext, raw_layout_dir: Path, *, force: bool) -> str:
    """Build a PyBIDS raw layout database when requested."""

    if raw_layout_dir.exists():
        if not force:
            return "kept"
        try:
            _remove_project_path(context.project_root, raw_layout_dir)
        except (OSError, ValueError) as exc:
            raise FmriprepInitError(
                f"Failed to remove existing raw BIDS layout database: {raw_layout_dir}"
            ) from exc

    raw_layout_dir.parent.mkdir(parents=True, exist_ok=True)
    code = (
        "from bids import BIDSLayout\n"
        "import sys\n"
        "BIDSLayout(sys.argv[1], validate=False, database_path=sys.argv[2], "
        "reset_database=True)\n"
    )
    command = [
        sys.executable,
        "-c",
        code,
        str(context.paths.raw_bids_root),
        str(raw_layout_dir),
    ]
    completed = subprocess.run(
        command,
        cwd=context.project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout or "").strip()
        suffix = f" {details}" if details else ""
        raise FmriprepInitError(
            "Failed to build raw BIDS layout database with PyBIDS. "
            "Install PyBIDS in the BIDSFlow Python environment or run without "
            f"--build-raw-layout.{suffix}"
        )
    return "rebuilt" if force else "created"


def _ensure_project_owned_path(project_root: Path, path: Path) -> None:
    """Reject init outputs that would escape the project root."""

    resolved_root = project_root.resolve()
    resolved_path = path.resolve()
    if not resolved_path.is_relative_to(resolved_root):
        raise FmriprepInitError(
            f"Refusing to write fMRIPrep init file outside the project root: {resolved_path}"
        )
