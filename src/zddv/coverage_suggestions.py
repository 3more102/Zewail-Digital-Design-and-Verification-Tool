from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from zddv.config import ProjectConfig


def _require_hole_report(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("coverage-hole report must be an object")

    report = dict(payload)
    holes = report.get("holes")
    if not isinstance(holes, list):
        raise ValueError("coverage-hole report must contain a 'holes' list")
    for index, hole in enumerate(holes):
        if not isinstance(hole, Mapping):
            raise ValueError(f"coverage hole {index} must be an object")
    return report


def _source_target(hole: Mapping[str, Any]) -> dict[str, Any]:
    target: dict[str, Any] = {}
    file_value = hole.get("source_file") or hole.get("file")
    if file_value:
        target["file"] = str(file_value)

    line_value = hole.get("line")
    if line_value is None:
        line_value = hole.get("origin_line")
    if line_value is not None:
        target["line"] = line_value

    for key in (
        "item",
        "row",
        "scope",
        "block",
        "source_code",
        "detail",
        "expression",
        "condition",
    ):
        value = hole.get(key)
        if value is not None and value != "":
            target[key] = value
    return target


def _suggestion_for_hole(
    hole: Mapping[str, Any],
    *,
    index: int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    name = str(hole.get("name") or "").strip()
    point_type = str(hole.get("type") or "unknown").strip().lower() or "unknown"

    if not name:
        return None, {
            "index": index,
            "code": "MISSING_HOLE_NAME",
            "message": (
                "The normalized coverage hole has no stable name, so ZDDV will not "
                "invent a verification target."
            ),
            "evidence": dict(hole),
        }

    target = {"coverage_point": name, **_source_target(hole)}
    action = "exercise_normalized_coverage_point"
    intent = (
        f"Author or extend a test that exercises the exact normalized "
        f"{point_type} coverage point '{name}'."
    )

    if point_type == "toggle":
        signal = hole.get("signal")
        transition = hole.get("toggle_transition")
        if signal:
            target["signal"] = signal
        if transition:
            target["transition"] = transition
        if signal and transition:
            action = "drive_toggle_transition"
            intent = (
                f"Author or extend stimulus that exercises the recorded uncovered "
                f"toggle transition {transition} for signal {signal}."
            )
        elif signal:
            action = "exercise_signal_toggle"
            intent = (
                f"Author or extend stimulus that toggles the explicitly recorded "
                f"coverage signal {signal}; no transition direction is inferred."
            )

    elif point_type == "fsm":
        fsm_id = hole.get("fsm_id")
        fsm_kind = hole.get("fsm_kind")
        transition = hole.get("transition")
        state = hole.get("state")
        if fsm_id:
            target["fsm_id"] = fsm_id
        if fsm_kind:
            target["fsm_kind"] = fsm_kind
        if transition:
            target["transition"] = transition
            action = "reach_fsm_transition"
            intent = (
                f"Author or extend a test that reaches the explicitly uncovered "
                f"FSM transition {transition}."
            )
        elif state:
            target["state"] = state
            action = "reach_fsm_state"
            intent = (
                f"Author or extend a test that reaches the explicitly uncovered "
                f"FSM state {state}."
            )

    elif point_type in {"condition", "expression"}:
        fec_target = hole.get("fec_target")
        truth_row = hole.get("truth_row")
        if fec_target:
            target["fec_target"] = fec_target
            for key in ("fec_hits", "fec_conditions", "bit", "multibit", "fec_context"):
                value = hole.get(key)
                if value is not None:
                    target[key] = value
            action = "exercise_fec_target"
            intent = (
                f"Author or extend a test that exercises the explicitly uncovered "
                f"{point_type} FEC target {fec_target}."
            )
        elif truth_row is not None:
            target["truth_row"] = truth_row
            action = "exercise_condition_truth_row"
            intent = (
                f"Author or extend a test that reaches the explicitly uncovered "
                f"{point_type} truth-table row {truth_row}."
            )
        elif target.get("file") is not None:
            action = "exercise_source_coverage_item"
            intent = (
                f"Author or extend a test that exercises the uncovered {point_type} "
                f"item at the recorded source location; ZDDV does not infer input values."
            )

    elif point_type == "branch":
        action = "exercise_branch_coverage_item"
        intent = (
            "Author or extend a test that exercises the exact uncovered branch item "
            "identified by the retained source/detail evidence; branch direction is not "
            "invented when it is absent from the normalized report."
        )

    elif point_type in {"statement", "line", "block"}:
        if target.get("file") is not None or target.get("line") is not None:
            action = "exercise_source_coverage_item"
            intent = (
                f"Author or extend a test that executes the recorded uncovered "
                f"{point_type} source location."
            )

    return {
        "index": index,
        "kind": "coverage_hole_test_intent",
        "coverage_type": point_type,
        "hole_name": name,
        "action": action,
        "target": target,
        "intent": intent,
        "review_required": True,
        "generated_test": False,
        "evidence": dict(hole),
    }, None


def suggest_coverage_hole_tests(
    project: ProjectConfig,
    report: Mapping[str, Any],
    *,
    limit: int = 200,
) -> dict[str, Any]:
    """Create reviewable test intents from explicit normalized coverage-hole evidence."""

    if limit < 1:
        raise ValueError("limit must be >= 1")

    normalized = _require_hole_report(report)
    holes = list(normalized["holes"])
    inspected = holes[:limit]

    suggestions: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    for index, hole in enumerate(inspected):
        suggestion, blocker = _suggestion_for_hole(hole, index=index)
        if suggestion is not None:
            suggestions.append(suggestion)
        if blocker is not None:
            blockers.append(blocker)

    return {
        "schema_version": 1,
        "analysis": "coverage_hole_test_suggestions",
        "project": project.name,
        "semantics": (
            "Suggestions are verification intents derived only from normalized coverage-hole "
            "evidence. ZDDV does not infer stimulus values, causal explanations, or executable "
            "testbench code in this step."
        ),
        "source_report": {
            "filter_type": normalized.get("filter_type"),
            "total_holes": normalized.get("total_holes", len(holes)),
            "reported_holes": normalized.get("reported_holes", len(holes)),
        },
        "suggestions": suggestions,
        "blockers": blockers,
        "summary": {
            "available_holes": len(holes),
            "inspected_holes": len(inspected),
            "suggestions": len(suggestions),
            "blockers": len(blockers),
            "truncated": len(holes) > len(inspected),
        },
    }


def write_coverage_test_suggestions(
    project: ProjectConfig,
    path: str | Path = ".zddv/coverage/holes.json",
    *,
    limit: int = 200,
    output: str | Path = ".zddv/coverage/test-suggestions.json",
) -> dict[str, Any]:
    source = Path(path)
    if not source.is_absolute():
        source = project.root / source
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(
            f"{source}; run 'zddv coverage-holes' first or provide an explicit report path"
        )

    payload = json.loads(source.read_text(encoding="utf-8"))
    report = suggest_coverage_hole_tests(project, payload, limit=limit)
    report["source_report_path"] = str(source)

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
