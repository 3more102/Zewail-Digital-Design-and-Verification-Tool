from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import re
from typing import Any

from zddv.config import ProjectConfig
from zddv.debug import correlate_assertions
from zddv.storage import list_run_records


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


def _candidate_sort_key(item: dict[str, Any]) -> tuple[int, int, int, str]:
    kind_priority = 0 if item["kind"] == "assertion" else 1
    return (
        -int(item["supporting_runs"]),
        -int(item["event_count"]),
        kind_priority,
        str(item["name"]),
    )


def build_failure_triage_report(
    project: ProjectConfig,
    *,
    limit: int = 200,
    candidate_limit: int = 20,
    signal_hint_limit: int = 20,
) -> dict[str, Any]:
    """Correlate failure groups with directly observed assertion/waveform evidence."""
    if limit < 1:
        raise ValueError("limit must be >= 1")
    if candidate_limit < 1:
        raise ValueError("candidate_limit must be >= 1")
    if signal_hint_limit < 1:
        raise ValueError("signal_hint_limit must be >= 1")

    records = list_run_records(
        project,
        limit=limit,
        statuses=("FAIL", "TIMEOUT"),
    )
    groups = group_failure_records(records)
    record_by_run = {str(record["run_id"]): record for record in records}

    triaged_groups: list[dict[str, Any]] = []
    total_candidates = 0
    total_probes = 0

    for group in groups:
        assertion_evidence: dict[str, dict[str, Any]] = {}
        signal_evidence: dict[str, dict[str, Any]] = {}
        correlated_events = 0
        events_with_waveform = 0

        for run_id in group["run_ids"]:
            correlation = correlate_assertions(
                project,
                run_id=run_id,
                status="FAIL",
                limit=100,
                signal_hint_limit=signal_hint_limit,
            )
            for event in correlation["events"]:
                correlated_events += 1
                assertion_name = str(event["assertion_name"])
                assertion = assertion_evidence.setdefault(
                    assertion_name,
                    {
                        "run_ids": set(),
                        "event_count": 0,
                        "messages": set(),
                    },
                )
                assertion["run_ids"].add(run_id)
                assertion["event_count"] += 1
                message = str(event.get("message") or "").strip()
                if message:
                    assertion["messages"].add(message)

                waveform = event.get("waveform")
                if waveform is None:
                    continue
                events_with_waveform += 1
                for hint in waveform.get("signal_hints", []):
                    path = str(hint["path"])
                    signal = signal_evidence.setdefault(
                        path,
                        {
                            "run_ids": set(),
                            "event_count": 0,
                            "matches": set(),
                        },
                    )
                    signal["run_ids"].add(run_id)
                    signal["event_count"] += 1
                    signal["matches"].add(str(hint.get("match") or "unknown"))

        candidates: list[dict[str, Any]] = []
        for name, evidence in assertion_evidence.items():
            candidates.append(
                {
                    "kind": "assertion",
                    "name": name,
                    "supporting_runs": len(evidence["run_ids"]),
                    "event_count": int(evidence["event_count"]),
                    "run_ids": sorted(evidence["run_ids"]),
                    "example_messages": sorted(evidence["messages"])[:3],
                    "basis": "failing assertion observed in runs in this failure group",
                }
            )
        for path, evidence in signal_evidence.items():
            candidates.append(
                {
                    "kind": "signal",
                    "name": path,
                    "supporting_runs": len(evidence["run_ids"]),
                    "event_count": int(evidence["event_count"]),
                    "run_ids": sorted(evidence["run_ids"]),
                    "matches": sorted(evidence["matches"]),
                    "basis": "waveform signal matched failing assertion text",
                }
            )

        candidates.sort(key=_candidate_sort_key)
        candidates = candidates[:candidate_limit]
        for rank, candidate in enumerate(candidates, start=1):
            candidate["rank"] = rank

        probes: list[dict[str, Any]] = []
        signal_candidates = [
            candidate for candidate in candidates if candidate["kind"] == "signal"
        ]
        for candidate in signal_candidates:
            supporting = [
                record_by_run[run_id]
                for run_id in candidate["run_ids"]
                if run_id in record_by_run
            ]
            if not supporting:
                continue
            selected = max(
                supporting,
                key=lambda record: (
                    str(record.get("created_at") or ""),
                    str(record.get("run_id") or ""),
                ),
            )
            run_id = str(selected["run_id"])
            signal = str(candidate["name"])
            probes.append(
                {
                    "action": "waveform-probe",
                    "run_id": run_id,
                    "signal": signal,
                    "selection": "latest supporting failed run",
                    "reason": (
                        f"{signal} matched failing assertion text in "
                        f"{candidate['supporting_runs']} run(s)"
                    ),
                    "command": [
                        "zddv",
                        "--project",
                        str(project.root),
                        "waveform-probe",
                        signal,
                        "--run",
                        run_id,
                    ],
                }
            )

        total_candidates += len(candidates)
        total_probes += len(probes)
        triaged_groups.append(
            {
                **group,
                "evidence_summary": {
                    "failed_runs": group["count"],
                    "correlated_assertion_events": correlated_events,
                    "assertion_names": len(assertion_evidence),
                    "waveform_signal_hints": len(signal_evidence),
                    "events_with_waveform": events_with_waveform,
                },
                "candidates": candidates,
                "suggested_probes": probes,
            }
        )

    return {
        "schema_version": 1,
        "project": project.name,
        "ranking_semantics": (
            "Candidates are ranked only by repeated direct evidence "
            "(supporting failed runs, then event count). Rank is not a claim of causality."
        ),
        "filters": {
            "limit": limit,
            "candidate_limit": candidate_limit,
            "signal_hint_limit": signal_hint_limit,
        },
        "summary": {
            "failed_runs": len(records),
            "failure_groups": len(triaged_groups),
            "candidates": total_candidates,
            "suggested_probes": total_probes,
        },
        "groups": triaged_groups,
    }


def write_failure_triage_report(
    project: ProjectConfig,
    *,
    limit: int = 200,
    candidate_limit: int = 20,
    signal_hint_limit: int = 20,
    output: str | Path = ".zddv/debug/failure-triage.json",
) -> dict[str, Any]:
    report = build_failure_triage_report(
        project,
        limit=limit,
        candidate_limit=candidate_limit,
        signal_hint_limit=signal_hint_limit,
    )
    path = Path(output)
    if not path.is_absolute():
        path = project.root / path
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return {**report, "path": str(path)}
