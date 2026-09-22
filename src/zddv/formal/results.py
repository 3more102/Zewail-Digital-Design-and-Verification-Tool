from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
import uuid

from zddv.config import ProjectConfig
from zddv.formal.base import (
    FormalCheckRequest,
    FormalCheckResult,
    FormalPropertyResult,
)
from zddv.storage import record_formal_result_snapshot


def _require_object(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def _require_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    return _require_text(value, field=field)


def _string_list(value: Any, *, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_require_text(item, field=f"{field}[{index}]"))
    return tuple(result)


def _property_from_data(item: Any, *, index: int) -> FormalPropertyResult:
    data = _require_object(item, field=f"properties[{index}]")
    return FormalPropertyResult(
        name=_require_text(data.get("name"), field=f"properties[{index}].name"),
        kind=_require_text(data.get("kind"), field=f"properties[{index}].kind"),
        status=_require_text(
            data.get("status"),
            field=f"properties[{index}].status",
        ),
        depth=data.get("depth"),
        trace_path=data.get("trace_path"),
        message=_optional_text(
            data.get("message"),
            field=f"properties[{index}].message",
        ),
    )


def formal_result_from_data(payload: Any) -> FormalCheckResult:
    """Build a normalized FormalCheckResult from simulator-independent JSON data."""

    data = _require_object(payload, field="formal result payload")
    request_data = _require_object(data.get("request"), field="request")

    request = FormalCheckRequest(
        mode=_require_text(request_data.get("mode"), field="request.mode"),
        depth=request_data.get("depth"),
        properties=_string_list(
            request_data.get("properties"),
            field="request.properties",
        ),
        timeout_s=request_data.get("timeout_s"),
    )

    raw_properties = data.get("properties", [])
    if not isinstance(raw_properties, list):
        raise ValueError("properties must be an array")
    properties = tuple(
        _property_from_data(item, index=index)
        for index, item in enumerate(raw_properties)
    )

    command = _string_list(data.get("command"), field="command")
    artifacts = _string_list(data.get("artifacts"), field="artifacts")

    return FormalCheckResult(
        backend=_require_text(data.get("backend"), field="backend"),
        request=request,
        command=command,
        returncode=int(data.get("returncode", 0)),
        status=_require_text(data.get("status"), field="status"),
        run_dir=Path(_require_text(data.get("run_dir"), field="run_dir")),
        log_path=Path(_require_text(data.get("log_path"), field="log_path")),
        properties=properties,
        artifacts=tuple(Path(item) for item in artifacts),
        engine=_optional_text(data.get("engine"), field="engine"),
        runtime_ms=data.get("runtime_ms"),
    )


def _proof_scope(request: FormalCheckRequest) -> str:
    if request.mode == "bmc":
        return "BOUNDED"
    if request.mode == "prove":
        return "UNBOUNDED"
    return "COVER"


def _interpret_property(
    request: FormalCheckRequest,
    item: FormalPropertyResult,
) -> str:
    if item.status in {"UNKNOWN", "ERROR"}:
        return item.status

    if item.kind == "assert":
        if item.status == "FAIL":
            return "COUNTEREXAMPLE"
        if request.mode == "bmc":
            if item.depth is not None or request.depth is not None:
                return "BOUNDED_SAFE"
            return "PASS_BOUNDED_UNSCOPED"
        if request.mode == "prove":
            return "PROVED"
        return "ASSERT_PASS"

    if item.status == "COVERED":
        return "COVERED"
    return "UNREACHED"


def _trace_role(item: FormalPropertyResult) -> str | None:
    if item.trace_path is None:
        return None
    if item.kind == "assert" and item.status == "FAIL":
        return "COUNTEREXAMPLE"
    if item.kind == "cover" and item.status == "COVERED":
        return "WITNESS"
    return "EVIDENCE"


def formal_result_to_record(result: FormalCheckResult) -> dict[str, Any]:
    """Serialize normalized formal evidence without strengthening its claims."""

    property_records: list[dict[str, Any]] = []
    for item in result.properties:
        effective_depth = (
            item.depth
            if item.depth is not None
            else result.request.depth
        )
        property_records.append(
            {
                "name": item.name,
                "kind": item.kind,
                "status": item.status,
                "interpretation": _interpret_property(result.request, item),
                "depth": item.depth,
                "effective_depth": effective_depth,
                "message": item.message,
                "trace": (
                    None
                    if item.trace_path is None
                    else {
                        "path": str(item.trace_path),
                        "role": _trace_role(item),
                    }
                ),
            }
        )

    kinds = Counter(item.kind for item in result.properties)
    statuses = Counter(item.status for item in result.properties)
    interpretations = Counter(
        item["interpretation"] for item in property_records
    )

    return {
        "analysis": "formal_results",
        "backend": result.backend,
        "engine": result.engine,
        "status": result.status,
        "request": {
            "mode": result.request.mode,
            "scope": _proof_scope(result.request),
            "depth": result.request.depth,
            "properties": list(result.request.properties),
            "timeout_s": result.request.timeout_s,
        },
        "execution": {
            "command": list(result.command),
            "returncode": result.returncode,
            "run_dir": str(result.run_dir),
            "log_path": str(result.log_path),
            "runtime_ms": result.runtime_ms,
        },
        "summary": {
            "properties": len(result.properties),
            "assertions": kinds.get("assert", 0),
            "covers": kinds.get("cover", 0),
            "property_statuses": dict(sorted(statuses.items())),
            "interpretations": dict(sorted(interpretations.items())),
            "counterexamples": interpretations.get("COUNTEREXAMPLE", 0),
            "bounded_safe_assertions": interpretations.get("BOUNDED_SAFE", 0),
            "proved_assertions": interpretations.get("PROVED", 0),
            "covered_goals": interpretations.get("COVERED", 0),
            "unreached_goals": interpretations.get("UNREACHED", 0),
        },
        "properties": property_records,
        "artifacts": [str(path) for path in result.artifacts],
    }


def persist_formal_check_result(
    project: ProjectConfig,
    result: FormalCheckResult,
    *,
    input_path: str | Path,
    output: str | Path = ".zddv/formal/latest.json",
) -> dict[str, Any]:
    """Persist an in-memory formal result through the shared evidence model."""

    source_path = Path(input_path)
    if not source_path.is_absolute():
        source_path = project.root / source_path
    source_path = source_path.resolve()

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
            "input_path": str(source_path),
            "report_path": str(report_path),
        }
    )

    report_path.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    record_formal_result_snapshot(project, record)
    return record


def analyze_formal_result_file(
    project: ProjectConfig,
    path: str | Path,
    *,
    output: str | Path = ".zddv/formal/latest.json",
) -> dict[str, Any]:
    """Import normalized formal JSON and persist a ZDDV evidence record."""

    input_path = Path(path)
    if not input_path.is_absolute():
        input_path = project.root / input_path
    input_path = input_path.resolve()

    with input_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    result = formal_result_from_data(payload)
    return persist_formal_check_result(
        project,
        result,
        input_path=input_path,
        output=output,
    )
