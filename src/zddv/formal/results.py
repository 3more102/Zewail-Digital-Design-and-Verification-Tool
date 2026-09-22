from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
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
from zddv.formal.vcd_trace import ingest_formal_vcd_trace
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


def _optional_bool(value: Any, *, field: str, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


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
        property_set_complete=_optional_bool(
            data.get("property_set_complete"),
            field="property_set_complete",
        ),
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
        "property_set_complete": result.property_set_complete,
    }


def formal_design_fingerprint(project: ProjectConfig) -> str:
    """Hash the formal design inputs so aggregate metrics never cross design revisions."""

    digest = hashlib.sha256()
    digest.update(b"zddv-formal-design-v1\0")
    digest.update(project.top.encode("utf-8"))
    digest.update(b"\0")

    root = project.root.resolve()
    sources = sorted(
        (path.resolve() for path in project.source_files()),
        key=lambda path: path.as_posix(),
    )
    for source in sources:
        try:
            label = source.relative_to(root).as_posix()
        except ValueError:
            label = source.as_posix()
        digest.update(label.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(source.read_bytes()).digest())

    return digest.hexdigest()


def _resolve_trace_source(
    project: ProjectConfig,
    result: FormalCheckResult,
    source_path: Path,
    trace_path: Path,
) -> tuple[Path | None, list[Path]]:
    """Resolve an attached trace only when its on-disk location is unambiguous."""

    if trace_path.is_absolute():
        resolved = trace_path.resolve()
        return (resolved if resolved.is_file() else None, [resolved])

    bases: list[Path] = []
    for raw_base in (result.run_dir, source_path.parent, project.root):
        base = Path(raw_base)
        if not base.is_absolute():
            base = project.root / base
        resolved_base = base.resolve()
        if resolved_base not in bases:
            bases.append(resolved_base)

    candidates: list[Path] = []
    for base in bases:
        candidate = (base / trace_path).resolve()
        if candidate.is_file() and candidate not in candidates:
            candidates.append(candidate)

    if len(candidates) == 1:
        return candidates[0], candidates
    return None, candidates


def _normalize_attached_vcd_traces(
    project: ProjectConfig,
    result: FormalCheckResult,
    record: dict[str, Any],
    *,
    source_path: Path,
    report_path: Path,
    snapshot_id: str,
) -> None:
    """Attach bounded VCD normalization evidence without strengthening formal claims."""

    for index, (item, property_record) in enumerate(
        zip(result.properties, record["properties"], strict=True)
    ):
        trace = property_record.get("trace")
        if trace is None or item.trace_path is None:
            continue
        if trace.get("role") not in {"COUNTEREXAMPLE", "WITNESS"}:
            continue

        resolved, candidates = _resolve_trace_source(
            project,
            result,
            source_path,
            Path(item.trace_path),
        )
        if resolved is None:
            if len(candidates) > 1:
                trace["normalization"] = {
                    "status": "AMBIGUOUS_PATH",
                    "candidates": [str(path) for path in candidates],
                }
            else:
                trace["normalization"] = {
                    "status": "MISSING",
                    "path": str(item.trace_path),
                }
            continue

        if resolved.suffix.lower() != ".vcd":
            trace["normalization"] = {
                "status": "UNSUPPORTED_FORMAT",
                "path": str(resolved),
                "format": resolved.suffix.lower() or None,
            }
            continue

        destination = report_path.parent / "traces" / snapshot_id / f"{index:04d}.json"
        try:
            normalized = ingest_formal_vcd_trace(
                project,
                resolved,
                property_name=item.name,
                property_kind=item.kind,
                source=f"{result.backend}-auto-vcd",
                output=destination,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            trace["normalization"] = {
                "status": "ERROR",
                "path": str(resolved),
                "error": str(exc),
            }
            continue

        trace["normalization"] = {
            "status": "NORMALIZED",
            "path": normalized["normalized_path"],
            "schema": normalized["schema"],
            "input_sha256": normalized["input_sha256"],
            "signals": normalized["summary"]["signals"],
            "steps": normalized["summary"]["steps"],
            "time_unit": normalized.get("time_unit"),
        }


def persist_formal_result(
    project: ProjectConfig,
    result: FormalCheckResult,
    *,
    input_path: str | Path,
    output: str | Path = ".zddv/formal/latest.json",
) -> dict[str, Any]:
    """Persist one already-normalized formal execution/import result."""

    source_path = Path(input_path)
    if not source_path.is_absolute():
        source_path = project.root / source_path
    source_path = source_path.resolve()

    record = formal_result_to_record(result)
    record["top"] = project.top
    record["design_fingerprint"] = formal_design_fingerprint(project)

    report_path = Path(output)
    if not report_path.is_absolute():
        report_path = project.root / report_path
    report_path = report_path.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)

    snapshot_id = uuid.uuid4().hex
    record.update(
        {
            "snapshot_id": snapshot_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "project": project.name,
            "input_path": str(source_path),
            "report_path": str(report_path),
        }
    )
    _normalize_attached_vcd_traces(
        project,
        result,
        record,
        source_path=source_path,
        report_path=report_path,
        snapshot_id=snapshot_id,
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
    return persist_formal_result(
        project,
        result,
        input_path=input_path,
        output=output,
    )
