from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any
import uuid

from zddv.config import ProjectConfig
from zddv.storage import record_formal_result_snapshot

from .base import FormalCheckRequest, FormalCheckResult, FormalPropertyResult
from .results import formal_result_to_record


_DONE_RE = re.compile(
    r"\bDONE\s*\(\s*(?P<status>PASS|FAIL|UNKNOWN|ERROR)\s*,\s*rc=(?P<rc>-?\d+)\s*\)",
    re.IGNORECASE,
)
_ENGINE_RE = re.compile(
    r"summary:\s+engine_\d+\s+\((?P<engine>[^)]+)\)\s+returned\b",
    re.IGNORECASE,
)
_ASSERT_FAIL_RE = re.compile(
    r"##\s+Assert failed in\s+(?P<scope>[^:]+):\s*(?P<name>.+?)\s*$",
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
    """Normalize explicit evidence from a completed native SymbiYosys logfile."""

    log_path = Path(path).resolve()
    text = log_path.read_text(encoding="utf-8", errors="replace")
    done = list(_DONE_RE.finditer(text))
    if not done:
        raise ValueError(
            "SymbiYosys log has no terminal DONE marker; refusing to infer completion status"
        )

    terminal = done[-1]
    status = terminal.group("status").upper()
    returncode = int(terminal.group("rc"))
    request = FormalCheckRequest(mode=mode, depth=depth)
    run_dir = log_path.parent

    engines = list(_ENGINE_RE.finditer(text))
    engine = engines[-1].group("engine").strip() if engines else None

    rows: list[dict[str, Any]] = []
    pending_index: int | None = None

    for line in text.splitlines():
        match = _ASSERT_FAIL_RE.search(line)
        if match:
            scope = match.group("scope").strip()
            name = match.group("name").strip()
            rows.append(
                {
                    "name": name,
                    "kind": "assert",
                    "status": "FAIL",
                    "message": f"SymbiYosys reported assertion failure in {scope}",
                    "trace_path": None,
                }
            )
            pending_index = len(rows) - 1
            continue

        match = _COVER_REACHED_RE.search(line)
        if match:
            name = match.group("name").strip()
            step = int(match.group("step"))
            rows.append(
                {
                    "name": name,
                    "kind": "cover",
                    "status": "COVERED",
                    "message": f"SymbiYosys reached cover statement at solver step {step}",
                    "trace_path": None,
                }
            )
            pending_index = len(rows) - 1
            continue

        match = _TRACE_RE.search(line)
        if match and pending_index is not None:
            if rows[pending_index]["trace_path"] is None:
                rows[pending_index]["trace_path"] = _resolve_artifact(
                    run_dir,
                    match.group("path"),
                )
            continue

        match = _COUNTEREXAMPLE_RE.search(line)
        if match:
            trace = _resolve_artifact(run_dir, match.group("path"))
            for index in range(len(rows) - 1, -1, -1):
                if rows[index]["kind"] == "assert" and rows[index]["status"] == "FAIL":
                    if rows[index]["trace_path"] is None:
                        rows[index]["trace_path"] = trace
                    break

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

    artifacts: list[Path] = []
    seen: set[Path] = set()
    for item in properties:
        if item.trace_path is not None and item.trace_path not in seen:
            seen.add(item.trace_path)
            artifacts.append(item.trace_path)

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
    output: str | Path = ".zddv/formal/sby/latest.json",
) -> dict[str, Any]:
    """Import a native SymbiYosys logfile and persist explicit formal evidence."""

    input_path = Path(path)
    if not input_path.is_absolute():
        input_path = project.root / input_path
    input_path = input_path.resolve()

    result = parse_sby_log(input_path, mode=mode, depth=depth)
    record = formal_result_to_record(result)

    report_path = Path(output)
    if not report_path.is_absolute():
        report_path = project.root / report_path
    report_path = report_path.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)

    record.update(
        {
            "snapshot_id": uuid.uuid4().hex,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "project": project.name,
            "input_path": str(input_path),
            "input_format": "symbiyosys-log",
            "report_path": str(report_path),
        }
    )

    report_path.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    record_formal_result_snapshot(project, record)
    return record
