# HeuDiConv workflow

## 1. Purpose

HeuDiConv should be the first **managed workflow** rebuilt in BIDSFlow.

The reason is not that HeuDiConv deserves special branding in the public
CLI. The reason is that its official usage already has a real
multi-stage lifecycle that benefits from orchestration.

## 2. What the official HeuDiConv workflow looks like

The official custom-heuristic tutorial describes a retrospective flow
with a clear sequence:

1. generate a skeleton heuristic with `convertall`
2. inspect `dicominfo.tsv` and edit the heuristic manually
3. rerun HeuDiConv for the actual conversion

Source notes:

- HeuDiConv custom heuristic tutorial:
  [https://heudiconv.readthedocs.io/en/latest/custom-heuristic.html](https://heudiconv.readthedocs.io/en/latest/custom-heuristic.html)

The CLI reference also exposes custom actions such as:

- `heuristics`
- `heuristic-info`
- `ls`
- `populate-templates`
- `populate-intended-for`

Source notes:

- HeuDiConv CLI reference:
  [https://heudiconv.readthedocs.io/en/latest/commandline.html](https://heudiconv.readthedocs.io/en/latest/commandline.html)

For prospective ReproIn-style data, the official docs show a more direct
path that can skip bootstrap and go straight to conversion with the
`reproin` heuristic.

Source notes:

- HeuDiConv ReproIn tutorial:
  [https://heudiconv.readthedocs.io/en/latest/reproin.html](https://heudiconv.readthedocs.io/en/latest/reproin.html)

## 3. What BIDSFlow should manage

BIDSFlow should treat HeuDiConv as a workflow with explicit step
contracts rather than as one opaque command.

The main managed concerns are:

- preserving the bootstrap outputs that the user must inspect
- providing a clean human-edit step between bootstrap and conversion
- recording `.heudiconv` provenance and rerun behavior
- recording the exact launcher and resulting HeuDiConv provenance used
  for the run
- making the raw BIDS dataset an explicit downstream artifact
- separating one-time finalization from repeated conversion runs

Source notes:

- HeuDiConv installation guide, explicit versions are preferred for
  provenance:
  [https://heudiconv.readthedocs.io/en/stable/installation.html](https://heudiconv.readthedocs.io/en/stable/installation.html)

### 3.1 Launcher model

BIDSFlow should not ask the user to maintain the full HeuDiConv command
line.

Instead, the project should define a `launcher` that tells BIDSFlow how
to invoke HeuDiConv, and BIDSFlow should append the managed step
arguments.

Examples:

- `["heudiconv"]`
- `["singularity", "exec", "--cleanenv", "/containers/heudiconv.sif", "heudiconv"]`

This keeps the workflow managed while still letting users choose local
execution, wrappers, or container launchers.

It also avoids introducing a large backend abstraction too early.

## 4. Proposed managed steps

### 4.1 `bootstrap`

Goal:

- generate a starter heuristic and descriptor files without performing
  conversion
- do so from a representative sample path without forcing the user to
  commit to final BIDS subject or session labels yet

Official basis:

- the custom heuristic tutorial uses `-f convertall` together with
  `-c none` to create the starter material

Typical HeuDiConv shape:

```bash
heudiconv --files <dicom-files> -o <output-dir> -f convertall -c none
```

Design choice for the first BIDSFlow version:

- bootstrap should use `--files`-style input selection first
- bootstrap should not require final `subject` or `session` labels
- template-style dataset expansion with `--dicom_dir_template` can wait
  until the workflow contract is stable

Why this matters:

- retrospective projects often start from one sample folder whose
  directory name is not yet the final BIDS subject or session label
- users may still need to decide how source identifiers map onto BIDS
  identifiers
- forcing placeholder `subject` and `session` values too early would
  turn a HeuDiConv CLI constraint into a BIDSFlow usability problem

What BIDSFlow should record:

- the input selection method
- the sample path used for bootstrap
- the configured launcher
- output directory
- HeuDiConv version
- the generated `.heudiconv` state path
- the generated heuristic skeleton path
- the generated `dicominfo.tsv` path

What BIDSFlow should expose as artifacts:

- `heuristic_template`
- `dicom_inventory`
- `heudiconv_state`
- `bootstrap_report`

Suggested first public shape:

```bash
bidsflow heudiconv bootstrap <sample-path> [--reset] [--dry-run]
```

Suggested first API behavior:

- `<sample-path>` identifies the representative DICOM sample
- `--reset` is required before regenerating bootstrap outputs for the
  same project bootstrap state
- `--dry-run` shows the planned command, output files, and state paths

Suggested generated files:

- `code/heudiconv/heuristic.py`
- `code/heudiconv/dicominfo.tsv`
- `state/heudiconv/bootstrap.json`

Important rerun rule:

- the official tutorial says bootstrap should normally be done once per
  project and repeated only after removing `.heudiconv`

Design implication:

- BIDSFlow should never silently reuse a stale bootstrap run when the
  user is asking to regenerate the starter material
- a repeated bootstrap should either require an explicit reset or write
  to a new state location

Source notes:

- HeuDiConv custom heuristic tutorial:
  [https://heudiconv.readthedocs.io/en/latest/custom-heuristic.html](https://heudiconv.readthedocs.io/en/latest/custom-heuristic.html)
- HeuDiConv CLI reference, `--files` and `-s/--subjects` behavior:
  [https://heudiconv.readthedocs.io/en/latest/commandline.html](https://heudiconv.readthedocs.io/en/latest/commandline.html)

### 4.2 `edit-heuristic`

Goal:

- let the user revise the generated heuristic based on the discovered
  sequence information

This is not a scientific step BIDSFlow should automate away.

BIDSFlow should instead make the handoff explicit:

- tell the user which generated files matter
- store the chosen project-owned heuristic path
- mark the workflow as waiting for a human edit before conversion can
  proceed
- tell the user whether the later convert step still depends on
  project-specific identity mapping or anonymization helpers

Design implication:

- BIDSFlow should treat heuristic editing as a first-class pause point,
  not as an invisible side effect

### 4.3 `convert`

Goal:

- run the actual DICOM-to-BIDS conversion with the chosen heuristic
- resolve final subject and session labels using a project-defined
  mapping strategy
- register the resulting raw BIDS dataset for downstream work
- build a persisted `BIDSLayout` database for later analysis steps

Typical HeuDiConv shape:

```bash
heudiconv --files <dicom-files> -o <output-dir> -f <heuristic.py> -s <subject> -c dcm2niix -b
```

Identity concern:

- the source folder name, DICOM metadata, or project naming scheme may
  not map directly onto the final BIDS subject and session labels
- BIDSFlow should treat this as an explicit resolver problem, not as an
  ad hoc string hack inside the command line

Suggested resolver kinds:

- `heuristic`: defer to HeuDiConv heuristic logic such as `infotoids`
- `regex`: extract source identifiers from paths and rewrite them with
  templates
- `script`: call a project-owned script that returns the desired labels
- `literal`: use fixed labels for debugging or one-off recovery only

Suggested anonymization support:

- BIDSFlow should allow a project-owned `anon-cmd` helper for output ID
  rewriting when the project needs that behavior
- this is distinct from bootstrap because it belongs to the actual
  conversion contract, not to starter-material generation

Suggested first public shape:

```bash
bidsflow heudiconv convert [--dry-run]
```

Suggested first API behavior:

- `convert` should use the project-owned heuristic generated or chosen
  after bootstrap
- `convert` should use the configured launcher to invoke HeuDiConv
- identity mapping and anonymization behavior should initially come from
  the heuristic or project config rather than from many public flags

What BIDSFlow should record:

- the selected heuristic path and a content fingerprint
- the configured launcher
- any configured identity-mapping or anonymization helper
- the converter mode, typically `dcm2niix`
- whether BIDS mode was enabled
- the `IntendedFor` strategy implied by the heuristic, when present
- overwrite behavior
- the raw BIDS dataset root
- produced logs and exit status

What BIDSFlow should expose as artifacts:

- `raw_bids_dataset`
- `heudiconv_provenance`
- `raw_bids_layout_db`

Design implication:

- a successful convert run should register the raw BIDS root as a named
  artifact for downstream tools instead of leaving later steps to guess
  the path
- if the heuristic defines `POPULATE_INTENDED_FOR_OPTS`, `IntendedFor`
  handling should be treated as part of `convert`, not as a mandatory
  extra step
- a successful convert run should immediately build or rebuild a
  persisted `BIDSLayout` database for the raw BIDS artifact

Suggested first layout behavior:

- store the database under `state/layouts/raw_bids`
- rebuild it when the raw BIDS artifact is newly produced or marked
  stale
- treat the resulting database path as another registered artifact that
  later jobs may reuse

Source notes:

- HeuDiConv CLI reference:
  [https://heudiconv.readthedocs.io/en/latest/commandline.html](https://heudiconv.readthedocs.io/en/latest/commandline.html)
- HeuDiConv heuristics file, `infotoids` support:
  [https://heudiconv.readthedocs.io/en/stable/heuristics.html](https://heudiconv.readthedocs.io/en/stable/heuristics.html)
- HeuDiConv quickstart example:
  [https://heudiconv.readthedocs.io/en/v1.2.0/quickstart.html](https://heudiconv.readthedocs.io/en/v1.2.0/quickstart.html)
- PyBIDS `BIDSLayout`, persistent database support:
  [https://bids-standard.github.io/pybids/generated/bids.layout.BIDSLayout.html](https://bids-standard.github.io/pybids/generated/bids.layout.BIDSLayout.html)

### 4.4 Optional maintenance actions

Goal:

- support the smaller set of post-conversion repair actions that are
  sometimes needed after `convert`

The official CLI exposes at least:

- `populate-templates`
- `populate-intended-for`

Design implication:

- these actions should not define the main HeuDiConv lifecycle
- `populate-intended-for` is usually unnecessary as a separate managed
  step if `IntendedFor` is handled during `convert`
- `populate-templates` is still useful as an explicit maintenance action
  for recovery or batch-style workflows
- this becomes especially useful if future parallel conversion modes use
  BIDS `notop` and generate top-level files only after all worker runs
  finish

Important limit:

- the HeuDiConv batch usage notes say `populate_templates.sh` can create
  top-level BIDS files later, except `participants.tsv`, which still
  must be created manually

So BIDSFlow should not promise a magically complete post-processing step
in the first version.

Source notes:

- HeuDiConv usage guide:
  [https://heudiconv.readthedocs.io/en/v1.0.0/usage.html](https://heudiconv.readthedocs.io/en/v1.0.0/usage.html)
- HeuDiConv CLI reference:
  [https://heudiconv.readthedocs.io/en/latest/commandline.html](https://heudiconv.readthedocs.io/en/latest/commandline.html)

## 5. First implementation boundary

The first rebuilt HeuDiConv integration should stay small.

It should include:

- a bootstrap run model
- explicit heuristic-edit handoff metadata
- a convert run model
- launcher support for local or wrapped HeuDiConv execution
- identity-mapping and anonymization planning for convert
- automatic `BIDSLayout` database construction after successful convert
- run records and artifact registration for those steps

It should defer:

- standalone maintenance actions unless they solve a concrete gap
- cluster submission
- automatic participants bookkeeping beyond what HeuDiConv already
  handles
- automatic heuristic authoring
- advanced HeuDiConv custom actions unrelated to the core workflow

## 6. Recommended implementation order

1. Define the HeuDiConv run record and artifact shapes.
2. Implement `bootstrap` planning and execution.
3. Implement the explicit handoff into human heuristic editing.
4. Define launcher, identity-mapping, and anonymization configuration
   for `convert`.
5. Implement `convert` together with raw BIDS `BIDSLayout` indexing.
6. Add optional maintenance actions only if a real workflow gap remains.

This keeps the first slice aligned with the official workflow rather
than with an overgeneralized app abstraction.
