# Tool Mapping

## Scope

Use this reference when turning BIDSFlow configuration and target intent
into runnable commands.

## Layer Boundaries

Keep these concerns separate:

- target contract and required inputs
- tool-specific flags
- backend wrapping
- scheduler submission

The command builder should own the middle two layers only.

## Target Anchors

- `curate`: HeuDiConv-backed curation into raw BIDS
- `validate`: validation and preflight logic, not a heavy executor
- `fmriprep`: preprocessing from raw BIDS to derivatives
- `mriqc`: raw-data quality evaluation target
- `xcpd`: downstream derivative processing from compatible fMRIPrep
  outputs
- `qsiprep`: diffusion preprocessing into derivative outputs
- `qsirecon`: reconstruction from compatible QSIPrep outputs

Future targets should follow the same pattern.

## Backend Rules

- Native mode should produce a direct argv and environment.
- Docker mode should explicitly carry image, bind mounts, and container
  argv.
- Apptainer mode should explicitly carry image, bind mounts, and runtime
  flags.
- Do not flatten backend structure too early into one opaque shell
  string.

## Provenance Rules

Always preserve:

- resolved executable or container image
- resolved argv
- selected scope unit
- key input and output roots
- environment overrides that materially affect reproducibility

## Practical Heuristics

- Prefer one participant per heavy app invocation.
- Treat session and task filters as part of the scope, not ad hoc
  string fragments.
- Make output and work directories explicit in the launch spec.
- Keep command construction deterministic for the same normalized input.
