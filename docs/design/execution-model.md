# Execution model

## 1. Purpose

BIDSFlow should act as a **workflow logistics layer** around scientific
tools, not as a flag-complete wrapper around every BIDS App.

Its job is to make runs predictable, resumable, and auditable.

## 2. What BIDSFlow should own

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

## 3. What BIDSFlow should not own

BIDSFlow should not try to:

- replace the full parameter surface of each BIDS App
- hide every native command detail from advanced users
- infer scientific intent from too little information
- pretend that all tools have the same lifecycle

The goal is to own the logistics, not to erase the tools.

## 4. Core runtime objects

The rebuilt runtime should revolve around a small set of durable
objects.

### 4.1 Command template

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

### 4.2 Artifact record

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
- a manifest or descriptor file produced during preparation

### 4.3 Run record

A run record is the durable record of one execution attempt.

It should capture at least:

- `workflow`
- `step`
- `status`
- `attempt`
- `command`
- `inputs`
- `outputs`
- `started_at`
- `finished_at`
- `log_paths`
- `exit_code`

This is the object that powers `status`, retry, and resumability.

### 4.4 Managed workflow

A managed workflow is a tool integration for which BIDSFlow knows the
internal step boundaries and the expected handoff between those steps.

This should be reserved for workflows that genuinely benefit from
orchestration logic, not for every BIDS App.

The first candidate is HeuDiConv because its official workflow already
has a natural multi-step shape.

## 5. Two execution styles

The first rebuilt runtime should support two styles of work.

### 5.1 Managed workflow

BIDSFlow knows the step sequence and the step-specific artifacts.

This is appropriate for HeuDiConv, where BIDSFlow can help with:

- bootstrap generation
- heuristic editing handoff
- conversion runs
- finalization steps
- rerun safety around `.heudiconv` state

### 5.2 Template-backed job

BIDSFlow generates or stores a user-editable command template and then
executes it while managing inputs, outputs, logs, and retries.

This is appropriate for most other BIDS Apps.

In that model, BIDSFlow owns the logistics contract while the user still
owns the scientific command details.

## 6. Status and rerun semantics

The initial runtime does not need an elaborate scheduler model, but it
does need explicit run states.

A first-pass run state model can stay small:

- `prepared`
- `running`
- `succeeded`
- `failed`
- `stale`

Suggested meanings:

- `prepared`: inputs were resolved and command materialization succeeded
- `running`: the process has been launched and not yet reached a final
  state
- `succeeded`: the process exited successfully and declared outputs were
  registered
- `failed`: the process exited unsuccessfully or did not produce the
  required outputs
- `stale`: a previously successful run is no longer trusted because the
  relevant inputs, template, or workflow definition changed

Reruns should create new attempts rather than mutating old records in
place.

## 7. CLI implications

This runtime model does not require a large public CLI.

The current minimal task set can remain:

- `init`
- `check`
- `run`
- `status`

The exact public noun that follows those tasks can remain conservative
while the runtime model stabilizes.

## 8. Relationship to handoffs

The existing handoff idea still matters, but it should become more
concrete.

A handoff is best understood as the downstream use of recorded
artifacts, not as opaque path passing between abstract stages.

## 9. Summary

The next implementation should treat BIDSFlow as a logistics system with
managed workflows, command templates, artifact records, and run records
at its core.
