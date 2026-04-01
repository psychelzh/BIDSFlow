from __future__ import annotations

import glob
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Sequence

from bidsflow.config.models import Config

CheckStatus = Literal["ready", "warning", "blocked"]
HeudiconvMode = Literal["bootstrap", "run"]


@dataclass(frozen=True)
class ScopeUnit:
    subject_label: str
    session_label: str | None = None

    @property
    def bids_subject(self) -> str:
        return format_subject_label(self.subject_label)

    @property
    def bids_session(self) -> str | None:
        if self.session_label is None:
            return None
        return format_session_label(self.session_label)

    @property
    def display_name(self) -> str:
        if self.session_label is None:
            return self.bids_subject
        return f"{self.bids_subject}/{self.bids_session}"


@dataclass(frozen=True)
class CommandSpec:
    command: tuple[str, ...]
    cwd: Path
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ReadinessResult:
    status: CheckStatus
    units: tuple[ScopeUnit, ...]
    messages: tuple[str, ...]


def normalize_subject_label(label: str) -> str:
    return _normalize_label(label, prefix="sub-", kind="subject")


def normalize_session_label(label: str) -> str:
    return _normalize_label(label, prefix="ses-", kind="session")


def format_subject_label(label: str) -> str:
    return f"sub-{normalize_subject_label(label)}"


def format_session_label(label: str) -> str:
    return f"ses-{normalize_session_label(label)}"


def discover_scope_units(
    config: Config,
    *,
    subject_label: str | None,
    session_label: str | None,
    all_units: bool,
) -> list[ScopeUnit]:
    if session_label is not None and subject_label is None:
        raise ValueError("--session-label requires --subject-label.")

    source_root = config.project.sourcedata_root
    if not source_root.exists():
        raise ValueError(f"sourcedata root does not exist: {source_root}")
    if not source_root.is_dir():
        raise ValueError(f"sourcedata root is not a directory: {source_root}")

    subject_dirs = [entry for entry in sorted(source_root.iterdir()) if entry.is_dir()]
    if not subject_dirs:
        return []

    if all_units:
        discovered: list[ScopeUnit] = []
        for discovered_subject_dir in subject_dirs:
            discovered.extend(_discover_subject_units(discovered_subject_dir))
        return discovered

    if subject_label is None:
        raise ValueError("Specify --subject-label or --all to select HeuDiConv scope.")

    subject_dir = _find_matching_dir(subject_dirs, normalize_subject_label(subject_label))
    if subject_dir is None:
        raise ValueError(f"subject not found under sourcedata root: {format_subject_label(subject_label)}")

    if session_label is not None:
        session_dirs = _candidate_session_dirs(subject_dir)
        if not session_dirs:
            raise ValueError(
                f"session requested for subject without discoverable session directories: "
                f"{format_subject_label(subject_label)}"
            )
        matched = _find_matching_dir(session_dirs, normalize_session_label(session_label))
        if matched is None:
            raise ValueError(
                f"session not found for {format_subject_label(subject_label)}: "
                f"{format_session_label(session_label)}"
            )
        return [ScopeUnit(subject_label=normalize_subject_label(subject_label), session_label=normalize_session_label(session_label))]

    units = _discover_subject_units(subject_dir)
    if units:
        return units
    return [ScopeUnit(subject_label=normalize_subject_label(subject_label))]


def build_run_command(
    config: Config,
    *,
    subject_label: str,
    session_label: str | None,
    files: Sequence[Path] = (),
) -> CommandSpec:
    heuristic = config.heudiconv.heuristic
    if heuristic is None:
        raise ValueError("heudiconv.heuristic must be set for run mode.")
    if not heuristic.exists():
        raise ValueError(f"HeuDiConv heuristic does not exist: {heuristic}")

    return _build_command(
        config,
        mode="run",
        subject_label=subject_label,
        session_label=session_label,
        files=files,
        heuristic=str(heuristic),
        converter=config.heudiconv.converter,
    )


def build_bootstrap_command(
    config: Config,
    *,
    subject_label: str,
    session_label: str | None,
    files: Sequence[Path] = (),
) -> CommandSpec:
    return _build_command(
        config,
        mode="bootstrap",
        subject_label=subject_label,
        session_label=session_label,
        files=files,
        heuristic="convertall",
        converter="none",
    )


def check_readiness(
    config: Config,
    *,
    mode: HeudiconvMode,
    subject_label: str | None,
    session_label: str | None,
    all_units: bool,
    files: Sequence[Path] = (),
) -> ReadinessResult:
    status: CheckStatus = "ready"
    messages: list[str] = []

    if not config.heudiconv.enabled:
        return ReadinessResult(
            status="blocked",
            units=(),
            messages=("heudiconv stage is disabled in the project config.",),
        )

    units: list[ScopeUnit] = []
    if files:
        if all_units:
            return ReadinessResult(
                status="blocked",
                units=(),
                messages=("--all cannot be combined with explicit --files inputs.",),
            )
        if subject_label is None:
            return ReadinessResult(
                status="blocked",
                units=(),
                messages=("--files requires --subject-label so BIDSFlow can name the output scope.",),
            )
        if session_label is not None:
            units = [ScopeUnit(normalize_subject_label(subject_label), normalize_session_label(session_label))]
        else:
            units = [ScopeUnit(normalize_subject_label(subject_label))]
        missing_files = [path for path in files if not path.exists()]
        if missing_files:
            return ReadinessResult(
                status="blocked",
                units=tuple(units),
                messages=tuple(f"input file does not exist: {path}" for path in missing_files),
            )
    else:
        try:
            units = discover_scope_units(
                config,
                subject_label=subject_label,
                session_label=session_label,
                all_units=all_units,
            )
        except ValueError as error:
            return ReadinessResult(status="blocked", units=(), messages=(str(error),))

    if not units:
        return ReadinessResult(
            status="blocked",
            units=(),
            messages=("no HeuDiConv execution units were discovered from the current selection.",),
        )

    backend_messages = _check_backend(config)
    for entry_status, text in backend_messages:
        status = _merge_status(status, entry_status)
        messages.append(text)

    if mode == "run":
        heuristic = config.heudiconv.heuristic
        if heuristic is None:
            return ReadinessResult(
                status="blocked",
                units=tuple(units),
                messages=("heudiconv.heuristic is not set; run mode requires a heuristic.py file.",),
            )
        if not heuristic.exists():
            return ReadinessResult(
                status="blocked",
                units=tuple(units),
                messages=(f"HeuDiConv heuristic does not exist: {heuristic}",),
            )

    if not files:
        template = config.heudiconv.dicom_dir_template
        if template is None:
            return ReadinessResult(
                status="blocked",
                units=tuple(units),
                messages=("heudiconv.dicom_dir_template is not set; provide a template or explicit files.",),
            )

        for unit in units:
            try:
                resolved_template = resolve_dicom_dir_template(
                    config,
                    subject_label=unit.subject_label,
                    session_label=unit.session_label,
                )
            except ValueError as error:
                return ReadinessResult(status="blocked", units=tuple(units), messages=(str(error),))

            matches = sorted(glob.glob(resolved_template))
            if not matches:
                status = _merge_status(status, "blocked")
                messages.append(f"no DICOM inputs matched for {unit.display_name}: {resolved_template}")

    outdir = config.heudiconv.outdir
    if outdir.exists() and not outdir.is_dir():
        status = _merge_status(status, "blocked")
        messages.append(f"HeuDiConv output root is not a directory: {outdir}")
    elif not outdir.exists():
        status = _merge_status(status, "warning")
        messages.append(f"HeuDiConv output root will be created on first run: {outdir}")

    if not messages:
        messages.append(f"HeuDiConv {mode} is ready for {len(units)} scope unit(s).")

    return ReadinessResult(status=status, units=tuple(units), messages=tuple(messages))


def resolve_dicom_dir_template(
    config: Config,
    *,
    subject_label: str,
    session_label: str | None,
) -> str:
    template = config.heudiconv.dicom_dir_template
    if template is None:
        raise ValueError("heudiconv.dicom_dir_template is not configured.")

    if session_label is None and ("{session" in template or "{bids_session" in template):
        raise ValueError(
            "The configured dicom_dir_template expects a session placeholder, but the selected "
            "scope has no session label."
        )

    formatted = template.format(
        subject=normalize_subject_label(subject_label),
        session="" if session_label is None else normalize_session_label(session_label),
        subject_label=normalize_subject_label(subject_label),
        session_label="" if session_label is None else normalize_session_label(session_label),
        bids_subject=format_subject_label(subject_label),
        bids_session="" if session_label is None else format_session_label(session_label),
    )
    candidate = Path(formatted)
    if candidate.is_absolute():
        return str(candidate)
    return str((config.project.sourcedata_root / candidate).resolve())


def find_bootstrap_heuristic(outdir: Path, *, subject_label: str, session_label: str | None) -> Path | None:
    return _find_latest_info_artifact(
        outdir,
        artifact_name="heuristic.py",
        subject_label=subject_label,
        session_label=session_label,
    )


def find_bootstrap_dicominfo(outdir: Path, *, subject_label: str, session_label: str | None) -> Path | None:
    return _find_latest_info_artifact(
        outdir,
        artifact_name="dicominfo.tsv",
        subject_label=subject_label,
        session_label=session_label,
    )


def _normalize_label(label: str, *, prefix: str, kind: str) -> str:
    normalized = label.strip()
    if normalized.startswith(prefix):
        normalized = normalized[len(prefix) :]
    if not normalized:
        raise ValueError(f"{kind} label cannot be empty.")
    return normalized


def _discover_subject_units(subject_dir: Path) -> list[ScopeUnit]:
    subject_label = normalize_subject_label(subject_dir.name)
    session_dirs = _candidate_session_dirs(subject_dir)
    if not session_dirs:
        return [ScopeUnit(subject_label=subject_label)]
    return [
        ScopeUnit(subject_label=subject_label, session_label=normalize_session_label(session_dir.name))
        for session_dir in session_dirs
    ]


def _candidate_session_dirs(subject_dir: Path) -> list[Path]:
    directories = [entry for entry in sorted(subject_dir.iterdir()) if entry.is_dir()]
    prefixed = [entry for entry in directories if entry.name.startswith("ses-")]
    if prefixed:
        return prefixed

    numeric = [entry for entry in directories if entry.name.isdigit()]
    if numeric and len(numeric) == len(directories):
        return directories

    return []


def _find_matching_dir(directories: Sequence[Path], normalized_label: str) -> Path | None:
    for directory in directories:
        name = directory.name
        if name.startswith("sub-"):
            current = normalize_subject_label(name)
        elif name.startswith("ses-"):
            current = normalize_session_label(name)
        else:
            current = name
        if current == normalized_label:
            return directory
    return None


def _build_command(
    config: Config,
    *,
    mode: HeudiconvMode,
    subject_label: str,
    session_label: str | None,
    files: Sequence[Path],
    heuristic: str,
    converter: Literal["dcm2niix", "none"],
) -> CommandSpec:
    tool_args: list[str] = []

    if files:
        tool_args.append("--files")
        tool_args.extend(str(path.resolve()) for path in files)
    else:
        tool_args.extend(
            [
                "-d",
                resolve_dicom_dir_template(
                    config,
                    subject_label=subject_label,
                    session_label=session_label,
                ),
            ]
        )

    tool_args.extend(["-o", str(config.heudiconv.outdir), "-f", heuristic, "-s", normalize_subject_label(subject_label)])
    if session_label is not None:
        tool_args.extend(["-ss", normalize_session_label(session_label)])
    tool_args.extend(["-c", converter])
    if mode == "run":
        tool_args.append("-b")
    if config.heudiconv.minmeta:
        tool_args.append("--minmeta")
    if config.heudiconv.overwrite:
        tool_args.append("--overwrite")
    if config.heudiconv.with_prov:
        tool_args.append("--with-prov")
    if config.heudiconv.dcmconfig is not None:
        tool_args.extend(["--dcmconfig", str(config.heudiconv.dcmconfig)])

    backend = config.execution.backend
    executable = config.heudiconv.executable
    if backend == "native":
        return CommandSpec(command=(executable, *tool_args), cwd=config.project.root)

    bind_paths = _collect_bind_paths(config, files)
    if backend == "apptainer":
        apptainer_image = config.heudiconv.apptainer_image
        if apptainer_image is None:
            raise ValueError("heudiconv.apptainer_image must be set when execution.backend=apptainer.")
        command: list[str] = ["apptainer", "exec", "--cleanenv"]
        for bind_path in bind_paths:
            command.extend(["--bind", f"{bind_path}:{bind_path}"])
        command.extend([str(apptainer_image), executable, *tool_args])
        return CommandSpec(command=tuple(command), cwd=config.project.root)

    docker_image = config.heudiconv.docker_image
    if docker_image is None:
        raise ValueError("heudiconv.docker_image must be set when execution.backend=docker.")
    command = ["docker", "run", "--rm"]
    for bind_path in bind_paths:
        command.extend(["-v", f"{bind_path}:{bind_path}"])
    command.extend([docker_image, executable, *tool_args])
    return CommandSpec(command=tuple(command), cwd=config.project.root)


def _collect_bind_paths(config: Config, files: Sequence[Path]) -> list[Path]:
    bind_paths = {
        config.project.sourcedata_root.resolve(),
        config.heudiconv.outdir.parent.resolve(),
    }
    if config.heudiconv.heuristic is not None:
        bind_paths.add(config.heudiconv.heuristic.parent.resolve())
    if config.heudiconv.dcmconfig is not None:
        bind_paths.add(config.heudiconv.dcmconfig.parent.resolve())
    for path in files:
        resolved = path.resolve()
        bind_paths.add(resolved if resolved.is_dir() else resolved.parent)
    return sorted(bind_paths, key=str)


def _check_backend(config: Config) -> list[tuple[CheckStatus, str]]:
    backend = config.execution.backend
    messages: list[tuple[CheckStatus, str]] = []

    if backend == "native":
        executable = config.heudiconv.executable
        if shutil.which(executable) is None:
            messages.append(("blocked", f"native HeuDiConv executable is not available in PATH: {executable}"))
        else:
            messages.append(("ready", f"native HeuDiConv executable resolved from PATH: {executable}"))
        return messages

    if backend == "apptainer":
        if shutil.which("apptainer") is None:
            messages.append(("blocked", "apptainer is not available in PATH."))
        apptainer_image = config.heudiconv.apptainer_image
        if apptainer_image is None:
            messages.append(("blocked", "heudiconv.apptainer_image is not configured."))
        elif not apptainer_image.exists():
            messages.append(("blocked", f"HeuDiConv Apptainer image does not exist: {apptainer_image}"))
        else:
            messages.append(("ready", f"HeuDiConv Apptainer image resolved: {apptainer_image}"))
        return messages

    if shutil.which("docker") is None:
        messages.append(("blocked", "docker is not available in PATH."))
    docker_image = config.heudiconv.docker_image
    if docker_image is None:
        messages.append(("blocked", "heudiconv.docker_image is not configured."))
    else:
        messages.append(("ready", f"HeuDiConv Docker image configured: {docker_image}"))
    return messages


def _find_latest_info_artifact(
    outdir: Path,
    *,
    artifact_name: str,
    subject_label: str,
    session_label: str | None,
) -> Path | None:
    info_root = outdir / ".heudiconv"
    if not info_root.exists():
        return None

    subject_tokens = {normalize_subject_label(subject_label), format_subject_label(subject_label)}
    session_tokens = {""}
    if session_label is not None:
        session_tokens = {normalize_session_label(session_label), format_session_label(session_label)}

    matches: list[Path] = []
    for candidate in info_root.rglob(artifact_name):
        candidate_parts = set(candidate.parts)
        if not subject_tokens.intersection(candidate_parts):
            continue
        if session_label is not None and not session_tokens.intersection(candidate_parts):
            continue
        matches.append(candidate)

    if not matches:
        return None
    matches.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return matches[0]


def _merge_status(current: CheckStatus, new: CheckStatus) -> CheckStatus:
    order = {"ready": 0, "warning": 1, "blocked": 2}
    if order[new] > order[current]:
        return new
    return current
