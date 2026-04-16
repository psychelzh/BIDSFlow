from __future__ import annotations

from importlib import resources
from pathlib import Path

import typer

from .heudiconv import (
    HeudiconvConvertError,
    HeudiconvManifestError,
    HeudiconvSkeletonError,
    format_command,
    list_manifest_review_issues,
    plan_convert,
    plan_manifest,
    plan_skeleton,
    run_convert,
    run_manifest,
    run_skeleton,
    summarize_manifest_entries,
)
from .project import find_project_config, load_project_context

app = typer.Typer(
    help="BIDSFlow: a task-first CLI for BIDS workflow logistics.",
    no_args_is_help=True,
)
heudiconv_app = typer.Typer(help="Managed HeuDiConv workflow commands.")
app.add_typer(heudiconv_app, name="heudiconv")

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


def _validate_config_name(config_name: str) -> str:
    candidate = Path(config_name)
    if config_name in {"", ".", ".."} or candidate.name != config_name:
        raise typer.BadParameter("Config name must be a filename, not a path.")
    return config_name


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
    config_name: str = typer.Option(
        "bidsflow.toml",
        "--config-name",
        help="Filename for the generated config.",
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
    config_name = _validate_config_name(config_name)
    target_directory = directory.resolve()

    if target_directory.exists() and not target_directory.is_dir():
        typer.echo(f"Target path is not a directory: {target_directory}", err=True)
        raise typer.Exit(code=2)

    config_path = target_directory / config_name
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


@heudiconv_app.command("skeleton")
def heudiconv_skeleton(
    sample_paths: list[Path] = typer.Argument(
        ...,
        exists=False,
        help="One or more representative sample paths under the configured source_root used to generate starter HeuDiConv outputs.",
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        help="Path to bidsflow.toml. Defaults to the nearest project config.",
    ),
    reset: bool = typer.Option(
        False,
        "--reset",
        help="Regenerate skeleton outputs after clearing prior HeuDiConv state.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show the managed command and planned output locations without running it.",
    ),
) -> None:
    """Generate a HeuDiConv skeleton from representative sample paths under source_root."""
    try:
        config_path = find_project_config(config, Path.cwd())
        context = load_project_context(config_path)
        plan = plan_skeleton(context, sample_paths)
    except (HeudiconvSkeletonError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    if dry_run:
        if len(plan.units) == 1 and plan.units[0].session_label is None:
            unit = plan.units[0]
            typer.echo("Planned HeuDiConv skeleton strategy: single-directory skeleton")
            typer.echo(
                f"Temporary subject for skeleton: {unit.subject_label}"
            )
            typer.echo(format_command(unit.initial_command))
        else:
            typer.echo(
                f"Planned HeuDiConv skeleton strategy: split {len(plan.units)} directories into "
                "single-directory session units"
            )
            for unit in plan.units:
                typer.echo(
                    f"{unit.unit_name}: sample={unit.sample_path} "
                    f"subject={unit.subject_label} session={unit.session_label}"
                )
                typer.echo(format_command(unit.initial_command))
        typer.echo(f"Config: {config_path}")
        typer.echo(f"Sample paths: {len(plan.sample_paths)}")
        typer.echo(f"Skeleton work root: {plan.skeleton_work_root}")
        typer.echo(f"Heuristic: {plan.heuristic_path}")
        typer.echo(f"DICOM inventories: {plan.dicominfo_root}")
        typer.echo(f"State: {plan.skeleton_state_path}")
        typer.echo(f"Units: {plan.skeleton_units_path}")
        typer.echo(f"Unit logs: {plan.log_dir}")
        return

    try:
        result = run_skeleton(context, plan, reset=reset)
    except HeudiconvSkeletonError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    typer.echo("Prepared HeuDiConv skeleton outputs.")
    typer.echo(f"Skeleton units: {len(result.unit_results)}")
    typer.echo(f"Skeleton work root: {plan.skeleton_work_root}")
    typer.echo(f"Heuristic: {result.heuristic_path}")
    typer.echo(f"DICOM inventories: {result.dicominfo_root} ({len(result.dicominfo_paths)} files)")
    typer.echo(f"State: {result.skeleton_state_path}")
    typer.echo(f"Units: {result.skeleton_units_path}")
    typer.echo(f"Unit logs: {result.log_dir}")
    typer.echo(
        "Next: review and edit the heuristic. Manifest can be prepared before or after "
        "skeleton, but convert will need both a confirmed manifest and a reviewed heuristic."
    )


@heudiconv_app.command("manifest")
def heudiconv_manifest(
    config: Path | None = typer.Option(
        None,
        "--config",
        help="Path to bidsflow.toml. Defaults to the nearest project config.",
    ),
    reset: bool = typer.Option(
        False,
        "--reset",
        help="Regenerate the manifest after clearing prior manifest state.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show the planned manifest outputs without writing files.",
    ),
) -> None:
    """Enumerate source_root into a reviewable manifest; configured commands receive source_name and must print one or two lines: subject_label, then optional session_label."""
    try:
        config_path = find_project_config(config, Path.cwd())
        context = load_project_context(config_path)
        plan = plan_manifest(context)
    except (HeudiconvManifestError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    if dry_run:
        typer.echo("Planned HeuDiConv manifest generation.")
        typer.echo(f"Config: {config_path}")
        typer.echo(f"Source root: {plan.source_root}")
        typer.echo(f"Entries discovered: {len(plan.entries)}")
        typer.echo(f"Manifest: {plan.manifest_path}")
        typer.echo(f"State: {plan.manifest_state_path}")
        typer.echo("Links: not created by manifest; convert will materialize temporary links if needed.")
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
        result = run_manifest(context, plan, reset=reset)
    except HeudiconvManifestError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    typer.echo("Wrote HeuDiConv manifest.")
    typer.echo(f"Entries: {len(result.entries)}")
    typer.echo(f"Manifest: {result.manifest_path}")
    typer.echo(f"State: {result.manifest_state_path}")
    if plan.command is not None:
        typer.echo(f"Label generation used command: {format_command(plan.command)}")
        typer.echo(
            "Command contract: source_name is passed as the last argv item; "
            "cwd is project_root; stdout line 1 is subject_label; "
            "stdout line 2 is optional session_label."
        )
    typer.echo("Summary:")
    summary = summarize_manifest_entries(result.entries)
    for key in ("total", "ready", "needs_review", "collision", "missing_source", "excluded"):
        typer.echo(f"- {key}: {summary[key]}")
    issues = list_manifest_review_issues(result.entries)
    if issues:
        typer.echo("Review needed:")
        for issue in issues:
            typer.echo(f"- {issue}")
    typer.echo(
        "Next: review manifest.tsv, then decide whether to keep the generated labels or "
        "edit them manually. Skeleton may happen before or after manifest, but "
        "convert will materialize any temporary links it needs from the confirmed manifest."
    )


@heudiconv_app.command("convert")
def heudiconv_convert(
    config: Path | None = typer.Option(
        None,
        "--config",
        help="Path to bidsflow.toml. Defaults to the nearest project config.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show the managed conversion commands and planned outputs without running them.",
    ),
) -> None:
    """Run the managed HeuDiConv conversion step."""
    try:
        config_path = find_project_config(config, Path.cwd())
        context = load_project_context(config_path)
        plan = plan_convert(context)
    except (HeudiconvConvertError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    if dry_run:
        typer.echo("Planned HeuDiConv convert run.")
        typer.echo(f"Config: {config_path}")
        typer.echo(f"Manifest: {plan.manifest_path}")
        typer.echo(f"Heuristic: {plan.heuristic_path}")
        typer.echo(f"Raw BIDS root: {plan.raw_bids_root}")
        typer.echo(f"Execution view root: {plan.execution_view_root}")
        typer.echo(f"State: {plan.state_path}")
        typer.echo(f"Units: {plan.units_path}")
        typer.echo(f"Unit logs: {plan.log_dir}")
        typer.echo(f"Conversion units: {len(plan.units)}")
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
                    "Convert will apply the same manifest-driven pattern to each ready row."
                )
        return

    try:
        result = run_convert(context, plan)
    except HeudiconvConvertError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    typer.echo("Completed managed HeuDiConv conversion.")
    typer.echo(f"Conversion units: {len(result.unit_results)}")
    typer.echo(f"Raw BIDS root: {result.raw_bids_root}")
    typer.echo(f"State: {result.state_path}")
    typer.echo(f"Units: {result.units_path}")
    typer.echo(f"Unit logs: {result.log_dir}")


if __name__ == "__main__":
    app()

