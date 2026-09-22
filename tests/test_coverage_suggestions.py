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
        "by_type": {"block": 1, "expression": 1, "fsm": 1, "toggle": 1},
        "holes": [
            {
                "type": "toggle",
                "name": "tb_top.dut.ready",
                "count": 0,
                "source_file": "rtl/dut.sv",
                "evidence": {"full": 0, "rise": 1, "fall": 0},
            },
            {
                "type": "fsm",
                "name": "rtl/dut.sv:fsm0:transition:IDLE->BUSY",
                "count": 0,
                "source_file": "rtl/dut.sv",
                "line": 71,
                "fsm_id": "fsm0",
                "fsm_kind": "transition",
                "transition": "IDLE -> BUSY",
            },
            {
                "type": "expression",
                "name": "tb_top.dut|rtl/dut.sv:42|expression:1.1|row:1.1.2",
                "count": 0,
                "source_file": "rtl/dut.sv",
                "line": 42,
                "fec_target": "rval=0; terms=0 - 0",
                "expression": "select ? data_a : data_b",
            },
            {
                "type": "block",
                "name": "tb_top.dut|rtl/dut.sv:138:block24",
                "count": 0,
                "source_file": "rtl/dut.sv",
                "line": 138,
                "block": 24,
                "source_code": "begin",
            },
        ],
    }


def test_build_coverage_test_suggestions_uses_only_explicit_hole_evidence():
    result = build_coverage_test_suggestions(_holes())

    assert result["suggestion_count"] == 4
    assert result["by_type"] == {
        "block": 1,
        "expression": 1,
        "fsm": 1,
        "toggle": 1,
    }
    assert all(item["review_required"] for item in result["suggestions"])
    assert all(not item["auto_execute"] for item in result["suggestions"])
    assert "do not infer DUT behavior" in result["semantics"]

    by_type = {
        item["coverage_type"]: item for item in result["suggestions"]
    }
    assert "rtl/dut.sv:138" in by_type["block"]["intent"]
    assert "rval=0; terms=0 - 0" in by_type["expression"]["intent"]
    assert "IDLE -> BUSY" in by_type["fsm"]["intent"]
    assert "1->0" in by_type["toggle"]["intent"]
    assert "0->1" not in by_type["toggle"]["intent"]


def test_coverage_test_suggestion_limit_is_deterministic():
    report = _holes()
    report["holes"].reverse()

    result = build_coverage_test_suggestions(report, limit=2)

    assert [item["coverage_type"] for item in result["suggestions"]] == [
        "block",
        "expression",
    ]
    assert [item["rank"] for item in result["suggestions"]] == [1, 2]


def test_write_coverage_test_suggestions_records_source(tmp_path: Path):
    source = tmp_path / "holes.json"
    output = tmp_path / "suggestions.json"
    source.write_text(json.dumps(_holes()), encoding="utf-8")

    result = write_coverage_test_suggestions(source, output)

    assert output.is_file()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["source_report"] == str(source.resolve())
    assert payload["suggestion_count"] == 4
    assert result["path"] == str(output.resolve())


def test_coverage_suggest_cli(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    source = project.root / ".zddv" / "coverage" / "holes.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps(_holes()), encoding="utf-8")

    rc = main(
        [
            "--project",
            str(project.root),
            "coverage-suggest",
            "--limit",
            "3",
            "--show",
            "2",
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "COVERAGE TEST SUGGESTIONS: 3" in output
    destination = project.root / ".zddv" / "coverage" / "test-suggestions.json"
    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["suggestion_count"] == 3
    assert all(item["review_required"] for item in payload["suggestions"])
