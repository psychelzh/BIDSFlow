"""HeuDiConv workflow exceptions."""

from __future__ import annotations


class HeudiconvInitError(Exception):
    """Raised when HeuDiConv support files cannot be initialized."""


class HeudiconvRunError(Exception):
    """Raised when HeuDiConv planning, submission, or execution cannot continue."""
