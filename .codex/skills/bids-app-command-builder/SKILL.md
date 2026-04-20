---
name: bids-app-command-builder
description: Design target-aware adapter command construction for app-backed BIDSFlow targets such as fMRIPrep, MRIQC, XCP-D, QSIPrep, and future BIDS Apps. Use when rebuilding target adapters, mapping request or config fields to tool arguments, separating target semantics from backend wrapping, or preserving executable provenance.
---

# Bids App Command Builder

Translate normalized target inputs into reproducible launch commands
without mixing tool semantics, container wrapping, and scheduler logic.

## Quick Start

Start by reading these files:

- `README.md`
- `src/bidsflow/project.py`
- `src/bidsflow/heudiconv/`
- `tests/`

Read `references/tool-mapping.md` before changing target-specific
command construction.

## Working Rules

Keep the launch pipeline separated into layers:

1. normalized project and target inputs
2. tool-specific argument construction
3. backend wrapping for native, Docker, or Apptainer
4. scheduler submission handled elsewhere

Return structured launch data whenever possible:

- argv as a list, not a shell-joined string
- environment variables as explicit key-value pairs
- bind mounts and working directories as separate fields
- provenance fields such as image reference and command snapshot

Keep the scheduler out of this layer:

- do not embed `sbatch` logic into BIDS App argument builders
- do not let SLURM resource keys leak into tool argument mapping
- let the scheduler consume a launch spec produced here

Keep user-facing behavior anchored in the CLI, implementation, tests,
issues, and pull requests. Do not recreate long design documents for
temporary decisions.

## Tool-Specific Guidance

Use one heavy BIDS App container invocation per execution unit unless a
tool clearly supports broader batching.

Preserve target semantics:

- `curate` should map to HeuDiConv-oriented inputs and curation outputs
- `fmriprep` and `mriqc` should consume validated raw BIDS inputs
- `xcpd` should consume derivative outputs and selected downstream scope
- `qsiprep` and `qsirecon` should preserve explicit diffusion and
  reconstruction contracts

Capture provenance every time:

- exact executable or image reference
- exact argv after config resolution
- selected participant, session, and task scope
- working, log, and output roots

## Change Workflow

When implementing or modifying command builders:

1. Confirm the target contract and required inputs.
2. Map public config fields to tool arguments.
3. Wrap the tool invocation for the selected backend.
4. Preserve enough metadata for logging and reruns.
5. Verify that the scheduler layer can submit the result unchanged.

## Validation

Run the narrowest local checks that cover adapter edits.

Do not recreate stage-first assumptions while rebuilding adapters.

## References

Use `references/tool-mapping.md` for target boundaries, backend wrapping,
and provenance rules.
