from __future__ import annotations

from pathlib import Path

import pytest

from zddv.config import initialize_project
from zddv.desktop_evidence import build_desktop_evidence_snapshot


def test_desktop_evidence_snapshot_bounds_detail_queries(
    tmp_path: Path,
    monkeypatch,
):
    project = initialize_project(tmp_path / "demo")
    calls: dict[str, object] = {}

    monkeypatch.setattr(
        "zddv.desktop_evidence.list_assertion_events",
        lambda project_arg, *, limit: [
            {
                "run_id": "run-1",
                "event_index": 0,
                "created_at": "2026-09-23T07:00:00+00:00",
                "assertion_name": "p_ready",
                "status": "FAIL",
                "message": "ready stayed low",
                "log_path": "simulation.log",
                "log_line": 12,
            }
        ],
    )
    monkeypatch.setattr(
        "zddv.desktop_evidence.list_formal_result_snapshots",
        lambda project_arg, *, limit: [
            {
                "snapshot_id": "formal-1",
                "status": "FAIL",
                "mode": "bmc",
                "property_count": 1,
            }
        ],
    )

    def formal_properties(project_arg, snapshot_id, *, limit):
        calls["formal"] = (snapshot_id, limit)
        return [
            {
                "snapshot_id": snapshot_id,
                "property_index": 0,
                "name": "p_ready",
                "kind": "assert",
                "status": "FAIL",
                "interpretation": "COUNTEREXAMPLE",
                "depth": 12,
                "effective_depth": 12,
                "message": "counterexample found",
                "trace_path": "trace.vcd",
                "trace_role": "COUNTEREXAMPLE",
            }
        ]

    monkeypatch.setattr(
        "zddv.desktop_evidence.list_formal_property_results",
        formal_properties,
    )
    monkeypatch.setattr(
        "zddv.desktop_evidence.list_uvm_log_snapshots",
        lambda project_arg, *, limit: [
            {
                "snapshot_id": "uvm-1",
                "status": "FAIL",
                "test_name": "stress",
                "error_count": 1,
                "fatal_count": 0,
            }
        ],
    )

    def uvm_messages(project_arg, snapshot_id, *, limit):
        calls["uvm"] = (snapshot_id, limit)
        return [
            {
                "snapshot_id": snapshot_id,
                "event_index": 0,
                "severity": "UVM_ERROR",
                "report_id": "MISMATCH",
                "component": "uvm_test_top.env.scoreboard",
                "message": "expected 42 got 41",
                "time_text": "120ns",
                "source_location": "scoreboard.sv(88)",
                "log_line": 44,
                "raw": "UVM_ERROR ...",
            }
        ]

    monkeypatch.setattr(
        "zddv.desktop_evidence.list_uvm_report_messages",
        uvm_messages,
    )

    snapshot = build_desktop_evidence_snapshot(project, limit=7)

    assert calls == {
        "formal": ("formal-1", 7),
        "uvm": ("uvm-1", 7),
    }
    assert snapshot["limit"] == 7
    assert snapshot["assertion_events"][0]["assertion_name"] == "p_ready"
    assert snapshot["latest_formal"]["snapshot_id"] == "formal-1"
    assert snapshot["formal_properties"][0]["interpretation"] == "COUNTEREXAMPLE"
    assert snapshot["latest_uvm"]["snapshot_id"] == "uvm-1"
    assert snapshot["uvm_messages"][0]["report_id"] == "MISMATCH"


def test_desktop_evidence_snapshot_skips_detail_queries_without_snapshots(
    tmp_path: Path,
    monkeypatch,
):
    project = initialize_project(tmp_path / "demo")

    monkeypatch.setattr(
        "zddv.desktop_evidence.list_assertion_events",
        lambda project_arg, *, limit: [],
    )
    monkeypatch.setattr(
        "zddv.desktop_evidence.list_formal_result_snapshots",
        lambda project_arg, *, limit: [],
    )
    monkeypatch.setattr(
        "zddv.desktop_evidence.list_uvm_log_snapshots",
        lambda project_arg, *, limit: [],
    )
    monkeypatch.setattr(
        "zddv.desktop_evidence.list_formal_property_results",
        lambda *args, **kwargs: pytest.fail("formal detail query should be skipped"),
    )
    monkeypatch.setattr(
        "zddv.desktop_evidence.list_uvm_report_messages",
        lambda *args, **kwargs: pytest.fail("UVM detail query should be skipped"),
    )

    snapshot = build_desktop_evidence_snapshot(project, limit=5)

    assert snapshot["assertion_events"] == []
    assert snapshot["latest_formal"] is None
    assert snapshot["formal_properties"] == []
    assert snapshot["latest_uvm"] is None
    assert snapshot["uvm_messages"] == []


def test_desktop_evidence_snapshot_rejects_nonpositive_limit(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")

    with pytest.raises(ValueError, match="limit must be >= 1"):
        build_desktop_evidence_snapshot(project, limit=0)
