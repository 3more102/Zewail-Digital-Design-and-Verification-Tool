from pathlib import Path

from zddv.config import initialize_project
from zddv.rerun import rerun_history
from zddv.simulator.base import BuildResult, RunResult
from zddv.storage import record_run


def _record(run_id: str, status: str, seed: int, plusargs: list[str]) -> dict:
    return {
        "run_id": run_id,
        "created_at": f"2026-09-21T19:10:0{seed}+00:00",
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
        "log": f"/tmp/{run_id}/simulation.log",
        "waveform": None,
        "coverage": None,
        "timeout_s": 7.5,
        "command": ["zddv_sim"],
        "plusargs": plusargs,
    }


class FakeBackend:
    name = "fake"

    def __init__(self, root: Path):
        self.root = root
        self.calls: list[dict] = []

    def build(self, project):
        executable = self.root / "zddv_sim"
        executable.write_text("", encoding="utf-8")
        log = self.root / "build.log"
        log.write_text("", encoding="utf-8")
        return BuildResult([], 0, log, executable)

    def run(
        self,
        project,
        *,
        test_name=None,
        seed=None,
        plusargs=None,
        timeout_s=None,
    ):
        self.calls.append(
            {
                "test_name": test_name,
                "seed": seed,
                "plusargs": plusargs,
                "timeout_s": timeout_s,
            }
        )
        run_dir = self.root / f"rerun-{seed}"
        run_dir.mkdir(exist_ok=True)
        log = run_dir / "simulation.log"
        log.write_text("PASS\n", encoding="utf-8")
        return RunResult(
            run_id=f"rerun-{seed}",
            command=["zddv_sim"],
            returncode=0,
            status="PASS",
            run_dir=run_dir,
            log_path=log,
            waveform_path=None,
            test_name=test_name,
            seed=seed,
        )


def test_rerun_failed_history_preserves_metadata(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    record_run(project, _record("run-pass", "PASS", 1, ["+MODE=0"]))
    record_run(project, _record("run-fail", "FAIL", 2, ["+MODE=1"]))

    backend = FakeBackend(tmp_path)
    summary = rerun_history(project, backend, statuses=("FAIL",), limit=10)

    assert summary["status"] == "PASS"
    assert summary["selected"] == 1
    assert summary["passed"] == 1
    assert backend.calls == [
        {
            "test_name": "smoke",
            "seed": 2,
            "plusargs": ["+MODE=1"],
            "timeout_s": 7.5,
        }
    ]


def test_rerun_empty_selection_does_not_build(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    backend = FakeBackend(tmp_path)

    summary = rerun_history(project, backend, statuses=("FAIL",), limit=10)

    assert summary["selected"] == 0
    assert backend.calls == []
