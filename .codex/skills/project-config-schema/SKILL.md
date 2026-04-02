---
name: project-config-schema
description: Maintain the BIDSFlow project configuration shape across design docs, scaffold expectations, and future implementation. Use when defining or renaming config concepts for `bidsflow init`, target-aware execution requests, path naming, or future schema fields, and when keeping config terminology aligned with the task-first CLI.
---

# Project Config Schema

Keep the user-facing TOML shape, future implementation hooks, and
documented semantics aligned.

## Quick Start

Start by reading these files:

- `README.md`
- `docs/design/project-init.md`
- `docs/design/task-first-cli.md`
- `docs/design/target-model.md`
- `docs/design/handoff-contract.md`

Read `references/schema-rules.md` before making non-trivial schema changes.

## Working Rules

Apply schema changes across every affected surface in the same change:

- design docs that define the public shape
- scaffold examples embedded in docs
- future model fields and defaults
- future code that consumes the settings
- docs that define the meaning of the setting

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

1. Update the design docs that define the public shape.
2. Update any scaffold snippets or examples in the docs.
3. Update implementation only if that implementation exists.
4. Check that the naming still matches task and target terminology.
5. Run the validation checks listed below when applicable.

## Validation

During the current docs-first phase, validate terminology and public
shape consistency across:

- `README.md`
- `docs/design/project-init.md`
- `docs/design/task-first-cli.md`
- `docs/design/target-model.md`

When implementation returns, run the narrowest code and CLI checks that
cover the affected schema surface.

## References

Use `references/schema-rules.md` for naming, compatibility, and
change-checklist guidance.
