from pathlib import Path

from zddv.config import initialize_project
from zddv.dashboard import generate_html_report
from zddv.storage import record_coverage_snapshot, record_run, run_statistics


def _record(run_id: str, status: str, seed: int, log: Path) -> dict:
    return {
        "run_id": run_id,
        "created_at": f"2026-09-21T20:00:0{seed}+00:00",
        "project": "demo",
        "simulator": "verilator",
        "simulator_version": "Verilator test",
        "top": "tb_top",
        "test": "smoke",
        "seed": seed,
        "status": status,
        "returncode": 0 if status == "PASS" else 1,
        "duration_ms": 10.0 + seed,
        "run_dir": str(log.parent),
        "log": str(log),
        "waveform": None,
        "coverage": None,
        "timeout_s": 10.0,
        "command": ["zddv_sim"],
        "plusargs": [],
    }


def test_statistics_and_html_report(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    pass_log = tmp_path / "pass.log"
    fail_log = tmp_path / "fail.log"
    pass_log.write_text("PASS\n", encoding="utf-8")
    fail_log.write_text(
        "ASSERT mismatch packet=41 expected=42 actual=43\n",
        encoding="utf-8",
    )

    record_run(project, _record("run-1", "PASS", 1, pass_log))
    record_run(project, _record("run-2", "FAIL", 2, fail_log))
    record_coverage_snapshot(
        project,
        {
            "snapshot_id": "cov-dashboard",
            "created_at": "2026-09-21T20:01:00+00:00",
            "project": "demo",
            "simulator": "verilator",
            "input_count": 2,
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

    stats = run_statistics(project)
    assert stats["total"] == 2
    assert stats["passed"] == 1
    assert stats["failed"] == 1
    assert stats["pass_rate"] == 50.0

    result = generate_html_report(project)
    report_path = Path(result["path"])
    assert report_path.exists()
    content = report_path.read_text(encoding="utf-8")
    assert "ZDDV Verification Report" in content
    assert "ASSERT mismatch packet=# expected=# actual=#" in content
    assert "Coverage hit rate" in content
    assert "80.0%" in content
    assert "cov-dashboard" in content
    assert len(result["failure_groups"]) == 1
    assert result["latest_coverage"]["snapshot_id"] == "cov-dashboard"
