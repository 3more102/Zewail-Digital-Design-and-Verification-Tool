from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zddv.config import ProjectConfig
from zddv.connectivity import build_connectivity_index
from zddv.root_cause import rank_root_cause_candidates
from zddv.storage import get_run_record
from zddv.waveform import write_waveform_index


def _display_command(project: ProjectConfig, parts: list[str]) -> list[str]:
    return ["zddv", "--project", str(project.root), *parts]


def _reproduction_command(
    project: ProjectConfig,
    run: dict[str, Any],
) -> list[str]:
    parts = ["run"]
    if run.get("test_name"):
        parts.extend(["--test", str(run["test_name"])])
    if run.get("seed") is not None:
        parts.extend(["--seed", str(run["seed"])])
    for plusarg in run.get("plusargs", []):
        parts.extend(["--plusarg", str(plusarg)])
    if run.get("timeout_s") is not None:
        parts.extend(["--timeout", str(run["timeout_s"])])
    return _display_command(project, parts)


def _resolve_waveform_dependency(
    waveform_index: dict[str, Any],
    *,
    target_path: str,
    signal_name: str,
) -> tuple[str | None, str | None]:
    signals = list(waveform_index.get("signals", []))
    scope = target_path.rsplit(".", 1)[0] if "." in target_path else ""
    scoped = f"{scope}.{signal_name}" if scope else signal_name

    exact = [
        str(item["path"])
        for item in signals
        if str(item.get("path") or "") == scoped
    ]
    if len(exact) == 1:
        return exact[0], "same-scope"

    by_name = [
        str(item["path"])
        for item in signals
        if str(item.get("name") or "") == signal_name
    ]
    if len(by_name) == 1:
        return by_name[0], "unique-name"
    return None, None


def _driver_fanin(
    connectivity: dict[str, Any],
    waveform_index: dict[str, Any],
    *,
    target_path: str,
    driver: dict[str, Any],
    max_signals: int,
) -> list[dict[str, Any]]:
    target_signal = str(driver.get("signal") or "")
    unit = str(driver.get("unit") or "")
    file_value = str(driver.get("file") or "")
    line = driver.get("line")

    dependencies: list[dict[str, Any]] = []
    seen: set[str] = {target_path}
    for item in connectivity.get("connections", []):
        if item.get("role") != "load":
            continue
        if str(item.get("unit") or "") != unit:
            continue
        if str(item.get("file") or "") != file_value:
            continue
        if item.get("line") != line:
            continue
        if str(item.get("destination") or "") != target_signal:
            continue

        signal_name = str(item.get("signal") or "")
        if not signal_name or signal_name == target_signal:
            continue
        path, match = _resolve_waveform_dependency(
            waveform_index,
            target_path=target_path,
            signal_name=signal_name,
        )
        if path is None or path in seen:
            continue
        seen.add(path)
        dependencies.append(
            {
                "signal": signal_name,
                "path": path,
                "waveform_match": match,
                "connection": item,
            }
        )
        if len(dependencies) >= max(0, max_signals - 1):
            break
    return dependencies


def _add_suggestion(
    suggestions: list[dict[str, Any]],
    seen: set[tuple[Any, ...]],
    *,
    key: tuple[Any, ...],
    kind: str,
    target: str,
    rationale: str,
    evidence: dict[str, Any],
    command: list[str] | None = None,
    signals: list[str] | None = None,
    expected_evidence: str,
    execution_mode: str,
    prerequisites: list[str] | None = None,
) -> None:
    if key in seen:
        return
    seen.add(key)
    suggestions.append(
        {
            "kind": kind,
            "target": target,
            "rationale": rationale,
            "evidence": evidence,
            "command": command,
            "signals": signals or [],
            "expected_evidence": expected_evidence,
            "execution_mode": execution_mode,
            "prerequisites": prerequisites or [],
        }
    )


def suggest_debug_probes(
    project: ProjectConfig,
    *,
    run_id: str,
    candidate_limit: int = 5,
    max_signals: int = 8,
    event_limit: int = 100,
    signal_limit: int = 20,
) -> dict[str, Any]:
    """Suggest reviewable next probes from explicit root-cause evidence."""

    if candidate_limit < 1:
        raise ValueError("candidate_limit must be >= 1")
    if max_signals < 1:
        raise ValueError("max_signals must be >= 1")
    if event_limit < 1:
        raise ValueError("event_limit must be >= 1")
    if signal_limit < 1:
        raise ValueError("signal_limit must be >= 1")

    run = get_run_record(project, run_id)
    if run is None:
        raise RuntimeError(f"Run '{run_id}' was not found.")
    if run["status"] == "PASS":
        raise RuntimeError(
            f"Run '{run_id}' is PASS; there is no failure evidence to probe."
        )

    root_cause = rank_root_cause_candidates(
        project,
        run_id=run_id,
        event_limit=event_limit,
        signal_limit=signal_limit,
    )

    waveform_index: dict[str, Any] | None = None
    waveform_path = run.get("waveform_path")
    if waveform_path and Path(str(waveform_path)).is_file():
        waveform_index = write_waveform_index(
            project,
            run_id=run_id,
            output=project.root / ".zddv" / "waveforms" / f"{run_id}.json",
        )

    connectivity: dict[str, Any] | None = None
    if waveform_index is not None and any(
        item["kind"] == "rtl_driver"
        for item in root_cause["candidates"][:candidate_limit]
    ):
        connectivity = build_connectivity_index(project)

    suggestions: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    for candidate in root_cause["candidates"][:candidate_limit]:
        kind = str(candidate["kind"])
        rank = int(candidate["rank"])
        score = int(candidate["evidence_score"])
        for evidence_index, evidence in enumerate(candidate.get("evidence", [])):
            signal = str(evidence.get("signal") or "")

            if (
                kind == "rtl_driver"
                and signal
                and waveform_index is not None
                and connectivity is not None
            ):
                driver = evidence.get("driver") or {}
                fanin = _driver_fanin(
                    connectivity,
                    waveform_index,
                    target_path=signal,
                    driver=driver,
                    max_signals=max_signals,
                )
                signals = [signal, *[item["path"] for item in fanin]]
                _add_suggestion(
                    suggestions,
                    seen,
                    key=("waveform_driver_cone", tuple(signals)),
                    kind="waveform_driver_cone",
                    target=signal,
                    rationale=(
                        "The root-cause ranking found an explicit RTL driver for a "
                        "signal named by a failing assertion. Probe that signal with "
                        "any same-assignment RHS dependencies that are also present "
                        "in the retained waveform."
                    ),
                    evidence={
                        "candidate_rank": rank,
                        "candidate_kind": kind,
                        "candidate_evidence_score": score,
                        "candidate_evidence_index": evidence_index,
                        "assertion_name": evidence.get("assertion_name"),
                        "driver": driver,
                        "fanin": fanin,
                    },
                    command=_display_command(
                        project,
                        [
                            "waveform-probe",
                            *signals,
                            "--run",
                            run_id,
                            "--max-changes",
                            "500",
                        ],
                    ),
                    signals=signals,
                    expected_evidence=(
                        "Timestamped value changes for the implicated driver output "
                        "and waveform-resolved immediate RHS dependencies."
                    ),
                    execution_mode="read_only_existing_artifact",
                )
                continue

            if kind == "rtl_declaration" and signal and waveform_index is not None:
                _add_suggestion(
                    suggestions,
                    seen,
                    key=("waveform_signal_history", signal),
                    kind="waveform_signal_history",
                    target=signal,
                    rationale=(
                        "The failing assertion maps to an RTL declaration but no "
                        "explicit source-level driver edge was retained. Inspect the "
                        "signal's actual value history before expanding the search."
                    ),
                    evidence={
                        "candidate_rank": rank,
                        "candidate_kind": kind,
                        "candidate_evidence_score": score,
                        "candidate_evidence_index": evidence_index,
                        "assertion_name": evidence.get("assertion_name"),
                        "source_declaration": evidence.get("source_declaration"),
                    },
                    command=_display_command(
                        project,
                        [
                            "waveform-probe",
                            signal,
                            "--run",
                            run_id,
                            "--max-changes",
                            "500",
                        ],
                    ),
                    signals=[signal],
                    expected_evidence="Timestamped value changes for the mapped RTL signal.",
                    execution_mode="read_only_existing_artifact",
                )
                continue

            if kind == "assertion_anchor":
                assertion_name = str(evidence.get("assertion_name") or candidate["subject"])
                hints = list(evidence.get("signal_hints") or [])
                hint_paths = [
                    str(item.get("path"))
                    for item in hints
                    if item.get("path")
                ]
                if hint_paths and waveform_index is not None:
                    selected = hint_paths[:max_signals]
                    _add_suggestion(
                        suggestions,
                        seen,
                        key=("assertion_waveform_focus", assertion_name, tuple(selected)),
                        kind="assertion_waveform_focus",
                        target=assertion_name,
                        rationale=(
                            "The failing assertion contains exact waveform signal hints. "
                            "Probe those retained signals together to inspect their dynamic relationship."
                        ),
                        evidence={
                            "candidate_rank": rank,
                            "candidate_evidence_score": score,
                            "candidate_evidence_index": evidence_index,
                            "log_path": evidence.get("log_path"),
                            "log_line": evidence.get("log_line"),
                            "signal_hints": hints,
                        },
                        command=_display_command(
                            project,
                            [
                                "waveform-probe",
                                *selected,
                                "--run",
                                run_id,
                                "--max-changes",
                                "500",
                            ],
                        ),
                        signals=selected,
                        expected_evidence=(
                            "Timestamped changes for every assertion-referenced signal "
                            "that was resolved in the waveform."
                        ),
                        execution_mode="read_only_existing_artifact",
                    )

    if waveform_index is None:
        prerequisites = []
        if not project.waveform:
            prerequisites.append("Set [run].waveform = true in zddv.toml before rerunning.")
        else:
            prerequisites.append(
                "Keep waveform capture enabled and verify the simulator produces the expected artifact."
            )
        _add_suggestion(
            suggestions,
            seen,
            key=("rerun_with_waveform_capture", run_id),
            kind="rerun_with_waveform_capture",
            target=run_id,
            rationale=(
                "The failed/timed-out run has no retained waveform. Reproduce the "
                "same ZDDV test/seed/plusargs/timeout with waveform capture before "
                "attempting signal-level localization."
            ),
            evidence={
                "run_id": run_id,
                "status": run["status"],
                "test_name": run.get("test_name"),
                "seed": run.get("seed"),
                "plusargs": run.get("plusargs", []),
                "timeout_s": run.get("timeout_s"),
                "waveform_path": run.get("waveform_path"),
            },
            command=_reproduction_command(project, run),
            expected_evidence="A new run record with a retained waveform artifact for the reproduced case.",
            execution_mode="manual_opt_in_new_run",
            prerequisites=prerequisites,
        )
    elif run["status"] == "TIMEOUT" and not suggestions:
        _add_suggestion(
            suggestions,
            seen,
            key=("waveform_index_review", run_id),
            kind="waveform_index_review",
            target=run_id,
            rationale=(
                "The timeout has a retained waveform but no assertion-linked signal "
                "candidate. Review the indexed signals first, then choose a targeted value probe."
            ),
            evidence={
                "run_id": run_id,
                "status": run["status"],
                "waveform_path": run.get("waveform_path"),
                "waveform_signals": waveform_index["summary"]["signals"],
            },
            command=_display_command(
                project,
                ["waveform-index", "--run", run_id],
            ),
            expected_evidence="A deterministic signal/scope inventory for selecting a targeted waveform probe.",
            execution_mode="read_only_existing_artifact",
        )

    if not suggestions:
        _add_suggestion(
            suggestions,
            seen,
            key=("log_context", run_id),
            kind="log_context",
            target=str(run["log_path"]),
            rationale=(
                "No stronger waveform/source probe can be justified from retained "
                "evidence. Inspect the failing log around the normalized failure anchor "
                "before adding instrumentation."
            ),
            evidence={
                "run_id": run_id,
                "status": run["status"],
                "failure_signature": root_cause["failure_signature"],
                "log_path": run["log_path"],
            },
            command=None,
            expected_evidence="Additional explicit failure context suitable for a new assertion or signal probe.",
            execution_mode="manual_read_only",
        )

    for priority, suggestion in enumerate(suggestions, start=1):
        suggestion["priority"] = priority

    return {
        "schema_version": 1,
        "analysis": "debug_probe_suggestions",
        "project": project.name,
        "run": root_cause["run"],
        "root_cause": {
            "failure_signature": root_cause["failure_signature"],
            "semantics": root_cause["semantics"],
            "candidate_count": root_cause["summary"]["candidates"],
            "candidate_limit": candidate_limit,
        },
        "suggestions": suggestions,
        "summary": {
            "suggestions": len(suggestions),
            "read_only": sum(
                item["execution_mode"].startswith("read_only")
                or item["execution_mode"] == "manual_read_only"
                for item in suggestions
            ),
            "manual_opt_in_new_runs": sum(
                item["execution_mode"] == "manual_opt_in_new_run"
                for item in suggestions
            ),
        },
        "policy": {
            "automatic_execution": False,
            "meaning": (
                "Suggestions are deterministic next-observation steps derived from "
                "retained evidence. They are not executed automatically and do not "
                "claim a candidate is causal."
            ),
        },
        "limitations": [
            "Waveform probe suggestions require a retained VCD waveform.",
            "Immediate fan-in suggestions use source-level simple-assignment connectivity only.",
            "Signals are included only when their waveform path resolves exactly in-scope or by unique leaf name.",
            "No generated assertion, test, RTL edit, or rerun is executed automatically.",
        ],
    }


def write_debug_probe_report(
    project: ProjectConfig,
    *,
    run_id: str,
    candidate_limit: int = 5,
    max_signals: int = 8,
    event_limit: int = 100,
    signal_limit: int = 20,
    output: str | Path = ".zddv/debug/probes.json",
) -> dict[str, Any]:
    report = suggest_debug_probes(
        project,
        run_id=run_id,
        candidate_limit=candidate_limit,
        max_signals=max_signals,
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
