# BIDSFlow

## Design Reset

BIDSFlow is being reset around a **task-first, logistics-oriented**
command line model for BIDS workflow work.

The historical implementation and stage-first design notes were removed
on purpose from this branch because they encoded the wrong abstraction
boundary. The repository currently serves as a design workspace for the
next implementation pass.

## Core Direction

- Users should express **what task** they want to perform first.
- BIDSFlow should own run logistics: inputs, outputs, logs, state,
  resumability, and downstream handoff.
- Multi-stage workflows such as HeuDiConv should be managed explicitly.
- Most other tools can stay template-driven instead of being deeply
  re-wrapped.
- Adapters, backends, and schedulers should stay behind the public CLI
  surface.

## Proposed CLI Surface

```bash
bidsflow init [DIRECTORY]
bidsflow check <target>
bidsflow run <target>
bidsflow status [<target>]
```

Representative managed work:

- HeuDiConv source review, draft heuristic generation, and managed runs
- validation and app-backed runs that consume recorded artifacts
- template-backed jobs such as `fmriprep`, `mriqc`, and `xcpd`

This keeps BIDSFlow's public language centered on workflow logistics
instead of turning the package into a large wrapper around tool-native
flags.

Additional commands such as `doctor`, `config`, or `source` can return
later if they grow into stable user-facing tasks. They are intentionally
deferred from the first rebuilt CLI.

## Current Implemented Slice

```bash
bidsflow init [DIRECTORY] [--scheduler auto|none|sge]
bidsflow sources [--reset] [--dry-run]
bidsflow heudiconv --draft <sample-path>... [--reset] [--dry-run]
bidsflow heudiconv [--dry-run]
```

Current `sources` behavior:

- `sources` scans the immediate child directories under the configured
  `source_root`
- `sources` does not call HeuDiConv
- by default it writes a review table with empty final label columns
- if `[sources].template` is configured, it derives final
  `subject_label/session_label` directly from `source_name`
- if `[sources].command` is configured, it calls that
  project-owned command with `source_name` and expects one stdout line
  for `subject_label`, plus an optional second line for `session_label`
- `state/sources.tsv` is the project-owned truth source for later
  `bidsflow heudiconv` execution
- `state/sources.json` records only overall metadata and artifact paths
- stdout includes a summary of `ready`, `needs_review`, `collision`,
  `missing_source`, and `excluded` rows

Current `heudiconv --draft` behavior:

- a single sample directory is processed as one draft unit with one
  temporary subject label
- multiple sample directories are split into separate single-directory
  draft units and treated as temporary sessions of one placeholder
  subject
- relative `sample-path` arguments are interpreted under the configured
  `source_root`
- absolute `sample-path` arguments are only accepted when they still
  resolve under that same `source_root`
- draft generation uses an isolated work root under
  `work/heudiconv/draft-work/` instead of writing into the real raw BIDS
  output directory
- draft generation writes the generated heuristic to
  `[heudiconv].heuristic`, which defaults to
  `code/heudiconv/heuristic.py`
- `state/heudiconv/draft.json` records only overall metadata and
  artifact paths
- each draft unit's tool output is written to
  `logs/heudiconv/draft-<attempt>/<unit>.log`
- `heudiconv --draft` and `sources` are parallel preparation steps;
  neither is a strict prerequisite for the other
- in many real projects, draft generation happens first because
  heuristic work must start before final naming is frozen

Current `bidsflow heudiconv` behavior:

- `heudiconv` reads `state/sources.tsv` and recomputes row
  status from the current table contents instead of trusting a stale
  `status` column
- it requires every included row to be `ready`
- it resolves each `source_name` back under the configured `source_root`
- it uses `[heudiconv].heuristic` and `[heudiconv].launcher`
- it materializes a temporary execution view under
  `work/heudiconv/run-<attempt>/`
- it runs one managed HeuDiConv invocation per ready source row
- `state/heudiconv/run.json` records only overall metadata and artifact
  paths
- `state/heudiconv/run.tsv` records the final status of each unit
- each unit's tool output is written to
  `logs/heudiconv/run-<attempt>/<source_name>.log`, with paths
  recorded in `run.tsv`
- the temporary execution view is removed after success or failure

Current run limit:

- automatic persisted `BIDSLayout` indexing is still not implemented

## `init` Direction

`bidsflow init` is intended to stay small.

It should:

- accept a positional target directory with `.` as the default
- write a minimal editable config file with short review comments
- scaffold an explicit `[execution]` scheduler choice
- optionally materialize the default layout directories when
  `--make-dirs` is requested

`--scheduler auto` is the default and currently only checks whether
`qsub` is available. If SGE is detected, `init` writes active SGE
settings; otherwise it writes `scheduler = "none"` and leaves scheduler
template settings commented.

It should not:

- choose backend defaults
- generate scheduler script templates
- generate tool-specific configuration
- perform source scanning or execution

The initial option set stays narrow: `--name`, `--force`,
`--make-dirs`, and `--scheduler`.

## Repository State

- `docs/` contains the active design.
- `src/` and `tests/` now contain `bidsflow init`, `bidsflow sources`,
  and the first managed `bidsflow heudiconv` slices.
- The rest of the historical implementation remains intentionally
  removed until the execution model is rebuilt cleanly.

## Active Design Docs

- [Execution model](docs/design/execution-model.md)
- [HeuDiConv workflow](docs/design/heudiconv-workflow.md)
- [Task-first CLI](docs/design/task-first-cli.md)
- [Project initialization](docs/design/project-init.md)
- [Config reference](docs/design/config.md)
- [Handoff contract](docs/design/handoff-contract.md)

## Next Implementation Milestones

1. Define artifact records, run records, and managed workflow state.
2. Rebuild HeuDiConv around explicit source review, draft generation,
   and managed run steps.
3. Rebuild `check`, `run`, and `status` around the execution model.
4. Add template-backed app runs after the core runtime stabilizes.
5. Add adapters, backends, and schedulers only after the public model
   stabilizes.
