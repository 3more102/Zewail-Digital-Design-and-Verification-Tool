from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zddv.config import ProjectConfig
from zddv.root_cause import rank_root_cause_candidates


def _probeable_evidence(
    candidate: dict[str, Any],
) -> list[tuple[str, Any, str | None, dict[str, Any]]]:
    """Return explicit signal evidence without requiring RTL cross-probe success."""

    result: list[tuple[str, Any, str | None, dict[str, Any]]] = []
    for evidence in candidate.get("evidence", []):
        waveform_artifact = evidence.get("waveform_artifact")
        signal = evidence.get("signal")
        if isinstance(signal, str) and signal and waveform_artifact:
            result.append(
                (
                    signal,
                    waveform_artifact,
                    evidence.get("signal_hint_match"),
                    evidence,
                )
            )

        for hint in evidence.get("signal_hints") or []:
            if not isinstance(hint, dict):
                continue
            path = hint.get("path")
            if not isinstance(path, str) or not path or not waveform_artifact:
                continue
            result.append((path, waveform_artifact, hint.get("match"), evidence))

    return result


def suggest_debug_probes(
    project: ProjectConfig,
    *,
    run_id: str,
    candidate_limit: int = 10,
    event_limit: int = 100,
    signal_limit: int = 20,
) -> dict[str, Any]:
    """Suggest only debug probes justified by explicit ranked failure evidence."""

    if candidate_limit < 1:
        raise ValueError("candidate_limit must be >= 1")

    ranking = rank_root_cause_candidates(
        project,
        run_id=run_id,
        event_limit=event_limit,
        signal_limit=signal_limit,
    )

    suggestions: list[dict[str, Any]] = []
    seen_signals: set[str] = set()
    evidence_candidates = ranking["candidates"][:candidate_limit]

    for candidate in evidence_candidates:
        for signal, waveform_artifact, hint_match, evidence in _probeable_evidence(
            candidate
        ):
            if signal in seen_signals:
                continue

            seen_signals.add(signal)
            suggestions.append(
                {
                    "kind": "waveform_probe",
                    "signal": signal,
                    "run_id": run_id,
                    "source_candidate_rank": candidate["rank"],
                    "source_candidate_kind": candidate["kind"],
                    "source_evidence_score": candidate["evidence_score"],
                    "reason": (
                        "Signal is explicitly linked to failure evidence and has a "
                        "recorded waveform artifact; inspect its recorded value changes."
                    ),
                    "argv": [
                        "waveform-probe",
                        signal,
                        "--run",
                        run_id,
                    ],
                    "evidence": {
                        "assertion_name": evidence.get("assertion_name"),
                        "event_index": evidence.get("event_index"),
                        "log_path": evidence.get("log_path"),
                        "log_line": evidence.get("log_line"),
                        "waveform_artifact": waveform_artifact,
                        "signal_hint_match": hint_match,
                        "driver": evidence.get("driver"),
                        "source_declaration": evidence.get("source_declaration"),
                    },
                }
            )

    suggestions.sort(
        key=lambda item: (
            -int(item["source_evidence_score"]),
            int(item["source_candidate_rank"]),
            item["signal"],
        )
    )
    for rank, item in enumerate(suggestions, start=1):
        item["rank"] = rank

    blockers: list[dict[str, Any]] = []
    if not suggestions:
        if ranking["run"].get("waveform_path") is None:
            blockers.append(
                {
                    "code": "NO_WAVEFORM_ARTIFACT",
                    "message": (
                        "The run has no recorded waveform artifact, so ZDDV will not "
                        "invent signal probes."
                    ),
                }
            )
        if ranking["summary"]["assertions_with_signal_hints"] == 0:
            blockers.append(
                {
                    "code": "NO_EXPLICIT_SIGNAL_HINTS",
                    "message": (
                        "No failing assertion supplied an exact indexed-waveform signal "
                        "name/path; ZDDV will not guess probe signals."
                    ),
                }
            )
        if not blockers:
            blockers.append(
                {
                    "code": "NO_PROBEABLE_SIGNAL_EVIDENCE",
                    "message": (
                        "The inspected ranked candidates contain no explicit signal plus "
                        "recorded waveform evidence; ZDDV will not invent a probe."
                    ),
                }
            )

    return {
        "schema_version": 1,
        "analysis": "debug_probe_suggestions",
        "project": project.name,
        "run": ranking["run"],
        "semantics": (
            "Suggestions are generated only from explicit ranked failure evidence. "
            "ZDDV does not invent signals, time windows, or causal relationships."
        ),
        "source_ranking": {
            "failure_signature": ranking["failure_signature"],
            "candidate_count": ranking["summary"]["candidates"],
            "candidate_limit": candidate_limit,
            "limitations": ranking["limitations"],
        },
        "suggestions": suggestions,
        "blockers": blockers,
        "summary": {
            "suggestions": len(suggestions),
            "unique_signals": len(seen_signals),
            "candidates_considered": len(evidence_candidates),
            "blockers": len(blockers),
        },
    }


def write_debug_probe_suggestions(
    project: ProjectConfig,
    *,
    run_id: str,
    candidate_limit: int = 10,
    event_limit: int = 100,
    signal_limit: int = 20,
    output: str | Path = ".zddv/debug/probe-suggestions.json",
) -> dict[str, Any]:
    report = suggest_debug_probes(
        project,
        run_id=run_id,
        candidate_limit=candidate_limit,
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
