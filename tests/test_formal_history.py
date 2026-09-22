from __future__ import annotations

import json
from pathlib import Path

from zddv.config import initialize_project
from zddv.formal.history import (
    list_formal_property_results,
    list_formal_result_snapshots,
)
from zddv.formal.results import analyze_formal_result_file
from zddv.storage import database_path


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
        "returncode": 0,
        "status": "FAIL",
        "run_dir": ".zddv/formal/runs/run-1",
        "log_path": ".zddv/formal/runs/run-1/formal.log",
        "runtime_ms": 12.5,
        "properties": [
            {
                "name": "p_bounded_safe",
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


def test_formal_analysis_persists_snapshot_and_properties(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    source = project.root / "formal-result.json"
    source.write_text(json.dumps(_payload()), encoding="utf-8")

    record = analyze_formal_result_file(project, source)

    assert database_path(project).is_file()
    rows = list_formal_result_snapshots(project)
    assert len(rows) == 1

    snapshot = rows[0]
    assert snapshot["snapshot_id"] == record["snapshot_id"]
    assert snapshot["backend"] == "example"
    assert snapshot["engine"] == "example-formal 1.0"
    assert snapshot["status"] == "FAIL"
    assert snapshot["mode"] == "bmc"
    assert snapshot["scope"] == "BOUNDED"
    assert snapshot["request_depth"] == 20
    assert snapshot["property_count"] == 3
    assert snapshot["counterexample_count"] == 1
    assert snapshot["bounded_safe_count"] == 1
    assert snapshot["covered_goal_count"] == 1
    assert snapshot["command"] == ["example-formal", "--mode", "bmc"]
    assert snapshot["artifacts"] == ["artifacts/summary.json"]

    properties = list_formal_property_results(project, record["snapshot_id"])
    assert [item["name"] for item in properties] == [
        "p_bounded_safe",
        "p_failure",
        "c_reachable",
    ]
    assert properties[0]["interpretation"] == "BOUNDED_SAFE"
    assert properties[0]["effective_depth"] == 20
    assert properties[1]["interpretation"] == "COUNTEREXAMPLE"
    assert properties[1]["trace_path"] == "artifacts/p_failure.vcd"
    assert properties[1]["trace_role"] == "COUNTEREXAMPLE"
    assert properties[2]["trace_role"] == "WITNESS"


def test_formal_history_filters_snapshot_and_property_rows(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    source = project.root / "formal-result.json"
    source.write_text(json.dumps(_payload()), encoding="utf-8")

    record = analyze_formal_result_file(project, source)

    assert len(
        list_formal_result_snapshots(
            project,
            status="FAIL",
            backend="example",
            mode="bmc",
        )
    ) == 1
    assert (
        list_formal_result_snapshots(project, status="PASS")
        == []
    )

    counterexamples = list_formal_property_results(
        project,
        record["snapshot_id"],
        kind="assert",
        interpretation="COUNTEREXAMPLE",
    )
    assert len(counterexamples) == 1
    assert counterexamples[0]["name"] == "p_failure"

    covered = list_formal_property_results(
        project,
        record["snapshot_id"],
        kind="cover",
        status="COVERED",
    )
    assert len(covered) == 1
    assert covered[0]["name"] == "c_reachable"


def test_formal_history_rejects_nonpositive_limit(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")

    try:
        list_formal_result_snapshots(project, limit=0)
    except ValueError as exc:
        assert str(exc) == "limit must be >= 1"
    else:
        raise AssertionError("expected ValueError")
