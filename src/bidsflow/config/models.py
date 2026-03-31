from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class ProjectConfig(BaseModel):
    name: str = "BIDSFlow project"
    root: Path = Path(".")
    bids_root: Path = Path("sourcedata/raw")
    derivatives_root: Path = Path("derivatives")


class ExecutionConfig(BaseModel):
    backend: Literal["docker", "apptainer", "native"] = "apptainer"
    work_root: Path = Path("work")
    logs_root: Path = Path("logs")
    state_root: Path = Path("state")
    max_jobs: int = Field(default=4, ge=1)


class HeudiconvConfig(BaseModel):
    enabled: bool = True
    heuristic: Path | None = None
    outdir: Path = Path("sourcedata/raw")
    converter: Literal["dcm2niix", "none"] = "dcm2niix"


class FMRIPrepConfig(BaseModel):
    enabled: bool = True
    output_spaces: list[str] = ["MNI152NLin2009cAsym:res-2", "fsLR"]
    nprocs: int = 8
    omp_nthreads: int = 4
    mem_mb: int = 32000


class MRIQCConfig(BaseModel):
    enabled: bool = True
    nprocs: int = 8
    omp_nthreads: int = 4
    mem_gb: int = 16


class XCPDConfig(BaseModel):
    enabled: bool = True
    mode: str = "linc"
    atlases: list[str] = ["Schaefer400"]
    fd_thresh: float = 0.2


class QSIPrepConfig(BaseModel):
    enabled: bool = True
    output_resolution: float | None = None
    nprocs: int = 8
    omp_nthreads: int = 4
    mem_mb: int = 32000


class QSIReconConfig(BaseModel):
    enabled: bool = True
    recon_spec: str | None = None
    nprocs: int = 8
    omp_nthreads: int = 4
    mem_mb: int = 32000


class Config(BaseModel):
    project: ProjectConfig = ProjectConfig()
    execution: ExecutionConfig = ExecutionConfig()
    heudiconv: HeudiconvConfig = HeudiconvConfig()
    fmriprep: FMRIPrepConfig = FMRIPrepConfig()
    mriqc: MRIQCConfig = MRIQCConfig()
    xcpd: XCPDConfig = XCPDConfig()
    qsiprep: QSIPrepConfig = QSIPrepConfig()
    qsirecon: QSIReconConfig = QSIReconConfig()
