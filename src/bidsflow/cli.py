from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from bidsflow.core.stages import STAGES

app = typer.Typer(
    help="BIDSFlow: a staged Python CLI orchestrator for BIDS Apps.",
    no_args_is_help=True,
)
console = Console()


@app.command()
def init(path: Path = typer.Option(Path("."), help="Project root to initialize.")) -> None:
    """Initialize a BIDSFlow project directory."""
    console.print(f"[green]BIDSFlow[/green] project scaffold target: {path}")
    console.print("Project initialization logic is not implemented yet.")


@app.command()
def doctor() -> None:
    """Inspect local execution prerequisites."""
    console.print("Doctor checks will cover Python, container backend, templates, and licenses.")


@app.command()
def validate(config: Path = typer.Option(..., exists=True, help="Path to TOML config.")) -> None:
    """Validate project configuration and stage inputs."""
    console.print(f"Validating configuration and inputs from: {config}")


@app.command()
def curate(
    config: Path = typer.Option(..., exists=True, help="Path to TOML config."),
    participant: str | None = typer.Option(None, help="Participant label, e.g. sub-001."),
) -> None:
    """Run HeuDiConv-backed curation."""
    console.print(f"Curate stage requested with config={config} participant={participant}")


@app.command()
def fmriprep(
    config: Path = typer.Option(..., exists=True, help="Path to TOML config."),
    participant: str | None = typer.Option(None, help="Participant label, e.g. sub-001."),
) -> None:
    """Run fMRIPrep stage."""
    console.print(f"fMRIPrep stage requested with config={config} participant={participant}")


@app.command()
def mriqc(
    config: Path = typer.Option(..., exists=True, help="Path to TOML config."),
    participant: str | None = typer.Option(None, help="Participant label, e.g. sub-001."),
) -> None:
    """Run MRIQC stage."""
    console.print(f"MRIQC stage requested with config={config} participant={participant}")


@app.command(name="xcpd")
def xcpd_cmd(
    config: Path = typer.Option(..., exists=True, help="Path to TOML config."),
    participant: str | None = typer.Option(None, help="Participant label, e.g. sub-001."),
) -> None:
    """Run XCP-D stage."""
    console.print(f"XCP-D stage requested with config={config} participant={participant}")


@app.command()
def qsiprep(
    config: Path = typer.Option(..., exists=True, help="Path to TOML config."),
    participant: str | None = typer.Option(None, help="Participant label, e.g. sub-001."),
) -> None:
    """Run QSIPrep stage."""
    console.print(f"QSIPrep stage requested with config={config} participant={participant}")


@app.command()
def qsirecon(
    config: Path = typer.Option(..., exists=True, help="Path to TOML config."),
    participant: str | None = typer.Option(None, help="Participant label, e.g. sub-001."),
) -> None:
    """Run QSIRecon stage."""
    console.print(f"QSIRecon stage requested with config={config} participant={participant}")


@app.command()
def status() -> None:
    """Display the current stage registry."""
    table = Table(title="BIDSFlow stages")
    table.add_column("Stage")
    table.add_column("Upstream")
    table.add_column("Products")
    for stage in STAGES.values():
        upstream = ", ".join(s.value for s in stage.upstream) or "-"
        products = ", ".join(stage.products)
        table.add_row(stage.id.value, upstream, products)
    console.print(table)


if __name__ == "__main__":
    app()
