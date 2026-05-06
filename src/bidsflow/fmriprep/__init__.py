"""Public fMRIPrep workflow API for BIDSFlow."""

from __future__ import annotations

from ..common import format_command
from .errors import FmriprepInitError, FmriprepRunError
from .init import FmriprepInitPlan, FmriprepInitResult, plan_fmriprep_init, run_fmriprep_init
from .participants import (
    ParticipantEntry,
    ParticipantsPlan,
    list_participants_review_issues,
    summarize_participants,
)
from .run import (
    FmriprepRunPlan,
    FmriprepRunResult,
    FmriprepRunUnitPlan,
    FmriprepRunUnitResult,
    plan_fmriprep_run,
    preview_fmriprep_unit_selection,
    run_fmriprep,
)
from .status import FmriprepStatus, FmriprepUnitStatus, get_fmriprep_status

__all__ = [
    "FmriprepInitError",
    "FmriprepInitPlan",
    "FmriprepInitResult",
    "FmriprepRunError",
    "FmriprepRunPlan",
    "FmriprepRunResult",
    "FmriprepRunUnitPlan",
    "FmriprepRunUnitResult",
    "FmriprepStatus",
    "FmriprepUnitStatus",
    "ParticipantEntry",
    "ParticipantsPlan",
    "format_command",
    "get_fmriprep_status",
    "list_participants_review_issues",
    "plan_fmriprep_init",
    "plan_fmriprep_run",
    "preview_fmriprep_unit_selection",
    "run_fmriprep",
    "run_fmriprep_init",
    "summarize_participants",
]
