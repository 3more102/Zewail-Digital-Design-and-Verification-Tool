from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
import uuid

from zddv.config import ProjectConfig


_PROPERTY_KINDS = {"ASSERT", "COVER"}
_PROPERTY_STATUSES = {"PASS", "FAIL", "UNKNOWN", "ERROR"}
_PROOF_SCOPES = {"BOUNDED", "UNBOUNDED", "UNSPECIFIED"}


def _require_text(value: Any, *, field: str, index: int | None = None) -> str:
    prefix = f"properties[{index}]." if index is not None else ""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{prefix}{field} must be a non-empty string")
    return value.strip()


def _optional_text(
    value: Any,
    *,
    field: str,
    index: int | None = None,
) -> str | None:
    if value is None:
        return None
    return _require_text(value, field=field, index=index)


def _normalize_artifact(
    value: Any,
    *,
    field: str,
    index: int,
) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"properties[{index}].{field} must be an object")

    path = _require_text(value.get("path"), field=f"{field}.path", index=index)
    artifact_format = _optional_text(
        value.get("format"),
        field=f"{field}.format",
        index=index,
    )
    description = _optional_text(
        value.get("description"),
        field=f"{field}.description",
        index=index,
    )
    metadata = value.get("metadata", {})
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError(
            f"properties[{index}].{field}.metadata must be an object"
        )

    return {
        "path": path,
        "format": artifact_format,
        "description": description,
        "metadata": dict(metadata),
    }


def _normalize_location(value: Any, *, index: int) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"properties[{index}].location must be an object")

    path = _optional_text(
        value.get("path"),
        field="location.path",
        index=index,
    )
    line = value.get("line")
    column = value.get("column")

    for field, number in (("line", line), ("column", column)):
        if number is None:
            continue
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise ValueError(
                f"properties[{index}].location.{field} must be an integer >= 1"
            )

    if path is None and line is None and column is None:
        raise ValueError(
            f"properties[{index}].location must contain path, line, or column"
        )

    return {
        "path": path,
        "line": line,
        "column": column,
    }


def _interpret_property(
    *,
    kind: str,
    status: str,
    scope: str,
) -> str:
    if status == "ERROR":
        return "ERROR"
    if status == "UNKNOWN":
        return "UNKNOWN"

    if kind == "ASSERT":
        if status == "FAIL":
            return "COUNTEREXAMPLE"
        if scope == "UNBOUNDED":
            return "PROVED"
        if scope == "BOUNDED":
            return "BOUNDED_SAFE"
        return "PASS_UNSCOPED"

    if status == "PASS":
        return "COVERED"
    return "UNREACHED"


def _normalize_property(item: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError(f"properties[{index}] must be an object")

    name = _require_text(item.get("name"), field="name", index=index)
    kind = _require_text(item.get("kind"), field="kind", index=index).upper()
    if kind not in _PROPERTY_KINDS:
        raise ValueError(
            f"properties[{index}].kind must be one of: "
            f"{', '.join(sorted(_PROPERTY_KINDS))}"
        )

    status = _require_text(
        item.get("status"),
        field="status",
        index=index,
    ).upper()
    if status not in _PROPERTY_STATUSES:
        raise ValueError(
            f"properties[{index}].status must be one of: "
            f"{', '.join(sorted(_PROPERTY_STATUSES))}"
        )

    raw_scope = item.get("scope", "UNSPECIFIED")
    if not isinstance(raw_scope, str) or not raw_scope.strip():
        raise ValueError(f"properties[{index}].scope must be a non-empty string")
    scope = raw_scope.strip().upper()
    if scope not in _PROOF_SCOPES:
        raise ValueError(
            f"properties[{index}].scope must be one of: "
            f"{', '.join(sorted(_PROOF_SCOPES))}"
        )

    depth = item.get("depth")
    if depth is not None:
        if isinstance(depth, bool) or not isinstance(depth, int) or depth < 0:
            raise ValueError(
                f"properties[{index}].depth must be an integer >= 0 or null"
            )
    if scope == "BOUNDED" and depth is None:
        raise ValueError(
            f"properties[{index}].depth is required for BOUNDED scope"
        )

    metadata = item.get("metadata", {})
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError(f"properties[{index}].metadata must be an object")

    counterexample = _normalize_artifact(
        item.get("counterexample"),
        field="counterexample",
        index=index,
    )
    witness = _normalize_artifact(
        item.get("witness"),
        field="witness",
        index=index,
    )

    if counterexample is not None and not (
        kind == "ASSERT" and status == "FAIL"
    ):
        raise ValueError(
            f"properties[{index}].counterexample is only valid for "
            "failed ASSERT properties"
        )
    if witness is not None and not (kind == "COVER" and status == "PASS"):
        raise ValueError(
            f"properties[{index}].witness is only valid for passed COVER properties"
        )

    return {
        "name": name,
        "kind": kind,
        "status": status,
        "scope": scope,
        "depth": depth,
        "interpretation": _interpret_property(
            kind=kind,
            status=status,
            scope=scope,
        ),
        "message": _optional_text(
            item.get("message"),
            field="message",
            index=index,
        ),
        "location": _normalize_location(item.get("location"), index=index),
        "counterexample": counterexample,
        "witness": witness,
        "metadata": dict(metadata),
    }


def normalize_formal_data(
    payload: Any,
    *,
    source: str | None = None,
) -> dict[str, Any]:
    """Normalize tool-specific formal evidence without inventing proof strength."""

    if not isinstance(payload, dict):
        raise ValueError("Formal result payload must be a JSON object")

    raw_properties = payload.get("properties")
    if not isinstance(raw_properties, list):
        raise ValueError("Formal result payload must contain a properties array")

    engine = _require_text(payload.get("engine"), field="engine")
    selected_source = source or payload.get("source") or "formal-json"
    selected_source = _require_text(selected_source, field="source")
    engine_version = _optional_text(
        payload.get("engine_version"),
        field="engine_version",
    )

    metadata = payload.get("metadata", {})
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")

    properties = [
        _normalize_property(item, index=index)
        for index, item in enumerate(raw_properties)
    ]

    assertions = [item for item in properties if item["kind"] == "ASSERT"]
    covers = [item for item in properties if item["kind"] == "COVER"]

    status_counts = {
        status.lower(): sum(item["status"] == status for item in properties)
        for status in ("PASS", "FAIL", "UNKNOWN", "ERROR")
    }

    if any(item["status"] == "ERROR" for item in assertions):
        proof_status = "ERROR"
    elif any(item["status"] == "FAIL" for item in assertions):
        proof_status = "FAIL"
    elif any(item["status"] == "UNKNOWN" for item in assertions):
        proof_status = "UNKNOWN"
    else:
        proof_status = "PASS"

    return {
        "analysis": "formal_results",
        "source": selected_source,
        "engine": engine,
        "engine_version": engine_version,
        "status": proof_status,
        "summary": {
            "properties": len(properties),
            "assertions": len(assertions),
            "covers": len(covers),
            **status_counts,
            "proved_assertions": sum(
                item["interpretation"] == "PROVED" for item in assertions
            ),
            "bounded_safe_assertions": sum(
                item["interpretation"] == "BOUNDED_SAFE" for item in assertions
            ),
            "counterexamples": sum(
                item["counterexample"] is not None for item in assertions
            ),
            "covered_goals": sum(
                item["interpretation"] == "COVERED" for item in covers
            ),
            "unreached_goals": sum(
                item["interpretation"] == "UNREACHED" for item in covers
            ),
        },
        "properties": properties,
        "metadata": dict(metadata),
    }


def analyze_formal_file(
    project: ProjectConfig,
    path: str | Path,
    *,
    source: str | None = None,
    output: str | Path = ".zddv/formal/latest.json",
) -> dict[str, Any]:
    input_path = Path(path)
    if not input_path.is_absolute():
        input_path = project.root / input_path
    input_path = input_path.resolve()

    with input_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    result = normalize_formal_data(payload, source=source)
    report_path = Path(output)
    if not report_path.is_absolute():
        report_path = project.root / report_path
    report_path = report_path.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)

    result.update(
        {
            "snapshot_id": uuid.uuid4().hex,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "project": project.name,
            "input_path": str(input_path),
            "report_path": str(report_path),
        }
    )
    report_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result
