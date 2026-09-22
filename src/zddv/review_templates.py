from __future__ import annotations

import json
from pathlib import Path


def _single_line(value: object) -> str:
    return " ".join(str(value).replace("\r", " ").replace("\n", " ").split())


def _candidate_sort_key(item: dict) -> tuple:
    rank = item.get("rank")
    numeric_rank = rank if isinstance(rank, int) and not isinstance(rank, bool) else 1_000_000_000
    return (
        numeric_rank,
        str(item.get("coverage_type") or ""),
        str(item.get("hole_name") or ""),
    )


def build_reviewable_test_template(
    report: dict,
    *,
    limit: int | None = None,
) -> dict:
    """Render inert SystemVerilog review scaffolds from explicit coverage suggestions."""
    suggestions = report.get("suggestions")
    if not isinstance(suggestions, list):
        raise ValueError("Coverage-suggestion report must contain a 'suggestions' list")

    normalized: list[dict] = []
    for suggestion in suggestions:
        if not isinstance(suggestion, dict):
            raise ValueError("Each coverage suggestion must be a JSON object")
        if suggestion.get("review_required") is not True:
            raise ValueError(
                "Refusing to template a suggestion that is not explicitly review-required"
            )
        if suggestion.get("auto_execute") is not False:
            raise ValueError(
                "Refusing to template a suggestion that is not explicitly non-executing"
            )
        normalized.append(suggestion)

    normalized.sort(key=_candidate_sort_key)
    selected = normalized if limit is None else normalized[: max(0, limit)]

    candidates: list[dict] = []
    lines = [
        "// ZDDV GENERATED REVIEW TEMPLATE",
        "//",
        "// This .svt file is intentionally outside normal SystemVerilog source globs.",
        "// It contains inert task scaffolds only: no initial blocks, no DUT stimulus,",
        "// no assertions, and no automatic execution.",
        "// Review every candidate and add design-aware stimulus/checking manually",
        "// before copying any accepted code into the verification environment.",
        "",
        "package zddv_generated_coverage_tests;",
        "",
    ]

    for index, suggestion in enumerate(selected, start=1):
        candidate_id = f"zddv_cov_candidate_{index:03d}"
        coverage_type = str(suggestion.get("coverage_type") or "unknown")
        hole_name = str(suggestion.get("hole_name") or "")
        intent = str(suggestion.get("intent") or "")
        evidence = suggestion.get("evidence")
        if not isinstance(evidence, dict):
            evidence = {}
        source_rank = suggestion.get("rank")

        candidate = {
            "id": candidate_id,
            "kind": "systemverilog_test_template",
            "source_rank": source_rank,
            "coverage_type": coverage_type,
            "hole_name": hole_name,
            "intent": intent,
            "evidence": evidence,
            "review_required": True,
            "auto_execute": False,
            "default_enabled": False,
        }
        candidates.append(candidate)

        evidence_text = json.dumps(
            evidence,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        lines.extend(
            [
                f"  // Candidate: {candidate_id}",
                f"  // Source suggestion rank: {_single_line(source_rank)}",
                f"  // Coverage type: {_single_line(coverage_type)}",
                f"  // Hole: {_single_line(hole_name)}",
                f"  // Objective: {_single_line(intent)}",
                f"  // Evidence: {_single_line(evidence_text)}",
                "  // REVIEW REQUIRED: add only legal, design-aware stimulus and checks.",
                f"  task automatic {candidate_id}();",
                "    // TODO(REVIEW): drive the explicit coverage objective.",
                "    // TODO(REVIEW): add self-checking expected behavior.",
                "  endtask",
                "",
            ]
        )

    lines.append("endpackage")
    lines.append("")

    return {
        "source_suggestion_count": int(
            report.get("suggestion_count", len(normalized))
        ),
        "reported_suggestion_count": len(normalized),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "review_required": True,
        "auto_execute": False,
        "default_enabled": False,
        "format": "systemverilog-review-template",
        "required_extension": ".svt",
        "template": "\n".join(lines),
        "semantics": (
            "Generated content is an inert review scaffold derived only from explicit "
            "coverage-test suggestions. It contains no inferred DUT behavior, no "
            "automatic execution, and must be explicitly materialized before review."
        ),
    }


def preview_reviewable_test_template(
    source: str | Path,
    *,
    limit: int | None = None,
) -> dict:
    source_path = Path(source).resolve()
    if not source_path.is_file():
        raise RuntimeError(
            f"Coverage-suggestion report not found at {source_path}. "
            "Run 'zddv coverage-suggest' first."
        )
    try:
        report = json.loads(source_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Coverage-suggestion report is not valid JSON: {source_path}"
        ) from exc
    if not isinstance(report, dict):
        raise ValueError("Coverage-suggestion report root must be a JSON object")

    result = build_reviewable_test_template(report, limit=limit)
    return {**result, "source_report": str(source_path)}


def write_reviewable_test_template(
    source: str | Path,
    output: str | Path,
    *,
    limit: int | None = None,
) -> dict:
    destination = Path(output).resolve()
    if destination.suffix.lower() != ".svt":
        raise ValueError(
            "Review templates must use the .svt extension so they are not "
            "accidentally included in normal SystemVerilog source globs"
        )

    result = preview_reviewable_test_template(source, limit=limit)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(result["template"], encoding="utf-8")
    return {**result, "path": str(destination)}
