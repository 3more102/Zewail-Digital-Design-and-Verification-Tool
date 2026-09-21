from pathlib import Path

from zddv.config import initialize_project
from zddv.storage import (
    database_path,
    get_run_record,
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


def test_get_run_record(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    record = _record("run-exact", "PASS", 3)
    record["plusargs"] = ["+MODE=debug"]
    record_run(project, record)

    row = get_run_record(project, "run-exact")

    assert row is not None
    assert row["run_id"] == "run-exact"
    assert row["plusargs"] == ["+MODE=debug"]
    assert get_run_record(project, "missing") is None


def test_list_assertions_can_filter_by_run(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    events = [
        {
            "run_id": "run-a",
            "event_index": 0,
            "created_at": "2026-09-21T20:00:00+00:00",
            "assertion_name": "a_ok",
            "status": "PASS",
            "message": None,
            "log_path": "/tmp/a.log",
            "log_line": 1,
        },
        {
            "run_id": "run-b",
            "event_index": 0,
            "created_at": "2026-09-21T20:01:00+00:00",
            "assertion_name": "b_fail",
            "status": "FAIL",
            "message": "count=4",
            "log_path": "/tmp/b.log",
            "log_line": 2,
        },
    ]
    record_assertion_events(project, events)

    rows = list_assertion_events(project, run_id="run-b", limit=10)

    assert len(rows) == 1
    assert rows[0]["assertion_name"] == "b_fail"
