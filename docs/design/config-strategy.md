# Configuration strategy

## 1. Purpose

BIDSFlow needs project-level configuration, but it should not force a
config file to be the starting point for every workflow.

This document defines the intended role of project configuration,
command-line overrides, and external tool-specific artifacts.

## 2. Design goals

The configuration model should remain:

- reproducible
- inspectable
- small enough to read comfortably
- expressive enough for real projects
- compatible with stage-specific tool semantics

## 3. Root project config

BIDSFlow should have one canonical root project config file.

Recommended long-term filename:

- `bidsflow.toml`

The current scaffold uses `examples/project.toml` as an example shape.
The final filename may change, but the role should remain stable.

The root config should hold:

- project paths and dataset roots
- execution defaults
- scheduler defaults
- stage-level high-frequency defaults

It should act as the canonical source for project defaults and
provenance snapshots.

## 4. Config is important, not universal

Project configuration should not be treated as a universal precondition
for every command.

Commands can be grouped into two broad classes.

### 4.1 Config-optional commands

These commands can help users discover state or bootstrap a project
before a canonical config exists.

Examples:

- `doctor`
- `init`
- `heudiconv bootstrap`
- future `inspect`-style commands

### 4.2 Config-backed commands

These commands generally benefit from project defaults, reproducibility,
and stable output layout.

Examples:

- `run`
- `check`
- scheduler-backed execution

## 5. One root config, many referenced artifacts

The preferred model is:

- one root BIDSFlow config
- multiple referenced stage or tool artifacts

This is better than either extreme:

- one giant TOML that mirrors every tool flag
- one separate TOML per stage with no clear project entry point

## 6. What belongs in the root config

Put these kinds of settings in the root config:

- project roots
- execution backend defaults
- scheduler defaults
- stage defaults that are high-frequency and stable
- paths to external stage-specific files

Examples:

- `heudiconv.heuristic`
- `heudiconv.dicom_dir_template`
- `fmriprep.output_spaces`
- `fmriprep.mem_mb`

## 7. What should stay external

Do not force every tool-native artifact into TOML.

Keep naturally independent artifacts in their own formats and reference
them by path from the root config.

Examples:

- HeuDiConv `heuristic.py`
- `dcm2niix` configuration files
- future BIDS filter files
- future plugin or site-specific scheduler snippets

This keeps the root config readable and preserves the native structure
of tool-specific content.

## 8. Parameter classes

Settings should be thought of in four classes.

### 8.1 BIDSFlow-wide CLI parameters

Examples:

- `--config`
- `--backend`
- `--scheduler`
- `--dry-run`
- `--subject-label`
- `--session-label`
- `--all`

### 8.2 Subcommand-fixed semantics

These should be defined by the action being performed rather than by a
user-set config key.

Examples:

- `heudiconv bootstrap` generates heuristic starting materials
- `heudiconv run` performs a real conversion
- `check` does not launch the stage executor

### 8.3 Stage-level defaults

These are the main candidates for root config fields.

Examples:

- HeuDiConv heuristic path
- DICOM discovery template
- output directory
- converter default

### 8.4 Advanced escape hatches

Some rare tool-specific flags may not deserve first-class schema fields
immediately. These should be added conservatively and only when real use
cases justify them.

Typed schema fields should remain the default preference.

## 9. Resolution order

When multiple sources define a value, use this order:

1. subcommand-fixed behavior
2. explicit CLI argument
3. root project config
4. underlying tool default

This order should be documented and kept consistent across stages.

## 10. Schema guidance

When adding config fields:

- prefer typed, explicit fields
- keep stage blocks small
- avoid mirroring an entire upstream CLI without evidence
- favor file references over embedding complex native formats

The goal is semantic completeness for project orchestration, not
one-to-one replication of every upstream tool flag.
