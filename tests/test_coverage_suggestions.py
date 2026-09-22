from __future__ import annotations

import json
from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project
from zddv.coverage_suggestions import (
    build_coverage_test_suggestions,
    write_coverage_test_suggestions,
)


def _holes() -> dict:
    return {
        "filter_type": None,
        "total_holes": 4,
        "reported_holes": 4,
        "by_type": {
            "statement": 1,
            "toggle": 1,
            "fsm": 1,
            "unknown": 1,
        },
        "holes": [
            {
                "type": "statement",
                "name": "stmt-12",
                "count": 0,
                "source_file": "rtl/core.sv",
                "line": 12,
                "source_code": "if (req) state <= BUSY;",
            },
            {
                "type": "toggle",
                "name": "req 0->1",
                "count": 0,
                "signal": "top.dut.req",
                "toggle_transition": "0->1",
            },
            {
                "type": "fsm",
                "name": "IDLE -> BUSY",
                "count": 0,
                "transition": "IDLE -> BUSY",
                "fsm_id": "state_fsm",
            },
            {
                "type": "unknown",
                "name": "vendor-item",
                "count": 0,
            },
        ],
    }


def test_coverage_suggestions_use_only_explicit_hole_evidence():
    result = build_coverage_test_suggestions(_holes())

    assert result["analysis"] == "coverage_test_suggestions"
    assert result["summary"]["suggestions"] == 4
    assert result["summary"]["by_type"] == {
        "fsm": 1,
        "statement": 1,
        "toggle": 1,
        "unknown": 1,
    }

    statement, toggle, fsm, unknown = result["suggestions"]
    assert statement["action"] == "reach_source_location"
    assert statement["specificity"] == "SOURCE_LOCATION"
    assert "rtl/core.sv:12" in statement["test_intent"]

    assert toggle["specificity"] == "EXPLICIT_TRANSITION"
    assert "top.dut.req" in toggle["test_intent"]
    assert "0->1" in toggle["test_intent"]

    assert fsm["specificity"] == "EXPLICIT_TRANSITION"
    assert "IDLE -> BUSY" in fsm["test_intent"]

    assert unknown["action"] == "review_coverage_hole"
    assert unknown["specificity"] == "NAMED_ONLY"
    assert "vendor-item" in unknown["test_intent"]


def test_toggle_suggestion_does_not_invent_missing_direction():
    result = build_coverage_test_suggestions(
        {
            "total_holes": 1,
            "reported_holes": 1,
            "holes": [
                {
                    "type": "toggle",
                    "name": "req",
                    "count": 0,
                    "signal": "top.req",
                }
            ],
        }
    )

    suggestion = result["suggestions"][0]
    assert suggestion["specificity"] == "EXPLICIT_SIGNAL"
    assert "missing transition direction is not inferred" in suggestion["test_intent"]
    assert "0->1" not in suggestion["test_intent"]
    assert "1->0" not in suggestion["test_intent"]


def test_coverage_suggestion_limit_is_explicit():
    result = build_coverage_test_suggestions(_holes(), limit=2)

    assert result["summary"]["suggestions"] == 2
    assert result["summary"]["truncated_by_suggestion_limit"] is True


def test_write_coverage_suggestions_and_cli(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    hole_path = project.root / ".zddv" / "coverage" / "holes.json"
    hole_path.parent.mkdir(parents=True, exist_ok=True)
    hole_path.write_text(json.dumps(_holes()), encoding="utf-8")

    direct = write_coverage_test_suggestions(project, limit=3)
    assert direct["summary"]["suggestions"] == 3
    assert len(direct["input_sha256"]) == 64
    assert Path(direct["path"]).is_file()

    rc = main(
        [
            "--project",
            str(project.root),
            "coverage-suggest-tests",
            "--limit",
            "2",
            "--show",
            "2",
        ]
    )
    assert rc == 0
    output = capsys.readouterr().out
    assert "COVERAGE TEST SUGGESTIONS: 2" in output
    assert "toggle" in output
    assert "statement" in output

    report_path = project.root / ".zddv" / "debug" / "coverage-test-suggestions.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"]["suggestions"] == 2
    assert report["semantics"].startswith("Suggestions are deterministic test intents")
