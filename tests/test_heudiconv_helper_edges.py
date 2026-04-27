from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import bidsflow.common as bidsflow_common
import bidsflow.heudiconv as h
import bidsflow.heudiconv.draft as heudiconv_draft
import bidsflow.heudiconv.run as heudiconv_run
import bidsflow.heudiconv.sources as heudiconv_sources
import bidsflow.heudiconv.state as heudiconv_state
from helpers import read_key_value_file


def test_low_level_run_helpers_cover_status_paths(tmp_path: Path, monkeypatch) -> None:
    unit = h.RunUnitPlan(
        index=1,
        unit_name="sub-001",
        source_name="SUB001",
        source_path=tmp_path / "source",
        subject_label="001",
        session_label=None,
        execution_path=tmp_path / "exec" / "sub-001",
        command=("heudiconv",),
        log_path=tmp_path / "logs" / "sub-001.log",
        status_path=tmp_path / "state" / "sub-001.status",
        claim_path=tmp_path / "claims" / "sub-001.running",
    )

    assert heudiconv_run._unit_status_value(unit.status_path) == ""
    heudiconv_run._write_unit_status(
        unit,
        status="succeeded",
        backend="sge",
        attempt_label="attempt",
        started_at="start",
        finished_at="finish",
        exit_code=0,
        log_dir=tmp_path / "logs",
        scheduler_job_id="123",
        scheduler_task_id=None,
        error="multi\nline\rerror",
    )
    assert heudiconv_run._unit_status_value(unit.status_path) == "succeeded"
    payload = read_key_value_file(unit.status_path)
    assert payload["task_id"] == ""
    assert payload["error"] == "multi line error"

    original_read_text = Path.read_text

    def _raise_os_error(path: Path, *args, **kwargs):
        if path == unit.status_path:
            raise OSError("cannot read")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _raise_os_error)
    assert heudiconv_run._unit_status_value(unit.status_path) == ""
    monkeypatch.undo()

    heudiconv_run._release_unit_claim(unit)
    heudiconv_run._write_unit_claim(unit=unit)
    assert unit.claim_path.exists()
    other_unit = replace(
        unit,
        unit_name="sub-002",
        claim_path=tmp_path / "claims" / "sub-002.running",
        status_path=tmp_path / "state" / "sub-002.status",
    )
    heudiconv_run._write_unit_claim(unit=other_unit)
    heudiconv_run._release_unfinished_claims((unit, other_unit), [], unit)
    assert not unit.claim_path.exists()
    assert not other_unit.claim_path.exists()

    unit.status_path.write_text("# comment\nignored\nstatus=succeeded\n", encoding="utf-8")
    assert heudiconv_state.read_key_value_status(unit.status_path) == {"status": "succeeded"}


def test_low_level_path_and_scheduler_helpers(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    outside_path = tmp_path / "outside"
    outside_path.mkdir()

    assert not heudiconv_run._cleanup_run_execution_view(project_root, outside_path)
    with pytest.raises(ValueError, match="outside the project root"):
        bidsflow_common._remove_project_path(project_root, outside_path)
    with pytest.raises(h.HeudiconvRunError, match="must name an immediate child directory"):
        heudiconv_sources._resolve_sources_source_path(project_root, "../SUB001")

    log_path = project_root / "logs" / "unit.log"
    bidsflow_common._append_log(log_path, "first")
    bidsflow_common._append_log(log_path, "second")
    assert log_path.read_text(encoding="utf-8") == "first\n\nsecond\n"

    assert bidsflow_common._combine_process_output("out\n", "err\n") == "out\nerr"
    assert heudiconv_run._parse_sge_job_id(None) == ""
    assert heudiconv_run._parse_sge_job_id("123.1 first line\nignored") == "123.1 first line"

    sources_plan = h.SourcesPlan(
        source_root=project_root / "sourcedata",
        sources_path=project_root / "state" / "sources.tsv",
        sources_state_path=project_root / "state" / "sources.json",
        pattern=None,
        command=None,
        entries=(),
    )
    sources_plan.sources_path.parent.mkdir(parents=True)
    sources_plan.sources_path.write_text("existing\n", encoding="utf-8")
    with pytest.raises(h.SourcesError, match="Existing BIDSFlow sources state"):
        heudiconv_sources._guard_sources_reset_requirement(sources_plan, reset=False)
    outside_sources_plan = replace(sources_plan, sources_path=tmp_path / "outside.tsv")
    (tmp_path / "outside.tsv").write_text("outside\n", encoding="utf-8")
    with pytest.raises(h.SourcesError, match="outside the project root"):
        heudiconv_sources._reset_sources_state(project_root, outside_sources_plan)

    draft_plan = h.DraftPlan(
        sample_paths=(),
        launcher=("heudiconv",),
        units=(),
        code_root=project_root / "code" / "heudiconv",
        heuristic_path=project_root / "code" / "heudiconv" / "heuristic.py",
        dicominfo_root=project_root / "code" / "heudiconv" / "dicominfo",
        draft_work_root=tmp_path / "outside-draft-work",
        heudiconv_state_path=project_root / "work" / ".heudiconv",
        draft_state_path=project_root / "state" / "heudiconv" / "draft.json",
        log_dir=project_root / "logs" / "heudiconv",
    )
    draft_plan.draft_work_root.mkdir()
    with pytest.raises(h.HeudiconvDraftError, match="outside the project root"):
        heudiconv_draft._reset_draft_state(project_root, draft_plan)


def test_make_executable_sets_posix_execute_bit(tmp_path: Path) -> None:
    executable = tmp_path / "script.sh"
    executable.write_text("#!/bin/sh\n", encoding="utf-8", newline="\n")

    bidsflow_common._make_executable(executable)

    assert executable.stat().st_mode & 0o111


def test_materialize_run_execution_view_error_paths(tmp_path: Path, monkeypatch) -> None:
    source_path = tmp_path / "source"
    source_path.mkdir()
    execution_path = tmp_path / "exec" / "source"

    symlink_calls: list[tuple[Path, bool]] = []

    def _record_symlink(self, target, target_is_directory=False):
        symlink_calls.append((Path(target), target_is_directory))

    monkeypatch.setattr(Path, "symlink_to", _record_symlink)
    heudiconv_run._materialize_run_execution_view(execution_path, source_path)
    assert symlink_calls == [(source_path, True)]

    def _raise_symlink_error(self, target, target_is_directory=False):
        raise OSError("no symlink")

    monkeypatch.setattr(Path, "symlink_to", _raise_symlink_error)
    with pytest.raises(h.HeudiconvRunError, match="Failed to create temporary execution link"):
        heudiconv_run._materialize_run_execution_view(execution_path, source_path)
