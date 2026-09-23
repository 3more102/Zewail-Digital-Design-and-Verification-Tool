from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from zddv.coverage import (
    parse_questa_code_coverage_report,
    parse_questa_toggle_coverage_xml,
)


_TEXT_EVIDENCE = (
    "code-details.txt",
    "multibit-expression.txt",
    "zeros.txt",
    "toggle-details.txt",
)
_XML_EVIDENCE = (
    "details.xml",
    "toggle-details.xml",
)
_PENDING_SCHEMAS = (
    "multibit-condition",
    "enumerated-or-unknown-toggle",
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text_cues(text: str, *, limit: int = 24) -> list[str]:
    cues: list[str] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        lowered = stripped.casefold()
        if (
            "coverage" in lowered
            or lowered.startswith(("line ", "rows:", "row ", "covered ", "uncovered "))
            or "fec" in lowered
            or "toggle" in lowered
            or "state" in lowered
            or "transition" in lowered
            or "<" in stripped
            or ">" in stripped
        ):
            cues.append(stripped)
        if len(cues) >= limit:
            break
    return cues


def _audit_text_file(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    payload: dict[str, Any] = {
        "name": path.name,
        "kind": "text",
        "size_bytes": len(raw),
        "sha256": _sha256(raw),
        "line_count": len(text.splitlines()),
        "schema_cues": _text_cues(text),
        "normalization_status": "evidence-only",
        "normalized_points": 0,
        "normalized_by_type": {},
    }

    if path.name in {"code-details.txt", "multibit-expression.txt"}:
        points = parse_questa_code_coverage_report(text)
        if path.name == "multibit-expression.txt":
            points = [
                point
                for point in points
                if point.get("type") == "expression"
                and point.get("multibit") is True
            ]

        by_type = Counter(str(point.get("type") or "unknown") for point in points)
        payload["normalized_points"] = len(points)
        payload["normalized_by_type"] = dict(sorted(by_type.items()))
        payload["normalization_status"] = (
            "verified-parser-available" if points else "captured-no-normalized-points"
        )

    return payload


def _audit_xml_file(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    payload: dict[str, Any] = {
        "name": path.name,
        "kind": "xml",
        "size_bytes": len(raw),
        "sha256": _sha256(raw),
        "normalization_status": "evidence-only",
        "normalized_points": 0,
        "normalized_by_type": {},
        "xml_tags": {},
        "pending_toggle_tags": {},
    }

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        payload["normalization_status"] = "malformed-xml"
        payload["parse_error"] = str(exc)
        return payload

    tag_counts = Counter(_local_name(element.tag) for element in root.iter())
    payload["xml_tags"] = dict(sorted(tag_counts.items()))

    if path.name == "toggle-details.xml":
        points = parse_questa_toggle_coverage_xml(path)
        payload["normalized_points"] = len(points)
        payload["normalized_by_type"] = {"toggle": len(points)} if points else {}
        payload["normalization_status"] = (
            "verified-parser-available" if points else "captured-no-normalized-points"
        )
        pending = {
            name: count
            for name, count in tag_counts.items()
            if name.casefold() not in {
                "coverage_report",
                "code_coverage_report",
                "instancedata",
                "toggleSummary".casefold(),
                "tog",
                "toge",
            }
            and "tog" in name.casefold()
        }
        payload["pending_toggle_tags"] = dict(sorted(pending.items()))

    return payload


def audit_questa_coverage_evidence(source_dir: str | Path) -> dict[str, Any]:
    """Inventory captured Questa coverage evidence without guessing pending schemas."""

    directory = Path(source_dir).resolve()
    if not directory.is_dir():
        raise FileNotFoundError(f"Questa coverage directory not found: {directory}")

    files: list[dict[str, Any]] = []
    for name in (*_TEXT_EVIDENCE, *_XML_EVIDENCE):
        path = directory / name
        if not path.is_file():
            continue
        if name.endswith(".xml"):
            files.append(_audit_xml_file(path))
        else:
            files.append(_audit_text_file(path))

    normalized_points = sum(int(item.get("normalized_points", 0)) for item in files)
    parser_supported = sum(
        item.get("normalization_status") == "verified-parser-available"
        for item in files
    )
    evidence_only = sum(
        item.get("normalization_status") == "evidence-only"
        for item in files
    )
    pending_toggle_tags: Counter[str] = Counter()
    for item in files:
        pending_toggle_tags.update(item.get("pending_toggle_tags") or {})

    limitations: list[str] = [
        "This audit fingerprints captured native Questa evidence and reports only "
        "already-supported parser results; it does not infer multibit-condition "
        "or enumerated/unknown toggle schemas."
    ]
    if not files:
        limitations.append(
            "No recognized Questa evidence files were found; run 'zddv coverage' "
            "or point --input at a populated coverage evidence directory."
        )

    return {
        "schema_version": 1,
        "analysis": "questa_coverage_schema_audit",
        "semantics": (
            "A verified-parser label means an existing ZDDV parser produced "
            "normalized points from the exact captured bytes. Evidence-only "
            "artifacts are retained without inventing item-level semantics."
        ),
        "source_dir": str(directory),
        "files": files,
        "summary": {
            "files_present": len(files),
            "parser_supported_files": parser_supported,
            "evidence_only_files": evidence_only,
            "normalized_points": normalized_points,
            "pending_schema_targets": list(_PENDING_SCHEMAS),
            "pending_toggle_tags": dict(sorted(pending_toggle_tags.items())),
        },
        "limitations": limitations,
    }


def write_questa_coverage_evidence_audit(
    source_dir: str | Path,
    output: str | Path,
) -> dict[str, Any]:
    report = audit_questa_coverage_evidence(source_dir)
    destination = Path(output).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**report, "path": str(destination)}
