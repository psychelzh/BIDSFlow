# SGE Site Configuration

## Goal

Use this guide to adapt BIDSFlow's SGE settings to a specific cluster
site, especially for Debian-packaged Son of Grid Engine deployments.

The example config in `examples/project.toml` is intentionally
conservative. It is meant to help you preview and submit a lightweight
placeholder stage first, then layer on site-specific queue, PE, and
resource settings.

## Inspect The Site

Before changing the config, inspect the cluster's available settings:

```bash
qconf -sql
qconf -spl
qconf -sc
```

Use them as follows:

- `qconf -sql`: choose a valid queue name
- `qconf -spl`: choose a valid parallel environment, if needed
- `qconf -sc`: choose valid resource names for walltime and memory

Useful focused queries:

```bash
qconf -sc | grep -E 'h_rt|h_vmem|mem_free|tmp_free'
```

## Conservative First Submission

Start with a single-slot job and no parallel environment:

```toml
[scheduler.sge]
queue = "short.q"
inherit_cwd = true
export_env = false
poll_interval_sec = 15

[scheduler.sge.default_resources]
slots = 1
walltime = "00:10:00"
memory = "1G"

[scheduler.sge.resource_map]
walltime = "h_rt"
memory = "mem_free"
```

This minimizes the number of site-specific assumptions.

## When To Set These Fields

Set `queue` when your site expects jobs to target a specific queue.

Set `project` only if your site requires `qsub -P ...`. If you are not
sure, leave it unset at first.

Set `parallel_environment` only when:

- the queue supports multi-slot jobs
- you have confirmed the PE name from `qconf -spl`

If your site only reports `ompi`, do not use `smp`.

## Memory And Time Mapping

Do not assume all SGE sites use the same resource names.

Common patterns include:

- `h_rt` for hard runtime limit
- `h_vmem` for hard memory limit
- `mem_free` for scheduler-side free-memory requests

For a conservative first pass, prefer `mem_free` over `h_vmem`.

## Validation Workflow

1. Validate the config:

```bash
bidsflow config validate --config examples/project.toml
```

1. Preview the stage invocation:

```bash
bidsflow fmriprep \
  --config examples/project.toml \
  --subject-label sub-001 \
  --dry-run
```

1. Submit the lightweight placeholder job:

```bash
bidsflow fmriprep \
  --config examples/project.toml \
  --subject-label sub-001
```

At the current development stage, this submits a lightweight BIDSFlow
placeholder command rather than a real fMRIPrep container invocation.

## Troubleshooting

If submission fails, compare the generated `qsub` options against the
site output from `qconf`.

Most early failures come from:

- an invalid queue name
- an invalid project name
- a missing or incorrect parallel environment
- resource names that do not exist on the site
