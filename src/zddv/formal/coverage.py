from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from zddv.config import ProjectConfig
from zddv.storage import list_formal_result_snapshots


def _configuration_id(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _load_snapshot_record(row: dict[str, Any]) -> dict[str, Any] | None:
    path = Path(row["report_path"])
    if not path.is_file():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict):
        return None
    if payload.get("snapshot_id") != row["snapshot_id"]:
        return None
    return payload


def _aggregate_property_status(statuses: list[str]) -> str:
    if "COVERED" in statuses:
        return "COVERED"
    if statuses and all(status == "UNCOVERED" for status in statuses):
        return "UNCOVERED"
    if "ERROR" in statuses:
        return "ERROR"
    return "UNKNOWN"


def aggregate_formal_cover_coverage(
    project: ProjectConfig,
    *,
    limit: int = 200,
    backend: str | None = None,
    depth: int | None = None,
) -> dict[str, Any]:
    """Aggregate only explicitly complete and mutually comparable cover snapshots."""

    if limit < 1:
        raise ValueError("limit must be >= 1")
    if depth is not None and depth < 1:
        raise ValueError("depth must be >= 1 when provided")

    backend_filter = backend.strip() if backend is not None else None
    if backend_filter == "":
        raise ValueError("backend must not be empty")

    rows = list_formal_result_snapshots(
        project,
        limit=limit,
        mode="cover",
    )

    excluded = Counter()
    groups: dict[str, dict[str, Any]] = {}
    eligible_snapshots = 0

    for row in rows:
        if backend_filter is not None and row["backend"] != backend_filter:
            continue
        if depth is not None and row["request_depth"] != depth:
            continue

        record = _load_snapshot_record(row)
        if record is None:
            excluded["missing_or_invalid_report"] += 1
            continue

        if record.get("property_set_complete") is not True:
            excluded["incomplete_property_set"] += 1
            continue

        design_fingerprint = record.get("design_fingerprint")
        if not isinstance(design_fingerprint, str) or not design_fingerprint:
            excluded["missing_design_fingerprint"] += 1
            continue

        request = record.get("request")
        if not isinstance(request, dict) or request.get("mode") != "cover":
            excluded["invalid_cover_request"] += 1
            continue

        request_depth = request.get("depth")
        if (
            isinstance(request_depth, bool)
            or not isinstance(request_depth, int)
            or request_depth < 1
        ):
            excluded["missing_explicit_depth"] += 1
            continue

        raw_properties = record.get("properties")
        if not isinstance(raw_properties, list) or not raw_properties:
            excluded["empty_property_universe"] += 1
            continue

        property_rows: list[tuple[str, str]] = []
        seen_names: set[str] = set()
        valid = True
        for item in raw_properties:
            if not isinstance(item, dict) or item.get("kind") != "cover":
                valid = False
                break
            name = item.get("name")
            status = item.get("status")
            if (
                not isinstance(name, str)
                or not name
                or name in seen_names
                or status not in {"COVERED", "UNCOVERED", "UNKNOWN", "ERROR"}
            ):
                valid = False
                break
            seen_names.add(name)
            property_rows.append((name, status))

        if not valid:
            excluded["invalid_property_universe"] += 1
            continue

        property_names = sorted(seen_names)
        compatibility = {
            "design_fingerprint": design_fingerprint,
            "top": record.get("top"),
            "backend": record.get("backend"),
            "engine": record.get("engine"),
            "depth": request_depth,
            "properties": property_names,
        }
        group_id = _configuration_id(compatibility)

        group = groups.setdefault(
            group_id,
            {
                "configuration_id": group_id,
                "design_fingerprint": design_fingerprint,
                "top": record.get("top"),
                "backend": record.get("backend"),
                "engine": record.get("engine"),
                "depth": request_depth,
                "property_count": len(property_names),
                "snapshot_count": 0,
                "snapshots": [],
                "_statuses": defaultdict(list),
                "_created_at": [],
            },
        )

        group["snapshot_count"] += 1
        group["snapshots"].append(row["snapshot_id"])
        group["_created_at"].append(row["created_at"])
        for name, status in property_rows:
            group["_statuses"][name].append(status)
        eligible_snapshots += 1

    output_groups: list[dict[str, Any]] = []
    for group in groups.values():
        properties = []
        status_totals = Counter()
        for name in sorted(group["_statuses"]):
            statuses = group["_statuses"][name]
            aggregate_status = _aggregate_property_status(statuses)
            status_totals[aggregate_status] += 1
            per_run = Counter(statuses)
            properties.append(
                {
                    "name": name,
                    "status": aggregate_status,
                    "covered_runs": per_run.get("COVERED", 0),
                    "uncovered_runs": per_run.get("UNCOVERED", 0),
                    "unknown_runs": per_run.get("UNKNOWN", 0),
                    "error_runs": per_run.get("ERROR", 0),
                }
            )

        total = group["property_count"]
        covered = status_totals.get("COVERED", 0)
        output_groups.append(
            {
                "configuration_id": group["configuration_id"],
                "design_fingerprint": group["design_fingerprint"],
                "top": group["top"],
                "backend": group["backend"],
                "engine": group["engine"],
                "depth": group["depth"],
                "property_count": total,
                "snapshot_count": group["snapshot_count"],
                "covered_goals": covered,
                "unreached_goals": status_totals.get("UNCOVERED", 0),
                "unknown_goals": status_totals.get("UNKNOWN", 0),
                "error_goals": status_totals.get("ERROR", 0),
                "coverage_rate": (covered / total) * 100.0,
                "snapshots": list(group["snapshots"]),
                "latest_created_at": max(group["_created_at"]),
                "properties": properties,
            }
        )

    output_groups.sort(
        key=lambda item: (
            item["latest_created_at"],
            item["configuration_id"],
        ),
        reverse=True,
    )

    return {
        "analysis": "formal_cover_coverage",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project": project.name,
        "scope": "FINITE_DEPTH_COVER_REACHABILITY",
        "semantics": (
            "Groups require the same design fingerprint, backend, engine, "
            "explicit depth, and complete cover-property universe. COVERED "
            "means reached in at least one snapshot inside that exact group."
        ),
        "considered_snapshots": len(rows),
        "eligible_snapshots": eligible_snapshots,
        "excluded_snapshots": dict(sorted(excluded.items())),
        "groups": output_groups,
    }


def write_formal_cover_coverage_report(
    project: ProjectConfig,
    *,
    limit: int = 200,
    backend: str | None = None,
    depth: int | None = None,
    output: str | Path = ".zddv/formal/coverage.json",
) -> dict[str, Any]:
    report = aggregate_formal_cover_coverage(
        project,
        limit=limit,
        backend=backend,
        depth=depth,
    )

    path = Path(output)
    if not path.is_absolute():
        path = project.root / path
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    report["report_path"] = str(path)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report
