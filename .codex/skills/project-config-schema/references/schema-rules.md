# Schema Rules

## Scope

Use this reference when touching the public configuration surface of
BIDSFlow.

Current public anchors:

- `README.md`
- `src/bidsflow/templates/bidsflow.toml.template`
- `src/bidsflow/project.py`
- related tests

## Naming Rules

- Keep TOML section names stable and human-readable.
- Prefer task and target terminology over stage terminology.
- Use one name per concept across TOML, Python, and docs.
- Prefer `*_root` for directory roots and `*_path` for individual files.
- Prefer positive, concrete names over abstract toggles.

## Compatibility Rules

- Favor additive settings over silent behavior changes.
- If introducing a new default, document the operational effect.
- If renaming a key, update all examples and all consumers in the same
  patch.
- Do not let examples drift from the scaffold template or CLI behavior.
- Keep future cluster settings under execution or backend-specific
  sections unless a stronger boundary emerges.
- Keep `init` scaffold output conservative.

## Modeling Rules

- Prefer explicit, user-editable names over hidden inference.
- Use `*_root` and `*_path` consistently.
- Keep target-specific fields focused on the target contract.
- Avoid baking backend or scheduler defaults into `init`.
- Avoid hidden inference that makes future generated commands hard to
  predict.

## Change Checklist

1. Update the scaffold template or implementation source of truth.
2. Update any README examples.
3. Update implementation consumers.
4. Re-check terminology against the CLI and project config model.
