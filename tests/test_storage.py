from pathlib import Path

from zddv.config import initialize_project
from zddv.storage import (
    assertion_statistics,
    database_path,
    list_assertion_events,
    list_coverage_snapshots,
    list_run_records,
    list_runs,
    record_assertion_events,
    record_coverage_snapshot,
    record_run,
)


def _record(run_id: str, status: str, seed: int) -> dict:
    return {
        "run_id": run_id,
        "created_at": f"2026-09-21T19:00:0{seed}+00:00",
        "project": "demo",
        "simulator": "verilator",
        "simulator_version": "Verilator test",
        "top": "tb_top",
        "test": "smoke",
        "seed": seed,
        "status": status,
        "returncode": 0 if status == "PASS" else 1,
        "duration_ms": 12.5 + seed,
        "run_dir": f"/tmp/{run_id}",
        "log": f"/tmp/{run_id}/simulation.log",
        "waveform": None,
        "coverage": None,
        "timeout_s": 10.0,
        "command": ["zddv_sim"],
        "plusargs": [],
    }


def test_record_and_list_runs(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")

    record_run(project, _record("run-1", "PASS", 1))
    record_run(project, _record("run-2", "FAIL", 2))

    assert database_path(project).exists()

    rows = list_runs(project, limit=10)
    assert [row["run_id"] for row in rows] == ["run-2", "run-1"]

    failed = list_runs(project, limit=10, status="FAIL")
    assert len(failed) == 1
    assert failed[0]["run_id"] == "run-2"


def test_run_limit_validation(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")

    try:
        list_runs(project, limit=0)
    except ValueError as exc:
        assert "limit" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_list_run_records_for_rerun(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    record = _record("run-fail", "FAIL", 7)
    record["plusargs"] = ["+MODE=stress"]
    record["timeout_s"] = 3.5
    record_run(project, record)

    rows = list_run_records(project, limit=10, statuses=("FAIL", "TIMEOUT"))

    assert len(rows) == 1
    assert rows[0]["run_id"] == "run-fail"
    assert rows[0]["plusargs"] == ["+MODE=stress"]
    assert rows[0]["timeout_s"] == 3.5


def test_record_and_list_coverage_snapshots(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    record_coverage_snapshot(
        project,
        {
            "snapshot_id": "cov-1",
            "created_at": "2026-09-21T20:00:00+00:00",
            "project": "demo",
            "simulator": "verilator",
            "input_count": 4,
            "total_points": 20,
            "hit_points": 15,
            "hit_rate": 75.0,
            "by_type": {
                "line": {"total": 10, "hit": 8, "hit_rate": 80.0},
            },
            "merged": "/tmp/coverage.dat",
            "summary": "/tmp/summary.txt",
            "metrics_path": "/tmp/metrics.json",
        },
    )

    rows = list_coverage_snapshots(project, limit=10)

    assert len(rows) == 1
    assert rows[0]["snapshot_id"] == "cov-1"
    assert rows[0]["hit_rate"] == 75.0
    assert rows[0]["by_type"]["line"]["hit"] == 8



def test_record_and_list_assertion_events(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    run = _record("run-assert", "FAIL", 8)
    record_run(project, run)
    record_assertion_events(
        project,
        run,
        [
            {
                "status": "PASS",
                "property_name": "p_boot",
                "scope": None,
                "source_file": None,
                "source_line": None,
                "source_column": None,
                "sim_time": None,
                "message": "boot complete",
                "raw_text": "ZDDV_ASSERT PASS p_boot :: boot complete",
                "parser": "zddv-marker",
            },
            {
                "status": "FAIL",
                "property_name": None,
                "scope": "TOP.tb",
                "source_file": "tb.sv",
                "source_line": 42,
                "source_column": 7,
                "sim_time": "25",
                "message": "grant missing",
                "raw_text": "Assertion failed in TOP.tb: grant missing",
                "parser": "verilator",
            },
        ],
    )
    rows = list_assertion_events(project, limit=10)
    assert len(rows) == 2
    assert {row["status"] for row in rows} == {"PASS", "FAIL"}
    assert rows[0]["test_name"] == "smoke"
    failed = list_assertion_events(project, limit=10, status="FAIL")
    assert len(failed) == 1
    assert failed[0]["source_line"] == 42
    assert assertion_statistics(project) == {
        "total": 2,
        "passed": 1,
        "failed": 1,
        "named_properties": 1,
    }
