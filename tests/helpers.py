from __future__ import annotations

import csv
import re
import shlex
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
    init_args = ["init", str(project_dir), "--scheduler", scheduler]
    init_result = runner.invoke(app, init_args)
    assert init_result.exit_code == 0, init_result.output
    return project_dir


def make_source_dirs(project_dir: Path, *source_names: str, root: str = "sourcedata") -> Path:
    source_root = project_dir / root
    for source_name in source_names:
        (source_root / source_name).mkdir(parents=True)
    return source_root


def replace_config(config_path: Path, old: str, new: str) -> None:
    text = config_path.read_text(encoding="utf-8")
    if old not in text:
        raise AssertionError(f"Expected config snippet not found in {config_path}: {old!r}")
    config_path.write_text(
        text.replace(old, new),
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


def write_successful_run_launcher(project_dir: Path) -> Path:
    return write_python_script(
        project_dir / "fake_heudiconv.py",
        (
            "from pathlib import Path",
            "import sys",
            "",
            "argv = sys.argv[1:]",
            "files_path = Path(argv[argv.index('--files') + 1])",
            "out_dir = Path(argv[argv.index('-o') + 1])",
            "subject = argv[argv.index('-s') + 1]",
            "session = argv[argv.index('-ss') + 1] if '-ss' in argv else None",
            "assert files_path.exists()",
            "target = out_dir / f'sub-{subject}'",
            "if session is not None:",
            "    target = target / f'ses-{session}'",
            "target.mkdir(parents=True, exist_ok=True)",
            "(target / 'marker.txt').write_text(str(files_path), encoding='utf-8')",
            "print('run ok')",
        ),
    )


def write_failing_run_launcher(project_dir: Path) -> Path:
    return write_python_script(
        project_dir / "fake_heudiconv_fails.py",
        (
            "print('failing heudiconv')",
            "raise SystemExit(42)",
        ),
    )


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


def assert_lines_in_order(text: str, expected_lines: list[str]) -> None:
    lines = text.splitlines()
    search_start = 0
    for expected in expected_lines:
        for index in range(search_start, len(lines)):
            if lines[index] == expected:
                search_start = index + 1
                break
        else:
            raise AssertionError(f"Could not find line after index {search_start}: {expected!r}")


def normalize_generated_text(
    text: str,
    *,
    replacements: dict[str, str] | None = None,
    drop_blank_lines: bool = False,
) -> str:
    normalized = text.replace("\r\n", "\n")
    for old, new in sorted((replacements or {}).items(), key=lambda item: len(item[0]), reverse=True):
        normalized = normalized.replace(old, new)
    normalized = re.sub(r"\d{8}T\d{6}\d{6}Z", "<ATTEMPT>", normalized)
    if drop_blank_lines:
        normalized = "\n".join(line for line in normalized.splitlines() if line.strip())
        normalized += "\n"
    return normalized


def assert_rendered_sge_wrapper(
    text: str,
    *,
    task_count: int,
    scheduler_log_dir: Path,
    runtime_script_path: Path,
) -> None:
    assert_lines_in_order(
        text,
        [
            "#$ -S /bin/bash",
            "#$ -cwd",
            "#$ -N bidsflow-heudiconv",
            f"#$ -t 1-{task_count}",
            "#$ -j y",
            f"#$ -o {scheduler_log_dir}",
        ],
    )
    assert "Site-specific scheduler settings can be enabled here." in text
    assert "Site-specific environment setup goes here." in text
    assert 'export BIDSFLOW_SCHEDULER="sge"' in text
    assert "BIDSFLOW_SCHEDULER_JOB_ID" in text
    assert "BIDSFLOW_TASK_ID" in text
    assert f"exec bash {shlex.quote(str(runtime_script_path))}" in text
    assert "write_unit_status" not in text
    assert "append_final_result" not in text
    assert "unit_row=" not in text
    assert "results_table_path" not in text
    assert "{{" not in text


def assert_rendered_sge_runtime_script(
    text: str,
    *,
    common_runtime_script_path: Path,
) -> None:
    assert "#$ -S /bin/bash" not in text
    assert "SGE_TASK_ID" not in text
    assert "${JOB_ID" not in text
    assert f"source {shlex.quote(str(common_runtime_script_path))}" in text
    assert "Scheduler job/task:" in text
    assert "run_error_message=" in text
    assert "Command failed with exit status" in text
    assert "set +e" in text
    assert "launcher=(" in text
    assert "bidsflow_write_unit_status" in text
    assert "bidsflow_finish_unit" in text
    assert "unit_row=" not in text
    assert "results_table_path" not in text
    assert "append_final_result" not in text
    assert "{{" not in text


def assert_rendered_array_common_script(
    text: str,
    *,
    unit_list_path: Path,
    results_path: Path,
    cleanup_execution_view: bool,
) -> None:
    assert "#$ -S /bin/bash" not in text
    assert "SGE_TASK_ID" not in text
    assert "${JOB_ID" not in text
    assert 'task_id="${BIDSFLOW_TASK_ID:?BIDSFLOW_TASK_ID is required.}"' in text
    assert f"unit_list_path={unit_list_path}" in text
    assert f"results_table_path={results_path}" in text
    assert "unit_row=" in text
    assert "bidsflow_write_unit_status" in text
    assert "bidsflow_append_final_result" in text
    assert "bidsflow_cleanup_results_table_lock" in text
    assert "bidsflow_finish_unit" in text
    assert "Timed out waiting for results table lock" in text
    assert 'rm -f -- "$claim_path"' in text
    if cleanup_execution_view:
        assert 'cleanup_execution_view="true"' in text
        assert 'if [[ "$cleanup_execution_view" == "true" ]]; then' in text
        assert 'rm -f -- "$execution_path"' in text
    else:
        assert 'cleanup_execution_view="false"' in text
    assert "{{" not in text
