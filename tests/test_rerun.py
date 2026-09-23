from __future__ import annotations

from pathlib import Path

import pytest

from zddv.config import initialize_project
from zddv.rerun import historical_run_snapshot, rerun_run_id, rerun_snapshot
from zddv.simulator.base import BuildResult, RunResult
from zddv.storage import record_run


def _record(
    project,
    *,
    run_id: str = "run-fail",
    seed: int = 7,
    simulator: str | None = None,
    top: str | None = None,
) -> None:
    record_run(
        project,
        {
            "run_id": run_id,
            "created_at": "2026-09-23T07:15:00+00:00",
            "project": project.name,
            "simulator": simulator or project.simulator,
            "simulator_version": "Verilator test",
            "top": top or project.top,
            "test": "smoke",
            "seed": seed,
            "status": "FAIL",
            "returncode": 1,
            "duration_ms": 12.0,
            "run_dir": f".zddv/runs/{run_id}",
            "log": f".zddv/runs/{run_id}/simulation.log",
            "waveform": f".zddv/runs/{run_id}/trace.vcd",
            "coverage": None,
            "timeout_s": 30.0,
            "command": ["sim", "+MODE=stress"],
            "plusargs": ["+MODE=stress", "+COUNT=4"],
        },
    )


class _Backend:
    def __init__(self, root: Path, *, build_passes: bool = True):
        self.root = root
        self.build_passes = build_passes
        self.build_calls = 0
        self.run_calls = []

    def build(self, _project):
        self.build_calls += 1
        return BuildResult(
            command=["build"],
            returncode=0 if self.build_passes else 1,
            log_path=self.root / "build.log",
            executable=(self.root / "sim") if self.build_passes else None,
        )

    def run(
        self,
        _project,
        *,
        test_name=None,
        seed=None,
        plusargs=None,
        timeout_s=None,
    ):
        self.run_calls.append(
            {
                "test_name": test_name,
                "seed": seed,
                "plusargs": list(plusargs or []),
                "timeout_s": timeout_s,
            }
        )
        return RunResult(
            run_id="rerun-pass",
            command=["sim"],
            returncode=0,
            status="PASS",
            run_dir=self.root / ".zddv" / "runs" / "rerun-pass",
            log_path=self.root / ".zddv" / "runs" / "rerun-pass" / "simulation.log",
            waveform_path=None,
            coverage_path=None,
            test_name=test_name,
            seed=seed,
        )


def test_historical_run_snapshot_binds_recorded_inputs(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    _record(project)

    snapshot = historical_run_snapshot(project, "run-fail")

    assert snapshot["run_id"] == "run-fail"
    assert snapshot["status"] == "FAIL"
    assert snapshot["identity"]["project"] == project.name
    assert snapshot["recorded_inputs"] == {
        "test_name": "smoke",
        "seed": 7,
        "plusargs": ["+MODE=stress", "+COUNT=4"],
        "timeout_s": 30.0,
    }
    assert snapshot["recorded_command"] == ["sim", "+MODE=stress"]
    assert snapshot["replay_contract"] == {
        "backend": "current_configured_backend",
        "recorded_runtime_inputs_exact": True,
        "recorded_command_replayed_verbatim": False,
    }


def test_rerun_run_id_uses_exact_recorded_runtime_inputs(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    _record(project)
    backend = _Backend(project.root)

    result = rerun_run_id(project, "run-fail", backend=backend)

    assert backend.build_calls == 1
    assert backend.run_calls == [
        {
            "test_name": "smoke",
            "seed": 7,
            "plusargs": ["+MODE=stress", "+COUNT=4"],
            "timeout_s": 30.0,
        }
    ]
    assert result["status"] == "PASS"
    assert result["selected"] == 1
    assert result["passed"] == 1
    assert result["results"][0]["source_run_id"] == "run-fail"
    assert result["source"]["run_id"] == "run-fail"


def test_rerun_run_id_stops_on_build_failure(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    _record(project)
    backend = _Backend(project.root, build_passes=False)

    result = rerun_run_id(project, "run-fail", backend=backend)

    assert result["status"] == "BUILD_FAIL"
    assert result["failed"] == 1
    assert backend.run_calls == []


def test_rerun_run_id_rejects_unknown_run(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")

    with pytest.raises(ValueError, match="Run not found"):
        rerun_run_id(project, "missing")


def test_rerun_snapshot_uses_reviewed_inputs_even_if_database_record_changes(
    tmp_path: Path,
):
    project = initialize_project(tmp_path / "demo")
    _record(project, seed=7)
    snapshot = historical_run_snapshot(project, "run-fail")
    _record(project, seed=99)
    backend = _Backend(project.root)

    result = rerun_snapshot(project, snapshot, backend=backend)

    assert result["status"] == "PASS"
    assert backend.run_calls[0]["seed"] == 7
    assert result["source"]["recorded_inputs"]["seed"] == 7


def test_reviewed_rerun_rejects_incompatible_historical_identity(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    _record(project, run_id="wrong-top", top="different_top")

    with pytest.raises(ValueError, match="Historical run identity mismatch"):
        historical_run_snapshot(project, "wrong-top")


def test_rerun_snapshot_rejects_tampered_identity(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    _record(project)
    snapshot = historical_run_snapshot(project, "run-fail")
    snapshot["identity"]["simulator"] = "questa"

    with pytest.raises(ValueError, match="Historical run identity mismatch"):
        rerun_snapshot(project, snapshot, backend=_Backend(project.root))
