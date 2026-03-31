from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class StageId(str, Enum):
    CURATE = "curate"
    VALIDATE = "validate"
    FMRIPREP = "fmriprep"
    MRIQC = "mriqc"
    XCPD = "xcpd"
    QSIPREP = "qsiprep"
    QSIRECON = "qsirecon"


@dataclass(frozen=True)
class StageSpec:
    id: StageId
    label: str
    upstream: tuple[StageId, ...] = ()
    products: tuple[str, ...] = ()
    scope: str = "participant"
    notes: str = ""


STAGES: dict[StageId, StageSpec] = {
    StageId.CURATE: StageSpec(
        id=StageId.CURATE,
        label="HeuDiConv curation",
        products=("raw BIDS dataset",),
        scope="participant/session",
        notes="Transforms sourcedata into a BIDS-aware raw dataset.",
    ),
    StageId.VALIDATE: StageSpec(
        id=StageId.VALIDATE,
        label="Validation",
        upstream=(StageId.CURATE,),
        products=("validation report",),
        scope="dataset",
        notes="Validates raw or derivative inputs before downstream execution.",
    ),
    StageId.FMRIPREP: StageSpec(
        id=StageId.FMRIPREP,
        label="fMRIPrep",
        upstream=(StageId.VALIDATE,),
        products=("fMRIPrep derivatives",),
        notes="Preprocesses BOLD/anatomical MRI and emits BIDS derivatives.",
    ),
    StageId.MRIQC: StageSpec(
        id=StageId.MRIQC,
        label="MRIQC",
        upstream=(StageId.VALIDATE,),
        products=("MRIQC reports", "IQMs"),
        notes="Runs quality assessment on raw MRI inputs.",
    ),
    StageId.XCPD: StageSpec(
        id=StageId.XCPD,
        label="XCP-D",
        upstream=(StageId.FMRIPREP,),
        products=("postprocessed functional derivatives",),
        notes="Consumes compatible fMRIPrep derivatives.",
    ),
    StageId.QSIPREP: StageSpec(
        id=StageId.QSIPREP,
        label="QSIPrep",
        upstream=(StageId.VALIDATE,),
        products=("QSIPrep diffusion derivatives",),
        notes="Preprocesses diffusion MRI and emits derivatives for reconstruction.",
    ),
    StageId.QSIRECON: StageSpec(
        id=StageId.QSIRECON,
        label="QSIRecon",
        upstream=(StageId.QSIPREP,),
        products=("reconstructed diffusion derivatives",),
        notes="Consumes compatible QSIPrep derivatives.",
    ),
}
