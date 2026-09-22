from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from zddv.config import ProjectConfig


def _source_location(hole: Mapping[str, Any]) -> str | None:
    file_value = hole.get("source_file") or hole.get("file")
    line_value = hole.get("line") or hole.get("origin_line")
    if file_value is None:
        return None
    if line_value is None:
        return str(file_value)
    return f"{file_value}:{line_value}"


def _functional_bin_holes(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    bins = payload.get("bins")
    if not isinstance(bins, list):
        raise ValueError("functional coverage payload must contain a 'bins' list")

    holes: list[dict[str, Any]] = []
    for index, raw in enumerate(bins):
        if not isinstance(raw, Mapping):
            raise ValueError(f"functional coverage bin {index} must be an object")

        hits = raw.get("hits", 0)
        goal = raw.get("goal", 1)
        if isinstance(hits, bool) or not isinstance(hits, int) or hits < 0:
            raise ValueError(f"functional coverage bin {index} has invalid hits")
        if isinstance(goal, bool) or not isinstance(goal, int) or goal < 1:
            raise ValueError(f"functional coverage bin {index} has invalid goal")

        status = str(raw.get("status") or "").strip().upper()
        uncovered = status == "UNCOVERED" if status else hits < goal
        if not uncovered:
            continue

        scope = str(raw.get("scope") or "").strip()
        coverpoint = str(raw.get("coverpoint") or "").strip()
        bin_name = str(raw.get("bin_name") or raw.get("bin") or "").strip()
        if not coverpoint or not bin_name:
            raise ValueError(
                f"functional coverage bin {index} requires coverpoint and bin name"
            )
        parts = [part for part in (scope, coverpoint, bin_name) if part]
        holes.append(
            {
                "type": "functional_bin",
                "name": ".".join(parts),
                "scope": scope,
                "coverpoint": coverpoint,
                "bin_name": bin_name,
                "hits": hits,
                "goal": goal,
                "metadata": dict(raw.get("metadata") or {}),
            }
        )
    return holes


def _normalized_holes(
    payload: Mapping[str, Any],
) -> tuple[str, list[dict[str, Any]], int, int]:
    if isinstance(payload.get("holes"), list):
        holes: list[dict[str, Any]] = []
        for index, raw in enumerate(payload["holes"]):
            if not isinstance(raw, Mapping):
                raise ValueError(f"coverage hole {index} must be an object")
            item = dict(raw)
            point_type = str(item.get("type") or "unknown").strip() or "unknown"
            name = str(item.get("name") or "").strip()
            if not name:
                raise ValueError(f"coverage hole {index} is missing a name")
            item["type"] = point_type
            item["name"] = name
            holes.append(item)

        total = payload.get("total_holes", len(holes))
        reported = payload.get("reported_holes", len(holes))
        if isinstance(total, bool) or not isinstance(total, int) or total < len(holes):
            raise ValueError("coverage hole report has invalid total_holes")
        if (
            isinstance(reported, bool)
            or not isinstance(reported, int)
            or reported < len(holes)
            or reported > total
        ):
            raise ValueError("coverage hole report has invalid reported_holes")
        return "code_coverage_holes", holes, total, reported

    if isinstance(payload.get("bins"), list):
        holes = _functional_bin_holes(payload)
        return "functional_coverage_bins", holes, len(holes), len(holes)

    raise ValueError(
        "coverage suggestion input must be a coverage-hole report with 'holes' "
        "or a normalized functional-coverage payload with 'bins'"
    )


def _suggestion_for_hole(hole: Mapping[str, Any]) -> dict[str, Any]:
    point_type = str(hole.get("type") or "unknown")
    name = str(hole.get("name") or "")
    location = _source_location(hole)
    where = location or name

    basis = ["explicit_uncovered_coverage_point"]
    objective: str

    if point_type in {"line", "statement", "block"}:
        objective = f"Exercise the uncovered {point_type} at {where} at least once."
        if location:
            basis.append("explicit_source_location")
    elif point_type == "branch":
        objective = (
            f"Exercise the uncovered branch at {where}. "
            "The coverage evidence does not specify the required stimulus values."
        )
        if location:
            basis.append("explicit_source_location")
    elif point_type in {"condition", "expression"}:
        fec_target = hole.get("fec_target")
        truth_row = hole.get("truth_row")
        if fec_target is not None:
            objective = (
                f"Exercise the explicit uncovered {point_type} FEC target "
                f"{fec_target!r} at {where}."
            )
            basis.append("explicit_fec_target")
        elif truth_row is not None:
            objective = (
                f"Exercise the explicit uncovered {point_type} truth row "
                f"{truth_row!r} at {where}."
            )
            basis.append("explicit_truth_row")
        else:
            objective = (
                f"Exercise the uncovered {point_type} point at {where}; "
                "no input assignment is inferred from aggregate coverage evidence."
            )
        if location:
            basis.append("explicit_source_location")
    elif point_type == "toggle":
        signal = str(hole.get("signal") or name)
        transition = hole.get("toggle_transition")
        if transition is not None:
            objective = (
                f"Drive {signal} through the explicitly uncovered "
                f"{transition} toggle transition."
            )
            basis.append("explicit_toggle_transition")
        else:
            objective = (
                f"Exercise the uncovered toggle point {signal}; "
                "the exact missing transition is not present in this evidence."
            )
        if location:
            basis.append("explicit_source_location")
    elif point_type == "fsm":
        fsm_id = str(hole.get("fsm_id") or "").strip()
        transition = hole.get("transition")
        state = hole.get("state")
        label = f"FSM {fsm_id}" if fsm_id else "the reported FSM"
        if transition is not None:
            objective = f"Exercise {label} through the uncovered transition {transition!r}."
            basis.append("explicit_fsm_transition")
        elif state is not None:
            objective = f"Reach the uncovered state {state!r} in {label}."
            basis.append("explicit_fsm_state")
        else:
            objective = (
                f"Exercise the uncovered FSM point {name}; "
                "no state/transition semantics are inferred beyond the retained evidence."
            )
        if location:
            basis.append("explicit_source_location")
    elif point_type == "functional_bin":
        hits = int(hole.get("hits", 0))
        goal = int(hole.get("goal", 1))
        objective = (
            f"Create focused stimulus intended to hit functional coverage bin "
            f"{name!r} until its explicit goal {goal} is met "
            f"(current hits: {hits})."
        )
        basis.extend(["explicit_functional_bin", "explicit_bin_goal"])
    else:
        objective = (
            f"Create focused stimulus intended to reach the explicit uncovered "
            f"coverage point {name!r}. No stimulus values are inferred from its label."
        )

    return {
        "kind": "coverage_test_objective",
        "coverage_type": point_type,
        "target": name,
        "objective": objective,
        "basis": basis,
        "evidence": dict(hole),
        "review_required": True,
        "generated_test": False,
    }


def suggest_coverage_hole_tests(
    payload: Mapping[str, Any],
    *,
    limit: int = 100,
) -> dict[str, Any]:
    """Create deterministic, review-only test objectives from explicit coverage holes."""

    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be >= 1")

    source_kind, holes, source_total, source_reported = _normalized_holes(payload)
    holes.sort(
        key=lambda item: (
            str(item.get("type") or "unknown"),
            str(item.get("name") or ""),
        )
    )
    selected = holes[:limit]
    suggestions = []
    for rank, hole in enumerate(selected, start=1):
        suggestions.append({"rank": rank, **_suggestion_for_hole(hole)})

    by_type: dict[str, int] = {}
    for item in suggestions:
        point_type = str(item["coverage_type"])
        by_type[point_type] = by_type.get(point_type, 0) + 1

    return {
        "schema_version": 1,
        "analysis": "coverage_hole_test_suggestions",
        "source_kind": source_kind,
        "semantics": (
            "Suggestions are reviewable coverage objectives derived only from explicit "
            "uncovered-point metadata. They are not generated tests and do not claim "
            "that any unspecified stimulus will reach the target."
        ),
        "source_summary": {
            "total_holes": source_total,
            "reported_holes": source_reported,
            "available_for_suggestion": len(holes),
            "input_truncated": source_total > len(holes),
        },
        "summary": {
            "suggestions": len(suggestions),
            "by_type": dict(sorted(by_type.items())),
            "limited": len(holes) > len(selected),
        },
        "suggestions": suggestions,
        "review_required": True,
        "automatic_test_generation": False,
    }


def write_coverage_test_suggestions(
    project: ProjectConfig,
    path: str | Path = ".zddv/coverage/holes.json",
    *,
    limit: int = 100,
    output: str | Path = ".zddv/coverage/test-suggestions.json",
) -> dict[str, Any]:
    input_path = Path(path)
    if not input_path.is_absolute():
        input_path = project.root / input_path
    input_path = input_path.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(
            f"{input_path}. Run 'zddv coverage-holes' first or pass --input "
            "with a normalized functional-coverage JSON file."
        )

    raw = input_path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("coverage suggestion input root must be a JSON object")

    report = suggest_coverage_hole_tests(payload, limit=limit)

    destination = Path(output)
    if not destination.is_absolute():
        destination = project.root / destination
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)

    report.update(
        {
            "project": project.name,
            "input_path": str(input_path),
            "input_sha256": hashlib.sha256(raw).hexdigest(),
            "report_path": str(destination),
        }
    )
    destination.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report
