# Project initialization

## 1. Purpose

`bidsflow init` should create a minimal project scaffold and nothing
more.

It is a repository setup command, not a source-ingestion command and not
an execution command.

## 2. Public Shape

The command should look like:

```bash
bidsflow init [DIRECTORY]
```

The directory argument should be positional and default to `.`.

## 3. Initial Responsibilities

The first implementation of `init` should:

- create the target directory if needed
- create a minimal project directory layout
- write a minimal editable config file

Suggested scaffold contents:

- `sourcedata/`
- `sourcedata/raw/`
- `derivatives/`
- `work/`
- `logs/`
- `state/`
- `project.toml`

## 4. Initial Options

The first option set should stay small:

- `--name`
- `--config-name`
- `--force`

These cover the main scaffold customizations without forcing early
decisions about execution internals.

## 5. What `init` Should Not Do

`init` should not:

- choose backend defaults
- choose scheduler defaults
- generate tool-specific configuration
- generate heuristic code
- inspect source directories
- submit or preview execution

Those concerns belong to later tasks such as `source`, `check`, and
`run`.

## 6. Example

```bash
bidsflow init .
bidsflow init /data/project --name "TJNU camp project"
bidsflow init /data/project --config-name bidsflow.toml --force
```

## 7. Summary

The main job of `init` is to give the user a clean place to begin.

It should be opinionated about scaffold shape, but conservative about
workflow semantics.
