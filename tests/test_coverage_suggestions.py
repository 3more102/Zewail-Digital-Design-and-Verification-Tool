from __future__ import annotations

import json
from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project
from zddv.coverage_suggestions import (
    suggest_coverage_hole_tests,
    write_coverage_test_suggestions,
)


def _project(tmp_path: Path):
    return initialize_project(tmp_path / "demo")


def test_coverage_hole_suggestions_preserve_explicit_semantics(tmp_path: Path):
    project = _project(tmp_path)
    report = {
        "filter_type": None,
        "total_holes": 4,
        "reported_holes": 4,
        "holes": [
            {
                "type": "toggle",
                "name": "top.dut.req 0->1",
                "signal": "top.dut.req",
                "toggle_transition": "0->1",
                "count": 0,
            },
            {
                "type": "fsm",
                "name": "rtl/fsm.sv:fsm0:transition:IDLE -> BUSY",
                "source_file": "rtl/fsm.sv",
                "fsm_id": "fsm0",
                "fsm_kind": "transition",
                "transition": "IDLE -> BUSY",
                "count": 0,
            },
            {
                "type": "expression",
                "name": "rtl/control.sv:20:1:req[2]",
                "source_file": "rtl/control.sv",
                "line": 20,
                "item": 1,
                "fec_target": "req[2]",
                "fec_hits": {"0": 7, "1": 0},
                "bit": 2,
                "multibit": True,
                "count": 0,
            },
            {
                "type": "statement",
                "name": "rtl/counter.sv:12:1",
                "source_file": "rtl/counter.sv",
                "line": 12,
                "item": 1,
                "count": 0,
            },
        ],
    }

    result = suggest_coverage_hole_tests(project, report)

    assert result["analysis"] == "coverage_hole_test_suggestions"
    assert result["summary"]["suggestions"] == 4
    assert result["summary"]["blockers"] == 0
    assert [item["action"] for item in result["suggestions"]] == [
        "drive_toggle_transition",
        "reach_fsm_transition",
        "exercise_fec_target",
        "exercise_source_coverage_item",
    ]
    assert result["suggestions"][0]["target"]["signal"] == "top.dut.req"
    assert result["suggestions"][1]["target"]["transition"] == "IDLE -> BUSY"
    assert result["suggestions"][2]["target"]["fec_hits"] == {"0": 7, "1": 0}
    assert all(item["review_required"] for item in result["suggestions"])
    assert not any(item["generated_test"] for item in result["suggestions"])


def test_coverage_hole_suggestions_do_not_invent_missing_targets(tmp_path: Path):
    project = _project(tmp_path)
    report = {
        "holes": [
            {"type": "condition", "name": "opaque-condition", "count": 0},
            {"type": "toggle", "name": "opaque-toggle", "count": 0},
            {"type": "unknown", "name": "", "count": 0},
        ]
    }

    result = suggest_coverage_hole_tests(project, report)

    assert result["suggestions"][0]["action"] == "exercise_normalized_coverage_point"
    assert "input" not in result["suggestions"][0]["target"]
    assert result["suggestions"][1]["action"] == "exercise_normalized_coverage_point"
    assert result["summary"]["blockers"] == 1
    assert result["blockers"][0]["code"] == "MISSING_HOLE_NAME"


def test_write_coverage_test_suggestions_and_cli(tmp_path: Path, capsys):
    project = _project(tmp_path)
    holes_path = project.root / ".zddv" / "coverage" / "holes.json"
    holes_path.parent.mkdir(parents=True, exist_ok=True)
    holes_path.write_text(
        json.dumps(
            {
                "filter_type": "branch",
                "total_holes": 1,
                "reported_holes": 1,
                "holes": [
                    {
                        "type": "branch",
                        "name": "rtl/a.sv:9:2 else",
                        "source_file": "rtl/a.sv",
                        "line": 9,
                        "item": 2,
                        "detail": "else",
                        "count": 0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    direct = write_coverage_test_suggestions(project)
    assert Path(direct["path"]).is_file()
    assert direct["suggestions"][0]["action"] == "exercise_branch_coverage_item"

    rc = main(
        [
            "--project",
            str(project.root),
            "coverage-suggest-tests",
            "--show",
            "1",
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "COVERAGE TEST SUGGESTIONS:" in output
    assert "exercise_branch_coverage_item" in output
    assert (project.root / ".zddv" / "coverage" / "test-suggestions.json").is_file()


def test_coverage_hole_suggestion_report_requires_holes_list(tmp_path: Path):
    project = _project(tmp_path)

    with pytest.raises(ValueError, match="holes"):
        suggest_coverage_hole_tests(project, {"total_holes": 1})
