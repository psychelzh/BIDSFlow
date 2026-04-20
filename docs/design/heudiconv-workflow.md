# HeuDiConv workflow

## 1. Purpose

HeuDiConv is the first **managed workflow** rebuilt in BIDSFlow because
its official usage already has a real lifecycle:

1. generate starter heuristic material with `convertall`
2. inspect `dicominfo` files and edit the heuristic manually
3. run the actual DICOM-to-BIDS conversion with the reviewed heuristic

BIDSFlow should manage the logistics around that lifecycle without
turning itself into a flag-complete wrapper for every HeuDiConv option.

Platform scope:

- the managed HeuDiConv workflow is supported in Unix-like environments
  such as Linux, macOS, WSL, and HPC systems
- native Windows is not supported; Windows users should run BIDSFlow
  inside WSL
- temporary execution views are materialized with symlinks and are
  treated as Unix-like runtime artifacts

Source notes:

- HeuDiConv custom heuristic tutorial:
  [https://heudiconv.readthedocs.io/en/latest/custom-heuristic.html](https://heudiconv.readthedocs.io/en/latest/custom-heuristic.html)
- HeuDiConv CLI reference:
  [https://heudiconv.readthedocs.io/en/latest/commandline.html](https://heudiconv.readthedocs.io/en/latest/commandline.html)

## 2. Current implementation status

The current codebase implements three public actions:

```bash
bidsflow heudiconv init [--force]
bidsflow heudiconv draft <sample-path>... [--force] [--dry-run]
bidsflow heudiconv [--dry-run] [--clean-workdir|--keep-workdir]
```

### 2.1 `heudiconv init`

`bidsflow heudiconv init` prepares HeuDiConv support files without
calling HeuDiConv.

Current behavior:

- scans the immediate child directories under `[paths].source_root`
- does not call HeuDiConv
- writes the reviewable truth table to `state/sources.tsv`
- writes overall metadata to `state/sources.json`
- leaves final `subject_label/session_label` blank by default
- derives labels from `[sources].pattern` when configured
- calls `[sources].command` with `source_name` when configured
- recomputes and reports `ready`, `needs_review`, `collision`,
  `missing_source`, and `excluded` status counts
- creates HeuDiConv support directories such as `code/heudiconv/`
- if `[execution].scheduler = "sge"`, writes the scheduler script to the
  configured scheduler script path, currently
  `code/bidsflow/sge/heudiconv.sh`
- keeps existing init-managed files unless `--force` is used

`sources.tsv` is the durable table that users review and edit. The JSON
state file only records metadata such as the command result, paths, and
summary counts.

### 2.2 `heudiconv draft`

`bidsflow heudiconv draft` prepares heuristic starter material from
one or more representative sample directories.

Current behavior:

- interprets relative sample paths under `[paths].source_root`
- accepts absolute sample paths only when they still resolve under
  `[paths].source_root`
- processes one sample path as one draft unit with a temporary subject
  label
- processes multiple sample paths as separate draft units with
  temporary session labels such as `draft-ses01`
- runs HeuDiConv with `-f convertall -c none`
- writes working output under `work/heudiconv/draft-work/`
- copies the generated heuristic to `[heudiconv].heuristic`
- copies generated `dicominfo*.tsv` files to
  `code/heudiconv/dicominfo/`
- writes overall metadata to `state/heudiconv/draft.json`
- writes each unit's raw tool output to
  `logs/heudiconv/draft-<attempt>/<unit>.log`

There is intentionally no `draft.tsv`. Draft generation is a heuristic
authoring aid, not the final per-source run-status table.

### 2.3 `heudiconv`

`bidsflow heudiconv` runs the managed HeuDiConv path using the reviewed
source table and the chosen heuristic.

Current behavior:

- reads `state/sources.tsv`
- recomputes each row's current status from the table contents
- requires every included source row to be `ready`
- resolves each `source_name` under `[paths].source_root`
- validates `[heudiconv].heuristic`
- uses `[heudiconv].launcher`, defaulting to `["heudiconv"]`
- materializes a temporary execution view under
  `work/heudiconv/run-<attempt>/`
- runs one managed HeuDiConv invocation per ready source row
- cleans temporary execution views by default; `--keep-workdir`
  preserves them for debugging
- writes the BIDSFlow run/submission record to
  `state/heudiconv/run.json`
- writes unit final results to `state/heudiconv/results.tsv`
- writes local unit output to
  `logs/heudiconv/local/run-<attempt>/<unit>.log`
- captures SGE unit output under `logs/heudiconv/sge/run-<attempt>/`
- removes the temporary execution view after success or failure unless
  `--keep-workdir` is used

`results.tsv` is the per-unit final-results table for
`bidsflow heudiconv`. `run.json` is the BIDSFlow control-plane record
for the latest local run or scheduler submission and should not
duplicate the unit table.

Current limit:

- automatic persisted `BIDSLayout` indexing is still not implemented

## 3. Managed concerns

BIDSFlow should own the workflow logistics around HeuDiConv:

- locating source inputs from project config
- preserving a reviewable source table
- generating starter heuristic material in an isolated work area
- making heuristic editing an explicit human handoff
- running HeuDiConv with the selected heuristic and launcher
- keeping raw tool output in per-unit log files
- keeping current state metadata separate from attempt history
- exposing the raw BIDS dataset as a downstream artifact

BIDSFlow should not own the scientific content of the heuristic, infer
final BIDS labels from too little information, or hide native HeuDiConv
behavior that users may need to inspect.

## 4. Relationship between preparation actions

`heudiconv init` and `heudiconv draft` solve different preparation
problems.

`heudiconv init` standardizes dataset-wide source identity and final
label handoff. It is useful even if a project already has a working
heuristic. It also materializes target support files such as scheduler
scripts.

`heudiconv draft` produces sample-level heuristic starter material. It
is useful even before final subject/session naming is frozen.

Neither action is a strict prerequisite for the other. The managed
`heudiconv` run is the point where both a reviewed `sources.tsv` and a
reviewed heuristic are required.

## 5. State and log model

State files describe the current known result of a workflow action.
Attempt history belongs in `logs/`.

Current files:

- `state/sources.json`: overall metadata for the latest source scan
- `state/sources.tsv`: reviewed source table and handoff truth
- `state/heudiconv/draft.json`: overall metadata for the latest draft
  generation
- `logs/heudiconv/draft-<attempt>/<unit>.log`: per-unit draft tool
  output
- `state/heudiconv/run.json`: control-plane record for the latest
  `bidsflow heudiconv` local run or scheduler submission
- `state/heudiconv/results.tsv`: final result of each execution unit
- `logs/heudiconv/local/run-<attempt>/<unit>.log`: per-unit local run
  tool output
- `logs/heudiconv/sge/run-<attempt>/`: scheduler-captured SGE run
  tool output

This layout is intentionally friendly to future parallel execution:
different units can write different log files without interleaving tool
stdout and stderr in one shared stream.

## 6. Launcher model

Projects should define how HeuDiConv is invoked, while BIDSFlow appends
managed arguments.

Examples:

- `["heudiconv"]`
- `["singularity", "run", "/containers/heudiconv.sif"]`

This keeps the public workflow stable across local execution, wrappers,
and containers.

## 7. Config concepts

Current config concepts:

- `[paths].source_root`: source directories to scan and run
- `[paths].raw_bids_root`: curated raw BIDS output root
- `[paths].work_root`: transient execution views and work files
- `[paths].logs_root`: BIDSFlow orchestration logs
- `[paths].state_root`: BIDSFlow state metadata
- `[sources].pattern`: optional source-name-to-label pattern
- `[sources].command`: optional project command that returns final
  labels
- `[heudiconv].heuristic`: project-owned heuristic path
- `[heudiconv].launcher`: optional launcher prefix
- `[execution].scheduler`: optional scheduler family selected during
  `bidsflow init`
- `[execution].scheduler_template`: path template for the HeuDiConv
  scheduler script

`[sources].pattern` and `[sources].command` are mutually exclusive.
Both operate on `source_name`, not on full filesystem paths.

## 8. Future work

Not implemented yet:

- persisted `BIDSLayout` database construction after successful runs
- additional identity-mapping helpers beyond `sources.tsv`
- cluster submission and scheduler observation
- automatic participants bookkeeping beyond what HeuDiConv already
  handles

Recommended next order:

1. Keep the current source, draft, and run state model stable.
2. Add raw BIDS artifact registration after successful runs.
3. Add persistent raw BIDS `BIDSLayout` indexing.
4. Add optional maintenance actions only if a real workflow gap remains.
5. Add scheduler integration behind the same state and log boundaries.
