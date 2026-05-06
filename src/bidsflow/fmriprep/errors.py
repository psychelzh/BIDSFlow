"""Public exceptions for the managed fMRIPrep workflow."""

from __future__ import annotations


class FmriprepInitError(Exception):
    """Raised when fMRIPrep initialization cannot be planned or written."""


class FmriprepRunError(Exception):
    """Raised when an fMRIPrep run cannot be planned, submitted, or completed."""
