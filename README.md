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
bidsflow config validate --config project.toml

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
- write a minimal editable config file

It should not:

- choose backend defaults
- choose scheduler defaults
- generate tool-specific configuration
- perform source scanning or execution

The initial option set should stay narrow: `--name`, `--config-name`,
and `--force` are enough for the first pass.

## Repository State

- `docs/` contains the active design.
- The previous implementation under `src/` and `tests/` has been
  intentionally removed.

## Active Design Docs

- [Target model](docs/design/target-model.md)
- [Task-first CLI](docs/design/task-first-cli.md)
- [Project initialization](docs/design/project-init.md)
- [Handoff contract](docs/design/handoff-contract.md)

## Next Implementation Milestones

1. Implement `init` as a minimal scaffold command.
2. Define a target registry and request model.
3. Rebuild `check`, `run`, and `status` around targets.
4. Add adapters, backends, and schedulers only after the public model
   stabilizes.
