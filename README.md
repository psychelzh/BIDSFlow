# BIDSFlow

[![CI](https://github.com/psychelzh/BIDSFlow/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/psychelzh/BIDSFlow/actions/workflows/ci.yml)
[![codecov](https://codecov.io/github/psychelzh/BIDSFlow/graph/badge.svg?branch=main)](https://app.codecov.io/github/psychelzh/BIDSFlow)

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
   The natural execution unit is typically `subject × stage`.

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
bidsflow config validate
bidsflow heudiconv bootstrap
bidsflow heudiconv check
bidsflow heudiconv run
bidsflow validate
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
│     │  ├─ load.py
│     │  └─ models.py
│     ├─ core/
│        └─ stages.py
│     ├─ stages/
│     │  ├─ __init__.py
│     │  └─ heudiconv.py
│     └─ scheduler/
│        ├─ __init__.py
│        ├─ models.py
│        └─ sge.py
├─ docs/
│  └─ design/
│     ├─ cli-conventions.md
│     ├─ config-strategy.md
│     ├─ heudiconv-workflow.md
│     ├─ stage-model.md
│     └─ handoff-contract.md
├─ examples/
│  └─ project.toml
├─ tests/
│  ├─ test_config_load.py
│  ├─ test_heudiconv.py
│  └─ test_scheduler_sge.py
├─ .codex/
│  └─ skills/
│     ├─ project-config-schema/
│     ├─ bids-app-command-builder/
│     └─ cluster-runner-sge/
└─ .github/
   └─ workflows/
      └─ ci.yml
```

## Current development status

This scaffold establishes the **project boundary**, **stage model**,
**handoff contract**, a working **HeuDiConv stage**, and a still-evolving
CLI surface for the remaining stages. The current SGE work also
includes config loading plus stage-level `--dry-run` preview and
submission through the configured scheduler. The next implementation
milestones should focus on:

1. configuration parsing and normalization
2. stage registry and dependency validation
3. scheduler runners (SGE first, SLURM later)
4. backend runners (Docker, Apptainer, native)
5. stage-specific command builders beyond HeuDiConv
6. state tracking and resumability

The design docs linked below capture both the implemented HeuDiConv
workflow and the broader conventions intended to guide later stage
implementation.

## Design documents

- [Stage model](docs/design/stage-model.md)
- [Handoff contract](docs/design/handoff-contract.md)
- [CLI conventions](docs/design/cli-conventions.md)
- [Configuration strategy](docs/design/config-strategy.md)
- [HeuDiConv workflow](docs/design/heudiconv-workflow.md)
- [SGE site configuration](docs/setup/sge-site-config.md)

## Development

```bash
python -m pip install -e .[dev]
python --version  # Python 3.11+
bidsflow --help
```

## Example

```bash
bidsflow init --path .
bidsflow doctor
bidsflow config validate --config examples/project.toml
bidsflow heudiconv check \
  --config examples/project.toml \
  --subject-label sub-001 \
  --session-label ses-01
bidsflow heudiconv run \
  --config examples/project.toml \
  --subject-label sub-001 \
  --session-label ses-01 \
  --dry-run
```
