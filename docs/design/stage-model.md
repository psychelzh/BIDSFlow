# Stage model

## 1. Purpose

BIDSFlow should be organized around **explicit workflow stages** rather
than around a monolithic end-to-end command. Each stage represents a
bounded unit of responsibility with clearly defined:

- inputs
- outputs
- execution scope
- upstream dependencies
- validation rules
- rerun semantics

This model is intended to make execution inspectable, resumable, and extensible.

## 2. Core principle

A stage is not merely a command wrapper. It is a **contracted execution
unit** with domain-specific requirements and declared products.

BIDSFlow should therefore treat each stage as an object with the
following minimum fields:

- `id`: unique stage identifier
- `label`: human-readable stage name
- `scope`: execution scope
- `upstream`: required predecessor stages
- `products`: declared output classes
- `preflight checks`: validation before execution
- `completion checks`: conditions for success
- `staleness rules`: when outputs should be treated as outdated

## 3. Stage inventory

The initial stage inventory is:

1. `curate` — HeuDiConv-backed curation into raw BIDS
2. `validate` — dataset or derivative validation
3. `fmriprep` — functional/anatomical MRI preprocessing
4. `mriqc` — MRI quality control
5. `xcpd` — downstream functional derivative processing
6. `qsiprep` — diffusion MRI preprocessing
7. `qsirecon` — downstream diffusion reconstruction

A future stage registry should allow new BIDS Apps to be added without
changing the orchestration core.

## 4. Stage categories

### 4.1 Curation stage

Transforms sourcedata into a BIDS-aware raw dataset.

Representative stage:

- `curate`

### 4.2 Validation stage

Checks whether raw or derivative datasets satisfy downstream requirements.

Representative stage:

- `validate`

### 4.3 Preprocessing stage

Consumes raw BIDS data and emits derivative datasets.

Representative stages:

- `fmriprep`
- `qsiprep`
- `mriqc`

### 4.4 Downstream derivative stage

Consumes derivative datasets and emits higher-order derivative products.

Representative stages:

- `xcpd`
- `qsirecon`

## 5. Execution scope

The scope of a stage must be explicit because it determines scheduling,
failure isolation, and rerun behavior.

Recommended scopes:

- `dataset`: the whole dataset
- `participant`: one participant at a time
- `participant/session`: one participant-session unit

The default operational unit for most heavy stages should be
`participant` or `participant/session`, because this aligns well with
containerized neuroimaging workflows and supports partial recovery.

## 6. State model

Each `stage × scope-unit` should have a tracked state:

- `pending`
- `running`
- `done`
- `failed`
- `skipped`
- `stale`

### 6.1 Meaning of `stale`

`stale` indicates that outputs exist but should no longer be trusted as
current because some relevant upstream configuration, code, or dependency
contract has changed.

Typical causes:

- changed heuristic file
- changed container image tag
- changed output spaces
- changed XCP-D mode or atlas set
- changed upstream derivative root

## 7. Preflight checks

Before a stage is executed, BIDSFlow should verify at least four dimensions:

1. **structural readiness**
   Required paths and files exist.
2. **semantic readiness**
   Required metadata or modalities are present.
3. **backend readiness**
   The selected runner backend is available.
4. **dependency readiness**
   Required upstream stages have completed successfully.

## 8. Completion checks

Completion should never be inferred solely from exit code. A stage
should also verify expected products.

Examples:

- `curate`: raw BIDS root exists and contains required top-level files
- `fmriprep`: derivative dataset exists with valid `dataset_description.json`
- `xcpd`: derivative outputs exist for requested participants and
  requested mode
- `qsirecon`: expected recon derivative artifacts are present

## 9. Staleness rules

A stage should be marked stale when any tracked upstream determinant changes.

Determinants may include:

- stage-specific configuration snapshot
- container image reference
- upstream dataset location
- upstream provenance signature
- stage code version in BIDSFlow

## 10. Dependency graph

The initial dependency graph should be simple and explicit:

- `curate -> validate`
- `validate -> fmriprep`
- `validate -> mriqc`
- `validate -> qsiprep`
- `fmriprep -> xcpd`
- `qsiprep -> qsirecon`

Importantly, `validate` should be reusable for both raw datasets and
derivative datasets, depending on context.

## 11. Consequences for CLI design

The stage model should remain central internally, but the public CLI
does not need to expose one top-level command per stage.

Instead, BIDSFlow should prefer task-first commands that operate on
stages as explicit targets. This implies:

- top-level commands for logistics tasks such as `source`, `check`,
  `run`, and `status`
- explicit subject/session selection where relevant
- explicit config input
- explicit stage targeting when a task acts on a specific stage
- future support for planned multi-stage execution that still surfaces
  intermediate boundaries

## 12. Consequences for internal architecture

The internal architecture should include:

- a stage registry
- stage-specific configuration models
- stage-specific preflight validators
- command builders per backend
- state store for stage units
- provenance snapshots attached to each run

## 13. Summary

The stage model is the core abstraction of BIDSFlow. It ensures that the
package remains:

- understandable
- extensible
- reproducible
- recoverable after failure
- suitable for real neuroimaging work rather than demo-style automation
