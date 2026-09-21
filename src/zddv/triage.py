from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import re
from typing import Any


_INTERESTING = re.compile(
    r"(assert|error|fatal|fail|mismatch|timeout|violation|unexpected)",
    re.IGNORECASE,
)
_PATH = re.compile(r"(?:(?:[A-Za-z]:[\\/])|/)[^\s:]+")
_HEX = re.compile(r"\b0x[0-9A-Fa-f]+\b")
_NUMBER = re.compile(r"(?<![A-Za-z_])[-+]?\d+(?:\.\d+)?(?![A-Za-z_])")
_SPACE = re.compile(r"\s+")


def _normalize_line(line: str) -> str:
    line = _PATH.sub("<path>", line)
    line = _HEX.sub("0x#", line)
    line = _NUMBER.sub("#", line)
    line = _SPACE.sub(" ", line).strip()
    return line[:240]


def signature_from_text(text: str, *, status: str) -> str:
    if status == "TIMEOUT":
        return "TIMEOUT"

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    interesting = [line for line in lines if _INTERESTING.search(line)]

    selected = interesting[:3]
    if not selected and lines:
        selected = lines[-1:]

    normalized = [_normalize_line(line) for line in selected if _normalize_line(line)]
    if not normalized:
        return status or "UNKNOWN"

    return " | ".join(normalized)


def signature_for_record(record: dict[str, Any]) -> str:
    log_path = Path(str(record.get("log_path") or ""))
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    return signature_from_text(text, status=str(record.get("status") or "UNKNOWN"))


def group_failure_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}

    for record in records:
        status = str(record.get("status") or "")
        if status == "PASS":
            continue

        signature = signature_for_record(record)
        group = grouped.setdefault(
            signature,
            {
                "signature": signature,
                "count": 0,
                "statuses": set(),
                "tests": set(),
                "seeds": set(),
                "run_ids": [],
                "latest_created_at": "",
            },
        )

        group["count"] += 1
        group["statuses"].add(status)
        if record.get("test_name"):
            group["tests"].add(str(record["test_name"]))
        if record.get("seed") is not None:
            group["seeds"].add(int(record["seed"]))
        group["run_ids"].append(str(record["run_id"]))

        created_at = str(record.get("created_at") or "")
        if created_at > group["latest_created_at"]:
            group["latest_created_at"] = created_at

    result: list[dict[str, Any]] = []
    for group in grouped.values():
        result.append(
            {
                **group,
                "statuses": sorted(group["statuses"]),
                "tests": sorted(group["tests"]),
                "seeds": sorted(group["seeds"]),
            }
        )

    result.sort(key=lambda item: (-item["count"], item["signature"]))
    return result


def write_failure_report(
    groups: list[dict[str, Any]],
    output: str | Path,
) -> Path:
    path = Path(output).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "groups": groups,
        "total_groups": len(groups),
        "total_runs": sum(group["count"] for group in groups),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
