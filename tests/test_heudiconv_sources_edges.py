from __future__ import annotations

import sys
from pathlib import Path

import pytest

import bidsflow.heudiconv as h
import bidsflow.heudiconv.sources as heudiconv_sources
from helpers import (
    append_config,
    init_project,
    make_source_dirs,
    read_tsv_rows,
    set_sources_pattern,
    write_minimal_heuristic,
    write_python_script,
)


def test_sources_reports_missing_or_file_source_root(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = init_project(tmp_path, runner, name="sources-bad-root")

    missing = invoke_from(project_dir, ["heudiconv", "init"])
    assert missing.exit_code == 2
    assert "Configured source root does not exist" in missing.output

    (project_dir / "sourcedata").write_text("not a directory\n", encoding="utf-8")
    file_root = invoke_from(project_dir, ["heudiconv", "init"])
    assert file_root.exit_code == 2
    assert "Configured source root is not a directory" in file_root.output


@pytest.mark.parametrize(
    ("pattern", "message"),
    [
        ("SUB{subject:03d}", "does not support format specs or conversions"),
        ("SUB{bad-name}", "field is not a valid identifier"),
        ("SUB{site}", "only supports {subject} and optional {session}"),
        ("NO_SUBJECT", "must include a {subject} field"),
        ("SUB{subject}{subject}", "Invalid BIDSFlow sources pattern"),
    ],
)
def test_sources_rejects_invalid_patterns(tmp_path: Path, invoke_from, runner, pattern: str, message: str) -> None:
    project_dir = init_project(tmp_path, runner, name="bad-pattern")
    set_sources_pattern(project_dir / "bidsflow.toml", pattern)
    make_source_dirs(project_dir, "SUB001")

    result = invoke_from(project_dir, ["heudiconv", "init"])

    assert result.exit_code == 2
    assert message in result.output


@pytest.mark.parametrize(
    ("script_body", "message"),
    [
        ("raise SystemExit(7)", "sources command failed"),
        ("", "sources command returned empty output"),
        ("print('001')", None),
    ],
)
def test_sources_command_status_variants(
    tmp_path: Path,
    invoke_from,
    runner,
    script_body: str,
    message: str | None,
) -> None:
    project_dir = init_project(tmp_path, runner, name="command-source-project")

    script_path = project_dir / "code" / "heudiconv" / "derive.py"
    write_python_script(script_path, (script_body,))
    append_config(
        project_dir / "bidsflow.toml",
        ["[sources]", f'command = ["{sys.executable}", "{script_path.as_posix()}"]'],
    )
    make_source_dirs(project_dir, "SUB001")

    result = invoke_from(project_dir, ["heudiconv", "init"])

    if message is None:
        assert result.exit_code == 0, result.output
        rows = read_tsv_rows(project_dir / "state" / "sources.tsv")
        assert rows[0]["subject_label"] == "001"
        assert rows[0]["session_label"] == ""
        assert rows[0]["status"] == "ready"
    else:
        assert result.exit_code == 2
        assert message in result.output


def test_sources_command_reports_missing_executable(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = init_project(tmp_path, runner, name="missing-command-project")
    append_config(project_dir / "bidsflow.toml", ["[sources]", 'command = ["definitely-not-bidsflow"]'])
    make_source_dirs(project_dir, "SUB001")

    result = invoke_from(project_dir, ["heudiconv", "init"])

    assert result.exit_code == 2
    assert "Failed to start sources command" in result.output


def test_sources_command_rejects_unsafe_labels(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = init_project(tmp_path, runner, name="unsafe-command-label-project")
    script_path = project_dir / "code" / "heudiconv" / "derive.py"
    write_python_script(script_path, ("print('../001')",))
    append_config(
        project_dir / "bidsflow.toml",
        ["[sources]", f'command = ["{sys.executable}", "{script_path.as_posix()}"]'],
    )
    make_source_dirs(project_dir, "SUB001")

    result = invoke_from(project_dir, ["heudiconv", "init"])

    assert result.exit_code == 2
    assert "unsafe characters" in result.output


def test_manual_sources_table_rejects_unsafe_labels_before_run(tmp_path: Path, invoke_from, runner) -> None:
    project_dir = init_project(tmp_path, runner, name="unsafe-manual-label-project")
    make_source_dirs(project_dir, "SUB001")
    (project_dir / "state").mkdir()
    (project_dir / "state" / "sources.tsv").write_text(
        "\n".join(
            (
                "source_name\tsubject_label\tsession_label\tinclude\tstatus\tnotes",
                "SUB001\t../001\t\ttrue\tready\tmanual",
            )
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    write_minimal_heuristic(project_dir)

    result = invoke_from(project_dir, ["heudiconv"])

    assert result.exit_code == 2
    assert "unsafe characters" in result.output


def test_sources_pattern_miss_and_duplicate_subject_without_sessions(
    tmp_path: Path,
    invoke_from,
    runner,
) -> None:
    miss_project = init_project(tmp_path, runner, name="pattern-miss")
    set_sources_pattern(miss_project / "bidsflow.toml", "SUB{subject}")
    make_source_dirs(miss_project, "NO_MATCH")

    miss = invoke_from(miss_project, ["heudiconv", "init"])
    assert miss.exit_code == 0, miss.output
    miss_rows = read_tsv_rows(miss_project / "state" / "sources.tsv")
    assert miss_rows[0]["notes"] == "sources pattern did not match source_name"

    duplicate_project = init_project(tmp_path, runner, name="duplicate-subject")
    script_path = duplicate_project / "derive.py"
    write_python_script(script_path, ("print('001')",))
    append_config(
        duplicate_project / "bidsflow.toml",
        ["[sources]", f'command = ["{sys.executable}", "{script_path.as_posix()}"]'],
    )
    make_source_dirs(duplicate_project, "A", "B")

    duplicate = invoke_from(duplicate_project, ["heudiconv", "init"])
    assert duplicate.exit_code == 0, duplicate.output
    duplicate_rows = read_tsv_rows(duplicate_project / "state" / "sources.tsv")
    assert [row["status"] for row in duplicate_rows] == ["needs_review", "needs_review"]


def test_compute_sources_status_handles_invalid_and_outside_source_names(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    invalid_entries = heudiconv_sources._compute_sources_statuses(
        source_root,
        (
            h.SourcesEntry("../bad", "001", "", True, "", ""),
        ),
    )
    assert invalid_entries[0].status == "missing_source"

    original_resolve = Path.resolve

    def _fake_resolve(path: Path, *args, **kwargs):
        if path == source_root:
            return source_root
        if path == source_root / "LINK":
            return tmp_path / "outside"
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", _fake_resolve)
    with pytest.raises(h.HeudiconvRunError, match="resolves outside source_root"):
        heudiconv_sources._resolve_sources_source_path(source_root, "LINK")
