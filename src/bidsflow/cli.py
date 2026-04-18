from __future__ import annotations

from importlib import resources
from pathlib import Path

import typer

from .heudiconv import (
    HeudiconvDraftError,
    HeudiconvRunError,
    SourcesError,
    format_command,
    list_sources_review_issues,
    plan_draft,
    plan_heudiconv_run,
    plan_sources,
    run_draft,
    run_heudiconv,
    run_sources,
    summarize_sources_entries,
)
from .project import find_project_config, load_project_context

app = typer.Typer(
    help="BIDSFlow: a task-first CLI for BIDS workflow logistics.",
    no_args_is_help=True,
)
DEFAULT_LAYOUT_DIRECTORIES = (
    Path("sourcedata"),
    Path("sourcedata") / "raw",
    Path("derivatives"),
    Path("work"),
    Path("logs"),
    Path("state"),
)


def _toml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _render_project_config(project_name: str) -> str:
    template = (
        resources.files("bidsflow")
        .joinpath("templates", "bidsflow.toml.template")
        .read_text(encoding="utf-8")
    )
    return template.replace('__PROJECT_NAME__', _toml_string(project_name))


def _default_project_name(directory: Path) -> str:
    return directory.resolve().name or "BIDSFlow project"


@app.callback()
def main() -> None:
    """BIDSFlow: task-first CLI for BIDS workflow logistics."""


@app.command()
def init(
    directory: Path = typer.Argument(
        Path("."),
        file_okay=False,
        help="Directory where the project scaffold should be created.",
    ),
    name: str | None = typer.Option(
        None,
        "--name",
        help="Project name to write into the generated config.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite the generated config if it already exists.",
    ),
    make_dirs: bool = typer.Option(
        False,
        "--make-dirs",
        help="Create the default project directories described by the generated config.",
    ),
) -> None:
    """Initialize a minimal BIDSFlow project scaffold."""
    target_directory = directory.resolve()

    if target_directory.exists() and not target_directory.is_dir():
        typer.echo(f"Target path is not a directory: {target_directory}", err=True)
        raise typer.Exit(code=2)

    config_path = target_directory / "bidsflow.toml"
    if config_path.exists() and not force:
        typer.echo(
            f"Refusing to overwrite existing config: {config_path}. Use --force to overwrite it.",
            err=True,
        )
        raise typer.Exit(code=2)

    target_directory.mkdir(parents=True, exist_ok=True)

    project_name = name or _default_project_name(target_directory)
    config_path.write_text(_render_project_config(project_name), encoding="utf-8", newline="\n")

    if make_dirs:
        for relative_path in DEFAULT_LAYOUT_DIRECTORIES:
            (target_directory / relative_path).mkdir(parents=True, exist_ok=True)

    typer.echo(f"Initialized BIDSFlow project at {target_directory}")
    typer.echo(f"Config: {config_path}")


@app.command("sources")
def sources(
    reset: bool = typer.Option(
        False,
        "--reset",
        help="Regenerate the sources table after clearing prior sources state.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show the planned sources outputs without writing files.",
    ),
) -> None:
    """Enumerate source_root into a reviewable sources table."""
    try:
        config_path = find_project_config(Path.cwd())
        context = load_project_context(config_path)
        plan = plan_sources(context)
    except (SourcesError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    if dry_run:
        typer.echo("Planned BIDSFlow sources scan.")
        typer.echo(f"Config: {config_path}")
        typer.echo(f"Source root: {plan.source_root}")
        typer.echo(f"Entries discovered: {len(plan.entries)}")
        typer.echo(f"Sources: {plan.sources_path}")
        typer.echo(f"State: {plan.sources_state_path}")
        typer.echo("Links: not created by sources; heudiconv will materialize temporary links if needed.")
        if plan.template is not None:
            typer.echo(f"Label generation: template={plan.template!r}")
        elif plan.command is not None:
            typer.echo(f"Label generation: command={format_command(plan.command)}")
            typer.echo(
                "Command contract: source_name is passed as the last argv item; "
                "cwd is project_root; stdout line 1 is subject_label; "
                "stdout line 2 is optional session_label."
            )
        else:
            typer.echo("Label generation: no template or command configured; labels will be left blank.")
        return

    try:
        result = run_sources(context, plan, reset=reset)
    except SourcesError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    typer.echo("Wrote BIDSFlow sources table.")
    typer.echo(f"Entries: {len(result.entries)}")
    typer.echo(f"Sources: {result.sources_path}")
    typer.echo(f"State: {result.sources_state_path}")
    if plan.command is not None:
        typer.echo(f"Label generation used command: {format_command(plan.command)}")
        typer.echo(
            "Command contract: source_name is passed as the last argv item; "
            "cwd is project_root; stdout line 1 is subject_label; "
            "stdout line 2 is optional session_label."
        )
    typer.echo("Summary:")
    summary = summarize_sources_entries(result.entries)
    for key in ("total", "ready", "needs_review", "collision", "missing_source", "excluded"):
        typer.echo(f"- {key}: {summary[key]}")
    issues = list_sources_review_issues(result.entries)
    if issues:
        typer.echo("Review needed:")
        for issue in issues:
            typer.echo(f"- {issue}")
    typer.echo(
        "Next: review sources.tsv, optionally generate a heuristic draft with "
        "`bidsflow heudiconv --draft <sample-path>`, then run `bidsflow heudiconv`."
    )


@app.command("heudiconv")
def heudiconv(
    sample_paths: list[Path] = typer.Argument(
        None,
        exists=False,
        help="Representative sample paths used with --draft.",
    ),
    draft: bool = typer.Option(
        False,
        "--draft",
        help="Generate a draft heuristic from representative sample paths instead of executing HeuDiConv.",
    ),
    reset: bool = typer.Option(
        False,
        "--reset",
        help="Regenerate draft outputs after clearing prior draft state. Only valid with --draft.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show planned commands and outputs without running them.",
    ),
) -> None:
    """Run managed HeuDiConv, or generate a draft heuristic with --draft."""
    sample_paths = sample_paths or []
    if draft:
        _run_heudiconv_draft(sample_paths, reset=reset, dry_run=dry_run)
        return
    if sample_paths:
        typer.echo("Sample paths are only valid with --draft.", err=True)
        raise typer.Exit(code=2)
    if reset:
        typer.echo("--reset is only valid with --draft.", err=True)
        raise typer.Exit(code=2)
    _run_heudiconv_default(dry_run=dry_run)


def _run_heudiconv_draft(
    sample_paths: list[Path],
    *,
    reset: bool,
    dry_run: bool,
) -> None:
    if not sample_paths:
        typer.echo("--draft requires at least one sample path.", err=True)
        raise typer.Exit(code=2)

    try:
        config_path = find_project_config(Path.cwd())
        context = load_project_context(config_path)
        plan = plan_draft(context, sample_paths)
    except (HeudiconvDraftError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    if dry_run:
        if len(plan.units) == 1 and plan.units[0].session_label is None:
            unit = plan.units[0]
            typer.echo("Planned HeuDiConv draft strategy: single-directory draft")
            typer.echo(f"Temporary subject for draft: {unit.subject_label}")
            typer.echo(format_command(unit.initial_command))
        else:
            typer.echo(
                f"Planned HeuDiConv draft strategy: split {len(plan.units)} directories into "
                "single-directory sample units"
            )
            for unit in plan.units:
                typer.echo(
                    f"{unit.unit_name}: sample={unit.sample_path} "
                    f"subject={unit.subject_label} session={unit.session_label}"
                )
                typer.echo(format_command(unit.initial_command))
        typer.echo(f"Config: {config_path}")
        typer.echo(f"Sample paths: {len(plan.sample_paths)}")
        typer.echo(f"Draft work root: {plan.draft_work_root}")
        typer.echo(f"Heuristic: {plan.heuristic_path}")
        typer.echo(f"DICOM inventories: {plan.dicominfo_root}")
        typer.echo(f"State: {plan.draft_state_path}")
        typer.echo(f"Unit logs: {plan.log_dir}")
        return

    try:
        result = run_draft(context, plan, reset=reset)
    except HeudiconvDraftError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    typer.echo("Prepared HeuDiConv draft outputs.")
    typer.echo(f"Draft samples: {len(result.unit_results)}")
    typer.echo(f"Draft work root: {plan.draft_work_root}")
    typer.echo(f"Heuristic: {result.heuristic_path}")
    typer.echo(f"DICOM inventories: {result.dicominfo_root} ({len(result.dicominfo_paths)} files)")
    typer.echo(f"State: {result.draft_state_path}")
    typer.echo(f"Unit logs: {result.log_dir}")
    typer.echo(
        "Next: review and edit the heuristic, review sources.tsv, then run `bidsflow heudiconv`."
    )


def _run_heudiconv_default(
    *,
    dry_run: bool,
) -> None:
    try:
        config_path = find_project_config(Path.cwd())
        context = load_project_context(config_path)
        plan = plan_heudiconv_run(context)
    except (HeudiconvRunError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    if dry_run:
        typer.echo("Planned `bidsflow heudiconv` execution.")
        typer.echo(f"Config: {config_path}")
        typer.echo(f"Sources: {plan.sources_path}")
        typer.echo(f"Heuristic: {plan.heuristic_path}")
        typer.echo(f"Raw BIDS root: {plan.raw_bids_root}")
        typer.echo(f"Execution view root: {plan.execution_view_root}")
        typer.echo(f"State: {plan.state_path}")
        typer.echo(f"Units: {plan.units_path}")
        typer.echo(f"Unit logs: {plan.log_dir}")
        typer.echo(f"Run units: {len(plan.units)}")
        if plan.units:
            unit = plan.units[0]
            typer.echo("Example unit:")
            typer.echo(
                f"{unit.source_name}: subject={unit.subject_label} "
                f"session={unit.session_label or '-'}"
            )
            typer.echo(format_command(unit.command))
            if len(plan.units) > 1:
                typer.echo(
                    f"Additional units omitted: {len(plan.units) - 1}. "
                    "HeuDiConv will apply the same sources-driven pattern to each ready row."
                )
        return

    try:
        result = run_heudiconv(context, plan)
    except HeudiconvRunError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    typer.echo("Completed `bidsflow heudiconv` execution.")
    typer.echo(f"Run units: {len(result.unit_results)}")
    typer.echo(f"Raw BIDS root: {result.raw_bids_root}")
    typer.echo(f"State: {result.state_path}")
    typer.echo(f"Units: {result.units_path}")
    typer.echo(f"Unit logs: {result.log_dir}")


if __name__ == "__main__":
    app()

