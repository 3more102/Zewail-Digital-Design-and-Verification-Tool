from pathlib import Path

from zddv.config import initialize_project
from zddv.storage import database_path, list_runs, record_run


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
