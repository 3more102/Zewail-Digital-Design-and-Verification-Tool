from __future__ import annotations

from pathlib import Path
import re
from typing import Any
import uuid

from zddv.config import ProjectConfig

from .base import FormalCheckRequest, FormalCheckResult, FormalPropertyResult
from .results import persist_formal_result


_DONE_RE = re.compile(
    r"\bDONE\s*\(\s*(?P<status>PASS|FAIL|UNKNOWN|ERROR)\s*,\s*rc=(?P<rc>-?\d+)\s*\)",
    re.IGNORECASE,
)
_ENGINE_RE = re.compile(
    r"summary:\s+engine(?:_|\s+)\d+\s+\((?P<engine>[^)]+)\)\s+returned\b",
    re.IGNORECASE,
)
_ASSERT_FAIL_RE = re.compile(
    r"##\s+Assert failed in\s+(?P<scope>[^:]+):\s*(?P<name>.+?)\s*$",
    re.IGNORECASE,
)
_SUMMARY_ASSERT_FAIL_RE = re.compile(
    r"summary:\s+failed assertion\s+(?P<name>\S+)\s+at\s+"
    r"(?P<location>.+?)\s+in step\s+(?P<step>\d+)\s*$",
    re.IGNORECASE,
)
_COVER_REACHED_RE = re.compile(
    r"##\s+Reached cover statement at\s+(?P<name>.+?)\s+in step\s+(?P<step>\d+)\.\s*$",
    re.IGNORECASE,
)
_TRACE_RE = re.compile(
    r"##\s+Writing trace to VCD file:\s*(?P<path>\S+)\s*$",
    re.IGNORECASE,
)
_COUNTEREXAMPLE_RE = re.compile(
    r"summary:\s+counterexample trace:\s*(?P<path>\S+)\s*$",
    re.IGNORECASE,
)


def _resolve_artifact(run_dir: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    if path.parts and path.parts[0] == run_dir.name:
        return (run_dir.parent / path).resolve()
    return (run_dir / path).resolve()


def parse_sby_log(
    path: str | Path,
    *,
    mode: str,
    depth: int | None = None,
) -> FormalCheckResult:
    """Normalize only explicit evidence from a completed SymbiYosys logfile."""

    normalized_mode = mode.strip().lower()
    if normalized_mode not in {"bmc", "cover"}:
        raise ValueError(
            "native SymbiYosys logfile import currently supports bmc and cover only; "
            "prove mode requires phase-aware basecase/induction interpretation"
        )

    log_path = Path(path).resolve()
    text = log_path.read_text(encoding="utf-8", errors="replace")

    terminals = list(_DONE_RE.finditer(text))
    if not terminals:
        raise ValueError(
            "SymbiYosys log has no terminal DONE marker; refusing to infer completion status"
        )

    terminal = terminals[-1]
    status = terminal.group("status").upper()
    returncode = int(terminal.group("rc"))
    request = FormalCheckRequest(mode=normalized_mode, depth=depth)
    run_dir = log_path.parent

    engines = list(_ENGINE_RE.finditer(text))
    engine = engines[-1].group("engine").strip() if engines else None

    rows: list[dict[str, Any]] = []
    row_index: dict[tuple[str, str, str], int] = {}
    pending_index: int | None = None
    artifacts: list[Path] = []
    artifact_seen: set[Path] = set()
    pending_counterexample_trace: Path | None = None

    def remember_artifact(raw_path: str) -> Path:
        resolved = _resolve_artifact(run_dir, raw_path)
        if resolved not in artifact_seen:
            artifact_seen.add(resolved)
            artifacts.append(resolved)
        return resolved

    def remember_failed_assertion(
        *,
        name: str,
        message: str,
    ) -> int:
        nonlocal pending_counterexample_trace
        key = ("assert", name, "FAIL")
        index = row_index.get(key)
        if index is None:
            rows.append(
                {
                    "name": name,
                    "kind": "assert",
                    "status": "FAIL",
                    "message": message,
                    "trace_path": pending_counterexample_trace,
                }
            )
            index = len(rows) - 1
            row_index[key] = index
            pending_counterexample_trace = None
        return index

    for line in text.splitlines():
        match = _ASSERT_FAIL_RE.search(line)
        if match:
            scope = match.group("scope").strip()
            name = match.group("name").strip()
            pending_index = remember_failed_assertion(
                name=name,
                message=f"SymbiYosys reported assertion failure in {scope}",
            )
            continue

        match = _SUMMARY_ASSERT_FAIL_RE.search(line)
        if match:
            name = match.group("name").strip()
            location = match.group("location").strip()
            step = int(match.group("step"))
            pending_index = remember_failed_assertion(
                name=name,
                message=(
                    "SymbiYosys summary reported assertion failure "
                    f"at {location} in solver step {step}"
                ),
            )
            continue

        match = _COVER_REACHED_RE.search(line)
        if match:
            name = match.group("name").strip()
            step = int(match.group("step"))
            key = ("cover", name, "COVERED")
            pending_index = row_index.get(key)
            if pending_index is None:
                rows.append(
                    {
                        "name": name,
                        "kind": "cover",
                        "status": "COVERED",
                        "message": (
                            "SymbiYosys reached cover statement "
                            f"at solver step {step}"
                        ),
                        "trace_path": None,
                    }
                )
                pending_index = len(rows) - 1
                row_index[key] = pending_index
            continue

        match = _TRACE_RE.search(line)
        if match:
            trace = remember_artifact(match.group("path"))
            if pending_index is not None and rows[pending_index]["trace_path"] is None:
                rows[pending_index]["trace_path"] = trace
            continue

        match = _COUNTEREXAMPLE_RE.search(line)
        if match:
            trace = remember_artifact(match.group("path"))
            attached = False
            for index in range(len(rows) - 1, -1, -1):
                if rows[index]["kind"] == "assert" and rows[index]["status"] == "FAIL":
                    if rows[index]["trace_path"] is None:
                        rows[index]["trace_path"] = trace
                        attached = True
                    break
            if not attached:
                pending_counterexample_trace = trace

    properties = tuple(
        FormalPropertyResult(
            name=row["name"],
            kind=row["kind"],
            status=row["status"],
            trace_path=row["trace_path"],
            message=row["message"],
        )
        for row in rows
    )

    return FormalCheckResult(
        backend="sby",
        engine=engine,
        request=request,
        command=(),
        returncode=returncode,
        status=status,
        run_dir=run_dir,
        log_path=log_path,
        properties=properties,
        artifacts=tuple(artifacts),
    )


def analyze_sby_log(
    project: ProjectConfig,
    path: str | Path,
    *,
    mode: str,
    depth: int | None = None,
    output: str | Path | None = None,
) -> dict[str, Any]:
    """Import a native SymbiYosys logfile into normalized persisted evidence."""

    input_path = Path(path)
    if not input_path.is_absolute():
        input_path = project.root / input_path
    input_path = input_path.resolve()

    result = parse_sby_log(input_path, mode=mode, depth=depth)
    report_path = (
        Path(output)
        if output is not None
        else Path(".zddv/formal/sby") / f"import-{uuid.uuid4().hex}.json"
    )
    return persist_formal_result(
        project,
        result,
        input_path=input_path,
        output=report_path,
    )
