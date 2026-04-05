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

- HeuDiConv bootstrap and convert steps
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
bidsflow init [DIRECTORY]
bidsflow heudiconv bootstrap <sample-path>... [--config bidsflow.toml] [--reset] [--dry-run]
```

The managed `heudiconv convert` step is stubbed in the CLI but not yet
implemented.

Current bootstrap behavior:

- a single sample directory is bootstrapped with one temporary subject
  label
- multiple sample directories are split into separate single-directory
  bootstrap units and treated as temporary sessions of one placeholder
  subject

## `init` Direction

`bidsflow init` is intended to stay small.

It should:

- accept a positional target directory with `.` as the default
- write a minimal editable config file with short review comments
- optionally materialize the default layout directories when
  `--make-dirs` is requested

It should not:

- choose backend defaults
- choose scheduler defaults
- generate tool-specific configuration
- perform source scanning or execution

The initial option set should stay narrow: `--name`, `--config-name`,
`--force`, and `--make-dirs` are enough for the first pass.

## Repository State

- `docs/` contains the active design.
- `src/` and `tests/` now contain `bidsflow init` and the first managed
  `bidsflow heudiconv bootstrap` slice.
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
2. Rebuild HeuDiConv around explicit bootstrap and convert steps.
3. Rebuild `check`, `run`, and `status` around the execution model.
4. Add template-backed app runs after the core runtime stabilizes.
5. Add adapters, backends, and schedulers only after the public model
   stabilizes.
