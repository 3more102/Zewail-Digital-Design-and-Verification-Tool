from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zddv.config import ProjectConfig
from zddv.connectivity import build_connectivity_index
from zddv.crossprobe import build_crossprobe
from zddv.debug import correlate_assertions
from zddv.design_index import build_design_index
from zddv.storage import (
    get_run_record,
    list_formal_property_results,
    list_formal_result_snapshots,
    list_run_records,
)
from zddv.triage import group_failure_records, signature_for_record
from zddv.waveform import write_waveform_index


def _failure_group_context(
    project: ProjectConfig,
    run: dict[str, Any],
    *,
    history_limit: int,
) -> dict[str, Any] | None:
    records = list_run_records(
        project,
        limit=history_limit,
        statuses=("FAIL", "TIMEOUT"),
    )
    if not any(row["run_id"] == run["run_id"] for row in records):
        records.append(run)

    for group in group_failure_records(records):
        if run["run_id"] in group["run_ids"]:
            return {
                "signature": group["signature"],
                "count": group["count"],
                "statuses": group["statuses"],
                "tests": group["tests"],
                "seeds": group["seeds"],
                "run_ids": group["run_ids"],
                "latest_created_at": group["latest_created_at"],
                "history_limit": history_limit,
            }
    return None


def _formal_name_context(
    project: ProjectConfig,
    assertion_names: set[str],
    *,
    snapshot_limit: int,
) -> dict[str, list[dict[str, Any]]]:
    if not assertion_names:
        return {}

    matches: dict[str, list[dict[str, Any]]] = {
        name: [] for name in sorted(assertion_names)
    }
    snapshots = list_formal_result_snapshots(project, limit=snapshot_limit)
    for snapshot in snapshots:
        properties = list_formal_property_results(
            project,
            snapshot["snapshot_id"],
        )
        for item in properties:
            name = str(item["name"])
            if name not in assertion_names:
                continue
            matches[name].append(
                {
                    "match": "exact-property-name",
                    "snapshot_id": snapshot["snapshot_id"],
                    "created_at": snapshot["created_at"],
                    "backend": snapshot["backend"],
                    "engine": snapshot.get("engine"),
                    "formal_status": snapshot["status"],
                    "mode": snapshot["mode"],
                    "proof_scope": snapshot["proof_scope"],
                    "request_depth": snapshot.get("request_depth"),
                    "property_status": item["status"],
                    "interpretation": item["interpretation"],
                    "depth": item.get("depth"),
                    "effective_depth": item.get("effective_depth"),
                    "trace_path": item.get("trace_path"),
                    "trace_role": item.get("trace_role"),
                }
            )

    return {name: rows for name, rows in matches.items() if rows}


def _candidate_rank_key(item: dict[str, Any]) -> tuple[Any, ...]:
    basis = item["rank_basis"]
    return (
        -int(basis["failed_assertion_events"]),
        -int(bool(basis["rtl_declaration_match"])),
        -int(basis["structural_driver_count"]),
        -int(basis["exact_path_hint_count"]),
        str(item["signal"]["path"]),
    )


def build_debug_triage(
    project: ProjectConfig,
    *,
    run_id: str,
    assertion_limit: int = 100,
    signal_hint_limit: int = 20,
    candidate_limit: int = 20,
    history_limit: int = 500,
    formal_snapshot_limit: int = 50,
) -> dict[str, Any]:
    """Build deterministic failure-localization candidates from retained evidence."""

    if assertion_limit < 1:
        raise ValueError("assertion_limit must be >= 1")
    if signal_hint_limit < 1:
        raise ValueError("signal_hint_limit must be >= 1")
    if candidate_limit < 1:
        raise ValueError("candidate_limit must be >= 1")
    if history_limit < 1:
        raise ValueError("history_limit must be >= 1")
    if formal_snapshot_limit < 1:
        raise ValueError("formal_snapshot_limit must be >= 1")

    run = get_run_record(project, run_id)
    if run is None:
        raise RuntimeError(f"Run '{run_id}' was not found in the verification database.")
    if run["status"] not in {"FAIL", "TIMEOUT"}:
        raise RuntimeError(
            f"Debug triage requires a FAIL or TIMEOUT run; '{run_id}' is {run['status']}."
        )

    assertion_report = correlate_assertions(
        project,
        run_id=run_id,
        status="FAIL",
        limit=assertion_limit,
        signal_hint_limit=signal_hint_limit,
    )
    events = [dict(item) for item in assertion_report["events"]]
    assertion_names = {
        str(item["assertion_name"])
        for item in events
        if item.get("assertion_name")
    }

    formal_context = _formal_name_context(
        project,
        assertion_names,
        snapshot_limit=formal_snapshot_limit,
    )
    for event in events:
        name = str(event.get("assertion_name") or "")
        event["formal_matches"] = formal_context.get(name, [])

    failure_group = _failure_group_context(
        project,
        run,
        history_limit=history_limit,
    )

    waveform_index: dict[str, Any] | None = None
    waveform_path = run.get("waveform_path")
    if waveform_path and Path(str(waveform_path)).is_file():
        waveform_index = write_waveform_index(
            project,
            run_id=run_id,
            output=project.root / ".zddv" / "waveforms" / f"{run_id}.json",
        )

    raw_candidates: dict[str, dict[str, Any]] = {}
    for event in events:
        waveform = event.get("waveform") or {}
        for hint in waveform.get("signal_hints", []):
            path = str(hint.get("path") or "")
            if not path:
                continue
            candidate = raw_candidates.setdefault(
                path,
                {
                    "signal": {
                        "path": path,
                        "name": hint.get("name"),
                        "width": hint.get("width"),
                        "range": hint.get("range"),
                    },
                    "event_refs": [],
                    "assertion_names": set(),
                    "hint_matches": [],
                    "crossprobe": None,
                },
            )
            event_ref = {
                "event_index": event["event_index"],
                "assertion_name": event["assertion_name"],
                "message": event.get("message"),
                "log_path": event.get("log_path"),
                "log_line": event.get("log_line"),
            }
            if event_ref not in candidate["event_refs"]:
                candidate["event_refs"].append(event_ref)
            candidate["assertion_names"].add(str(event["assertion_name"]))
            candidate["hint_matches"].append(str(hint.get("match") or "unknown"))

    if waveform_index is not None and raw_candidates:
        design_index = build_design_index(project)
        connectivity_index = build_connectivity_index(project)
        for path, candidate in raw_candidates.items():
            try:
                candidate["crossprobe"] = build_crossprobe(
                    project,
                    path,
                    waveform_index,
                    design_index=design_index,
                    connectivity_index=connectivity_index,
                )
            except (RuntimeError, ValueError) as exc:
                candidate["crossprobe"] = {
                    "status": "ERROR",
                    "note": str(exc),
                }

    candidates: list[dict[str, Any]] = []
    for candidate in raw_candidates.values():
        crossprobe = candidate["crossprobe"]
        source = crossprobe.get("source") if isinstance(crossprobe, dict) else None
        declaration = source.get("declaration") if isinstance(source, dict) else None
        connectivity = (
            crossprobe.get("connectivity")
            if isinstance(crossprobe, dict)
            else None
        )
        drivers = (
            connectivity.get("drivers", [])
            if isinstance(connectivity, dict)
            else []
        )
        loads = (
            connectivity.get("loads", [])
            if isinstance(connectivity, dict)
            else []
        )

        item = {
            "signal": candidate["signal"],
            "assertion_names": sorted(candidate["assertion_names"]),
            "assertion_events": sorted(
                candidate["event_refs"],
                key=lambda row: int(row["event_index"]),
            ),
            "hint_matches": sorted(set(candidate["hint_matches"])),
            "crossprobe": crossprobe,
            "rank_basis": {
                "failed_assertion_events": len(candidate["event_refs"]),
                "rtl_declaration_match": declaration is not None,
                "structural_driver_count": len(drivers),
                "structural_load_count": len(loads),
                "exact_path_hint_count": sum(
                    value == "exact-path"
                    for value in candidate["hint_matches"]
                ),
            },
        }
        candidates.append(item)

    candidates.sort(key=_candidate_rank_key)
    candidates = candidates[:candidate_limit]
    for index, candidate in enumerate(candidates, start=1):
        candidate["priority_rank"] = index

    formal_match_count = sum(
        len(event["formal_matches"])
        for event in events
    )
    source_match_count = sum(
        bool(
            isinstance(item.get("crossprobe"), dict)
            and isinstance(item["crossprobe"].get("source"), dict)
            and item["crossprobe"]["source"].get("declaration") is not None
        )
        for item in candidates
    )

    return {
        "schema_version": 1,
        "analysis": "debug_triage",
        "project": project.name,
        "run": {
            "run_id": run["run_id"],
            "created_at": run["created_at"],
            "status": run["status"],
            "test_name": run.get("test_name"),
            "seed": run.get("seed"),
            "simulator": run["simulator"],
            "simulator_version": run.get("simulator_version"),
            "top": run["top"],
            "returncode": run["returncode"],
            "duration_ms": run.get("duration_ms"),
            "run_dir": run["run_dir"],
            "log_path": run["log_path"],
            "waveform_path": run.get("waveform_path"),
        },
        "failure": {
            "signature": signature_for_record(run),
            "group": failure_group,
        },
        "assertions": {
            "events": events,
            "summary": assertion_report["summary"],
        },
        "candidates": candidates,
        "formal_context": {
            "match_policy": "exact-property-name-only",
            "snapshot_limit": formal_snapshot_limit,
            "matches_by_assertion": formal_context,
        },
        "summary": {
            "failed_assertion_events": len(events),
            "signal_candidates": len(candidates),
            "source_matched_candidates": source_match_count,
            "formal_exact_name_matches": formal_match_count,
            "failure_group_occurrences": (
                int(failure_group["count"]) if failure_group is not None else 0
            ),
            "waveform_available": waveform_index is not None,
        },
        "ranking_policy": {
            "meaning": (
                "Deterministic localization priority from retained evidence; "
                "not a probability and not proof of root cause."
            ),
            "order": [
                "more failed assertion events referencing the signal",
                "matched RTL declaration",
                "more structural drivers",
                "more exact-path assertion hints",
                "stable signal path tie-break",
            ],
            "formal_history_affects_rank": False,
        },
        "limitations": [
            "A ranked signal is a debug candidate, not a proven causal root cause.",
            "Assertion signal hints are lexical matches against assertion names/messages.",
            "Connectivity is source-structural evidence and may differ from elaborated connectivity.",
            "Formal context is included only for exact property-name matches and is not automatically correlated to this simulation run.",
            "Missing waveforms or source mappings reduce available evidence but do not fabricate candidates.",
        ],
    }


def write_debug_triage_report(
    project: ProjectConfig,
    *,
    run_id: str,
    assertion_limit: int = 100,
    signal_hint_limit: int = 20,
    candidate_limit: int = 20,
    history_limit: int = 500,
    formal_snapshot_limit: int = 50,
    output: str | Path = ".zddv/debug/triage.json",
) -> dict[str, Any]:
    report = build_debug_triage(
        project,
        run_id=run_id,
        assertion_limit=assertion_limit,
        signal_hint_limit=signal_hint_limit,
        candidate_limit=candidate_limit,
        history_limit=history_limit,
        formal_snapshot_limit=formal_snapshot_limit,
    )

    destination = Path(output)
    if not destination.is_absolute():
        destination = project.root / destination
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**report, "report_path": str(destination)}
