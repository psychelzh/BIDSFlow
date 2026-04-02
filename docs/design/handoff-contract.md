# Handoff contract

## 1. Purpose

BIDSFlow should treat transitions between targets as **formal handoffs**
rather than as informal path passing.

A handoff contract records what an upstream target has produced, what a
downstream target expects, and which blockers still prevent execution.

## 2. Why This Matters

Passing only a directory path is not enough.

Downstream work needs explicit assurances about:

- coverage
- modality availability
- derivative lineage
- provenance
- compatibility with the intended target

This is one of the main value propositions of BIDSFlow: not replacing
scientific tools, but making their boundaries explicit and auditable.

## 3. Required Fields

Each handoff object should minimally carry:

- `from_target`
- `to_target`
- `project_root`
- `dataset_root`
- `dataset_kind`
- `scope_units`
- `coverage`
- `modalities`
- `provenance`
- `guarantees`
- `blockers`

The exact schema may evolve, but these concepts should remain stable.

## 4. Example Handoffs

### 4.1 `curate -> validate`

The handoff should describe:

- the raw BIDS root
- participant and session coverage
- top-level metadata presence
- curation warnings that remain unresolved

### 4.2 `validate -> fmriprep`

The handoff should confirm:

- the selected scope exists
- the necessary anatomical and functional inputs are present
- no blocking raw-data issues remain

### 4.3 `validate -> mriqc`

The handoff should confirm:

- the selected raw inputs exist
- the intended scope is coherent
- dataset structure is sufficient for the requested check

### 4.4 `fmriprep -> xcpd`

The handoff should confirm:

- the derivative root exists
- derivative metadata are present
- participant coverage matches the request
- required spaces and file classes are available
- provenance identifies the upstream run

### 4.5 `validate -> qsiprep`

The handoff should confirm:

- diffusion inputs exist for the selected scope
- accompanying structural data are present when needed
- no blocking structure issues remain

### 4.6 `qsiprep -> qsirecon`

The handoff should confirm:

- upstream derivatives exist
- reconstruction inputs are complete
- the requested reconstruction contract matches the available products

## 5. Evaluation Outcomes

A handoff evaluation should return one of:

- `ready`
- `warning`
- `blocked`

This separates fatal blockers from non-fatal caveats.

## 6. Suggested Internal Shape

```python
class HandoffContract(BaseModel):
    from_target: str
    to_target: str
    dataset_root: Path
    dataset_kind: str
    scope_units: list[str]
    guarantees: dict
    blockers: list[str]
```

## 7. Summary

The handoff contract should be defined in terms of **targets**, not
stages.

It is the mechanism that lets BIDSFlow coordinate app-backed work
without collapsing everything into opaque path passing.
