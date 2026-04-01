# HeuDiConv workflow

## 1. Purpose

HeuDiConv is not only a conversion executor. In practice it also serves
as the entry point for discovering source data structure and building an
initial heuristic.

BIDSFlow should therefore model HeuDiConv as a small workflow rather
than as a single opaque command.

## 2. User-facing command name

The preferred user-facing command is:

- `heudiconv`

This is clearer than exposing the stage only as `curate`, even though
HeuDiConv still belongs to the broader curation stage category.

## 3. Subcommands

Recommended initial subcommands:

- `bootstrap`
- `check`
- `run`

### 3.1 `bootstrap`

`bootstrap` helps the user create the initial materials needed for a
real conversion workflow.

It should be designed to produce or preserve:

- a starter `heuristic.py`
- the discovery outputs that help a user edit that heuristic

This step should bias toward explicit sample selection rather than
silently choosing an arbitrary subject or session.

### 3.2 `check`

`check` verifies that the intended HeuDiConv run is structurally ready
without running the real conversion.

Expected checks include:

- source discovery succeeds
- selected subject or session exists
- heuristic file exists when required
- output location is writable
- backend or executable is available

### 3.3 `run`

`run` performs the real conversion workflow.

At the BIDSFlow layer, `run` should remain scheduler-agnostic:

- local execution runs directly
- scheduled execution submits the work and reports the job id

## 4. Scope discovery

HeuDiConv needs to discover the execution units it should process. This
should be modeled as a BIDSFlow concern rather than buried inside a
single command builder.

Recommended CLI behavior:

- `--subject-label` plus `--session-label` selects one unit
- `--subject-label` alone expands all sessions for that subject
- `--all` expands all discovered units

This pattern should inform future stage implementations as well, even if
the discovery source changes from DICOM inputs to BIDS or derivative
datasets.

## 5. Scope normalization

BIDSFlow should normalize subject and session labels before passing them
to HeuDiConv.

Examples:

- `sub-001` and `001` should normalize to the same subject label
- `ses-01` and `01` should normalize to the same session label

The command builder can then map the normalized values to HeuDiConv's
native flags without duplicating label-cleaning logic.

## 6. Config interaction

HeuDiConv should work with project configuration, but bootstrap-oriented
flows should not require a pre-existing root config file.

When config is present, it should provide defaults for:

- heuristic path
- DICOM discovery strategy
- output root
- converter selection

When config is absent, bootstrap flows may still run using explicit CLI
inputs and can help create the project defaults that will later be
stored in the root config.

## 7. Separation of builder layers

Keep the HeuDiConv implementation split into layers:

1. discover scope units
2. resolve stage defaults and CLI overrides
3. build HeuDiConv argv
4. wrap for native or container execution
5. submit through the selected scheduler if needed

This keeps HeuDiConv-specific logic compatible with the broader BIDSFlow
orchestration model.
