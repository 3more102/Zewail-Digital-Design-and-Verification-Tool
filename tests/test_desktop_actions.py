from __future__ import annotations

from pathlib import Path

import pytest

from zddv.config import initialize_project
from zddv.desktop_actions import build_rerun_action_preview, execute_rerun_action
from zddv.simulator import BuildResult, RunResult
from zddv.storage import record_run


def _record(project, run_id: str = "run-old") -> None:
    run_dir = project.root / ".zddv" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    record_run(
        project,
        {
            "run_id": run_id,
            "created_at": "2026-09-23T07:00:00+00:00",
            "project": project.name,
            "simulator": project.simulator,
            "simulator_version": "test",
            "top": project.top,
            "test": "stress",
            "seed": 23,
            "status": "FAIL",
            "returncode": 1,
            "duration_ms": 12.5,
            "run_dir": str(run_dir),
            "log": str(run_dir / "simulation.log"),
            "waveform": None,
            "coverage": None,
            "timeout_s": 42.0,
            "command": ["sim", "+MODE=stress"],
            "plusargs": ["+MODE=stress"],
        },
    )


class _Backend:
    def __init__(self, project, *, build_passed: bool = True):
        self.project = project
        self.build_passed = build_passed
        self.build_calls = 0
        self.run_calls = []

    def build(self, project):
        self.build_calls += 1
        assert project is self.project
        return BuildResult(
            command=["build"],
            returncode=0 if self.build_passed else 2,
            log_path=project.root / "build.log",
            executable=(project.root / "simv") if self.build_passed else None,
        )

    def run(
        self,
        project,
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
                "plusargs": plusargs,
                "timeout_s": timeout_s,
            }
        )
        run_dir = project.root / ".zddv" / "runs" / "run-new"
        return RunResult(
            run_id="run-new",
            command=["sim"],
            returncode=0,
            status="PASS",
            run_dir=run_dir,
            log_path=run_dir / "simulation.log",
            waveform_path=None,
            coverage_path=None,
            test_name=test_name,
            seed=seed,
        )


def test_rerun_preview_is_deterministic_and_non_executing(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    _record(project)

    first = build_rerun_action_preview(project, "run-old")
    second = build_rerun_action_preview(project, "run-old")

    assert first == second
    assert first["action"] == "rerun"
    assert first["source_run"] == {
        "run_id": "run-old",
        "status": "FAIL",
        "test_name": "stress",
        "seed": 23,
        "plusargs": ["+MODE=stress"],
        "timeout_s": 42.0,
    }
    assert first["effects"]["execute_simulation"] is True
    assert first["effects"]["invoke_ai"] is False
    assert len(first["review"]["confirmation_sha256"]) == 64


def test_rerun_execution_rejects_wrong_confirmation_without_building(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    _record(project)
    backend = _Backend(project)

    with pytest.raises(ValueError, match="confirmation fingerprint"):
        execute_rerun_action(
            project,
            "run-old",
            confirmation_sha256="0" * 64,
            backend=backend,
        )

    assert backend.build_calls == 0
    assert backend.run_calls == []


def test_rerun_execution_uses_exact_reviewed_historical_inputs(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    _record(project)
    backend = _Backend(project)
    preview = build_rerun_action_preview(project, "run-old")

    result = execute_rerun_action(
        project,
        "run-old",
        confirmation_sha256=preview["review"]["confirmation_sha256"],
        backend=backend,
    )

    assert backend.build_calls == 1
    assert backend.run_calls == [
        {
            "test_name": "stress",
            "seed": 23,
            "plusargs": ["+MODE=stress"],
            "timeout_s": 42.0,
        }
    ]
    assert result["status"] == "PASS"
    assert result["run"]["run_id"] == "run-new"
    assert result["source_run_id"] == "run-old"


def test_rerun_execution_stops_after_build_failure(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    _record(project)
    backend = _Backend(project, build_passed=False)
    preview = build_rerun_action_preview(project, "run-old")

    result = execute_rerun_action(
        project,
        "run-old",
        confirmation_sha256=preview["review"]["confirmation_sha256"],
        backend=backend,
    )

    assert result["status"] == "BUILD_FAIL"
    assert result["run"] is None
    assert backend.build_calls == 1
    assert backend.run_calls == []


def test_rerun_preview_rejects_stale_project_identity(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    _record(project)
    project.top = "different_top"

    with pytest.raises(RuntimeError, match="identity mismatch"):
        build_rerun_action_preview(project, "run-old")
