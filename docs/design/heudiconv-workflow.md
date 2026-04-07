# HeuDiConv workflow

## 1. Purpose

HeuDiConv should be the first **managed workflow** rebuilt in BIDSFlow.

The reason is not that HeuDiConv deserves special branding in the public
CLI. The reason is that its official usage already has a real
multi-stage lifecycle that benefits from orchestration.

## 1.1 Current implementation status

The current codebase implements two early slices:

```bash
bidsflow heudiconv manifest [--config bidsflow.toml] [--subject-regex REGEX] [--session-regex REGEX] [--reset] [--dry-run]
bidsflow heudiconv skeleton <sample-path>... [--config bidsflow.toml] [--reset] [--dry-run]
```

Current supported behavior:

- manifest scans the immediate child directories under the configured
  `source_root`
- manifest does not call HeuDiConv
- manifest writes a reviewable `manifest.tsv` plus `manifest.json`
- by default manifest leaves `subject/session` fields blank instead of
  guessing from directory names
- optional explicit regex rules can fill `subject_raw/session_raw`
- manifest is the project-owned truth source for later conversion
  handoff
- manifest does not create links or other execution views
- default heuristic is `code/heudiconv/heuristic.py`, and it may be
  absent until skeleton runs
- default launcher is `["heudiconv"]`
- projects may override that with `[heudiconv].launcher`
- skeleton accepts one or more representative sample paths
- a single sample path is processed as one skeleton unit with one
  temporary subject label
- multiple sample paths are split into separate single-directory
  skeleton units
- those multi-path units are treated as temporary sessions of one
  placeholder subject, and the generated session mapping is recorded in
  skeleton state
- skeleton writes its HeuDiConv working output into an isolated
  skeleton work root under `state/heudiconv/` instead of the real raw
  BIDS output directory
- skeleton copies the generated heuristic into `code/heudiconv/`
- skeleton copies every generated `dicominfo*.tsv` into
  `code/heudiconv/dicominfo/`
- skeleton records run metadata in `state/heudiconv/skeleton.json`

Current limit:

- `bidsflow heudiconv convert` is only a placeholder command today

## 2. What the official HeuDiConv workflow looks like

The official custom-heuristic tutorial describes a retrospective flow
with a clear sequence:

1. generate a skeleton heuristic with `convertall`
2. inspect the generated `dicominfo` files and edit the heuristic manually
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
path that can skip skeleton and go straight to conversion with the
`reproin` heuristic.

Source notes:

- HeuDiConv ReproIn tutorial:
  [https://heudiconv.readthedocs.io/en/latest/reproin.html](https://heudiconv.readthedocs.io/en/latest/reproin.html)

## 3. What BIDSFlow should manage

BIDSFlow should treat HeuDiConv as a workflow with explicit step
contracts rather than as one opaque command.

The main managed concerns are:

- freezing a reviewable manifest before conversion
- preserving the skeleton outputs that the user must inspect
- providing a clean human-edit step between skeleton and conversion
- recording `.heudiconv` provenance and rerun behavior
- recording the exact launcher and resulting HeuDiConv provenance used
  for the run
- making the raw BIDS dataset an explicit downstream artifact
- separating one-time finalization from repeated conversion runs

Relationship between early preparation steps:

- `manifest` and `skeleton` solve different preparation problems
- `manifest` standardizes dataset-wide naming and handoff
- `skeleton` produces sample-level heuristic starter material
- neither step should be treated as a strict prerequisite for the other
- many projects will run `skeleton` first because heuristic work often
  starts before final subject/session naming is frozen
- `convert` is the first step that should rely on both a confirmed
  manifest and a reviewed heuristic

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
- `["singularity", "run", "/containers/heudiconv.sif"]`

This keeps the workflow managed while still letting users choose local
execution, wrappers, or container launchers.

It also avoids introducing a large backend abstraction too early.

## 4. Proposed managed steps

### 4.1 `manifest`

Goal:

- enumerate candidate source directories into a reviewable handoff table
- avoid guessing subject or session labels unless the user provides an
  explicit extraction rule
- provide a project-owned manifest that later conversion can consume

Design choice for the first BIDSFlow version:

- manifest should be a filesystem-only step and should not call
  HeuDiConv
- manifest should use the configured project `source_root` instead of a
  separate command-line input root
- manifest should scan the immediate child directories under that source
  root
- manifest should default to empty `subject_raw/session_raw` and empty
  final `subject_label/session_label`
- optional explicit regex rules may fill `subject_raw/session_raw`
- manifest should leave final naming decisions visible and editable in
  the output table
- manifest should be the durable truth source for later conversion
  inputs
- manifest should not create symlink trees or other execution views on
  its own
- if conversion later needs a normalized input tree, `convert` should
  materialize it temporarily from the confirmed manifest and remove it
  when the run finishes

Suggested first public shape:

```bash
bidsflow heudiconv manifest [--subject-regex REGEX] [--session-regex REGEX] [--reset] [--dry-run]
```

Suggested generated files:

- `code/heudiconv/manifest.tsv`
- `state/heudiconv/manifest.json`

Ordering note:

- `manifest` does not need to happen before `skeleton`
- it is one of two preparation tracks that eventually hand off into
  `convert`

### 4.2 `skeleton`

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

- skeleton should use `--files`-style input selection first
- skeleton should not require final `subject` or `session` labels
- skeleton should treat multiple input directories as multiple
  single-directory units, not as one implicit HeuDiConv multi-directory
  grouping
- template-style dataset expansion with `--dicom_dir_template` can wait
  until the workflow contract is stable

Why this matters:

- retrospective projects often start from one sample folder whose
  directory name is not yet the final BIDS subject or session label
- some projects need more than one representative sample path because
  different sessions may expose different sequence sets
- users may still need to decide how source identifiers map onto BIDS
  identifiers
- HeuDiConv can require a subject id even during skeleton, so BIDSFlow
  should provide a temporary subject without pretending it is the final
  BIDS identity
- multiple input directories are often "same subject, different
  sessions" in practice, but HeuDiConv does not reliably treat arbitrary
  directory lists that way on its own
- forcing placeholder `subject` and `session` values too early would
  turn a HeuDiConv CLI constraint into a BIDSFlow usability problem

What BIDSFlow should record:

- the input selection method
- the sample paths used for skeleton
- whether skeleton ran as a single-directory attempt or as a
  multi-session split
- the configured launcher
- skeleton work directory
- HeuDiConv version
- the generated `.heudiconv` state path
- the generated heuristic skeleton path
- the generated `dicominfo` inventory directory and copied file paths
- any temporary subject or session labels BIDSFlow had to generate

What BIDSFlow should expose as artifacts:

- `heuristic_template`
- `dicom_inventory_dir`
- `dicom_inventories`
- `heudiconv_state`
- `skeleton_report`

Suggested first public shape:

```bash
bidsflow heudiconv skeleton <sample-path>... [--reset] [--dry-run]
```

Suggested first API behavior:

- `<sample-path>...` identifies one or more representative DICOM samples
- when one sample path is provided, BIDSFlow runs one skeleton unit
  with a generated temporary subject label
- when multiple sample paths are provided, BIDSFlow treats them as
  separate single-directory skeleton units and assigns temporary
  session labels such as `skeleton-ses01`
- `--reset` is required before regenerating skeleton outputs for the
  same project skeleton state
- `--dry-run` shows the planned command, output files, and state paths

Suggested generated files:

- `code/heudiconv/heuristic.py`
- `code/heudiconv/dicominfo/`
- `state/heudiconv/skeleton-work/`
- `state/heudiconv/skeleton.json`

Ordering note:

- `skeleton` does not need to wait for `manifest`
- in practice it often happens earlier because heuristic editing starts
  from representative sample data rather than from finalized dataset
  naming

Important rerun rule:

- the official tutorial says skeleton should normally be done once per
  project and repeated only after removing `.heudiconv`

Design implication:

- BIDSFlow should never silently reuse a stale skeleton run when the
  user is asking to regenerate the starter material
- a repeated skeleton should either require an explicit reset or write
  to a new state location

Source notes:

- HeuDiConv custom heuristic tutorial:
  [https://heudiconv.readthedocs.io/en/latest/custom-heuristic.html](https://heudiconv.readthedocs.io/en/latest/custom-heuristic.html)
- HeuDiConv CLI reference, `--files` and `-s/--subjects` behavior:
  [https://heudiconv.readthedocs.io/en/latest/commandline.html](https://heudiconv.readthedocs.io/en/latest/commandline.html)

### 4.3 `edit-heuristic`

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

### 4.4 `convert`

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
- this is distinct from skeleton because it belongs to the actual
  conversion contract, not to starter-material generation

Suggested first public shape:

```bash
bidsflow heudiconv convert [--dry-run]
```

Suggested first API behavior:

- `convert` should use the project-owned heuristic generated or chosen
  after skeleton
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
- when conversion needs a normalized input tree, it should materialize
  a temporary links view from the confirmed manifest rather than
  treating links as a second truth source
- those temporary links should live under the run state area and should
  normally be removed after success or failure
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

### 4.5 Optional maintenance actions

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

- a manifest run model
- a skeleton run model
- explicit heuristic-edit handoff metadata
- a convert run model
- launcher support for local or wrapped HeuDiConv execution
- identity-mapping and anonymization planning for convert
- automatic `BIDSLayout` database construction after successful convert
- run records and artifact registration for those steps

Implemented now:

- manifest planning and generation
- skeleton planning and execution
- optional `[heudiconv].launcher` support
- `[heudiconv].heuristic` parsing and skeleton destination support
- skeleton artifact copying and run-record writing

Not implemented yet:

- managed convert execution
- identity-mapping and anonymization behavior
- automatic `BIDSLayout` indexing

It should defer:

- standalone maintenance actions unless they solve a concrete gap
- cluster submission
- automatic participants bookkeeping beyond what HeuDiConv already
  handles
- automatic heuristic authoring
- advanced HeuDiConv custom actions unrelated to the core workflow

## 6. Recommended implementation order

1. Define the HeuDiConv run record and artifact shapes.
2. Implement `manifest` and `skeleton` as independent preparation
   tracks.
3. Keep their user-facing order flexible, even if the codebase happens
   to land one before the other.
4. Implement the explicit handoff into human heuristic editing.
5. Define launcher, identity-mapping, and anonymization configuration
   for `convert`.
6. Implement `convert` together with raw BIDS `BIDSLayout` indexing.
7. Add optional maintenance actions only if a real workflow gap remains.

This keeps the first slice aligned with the official workflow rather
than with an overgeneralized app abstraction.

