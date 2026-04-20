from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bidsflow.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def invoke_from(runner: CliRunner) -> Callable[[Path, list[str]], object]:
    def _invoke_from(project_dir: Path, args: list[str]) -> object:
        previous_cwd = Path.cwd()
        try:
            os.chdir(project_dir)
            return runner.invoke(app, args)
        finally:
            os.chdir(previous_cwd)

    return _invoke_from
