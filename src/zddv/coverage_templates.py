from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any


_VALID_KINDS = {"test", "assertion", "both"}


def _slug(value: str, *, limit: int = 64) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    if not slug:
        slug = "coverage_hole"
    return slug[:limit].rstrip("_") or "coverage_hole"


def _comment_block(value: str) -> list[str]:
    lines = value.splitlines() or [""]
    return [f"// {line}" if line else "//" for line in lines]


def _validate_suggestion(item: dict[str, Any]) -> None:
    if item.get("review_required") is not True:
        raise ValueError(
            "Coverage template generation requires review_required=true on every suggestion"
        )
    if item.get("auto_execute") is not False:
        raise ValueError(
            "Coverage template generation requires auto_execute=false on every suggestion"
        )
    for key in ("coverage_type", "hole_name", "intent"):
        if not isinstance(item.get(key), str) or not item[key]:
            raise ValueError(f"Coverage suggestion must contain a non-empty '{key}'")


def _header(
    *,
    title: str,
    template_id: str,
    suggestion: dict[str, Any],
) -> list[str]:
    evidence = json.dumps(
        suggestion.get("evidence") or {},
        indent=2,
        sort_keys=True,
    )
    lines = [
        f"// {title}",
        "//",
        "// ZDDV review-only generated artifact.",
        "// review_required=true",
        "// auto_execute=false",
        "// This file is intentionally inert: every line is a comment.",
        "// Rename or copy content into executable verification code only after manual review.",
        f"// template_id={template_id}",
        f"// source_suggestion_rank={suggestion.get('rank')}",
        f"// coverage_type={suggestion['coverage_type']}",
        f"// hole_name={suggestion['hole_name']}",
        "//",
        "// Coverage intent:",
    ]
    lines.extend(_comment_block(str(suggestion["intent"])))
    lines.extend(["//", "// Retained evidence (JSON):"])
    lines.extend(_comment_block(evidence))
    return lines


def _render_test_template(
    template_id: str,
    suggestion: dict[str, Any],
) -> str:
    lines = _header(
        title="ZDDV COVERAGE TEST TEMPLATE",
        template_id=template_id,
        suggestion=suggestion,
    )
    lines.extend(
        [
            "//",
            "// REVIEW TASKS:",
            "// 1. Inspect the referenced RTL/testbench and confirm the intended reachable scenario.",
            "// 2. Reuse the project's existing test or UVM base classes; ZDDV does not guess them.",
            "// 3. Add only stimulus justified by the design/specification and retained evidence.",
            "// 4. Add an explicit self-check and completion criterion before enabling the test.",
            "// 5. Keep seed/plusarg requirements explicit and reproducible.",
            "//",
            "// Suggested inert shape:",
            f"// class {template_id}_test extends <EXISTING_BASE_TEST>;",
            f"//   `uvm_component_utils({template_id}_test)",
            "//   task run_phase(uvm_phase phase);",
            "//     phase.raise_objection(this);",
            "//     // TODO(REVIEW): evidence-backed stimulus targeting the retained coverage hole.",
            "//     // TODO(REVIEW): self-check / scoreboard expectation.",
            "//     phase.drop_objection(this);",
            "//   endtask",
            "// endclass",
            "//",
            "// No executable stimulus was generated.",
        ]
    )
    return "\n".join(lines) + "\n"


def _render_assertion_template(
    template_id: str,
    suggestion: dict[str, Any],
) -> str:
    lines = _header(
        title="ZDDV ASSERTION / COVER PROPERTY TEMPLATE",
        template_id=template_id,
        suggestion=suggestion,
    )
    lines.extend(
        [
            "//",
            "// REVIEW TASKS:",
            "// 1. Confirm whether this hole is best targeted by cover property, assert property, or a test.",
            "// 2. Supply the real clocking event and reset semantics from the RTL/specification.",
            "// 3. Derive the predicate only from verified design behavior; ZDDV does not invent one.",
            "// 4. Bind the property at a manually reviewed hierarchy location.",
            "//",
            "// Suggested inert shape:",
            f"// property {template_id}_p;",
            "//   @(<CLOCKING_EVENT>) disable iff (<RESET_CONDITION>)",
            "//     <EVIDENCE_BACKED_PREDICATE>;",
            "// endproperty",
            f"// cover property ({template_id}_p);",
            "// // Or, only when justified by the specification:",
            f"// // assert property ({template_id}_p);",
            "//",
            "// No executable property was generated.",
        ]
    )
    return "\n".join(lines) + "\n"


def build_coverage_review_templates(
    report: dict[str, Any],
    *,
    kind: str = "both",
    limit: int | None = None,
) -> dict[str, Any]:
    """Build deterministic, inert review templates from coverage-test suggestions."""

    if kind not in _VALID_KINDS:
        choices = ", ".join(sorted(_VALID_KINDS))
        raise ValueError(f"kind must be one of: {choices}")

    suggestions = report.get("suggestions")
    if not isinstance(suggestions, list):
        raise ValueError("Coverage suggestion report must contain a 'suggestions' list")

    normalized: list[dict[str, Any]] = []
    for item in suggestions:
        if not isinstance(item, dict):
            raise ValueError("Each coverage suggestion must be a JSON object")
        _validate_suggestion(item)
        normalized.append(item)

    def _rank_key(item: dict[str, Any]) -> tuple[int, str, str]:
        rank = item.get("rank")
        numeric_rank = rank if isinstance(rank, int) else 1_000_000_000
        return (
            numeric_rank,
            str(item["coverage_type"]),
            str(item["hole_name"]),
        )

    normalized.sort(key=_rank_key)
    selected = normalized if limit is None else normalized[: max(0, limit)]

    templates: list[dict[str, Any]] = []
    for index, suggestion in enumerate(selected, start=1):
        source_rank = suggestion.get("rank")
        prefix = f"coverage_{index:03d}_{_slug(str(suggestion['coverage_type']), limit=20)}"
        suffix = _slug(str(suggestion["hole_name"]), limit=48)
        template_id = f"{prefix}_{suffix}"

        common = {
            "template_id": template_id,
            "source_suggestion_rank": source_rank,
            "coverage_type": suggestion["coverage_type"],
            "hole_name": suggestion["hole_name"],
            "intent": suggestion["intent"],
            "evidence": suggestion.get("evidence") or {},
            "review_required": True,
            "auto_execute": False,
        }

        if kind in {"test", "both"}:
            templates.append(
                {
                    **common,
                    "kind": "test",
                    "relative_path": f"tests/{template_id}_test.sv.template",
                    "content": _render_test_template(template_id, suggestion),
                }
            )
        if kind in {"assertion", "both"}:
            templates.append(
                {
                    **common,
                    "kind": "assertion",
                    "relative_path": (
                        f"assertions/{template_id}_property.sv.template"
                    ),
                    "content": _render_assertion_template(template_id, suggestion),
                }
            )

    return {
        "schema_version": 1,
        "analysis": "coverage_review_templates",
        "source_suggestion_count": len(normalized),
        "selected_suggestion_count": len(selected),
        "template_count": len(templates),
        "kind": kind,
        "review_required": True,
        "auto_execute": False,
        "templates": templates,
        "semantics": (
            "Templates are deterministic, comment-only review artifacts derived from "
            "explicit coverage-test suggestions. ZDDV does not invent DUT behavior, "
            "clock/reset semantics, hierarchy bindings, predicates, stimulus, or "
            "self-checks, and it never compiles or executes these templates automatically."
        ),
    }


def write_coverage_review_templates(
    source: str | Path,
    output_dir: str | Path,
    *,
    kind: str = "both",
    limit: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Write inert review templates plus a manifest, without enabling them."""

    source_path = Path(source).resolve()
    if not source_path.is_file():
        raise RuntimeError(
            f"Coverage suggestion report not found at {source_path}. "
            "Run 'zddv coverage-suggest' first."
        )

    try:
        report = json.loads(source_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Coverage suggestion report is not valid JSON: {source_path}"
        ) from exc
    if not isinstance(report, dict):
        raise ValueError("Coverage suggestion report root must be a JSON object")

    result = build_coverage_review_templates(report, kind=kind, limit=limit)

    destination_root = Path(output_dir).resolve()
    manifest_path = destination_root / "manifest.json"
    destinations = [
        destination_root / item["relative_path"]
        for item in result["templates"]
    ]
    planned = [*destinations, manifest_path]
    collisions = [path for path in planned if path.exists()]
    if collisions and not force:
        names = ", ".join(str(path) for path in collisions[:5])
        if len(collisions) > 5:
            names += f", ... (+{len(collisions) - 5} more)"
        raise RuntimeError(
            "Refusing to overwrite existing generated review artifact(s): "
            f"{names}. Re-run with --force only after reviewing those files."
        )

    for item, path in zip(result["templates"], destinations):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(item["content"], encoding="utf-8")

    manifest_templates = [
        {key: value for key, value in item.items() if key != "content"}
        for item in result["templates"]
    ]
    payload = {
        **{key: value for key, value in result.items() if key != "templates"},
        "source_report": str(source_path),
        "output_dir": str(destination_root),
        "templates": manifest_templates,
    }
    destination_root.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        **payload,
        "manifest_path": str(manifest_path),
    }
