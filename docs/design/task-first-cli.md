# Task-first CLI

## 1. Purpose

BIDSFlow should present a CLI that is organized around **tasks** rather
than around a grab bag of app names.

The public question should be:

- what do you want to do
- and, if needed, which target do you want to do it to

## 2. Command Grammar

The intended public grammar is:

```text
bidsflow <task> [target] [options]
```

Examples:

```bash
bidsflow init .
bidsflow check curate
bidsflow run fmriprep
bidsflow status xcpd
```

## 3. Top-level Commands

The initial top-level command set should stay small:

- `init`
- `check`
- `run`
- `status`

This is enough to express project setup, readiness checks, execution,
and state inspection.

Other task namespaces can be added later once they carry stable,
non-trivial behavior. They should not appear in the first rebuilt CLI
just to mirror an internal implementation detail.

## 4. Why Targets Should Not Be Top-level Commands

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

## 5. How BIDS Apps Stay Visible

App-backed targets do not need to disappear.

They can appear directly as target names:

- `bidsflow run fmriprep`
- `bidsflow run mriqc`
- `bidsflow run xcpd`

This is enough to keep the intended app explicit without letting app
names define the entire CLI tree.

An `app` namespace can still exist later for inspection or metadata,
but it is not needed for the main execution path.

## 6. Parameter Style

Public parameters should remain logistics-oriented.

Good public parameters:

- `--config`
- `--subject-label`
- `--session-label`
- `--all`
- `--dry-run`
- input paths, manifest paths, and output roots when a target needs them

Avoid exposing raw native tool flags directly in the public surface.

Tool-specific details belong in adapters and configuration, not in the
top-level BIDSFlow CLI.

## 7. Example Flow

```bash
bidsflow init .
bidsflow check curate --subject-label 041 --session-label 01
bidsflow run curate --subject-label 041 --session-label 01
bidsflow run fmriprep --subject-label 041
bidsflow status fmriprep
```

## 8. Summary

BIDSFlow should own the verbs.

Targets, including BIDS Apps, should remain explicit as nouns under
those verbs.

The first rebuilt CLI should keep that verb set minimal: `init`,
`check`, `run`, and `status`.
