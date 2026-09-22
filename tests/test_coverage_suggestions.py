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


def _hole_report() -> dict:
    return {
        "filter_type": None,
        "total_holes": 5,
        "reported_holes": 5,
        "by_type": {
            "branch": 1,
            "expression": 1,
            "fsm": 1,
            "statement": 1,
            "toggle": 1,
        },
        "holes": [
            {
                "type": "toggle",
                "name": "dut.ready",
                "count": 0,
                "signal": "dut.ready",
                "toggle_transition": "0L->1H",
                "source_file": "rtl/dut.sv",
                "line": 24,
            },
            {
                "type": "fsm",
                "name": "rtl/dut.sv:40:WAIT->DONE",
                "count": 0,
                "fsm_id": "control_fsm",
                "fsm_kind": "transition",
                "transition": "WAIT -> DONE",
                "source_file": "rtl/dut.sv",
                "line": 40,
            },
            {
                "type": "expression",
                "name": "rtl/dut.sv:18:2:req[i]_1",
                "count": 0,
                "source_file": "rtl/dut.sv",
                "line": 18,
                "fec_target": "req[2]",
                "multibit": True,
            },
            {
                "type": "branch",
                "name": "rtl/dut.sv:31:1 else",
                "count": 0,
                "source_file": "rtl/dut.sv",
                "line": 31,
                "detail": "else",
            },
            {
                "type": "statement",
                "name": "rtl/dut.sv:12:1",
                "count": 0,
                "source_file": "rtl/dut.sv",
                "line": 12,
            },
        ],
    }


def test_coverage_suggestions_use_only_explicit_hole_semantics():
    result = suggest_coverage_hole_tests(_hole_report())

    assert result["analysis"] == "coverage_hole_test_suggestions"
    assert result["summary"]["suggestions"] == 5
    assert result["review_required"] is True
    assert result["automatic_test_generation"] is False
    assert "not generated tests" in result["semantics"]

    by_type = {
        item["coverage_type"]: item
        for item in result["suggestions"]
    }
    assert "required stimulus values" in by_type["branch"]["objective"]
    assert "req[2]" in by_type["expression"]["objective"]
    assert "WAIT -> DONE" in by_type["fsm"]["objective"]
    assert "rtl/dut.sv:12" in by_type["statement"]["objective"]
    assert "0L->1H" in by_type["toggle"]["objective"]
    assert by_type["toggle"]["generated_test"] is False
    assert "explicit_toggle_transition" in by_type["toggle"]["basis"]
    assert by_type["fsm"]["evidence"]["fsm_id"] == "control_fsm"


def test_coverage_suggestions_support_uncovered_functional_bins():
    payload = {
        "source": "uvm-export",
        "bins": [
            {
                "scope": "tb.axi",
                "coverpoint": "burst_len",
                "bin_name": "len16",
                "hits": 0,
                "goal": 2,
                "status": "UNCOVERED",
                "metadata": {"kind": "coverpoint"},
            },
            {
                "scope": "tb.axi",
                "coverpoint": "burst_len",
                "bin_name": "len1",
                "hits": 3,
                "goal": 1,
                "status": "COVERED",
                "metadata": {},
            },
        ],
    }

    result = suggest_coverage_hole_tests(payload)

    assert result["source_kind"] == "functional_coverage_bins"
    assert result["summary"]["suggestions"] == 1
    suggestion = result["suggestions"][0]
    assert suggestion["coverage_type"] == "functional_bin"
    assert suggestion["target"] == "tb.axi.burst_len.len16"
    assert "explicit goal 2" in suggestion["objective"]
    assert "current hits: 0" in suggestion["objective"]
    assert suggestion["evidence"]["metadata"] == {"kind": "coverpoint"}


def test_coverage_suggestions_preserve_truncated_source_boundary():
    payload = {
        "total_holes": 12,
        "reported_holes": 2,
        "holes": [
            {"type": "line", "name": "a", "count": 0},
            {"type": "line", "name": "b", "count": 0},
        ],
    }

    result = suggest_coverage_hole_tests(payload, limit=1)

    assert result["source_summary"]["total_holes"] == 12
    assert result["source_summary"]["reported_holes"] == 2
    assert result["source_summary"]["input_truncated"] is True
    assert result["summary"]["suggestions"] == 1
    assert result["summary"]["limited"] is True


def test_coverage_suggestions_reject_invalid_report_counts():
    with pytest.raises(ValueError, match="invalid reported_holes"):
        suggest_coverage_hole_tests(
            {
                "total_holes": 2,
                "reported_holes": 1,
                "holes": [
                    {"type": "line", "name": "a"},
                    {"type": "line", "name": "b"},
                ],
            }
        )


def test_write_coverage_suggestions_and_cli(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    coverage_dir = project.root / ".zddv" / "coverage"
    coverage_dir.mkdir(parents=True, exist_ok=True)
    input_path = coverage_dir / "holes.json"
    input_path.write_text(json.dumps(_hole_report()), encoding="utf-8")

    report = write_coverage_test_suggestions(project, limit=3)
    assert report["summary"]["suggestions"] == 3
    assert len(report["input_sha256"]) == 64
    assert Path(report["report_path"]).is_file()

    rc = main(
        [
            "--project",
            str(project.root),
            "coverage-suggest",
            "--limit",
            "2",
            "--show",
            "2",
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "COVERAGE TEST SUGGESTIONS:" in output
    assert "review-required" in output
    assert (coverage_dir / "test-suggestions.json").is_file()
