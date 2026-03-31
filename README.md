# BIDSFlow

## A Python CLI Orchestrator for BIDS Apps

BIDSFlow is an extensible Python CLI toolkit for orchestrating staged
neuroimaging workflows across BIDS Apps. It is designed to support
**stepwise execution**, **execution logistics**, and **reliable handoffs**
between workflow stages, rather than hiding everything behind a single
black-box command.

The initial focus is on the following tools:

- HeuDiConv
- fMRIPrep
- MRIQC
- XCP-D
- QSIPrep
- QSIRecon

## Positioning

BIDSFlow is **not** intended to reimplement the scientific logic of
existing BIDS Apps. Instead, it is intended to provide the infrastructure
around them:

- stage-oriented CLI execution
- configuration management
- container/backend abstraction
- cluster scheduler abstraction
- state tracking and resumability
- stage-to-stage contract validation
- path and derivative organization
- provenance capture and auditability

## Design principles

1. **Staged orchestration, not one-shot automation**
   Users should run and inspect each stage explicitly.
2. **BIDS as the primary data contract**
   Raw and derivative datasets must remain BIDS-aware.
3. **Reliable handoffs between stages**
   Downstream stages should receive validated inputs and explicit metadata.
4. **Containers first**
   Docker and Apptainer/Singularity should be first-class backends.
5. **Subject-level execution and recovery**
   The natural execution unit is typically `participant × stage`.

## Non-goals

At the current stage, BIDSFlow is **not** intended to:

- provide a single default command that silently runs the whole pipeline
  end-to-end
- replace the native CLIs of HeuDiConv, fMRIPrep, MRIQC, XCP-D, QSIPrep,
  or QSIRecon
- conceal intermediate outputs, logs, or failure states

## Planned command structure

```bash
bidsflow init
bidsflow doctor
bidsflow validate
bidsflow curate
bidsflow fmriprep
bidsflow mriqc
bidsflow xcpd
bidsflow qsiprep
bidsflow qsirecon
bidsflow status
```

A future `plan` or `run` command may coordinate a declared sequence of
stages, but it should remain explicit and inspectable rather than opaque.

Initial cluster support should target SGE-style schedulers, with
Debian-packaged Son of Grid Engine as the first concrete environment.
Scheduler selection should remain separate from native, Docker, or
Apptainer execution backends.

## Repository layout

```text
BIDSFlow/
├─ README.md
├─ pyproject.toml
├─ .gitignore
├─ src/
│  └─ bidsflow/
│     ├─ __init__.py
│     ├─ cli.py
│     ├─ config/
│     │  └─ models.py
│     └─ core/
│        └─ stages.py
├─ docs/
│  └─ design/
│     ├─ stage-model.md
│     └─ handoff-contract.md
├─ examples/
│  └─ project.toml
└─ .github/
   └─ workflows/
      └─ ci.yml
```

## Current development status

This scaffold establishes the **project boundary**, **stage model**,
**handoff contract**, and a **minimal CLI skeleton**. The next
implementation milestones should focus on:

1. configuration parsing and normalization
2. stage registry and dependency validation
3. scheduler runners (SGE first, SLURM later)
4. backend runners (Docker, Apptainer, native)
5. stage-specific command builders
6. state tracking and resumability

## Design documents

- [Stage model](docs/design/stage-model.md)
- [Handoff contract](docs/design/handoff-contract.md)

## Development

```bash
python -m pip install -e .[dev]
bidsflow --help
```

## Example

```bash
bidsflow init --path .
bidsflow doctor
bidsflow validate --config examples/project.toml
bidsflow curate --config examples/project.toml --participant sub-001
```
