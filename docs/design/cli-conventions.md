# CLI conventions

## 1. Purpose

BIDSFlow should expose a CLI that is easy to predict across stages while
still respecting the semantics of each underlying tool.

The CLI should therefore separate:

- user-facing command naming
- stage capability naming
- scope selection
- scheduler and backend selection
- tool-specific execution details

## 2. User-facing command names

Prefer concrete tool names over abstract stage-category names.

Examples:

- `heudiconv`
- `validate`
- `fmriprep`
- `mriqc`
- `xcpd`
- `qsiprep`
- `qsirecon`

Terms such as "curation stage" remain useful in design docs, but they
should not be the primary user-facing command when a stage is tightly
bound to a specific tool.

### 2.1 Transitional note

The current scaffold still exposes `curate`. The intended CLI direction
is to rename this surface to `heudiconv` while preserving the idea that
it belongs to the broader curation stage category.

## 3. Capability-oriented subcommands

Not every stage needs the same subcommands. BIDSFlow should expose
subcommands based on stage capability rather than forcing a fully
symmetric tree.

Recommended common capabilities:

- `check` - verify readiness without launching the underlying tool
- `run` - begin execution using the configured local or scheduled mode

Recommended stage-specific capabilities:

- `bootstrap` - create starting materials needed before a real run

Future capabilities may include:

- `inspect`
- `status`
- `cancel`

## 4. Action semantics

### 4.1 `run`

`run` means "start the stage". It should not split into separate
user-facing `run` and `submit` commands at the current project stage.

If the selected scheduler is:

- `local`, BIDSFlow executes the stage directly
- `sge`, BIDSFlow submits the stage and returns the job id

This keeps the user mental model simple: `run` starts work, regardless
of where that work is executed.

### 4.2 `--dry-run`

`--dry-run` should remain the primary execution preview surface for now.
It is sufficient for single-stage preview because it can show:

- resolved scope units
- resolved command argv
- resolved working, log, and output paths
- scheduler submission details when applicable

A dedicated `plan` command should be reserved for future cases where
BIDSFlow needs to expand and report a larger structured execution plan.

### 4.3 `check`

`check` should answer whether a stage is ready to run without launching
the stage executor.

It should cover at least:

- structural readiness
- semantic readiness
- backend readiness
- dependency or handoff readiness

Suggested outcomes:

- `ready`
- `warning`
- `blocked`

## 5. Scope parameter naming

Use BIDS-oriented scope names at the BIDSFlow layer.

Preferred flags:

- `--subject-label`
- `--session-label`
- `--all`

Compatibility aliases may include:

- `--participant-label`

Avoid exposing `--participant` as the long-term primary name.

CLI inputs may accept either prefixed or unprefixed forms:

- `sub-01` or `01`
- `ses-01` or `01`

BIDSFlow should normalize these values internally before mapping them to
tool-specific flags.

## 6. Separation of responsibilities

Keep these concerns distinct:

1. scope discovery
2. readiness checking
3. tool-specific argv construction
4. backend wrapping
5. scheduler submission

In particular, scope discovery should happen before tool-specific
command construction. A command builder should receive normalized scope
units rather than be responsible for filesystem discovery on its own.

## 7. Configuration interaction

CLI conventions should not force a project config file to exist for
every command. Some commands should be able to run without a config and
help the user bootstrap one.

Likely config-optional commands:

- `doctor`
- `init`
- `heudiconv bootstrap`

Likely config-backed commands:

- `run`
- `check`

The exact command set may evolve, but the default bias should be:

- use config for project defaults and reproducibility
- do not require config where it creates unnecessary friction
