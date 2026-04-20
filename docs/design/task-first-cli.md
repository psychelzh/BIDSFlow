# Task-first CLI

## 1. Purpose

BIDSFlow should present a CLI that is organized around **tasks** rather
than around a grab bag of app names.

The public question should be:

- what do you want to do
- and, if needed, which target do you want to do it to

## 2. Current Command Surface

The current rebuilt slice ships a deliberately small surface:

```bash
bidsflow init [DIRECTORY]
bidsflow heudiconv init
bidsflow heudiconv draft <sample-path>...
bidsflow heudiconv
```

This reflects the work implemented so far: project setup plus the
managed HeuDiConv preparation and conversion flow.

## 3. Future Command Grammar

A future broader target model may use:

```text
bidsflow <task> [target] [options]
```

Possible future examples:

```bash
bidsflow init .
bidsflow check curate
bidsflow run fmriprep
bidsflow status xcpd
```

These commands are design direction, not the shipped CLI in the current
HeuDiConv slice.

## 4. Top-level Commands

The current top-level command set is:

- `init`
- `heudiconv`

This is enough for project setup and managed HeuDiConv
preparation/execution.

Other task namespaces can be added later once they carry stable,
non-trivial behavior. They should not appear in the first rebuilt CLI
just to mirror an internal implementation detail.

## 5. Why Targets Should Not Be Top-level Commands

A CLI such as:

- `bidsflow fmriprep`
- `bidsflow mriqc`
- `bidsflow qsiprep`

pushes the product back toward tool-first branding.

That is the wrong center of gravity for BIDSFlow because:

1. it makes the package look like a wrapper bundle
2. it weakens BIDSFlow's own public language
3. it encourages exposure of tool-native flags
4. it makes non-app targets such as `curate` feel second-class

Targets should be visible, but they should sit under tasks such as
`check`, `run`, and `status`.

This is a future CLI direction. The current `heudiconv` namespace is a
managed workflow slice and should not be treated as the final target
model.

## 6. How BIDS Apps Stay Visible

App-backed targets do not need to disappear.

They can appear directly as target names:

- `bidsflow run fmriprep`
- `bidsflow run mriqc`
- `bidsflow run xcpd`

This is enough to keep the intended app explicit without letting app
names define the entire CLI tree.

An `app` namespace can still exist later for inspection or metadata,
but it is not needed for the main execution path.

## 7. Parameter Style

Public parameters should remain logistics-oriented.

Good public parameters:

- `--subject-label`
- `--session-label`
- `--all`
- `--dry-run`
- input paths, source-table paths, and output roots when a target needs
  them

Avoid exposing raw native tool flags directly in the public surface.

Tool-specific details belong in adapters and configuration, not in the
top-level BIDSFlow CLI.

## 8. Current Example Flow

```bash
bidsflow init .
bidsflow heudiconv init
bidsflow heudiconv draft sourcedata/SUB001_SES01
bidsflow heudiconv
```

## 9. Possible Future Flow

```bash
bidsflow init .
bidsflow check curate --subject-label 041 --session-label 01
bidsflow run curate --subject-label 041 --session-label 01
bidsflow run fmriprep --subject-label 041
bidsflow status fmriprep
```

## 10. Summary

BIDSFlow should eventually own the verbs.

Targets, including BIDS Apps, should remain explicit as nouns under
those verbs.

The current rebuilt CLI keeps the shipped surface minimal: `init` and
the managed `heudiconv` task flow. Broader task verbs such as `check`,
`run`, and `status` should be added only when their runtime contracts are
implemented.
