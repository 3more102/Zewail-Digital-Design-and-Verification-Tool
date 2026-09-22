from __future__ import annotations

from collections import Counter
import json
from pathlib import Path


_EVIDENCE_KEYS = (
    "scope",
    "source_file",
    "line",
    "item",
    "row",
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
    "origin_line",
    "source_code",
    "type_name",
    "expression_index",
    "truth_row",
    "detail",
    "evidence",
)


def _location(hole: dict) -> str | None:
    source_file = hole.get("source_file") or hole.get("file")
    line = hole.get("line")
    if source_file and line is not None:
        return f"{source_file}:{line}"
    if source_file:
        return str(source_file)
    return None


def _toggle_intent(hole: dict) -> str:
    signal = hole.get("signal") or hole.get("name") or "the uncovered signal"
    evidence = hole.get("evidence")
    missing: list[str] = []
    if isinstance(evidence, dict):
        if evidence.get("rise") == 0:
            missing.append("0->1")
        if evidence.get("fall") == 0:
            missing.append("1->0")
    transition = hole.get("toggle_transition")
    if transition:
        missing = [str(transition)]

    if missing:
        edges = " and ".join(missing)
        return (
            f"Create a reviewable stimulus scenario that makes {signal} exercise "
            f"the explicitly uncovered toggle transition(s): {edges}."
        )
    return (
        f"Create a reviewable stimulus scenario targeting the uncovered toggle "
        f"point {signal}; derive the driving stimulus from the RTL/testbench, "
        "not from this suggestion."
    )


def _intent_for_hole(hole: dict) -> str:
    point_type = str(hole.get("type") or "unknown")
    name = str(hole.get("name") or "<unnamed>")
    location = _location(hole)
    where = f" at {location}" if location else ""

    if point_type == "fsm":
        fsm_id = hole.get("fsm_id")
        fsm_label = f" in FSM {fsm_id}" if fsm_id else ""
        if hole.get("fsm_kind") == "transition" and hole.get("transition"):
            return (
                "Add a directed or constrained-random scenario whose explicit "
                f"coverage objective is FSM transition {hole['transition']}{fsm_label}{where}."
            )
        if hole.get("state"):
            return (
                "Add a directed or constrained-random scenario whose explicit "
                f"coverage objective is reaching FSM state {hole['state']}{fsm_label}{where}."
            )
        return f"Target the explicit uncovered FSM point {name}{where}."

    if point_type == "toggle":
        return _toggle_intent(hole)

    if point_type in {"condition", "expression"}:
        target = hole.get("fec_target")
        if target:
            return (
                f"Target the explicit uncovered {point_type} FEC combination "
                f"{target}{where}; use the retained expression context as the oracle."
            )
        return (
            f"Target the explicit uncovered {point_type} point {name}{where}; "
            "do not infer missing Boolean combinations beyond the retained evidence."
        )

    if point_type in {"line", "statement", "branch", "block"}:
        return (
            f"Add a scenario that reaches the uncovered {point_type} point "
            f"{name}{where}; inspect the referenced RTL to choose valid driving stimulus."
        )

    if point_type in {"covergroup", "coverpoint", "bin", "cross"}:
        return (
            f"Add a scenario that samples the explicit uncovered functional-coverage "
            f"point {name}{where}."
        )

    return (
        f"Review and target the explicit uncovered coverage point {name} "
        f"(type={point_type}){where}; no unstated DUT behavior is inferred."
    )


def build_coverage_test_suggestions(
    report: dict,
    *,
    limit: int | None = None,
) -> dict:
    """Build deterministic, non-executing test intents from normalized holes."""
    holes = report.get("holes")
    if not isinstance(holes, list):
        raise ValueError("Coverage-hole report must contain a 'holes' list")

    normalized: list[dict] = []
    for hole in holes:
        if not isinstance(hole, dict):
            raise ValueError("Each coverage hole must be a JSON object")
        normalized.append(hole)

    normalized.sort(
        key=lambda hole: (
            str(hole.get("type") or "unknown"),
            str(hole.get("name") or ""),
        )
    )
    selected = normalized if limit is None else normalized[: max(0, limit)]

    suggestions: list[dict] = []
    for rank, hole in enumerate(selected, start=1):
        point_type = str(hole.get("type") or "unknown")
        evidence = {
            key: hole[key]
            for key in _EVIDENCE_KEYS
            if key in hole and hole[key] is not None
        }
        suggestions.append(
            {
                "rank": rank,
                "kind": "coverage_test_intent",
                "coverage_type": point_type,
                "hole_name": str(hole.get("name") or ""),
                "intent": _intent_for_hole(hole),
                "evidence": evidence,
                "review_required": True,
                "auto_execute": False,
            }
        )

    by_type = Counter(item["coverage_type"] for item in suggestions)
    return {
        "source_total_holes": int(report.get("total_holes", len(normalized))),
        "source_reported_holes": len(normalized),
        "suggestion_count": len(suggestions),
        "by_type": dict(sorted(by_type.items())),
        "suggestions": suggestions,
        "semantics": (
            "Suggestions are deterministic test intents derived only from explicit "
            "normalized coverage-hole evidence. They do not infer DUT behavior, "
            "generate test code, or execute stimulus automatically."
        ),
    }


def write_coverage_test_suggestions(
    source: str | Path,
    output: str | Path,
    *,
    limit: int | None = None,
) -> dict:
    source_path = Path(source).resolve()
    if not source_path.is_file():
        raise RuntimeError(
            f"Coverage-hole report not found at {source_path}. "
            "Run 'zddv coverage-holes' first."
        )

    try:
        report = json.loads(source_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Coverage-hole report is not valid JSON: {source_path}"
        ) from exc
    if not isinstance(report, dict):
        raise ValueError("Coverage-hole report root must be a JSON object")

    result = build_coverage_test_suggestions(report, limit=limit)
    destination = Path(output).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {**result, "source_report": str(source_path)}
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return {**payload, "path": str(destination)}
