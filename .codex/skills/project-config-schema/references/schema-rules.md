# Schema Rules

## Scope

Use this reference when touching the public configuration surface of
BIDSFlow.

Current public anchors:

- `src/bidsflow/config/models.py`
- `examples/project.toml`
- `README.md`
- `docs/design/config-strategy.md`
- `docs/design/cli-conventions.md`
- stage and handoff design docs under `docs/design/`

## Naming Rules

- Keep TOML section names stable and human-readable.
- Match stage names to the canonical stage ids in
  `src/bidsflow/core/stages.py`.
- Use one name per concept across TOML, Python, and docs.
- Prefer `*_root` for directory roots and `*_path` for individual files.
- Prefer positive, concrete names over abstract toggles.
- Keep project-default config names distinct from invocation-only CLI
  flags such as scope selectors.

## Compatibility Rules

- Favor additive settings over silent behavior changes.
- If introducing a new default, document the operational effect.
- If renaming a key, update all examples and all consumers in the same
  patch.
- Do not let examples drift from the actual models.
- Keep future cluster settings under execution or backend-specific
  sections unless a stronger boundary emerges.
- Do not force new config keys to exist for commands that are intended
  to remain config-optional.

## Modeling Rules

- Prefer Pydantic field constraints for numeric bounds.
- Prefer `Literal` when the value set is intentionally closed.
- Use `Path` for filesystem paths instead of raw strings.
- Keep stage-specific models small and focused on the stage contract.
- Prefer one root project config plus references to external
  tool-specific artifacts over mirroring an entire upstream CLI.
- Avoid hidden inference that makes generated commands hard to predict.

## Change Checklist

1. Update `src/bidsflow/config/models.py`.
2. Update `examples/project.toml`.
3. Update any code that reads the changed fields.
4. Update docs if semantics changed.
5. Run `python -m mypy src`.
6. Run `python -m ruff check .`.
