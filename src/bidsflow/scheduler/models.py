from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from bidsflow.core.stages import StageId


@dataclass(frozen=True)
class LaunchSpec:
    stage: StageId
    participant: str | None
    job_name: str
    cwd: Path
    command: tuple[str, ...]
    stdout_path: Path
    stderr_path: Path
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SGERequestedResources:
    slots: int
    walltime: str
    memory: str
    extra_requests: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SGEPlannedSubmission:
    launch: LaunchSpec
    resources: SGERequestedResources
    script_path: Path
    script_text: str
    qsub_command: tuple[str, ...]


@dataclass(frozen=True)
class SubmittedJob:
    job_id: str
    script_path: Path
    qsub_command: tuple[str, ...]


@dataclass(frozen=True)
class SGEJobStatus:
    job_id: str
    name: str
    state: str
    owner: str | None = None
    queue_name: str | None = None
    slots: int | None = None


@dataclass(frozen=True)
class SGEAccounting:
    job_id: str
    exit_status: str | None
    failed: str | None
    wallclock: str | None
    cpu: str | None
    maxvmem: str | None
    raw_fields: dict[str, str] = field(default_factory=dict)
