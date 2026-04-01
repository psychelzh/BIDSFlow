# Handoff contract

## 1. Purpose

BIDSFlow should treat transitions between stages as **formal handoffs**
rather than informal path passing. A handoff contract defines what an
upstream stage must provide before a downstream stage can be executed
safely.

This is one of the central responsibilities of BIDSFlow: not merely
launching tools, but ensuring that stage boundaries are well-formed.

## 2. Contract concept

A handoff contract is a structured description of:

- what dataset is being handed off
- what scope units are covered
- what modality or derivative class is available
- what provenance produced it
- what downstream assumptions are satisfied
- what unresolved constraints still block execution

## 3. Required contract fields

Each handoff object should minimally include the following fields.

### 3.1 Identity

- `from_stage`
- `to_stage`
- `project_root`
- `dataset_root`
- `dataset_type` (`raw` or `derivative`)
- `scope_units` (participants / sessions / tasks as relevant)

### 3.2 Dataset metadata

- presence of `dataset_description.json`
- derivative `GeneratedBy` content when applicable
- dataset name and derivative lineage

### 3.3 Coverage

- participant list
- session coverage if applicable
- modality availability
- task coverage if relevant

### 3.4 Provenance

- BIDSFlow version
- stage configuration snapshot
- backend type
- container image reference
- execution timestamp
- upstream signature or hash

### 3.5 Stage-specific guarantees

Examples:

- required spaces available for XCP-D
- expected diffusion derivatives available for QSIRecon
- raw BIDS compliance level sufficient for MRIQC or fMRIPrep

### 3.6 Blocking issues

A handoff should explicitly record unresolved blockers rather than failing silently.

Examples:

- missing participant outputs
- missing `fsLR` outputs needed for CIFTI downstream processing
- missing `dataset_description.json`
- unsupported modality combination

## 4. Handoff classes in the initial BIDSFlow scope

### 4.1 `heudiconv -> validate`

The HeuDiConv stage should hand off:

- a raw BIDS root
- top-level BIDS metadata
- subject/session coverage summary
- any known curation warnings

### 4.2 `validate -> fmriprep`

Validation should hand off confirmation that:

- the raw dataset is structurally readable
- necessary functional/anatomical modalities exist for the selected subject(s)
- no blocking dataset-level errors remain

### 4.3 `validate -> mriqc`

Validation should hand off confirmation that:

- the selected subject(s) exist in raw BIDS
- raw image files needed for MRIQC are available
- the intended scope selection is coherent

### 4.4 `fmriprep -> xcpd`

This is a critical derivative handoff. The contract should verify at least:

- fMRIPrep derivative root exists
- derivative `dataset_description.json` exists
- subject coverage matches the intended scope
- required spaces or file formats for the requested XCP-D mode are available
- provenance records identify the upstream fMRIPrep run

### 4.5 `validate -> qsiprep`

Validation should hand off confirmation that:

- diffusion data are present for selected subjects
- accompanying structural inputs exist if required by the chosen configuration
- no blocking raw-data structure issues remain

### 4.6 `qsiprep -> qsirecon`

This derivative handoff should verify at least:

- QSIPrep derivative root exists
- derivative metadata are intact
- required reconstruction inputs exist
- the requested reconstruction specification is compatible with available products

## 5. Why path passing is insufficient

Passing only a directory path is inadequate because downstream stages
need more than location. They need assurances about:

- semantic compatibility
- subject/session coverage
- modality completeness
- provenance lineage
- configuration compatibility

Therefore, BIDSFlow should internally compile a structured handoff
object, even if the final backend execution still uses filesystem paths.

## 6. Suggested internal representation

A future Python model may look conceptually like this:

```python
class HandoffContract(BaseModel):
    from_stage: str
    to_stage: str
    dataset_root: Path
    dataset_type: str
    scope_units: list[str]
    modalities: list[str]
    provenance: dict
    guarantees: dict
    blockers: list[str]
```

The exact schema may evolve, but the principle should remain stable.

## 7. Contract evaluation outcomes

A handoff evaluation should return one of three high-level outcomes:

- `ready`: downstream stage may proceed
- `warning`: downstream stage may proceed, but non-blocking issues exist
- `blocked`: downstream stage must not proceed

This makes it possible to separate fatal from non-fatal issues.

## 8. Relationship to provenance and resumability

The handoff contract should be stored in the run state because it supports:

- rerun diagnostics
- stale detection
- downstream reproducibility
- audit trails

If a downstream result is questioned later, the recorded handoff object
should explain what was assumed at execution time.

## 9. Summary

BIDSFlow should regard stage transitions as first-class objects. This is
the mechanism by which it can deliver robust orchestration without
replacing the scientific tools themselves.
