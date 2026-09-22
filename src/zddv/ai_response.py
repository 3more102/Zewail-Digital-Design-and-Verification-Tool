from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from zddv.ai_provider import (
    build_model_request,
    load_ai_context,
    model_evidence_references,
)
from zddv.config import ProjectConfig
from zddv.generated_artifacts import normalize_generation_proposal


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REVIEW_ID_RE = re.compile(r"^review-[0-9a-f]{16}$")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _project_path(project: ProjectConfig, value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = project.root / path
    path = path.resolve()
    root = project.root.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path must remain inside the project root: {path}") from exc
    if path == root:
        raise ValueError("Path must identify a file inside the project root")
    return path


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError(f"{label} root must be a JSON object")
    return dict(payload)


def _validate_string(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _validate_string_list(
    value: Any,
    *,
    label: str,
    allow_empty: bool,
) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    if not allow_empty and not value:
        raise ValueError(f"{label} must not be empty")
    result: list[str] = []
    for index, item in enumerate(value, start=1):
        result.append(_validate_string(item, label=f"{label}[{index}]"))
    if len(set(result)) != len(result):
        raise ValueError(f"{label} must not contain duplicate entries")
    return result


def _validate_evidence_refs(
    value: Any,
    *,
    label: str,
    allowed: set[str],
) -> list[str]:
    refs = _validate_string_list(value, label=label, allow_empty=False)
    unknown = [ref for ref in refs if ref not in allowed]
    if unknown:
        raise ValueError(
            f"{label} contains unknown evidence reference(s): "
            + ", ".join(unknown)
        )
    return refs


def _validate_statement_items(
    value: Any,
    *,
    label: str,
    statement_key: str,
    allowed_refs: set[str],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"{label}[{index}] must be an object")
        unknown_keys = set(item) - {statement_key, "evidence_refs"}
        if unknown_keys:
            raise ValueError(
                f"{label}[{index}] has unsupported field(s): "
                + ", ".join(sorted(unknown_keys))
            )
        statement = _validate_string(
            item.get(statement_key),
            label=f"{label}[{index}].{statement_key}",
        )
        refs = _validate_evidence_refs(
            item.get("evidence_refs"),
            label=f"{label}[{index}].evidence_refs",
            allowed=allowed_refs,
        )
        normalized.append(
            {statement_key: statement, "evidence_refs": refs}
        )
    return normalized


def _validate_generated_proposals(
    value: Any,
    *,
    allowed_refs: set[str],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("generated_proposals must be a list")

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(
                f"generated_proposals[{index}] must be an object"
            )
        unknown_keys = set(item) - {
            "kind",
            "language",
            "name",
            "content",
            "target_path",
            "evidence_refs",
        }
        if unknown_keys:
            raise ValueError(
                f"generated_proposals[{index}] has unsupported field(s): "
                + ", ".join(sorted(unknown_keys))
            )
        refs = _validate_evidence_refs(
            item.get("evidence_refs"),
            label=f"generated_proposals[{index}].evidence_refs",
            allowed=allowed_refs,
        )
        proposal = normalize_generation_proposal(
            {
                "kind": item.get("kind"),
                "language": item.get("language"),
                "name": item.get("name"),
                "content": item.get("content"),
                "target_path": item.get("target_path"),
                "source": "ai-validated-unreviewed",
                "evidence": {"evidence_refs": refs},
            }
        )
        normalized.append(
            {
                "kind": proposal["kind"],
                "language": proposal["language"],
                "name": proposal["name"],
                "content": proposal["content"],
                "target_path": proposal["target_path"],
                "evidence_refs": refs,
            }
        )
    return normalized


def validate_ai_response_payload(
    payload: Mapping[str, Any],
    *,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    allowed_top = {
        "schema_version",
        "analysis",
        "observed_facts",
        "hypotheses",
        "unknowns",
        "next_checks",
        "generated_proposals",
    }
    unknown_top = set(payload) - allowed_top
    if unknown_top:
        raise ValueError(
            "AI response has unsupported top-level field(s): "
            + ", ".join(sorted(unknown_top))
        )
    if payload.get("schema_version") != 1:
        raise ValueError("AI response schema_version must be 1")
    if payload.get("analysis") != "zddv_ai_rca_response":
        raise ValueError(
            "AI response analysis must be 'zddv_ai_rca_response'"
        )

    allowed_refs = set(model_evidence_references(context))
    observed_facts = _validate_statement_items(
        payload.get("observed_facts"),
        label="observed_facts",
        statement_key="statement",
        allowed_refs=allowed_refs,
    )
    hypotheses = _validate_statement_items(
        payload.get("hypotheses"),
        label="hypotheses",
        statement_key="statement",
        allowed_refs=allowed_refs,
    )
    unknowns = _validate_string_list(
        payload.get("unknowns"),
        label="unknowns",
        allow_empty=True,
    )
    next_checks = _validate_statement_items(
        payload.get("next_checks"),
        label="next_checks",
        statement_key="description",
        allowed_refs=allowed_refs,
    )
    generated_proposals = _validate_generated_proposals(
        payload.get("generated_proposals"),
        allowed_refs=allowed_refs,
    )

    return {
        "schema_version": 1,
        "analysis": "zddv_ai_rca_response",
        "observed_facts": observed_facts,
        "hypotheses": hypotheses,
        "unknowns": unknowns,
        "next_checks": next_checks,
        "generated_proposals": generated_proposals,
    }


def ingest_ai_provider_response(
    project: ProjectConfig,
    *,
    response_path: str | Path,
    context_path: str | Path,
    output: str | Path = ".zddv/ai/validated-response.json",
) -> dict[str, Any]:
    response_file = _project_path(project, response_path)
    if not response_file.is_file():
        raise FileNotFoundError(response_file)
    raw_response_bytes = response_file.read_bytes()
    raw_artifact = _load_json_object(
        response_file,
        label="raw AI provider response",
    )
    if raw_artifact.get("analysis") != "ai_provider_response_raw":
        raise ValueError(
            "Input is not a ZDDV ai_provider_response_raw artifact"
        )

    policy = raw_artifact.get("policy")
    if not isinstance(policy, Mapping):
        raise ValueError("Raw provider response is missing policy metadata")
    required_policy = {
        "untrusted_model_output": True,
        "response_schema_validated": False,
        "automatic_generated_artifact_staging": False,
        "automatic_command_execution": False,
        "human_review_required": True,
    }
    for key, expected in required_policy.items():
        if policy.get(key) is not expected:
            raise ValueError(
                f"Raw provider response violates required policy field: {key}"
            )

    context = load_ai_context(project, context_path)
    evidence_sha = context["provenance"]["evidence_sha256"]
    if raw_artifact.get("context_evidence_sha256") != evidence_sha:
        raise ValueError(
            "Raw provider response context evidence SHA-256 does not match "
            "the supplied AI context"
        )

    expected_request_sha = _canonical_sha256(build_model_request(context))
    if raw_artifact.get("request_sha256") != expected_request_sha:
        raise ValueError(
            "Raw provider response request SHA-256 does not match the "
            "supplied AI context"
        )

    provider_response = raw_artifact.get("response")
    if not isinstance(provider_response, Mapping):
        raise ValueError("Raw provider response payload must be an object")
    content = provider_response.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError(
            "Raw provider response must contain non-empty response.content"
        )
    try:
        decoded = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "Model response content must be a strict JSON object; "
            "markdown fences or free-form prose are not accepted"
        ) from exc
    if not isinstance(decoded, Mapping):
        raise ValueError("Model response content JSON root must be an object")

    validated_payload = validate_ai_response_payload(
        decoded,
        context=context,
    )
    validated_sha256 = _canonical_sha256(validated_payload)

    destination = _project_path(project, output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "schema_version": 1,
        "analysis": "ai_response_validated",
        "status": "VALIDATED_UNREVIEWED",
        "provider": raw_artifact.get("provider"),
        "validated_payload": validated_payload,
        "validated_payload_sha256": validated_sha256,
        "provenance": {
            "context_evidence_sha256": evidence_sha,
            "provider_request_sha256": expected_request_sha,
            "raw_response_sha256": _sha256_bytes(raw_response_bytes),
            "raw_response_path": str(response_file),
            "context_path": str(_project_path(project, context_path)),
        },
        "policy": {
            "schema_validated": True,
            "human_review_required": True,
            "approved": False,
            "automatic_generated_artifact_staging": False,
            "automatic_project_modification": False,
            "automatic_command_execution": False,
        },
        "semantics": (
            "The model response matched ZDDV's strict response schema and all "
            "evidence references resolve to the supplied deterministic context. "
            "Schema validation is not human approval and does not establish that "
            "a hypothesis is correct."
        ),
    }
    destination.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**result, "path": str(destination)}


def review_validated_ai_response(
    project: ProjectConfig,
    validated_path: str | Path,
    *,
    expected_sha256: str,
    approve_reviewed: bool,
    output_root: str | Path = ".zddv/ai/reviews",
) -> dict[str, Any]:
    if not approve_reviewed:
        raise RuntimeError(
            "AI response review requires explicit --approve-reviewed opt-in"
        )
    expected = str(expected_sha256 or "").strip().lower()
    if not _SHA256_RE.fullmatch(expected):
        raise ValueError(
            "expected_sha256 must be a 64-character lowercase SHA-256"
        )

    validated_file = _project_path(project, validated_path)
    if not validated_file.is_file():
        raise FileNotFoundError(validated_file)
    artifact = _load_json_object(
        validated_file,
        label="validated AI response",
    )
    if artifact.get("analysis") != "ai_response_validated":
        raise ValueError("Input is not a validated AI response")
    if artifact.get("status") != "VALIDATED_UNREVIEWED":
        raise ValueError(
            "Validated AI response status must be VALIDATED_UNREVIEWED"
        )

    payload = artifact.get("validated_payload")
    if not isinstance(payload, Mapping):
        raise ValueError("Validated AI response is missing validated_payload")
    actual = _canonical_sha256(payload)
    stored = str(artifact.get("validated_payload_sha256") or "").lower()
    if actual != stored:
        raise RuntimeError(
            "Validated AI response payload changed after validation"
        )
    if actual != expected:
        raise RuntimeError(
            "Reviewed SHA-256 does not match the validated AI response"
        )

    review_id = f"review-{actual[:16]}"
    root = _project_path(project, output_root)
    allowed_root = (project.root / ".zddv" / "ai" / "reviews").resolve()
    try:
        root.relative_to(allowed_root)
    except ValueError as exc:
        raise ValueError(
            "AI review records must remain under .zddv/ai/reviews"
        ) from exc
    root.mkdir(parents=True, exist_ok=True)
    review_path = root / f"{review_id}.json"

    result = {
        "schema_version": 1,
        "analysis": "ai_response_review",
        "review_id": review_id,
        "status": "APPROVED",
        "approved": True,
        "validated_response_path": str(validated_file),
        "validated_payload_sha256": actual,
        "generated_proposals": len(
            payload.get("generated_proposals", [])
        ),
        "policy": {
            "human_review_recorded": True,
            "automatic_generated_artifact_staging": False,
            "automatic_project_modification": False,
            "automatic_command_execution": False,
        },
        "semantics": (
            "Approval records that a human reviewed the exact schema-validated "
            "payload identified by SHA-256. It does not automatically export, "
            "stage, apply, compile, or execute generated verification code."
        ),
    }
    review_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**result, "path": str(review_path)}


def export_reviewed_generation_proposal(
    project: ProjectConfig,
    review_path: str | Path,
    *,
    proposal_index: int,
    output: str | Path | None = None,
) -> dict[str, Any]:
    if proposal_index < 1:
        raise ValueError("proposal_index must be >= 1")

    review_file = _project_path(project, review_path)
    if not review_file.is_file():
        raise FileNotFoundError(review_file)
    review = _load_json_object(review_file, label="AI review record")
    if review.get("analysis") != "ai_response_review":
        raise ValueError("Input is not an AI response review record")
    if review.get("status") != "APPROVED" or review.get("approved") is not True:
        raise ValueError("AI response review record is not approved")

    review_id = str(review.get("review_id") or "")
    if not _REVIEW_ID_RE.fullmatch(review_id):
        raise ValueError("AI response review record has an invalid review_id")

    validated_path = review.get("validated_response_path")
    if not isinstance(validated_path, str) or not validated_path:
        raise ValueError(
            "AI response review record is missing validated_response_path"
        )
    validated_file = _project_path(project, validated_path)
    if not validated_file.is_file():
        raise FileNotFoundError(validated_file)
    validated = _load_json_object(
        validated_file,
        label="validated AI response",
    )
    payload = validated.get("validated_payload")
    if not isinstance(payload, Mapping):
        raise ValueError("Validated AI response is missing validated_payload")
    actual_sha = _canonical_sha256(payload)
    if actual_sha != review.get("validated_payload_sha256"):
        raise RuntimeError(
            "Approved AI review no longer matches the validated payload"
        )

    proposals = payload.get("generated_proposals")
    if not isinstance(proposals, list):
        raise ValueError(
            "Validated AI response generated_proposals must be a list"
        )
    if proposal_index > len(proposals):
        raise IndexError(
            f"proposal_index {proposal_index} exceeds available proposals "
            f"({len(proposals)})"
        )
    selected = proposals[proposal_index - 1]
    if not isinstance(selected, Mapping):
        raise ValueError("Selected generated proposal is invalid")

    proposal = normalize_generation_proposal(
        {
            "kind": selected.get("kind"),
            "language": selected.get("language"),
            "name": selected.get("name"),
            "content": selected.get("content"),
            "target_path": selected.get("target_path"),
            "source": "ai-reviewed-response",
            "evidence": {
                "evidence_refs": selected.get("evidence_refs", []),
                "review_id": review_id,
                "proposal_index": proposal_index,
                "validated_payload_sha256": actual_sha,
                "validated_response_path": str(validated_file),
            },
        }
    )

    if output is None:
        safe_name = proposal["name"].replace(".", "_")
        output = (
            f".zddv/ai/proposals/{review_id}-"
            f"{proposal_index:02d}-{safe_name}.json"
        )
    destination = _project_path(project, output)
    allowed_root = (project.root / ".zddv" / "ai" / "proposals").resolve()
    try:
        destination.relative_to(allowed_root)
    except ValueError as exc:
        raise ValueError(
            "Reviewed AI proposals must remain under .zddv/ai/proposals"
        ) from exc
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(proposal, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "schema_version": 1,
        "analysis": "ai_reviewed_proposal_export",
        "status": "EXPORTED_FOR_STAGING",
        "review_id": review_id,
        "proposal_index": proposal_index,
        "proposal": proposal,
        "path": str(destination),
        "policy": {
            "human_review_recorded": True,
            "automatic_stage": False,
            "automatic_apply": False,
            "automatic_execution": False,
        },
        "semantics": (
            "The approved proposal was exported as a normal ZDDV generation "
            "proposal. It is not yet staged; generated-stage remains a separate "
            "review-isolated action."
        ),
    }
