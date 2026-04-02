# Target model

## 1. Purpose

BIDSFlow should model workflow work in terms of **targets**, not stages.

The word `stage` over-emphasizes a linear pipeline, while the real
workflow shape is a graph with branching, fan-out, and multiple kinds of
targets.

## 2. Core Terms

### 2.1 Task

A task is the user-facing verb.

Examples:

- `init`
- `source scan`
- `check`
- `run`
- `status`

### 2.2 Target

A target is the workflow object that a task acts on.

Examples:

- `curate`
- `validate`
- `fmriprep`
- `mriqc`
- `xcpd`
- `qsiprep`
- `qsirecon`

### 2.3 Adapter

An adapter maps a target onto a concrete tool or internal workflow
implementation.

Examples:

- `curate` may map to a HeuDiConv-backed adapter
- `fmriprep` maps to an fMRIPrep adapter
- `xcpd` maps to an XCP-D adapter

### 2.4 Backend and scheduler

Backends and schedulers are execution details that sit below the public
task and target model.

They are important, but they should not define the top-level CLI tree.

## 3. Why `stage` Is the Wrong Public Abstraction

Not every distinction in BIDSFlow is best understood as a stage
difference.

Examples:

- `validate` fans out to multiple downstream targets
- `curate` is workflow-owned and not merely an app launch
- some targets are app-backed and some are not

This makes the model closer to a target graph than to a simple sequence
of stages.

## 4. Target Categories

The initial target set spans multiple categories:

- source-facing targets such as `curate`
- validation targets such as `validate`
- app-backed processing targets such as `fmriprep`, `mriqc`, and
  `qsiprep`
- derivative-consuming targets such as `xcpd` and `qsirecon`

The model should allow those categories to coexist without forcing them
into one misleading label.

## 5. Target Specification

A future `TargetSpec` should describe at least:

- `id`
- `label`
- `kind`
- `scope`
- `consumes`
- `produces`
- `prerequisites`
- `adapter`

Illustrative shape:

```python
class TargetSpec(BaseModel):
    id: str
    label: str
    kind: str
    scope: str
    consumes: list[str]
    produces: list[str]
    prerequisites: list[str]
    adapter: str | None
```

## 6. CLI Implications

The public CLI should speak in terms of:

- tasks at the top level
- targets as the main object for `check`, `run`, and `status`

That means:

- prefer `bidsflow run fmriprep`
- prefer `bidsflow check curate`
- avoid public `--stage` flags
- avoid one top-level command per app

## 7. Summary

The next implementation should use `TargetId` and `TargetSpec` as its
core internal language.

`stage` should not be the organizing abstraction for the rebuilt CLI.
