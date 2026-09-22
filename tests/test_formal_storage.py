from __future__ import annotations

import json

from zddv.config import initialize_project
from zddv.formal.results import analyze_formal_result_file
from zddv.storage import (
    list_formal_property_results,
    list_formal_result_snapshots,
)


def _payload() -> dict[str, object]:
    return {
        "backend": "example",
        "engine": "example-formal 1.0",
        "request": {
            "mode": "bmc",
            "depth": 20,
            "properties": [],
            "timeout_s": 30.0,
        },
        "command": ["example-formal", "--mode", "bmc"],
        "returncode": 1,
        "status": "FAIL",
        "run_dir": ".zddv/formal/runs/run-1",
        "log_path": ".zddv/formal/runs/run-1/formal.log",
        "runtime_ms": 12.5,
        "properties": [
            {
                "name": "p_safe",
                "kind": "assert",
                "status": "PASS",
            },
            {
                "name": "p_failure",
                "kind": "assert",
                "status": "FAIL",
                "depth": 7,
                "trace_path": "artifacts/p_failure.vcd",
            },
            {
                "name": "c_reachable",
                "kind": "cover",
                "status": "COVERED",
                "depth": 12,
                "trace_path": "artifacts/c_reachable.vcd",
            },
        ],
        "artifacts": ["artifacts/summary.json"],
    }


def test_formal_import_persists_snapshot_and_properties(tmp_path):
    project = initialize_project(tmp_path / "demo")
    input_path = project.root / "formal-result.json"
    input_path.write_text(json.dumps(_payload()), encoding="utf-8")

    record = analyze_formal_result_file(project, input_path)

    rows = list_formal_result_snapshots(project)
    assert len(rows) == 1
    row = rows[0]
    assert row["snapshot_id"] == record["snapshot_id"]
    assert row["backend"] == "example"
    assert row["status"] == "FAIL"
    assert row["mode"] == "bmc"
    assert row["scope"] == "BOUNDED"
    assert row["property_count"] == 3
    assert row["assertion_count"] == 2
    assert row["cover_count"] == 1
    assert row["counterexample_count"] == 1
    assert row["bounded_safe_count"] == 1
    assert row["proved_count"] == 0
    assert row["covered_goal_count"] == 1
    assert row["unreached_goal_count"] == 0
    assert row["command"] == ["example-formal", "--mode", "bmc"]
    assert row["artifacts"] == ["artifacts/summary.json"]

    properties = list_formal_property_results(project, record["snapshot_id"])
    assert [item["name"] for item in properties] == [
        "p_safe",
        "p_failure",
        "c_reachable",
    ]
    assert properties[0]["interpretation"] == "BOUNDED_SAFE"
    assert properties[0]["effective_depth"] == 20
    assert properties[1]["interpretation"] == "COUNTEREXAMPLE"
    assert properties[1]["trace_role"] == "COUNTEREXAMPLE"
    assert properties[2]["interpretation"] == "COVERED"
    assert properties[2]["trace_role"] == "WITNESS"


def test_formal_history_filters_are_evidence_preserving(tmp_path):
    project = initialize_project(tmp_path / "demo")
    input_path = project.root / "formal-result.json"
    input_path.write_text(json.dumps(_payload()), encoding="utf-8")
    record = analyze_formal_result_file(project, input_path)

    assert len(list_formal_result_snapshots(project, status="FAIL")) == 1
    assert len(list_formal_result_snapshots(project, status="PASS")) == 0
    assert len(list_formal_result_snapshots(project, backend="example")) == 1
    assert len(list_formal_result_snapshots(project, mode="bmc")) == 1

    counters = list_formal_property_results(
        project,
        record["snapshot_id"],
        interpretation="COUNTEREXAMPLE",
    )
    assert [item["name"] for item in counters] == ["p_failure"]
