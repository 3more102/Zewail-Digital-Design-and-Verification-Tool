from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import tomllib

from zddv.config import ProjectConfig
from zddv.simulator.base import RunResult, SimulatorBackend


@dataclass(frozen=True)
class RegressionCase:
    name: str
    seeds: tuple[int, ...]
    plusargs: tuple[str, ...]
    timeout_s: float | None = None


@dataclass(frozen=True)
class RegressionSpec:
    name: str
    jobs: int
    cases: tuple[RegressionCase, ...]


def load_regression(path: str | Path) -> RegressionSpec:
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Regression file not found: {path}")

    with path.open("rb") as f:
        raw = tomllib.load(f)

    meta = raw.get("regression", {})
    tests = raw.get("tests", [])

    if not tests:
        raise ValueError("Regression must contain at least one [[tests]] entry.")

    cases: list[RegressionCase] = []
    for item in tests:
        name = str(item.get("name", "")).strip()
        if not name:
            raise ValueError("Each regression test requires a non-empty name.")

        seeds_raw = item.get("seeds", [1])
        seeds = tuple(int(seed) for seed in seeds_raw)
        if not seeds:
            raise ValueError(f"Test '{name}' must contain at least one seed.")

        plusargs = tuple(str(arg) for arg in item.get("plusargs", []))
        timeout = item.get("timeout_s")
        timeout_s = float(timeout) if timeout is not None else None

        cases.append(
            RegressionCase(
                name=name,
                seeds=seeds,
                plusargs=plusargs,
                timeout_s=timeout_s,
            )
        )

    jobs = int(meta.get("jobs", 1))
    if jobs < 1:
        raise ValueError("regression.jobs must be >= 1.")

    return RegressionSpec(
        name=str(meta.get("name", path.stem)),
        jobs=jobs,
        cases=tuple(cases),
    )


def _run_one(
    backend: SimulatorBackend,
    project: ProjectConfig,
    case: RegressionCase,
    seed: int,
) -> RunResult:
    return backend.run(
        project,
        test_name=case.name,
        seed=seed,
        plusargs=list(case.plusargs),
        timeout_s=case.timeout_s,
    )


def run_regression(
    project: ProjectConfig,
    backend: SimulatorBackend,
    regression_file: str | Path,
) -> dict:
    spec = load_regression(regression_file)

    build = backend.build(project)
    if not build.passed:
        raise RuntimeError(f"Regression build failed. See {build.log_path}")

    work: list[tuple[int, RegressionCase, int]] = []
    index = 0
    for case in spec.cases:
        for seed in case.seeds:
            work.append((index, case, seed))
            index += 1

    results: list[tuple[int, RunResult]] = []
    with ThreadPoolExecutor(max_workers=spec.jobs) as pool:
        futures = {
            pool.submit(_run_one, backend, project, case, seed): idx
            for idx, case, seed in work
        }
        for future in as_completed(futures):
            idx = futures[future]
            results.append((idx, future.result()))

    results.sort(key=lambda item: item[0])
    ordered = [result for _, result in results]

    passed = sum(result.status == "PASS" for result in ordered)
    failed = sum(result.status == "FAIL" for result in ordered)
    timed_out = sum(result.status == "TIMEOUT" for result in ordered)
    total = len(ordered)
    status = "PASS" if passed == total else "FAIL"

    now = datetime.now(timezone.utc)
    regress_id = now.strftime("%Y%m%dT%H%M%SZ")
    regress_dir = (project.root / ".zddv" / "regressions").resolve()
    regress_dir.mkdir(parents=True, exist_ok=True)
    summary_path = regress_dir / f"{regress_id}-{spec.name}.json"

    record = {
        "regression": spec.name,
        "created_at": now.isoformat(),
        "project": project.name,
        "simulator": backend.name,
        "jobs": spec.jobs,
        "status": status,
        "total": total,
        "passed": passed,
        "failed": failed,
        "timed_out": timed_out,
        "runs": [
            {
                "run_id": result.run_id,
                "test": result.test_name,
                "seed": result.seed,
                "status": result.status,
                "returncode": result.returncode,
                "run_dir": str(result.run_dir),
                "log": str(result.log_path),
                "waveform": (
                    str(result.waveform_path) if result.waveform_path else None
                ),
                "coverage": (
                    str(result.coverage_path) if result.coverage_path else None
                ),
            }
            for result in ordered
        ],
    }
    summary_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    record["summary_path"] = str(summary_path)
    return record
