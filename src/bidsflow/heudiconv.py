from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

from .project import ProjectContext


@dataclass(frozen=True)
class BootstrapPlan:
    sample_paths: tuple[Path, ...]
    launcher: tuple[str, ...]
    command: tuple[str, ...]
    raw_bids_root: Path
    code_root: Path
    heuristic_path: Path
    dicominfo_root: Path
    heudiconv_state_path: Path
    bootstrap_state_path: Path
    log_path: Path


@dataclass(frozen=True)
class BootstrapResult:
    heuristic_path: Path
    dicominfo_root: Path
    dicominfo_paths: tuple[Path, ...]
    bootstrap_state_path: Path
    log_path: Path


class HeudiconvBootstrapError(Exception):
    pass


def plan_bootstrap(context: ProjectContext, sample_paths: list[Path]) -> BootstrapPlan:
    if not sample_paths:
        raise HeudiconvBootstrapError("At least one sample path is required for bootstrap.")

    resolved_samples: list[Path] = []
    for sample_path in sample_paths:
        resolved_sample = sample_path.resolve()
        if not resolved_sample.exists():
            raise HeudiconvBootstrapError(f"Sample path does not exist: {resolved_sample}")
        resolved_samples.append(resolved_sample)

    launcher = _load_launcher(context.config)
    raw_bids_root = context.raw_bids_root
    code_root = context.project_root / "code" / "heudiconv"
    state_root = context.state_root / "heudiconv"
    log_root = context.logs_root / "heudiconv"

    command = (
        *launcher,
        "--files",
        *(str(path) for path in resolved_samples),
        "-o",
        str(raw_bids_root),
        "-f",
        "convertall",
        "-c",
        "none",
    )

    return BootstrapPlan(
        sample_paths=tuple(resolved_samples),
        launcher=launcher,
        command=command,
        raw_bids_root=raw_bids_root,
        code_root=code_root,
        heuristic_path=code_root / "heuristic.py",
        dicominfo_root=code_root / "dicominfo",
        heudiconv_state_path=raw_bids_root / ".heudiconv",
        bootstrap_state_path=state_root / "bootstrap.json",
        log_path=log_root / "bootstrap.log",
    )


def run_bootstrap(context: ProjectContext, plan: BootstrapPlan, reset: bool) -> BootstrapResult:
    _guard_reset_requirement(plan, reset)
    _prepare_bootstrap_directories(plan)

    if reset:
        _reset_bootstrap_state(context.project_root, plan)

    try:
        with plan.log_path.open("w", encoding="utf-8", newline="\n") as log_handle:
            completed = subprocess.run(
                list(plan.command),
                cwd=context.project_root,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
    except FileNotFoundError as exc:
        _write_bootstrap_record(
            context=context,
            plan=plan,
            status="failed",
            error=str(exc),
        )
        raise HeudiconvBootstrapError(
            f"Failed to start HeuDiConv launcher. See {plan.log_path} for details."
        ) from exc

    if completed.returncode != 0:
        _write_bootstrap_record(
            context=context,
            plan=plan,
            status="failed",
            exit_code=completed.returncode,
        )
        raise HeudiconvBootstrapError(
            f"HeuDiConv bootstrap failed with exit code {completed.returncode}. "
            f"See {plan.log_path} for details."
        )

    generated_heuristic = _find_latest_generated_file(plan.heudiconv_state_path, "heuristic.py")
    generated_dicominfo_paths = _find_generated_dicominfo_files(plan.heudiconv_state_path)

    shutil.copy2(generated_heuristic, plan.heuristic_path)
    copied_dicominfo_paths = _copy_dicominfo_files(plan.dicominfo_root, generated_dicominfo_paths)

    _write_bootstrap_record(
        context=context,
        plan=plan,
        status="succeeded",
        generated_heuristic=generated_heuristic,
        generated_dicominfo_paths=generated_dicominfo_paths,
        copied_dicominfo_paths=copied_dicominfo_paths,
    )

    return BootstrapResult(
        heuristic_path=plan.heuristic_path,
        dicominfo_root=plan.dicominfo_root,
        dicominfo_paths=copied_dicominfo_paths,
        bootstrap_state_path=plan.bootstrap_state_path,
        log_path=plan.log_path,
    )


def format_command(argv: tuple[str, ...]) -> str:
    return subprocess.list2cmdline(list(argv))


def _load_launcher(config: dict[str, Any]) -> tuple[str, ...]:
    heudiconv_section = config.get("heudiconv", {})
    if heudiconv_section == {}:
        return ("heudiconv",)
    if not isinstance(heudiconv_section, dict):
        raise HeudiconvBootstrapError("[heudiconv] must be a TOML table.")

    launcher = heudiconv_section.get("launcher")
    if launcher is None:
        return ("heudiconv",)

    if not isinstance(launcher, list) or not launcher or not all(isinstance(item, str) for item in launcher):
        raise HeudiconvBootstrapError("[heudiconv].launcher must be a non-empty list of strings.")

    return tuple(launcher)


def _guard_reset_requirement(plan: BootstrapPlan, reset: bool) -> None:
    if reset:
        return
    if plan.bootstrap_state_path.exists() or plan.heudiconv_state_path.exists():
        raise HeudiconvBootstrapError(
            "Existing HeuDiConv bootstrap state was found. Use --reset to regenerate it."
        )


def _prepare_bootstrap_directories(plan: BootstrapPlan) -> None:
    plan.raw_bids_root.mkdir(parents=True, exist_ok=True)
    plan.code_root.mkdir(parents=True, exist_ok=True)
    plan.dicominfo_root.mkdir(parents=True, exist_ok=True)
    plan.bootstrap_state_path.parent.mkdir(parents=True, exist_ok=True)
    plan.log_path.parent.mkdir(parents=True, exist_ok=True)


def _reset_bootstrap_state(project_root: Path, plan: BootstrapPlan) -> None:
    for path in (
        plan.heudiconv_state_path,
        plan.heuristic_path,
        plan.dicominfo_root,
        plan.bootstrap_state_path,
        plan.log_path,
    ):
        _remove_project_path(project_root, path)


def _remove_project_path(project_root: Path, path: Path) -> None:
    resolved_root = project_root.resolve()
    resolved_path = path.resolve()
    if not resolved_path.is_relative_to(resolved_root):
        raise HeudiconvBootstrapError(f"Refusing to remove path outside the project root: {resolved_path}")
    if not resolved_path.exists():
        return
    if resolved_path.is_dir():
        shutil.rmtree(resolved_path)
        return
    resolved_path.unlink()


def _find_latest_generated_file(root: Path, filename: str) -> Path:
    candidates = [candidate for candidate in root.rglob(filename) if candidate.is_file()]
    if not candidates:
        raise HeudiconvBootstrapError(
            f"HeuDiConv bootstrap did not generate {filename} under {root}."
        )
    return max(candidates, key=lambda candidate: candidate.stat().st_mtime_ns)


def _find_generated_dicominfo_files(root: Path) -> tuple[Path, ...]:
    candidates = [
        candidate
        for candidate in root.rglob("dicominfo*.tsv")
        if candidate.is_file()
    ]
    if not candidates:
        raise HeudiconvBootstrapError(
            f"HeuDiConv bootstrap did not generate dicominfo output under {root}."
        )
    return tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                str(candidate.parent).lower(),
                candidate.name.lower(),
            ),
        )
    )


def _copy_dicominfo_files(dicominfo_root: Path, generated_paths: tuple[Path, ...]) -> tuple[Path, ...]:
    common_parent = Path(os.path.commonpath([str(path.parent) for path in generated_paths]))
    copied_paths: list[Path] = []

    for generated_path in generated_paths:
        relative_path = generated_path.relative_to(common_parent)
        destination = dicominfo_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(generated_path, destination)
        copied_paths.append(destination)

    return tuple(copied_paths)


def _write_bootstrap_record(
    *,
    context: ProjectContext,
    plan: BootstrapPlan,
    status: str,
    exit_code: int | None = None,
    error: str | None = None,
    generated_heuristic: Path | None = None,
    generated_dicominfo_paths: tuple[Path, ...] = (),
    copied_dicominfo_paths: tuple[Path, ...] = (),
) -> None:
    plan.bootstrap_state_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "step": "bootstrap",
        "status": status,
        "recorded_at": datetime.now(UTC).isoformat(),
        "config_path": str(context.config_path),
        "project_root": str(context.project_root),
        "sample_paths": [str(path) for path in plan.sample_paths],
        "launcher": list(plan.launcher),
        "command": list(plan.command),
        "raw_bids_root": str(plan.raw_bids_root),
        "heudiconv_state_path": str(plan.heudiconv_state_path),
        "artifacts": {
            "heuristic_template": str(plan.heuristic_path),
            "dicom_inventory_dir": str(plan.dicominfo_root),
            "dicom_inventories": [str(path) for path in copied_dicominfo_paths],
        },
        "log_path": str(plan.log_path),
    }

    if generated_heuristic is not None:
        payload["generated_heuristic"] = str(generated_heuristic)
    if generated_dicominfo_paths:
        payload["generated_dicominfo"] = [str(path) for path in generated_dicominfo_paths]
    if exit_code is not None:
        payload["exit_code"] = exit_code
    if error is not None:
        payload["error"] = error

    plan.bootstrap_state_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
