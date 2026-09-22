from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from zddv.config import ProjectConfig


_SOURCE_TYPES = {"line", "statement"}
_LOGIC_TYPES = {"branch", "condition", "expression"}


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _source_evidence(hole: Mapping[str, Any]) -> dict[str, Any] | None:
    file_value = _optional_text(hole.get("source_file")) or _optional_text(
        hole.get("file")
    )
    line_value = hole.get("line")
    if line_value is None:
        line_value = hole.get("origin_line")

    if file_value is None and line_value is None:
        return None

    result: dict[str, Any] = {}
    if file_value is not None:
        result["file"] = file_value
    if line_value is not None:
        result["line"] = int(line_value)
    source_code = _optional_text(hole.get("source_code"))
    if source_code is not None:
        result["source_code"] = source_code
    return result


def _named_target(hole: Mapping[str, Any]) -> str:
    for key in (
        "signal",
        "transition",
        "state",
        "condition",
        "expression",
        "statement",
        "name",
    ):
        value = _optional_text(hole.get(key))
        if value is not None:
            return value
    return "unnamed coverage item"


def _suggestion_for_hole(hole: Mapping[str, Any], *, index: int) -> dict[str, Any]:
    point_type = (_optional_text(hole.get("type")) or "unknown").lower()
    source = _source_evidence(hole)
    name = _optional_text(hole.get("name")) or ""
    signal = _optional_text(hole.get("signal"))
    transition = _optional_text(hole.get("transition"))
    toggle_transition = _optional_text(hole.get("toggle_transition"))
    state = _optional_text(hole.get("state"))
    detail = _optional_text(hole.get("detail"))
    truth_row = hole.get("truth_row")
    fec_target = _optional_text(hole.get("fec_target"))

    action = "review_coverage_hole"
    specificity = "NAMED_ONLY"
    test_intent = (
        f"Review the uncovered {point_type} coverage item '{_named_target(hole)}' "
        "and define stimulus only after its coverage semantics are confirmed."
    )

    if point_type in _SOURCE_TYPES:
        action = "reach_source_location"
        if source is not None:
            specificity = "SOURCE_LOCATION"
            location = source.get("file", "source")
            if source.get("line") is not None:
                location = f"{location}:{source['line']}"
            test_intent = (
                f"Create or select a test scenario that reaches the uncovered "
                f"{point_type} at {location}; preserve existing checks to confirm "
                "the scenario is legal and observable."
            )
        else:
            test_intent = (
                f"Create or select a test scenario that reaches the explicitly "
                f"reported uncovered {point_type} item '{_named_target(hole)}'."
            )
    elif point_type in _LOGIC_TYPES:
        action = f"exercise_reported_{point_type}"
        qualifiers: list[str] = []
        if truth_row is not None:
            qualifiers.append(f"truth row {truth_row}")
        if fec_target is not None:
            qualifiers.append(f"FEC target {fec_target}")
        if detail is not None:
            qualifiers.append(f"reported detail {detail}")
        if qualifiers:
            specificity = "EXPLICIT_OUTCOME"
            target = ", ".join(qualifiers)
            test_intent = (
                f"Drive legal design inputs/state so the uncovered {point_type} "
                f"outcome ({target}) is exercised and observed by coverage."
            )
        elif source is not None:
            specificity = "SOURCE_LOCATION"
            location = source.get("file", "source")
            if source.get("line") is not None:
                location = f"{location}:{source['line']}"
            test_intent = (
                f"Exercise the uncovered {point_type} reported at {location}. "
                "Do not assume a Boolean outcome unless the coverage evidence "
                "identifies it explicitly."
            )
        else:
            test_intent = (
                f"Exercise the explicitly reported uncovered {point_type} item "
                f"'{_named_target(hole)}' without inventing an unreported outcome."
            )
    elif point_type == "toggle":
        action = "exercise_reported_toggle"
        if signal is not None and toggle_transition is not None:
            specificity = "EXPLICIT_TRANSITION"
            test_intent = (
                f"Create a legal scenario that drives signal '{signal}' through "
                f"the explicitly uncovered transition '{toggle_transition}' and "
                "keeps coverage sampling enabled."
            )
        elif signal is not None:
            specificity = "EXPLICIT_SIGNAL"
            test_intent = (
                f"Exercise toggling of explicitly reported signal '{signal}'. "
                "The missing transition direction is not inferred."
            )
        else:
            test_intent = (
                f"Exercise the explicitly reported toggle item '{_named_target(hole)}'; "
                "do not invent a signal alias or transition direction."
            )
    elif point_type == "fsm":
        action = "exercise_reported_fsm_item"
        if transition is not None:
            specificity = "EXPLICIT_TRANSITION"
            test_intent = (
                f"Create a legal scenario that reaches and takes the explicitly "
                f"uncovered FSM transition '{transition}'."
            )
        elif state is not None:
            specificity = "EXPLICIT_STATE"
            test_intent = (
                f"Create a legal scenario that reaches the explicitly uncovered "
                f"FSM state '{state}'."
            )
        else:
            test_intent = (
                f"Exercise the explicitly reported uncovered FSM item "
                f"'{_named_target(hole)}' without inventing a state path."
            )
    elif point_type in {"user", "covergroup", "bin", "functional"}:
        action = "exercise_named_coverage_point"
        test_intent = (
            f"Create or select stimulus that hits the explicitly named coverage "
            f"point '{_named_target(hole)}'; derive concrete values only from the "
            "coverpoint/bin definition."
        )

    evidence = {
        key: hole[key]
        for key in (
            "type",
            "name",
            "count",
            "scope",
            "file",
            "source_file",
            "line",
            "origin_line",
            "statement",
            "condition",
            "expression",
            "signal",
            "toggle_mode",
            "toggle_transition",
            "fec_context",
            "fec_target",
            "bit",
            "multibit",
            "fsm_id",
            "fsm_kind",
            "state",
            "transition",
            "transition_id",
            "block",
            "source_code",
            "type_name",
            "expression_index",
            "truth_row",
            "row",
            "detail",
        )
        if key in hole and hole[key] is not None
    }

    return {
        "hole_index": index,
        "coverage_type": point_type,
        "action": action,
        "specificity": specificity,
        "target": _named_target(hole),
        "test_intent": test_intent,
        "source": source,
        "evidence": evidence,
    }


def build_coverage_test_suggestions(
    hole_report: Mapping[str, Any],
    *,
    limit: int = 100,
) -> dict[str, Any]:
    """Derive conservative test intents only from explicit normalized hole evidence."""

    if limit < 1:
        raise ValueError("limit must be >= 1")
    if not isinstance(hole_report, Mapping):
        raise ValueError("coverage hole report must be a JSON object")

    holes = hole_report.get("holes")
    if not isinstance(holes, list):
        raise ValueError("coverage hole report must contain a holes array")

    suggestions: list[dict[str, Any]] = []
    for index, raw_hole in enumerate(holes):
        if not isinstance(raw_hole, Mapping):
            raise ValueError(f"coverage hole {index} must be a JSON object")
        suggestions.append(_suggestion_for_hole(raw_hole, index=index))
        if len(suggestions) >= limit:
            break

    specificity: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for item in suggestions:
        specificity[item["specificity"]] = specificity.get(item["specificity"], 0) + 1
        by_type[item["coverage_type"]] = by_type.get(item["coverage_type"], 0) + 1

    reported_holes = hole_report.get("reported_holes")
    if reported_holes is None:
        reported_holes = len(holes)

    return {
        "schema_version": 1,
        "analysis": "coverage_test_suggestions",
        "summary": {
            "input_total_holes": int(hole_report.get("total_holes", len(holes))),
            "input_reported_holes": int(reported_holes),
            "suggestions": len(suggestions),
            "truncated_by_suggestion_limit": len(holes) > len(suggestions),
            "by_type": dict(sorted(by_type.items())),
            "by_specificity": dict(sorted(specificity.items())),
        },
        "suggestions": suggestions,
        "semantics": (
            "Suggestions are deterministic test intents derived from explicit normalized "
            "coverage-hole evidence. They are not generated executable tests and do not "
            "invent stimulus values, state paths, or uncovered outcomes."
        ),
        "limitations": [
            "The input coverage-hole report may itself be filtered or truncated.",
            "A source location does not identify the stimulus needed to reach it.",
            "Concrete stimulus values must come from RTL, protocol, or coverage definitions.",
            "Executable test/assertion generation remains a separate reviewable opt-in step.",
        ],
    }


def write_coverage_test_suggestions(
    project: ProjectConfig,
    path: str | Path = ".zddv/coverage/holes.json",
    *,
    limit: int = 100,
    output: str | Path = ".zddv/debug/coverage-test-suggestions.json",
) -> dict[str, Any]:
    input_path = Path(path)
    if not input_path.is_absolute():
        input_path = project.root / input_path
    input_path = input_path.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(
            f"Coverage-hole report not found: {input_path}. "
            "Run 'zddv coverage-holes' first or provide an explicit report path."
        )

    raw_bytes = input_path.read_bytes()
    payload = json.loads(raw_bytes.decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("coverage hole report must be a JSON object")

    report = build_coverage_test_suggestions(payload, limit=limit)
    report["project"] = project.name
    report["input_path"] = str(input_path)
    report["input_sha256"] = hashlib.sha256(raw_bytes).hexdigest()

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
