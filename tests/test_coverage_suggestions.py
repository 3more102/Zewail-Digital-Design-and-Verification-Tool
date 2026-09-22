import json
from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project
from zddv.coverage_suggestions import (
    build_coverage_test_suggestions,
    write_coverage_test_suggestions,
)


def _hole_report() -> dict:
    return {
        "filter_type": None,
        "total_holes": 6,
        "reported_holes": 4,
        "holes": [
            {
                "type": "toggle",
                "name": "top.req 0->1",
                "signal": "top.req",
                "toggle_transition": "0->1",
                "count": 0,
            },
            {
                "type": "fsm",
                "name": "state ERROR",
                "fsm_kind": "state",
                "state": "ERROR",
                "fsm_id": "ctrl_fsm",
                "count": 0,
            },
            {
                "type": "expression",
                "name": "expr-row",
                "source_file": "rtl/control.sv",
                "line": 42,
                "fec_target": "input_term",
                "bit": 2,
                "truth_row": 3,
                "count": 0,
            },
            {
                "type": "branch",
                "name": "branch-1",
                "source_file": "rtl/control.sv",
                "line": 51,
                "statement": "if (ready && valid)",
                "count": 0,
            },
        ],
    }


def test_builds_evidence_bound_test_intents_without_generated_code():
    result = build_coverage_test_suggestions(_hole_report())

    assert result["source"]["input_truncated"] is True
    assert result["summary"]["suggestions"] == 4
    assert result["summary"]["generated_test_code"] == 0
    assert all(item["review_required"] for item in result["suggestions"])
    assert all(not item["generated_test_code"] for item in result["suggestions"])

    toggle = result["suggestions"][0]
    assert toggle["strategy"] == "exercise_toggle_transition"
    assert "top.req" in toggle["intent"]
    assert "0->1" in toggle["intent"]

    fsm = result["suggestions"][1]
    assert fsm["strategy"] == "reach_fsm_state"
    assert "ERROR" in fsm["intent"]

    expression = result["suggestions"][2]
    assert expression["strategy"] == "exercise_fec_target"
    assert "target=input_term" in expression["intent"]
    assert "bit=2" in expression["intent"]
    assert "truth_row=3" in expression["intent"]
    assert "invent input assignments" in expression["intent"]


def test_generic_hole_does_not_invent_semantics():
    result = build_coverage_test_suggestions(
        {
            "total_holes": 1,
            "reported_holes": 1,
            "holes": [
                {
                    "type": "vendor_custom",
                    "name": "opaque-item",
                    "count": 0,
                }
            ],
        }
    )

    suggestion = result["suggestions"][0]
    assert suggestion["strategy"] == "exercise_coverage_point"
    assert suggestion["evidence"]["name"] == "opaque-item"
    assert "vendor_custom" in suggestion["intent"]


def test_limit_is_applied_only_to_reported_holes():
    result = build_coverage_test_suggestions(_hole_report(), limit=2)

    assert result["source"]["total_holes"] == 6
    assert result["source"]["reported_holes"] == 4
    assert result["summary"]["suggestions"] == 2
    assert [item["rank"] for item in result["suggestions"]] == [1, 2]


def test_rejects_invalid_hole_payload():
    try:
        build_coverage_test_suggestions({"holes": ["not-an-object"]})
    except ValueError as exc:
        assert "coverage hole must be an object" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_writes_report_and_cli(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    source = project.root / ".zddv" / "coverage" / "holes.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps(_hole_report()), encoding="utf-8")

    result = write_coverage_test_suggestions(project, limit=3)
    assert Path(result["path"]).is_file()
    assert result["summary"]["suggestions"] == 3

    rc = main(
        [
            "--project",
            str(project.root),
            "coverage-suggestions",
            "--limit",
            "2",
        ]
    )
    assert rc == 0
    output = capsys.readouterr().out
    assert "COVERAGE TEST SUGGESTIONS: 2" in output
    assert "exercise_toggle_transition" in output
    report = json.loads(
        (project.root / ".zddv" / "coverage" / "test-suggestions.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["summary"]["suggestions"] == 2
    assert report["summary"]["generated_test_code"] == 0
