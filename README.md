# BIDSFlow

## Design Reset

BIDSFlow is being reset around a **task-first, target-oriented** command
line model for BIDS workflow logistics.

The historical implementation and stage-first design notes were removed
on purpose from this branch because they encoded the wrong abstraction
boundary. The repository currently serves as a design workspace for the
next implementation pass.

## Core Direction

- Users should express **what task** they want to perform first.
- Tasks should operate on **targets** such as `curate`, `validate`,
  `fmriprep`, or `xcpd`.
- Some targets are backed by BIDS Apps, while others are BIDSFlow-owned
  workflow targets.
- Adapters, backends, and schedulers should stay behind the public CLI
  surface.

## Proposed CLI Surface

```bash
bidsflow init [DIRECTORY]
bidsflow doctor
bidsflow config validate --config bidsflow.toml

bidsflow source bootstrap
bidsflow source scan
bidsflow source link

bidsflow check <target>
bidsflow run <target>
bidsflow status [<target>]
```

Representative targets:

- `curate`
- `validate`
- `fmriprep`
- `mriqc`
- `xcpd`
- `qsiprep`
- `qsirecon`

This keeps BIDSFlow's public language centered on workflow logistics,
while still allowing app-backed targets to be explicit and visible.

## `init` Direction

`bidsflow init` is intended to stay small.

It should:

- accept a positional target directory with `.` as the default
- create a minimal project scaffold
- write a minimal editable config file with short review comments

It should not:

- choose backend defaults
- choose scheduler defaults
- generate tool-specific configuration
- perform source scanning or execution

The initial option set should stay narrow: `--name`, `--config-name`,
and `--force` are enough for the first pass.

## Repository State

- `docs/` contains the active design.
- `src/` and `tests/` now contain only the first rebuilt command:
  `bidsflow init`.
- The rest of the historical implementation remains intentionally
  removed until the target model is rebuilt cleanly.

## Active Design Docs

- [Target model](docs/design/target-model.md)
- [Task-first CLI](docs/design/task-first-cli.md)
- [Project initialization](docs/design/project-init.md)
- [Config reference](docs/design/config.md)
- [Handoff contract](docs/design/handoff-contract.md)

## Next Implementation Milestones

1. Define a target registry and request model.
2. Rebuild `check`, `run`, and `status` around targets.
3. Add adapters, backends, and schedulers only after the public model
   stabilizes.
