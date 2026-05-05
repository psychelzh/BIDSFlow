---
name: project-config-schema
description: Maintain the BIDSFlow project configuration shape across the README, scaffold template, implementation, tests, issues, and pull requests. Use when defining or renaming config concepts for `bidsflow init`, execution requests, path naming, or future schema fields.
---

# Project Config Schema

Keep the user-facing TOML shape, future implementation hooks, and
documented semantics aligned.

## Quick Start

Start by reading these files:

- `README.md`
- `src/bidsflow/templates/bidsflow.toml.template`
- `src/bidsflow/project.py`
- `src/bidsflow/cli.py`
- `tests/`

Read `references/schema-rules.md` before making non-trivial schema changes.

## Working Rules

Apply schema changes across every affected surface in the same change:

- public files that define the active shape
- scaffold templates
- `src/bidsflow/project.py`, which is the implementation source of truth for
  active fields, defaults, and validation
- code that consumes the settings
- README examples when they mention the setting

Keep the schema explicit and typed:

- prefer explicit filesystem and scope names over vague toggles
- keep `init` output minimal in the first implementation
- group future settings by stable workflow concern instead of leaking tool internals
- prefer target-aware terminology over stage-first terminology
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
- init scaffold change
- target-specific addition

Then make the change in this order:

1. Update the scaffold template or implementation source of truth.
2. Update any README examples that mention the setting.
3. Update implementation consumers.
4. Check that the naming still matches task and target terminology.
5. Run the validation checks listed below when applicable.

## Validation

Validate terminology and public shape consistency across:

- `README.md`
- `src/bidsflow/templates/bidsflow.toml.template`
- `src/bidsflow/project.py`
- related tests

Run the narrowest code and CLI checks that cover the affected schema
surface.

## References

Use `references/schema-rules.md` for naming, compatibility, and
change-checklist guidance.
