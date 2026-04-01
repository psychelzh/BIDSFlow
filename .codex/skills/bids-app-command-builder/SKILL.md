---
name: bids-app-command-builder
description: Build backend-aware command construction for HeuDiConv, fMRIPrep, XCP-D, and future BIDS Apps. Use when implementing or refactoring stage runners, mapping config fields to CLI flags, handling Docker or Apptainer wrapping, generating per-participant or per-session execution units, or preserving executable provenance.
---

# Bids App Command Builder

Translate normalized stage inputs into reproducible launch commands
without mixing tool semantics, container wrapping, and scheduler logic.

## Quick Start

Start by reading these files:

- `src/bidsflow/core/stages.py`
- `src/bidsflow/config/models.py`
- `src/bidsflow/cli.py`
- `docs/design/cli-conventions.md`
- `docs/design/heudiconv-workflow.md`
- `docs/design/stage-model.md`
- `docs/design/handoff-contract.md`

Read `references/tool-mapping.md` before changing stage-specific command
construction.

## Working Rules

Keep the launch pipeline separated into layers:

1. normalized project and stage inputs
2. scope discovery and normalization
3. tool-specific argument construction
4. backend wrapping for native, Docker, or Apptainer
5. scheduler submission handled elsewhere

Return structured launch data whenever possible:

- argv as a list, not a shell-joined string
- environment variables as explicit key-value pairs
- bind mounts and working directories as separate fields
- provenance fields such as image reference and command snapshot

Keep the scheduler out of this layer:

- do not embed `sbatch` logic into BIDS App argument builders
- do not let SLURM resource keys leak into tool argument mapping
- let the scheduler consume a launch spec produced here
- keep user-facing command naming and subcommand semantics outside the
  tool argv builder

## Tool-Specific Guidance

Use one heavy BIDS App container invocation per execution unit unless a
tool clearly supports broader batching.

Preserve stage semantics:

- `heudiconv` should map to HeuDiConv-oriented inputs and curation outputs
- `fmriprep` should consume validated raw BIDS and emit derivative roots
- `xcpd` should consume derivative outputs and selected downstream scope

Capture provenance every time:

- exact executable or image reference
- exact argv after config resolution
- selected participant, session, and task scope
- working, log, and output roots

## Change Workflow

When implementing or modifying command builders:

1. Confirm the stage contract and required inputs.
2. Map public config fields to tool arguments.
3. Wrap the tool invocation for the selected backend.
4. Preserve enough metadata for logging and reruns.
5. Verify that the scheduler layer can submit the result unchanged.

## Validation

Run the local checks that cover command-building edits:

```bash
python -m mypy src
python -m ruff check .
```

If you touched CLI wiring or stage exposure, also run:

```bash
bidsflow --help
bidsflow status
```

## References

Use `references/tool-mapping.md` for stage boundaries, backend wrapping,
and provenance rules.
