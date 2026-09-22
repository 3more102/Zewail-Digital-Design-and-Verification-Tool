from __future__ import annotations

from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project
from zddv.signoff import (
    build_verification_signoff_bundle,
    write_verification_signoff_bundle,
)
from zddv.storage import record_coverage_snapshot, record_run


def _run_record(
    run_id: str,
    status: str,
    *,
    seed: int = 1,
) -> dict:
    return {
        "run_id": run_id,
        "created_at": f"2026-09-22T20:00:{seed:02d}+00:00",
        "project": "demo",
        "simulator": "verilator",
        "simulator_version": "Verilator test",
        "top": "tb_top",
        "test": "smoke",
        "seed": seed,
        "status": status,
        "returncode": 0 if status == "PASS" else 1,
        "duration_ms": 10.0,
        "run_dir": f"/tmp/{run_id}",
        "log": f"/tmp/{run_id}.log",
        "waveform": None,
        "coverage": None,
        "timeout_s": 10.0,
        "command": ["zddv_sim"],
        "plusargs": [],
    }


def _coverage_snapshot(percent: float) -> dict:
    total = 100
    hit = int(percent)
    return {
        "snapshot_id": "cov-signoff",
        "created_at": "2026-09-22T20:10:00+00:00",
        "project": "demo",
        "simulator": "verilator",
        "input_count": 1,
        "total_points": total,
        "hit_points": hit,
        "hit_rate": float(percent),
        "by_type": {
            "line": {
                "total": total,
                "hit": hit,
                "hit_rate": float(percent),
            }
        },
        "merged": "/tmp/coverage.dat",
        "summary": "/tmp/coverage-summary.txt",
        "metrics_path": "/tmp/coverage-metrics.json",
    }


def test_signoff_blocks_without_simulation_evidence(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")

    bundle = build_verification_signoff_bundle(project)

    assert bundle["summary"]["review_state"] == "BLOCKED"
    assert bundle["summary"]["blocking_checks"] == ["simulation"]
    checks = {item["name"]: item for item in bundle["checks"]}
    assert checks["simulation"]["status"] == "MISSING"
    assert checks["coverage"]["status"] == "NOT_PRESENT"
    assert checks["formal"]["status"] == "NOT_PRESENT"
    assert checks["uvm"]["status"] == "NOT_PRESENT"


def test_signoff_ready_for_review_with_passing_run_and_stable_sha(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    record_run(project, _run_record("run-pass", "PASS"))

    first = build_verification_signoff_bundle(project)
    second = build_verification_signoff_bundle(project)

    assert first["summary"]["review_state"] == "READY_FOR_REVIEW"
    assert first["summary"]["blocking_checks"] == []
    assert first["provenance"]["signoff_sha256"] == second["provenance"]["signoff_sha256"]
    assert first["provenance"]["evidence_sha256"] == second["provenance"]["evidence_sha256"]


def test_signoff_blocks_on_selected_failed_run(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    record_run(project, _run_record("run-pass", "PASS", seed=1))
    record_run(project, _run_record("run-fail", "FAIL", seed=2))

    bundle = build_verification_signoff_bundle(project, run_limit=10)

    assert bundle["summary"]["review_state"] == "BLOCKED"
    checks = {item["name"]: item for item in bundle["checks"]}
    assert checks["simulation"]["status"] == "FAIL"
    assert checks["simulation"]["details"]["nonpass_run_ids"] == ["run-fail"]


def test_signoff_coverage_requirement_and_threshold(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    record_run(project, _run_record("run-pass", "PASS"))
    record_coverage_snapshot(project, _coverage_snapshot(75.0))

    blocked = build_verification_signoff_bundle(
        project,
        min_coverage=80.0,
    )
    ready = build_verification_signoff_bundle(
        project,
        min_coverage=70.0,
    )

    blocked_checks = {item["name"]: item for item in blocked["checks"]}
    ready_checks = {item["name"]: item for item in ready["checks"]}
    assert blocked["summary"]["review_state"] == "BLOCKED"
    assert blocked_checks["coverage"]["status"] == "FAIL"
    assert ready["summary"]["review_state"] == "READY_FOR_REVIEW"
    assert ready_checks["coverage"]["status"] == "PASS"


def test_signoff_writer_and_cli(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    record_run(project, _run_record("run-pass", "PASS"))

    direct = write_verification_signoff_bundle(
        project,
        output=".zddv/signoff/direct.json",
    )
    assert Path(direct["path"]).is_file()

    rc = main(
        [
            "--project",
            str(project.root),
            "signoff",
            "--output",
            ".zddv/signoff/cli.json",
        ]
    )
    output = capsys.readouterr().out
    assert rc == 0
    assert "SIGNOFF READY_FOR_REVIEW" in output
    assert "Signoff SHA-256:" in output
    assert (project.root / ".zddv" / "signoff" / "cli.json").is_file()
