---
name: cluster-runner-sge
description: Design future SGE integration for BIDSFlow after the target and run model stabilizes. Use when reintroducing scheduler support for target executions, mapping execution requests to `qsub`, handling `qstat` or `qacct` state checks, wiring dependencies, or organizing scheduler-facing state without letting SGE shape the public CLI.
---

# Cluster Runner Sge

Translate BIDSFlow execution units into reliable SGE submissions while
keeping scheduler concerns separate from target semantics and adapter
logic.

## Quick Start

Start by reading these files:

- `README.md`
- `docs/design/target-model.md`
- `docs/design/task-first-cli.md`
- `docs/design/handoff-contract.md`

Read `references/sge-patterns.md` before adding submission or job-state
logic.

## Working Rules

Treat the scheduler as a transport layer over a launch specification.

This skill is intentionally downstream of the current design reset. Use
it only after the task and target model are stable enough to support
real execution requests.

Keep these pieces separate:

- execution unit selection
- command or container launch spec
- SGE resource request
- batch script rendering
- submission result and state tracking

Prefer one `target x scope-unit` execution unit per submitted job unless
resource homogeneity and failure semantics clearly favor job arrays.

Do not let scheduler concerns leak back upward into top-level command
design.

## Submission Rules

Prefer machine-readable submission flows:

- use `qsub -terse` when possible
- capture the returned job id explicitly
- write stdout and stderr to predictable paths
- retain the rendered script or submission payload for auditability

Model dependencies explicitly:

- use `-hold_jid` or equivalent dependency links for target dependencies
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
- target id
- participant or session scope
- requested resources
- submission timestamp
- stdout and stderr paths
- rendered script path
- resolved launch command summary

## Validation

If work is still in design, validate that scheduler language remains
strictly below the task and target model.

If implementation resumes later, run the narrowest scheduler-facing
checks available for the code you changed.

## References

Use `references/sge-patterns.md` for submission patterns, state fields,
and dependency guidance.
