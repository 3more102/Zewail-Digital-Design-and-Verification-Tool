from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from zddv.ai_provider import build_model_request, load_ai_context
from zddv.ai_response import validate_ai_response_payload
from zddv.config import ProjectConfig


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


def _project_file(
    project: ProjectConfig,
    value: str | Path,
    *,
    label: str,
) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = project.root / path
    path = path.resolve()
    root = project.root.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} must remain inside the project root: {path}") from exc
    if path == root:
        raise ValueError(f"{label} must identify a file inside the project root")
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def _project_output(
    project: ProjectConfig,
    value: str | Path,
) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = project.root / path
    path = path.resolve()
    allowed_root = (project.root / ".zddv" / "ai" / "audits").resolve()
    try:
        path.relative_to(allowed_root)
    except ValueError as exc:
        raise ValueError(
            "AI chain audit output must remain under .zddv/ai/audits"
        ) from exc
    if path == allowed_root:
        raise ValueError("AI chain audit output must identify a JSON file")
    return path


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError(f"{label} root must be a JSON object")
    return dict(payload)


def _recorded_path_matches(
    project: ProjectConfig,
    recorded: Any,
    actual: Path,
    *,
    label: str,
) -> None:
    if not isinstance(recorded, str) or not recorded:
        raise ValueError(f"{label} is missing")
    candidate = Path(recorded)
    if not candidate.is_absolute():
        candidate = project.root / candidate
    if candidate.resolve() != actual.resolve():
        raise RuntimeError(f"{label} does not match the audited artifact path")


def audit_ai_chain(
    project: ProjectConfig,
    *,
    context_path: str | Path,
    response_path: str | Path,
    validated_path: str | Path,
    review_path: str | Path | None = None,
    output: str | Path = ".zddv/ai/audits/latest.json",
) -> dict[str, Any]:
    """Verify the persisted AI RCA provenance chain without invoking any model."""

    context_file = _project_file(
        project,
        context_path,
        label="AI context path",
    )
    context = load_ai_context(project, context_file)
    evidence_sha = str(context["provenance"]["evidence_sha256"])
    request_sha = _canonical_sha256(build_model_request(context))

    response_file = _project_file(
        project,
        response_path,
        label="Raw provider response path",
    )
    response_bytes = response_file.read_bytes()
    response_sha = _sha256_bytes(response_bytes)
    raw = _load_json_object(
        response_file,
        label="raw AI provider response",
    )
    if raw.get("analysis") != "ai_provider_response_raw":
        raise ValueError("Input is not a ZDDV ai_provider_response_raw artifact")
    if raw.get("context_evidence_sha256") != evidence_sha:
        raise RuntimeError(
            "Raw provider response context evidence SHA-256 does not match the context"
        )
    if raw.get("request_sha256") != request_sha:
        raise RuntimeError(
            "Raw provider response request SHA-256 does not match the reconstructed request"
        )

    validated_file = _project_file(
        project,
        validated_path,
        label="Validated AI response path",
    )
    validated_bytes = validated_file.read_bytes()
    validated_artifact_sha = _sha256_bytes(validated_bytes)
    validated = _load_json_object(
        validated_file,
        label="validated AI response",
    )
    if validated.get("analysis") != "ai_response_validated":
        raise ValueError("Input is not a validated AI response")
    if validated.get("status") != "VALIDATED_UNREVIEWED":
        raise ValueError(
            "Validated AI response status must be VALIDATED_UNREVIEWED"
        )

    payload = validated.get("validated_payload")
    if not isinstance(payload, Mapping):
        raise ValueError("Validated AI response is missing validated_payload")
    normalized_payload = validate_ai_response_payload(
        payload,
        context=context,
    )
    if dict(payload) != normalized_payload:
        raise RuntimeError(
            "Validated AI response payload is not in canonical validated form"
        )
    validated_payload_sha = _canonical_sha256(payload)
    if validated.get("validated_payload_sha256") != validated_payload_sha:
        raise RuntimeError(
            "Validated AI response payload SHA-256 does not match its payload"
        )
    if validated.get("provider") != raw.get("provider"):
        raise RuntimeError(
            "Validated AI response provider metadata does not match the raw response"
        )

    provenance = validated.get("provenance")
    if not isinstance(provenance, Mapping):
        raise ValueError("Validated AI response is missing provenance metadata")
    if provenance.get("context_evidence_sha256") != evidence_sha:
        raise RuntimeError(
            "Validated AI response context evidence SHA-256 does not match the context"
        )
    if provenance.get("provider_request_sha256") != request_sha:
        raise RuntimeError(
            "Validated AI response provider request SHA-256 does not match the request"
        )
    if provenance.get("raw_response_sha256") != response_sha:
        raise RuntimeError(
            "Validated AI response raw-response SHA-256 does not match the raw artifact"
        )
    _recorded_path_matches(
        project,
        provenance.get("raw_response_path"),
        response_file,
        label="Validated AI raw_response_path",
    )
    _recorded_path_matches(
        project,
        provenance.get("context_path"),
        context_file,
        label="Validated AI context_path",
    )

    stages: list[dict[str, Any]] = [
        {
            "stage": "context",
            "status": "VERIFIED",
            "path": str(context_file),
            "evidence_sha256": evidence_sha,
        },
        {
            "stage": "provider-response",
            "status": "VERIFIED",
            "path": str(response_file),
            "request_sha256": request_sha,
            "artifact_sha256": response_sha,
        },
        {
            "stage": "validated-response",
            "status": "VERIFIED",
            "path": str(validated_file),
            "artifact_sha256": validated_artifact_sha,
            "validated_payload_sha256": validated_payload_sha,
        },
    ]

    review_id: str | None = None
    review_file: Path | None = None
    review_artifact_sha: str | None = None
    if review_path is not None:
        review_file = _project_file(
            project,
            review_path,
            label="AI review path",
        )
        review_bytes = review_file.read_bytes()
        review_artifact_sha = _sha256_bytes(review_bytes)
        review = _load_json_object(
            review_file,
            label="AI response review",
        )
        if review.get("analysis") != "ai_response_review":
            raise ValueError("Input is not an AI response review record")
        if review.get("status") != "APPROVED" or review.get("approved") is not True:
            raise RuntimeError("AI response review record is not approved")
        if review.get("validated_payload_sha256") != validated_payload_sha:
            raise RuntimeError(
                "AI response review does not match the validated payload SHA-256"
            )
        _recorded_path_matches(
            project,
            review.get("validated_response_path"),
            validated_file,
            label="AI review validated_response_path",
        )
        expected_proposals = len(normalized_payload.get("generated_proposals", []))
        if review.get("generated_proposals") != expected_proposals:
            raise RuntimeError(
                "AI response review generated-proposal count does not match the validated payload"
            )
        review_id = str(review.get("review_id") or "")
        if not review_id:
            raise ValueError("AI response review record is missing review_id")
        stages.append(
            {
                "stage": "human-review",
                "status": "VERIFIED",
                "path": str(review_file),
                "artifact_sha256": review_artifact_sha,
                "review_id": review_id,
            }
        )

    destination = _project_output(project, output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "schema_version": 1,
        "analysis": "ai_chain_audit",
        "status": "PASS",
        "last_verified_stage": stages[-1]["stage"],
        "stages": stages,
        "chain": {
            "context_evidence_sha256": evidence_sha,
            "provider_request_sha256": request_sha,
            "raw_response_sha256": response_sha,
            "validated_artifact_sha256": validated_artifact_sha,
            "validated_payload_sha256": validated_payload_sha,
            "review_artifact_sha256": review_artifact_sha,
            "review_id": review_id,
        },
        "policy": {
            "read_only_validation": True,
            "model_invocation": False,
            "external_transmission": False,
            "generated_artifact_staging": False,
            "project_modification": False,
            "command_execution": False,
        },
        "semantics": (
            "PASS means the supplied persisted AI artifacts form a self-consistent "
            "ZDDV provenance chain at audit time. Hash consistency is an integrity "
            "check, not an authenticity signature and not evidence that model "
            "hypotheses are correct."
        ),
    }
    destination.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**result, "path": str(destination)}
