# SGE Patterns

## Scope

Use this reference when implementing scheduler-facing behavior for
BIDSFlow on HPC systems.

## Design Boundary

The SGE layer should consume a launch spec. It should not decide BIDS App
flags or rewrite stage semantics.

Keep these boundaries intact:

- command building happens before scheduling
- resource mapping happens in the scheduler layer
- state persistence happens alongside submission and polling

## Default Execution Unit

Use `participant x stage` as the default submitted unit for heavy
applications.

Consider job arrays only when:

- commands differ only by scope values
- resources are effectively identical
- logging can still be traced back to each unit
- failure handling remains acceptable

## Submission Checklist

1. Resolve the launch spec first.
2. Map execution settings to SGE fields such as queue, project, parallel
   environment, slots, and `-l` resource requests.
3. Render a reproducible script or direct submission payload.
4. Submit with `qsub -terse` when available.
5. Store the job id and submission metadata immediately.

## State Fields

Persist at least:

- job id
- scheduler name
- stage id
- scope unit
- resource request
- dependency expression
- log paths
- script path
- submission time

## SoGE Notes

- Treat Debian-packaged Son of Grid Engine as the first concrete target.
- Keep `qsub`, `qstat`, `qdel`, and `qacct` as the primary interface.
- Keep DRMAA1 optional behind a separate driver path.
- Do not assume site-specific resource names are portable.
- Prefer explicit environment exports over blanket `-V` inheritance.
