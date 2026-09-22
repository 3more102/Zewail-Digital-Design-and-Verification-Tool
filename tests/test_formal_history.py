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


def _payload(*, status: str = "FAIL", mode: str = "bmc") -> dict[str, object]:
    return {
        "backend": "example",
        "engine": "example-formal 1.0",
        "request": {
            "mode": mode,
            "depth": 20 if mode == "bmc" else None,
            "properties": ["p_safe", "p_failure"],
            "timeout_s": 30.0,
        },
        "command": ["example-formal", "--mode", mode],
        "returncode": 1 if status == "FAIL" else 0,
        "status": status,
        "run_dir": ".zddv/formal/runs/run-1",
        "log_path": ".zddv/formal/runs/run-1/formal.log",
        "runtime_ms": 12.5,
        "properties": (
            [
                {"name": "p_safe", "kind": "assert", "status": "PASS"},
                {
                    "name": "p_failure",
                    "kind": "assert",
                    "status": "FAIL",
                    "depth": 7,
                    "trace_path": "artifacts/p_failure.vcd",
                },
            ]
            if status == "FAIL"
            else [
                {"name": "p_safe", "kind": "assert", "status": "PASS"},
            ]
        ),
        "artifacts": ["artifacts/summary.json"],
    }


def _write_payload(project, payload: dict[str, object], name: str) -> Path:
    path = project.root / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_analyze_formal_result_persists_snapshot_and_properties(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    path = _write_payload(project, _payload(), "formal-fail.json")

    record = analyze_formal_result_file(project, path)

    snapshots = list_formal_result_snapshots(project, limit=5)
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot["snapshot_id"] == record["snapshot_id"]
    assert snapshot["status"] == "FAIL"
    assert snapshot["mode"] == "bmc"
    assert snapshot["proof_scope"] == "BOUNDED"
    assert snapshot["property_count"] == 2
    assert snapshot["counterexample_count"] == 1
    assert snapshot["bounded_safe_count"] == 1
    assert snapshot["request_properties"] == ["p_safe", "p_failure"]
    assert snapshot["command"] == ["example-formal", "--mode", "bmc"]

    properties = list_formal_property_results(project, record["snapshot_id"])
    assert [item["name"] for item in properties] == ["p_safe", "p_failure"]
    assert properties[0]["interpretation"] == "BOUNDED_SAFE"
    assert properties[0]["effective_depth"] == 20
    assert properties[1]["interpretation"] == "COUNTEREXAMPLE"
    assert properties[1]["trace_role"] == "COUNTEREXAMPLE"
    assert properties[1]["trace_path"] == "artifacts/p_failure.vcd"


def test_formal_history_filters_status_and_mode(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    fail_path = _write_payload(project, _payload(), "formal-fail.json")
    pass_path = _write_payload(
        project,
        _payload(status="PASS", mode="prove"),
        "formal-pass.json",
    )

    analyze_formal_result_file(project, fail_path)
    analyze_formal_result_file(project, pass_path)

    failed_bmc = list_formal_result_snapshots(
        project,
        limit=10,
        status="FAIL",
        mode="bmc",
    )
    assert len(failed_bmc) == 1
    assert failed_bmc[0]["counterexample_count"] == 1

    proved = list_formal_result_snapshots(
        project,
        limit=10,
        status="PASS",
        mode="prove",
    )
    assert len(proved) == 1
    assert proved[0]["proved_count"] == 1
    assert proved[0]["bounded_safe_count"] == 0


def test_cli_formal_import_and_history(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    path = _write_payload(
        project,
        _payload(status="PASS", mode="prove"),
        "formal-pass.json",
    )

    rc = main(
        [
            "--project",
            str(project.root),
            "formal-import",
            str(path),
        ]
    )
    assert rc == 0
    imported = capsys.readouterr().out
    assert "FORMAL PASS:" in imported
    assert "mode=prove" in imported
    assert "Results DB:" in imported

    rc = main(
        [
            "--project",
            str(project.root),
            "formal-history",
            "--status",
            "PASS",
            "--mode",
            "prove",
        ]
    )
    assert rc == 0
    history = capsys.readouterr().out
    assert "STATUS" in history
    assert "prove" in history
    assert "example" in history
