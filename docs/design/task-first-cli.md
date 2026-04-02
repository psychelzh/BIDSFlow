# Task-first CLI

## 1. Purpose

BIDSFlow should present itself as a **workflow logistics tool**, not as
a branded launcher for individual BIDS Apps.

The CLI should therefore describe:

- what logistics task the user wants to perform
- what dataset scope the task applies to
- what internal stage or workflow contract is affected

It should not require the user to think in terms of native tool names
unless that tool identity is scientifically meaningful.

## 2. Problem with tool-first commands

A tool-first surface such as:

- `bidsflow heudiconv`
- `bidsflow fmriprep`
- `bidsflow qsiprep`

creates several problems:

1. It makes BIDSFlow appear to be a thin wrapper around external CLIs.
2. It mixes BIDSFlow logistics semantics with native tool semantics.
3. It encourages direct exposure of tool-specific flags.
4. It weakens the package's own identity and long-term API stability.
5. It makes command growth follow vendor tools rather than workflow
   responsibilities.

For BIDSFlow, these are the wrong incentives.

## 3. Principle

Top-level commands should describe **BIDSFlow-owned logistics tasks**.

Examples:

- initialize a project
- inspect execution prerequisites
- bootstrap source interpretation
- discover source units
- create normalized links
- check readiness for a stage
- run a stage
- inspect status

In this model:

- stages remain important internally
- tools remain important internally
- neither needs to define the top-level public CLI

## 4. Recommended command surface

Recommended near-term command structure:

```bash
bidsflow init
bidsflow doctor
bidsflow config validate

bidsflow source bootstrap
bidsflow source scan
bidsflow source link

bidsflow check --stage curate
bidsflow run --stage curate
bidsflow status --stage curate
```

Possible later additions:

- `bidsflow report`
- `bidsflow watch`
- `bidsflow cancel`

## 5. Meaning of each command family

### 5.1 `source`

`source` owns source-data logistics before a formal stage run.

Suggested responsibilities:

- `bootstrap`
  Generate starter interpretation materials from a representative input
  sample.
- `scan`
  Discover raw source units and emit a manifest-like inventory.
- `link`
  Create normalized project-facing source views such as symlink trees.

This is a better public surface for HeuDiConv-backed source work than
publishing `heudiconv` directly as a top-level command.

### 5.2 `check`

`check` answers whether a stage is ready to run.

Suggested dimensions:

- structural readiness
- semantic readiness
- backend readiness
- upstream handoff readiness

### 5.3 `run`

`run` starts a stage execution using the configured backend and
scheduler.

It does not promise local execution specifically.

### 5.4 `status`

`status` reports state for a stage, scope unit, or workflow artifact.

## 6. Public parameters should remain logistics-oriented

The public CLI should expose BIDSFlow semantics, not native BIDS App
flags.

Good public parameter categories:

- scope selection
  - `--subject-label`
  - `--session-label`
  - `--all`
- source logistics
  - sample path
  - source template
  - manifest path
  - links root
- execution logistics
  - `--config`
  - `--backend`
  - `--scheduler`
  - `--dry-run`
- project artifacts
  - heuristic path
  - output roots
  - logs or state roots

Bad public parameter categories:

- native HeuDiConv flags such as `-d`, `-s`, `-ss`, `-f`
- native fMRIPrep or XCP-D flags
- direct mirrors of tool-specific converter options

If BIDSFlow only provides logistics, then the public CLI should only
describe logistics.

## 7. Internal layering

The task-first CLI does not remove stages or adapters. It clarifies
their roles.

Recommended internal layering:

1. CLI task commands
2. task request objects
3. stage workflow services
4. tool adapters
5. backend wrappers
6. scheduler submission

Important boundary:

- CLI talks in BIDSFlow semantics
- adapters talk in tool semantics

## 8. Example workflow

An intended source-to-curation flow might look like:

```bash
bidsflow source bootstrap --sample-path /data/source/sub041_session1
bidsflow source scan --source-template "/data/source/TJNU_WQ_CAMP_SUB{subject}_*_{session}"
bidsflow source link --manifest code/source-manifest.tsv
bidsflow check --stage curate --subject-label 041 --session-label 01
bidsflow run --stage curate --subject-label 041 --session-label 01
```

The underlying adapter may use HeuDiConv, but the user is interacting
with BIDSFlow's workflow concepts rather than with HeuDiConv's native
CLI.

## 9. Consequences for future implementation

This direction implies:

1. The current stage-named public CLI should be treated as transitional.
2. HeuDiConv should move behind `source` tasks and internal curation
   adapters.
3. Future stages should be selected through task semantics such as
   `check` and `run`, not by promoting every external tool to a top-level
   command.
4. Configuration should prefer BIDSFlow concepts over copied native tool
   flags.

## 10. Summary

BIDSFlow should own the user-facing workflow language.

Top-level commands should describe logistics tasks, while stage and tool
details remain explicit but internal. This keeps the package simpler,
more stable, and more obviously distinct from the tools it orchestrates.
