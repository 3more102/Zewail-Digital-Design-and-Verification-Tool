from __future__ import annotations

import json
from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project
from zddv.formal.results import analyze_formal_result_file
from zddv.storage import (
    list_formal_property_results,
    list_formal_result_snapshots,
)


def _payload(*, status: str = "FAIL") -> dict[str, object]:
    return {
        "backend": "mock-formal",
        "engine": "mock 1.0",
        "request": {
            "mode": "bmc",
            "depth": 12,
            "properties": [],
            "timeout_s": 10.0,
        },
        "command": ["mock-formal", "--bmc", "12"],
        "returncode": 1 if status == "FAIL" else 0,
        "status": status,
        "run_dir": ".zddv/formal/runs/mock",
        "log_path": ".zddv/formal/runs/mock/formal.log",
        "properties": [
            {
                "name": "p_safe",
                "kind": "assert",
                "status": "PASS",
            },
            {
                "name": "p_fail",
                "kind": "assert",
                "status": "FAIL",
                "depth": 7,
                "trace_path": "artifacts/p_fail.vcd",
            },
            {
                "name": "c_reached",
                "kind": "cover",
                "status": "COVERED",
                "depth": 9,
                "trace_path": "artifacts/c_reached.vcd",
            },
        ],
        "artifacts": ["artifacts/summary.json"],
    }


def test_formal_import_persists_snapshot_and_properties(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    input_path = project.root / "formal-result.json"
    input_path.write_text(json.dumps(_payload()), encoding="utf-8")

    record = analyze_formal_result_file(project, input_path)

    rows = list_formal_result_snapshots(project, limit=5)
    assert len(rows) == 1
    row = rows[0]
    assert row["snapshot_id"] == record["snapshot_id"]
    assert row["backend"] == "mock-formal"
    assert row["status"] == "FAIL"
    assert row["mode"] == "bmc"
    assert row["proof_scope"] == "BOUNDED"
    assert row["property_count"] == 3
    assert row["counterexample_count"] == 1
    assert row["bounded_safe_count"] == 1
    assert row["covered_goal_count"] == 1

    failed = list_formal_property_results(
        project,
        record["snapshot_id"],
        kind="assert",
        status="FAIL",
    )
    assert len(failed) == 1
    assert failed[0]["name"] == "p_fail"
    assert failed[0]["interpretation"] == "COUNTEREXAMPLE"
    assert failed[0]["effective_depth"] == 7
    assert failed[0]["trace_role"] == "COUNTEREXAMPLE"


def test_formal_history_cli_filters_backend_and_status(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    input_path = project.root / "formal-result.json"
    input_path.write_text(json.dumps(_payload()), encoding="utf-8")
    analyze_formal_result_file(project, input_path)

    rc = main(
        [
            "--project",
            str(project.root),
            "formal-history",
            "--status",
            "FAIL",
            "--backend",
            "mock-formal",
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "mock-formal" in output
    assert "FAIL" in output
    assert "COUNTEREXAMPLE" not in output


def test_formal_import_cli_persists_result(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    payload = _payload(status="PASS")
    payload["properties"] = [
        {
            "name": "p_safe",
            "kind": "assert",
            "status": "PASS",
        }
    ]
    input_path = project.root / "formal-pass.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")

    rc = main(
        [
            "--project",
            str(project.root),
            "formal-import",
            str(input_path),
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "FORMAL PASS" in output
    assert "Snapshot:" in output
    rows = list_formal_result_snapshots(project, status="PASS")
    assert len(rows) == 1
