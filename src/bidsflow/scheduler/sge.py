from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from bidsflow.config.models import Config, SGEConfig
from bidsflow.core.stages import STAGES, StageId
from bidsflow.scheduler.models import (
    LaunchSpec,
    SGEAccounting,
    SGEJobStatus,
    SGEPlannedSubmission,
    SGERequestedResources,
    SubmittedJob,
)


def _slugify(value: str) -> str:
    allowed = [char if char.isalnum() or char in {"-", "_", "."} else "-" for char in value]
    normalized = "".join(allowed).strip("-")
    return normalized or "job"


def build_stage_launch_spec(config_path: Path, config: Config, stage: StageId, participant: str | None) -> LaunchSpec:
    log_dir = config.execution.logs_root / "sge" / stage.value
    job_name_parts = ["bidsflow", stage.value]
    if participant:
        job_name_parts.append(participant)
    job_name = _slugify("-".join(job_name_parts))

    stdout_path = log_dir / f"{job_name}.out"
    stderr_path = log_dir / f"{job_name}.err"

    command = [
        sys.executable,
        "-m",
        "bidsflow.cli",
        stage.value,
        "--config",
        str(config_path.resolve()),
    ]
    if participant:
        command.extend(["--participant", participant])

    return LaunchSpec(
        stage=stage,
        participant=participant,
        job_name=job_name,
        cwd=config.project.root,
        command=tuple(command),
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )


class SGECliScheduler:
    def __init__(self, config: SGEConfig) -> None:
        self.config = config

    @staticmethod
    def _require_command(name: str) -> None:
        if shutil.which(name) is None:
            raise RuntimeError(f"{name} is not available in PATH for SGE operations.")

    @staticmethod
    def _run_command(command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )

    def requested_resources(self) -> SGERequestedResources:
        defaults = self.config.default_resources
        return SGERequestedResources(
            slots=defaults.slots,
            walltime=defaults.walltime,
            memory=defaults.memory,
            extra_requests=dict(self.config.extra_requests),
        )

    def plan_stage_submission(
        self,
        *,
        config_path: Path,
        config: Config,
        stage: StageId,
        participant: str | None,
        hold_jid: str | None = None,
    ) -> SGEPlannedSubmission:
        launch = build_stage_launch_spec(config_path=config_path, config=config, stage=stage, participant=participant)
        resources = self.requested_resources()
        script_path = config.execution.state_root / "sge" / f"{launch.job_name}.sh"
        script_text = self.render_script(launch)
        qsub_command = self.build_qsub_command(
            launch=launch,
            resources=resources,
            script_path=script_path,
            hold_jid=hold_jid,
        )
        return SGEPlannedSubmission(
            launch=launch,
            resources=resources,
            script_path=script_path,
            script_text=script_text,
            qsub_command=qsub_command,
        )

    def render_script(self, launch: LaunchSpec) -> str:
        lines = [
            "#!/bin/sh",
            "set -eu",
            f"cd {shlex.quote(str(launch.cwd))}",
        ]
        for key, value in sorted(launch.env.items()):
            lines.append(f"export {key}={shlex.quote(value)}")
        lines.append(f"exec {shlex.join(launch.command)}")
        return "\n".join(lines) + "\n"

    def build_qsub_command(
        self,
        *,
        launch: LaunchSpec,
        resources: SGERequestedResources,
        script_path: Path,
        hold_jid: str | None = None,
    ) -> tuple[str, ...]:
        command: list[str] = [
            "qsub",
            "-terse",
            "-N",
            launch.job_name,
            "-o",
            str(launch.stdout_path),
            "-e",
            str(launch.stderr_path),
        ]

        if self.config.inherit_cwd:
            command.append("-cwd")
        if self.config.export_env:
            command.append("-V")
        if self.config.queue:
            command.extend(["-q", self.config.queue])
        if self.config.project:
            command.extend(["-P", self.config.project])
        if self.config.parallel_environment:
            command.extend(["-pe", self.config.parallel_environment, str(resources.slots)])
        if hold_jid:
            command.extend(["-hold_jid", hold_jid])

        resource_specs = [
            f"{self.config.resource_map.walltime}={resources.walltime}",
            f"{self.config.resource_map.memory}={resources.memory}",
        ]
        resource_specs.extend(f"{key}={value}" for key, value in sorted(resources.extra_requests.items()))
        command.extend(["-l", ",".join(resource_specs)])
        command.append(str(script_path))
        return tuple(command)

    def write_script(self, plan: SGEPlannedSubmission) -> None:
        plan.launch.stdout_path.parent.mkdir(parents=True, exist_ok=True)
        plan.launch.stderr_path.parent.mkdir(parents=True, exist_ok=True)
        plan.script_path.parent.mkdir(parents=True, exist_ok=True)
        plan.script_path.write_text(plan.script_text, encoding="utf-8")

    def submit(self, plan: SGEPlannedSubmission) -> SubmittedJob:
        self._require_command("qsub")
        self.write_script(plan)
        completed = self._run_command(plan.qsub_command)
        job_id = self.parse_job_id(completed.stdout)
        return SubmittedJob(
            job_id=job_id,
            script_path=plan.script_path,
            qsub_command=plan.qsub_command,
        )

    def build_qdel_command(self, job_id: str) -> tuple[str, ...]:
        return ("qdel", job_id)

    def build_qstat_command(self) -> tuple[str, ...]:
        return ("qstat", "-xml")

    def build_qacct_command(self, job_id: str) -> tuple[str, ...]:
        return ("qacct", "-j", job_id)

    def cancel(self, job_id: str) -> tuple[str, ...]:
        self._require_command("qdel")
        command = self.build_qdel_command(job_id)
        self._run_command(command)
        return command

    def status(self, job_id: str) -> SGEJobStatus | None:
        self._require_command("qstat")
        completed = self._run_command(self.build_qstat_command())
        return self.parse_qstat_xml(completed.stdout, job_id=job_id)

    def accounting(self, job_id: str) -> SGEAccounting | None:
        self._require_command("qacct")
        try:
            completed = self._run_command(self.build_qacct_command(job_id))
        except subprocess.CalledProcessError as error:
            stderr = (error.stderr or "").strip().lower()
            stdout = (error.stdout or "").strip().lower()
            if "no jobs running since startup" in stdout or "no such file or directory" in stderr:
                return None
            raise
        return self.parse_qacct_output(completed.stdout, job_id=job_id)

    @staticmethod
    def parse_job_id(output: str) -> str:
        stripped = output.strip()
        if not stripped:
            raise ValueError("qsub output did not contain a job id.")
        return stripped.split()[0]

    @staticmethod
    def parse_qstat_xml(output: str, *, job_id: str) -> SGEJobStatus | None:
        root = ET.fromstring(output)
        for job in root.findall(".//job_list"):
            parsed_job_id = (job.findtext("JB_job_number") or "").strip()
            if parsed_job_id != job_id:
                continue
            slots_text = (job.findtext("slots") or "").strip()
            slots = int(slots_text) if slots_text.isdigit() else None
            return SGEJobStatus(
                job_id=parsed_job_id,
                name=(job.findtext("JB_name") or "").strip(),
                state=(job.findtext("state") or "").strip(),
                owner=(job.findtext("JB_owner") or "").strip() or None,
                queue_name=(job.findtext("queue_name") or "").strip() or None,
                slots=slots,
            )
        return None

    @staticmethod
    def parse_qacct_output(output: str, *, job_id: str) -> SGEAccounting | None:
        stripped = output.strip()
        lowered = stripped.lower()
        if not stripped or "not found" in lowered:
            return None

        fields: dict[str, str] = {}
        for line in stripped.splitlines():
            if not line.strip():
                continue
            if line.startswith("==="):
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            key, value = parts
            fields[key] = value.strip()

        if not fields:
            return None

        return SGEAccounting(
            job_id=fields.get("jobnumber", job_id),
            exit_status=fields.get("exit_status"),
            failed=fields.get("failed"),
            wallclock=fields.get("ru_wallclock"),
            cpu=fields.get("cpu"),
            maxvmem=fields.get("maxvmem"),
            raw_fields=fields,
        )


def build_stage_choices() -> tuple[StageId, ...]:
    return tuple(STAGES.keys())
