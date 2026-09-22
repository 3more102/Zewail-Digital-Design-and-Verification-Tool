from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zddv.config import ProjectConfig


_RETAINED_EVIDENCE_KEYS = (
    "type",
    "name",
    "count",
    "scope",
    "file",
    "source_file",
    "line",
    "item",
    "row",
    "detail",
    "statement",
    "condition",
    "expression",
    "signal",
    "toggle_mode",
    "toggle_transition",
    "scope_kind",
    "fec_context",
    "fec_target",
    "bit",
    "multibit",
    "fec_hits",
    "fec_conditions",
    "fsm_id",
    "fsm_kind",
    "state",
    "transition",
    "transition_id",
    "block",
    "origin_line",
    "source_code",
    "type_name",
    "expression_index",
    "truth_row",
    "evidence",
)


def _location(hole: dict[str, Any]) -> str | None:
    file_value = hole.get("source_file") or hole.get("file")
    line_value = hole.get("line") or hole.get("origin_line")
    if file_value is not None and line_value is not None:
        return f"{file_value}:{line_value}"
    if file_value is not None:
        return str(file_value)
    return None


def _retained_evidence(hole: dict[str, Any]) -> dict[str, Any]:
    return {
        key: hole[key]
        for key in _RETAINED_EVIDENCE_KEYS
        if key in hole and hole[key] is not None
    }


def _coverage_test_intent(hole: dict[str, Any]) -> tuple[str, str]:
    kind = str(hole.get("type") or "unknown")
    location = _location(hole)

    if kind == "toggle":
        signal = str(hole.get("signal") or hole.get("name") or "").strip()
        transition = str(hole.get("toggle_transition") or "").strip()
        if signal and transition:
            return (
                "exercise_toggle_transition",
                f"Drive {signal} through the recorded uncovered transition "
                f"{transition}.",
            )
        if signal:
            return (
                "exercise_toggle",
                f"Exercise the recorded uncovered toggle point {signal}; "
                "do not infer a missing transition that is not present in the evidence.",
            )

    if kind == "fsm":
        state = str(hole.get("state") or "").strip()
        transition = str(hole.get("transition") or "").strip()
        fsm_kind = str(hole.get("fsm_kind") or "").strip().lower()
        if transition or fsm_kind == "transition":
            target = transition or str(hole.get("name") or "recorded transition")
            return (
                "exercise_fsm_transition",
                f"Exercise the recorded uncovered FSM transition {target} through "
                "a legal design path.",
            )
        if state or fsm_kind == "state":
            target = state or str(hole.get("name") or "recorded state")
            return (
                "reach_fsm_state",
                f"Reach the recorded uncovered FSM state {target} through a legal "
                "design path.",
            )

    if kind in {"condition", "expression"}:
        targets: list[str] = []
        if hole.get("fec_target") is not None:
            targets.append(f"target={hole['fec_target']}")
        if hole.get("bit") is not None:
            targets.append(f"bit={hole['bit']}")
        if hole.get("truth_row") is not None:
            targets.append(f"truth_row={hole['truth_row']}")
        if hole.get("detail") is not None:
            targets.append(f"detail={hole['detail']}")
        suffix = f" ({', '.join(targets)})" if targets else ""
        where = f" at {location}" if location else ""
        return (
            "exercise_fec_target",
            f"Exercise the recorded uncovered {kind} coverage target{suffix}{where}. "
            "Derive concrete stimulus from the design/testbench; coverage metadata "
            "alone is not used to invent input assignments.",
        )

    if kind == "branch":
        where = f" at {location}" if location else ""
        statement = str(hole.get("statement") or "").strip()
        detail = f" for {statement}" if statement else ""
        return (
            "exercise_branch",
            f"Exercise the recorded uncovered branch{where}{detail}. Select concrete "
            "stimulus from design/testbench semantics rather than guessing branch inputs.",
        )

    if kind in {"line", "statement", "block"}:
        where = f" at {location}" if location else ""
        return (
            "execute_source_item",
            f"Execute the recorded uncovered {kind} coverage item{where}.",
        )

    name = str(hole.get("name") or "").strip()
    target = f" {name}" if name else ""
    return (
        "exercise_coverage_point",
        f"Exercise the recorded uncovered {kind} coverage point{target}; keep the "
        "test stimulus tied to verified design/testbench semantics.",
    )


def build_coverage_test_suggestions(
    hole_report: dict[str, Any],
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    """Build reviewable test intents only from normalized coverage-hole evidence."""

    holes = hole_report.get("holes")
    if not isinstance(holes, list):
        raise ValueError("coverage-hole report must contain a 'holes' list")
    if limit is not None and limit < 0:
        raise ValueError("limit must be >= 0")

    total_holes = int(hole_report.get("total_holes", len(holes)))
    reported_holes = int(hole_report.get("reported_holes", len(holes)))
    selected = holes if limit is None else holes[:limit]

    suggestions: list[dict[str, Any]] = []
    for index, raw_hole in enumerate(selected, start=1):
        if not isinstance(raw_hole, dict):
            raise ValueError("each coverage hole must be an object")
        strategy, intent = _coverage_test_intent(raw_hole)
        suggestions.append(
            {
                "rank": index,
                "kind": "test_intent",
                "coverage_type": str(raw_hole.get("type") or "unknown"),
                "strategy": strategy,
                "intent": intent,
                "review_required": True,
                "generated_test_code": False,
                "evidence": _retained_evidence(raw_hole),
            }
        )

    return {
        "schema_version": 1,
        "analysis": "coverage_hole_test_suggestions",
        "semantics": (
            "Suggestions are reviewable test intents derived only from normalized "
            "coverage-hole evidence. ZDDV does not invent input values, legal paths, "
            "or executable test code."
        ),
        "source": {
            "filter_type": hole_report.get("filter_type"),
            "total_holes": total_holes,
            "reported_holes": reported_holes,
            "input_truncated": total_holes > reported_holes,
        },
        "suggestions": suggestions,
        "summary": {
            "suggestions": len(suggestions),
            "coverage_types": sorted(
                {str(item["coverage_type"]) for item in suggestions}
            ),
            "review_required": len(suggestions),
            "generated_test_code": 0,
        },
    }


def write_coverage_test_suggestions(
    project: ProjectConfig,
    *,
    input_path: str | Path = ".zddv/coverage/holes.json",
    output: str | Path = ".zddv/coverage/test-suggestions.json",
    limit: int | None = None,
) -> dict[str, Any]:
    source = Path(input_path)
    if not source.is_absolute():
        source = project.root / source
    source = source.resolve()
    if not source.is_file():
        raise RuntimeError(
            f"Coverage-hole report not found at {source}. "
            "Run 'zddv coverage-holes' first."
        )

    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid coverage-hole JSON at {source}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Coverage-hole report must be a JSON object.")

    report = build_coverage_test_suggestions(payload, limit=limit)
    destination = Path(output)
    if not destination.is_absolute():
        destination = project.root / destination
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)

    result = {
        **report,
        "project": project.name,
        "source_report": str(source),
        "path": str(destination),
    }
    destination.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result
