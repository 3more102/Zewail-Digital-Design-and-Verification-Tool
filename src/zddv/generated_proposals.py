from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _clean_comment(value: Any) -> str:
    return " ".join(str(value).replace("\r", " ").replace("\n", " ").split())


def _evidence_lines(evidence: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for key in sorted(evidence):
        value = evidence[key]
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, sort_keys=True)
        else:
            rendered = str(value)
        lines.append(f"{key}: {_clean_comment(rendered)}")
    return lines


def _test_scaffold(proposal_id: str, suggestion: dict[str, Any]) -> str:
    intent = _clean_comment(suggestion.get("intent") or "Review the explicit coverage objective.")
    evidence = suggestion.get("evidence")
    evidence_lines = _evidence_lines(evidence if isinstance(evidence, dict) else {})
    lines = [
        "// ZDDV GENERATED TEST SCAFFOLD - REVIEW REQUIRED",
        "// This file is intentionally disabled (.sv.disabled) and is never added",
        "// to project sources automatically.",
        f"// Proposal: {proposal_id}",
        f"// Objective: {intent}",
    ]
    lines.extend(f"// Evidence: {line}" for line in evidence_lines)
    lines.extend(
        [
            "//",
            "// TODO(review): choose an existing testbench/UVM test or sequence.",
            "// TODO(review): derive legal stimulus from the RTL/specification and existing TB.",
            "// TODO(review): add an explicit checker/coverage observation for the objective.",
            "// TODO(review): run the candidate manually and confirm the target hole closes",
            "//               without introducing failures or masking unrelated coverage.",
            "//",
            "// Intentionally no executable SystemVerilog is emitted here.",
            "",
        ]
    )
    return "\n".join(lines)


def _assertion_scaffold(proposal_id: str, suggestion: dict[str, Any]) -> str:
    intent = _clean_comment(suggestion.get("intent") or "Review the explicit coverage objective.")
    evidence = suggestion.get("evidence")
    evidence_lines = _evidence_lines(evidence if isinstance(evidence, dict) else {})
    lines = [
        "// ZDDV GENERATED ASSERTION/COVER SCAFFOLD - REVIEW REQUIRED",
        "// This file is intentionally disabled (.sv.disabled) and is never added",
        "// to project sources automatically.",
        f"// Proposal: {proposal_id}",
        f"// Objective: {intent}",
    ]
    lines.extend(f"// Evidence: {line}" for line in evidence_lines)
    lines.extend(
        [
            "//",
            "// TODO(review): select the correct design scope/bind target.",
            "// TODO(review): select the real sampling clock/reset from design evidence.",
            "// TODO(review): write the property expression from the specification.",
            "// TODO(review): decide whether this objective needs assert/assume/cover semantics.",
            "// TODO(review): compile and review the property before enabling it.",
            "//",
            "// Suggested shape (kept commented so no semantics are invented):",
            "// property zddv_review_property;",
            "//   @(posedge /* TODO clock */) disable iff (/* TODO reset */)",
            "//     /* TODO property expression */;",
            "// endproperty",
            "// // assert property (zddv_review_property);",
            "// // cover  property (zddv_review_property);",
            "",
        ]
    )
    return "\n".join(lines)


def build_reviewable_verification_proposals(
    suggestion_report: dict[str, Any],
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    """Build disabled test/assertion scaffolds from explicit coverage suggestions."""

    if limit is not None and limit < 1:
        raise ValueError("limit must be >= 1")

    suggestions = suggestion_report.get("suggestions")
    if not isinstance(suggestions, list):
        raise ValueError("Coverage suggestion report must contain a 'suggestions' list")

    normalized: list[dict[str, Any]] = []
    for item in suggestions:
        if not isinstance(item, dict):
            raise ValueError("Each coverage suggestion must be a JSON object")
        normalized.append(item)

    selected = normalized if limit is None else normalized[:limit]
    proposals: list[dict[str, Any]] = []
    for index, suggestion in enumerate(selected, start=1):
        proposal_id = f"coverage-{index:04d}"
        evidence = suggestion.get("evidence")
        if not isinstance(evidence, dict):
            evidence = {}
        objective = str(suggestion.get("intent") or "")
        proposal = {
            "proposal_id": proposal_id,
            "source_rank": suggestion.get("rank"),
            "coverage_type": str(suggestion.get("coverage_type") or "unknown"),
            "hole_name": str(suggestion.get("hole_name") or ""),
            "objective": objective,
            "evidence": evidence,
            "review_required": True,
            "auto_apply": False,
            "test_scaffold": _test_scaffold(proposal_id, suggestion),
            "assertion_scaffold": _assertion_scaffold(proposal_id, suggestion),
        }
        proposals.append(proposal)

    return {
        "schema_version": 1,
        "analysis": "reviewable_verification_proposals",
        "source_suggestion_count": len(normalized),
        "proposal_count": len(proposals),
        "proposals": proposals,
        "policy": {
            "review_required": True,
            "automatic_source_modification": False,
            "automatic_execution": False,
            "code_emission_default": False,
            "meaning": (
                "Generated material is scaffolding only. ZDDV does not infer clocks, "
                "resets, legal stimulus, expected DUT behavior, or property semantics. "
                "An engineer must review and edit every proposal before use."
            ),
        },
    }


def write_reviewable_verification_proposals(
    source: str | Path,
    output: str | Path,
    *,
    limit: int | None = None,
    emit_dir: str | Path | None = None,
) -> dict[str, Any]:
    source_path = Path(source).resolve()
    if not source_path.is_file():
        raise RuntimeError(
            f"Coverage suggestion report not found at {source_path}. "
            "Run 'zddv coverage-suggest' first."
        )

    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Coverage suggestion report is not valid JSON: {source_path}"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError("Coverage suggestion report root must be a JSON object")

    result = build_reviewable_verification_proposals(payload, limit=limit)
    emitted: list[dict[str, str]] = []

    if emit_dir is not None:
        emission_root = Path(emit_dir).resolve()
        emission_root.mkdir(parents=True, exist_ok=True)
        for proposal in result["proposals"]:
            proposal_id = str(proposal["proposal_id"])
            test_path = emission_root / f"{proposal_id}-test.sv.disabled"
            assertion_path = emission_root / f"{proposal_id}-assertion.sv.disabled"
            test_path.write_text(str(proposal["test_scaffold"]), encoding="utf-8")
            assertion_path.write_text(
                str(proposal["assertion_scaffold"]),
                encoding="utf-8",
            )
            emitted.extend(
                [
                    {
                        "proposal_id": proposal_id,
                        "kind": "test_scaffold",
                        "path": str(test_path),
                    },
                    {
                        "proposal_id": proposal_id,
                        "kind": "assertion_scaffold",
                        "path": str(assertion_path),
                    },
                ]
            )

    destination = Path(output).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    report = {
        **result,
        "source_report": str(source_path),
        "emission_opt_in": emit_dir is not None,
        "emitted_artifacts": emitted,
    }
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return {**report, "path": str(destination)}
