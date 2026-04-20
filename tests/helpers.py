from __future__ import annotations

import csv
from collections.abc import Callable, Iterable
from pathlib import Path

from bidsflow.cli import app
from bidsflow.project import load_project_context


def append_config(config_path: Path, lines: list[str]) -> None:
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + "\n" + "\n".join(lines) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def init_project(
    tmp_path: Path,
    runner,
    *,
    name: str = "demo-project",
    scheduler: str = "none",
) -> Path:
    project_dir = tmp_path / name
    init_args = ["init", str(project_dir)]
    if scheduler != "none":
        init_args.extend(["--scheduler", scheduler])
    init_result = runner.invoke(app, init_args)
    assert init_result.exit_code == 0, init_result.output
    return project_dir


def make_source_dirs(project_dir: Path, *source_names: str, root: str = "sourcedata") -> Path:
    source_root = project_dir / root
    for source_name in source_names:
        (source_root / source_name).mkdir(parents=True)
    return source_root


def replace_config(config_path: Path, old: str, new: str) -> None:
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(old, new),
        encoding="utf-8",
        newline="\n",
    )


def set_sources_pattern(config_path: Path, pattern: str) -> None:
    append_config(config_path, ["[sources]", f'pattern = "{pattern}"'])


def set_launcher(config_path: Path, launcher_line: str) -> None:
    replace_config(config_path, '# launcher = ["heudiconv"]', launcher_line)


def set_submit_command(config_path: Path, submit_command_line: str) -> None:
    replace_config(config_path, 'submit_command = ["qsub", "-terse"]', submit_command_line)


def write_minimal_heuristic(project_dir: Path) -> Path:
    heuristic_path = project_dir / "code" / "heudiconv" / "heuristic.py"
    heuristic_path.parent.mkdir(parents=True, exist_ok=True)
    heuristic_path.write_text(
        "def infotodict(seqinfo):\n    return {}\n",
        encoding="utf-8",
        newline="\n",
    )
    return heuristic_path


def write_python_script(path: Path, lines: Iterable[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines)
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def ready_heudiconv_project(
    tmp_path: Path,
    runner,
    invoke_from: Callable[[Path, list[str]], object],
    *,
    scheduler: str = "none",
) -> Path:
    project_dir = init_project(tmp_path, runner, name=f"{scheduler}-project", scheduler=scheduler)
    set_sources_pattern(project_dir / "bidsflow.toml", "SUB{subject}_SES{session}")
    make_source_dirs(project_dir, "SUB001_SES01")
    init_heudiconv = invoke_from(project_dir, ["heudiconv", "init"])
    assert init_heudiconv.exit_code == 0, init_heudiconv.output
    write_minimal_heuristic(project_dir)
    return project_dir


def load_context(project_dir: Path):
    return load_project_context(project_dir / "bidsflow.toml")


def read_tsv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def read_key_value_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values
