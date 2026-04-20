# BIDSFlow

BIDSFlow is a CLI for managing BIDS workflow logistics.

The current implementation focuses on managed HeuDiConv preparation and
conversion.

## Requirements

BIDSFlow targets Unix-like environments: Linux, macOS, WSL, and typical
HPC login or compute nodes.

Native Windows execution is not supported. On Windows, use WSL.

## Installation

```bash
pip install .
```

## Example

Minimal project flow:

```bash
bidsflow init . --make-dirs
bidsflow heudiconv init
bidsflow heudiconv draft sourcedata/SUB001_SES01
bidsflow heudiconv --dry-run
bidsflow heudiconv
```

## Configuration

Project configuration lives in `bidsflow.toml`.

## Development

```bash
pip install -e ".[dev]"
python -m ruff check .
python -m pytest
```
