from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any


_SECTION_HEADER = re.compile(
    r"^\s*Coverage\s+Report:\s*(?P<title>.+?)\s*$",
    re.IGNORECASE,
)
_FIELD_CUE = re.compile(
    r"^\s*(?:Instance name|Module/Entity name|Type name|File name)\s*:",
    re.IGNORECASE,
)
_COUNT_CUE = re.compile(r"^\s*Number of\b", re.IGNORECASE)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _metric_from_title(title: str) -> tuple[str, str]:
    normalized = re.sub(r"[^a-z0-9]+", " ", title.casefold()).strip()
    words = set(normalized.split())

    if "block" in words:
        return "block", "verified-parser-available"
    if "expression" in words:
        return "expression", "verified-parser-available"
    if "toggle" in words:
        return "toggle", "verified-parser-available"
    if "fsm" in words or {"finite", "state", "machine"}.issubset(words):
        return "fsm", "schema-unverified"
    if (
        "functional" in words
        or "covergroup" in words
        or "coverpoint" in words
        or "cross" in words
    ):
        return "functional", "schema-unverified"
    return "unknown", "schema-unverified"


def _schema_cues(section_lines: list[str], *, limit: int = 20) -> list[str]:
    cues: list[str] = []
    for raw_line in section_lines:
        stripped = raw_line.strip()
        if not stripped:
            continue
        if (
            _SECTION_HEADER.match(raw_line)
            or _FIELD_CUE.match(raw_line)
            or _COUNT_CUE.match(raw_line)
            or "|" in stripped
            or stripped.casefold().startswith(("count ", "hit(", "state ", "transition "))
        ):
            cues.append(stripped)
        if len(cues) >= limit:
            break
    return cues


def audit_xcelium_imc_detail(text: str) -> dict[str, Any]:
    """Inventory native IMC detail sections without inferring unknown row schemas."""

    lines = text.splitlines(keepends=True)
    starts: list[tuple[int, str]] = []
    for index, raw_line in enumerate(lines):
        match = _SECTION_HEADER.match(raw_line.rstrip("\r\n"))
        if match is not None:
            starts.append((index, match.group("title").strip()))

    sections: list[dict[str, Any]] = []
    for ordinal, (start, title) in enumerate(starts, start=1):
        end = starts[ordinal][0] if ordinal < len(starts) else len(lines)
        section_lines = lines[start:end]
        section_text = "".join(section_lines)
        metric, normalization_status = _metric_from_title(title)
        sections.append(
            {
                "ordinal": ordinal,
                "title": title,
                "metric": metric,
                "normalization_status": normalization_status,
                "start_line": start + 1,
                "end_line": end,
                "line_count": end - start,
                "section_sha256": _sha256(section_text.encode("utf-8")),
                "schema_cues": _schema_cues(section_lines),
            }
        )

    verified = sum(
        1
        for section in sections
        if section["normalization_status"] == "verified-parser-available"
    )
    unverified = len(sections) - verified
    limitations: list[str] = []
    if not sections:
        limitations.append(
            "No 'Coverage Report:' section headers were recognized; "
            "the source remains evidence-only."
        )
    elif unverified:
        limitations.append(
            "FSM, functional, and unknown IMC detail layouts are intentionally "
            "not normalized until exact native row schemas are verified."
        )

    first_section_line = starts[0][0] + 1 if starts else None
    preamble_lines = starts[0][0] if starts else len(lines)
    return {
        "schema_version": 1,
        "analysis": "xcelium_imc_detail_schema_audit",
        "semantics": (
            "This report inventories captured native IMC sections and preserves "
            "their decoded-text fingerprints. A verified-parser label only means "
            "ZDDV already has a parser for that section family; unverified sections "
            "remain evidence-only and are not interpreted."
        ),
        "line_count": len(lines),
        "first_section_line": first_section_line,
        "preamble_line_count": preamble_lines,
        "sections": sections,
        "summary": {
            "sections": len(sections),
            "verified_sections": verified,
            "unverified_sections": unverified,
            "metrics": sorted({str(section["metric"]) for section in sections}),
        },
        "limitations": limitations,
    }


def write_xcelium_imc_detail_audit(
    source: str | Path,
    output: str | Path,
) -> dict[str, Any]:
    source_path = Path(source).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(
            f"Xcelium IMC detail report not found: {source_path}"
        )

    raw = source_path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    report = audit_xcelium_imc_detail(text)
    payload = {
        **report,
        "source_path": str(source_path),
        "source_sha256": _sha256(raw),
    }

    destination = Path(output).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**payload, "path": str(destination)}
