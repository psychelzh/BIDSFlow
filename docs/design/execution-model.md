# Execution model

## 1. Purpose

BIDSFlow should act as a **workflow logistics layer** around scientific
tools, not as a flag-complete wrapper around every BIDS App.

Its job is to make runs predictable, resumable, and auditable.

## 2. Platform boundary

BIDSFlow currently targets Unix-like execution environments: Linux,
macOS, WSL, and HPC systems. Native Windows is intentionally outside the
supported runtime boundary.

This keeps the execution model aligned with the environments where BIDS
conversion and BIDS Apps are normally run. It also avoids maintaining
parallel filesystem behavior for shell scripts, POSIX permissions,
symlinks, and scheduler integration. Windows users should run BIDSFlow
inside WSL.

## 3. What BIDSFlow should own

For each task execution, BIDSFlow should be responsible for:

- locating the intended inputs
- deciding where declared outputs should live
- materializing the concrete command from a user-facing template or a
  managed workflow step
- preparing any required working directories or helper files
- launching and monitoring the process
- recording status, logs, and provenance
- deciding whether the step may be resumed, retried, or must be rerun
- registering outputs so later work can consume them explicitly

This is the stable value BIDSFlow adds even when the scientific command
itself remains user-editable.

## 4. What BIDSFlow should not own

BIDSFlow should not try to:

- replace the full parameter surface of each BIDS App
- hide every native command detail from advanced users
- infer scientific intent from too little information
- pretend that all tools have the same lifecycle

The goal is to own the logistics, not to erase the tools.

## 5. Core runtime objects

The rebuilt runtime should revolve around a small set of durable
objects.

### 5.1 Command template

A command template is the user-editable command definition for a tool
run.

It should capture at least:

- the tool or workflow name
- the launcher prefix, if the project runs the tool through a wrapper or
  container command
- the command body or script path
- placeholder values BIDSFlow is allowed to fill in
- the artifact kinds it expects as input
- the artifact kinds it promises as output

This is the right abstraction for tools such as `fmriprep`, `mriqc`,
`xcpd`, and other app-backed runs where BIDSFlow does not need to own
the full native flag surface.

### 5.2 Artifact record

An artifact record describes an input or output that later work may
consume.

It should capture at least:

- `id`
- `kind`
- `path`
- `scope`
- `producer_run`
- `status`
- `provenance`

Examples include:

- a raw BIDS dataset root
- a persisted `BIDSLayout` database for that dataset
- a derivatives root produced by a specific tool
- a generated heuristic file
- a reviewed source table or descriptor file produced during preparation

### 5.3 State record

A state record is the durable record of the current known status for one
managed step.

It should capture at least:

- `workflow`
- `step`
- `status`
- `inputs`
- `outputs`
- `started_at`
- `finished_at`
- `unit_log_dir`
- `unit_table_path`

This is the object that powers `status`.

Attempt history should live in `logs/`, not in the state record itself.
For multi-unit steps, the state record points to the unit log directory
while the unit table records each concrete per-unit log path. This keeps
future parallel execution from interleaving unrelated tool output.

### 5.4 Managed workflow

A managed workflow is a tool integration for which BIDSFlow knows the
internal step boundaries and the expected handoff between those steps.

This should be reserved for workflows that genuinely benefit from
orchestration logic, not for every BIDS App.

The first candidate is HeuDiConv because its official workflow already
has a natural multi-step shape.

### 5.5 Derived execution view

Some runs may need a temporary filesystem view that is derived from a
durable artifact but is not itself the source of truth.

Examples include:

- a normalized symlink tree materialized from a reviewed source table
- a per-run working directory assembled from recorded inputs

These views should be:

- derived from an explicit durable record
- reproducible on demand
- safe to delete after the run ends

This keeps long-lived truth in artifacts such as `sources.tsv` while
letting execution steps materialize short-lived helper structures only
when they are actually needed.

## 6. Two execution styles

The first rebuilt runtime should support two styles of work.

### 6.1 Managed workflow

BIDSFlow knows the step sequence and the step-specific artifacts.

This is appropriate for HeuDiConv, where BIDSFlow can help with:

- draft heuristic generation
- heuristic editing handoff
- `bidsflow heudiconv` executions
- finalization steps
- rerun safety around `.heudiconv` state

### 6.2 Template-backed job

BIDSFlow generates or stores a user-editable command template and then
executes it while managing inputs, outputs, logs, and retries.

This is appropriate for most other BIDS Apps.

In that model, BIDSFlow owns the logistics contract while the user still
owns the scientific command details.

## 7. Status and rerun semantics

The initial runtime does not need an elaborate scheduler model, but it
does need explicit run states.

A first-pass run state model can stay small while still leaving room for
future scheduler transport:

- `prepared`
- `submitted`
- `running`
- `succeeded`
- `failed`
- `cancelled`

Suggested meanings:

- `prepared`: inputs were resolved, command materialization succeeded,
  and the run record was created
- `submitted`: the run was handed off to a scheduler, but the worker has
  not yet been observed as running
- `running`: the worker process has started and not yet reached a final
  state
- `succeeded`: the process exited successfully and declared outputs were
  registered
- `failed`: the process exited unsuccessfully or did not produce the
  required outputs
- `cancelled`: the run was intentionally stopped before a successful
  completion

Scheduler observation should stay separate from the durable run status.

For example, a scheduler-facing field may report:

- `queued`
- `running`
- `done`
- `unknown`

without overwriting a stronger run-level conclusion that BIDSFlow has
already recorded locally.

Reruns may write new unit log directories while mutating the current
state files in place.

## 8. CLI implications

This runtime model does not require a large public CLI.

The current minimal task set can remain:

- `init`
- `check`
- `run`
- `status`

The exact public noun that follows those tasks can remain conservative
while the runtime model stabilizes.

## 9. Relationship to handoffs

The existing handoff idea still matters, but it should become more
concrete.

A handoff is best understood as the downstream use of recorded
artifacts, not as opaque path passing between abstract stages.

## 10. Summary

The next implementation should treat BIDSFlow as a logistics system with
managed workflows, command templates, artifact records, and run records
at its core.
