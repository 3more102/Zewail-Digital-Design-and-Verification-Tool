from __future__ import annotations

import json
from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project
from zddv.signoff import write_verification_signoff_bundle
from zddv.signoff_diff import (
    compare_verification_signoff_bundles,
    write_verification_signoff_diff,
)
from zddv.storage import record_coverage_snapshot, record_run


def _run_record(run_id: str, *, seed: int = 1) -> dict:
    return {
        "run_id": run_id,
        "created_at": f"2026-09-23T06:00:{seed:02d}+00:00",
        "project": "demo",
        "simulator": "verilator",
        "simulator_version": "Verilator test",
        "top": "tb_top",
        "test": "smoke",
        "seed": seed,
        "status": "PASS",
        "returncode": 0,
        "duration_ms": 10.0,
        "run_dir": f"/tmp/{run_id}",
        "log": f"/tmp/{run_id}.log",
        "waveform": None,
        "coverage": None,
        "timeout_s": 10.0,
        "command": ["zddv_sim"],
        "plusargs": [],
    }


def _coverage_snapshot(percent: float, snapshot_id: str) -> dict:
    hit = int(percent)
    return {
        "snapshot_id": snapshot_id,
        "created_at": "2026-09-23T06:10:00+00:00",
        "project": "demo",
        "simulator": "verilator",
        "input_count": 1,
        "total_points": 100,
        "hit_points": hit,
        "hit_rate": float(percent),
        "by_type": {
            "line": {
                "total": 100,
                "hit": hit,
                "hit_rate": float(percent),
            }
        },
        "merged": "/tmp/coverage.dat",
        "summary": "/tmp/coverage-summary.txt",
        "metrics_path": "/tmp/coverage-metrics.json",
    }


def test_signoff_diff_identical_bundles_are_stable(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    record_run(project, _run_record("run-pass"))
    write_verification_signoff_bundle(
        project,
        run_ids=["run-pass"],
        output=".zddv/signoff/baseline.json",
    )
    write_verification_signoff_bundle(
        project,
        run_ids=["run-pass"],
        output=".zddv/signoff/current.json",
    )

    first = compare_verification_signoff_bundles(
        project,
        ".zddv/signoff/baseline.json",
        ".zddv/signoff/current.json",
    )
    second = compare_verification_signoff_bundles(
        project,
        ".zddv/signoff/baseline.json",
        ".zddv/signoff/current.json",
    )

    assert first["summary"]["changed"] is False
    assert first["policy"]["changed"] is False
    assert first["evidence"]["changed"] is False
    assert first["checks"]["changed"] is False
    assert first["provenance"]["diff_sha256"] == second["provenance"]["diff_sha256"]


def test_signoff_diff_reports_exact_policy_and_evidence_changes(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    record_run(project, _run_record("run-a", seed=1))
    write_verification_signoff_bundle(
        project,
        run_ids=["run-a"],
        output=".zddv/signoff/baseline.json",
    )

    record_run(project, _run_record("run-b", seed=2))
    record_coverage_snapshot(project, _coverage_snapshot(82.0, "cov-current"))
    write_verification_signoff_bundle(
        project,
        run_ids=["run-b"],
        min_coverage=80.0,
        coverage_snapshot_id="cov-current",
        output=".zddv/signoff/current.json",
    )

    diff = compare_verification_signoff_bundles(
        project,
        ".zddv/signoff/baseline.json",
        ".zddv/signoff/current.json",
    )

    assert diff["summary"]["changed"] is True
    assert diff["summary"]["baseline_review_state"] == "READY_FOR_REVIEW"
    assert diff["summary"]["current_review_state"] == "READY_FOR_REVIEW"
    assert diff["policy"]["changed"] is True
    assert "run_selection" in diff["policy"]["changed_keys"]
    assert "coverage_snapshot_id" in diff["policy"]["changed_keys"]
    assert "min_coverage" in diff["policy"]["changed_keys"]
    assert diff["evidence"]["runs"]["added_run_ids"] == ["run-b"]
    assert diff["evidence"]["runs"]["removed_run_ids"] == ["run-a"]
    assert diff["evidence"]["coverage"]["changed"] is True
    assert diff["evidence"]["coverage"]["baseline"] is None
    assert diff["evidence"]["coverage"]["current"]["snapshot_id"] == "cov-current"


def test_signoff_diff_rejects_tampered_bundle(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    record_run(project, _run_record("run-pass"))
    baseline = project.root / ".zddv" / "signoff" / "baseline.json"
    write_verification_signoff_bundle(
        project,
        run_ids=["run-pass"],
        output=baseline,
    )
    write_verification_signoff_bundle(
        project,
        run_ids=["run-pass"],
        output=".zddv/signoff/current.json",
    )

    payload = json.loads(baseline.read_text(encoding="utf-8"))
    payload["summary"]["review_state"] = "BLOCKED"
    baseline.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="signoff_sha256"):
        compare_verification_signoff_bundles(
            project,
            ".zddv/signoff/baseline.json",
            ".zddv/signoff/current.json",
        )


def test_signoff_diff_cli_writes_report(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    record_run(project, _run_record("run-pass"))
    write_verification_signoff_bundle(
        project,
        run_ids=["run-pass"],
        output=".zddv/signoff/baseline.json",
    )
    write_verification_signoff_bundle(
        project,
        run_ids=["run-pass"],
        output=".zddv/signoff/current.json",
    )

    rc = main(
        [
            "--project",
            str(project.root),
            "signoff-diff",
            ".zddv/signoff/baseline.json",
            ".zddv/signoff/current.json",
            "--output",
            ".zddv/signoff/diff.json",
        ]
    )
    output = capsys.readouterr().out

    assert rc == 0
    assert "SIGNOFF DIFF: changed=no" in output
    assert "Diff SHA-256:" in output
    assert (project.root / ".zddv" / "signoff" / "diff.json").is_file()


def test_signoff_diff_writer_rejects_output_outside_project(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    record_run(project, _run_record("run-pass"))
    write_verification_signoff_bundle(
        project,
        run_ids=["run-pass"],
        output=".zddv/signoff/baseline.json",
    )
    write_verification_signoff_bundle(
        project,
        run_ids=["run-pass"],
        output=".zddv/signoff/current.json",
    )

    with pytest.raises(ValueError, match="inside the project root"):
        write_verification_signoff_diff(
            project,
            ".zddv/signoff/baseline.json",
            ".zddv/signoff/current.json",
            output=tmp_path / "outside-diff.json",
        )
