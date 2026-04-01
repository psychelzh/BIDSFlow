---
name: project-config-schema
description: Maintain the BIDSFlow project configuration schema across `src/bidsflow/config/models.py`, `examples/project.toml`, CLI-facing defaults, and related docs. Use when adding, renaming, deprecating, or validating config keys; introducing backend or stage settings; normalizing defaults; or keeping config examples aligned with code.
---

# Project Config Schema

Keep the user-facing TOML shape, the internal Pydantic models, and the
documented semantics aligned.

## Quick Start

Start by reading these files:

- `src/bidsflow/config/models.py`
- `examples/project.toml`
- `README.md`
- `docs/design/config-strategy.md`
- `docs/design/cli-conventions.md`
- `docs/design/stage-model.md`
- `docs/design/handoff-contract.md`

Read `references/schema-rules.md` before making non-trivial schema changes.

## Working Rules

Apply schema changes across every affected surface in the same change:

- Pydantic model fields and defaults
- example configuration files
- code that consumes the settings
- docs that define the meaning of the setting

Keep the schema explicit and typed:

- prefer `Path`, `Literal`, and bounded numeric fields over free-form strings
- group settings by concern: project, execution, then per-stage sections
- keep stage-specific settings inside the matching stage block
- treat the root TOML as project defaults, not as a mandatory prerequisite
  for every command
- prefer references to external tool-native files over embedding complex
  native content directly in TOML
- avoid adding loosely typed `dict` escape hatches unless there is no
  stable alternative

Preserve operator clarity:

- choose names that map cleanly to CLI concepts and filesystem locations
- avoid synonyms for the same concept across TOML, code, and docs
- prefer additive changes over silent behavior changes
- if a rename is unavoidable, update every call site and example in the
  same patch

## Change Workflow

Classify the change before editing:

- additive key
- rename or deprecation
- default change
- backend-specific addition
- stage-specific addition

Then make the change in this order:

1. Update the root and nested Pydantic models.
2. Update example TOML files to show the intended public shape.
3. Update consuming code if any defaults or names changed.
4. Update docs if the meaning, workflow, or stage contract changed.
5. Run the validation commands listed below.

## Validation

Run the narrowest checks that cover the change:

```bash
python -m mypy src
python -m ruff check .
```

If the change affects CLI behavior or default rendering, also run:

```bash
bidsflow --help
bidsflow status
```

## References

Use `references/schema-rules.md` for naming, compatibility, and
change-checklist guidance.
