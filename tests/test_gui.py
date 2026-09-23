from pathlib import Path

import pytest

from zddv.config import initialize_project
from zddv.gui import build_debug_gui_snapshot
from zddv.storage import (
    record_coverage_score_snapshot,
    record_coverage_snapshot,
    record_run,
)


def _run(run_id: str, status: str, seed: int, log: Path) -> dict:
    return {
        "run_id": run_id,
        "created_at": f"2026-09-23T06:30:0{seed}+00:00",
        "project": "demo",
        "simulator": "verilator",
        "simulator_version": "Verilator test",
        "top": "tb_top",
        "test": "smoke",
        "seed": seed,
        "status": status,
        "returncode": 0 if status == "PASS" else 1,
        "duration_ms": 12.0 + seed,
        "run_dir": str(log.parent),
        "log": str(log),
        "waveform": None,
        "coverage": None,
        "timeout_s": 10.0,
        "command": ["zddv_sim"],
        "plusargs": [],
    }


def test_debug_gui_snapshot_is_display_only_backend_view(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    pass_log = tmp_path / "pass.log"
    fail_log = tmp_path / "fail.log"
    pass_log.write_text("PASS\n", encoding="utf-8")
    fail_log.write_text(
        "ASSERT mismatch packet=41 expected=42 actual=43\n",
        encoding="utf-8",
    )

    record_run(project, _run("run-1", "PASS", 1, pass_log))
    record_run(project, _run("run-2", "FAIL", 2, fail_log))
    record_coverage_snapshot(
        project,
        {
            "snapshot_id": "cov-gui",
            "created_at": "2026-09-23T06:31:00+00:00",
            "project": "demo",
            "simulator": "verilator",
            "input_count": 2,
            "total_points": 20,
            "hit_points": 18,
            "hit_rate": 90.0,
            "by_type": {
                "line": {"total": 20, "hit": 18, "hit_rate": 90.0},
            },
            "merged": "/tmp/coverage.dat",
            "summary": "/tmp/summary.txt",
            "metrics_path": "/tmp/metrics.json",
        },
    )

    snapshot = build_debug_gui_snapshot(project, limit=10)

    assert snapshot["project"] == "demo"
    assert snapshot["top"] == "tb_top"
    assert snapshot["simulator"] == "verilator"
    assert snapshot["stats"]["total"] == 2
    assert snapshot["stats"]["passed"] == 1
    assert snapshot["stats"]["failed"] == 1
    assert snapshot["stats"]["pass_rate"] == 50.0
    assert [row["run_id"] for row in snapshot["runs"]] == ["run-2", "run-1"]
    assert len(snapshot["failure_groups"]) == 1
    assert snapshot["failure_groups"][0]["signature"] == (
        "ASSERT mismatch packet=# expected=# actual=#"
    )
    coverage = snapshot["latest_coverage"]
    assert coverage["snapshot_id"] == "cov-gui"
    assert coverage["kind"] == "points"
    assert coverage["percent"] == 90.0
    assert coverage["items"] == [
        {
            "metric": "line",
            "percent": 90.0,
            "covered": 18,
            "total": 20,
        }
    ]
    assert snapshot["assertions"] == {
        "total": 0,
        "passed": 0,
        "failed": 0,
        "pass_rate": 0.0,
    }
    assert snapshot["latest_formal"] is None
    assert snapshot["latest_uvm"] is None
    assert snapshot["policy"] == {
        "display_only": True,
        "executes_verification": False,
        "invokes_ai": False,
        "applies_generated_artifacts": False,
    }


def test_debug_gui_prefers_newest_native_score_snapshot(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    record_coverage_snapshot(
        project,
        {
            "snapshot_id": "cov-points",
            "created_at": "2026-09-23T06:31:00+00:00",
            "project": "demo",
            "simulator": "verilator",
            "input_count": 1,
            "total_points": 10,
            "hit_points": 8,
            "hit_rate": 80.0,
            "by_type": {
                "line": {"total": 10, "hit": 8, "hit_rate": 80.0},
            },
            "merged": "/tmp/coverage.dat",
            "summary": "/tmp/summary.txt",
            "metrics_path": "/tmp/metrics.json",
        },
    )
    record_coverage_score_snapshot(
        project,
        {
            "snapshot_id": "cov-score",
            "created_at": "2026-09-23T06:32:00+00:00",
            "project": "demo",
            "simulator": "questa",
            "input_count": 1,
            "score": 93.5,
            "by_metric": {"TOTAL": 93.5},
            "by_metric_counts": {
                "TOTAL": {"covered": 187, "total": 200, "hit_rate": 93.5},
            },
            "merged": "/tmp/coverage.ucdb",
            "summary": "/tmp/summary.txt",
            "metrics_path": "/tmp/metrics.json",
        },
    )

    coverage = build_debug_gui_snapshot(project)["latest_coverage"]

    assert coverage["snapshot_id"] == "cov-score"
    assert coverage["kind"] == "score"
    assert coverage["simulator"] == "questa"
    assert coverage["percent"] == 93.5
    assert coverage["items"] == [
        {
            "metric": "TOTAL",
            "percent": 93.5,
            "covered": 187,
            "total": 200,
        }
    ]


def test_debug_gui_includes_latest_formal_and_uvm_evidence(
    tmp_path: Path,
    monkeypatch,
):
    project = initialize_project(tmp_path / "demo")
    formal = {
        "snapshot_id": "formal-gui",
        "created_at": "2026-09-23T06:33:00+00:00",
        "status": "PASS",
        "mode": "bmc",
        "property_count": 7,
    }
    uvm = {
        "snapshot_id": "uvm-gui",
        "created_at": "2026-09-23T06:34:00+00:00",
        "status": "PASS",
        "test_name": "smoke",
        "error_count": 0,
        "fatal_count": 0,
    }
    monkeypatch.setattr(
        "zddv.gui.list_formal_result_snapshots",
        lambda project_arg, *, limit: [formal],
    )
    monkeypatch.setattr(
        "zddv.gui.list_uvm_log_snapshots",
        lambda project_arg, *, limit: [uvm],
    )

    snapshot = build_debug_gui_snapshot(project)

    assert snapshot["latest_formal"] == formal
    assert snapshot["latest_uvm"] == uvm


def test_debug_gui_snapshot_rejects_nonpositive_limit(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    with pytest.raises(ValueError, match="limit must be >= 1"):
        build_debug_gui_snapshot(project, limit=0)
