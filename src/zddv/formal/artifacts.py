from __future__ import annotations

from collections import Counter
import hashlib
from pathlib import Path
from typing import Any

from zddv.formal.base import FormalCheckResult, FormalPropertyResult


_FORMAT_BY_SUFFIX = {
    ".vcd": "VCD",
    ".fst": "FST",
    ".wlf": "WLF",
    ".vpd": "VPD",
    ".fsdb": "FSDB",
    ".json": "JSON",
    ".txt": "TEXT",
    ".log": "LOG",
}


def _trace_role(item: FormalPropertyResult) -> str:
    if item.kind == "assert" and item.status == "FAIL":
        return "COUNTEREXAMPLE"
    if item.kind == "cover" and item.status == "COVERED":
        return "WITNESS"
    return "TRACE_EVIDENCE"


def _artifact_format(path: Path) -> str | None:
    return _FORMAT_BY_SUFFIX.get(path.suffix.lower())


def _resolve_artifact_path(result: FormalCheckResult, path: Path) -> Path:
    if path.is_absolute():
        return path.resolve()
    return (result.run_dir / path).resolve()


def _fingerprint(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _record_path(
    result: FormalCheckResult,
    path: Path,
    *,
    role: str,
    property_name: str | None = None,
    property_kind: str | None = None,
    property_status: str | None = None,
    depth: int | None = None,
    verify_files: bool,
) -> dict[str, Any]:
    resolved = _resolve_artifact_path(result, path)
    exists = resolved.is_file()

    size: int | None = None
    sha256: str | None = None
    if verify_files and exists:
        size, sha256 = _fingerprint(resolved)

    return {
        "path": str(path),
        "resolved_path": str(resolved),
        "role": role,
        "format": _artifact_format(path),
        "exists": exists,
        "size_bytes": size,
        "sha256": sha256,
        "property_name": property_name,
        "property_kind": property_kind,
        "property_status": property_status,
        "depth": depth,
    }


def collect_formal_artifact_manifest(
    result: FormalCheckResult,
    *,
    verify_files: bool = True,
) -> dict[str, Any]:
    """Collect trace/supporting-artifact evidence from one formal check.

    Paths are resolved relative to the formal run directory, matching the
    execution artifact boundary. Missing files are retained explicitly rather
    than dropped. File hashes are calculated only when verification is enabled
    and the file exists.
    """

    records: list[dict[str, Any]] = []

    for item in result.properties:
        if item.trace_path is None:
            continue
        records.append(
            _record_path(
                result,
                item.trace_path,
                role=_trace_role(item),
                property_name=item.name,
                property_kind=item.kind,
                property_status=item.status,
                depth=item.depth if item.depth is not None else result.request.depth,
                verify_files=verify_files,
            )
        )

    trace_paths = {
        record["resolved_path"]
        for record in records
    }
    for path in result.artifacts:
        resolved = _resolve_artifact_path(result, path)
        if str(resolved) in trace_paths:
            continue
        records.append(
            _record_path(
                result,
                path,
                role="SUPPORTING",
                verify_files=verify_files,
            )
        )

    roles = Counter(record["role"] for record in records)
    formats = Counter(
        record["format"]
        for record in records
        if record["format"] is not None
    )
    unique_paths = {record["resolved_path"] for record in records}

    return {
        "analysis": "formal_artifact_manifest",
        "backend": result.backend,
        "engine": result.engine,
        "status": result.status,
        "run_dir": str(result.run_dir.resolve()),
        "log_path": str(result.log_path),
        "verify_files": verify_files,
        "summary": {
            "artifact_links": len(records),
            "unique_files": len(unique_paths),
            "existing_files": sum(record["exists"] for record in records),
            "missing_files": sum(not record["exists"] for record in records),
            "verified_files": sum(
                record["sha256"] is not None for record in records
            ),
            "roles": dict(sorted(roles.items())),
            "formats": dict(sorted(formats.items())),
        },
        "artifacts": records,
    }
