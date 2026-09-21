from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import tomllib
import xml.etree.ElementTree as ET

from zddv.config import ProjectConfig
from zddv.simulator.base import RunResult, SimulatorBackend
from zddv.triage import failure_signature, group_failure_records


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


def _run_record(result: RunResult) -> dict:
    metadata: dict = {}
    try:
        metadata = json.loads((result.run_dir / "run.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass

    return {
        "run_id": result.run_id,
        "test": result.test_name,
        "seed": result.seed,
        "status": result.status,
        "returncode": result.returncode,
        "duration_ms": metadata.get("duration_ms"),
        "plusargs": metadata.get("plusargs", []),
        "timeout_s": metadata.get("timeout_s"),
        "run_dir": str(result.run_dir),
        "log": str(result.log_path),
        "waveform": str(result.waveform_path) if result.waveform_path else None,
        "coverage": str(result.coverage_path) if result.coverage_path else None,
        "failure_signature": failure_signature(result.log_path, result.status),
    }


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return cleaned[:80] or "regression"


def write_junit_report(record: dict, path: str | Path) -> Path:
    path = Path(path)
    suite = ET.Element(
        "testsuite",
        {
            "name": str(record["regression"]),
            "tests": str(record["total"]),
            "failures": str(record["failed"]),
            "errors": str(record["timed_out"]),
        },
    )

    for run in record["runs"]:
        name = run.get("test") or "unnamed"
        seed = run.get("seed")
        if seed is not None:
            name = f"{name}[seed={seed}]"
        attrs = {"name": name, "classname": str(record["regression"])}
        duration_ms = run.get("duration_ms")
        if duration_ms is not None:
            attrs["time"] = f"{float(duration_ms) / 1000.0:.6f}"
        case = ET.SubElement(suite, "testcase", attrs)

        status = str(run.get("status", ""))
        signature = run.get("failure_signature") or status
        if status == "FAIL":
            node = ET.SubElement(
                case,
                "failure",
                {"message": str(signature), "type": "simulation-failure"},
            )
            node.text = f"log: {run.get('log', '')}"
        elif status == "TIMEOUT":
            node = ET.SubElement(
                case,
                "error",
                {"message": str(signature), "type": "simulation-timeout"},
            )
            node.text = f"log: {run.get('log', '')}"

    tree = ET.ElementTree(suite)
    ET.indent(tree, space="  ")
    path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return path


def _finalize_regression(
    project: ProjectConfig,
    backend: SimulatorBackend,
    *,
    name: str,
    jobs: int,
    ordered: list[RunResult],
    source_summary: str | None = None,
) -> dict:
    runs = [_run_record(result) for result in ordered]
    passed = sum(run["status"] == "PASS" for run in runs)
    failed = sum(run["status"] == "FAIL" for run in runs)
    timed_out = sum(run["status"] == "TIMEOUT" for run in runs)
    total = len(runs)
    status = "PASS" if passed == total else "FAIL"

    now = datetime.now(timezone.utc)
    regress_id = now.strftime("%Y%m%dT%H%M%SZ")
    regress_dir = (project.root / ".zddv" / "regressions").resolve()
    regress_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{regress_id}-{_safe_name(name)}"
    summary_path = regress_dir / f"{stem}.json"
    junit_path = regress_dir / f"{stem}.xml"

    record = {
        "regression": name,
        "created_at": now.isoformat(),
        "project": project.name,
        "simulator": backend.name,
        "jobs": jobs,
        "status": status,
        "total": total,
        "passed": passed,
        "failed": failed,
        "timed_out": timed_out,
        "failure_groups": group_failure_records(runs),
        "runs": runs,
    }
    if source_summary is not None:
        record["source_summary"] = source_summary

    summary_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    write_junit_report(record, junit_path)
    record["summary_path"] = str(summary_path)
    record["junit_path"] = str(junit_path)
    return record


def _execute_work(
    project: ProjectConfig,
    backend: SimulatorBackend,
    work: list[tuple[int, RegressionCase, int]],
    *,
    jobs: int,
) -> list[RunResult]:
    results: list[tuple[int, RunResult]] = []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {
            pool.submit(_run_one, backend, project, case, seed): idx
            for idx, case, seed in work
        }
        for future in as_completed(futures):
            idx = futures[future]
            results.append((idx, future.result()))

    results.sort(key=lambda item: item[0])
    return [result for _, result in results]


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

    ordered = _execute_work(project, backend, work, jobs=spec.jobs)
    return _finalize_regression(
        project,
        backend,
        name=spec.name,
        jobs=spec.jobs,
        ordered=ordered,
    )


def _metadata_from_previous_run(run: dict) -> tuple[list[str], float | None]:
    plusargs = list(run.get("plusargs") or [])
    timeout_s = run.get("timeout_s")
    if plusargs or timeout_s is not None:
        return plusargs, float(timeout_s) if timeout_s is not None else None

    run_dir = run.get("run_dir")
    if not run_dir:
        return plusargs, None
    try:
        raw = json.loads((Path(run_dir) / "run.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return plusargs, None
    plusargs = list(raw.get("plusargs") or [])
    timeout_s = raw.get("timeout_s")
    return plusargs, float(timeout_s) if timeout_s is not None else None


def rerun_from_summary(
    project: ProjectConfig,
    backend: SimulatorBackend,
    summary_file: str | Path,
    *,
    statuses: tuple[str, ...] = ("FAIL", "TIMEOUT"),
    jobs: int = 1,
) -> dict:
    if jobs < 1:
        raise ValueError("jobs must be >= 1")

    summary_path = Path(summary_file).resolve()
    if not summary_path.exists():
        raise FileNotFoundError(f"Regression summary not found: {summary_path}")
    raw = json.loads(summary_path.read_text(encoding="utf-8"))

    wanted = {status.upper() for status in statuses}
    invalid = wanted - {"PASS", "FAIL", "TIMEOUT"}
    if invalid:
        raise ValueError(f"Unsupported rerun status: {', '.join(sorted(invalid))}")

    selected = [
        run
        for run in raw.get("runs", [])
        if str(run.get("status", "")).upper() in wanted
    ]
    if not selected:
        raise ValueError("No runs in the summary match the requested rerun status.")

    build = backend.build(project)
    if not build.passed:
        raise RuntimeError(f"Rerun build failed. See {build.log_path}")

    work: list[tuple[int, RegressionCase, int]] = []
    for idx, run in enumerate(selected):
        test_name = str(run.get("test") or "rerun")
        seed = int(run.get("seed") if run.get("seed") is not None else 1)
        plusargs, timeout_s = _metadata_from_previous_run(run)
        case = RegressionCase(
            name=test_name,
            seeds=(seed,),
            plusargs=tuple(plusargs),
            timeout_s=timeout_s,
        )
        work.append((idx, case, seed))

    ordered = _execute_work(project, backend, work, jobs=jobs)
    source_name = str(raw.get("regression", summary_path.stem))
    return _finalize_regression(
        project,
        backend,
        name=f"{source_name}-rerun",
        jobs=jobs,
        ordered=ordered,
        source_summary=str(summary_path),
    )
