from __future__ import annotations

from zddv.config import ProjectConfig
from zddv.simulator.base import RunResult, SimulatorBackend
from zddv.storage import rerun_candidates


def rerun_history(
    project: ProjectConfig,
    backend: SimulatorBackend,
    *,
    statuses: tuple[str, ...] = ("FAIL", "TIMEOUT"),
    limit: int = 20,
) -> dict:
    candidates = rerun_candidates(project, statuses=statuses, limit=limit)
    if not candidates:
        return {
            "status": "PASS",
            "selected": 0,
            "passed": 0,
            "failed": 0,
            "timed_out": 0,
            "runs": [],
        }

    build = backend.build(project)
    if not build.passed:
        raise RuntimeError(f"Rerun build failed. See {build.log_path}")

    results: list[RunResult] = []
    for candidate in reversed(candidates):
        results.append(
            backend.run(
                project,
                test_name=candidate["test_name"],
                seed=candidate["seed"],
                plusargs=list(candidate["plusargs"]),
                timeout_s=candidate["timeout_s"],
            )
        )

    passed = sum(result.status == "PASS" for result in results)
    failed = sum(result.status == "FAIL" for result in results)
    timed_out = sum(result.status == "TIMEOUT" for result in results)

    return {
        "status": "PASS" if passed == len(results) else "FAIL",
        "selected": len(results),
        "passed": passed,
        "failed": failed,
        "timed_out": timed_out,
        "runs": results,
    }
