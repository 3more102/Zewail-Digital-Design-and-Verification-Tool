from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from zddv.config import ProjectConfig
from zddv.storage import get_run_record, list_assertion_events
from zddv.waveform import write_waveform_index


_HINT_RE = re.compile(
    r"(?<![A-Za-z0-9_$])"
    r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*)"
    r"(?![A-Za-z0-9_$])"
)


def _signal_hints(
    event: dict[str, Any],
    waveform_index: dict[str, Any],
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    text = " ".join(
        value
        for value in (
            str(event.get("assertion_name") or ""),
            str(event.get("message") or ""),
        )
        if value
    )
    raw_tokens = {match.group("name") for match in _HINT_RE.finditer(text)}
    tokens = set(raw_tokens)
    for token in raw_tokens:
        tokens.update(part for part in re.split(r"[._]", token) if part)
    if not tokens:
        return []

    matches: list[dict[str, Any]] = []
    seen: set[str] = set()
    for signal in waveform_index.get("signals", []):
        path = str(signal.get("path") or "")
        name = str(signal.get("name") or "")
        match_kind: str | None = None

        if path and path in tokens:
            match_kind = "exact-path"
        elif name and name in tokens:
            match_kind = "exact-name"

        if match_kind is None or path in seen:
            continue
        seen.add(path)
        matches.append(
            {
                "path": path,
                "name": name,
                "width": signal.get("width"),
                "range": signal.get("range"),
                "match": match_kind,
            }
        )

    matches.sort(
        key=lambda item: (
            0 if item["match"] == "exact-path" else 1,
            item["path"],
        )
    )
    return matches[:limit]


def correlate_assertions(
    project: ProjectConfig,
    *,
    run_id: str | None = None,
    status: str | None = None,
    assertion_name: str | None = None,
    limit: int = 100,
    signal_hint_limit: int = 20,
) -> dict[str, Any]:
    events = list_assertion_events(
        project,
        run_id=run_id,
        status=status,
        assertion_name=assertion_name,
        limit=limit,
    )

    waveform_cache: dict[str, dict[str, Any] | None] = {}
    run_cache: dict[str, dict[str, Any] | None] = {}
    correlated: list[dict[str, Any]] = []

    with_waveform = 0
    fully_indexed = 0
    with_signal_hints = 0

    for event in events:
        event_run_id = str(event["run_id"])
        if event_run_id not in run_cache:
            run_cache[event_run_id] = get_run_record(project, event_run_id)
        run = run_cache[event_run_id]

        run_summary: dict[str, Any] | None = None
        waveform_summary: dict[str, Any] | None = None

        if run is not None:
            run_summary = {
                "run_id": run["run_id"],
                "created_at": run["created_at"],
                "status": run["status"],
                "test_name": run["test_name"],
                "seed": run["seed"],
                "simulator": run["simulator"],
                "top": run["top"],
                "run_dir": run["run_dir"],
            }

            if event_run_id not in waveform_cache:
                waveform_path = run.get("waveform_path")
                if waveform_path and Path(str(waveform_path)).exists():
                    output = (
                        project.root
                        / ".zddv"
                        / "waveforms"
                        / f"{event_run_id}.json"
                    )
                    waveform_cache[event_run_id] = write_waveform_index(
                        project,
                        run_id=event_run_id,
                        output=output,
                    )
                else:
                    waveform_cache[event_run_id] = None

            waveform = waveform_cache[event_run_id]
            if waveform is not None:
                with_waveform += 1
                if waveform["parse_status"] == "indexed":
                    fully_indexed += 1
                hints = _signal_hints(
                    event,
                    waveform,
                    limit=signal_hint_limit,
                )
                if hints:
                    with_signal_hints += 1

                waveform_summary = {
                    "artifact": waveform["artifact"]["path"],
                    "project_path": waveform["artifact"].get("project_path"),
                    "index": waveform["path"],
                    "format": waveform["format"],
                    "parse_status": waveform["parse_status"],
                    "timescale": waveform.get("timescale"),
                    "signal_count": waveform["summary"]["signals"],
                    "scope_count": waveform["summary"]["scopes"],
                    "signal_hints": hints,
                }
                if waveform.get("note"):
                    waveform_summary["note"] = waveform["note"]

        correlated.append(
            {
                "run_id": event_run_id,
                "event_index": event["event_index"],
                "created_at": event["created_at"],
                "assertion_name": event["assertion_name"],
                "status": event["status"],
                "message": event["message"],
                "log_path": event["log_path"],
                "log_line": event["log_line"],
                "run": run_summary,
                "waveform": waveform_summary,
            }
        )

    return {
        "schema_version": 1,
        "project": project.name,
        "filters": {
            "run_id": run_id,
            "status": status,
            "assertion_name": assertion_name,
            "limit": limit,
        },
        "events": correlated,
        "summary": {
            "events": len(correlated),
            "with_run_record": sum(1 for item in correlated if item["run"] is not None),
            "with_waveform": with_waveform,
            "with_indexed_waveform": fully_indexed,
            "with_signal_hints": with_signal_hints,
        },
    }


def write_assertion_waveform_report(
    project: ProjectConfig,
    *,
    run_id: str | None = None,
    status: str | None = None,
    assertion_name: str | None = None,
    limit: int = 100,
    signal_hint_limit: int = 20,
    output: str | Path = ".zddv/debug/assertion-waveform.json",
) -> dict[str, Any]:
    report = correlate_assertions(
        project,
        run_id=run_id,
        status=status,
        assertion_name=assertion_name,
        limit=limit,
        signal_hint_limit=signal_hint_limit,
    )

    destination = Path(output)
    if not destination.is_absolute():
        destination = project.root / destination
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    return {**report, "path": str(destination)}
