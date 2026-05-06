"""Flat TOML option parsing for managed fMRIPrep runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import tomllib

from .errors import FmriprepRunError


MANAGED_OPTION_KEYS = frozenset(
    {
        "participant-label",
        "work-dir",
        "w",
        "bids-database-dir",
        "fs-license-file",
    }
)


@dataclass(frozen=True)
class FmriprepOptions:
    """Parsed fMRIPrep user options and their argv representation."""

    path: Path
    values: dict[str, Any]
    argv: tuple[str, ...]


def load_fmriprep_options(path: Path) -> FmriprepOptions:
    """Load the [options] table from the fMRIPrep target config."""

    try:
        raw_config = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise FmriprepRunError(f"Invalid fMRIPrep target config TOML at {path}: {exc}") from exc
    except OSError as exc:
        raise FmriprepRunError(f"Could not read fMRIPrep target config: {path}") from exc

    extra_top_level_keys = sorted(set(raw_config).difference({"launcher", "options"}))
    if extra_top_level_keys:
        raise FmriprepRunError(
            "fMRIPrep target config only supports top-level launcher and [options]. "
            "Move option keys under [options]: " + ", ".join(extra_top_level_keys)
        )
    raw_options = raw_config.get("options", {})
    if not isinstance(raw_options, dict):
        raise FmriprepRunError(f"[options] in {path} must be a TOML table.")

    _validate_options(raw_options, path)
    return FmriprepOptions(
        path=path,
        values=raw_options,
        argv=_options_to_argv(raw_options),
    )


def _validate_options(options: dict[str, Any], path: Path) -> None:
    """Reject managed keys and non-flat or unsupported TOML values."""

    for key, value in options.items():
        if not isinstance(key, str) or not key.strip():
            raise FmriprepRunError(f"fMRIPrep option keys must be non-empty strings: {path}")
        normalized_key = key.strip()
        if normalized_key != key or normalized_key.startswith("-") or any(
            character.isspace() for character in normalized_key
        ):
            raise FmriprepRunError(
                f"Invalid fMRIPrep option key {key!r} in {path}. "
                "Use flat fMRIPrep CLI names without leading dashes."
            )
        if normalized_key in MANAGED_OPTION_KEYS:
            raise FmriprepRunError(
                f"fMRIPrep option {normalized_key!r} is managed by BIDSFlow and "
                f"must not be set in {path}."
            )
        _validate_option_value(normalized_key, value, path)


def _validate_option_value(key: str, value: Any, path: Path) -> None:
    """Validate one option value for deterministic argv conversion."""

    if isinstance(value, bool | str | int | float):
        return
    if isinstance(value, list):
        for item in value:
            if isinstance(item, bool) or not isinstance(item, str | int | float):
                raise FmriprepRunError(
                    f"fMRIPrep option {key!r} in {path} has an unsupported list item. "
                    "Use strings, integers, or floats."
                )
        return
    raise FmriprepRunError(
        f"fMRIPrep option {key!r} in {path} has unsupported value type. "
        "Use booleans, strings, numbers, or lists of strings/numbers."
    )


def _options_to_argv(options: dict[str, Any]) -> tuple[str, ...]:
    """Convert validated flat TOML options to fMRIPrep argv tokens."""

    argv: list[str] = []
    for key, value in options.items():
        flag = f"--{key}"
        if isinstance(value, bool):
            if value:
                argv.append(flag)
            continue
        if isinstance(value, list):
            if value:
                argv.append(flag)
                argv.extend(str(item) for item in value)
            continue
        argv.extend((flag, str(value)))
    return tuple(argv)
