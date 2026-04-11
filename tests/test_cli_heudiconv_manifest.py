from __future__ import annotations

import csv
import json
from pathlib import Path
import sys

from typer.testing import CliRunner

from bidsflow.cli import app

runner = CliRunner()


def _read_manifest_rows(manifest_path: Path) -> list[dict[str, str]]:
    with manifest_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return list(reader)


def _append_manifest_config(config_path: Path, lines: list[str]) -> None:
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + "\n" + "\n".join(lines) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def test_heudiconv_manifest_dry_run_shows_blank_label_behavior(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    source_root = project_dir / "sourcedata"
    (source_root / "SUB001_SES01").mkdir(parents=True)
    (source_root / "SUB001_SES02").mkdir(parents=True)

    result = runner.invoke(
        app,
        [
            "heudiconv",
            "manifest",
            "--config",
            str(project_dir / "bidsflow.toml"),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Planned HeuDiConv manifest generation." in result.output
    assert "Entries discovered: 2" in result.output
    assert "Label generation: no template or command configured" in result.output
    assert "Links: not created by manifest" in result.output
    assert str(project_dir / "code" / "heudiconv" / "manifest.tsv") in result.output
    assert str(project_dir / "state" / "heudiconv" / "manifest.json") in result.output


def test_heudiconv_manifest_writes_blank_review_table_by_default(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    source_root = project_dir / "sourcedata"
    (source_root / "SUB001_SES01").mkdir(parents=True)
    (source_root / "SUB002_SES03").mkdir(parents=True)

    result = runner.invoke(
        app,
        [
            "heudiconv",
            "manifest",
            "--config",
            str(project_dir / "bidsflow.toml"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Wrote HeuDiConv manifest." in result.output
    assert "- total: 2" in result.output
    assert "- needs_review: 2" in result.output
    assert "Review needed:" in result.output

    manifest_path = project_dir / "code" / "heudiconv" / "manifest.tsv"
    manifest_state_path = project_dir / "state" / "heudiconv" / "manifest.json"

    assert manifest_path.is_file()
    assert manifest_state_path.is_file()

    rows = _read_manifest_rows(manifest_path)
    assert [row["source_name"] for row in rows] == ["SUB001_SES01", "SUB002_SES03"]
    assert all(row["subject_label"] == "" for row in rows)
    assert all(row["session_label"] == "" for row in rows)
    assert all(row["include"] == "true" for row in rows)
    assert all(row["status"] == "needs_review" for row in rows)

    state = json.loads(manifest_state_path.read_text(encoding="utf-8"))
    assert state["step"] == "manifest"
    assert state["status"] == "succeeded"
    assert state["source_root"] == str(source_root.resolve())
    assert state["artifacts"]["manifest"] == str(manifest_path)
    assert state["handoff"]["role"] == "truth_source"
    assert state["handoff"]["derived_execution_views"]["links"]["managed_by"] == "convert"
    assert state["handoff"]["derived_execution_views"]["links"]["lifecycle"] == "ephemeral"
    assert state["entry_count"] == 2
    assert state["status_summary"]["needs_review"] == 2
    assert "entries" not in state


def test_heudiconv_manifest_applies_configured_template(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    config_path = project_dir / "bidsflow.toml"
    _append_manifest_config(
        config_path,
        [
            "[heudiconv.manifest]",
            'template = "CAMP_SUB{subject}_VISIT{session}"',
        ],
    )

    source_root = project_dir / "sourcedata"
    (source_root / "CAMP_SUB041_VISIT01").mkdir(parents=True)
    (source_root / "CAMP_SUB041_VISIT02").mkdir(parents=True)

    result = runner.invoke(
        app,
        [
            "heudiconv",
            "manifest",
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "- ready: 2" in result.output
    assert "Review needed:" not in result.output

    rows = _read_manifest_rows(project_dir / "code" / "heudiconv" / "manifest.tsv")
    assert [row["subject_label"] for row in rows] == ["041", "041"]
    assert [row["session_label"] for row in rows] == ["01", "02"]
    assert all(row["status"] == "ready" for row in rows)

    state = json.loads((project_dir / "state" / "heudiconv" / "manifest.json").read_text(encoding="utf-8"))
    assert state["label_generation"]["template"] == "CAMP_SUB{subject}_VISIT{session}"
    assert state["label_generation"]["command"] is None


def test_heudiconv_manifest_applies_configured_command(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    command_script = project_dir / "code" / "heudiconv" / "derive_labels.py"
    command_script.parent.mkdir(parents=True, exist_ok=True)
    command_script.write_text(
        "\n".join(
            (
                "import re",
                "import sys",
                "",
                "source_name = sys.argv[-1]",
                "match = re.search(r'SUB(\\d+).*?(?:VISIT|SES)(\\d+)', source_name)",
                "if not match:",
                "    raise SystemExit('cannot parse labels')",
                "print(match.group(1))",
                "print(match.group(2))",
            )
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    config_path = project_dir / "bidsflow.toml"
    python_executable = sys.executable.replace("\\", "\\\\")
    _append_manifest_config(
        config_path,
        [
            "[heudiconv.manifest]",
            f'command = ["{python_executable}", "code/heudiconv/derive_labels.py"]',
        ],
    )

    source_root = project_dir / "sourcedata"
    (source_root / "SITE_SUB001_VISIT01").mkdir(parents=True)
    (source_root / "SITE_SUB001_VISIT02").mkdir(parents=True)

    result = runner.invoke(
        app,
        [
            "heudiconv",
            "manifest",
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Label generation used command:" in result.output
    assert (
        "Command contract: source_name is passed as the last argv item; cwd is project_root; "
        "stdout line 1 is subject_label; stdout line 2 is optional session_label."
        in result.output
    )

    rows = _read_manifest_rows(project_dir / "code" / "heudiconv" / "manifest.tsv")
    assert [row["subject_label"] for row in rows] == ["001", "001"]
    assert [row["session_label"] for row in rows] == ["01", "02"]
    assert all(row["status"] == "ready" for row in rows)


def test_heudiconv_manifest_reports_collisions(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    config_path = project_dir / "bidsflow.toml"
    command_script = project_dir / "code" / "heudiconv" / "derive_collision_labels.py"
    command_script.parent.mkdir(parents=True, exist_ok=True)
    command_script.write_text(
        "\n".join(
            (
                "print('001')",
                "print('01')",
            )
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    python_executable = sys.executable.replace("\\", "\\\\")
    _append_manifest_config(
        config_path,
        [
            "[heudiconv.manifest]",
            f'command = ["{python_executable}", "code/heudiconv/derive_collision_labels.py"]',
        ],
    )

    source_root = project_dir / "sourcedata"
    (source_root / "SITEA_SUB001_VISIT01").mkdir(parents=True)
    (source_root / "SITEB_SUB001_VISIT01").mkdir(parents=True)

    result = runner.invoke(
        app,
        [
            "heudiconv",
            "manifest",
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "- collision: 2" in result.output
    assert "Review needed:" in result.output

    rows = _read_manifest_rows(project_dir / "code" / "heudiconv" / "manifest.tsv")
    assert rows[0]["status"] == "collision"
    assert rows[1]["status"] == "collision"


def test_heudiconv_manifest_rejects_command_with_more_than_two_lines(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    config_path = project_dir / "bidsflow.toml"
    command_script = project_dir / "code" / "heudiconv" / "derive_too_many_lines.py"
    command_script.parent.mkdir(parents=True, exist_ok=True)
    command_script.write_text(
        "\n".join(
            (
                "print('001')",
                "print('01')",
                "print('extra')",
            )
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    python_executable = sys.executable.replace("\\", "\\\\")
    _append_manifest_config(
        config_path,
        [
            "[heudiconv.manifest]",
            f'command = ["{python_executable}", "code/heudiconv/derive_too_many_lines.py"]',
        ],
    )

    source_root = project_dir / "sourcedata"
    (source_root / "SITE_SUB001_VISIT01").mkdir(parents=True)

    result = runner.invoke(
        app,
        [
            "heudiconv",
            "manifest",
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 2
    assert "returned more than two non-empty output lines" in result.output


def test_heudiconv_manifest_requires_reset_before_regenerating(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    source_root = project_dir / "sourcedata"
    (source_root / "SUB001_SES01").mkdir(parents=True)

    first = runner.invoke(
        app,
        ["heudiconv", "manifest", "--config", str(project_dir / "bidsflow.toml")],
    )
    assert first.exit_code == 0, first.output

    blocked = runner.invoke(
        app,
        ["heudiconv", "manifest", "--config", str(project_dir / "bidsflow.toml")],
    )
    assert blocked.exit_code == 2
    assert "Existing HeuDiConv manifest state was found" in blocked.output

    allowed = runner.invoke(
        app,
        [
            "heudiconv",
            "manifest",
            "--config",
            str(project_dir / "bidsflow.toml"),
            "--reset",
        ],
    )
    assert allowed.exit_code == 0, allowed.output


def test_heudiconv_manifest_uses_configured_source_root(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    config_path = project_dir / "bidsflow.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'source_root = "sourcedata"',
            'source_root = "incoming"',
        ),
        encoding="utf-8",
        newline="\n",
    )

    source_root = project_dir / "incoming"
    (source_root / "SUB010_SES01").mkdir(parents=True)

    result = runner.invoke(
        app,
        ["heudiconv", "manifest", "--config", str(config_path)],
    )

    assert result.exit_code == 0, result.output
    state = json.loads((project_dir / "state" / "heudiconv" / "manifest.json").read_text(encoding="utf-8"))
    assert state["source_root"] == str(source_root.resolve())


def test_heudiconv_manifest_rejects_mutually_exclusive_generation_config(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"

    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    config_path = project_dir / "bidsflow.toml"
    _append_manifest_config(
        config_path,
        [
            "[heudiconv.manifest]",
            'template = "SUB{subject}"',
            'command = ["python", "code/heudiconv/derive_labels.py"]',
        ],
    )

    result = runner.invoke(
        app,
        ["heudiconv", "manifest", "--config", str(config_path)],
    )

    assert result.exit_code == 2
    assert "may define template or command, but not both" in result.output
