---
name: cluster-runner-sge
description: Design and implement SGE execution for BIDSFlow, with Debian-packaged Son of Grid Engine as the first target. Use when adding `qsub` submission, translating execution settings into SGE resources, handling `qstat` or `qacct` state checks, wiring dependencies, organizing logs, or supporting optional DRMAA1 integration for participant-level execution units.
---

# Cluster Runner Sge

Translate BIDSFlow execution units into reliable SGE submissions while
keeping scheduler concerns separate from BIDS App argument construction.

## Quick Start

Start by reading these files:

- `src/bidsflow/cli.py`
- `src/bidsflow/config/models.py`
- `src/bidsflow/core/stages.py`
- `docs/design/stage-model.md`
- `docs/design/handoff-contract.md`

Read `references/sge-patterns.md` before adding submission or job-state
logic.

## Working Rules

Treat the scheduler as a transport layer over a launch specification.

Keep these pieces separate:

- execution unit selection
- command or container launch spec
- SGE resource request
- batch script rendering
- submission result and state tracking

Prefer one `participant x stage` execution unit per submitted job unless
resource homogeneity and failure semantics clearly favor job arrays.

## Submission Rules

Prefer machine-readable submission flows:

- use `qsub -terse` when possible
- capture the returned job id explicitly
- write stdout and stderr to predictable paths
- retain the rendered script or submission payload for auditability

Model dependencies explicitly:

- use `-hold_jid` or equivalent dependency links for stage chaining
- keep dependency calculation outside raw command building
- store upstream job ids alongside the submitted unit

Treat Debian-packaged Son of Grid Engine as the first supported variant:

- prefer CLI-driven submission with `qsub`, `qstat`, `qdel`, and `qacct`
- keep DRMAA1 optional rather than mandatory
- keep site-specific resource names configurable instead of hard-coding
  `h_rt`, `h_vmem`, or similar values

## State And Logging

Persist enough data to make `status`, rerun, and debugging possible:

- job id
- stage id
- participant or session scope
- requested resources
- submission timestamp
- stdout and stderr paths
- rendered script path
- resolved launch command summary

## Validation

Run local checks after SGE runner edits:

```bash
python -m mypy src
python -m ruff check .
```

If you touch CLI submission surfaces, also run:

```bash
bidsflow --help
bidsflow status
```

## References

Use `references/sge-patterns.md` for submission patterns, state fields,
and dependency guidance.
