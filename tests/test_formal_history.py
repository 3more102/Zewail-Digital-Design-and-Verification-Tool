from __future__ import annotations

import json
from pathlib import Path

from zddv.config import initialize_project
from zddv.formal.results import analyze_formal_result_file
from zddv.storage import (
    list_formal_property_results,
    list_formal_result_snapshots,
)


def _payload(*, mode: str = "bmc", status: str = "FAIL") -> dict[str, object]:
    request: dict[str, object] = {
        "mode": mode,
        "properties": [],
        "timeout_s": 30.0,
    }
    if mode == "bmc":
        request["depth"] = 20

    return {
        "backend": "example",
        "engine": "example-formal 1.0",
        "request": request,
        "command": ["example-formal", "--mode", mode],
        "returncode": 1 if status == "FAIL" else 0,
        "status": status,
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


def test_formal_import_persists_snapshot_and_property_evidence(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    source = project.root / "formal-result.json"
    source.write_text(json.dumps(_payload()), encoding="utf-8")

    record = analyze_formal_result_file(project, source)

    snapshots = list_formal_result_snapshots(project, limit=10)
    assert len(snapshots) == 1
    row = snapshots[0]
    assert row["snapshot_id"] == record["snapshot_id"]
    assert row["status"] == "FAIL"
    assert row["mode"] == "bmc"
    assert row["scope"] == "BOUNDED"
    assert row["property_count"] == 3
    assert row["counterexample_count"] == 1
    assert row["bounded_safe_count"] == 1
    assert row["covered_goal_count"] == 1

    properties = list_formal_property_results(project, record["snapshot_id"])
    assert [item["name"] for item in properties] == [
        "p_safe",
        "p_failure",
        "c_reachable",
    ]
    failed = properties[1]
    assert failed["interpretation"] == "COUNTEREXAMPLE"
    assert failed["effective_depth"] == 7
    assert failed["trace_role"] == "COUNTEREXAMPLE"
    assert failed["trace_path"] == "artifacts/p_failure.vcd"


def test_formal_history_filters_do_not_strengthen_evidence(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")

    bmc_path = project.root / "bmc.json"
    bmc_path.write_text(json.dumps(_payload()), encoding="utf-8")
    bmc_record = analyze_formal_result_file(project, bmc_path)

    prove_payload = _payload(mode="prove", status="PASS")
    prove_payload["properties"] = [
        {
            "name": "p_complete",
            "kind": "assert",
            "status": "PASS",
        }
    ]
    prove_path = project.root / "prove.json"
    prove_path.write_text(json.dumps(prove_payload), encoding="utf-8")
    prove_record = analyze_formal_result_file(project, prove_path)

    passed = list_formal_result_snapshots(
        project,
        limit=10,
        status="PASS",
        mode="prove",
        backend="example",
    )
    assert [row["snapshot_id"] for row in passed] == [prove_record["snapshot_id"]]
    assert passed[0]["proved_count"] == 1
    assert passed[0]["bounded_safe_count"] == 0

    counterexamples = list_formal_property_results(
        project,
        bmc_record["snapshot_id"],
        interpretation="COUNTEREXAMPLE",
    )
    assert [item["name"] for item in counterexamples] == ["p_failure"]
