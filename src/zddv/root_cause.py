from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zddv.config import ProjectConfig
from zddv.connectivity import build_connectivity_index
from zddv.crossprobe import build_crossprobe
from zddv.debug import correlate_assertions
from zddv.design_index import build_design_index
from zddv.storage import get_run_record
from zddv.triage import signature_for_record


def _candidate(
    grouped: dict[tuple[Any, ...], dict[str, Any]],
    *,
    key: tuple[Any, ...],
    kind: str,
    subject: str,
    basis: list[tuple[str, int]],
    evidence: dict[str, Any],
) -> None:
    item = grouped.setdefault(
        key,
        {
            "kind": kind,
            "subject": subject,
            "_basis": {},
            "evidence": [],
        },
    )
    for reason, points in basis:
        current = int(item["_basis"].get(reason, 0))
        if points > current:
            item["_basis"][reason] = int(points)
    item["evidence"].append(evidence)


def _finalize_candidates(
    grouped: dict[tuple[Any, ...], dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in grouped.values():
        score_basis = [
            {"reason": reason, "points": points}
            for reason, points in item["_basis"].items()
        ]
        score_basis.sort(key=lambda entry: (-entry["points"], entry["reason"]))
        score = min(100, sum(entry["points"] for entry in score_basis))
        result.append(
            {
                "kind": item["kind"],
                "subject": item["subject"],
                "evidence_score": score,
                "score_basis": score_basis,
                "evidence": item["evidence"],
            }
        )

    result.sort(
        key=lambda item: (
            -item["evidence_score"],
            item["kind"],
            item["subject"],
        )
    )
    for rank, item in enumerate(result, start=1):
        item["rank"] = rank
    return result


def rank_root_cause_candidates(
    project: ProjectConfig,
    *,
    run_id: str,
    event_limit: int = 100,
    signal_limit: int = 20,
) -> dict[str, Any]:
    """Rank debug candidates using only explicit run/assertion/waveform/RTL evidence."""

    if event_limit < 1:
        raise ValueError("event_limit must be >= 1")
    if signal_limit < 1:
        raise ValueError("signal_limit must be >= 1")

    run = get_run_record(project, run_id)
    if run is None:
        raise RuntimeError(f"Run '{run_id}' was not found.")
    if run["status"] == "PASS":
        raise RuntimeError(
            f"Run '{run_id}' is PASS; there is no failure evidence to rank."
        )

    correlation = correlate_assertions(
        project,
        run_id=run_id,
        status="FAIL",
        limit=event_limit,
        signal_hint_limit=signal_limit,
    )
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    limitations: list[str] = []

    if run["status"] == "TIMEOUT":
        basis = [("run_status_timeout", 70)]
        if run.get("timeout_s") is not None:
            basis.append(("explicit_timeout_limit", 10))
        _candidate(
            grouped,
            key=("runtime_timeout",),
            kind="runtime_timeout",
            subject="simulation timeout",
            basis=basis,
            evidence={
                "run_id": run_id,
                "status": run["status"],
                "timeout_s": run.get("timeout_s"),
                "log_path": run.get("log_path"),
            },
        )

    waveform_indexes: dict[str, dict[str, Any]] = {}
    design_index: dict[str, Any] | None = None
    connectivity_index: dict[str, Any] | None = None

    for event in correlation["events"]:
        waveform = event.get("waveform")
        signal_hints = [] if waveform is None else list(waveform.get("signal_hints", []))

        anchor_basis = [("failing_assertion", 15)]
        if event.get("log_line") is not None:
            anchor_basis.append(("assertion_log_line", 5))
        if waveform is not None and waveform.get("parse_status") == "indexed":
            anchor_basis.append(("indexed_waveform", 10))
        if signal_hints:
            anchor_basis.append(("assertion_signal_hint", 5))
        _candidate(
            grouped,
            key=("assertion_anchor", event["assertion_name"]),
            kind="assertion_anchor",
            subject=str(event["assertion_name"]),
            basis=anchor_basis,
            evidence={
                "run_id": run_id,
                "event_index": event["event_index"],
                "assertion_name": event["assertion_name"],
                "message": event.get("message"),
                "log_path": event.get("log_path"),
                "log_line": event.get("log_line"),
                "waveform_artifact": (
                    None if waveform is None else waveform.get("artifact")
                ),
                "signal_hints": signal_hints,
            },
        )

        if (
            waveform is None
            or waveform.get("parse_status") != "indexed"
            or not signal_hints
        ):
            continue

        index_path = str(waveform.get("index") or "")
        if not index_path:
            limitations.append(
                f"Assertion {event['assertion_name']} has indexed waveform metadata "
                "without an index path."
            )
            continue

        if index_path not in waveform_indexes:
            try:
                waveform_indexes[index_path] = json.loads(
                    Path(index_path).read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError) as exc:
                limitations.append(
                    f"Could not load waveform index for assertion "
                    f"{event['assertion_name']}: {exc}"
                )
                continue
        waveform_index = waveform_indexes[index_path]

        if design_index is None or connectivity_index is None:
            try:
                design_index = build_design_index(project)
                connectivity_index = build_connectivity_index(project)
            except (OSError, RuntimeError, ValueError) as exc:
                limitations.append(f"RTL cross-probe index unavailable: {exc}")
                design_index = None
                connectivity_index = None
                continue

        for hint in signal_hints:
            signal_path = str(hint["path"])
            try:
                crossprobe = build_crossprobe(
                    project,
                    signal_path,
                    waveform_index,
                    design_index=design_index,
                    connectivity_index=connectivity_index,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                limitations.append(
                    f"Cross-probe unavailable for {signal_path}: {exc}"
                )
                continue

            source = crossprobe.get("source") or {}
            declaration = source.get("declaration")
            connectivity = crossprobe.get("connectivity") or {}
            drivers = list(connectivity.get("drivers") or [])
            hint_reason = (
                "exact_waveform_path_hint"
                if hint.get("match") == "exact-path"
                else "exact_waveform_name_hint"
            )
            hint_points = 25 if hint.get("match") == "exact-path" else 20
            common_basis = [
                ("failing_assertion", 15),
                (hint_reason, hint_points),
            ]
            if event.get("log_line") is not None:
                common_basis.append(("assertion_log_line", 5))
            if declaration is not None:
                common_basis.append(("rtl_declaration_match", 20))

            if drivers:
                for driver in drivers:
                    file_value = str(driver.get("file") or source.get("file") or "")
                    line_value = driver.get("line")
                    detail = str(driver.get("detail") or driver.get("kind") or "driver")
                    subject = (
                        f"{file_value}:{line_value} {detail}"
                        if file_value and line_value is not None
                        else f"{signal_path} driver"
                    )
                    _candidate(
                        grouped,
                        key=(
                            "rtl_driver",
                            file_value,
                            line_value,
                            str(driver.get("kind") or ""),
                            detail,
                        ),
                        kind="rtl_driver",
                        subject=subject,
                        basis=[*common_basis, ("explicit_driver_edge", 35)],
                        evidence={
                            "run_id": run_id,
                            "event_index": event["event_index"],
                            "assertion_name": event["assertion_name"],
                            "message": event.get("message"),
                            "log_path": event.get("log_path"),
                            "log_line": event.get("log_line"),
                            "signal": signal_path,
                            "signal_hint_match": hint.get("match"),
                            "waveform_artifact": waveform.get("artifact"),
                            "source_declaration": declaration,
                            "driver": driver,
                        },
                    )
                continue

            if declaration is not None:
                file_value = str(declaration.get("file") or source.get("file") or "")
                line_value = declaration.get("line")
                subject = (
                    f"{file_value}:{line_value} {signal_path}"
                    if file_value and line_value is not None
                    else signal_path
                )
                _candidate(
                    grouped,
                    key=("rtl_declaration", file_value, line_value, signal_path),
                    kind="rtl_declaration",
                    subject=subject,
                    basis=common_basis,
                    evidence={
                        "run_id": run_id,
                        "event_index": event["event_index"],
                        "assertion_name": event["assertion_name"],
                        "message": event.get("message"),
                        "log_path": event.get("log_path"),
                        "log_line": event.get("log_line"),
                        "signal": signal_path,
                        "signal_hint_match": hint.get("match"),
                        "waveform_artifact": waveform.get("artifact"),
                        "source_declaration": declaration,
                    },
                )

    failure_signature = signature_for_record(run)
    if not correlation["events"]:
        _candidate(
            grouped,
            key=("failure_signature", failure_signature),
            kind="failure_signature",
            subject=failure_signature,
            basis=[("normalized_failure_log_signature", 20)],
            evidence={
                "run_id": run_id,
                "status": run["status"],
                "log_path": run.get("log_path"),
                "signature": failure_signature,
            },
        )

    candidates = _finalize_candidates(grouped)
    unique_limitations = list(dict.fromkeys(limitations))
    return {
        "schema_version": 1,
        "analysis": "root_cause_candidates",
        "project": project.name,
        "run": {
            "run_id": run["run_id"],
            "created_at": run["created_at"],
            "status": run["status"],
            "test_name": run.get("test_name"),
            "seed": run.get("seed"),
            "simulator": run["simulator"],
            "top": run["top"],
            "log_path": run["log_path"],
            "waveform_path": run.get("waveform_path"),
        },
        "failure_signature": failure_signature,
        "semantics": (
            "Candidates are ranked by explicit evidence richness, not by causal "
            "probability. A high score is not proof that a candidate caused the failure."
        ),
        "score_model": {
            "run_status_timeout": 70,
            "explicit_timeout_limit": 10,
            "explicit_driver_edge": 35,
            "exact_waveform_path_hint": 25,
            "exact_waveform_name_hint": 20,
            "rtl_declaration_match": 20,
            "normalized_failure_log_signature": 20,
            "failing_assertion": 15,
            "indexed_waveform": 10,
            "assertion_log_line": 5,
            "assertion_signal_hint": 5,
        },
        "candidates": candidates,
        "summary": {
            "candidates": len(candidates),
            "rtl_driver_candidates": sum(
                1 for item in candidates if item["kind"] == "rtl_driver"
            ),
            "assertion_events": correlation["summary"]["events"],
            "assertions_with_waveform": correlation["summary"]["with_waveform"],
            "assertions_with_signal_hints": correlation["summary"]["with_signal_hints"],
        },
        "limitations": unique_limitations,
    }


def write_root_cause_report(
    project: ProjectConfig,
    *,
    run_id: str,
    event_limit: int = 100,
    signal_limit: int = 20,
    output: str | Path = ".zddv/debug/root-cause.json",
) -> dict[str, Any]:
    report = rank_root_cause_candidates(
        project,
        run_id=run_id,
        event_limit=event_limit,
        signal_limit=signal_limit,
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
    return {**report, "path": str(destination)}
