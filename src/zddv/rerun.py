from __future__ import annotations

from typing import Any

from zddv.config import ProjectConfig
from zddv.simulator import SimulatorBackend, get_backend
from zddv.storage import get_run_record, list_run_records


DEFAULT_RERUN_STATUSES = ("FAIL", "TIMEOUT")


def select_historical_runs(
    project: ProjectConfig,
    *,
    limit: int = 20,
    statuses: tuple[str, ...] | list[str] = DEFAULT_RERUN_STATUSES,
) -> list[dict[str, Any]]:
    """Select persisted runs using the same status contract as the CLI."""
    return list_run_records(project, limit=limit, statuses=statuses)


def historical_run_snapshot(
    project: ProjectConfig,
    run_id: str,
) -> dict[str, Any]:
    """Return the exact persisted run evidence used to prepare a reviewed rerun."""
    source = get_run_record(project, run_id)
    if source is None:
        raise ValueError(f"Run not found: {run_id}")

    return {
        "run_id": str(source["run_id"]),
        "created_at": source["created_at"],
        "status": source["status"],
        "identity": {
            "project": source["project"],
            "simulator": source["simulator"],
            "simulator_version": source["simulator_version"],
            "top": source["top"],
        },
        "recorded_inputs": {
            "test_name": source["test_name"],
            "seed": source["seed"],
            "plusargs": list(source["plusargs"]),
            "timeout_s": source["timeout_s"],
        },
        "recorded_command": list(source["command"]),
        "evidence": {
            "run_dir": source["run_dir"],
            "log_path": source["log_path"],
            "waveform_path": source["waveform_path"],
            "coverage_path": source["coverage_path"],
        },
    }


def rerun_records(
    project: ProjectConfig,
    rows: list[dict[str, Any]],
    *,
    backend: SimulatorBackend | None = None,
) -> dict[str, Any]:
    """Build once, then rerun the exact recorded runtime inputs."""
    selected = [dict(row) for row in rows]
    if not selected:
        return {
            "status": "NO_RUNS",
            "selected": 0,
            "passed": 0,
            "failed": 0,
            "build": None,
            "results": [],
        }

    active_backend = backend or get_backend(project.simulator)
    build = active_backend.build(project)
    build_payload = {
        "passed": bool(build.passed),
        "returncode": int(build.returncode),
        "log_path": str(build.log_path),
        "command": list(build.command),
    }
    if not build.passed:
        return {
            "status": "BUILD_FAIL",
            "selected": len(selected),
            "passed": 0,
            "failed": len(selected),
            "build": build_payload,
            "results": [],
        }

    results: list[dict[str, Any]] = []
    passed = 0
    for source in reversed(selected):
        result = active_backend.run(
            project,
            test_name=source["test_name"],
            seed=source["seed"],
            plusargs=list(source["plusargs"]),
            timeout_s=source["timeout_s"],
        )
        if result.status == "PASS":
            passed += 1
        results.append(
            {
                "source_run_id": str(source["run_id"]),
                "run_id": str(result.run_id),
                "status": str(result.status),
                "returncode": int(result.returncode),
                "test_name": result.test_name,
                "seed": result.seed,
                "log_path": str(result.log_path),
                "waveform_path": (
                    None if result.waveform_path is None else str(result.waveform_path)
                ),
                "coverage_path": (
                    None if result.coverage_path is None else str(result.coverage_path)
                ),
            }
        )

    total = len(results)
    return {
        "status": "PASS" if passed == total else "FAIL",
        "selected": len(selected),
        "passed": passed,
        "failed": total - passed,
        "build": build_payload,
        "results": results,
    }


def rerun_run_id(
    project: ProjectConfig,
    run_id: str,
    *,
    backend: SimulatorBackend | None = None,
) -> dict[str, Any]:
    """Rerun one persisted run by ID using its recorded runtime inputs."""
    source = get_run_record(project, run_id)
    if source is None:
        raise ValueError(f"Run not found: {run_id}")
    summary = rerun_records(project, [source], backend=backend)
    return {
        **summary,
        "source": historical_run_snapshot(project, run_id),
    }
