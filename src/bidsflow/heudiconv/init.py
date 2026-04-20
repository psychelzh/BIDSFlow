from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from ..project import ProjectContext
from .common import (
    HeudiconvInitError,
    HeudiconvRunError,
    _ensure_project_owned_path,
    _render_sge_heudiconv_script,
    _resolve_scheduler_script_path,
)
from .sources import (
    SourcesEntry,
    SourcesError,
    SourcesPlan,
    _load_confirmed_sources,
    _write_sources_state,
    plan_sources,
    run_sources,
)


@dataclass(frozen=True)
class InitPlan:
    sources_plan: SourcesPlan
    code_root: Path
    heuristic_parent: Path
    scheduler: str
    scheduler_script_path: Path | None
    scheduler_script_content: str | None


@dataclass(frozen=True)
class InitResult:
    sources_path: Path
    sources_state_path: Path
    entries: tuple[SourcesEntry, ...]
    sources_action: str
    code_root: Path
    heuristic_parent: Path
    scheduler: str
    scheduler_script_path: Path | None
    scheduler_script_action: str


def plan_heudiconv_init(context: ProjectContext) -> InitPlan:
    sources_plan = plan_sources(context)
    scheduler = context.execution.scheduler
    scheduler_script_path: Path | None = None
    scheduler_script_content: str | None = None

    if scheduler == "sge":
        scheduler_script_path = _resolve_scheduler_script_path(context, target="heudiconv")
        scheduler_script_content = _render_sge_heudiconv_script()
    elif scheduler != "none":  # pragma: no cover
        raise HeudiconvInitError(f"Unsupported scheduler for HeuDiConv init: {scheduler}")

    return InitPlan(
        sources_plan=sources_plan,
        code_root=context.project_root / "code" / "heudiconv",
        heuristic_parent=context.heudiconv.heuristic.parent,
        scheduler=scheduler,
        scheduler_script_path=scheduler_script_path,
        scheduler_script_content=scheduler_script_content,
    )


def run_heudiconv_init(context: ProjectContext, plan: InitPlan, force: bool) -> InitResult:
    sources_table_exists = plan.sources_plan.sources_path.exists()
    sources_state_exists = plan.sources_plan.sources_state_path.exists()
    sources_exist = sources_table_exists or sources_state_exists
    sources_entries = plan.sources_plan.entries

    if sources_table_exists and not force:
        sources_entries = _load_existing_sources_entries(plan.sources_plan)
    if sources_table_exists and sources_state_exists and not force:
        sources_action = "kept"
    elif sources_table_exists and not force:
        refreshed_plan = replace(plan.sources_plan, entries=sources_entries)
        _write_sources_state(context, refreshed_plan)
        sources_action = "metadata refreshed"
    else:
        run_sources(context, plan.sources_plan, reset=force)
        sources_action = "overwritten" if sources_exist else "created"
        sources_entries = plan.sources_plan.entries

    plan.code_root.mkdir(parents=True, exist_ok=True)
    plan.heuristic_parent.mkdir(parents=True, exist_ok=True)

    scheduler_script_action = "not configured"
    if plan.scheduler_script_path is not None and plan.scheduler_script_content is not None:
        _ensure_project_owned_path(context.project_root, plan.scheduler_script_path)
        scheduler_script_exists = plan.scheduler_script_path.exists()
        if scheduler_script_exists and not force:
            scheduler_script_action = "kept"
        else:
            plan.scheduler_script_path.parent.mkdir(parents=True, exist_ok=True)
            plan.scheduler_script_path.write_text(
                plan.scheduler_script_content,
                encoding="utf-8",
                newline="\n",
            )
            scheduler_script_action = "overwritten" if scheduler_script_exists else "created"

    return InitResult(
        sources_path=plan.sources_plan.sources_path,
        sources_state_path=plan.sources_plan.sources_state_path,
        entries=sources_entries,
        sources_action=sources_action,
        code_root=plan.code_root,
        heuristic_parent=plan.heuristic_parent,
        scheduler=plan.scheduler,
        scheduler_script_path=plan.scheduler_script_path,
        scheduler_script_action=scheduler_script_action,
    )


def _load_existing_sources_entries(plan: SourcesPlan) -> tuple[SourcesEntry, ...]:
    try:
        return _load_confirmed_sources(plan.source_root, plan.sources_path)
    except HeudiconvRunError as exc:
        raise SourcesError(str(exc)) from exc
