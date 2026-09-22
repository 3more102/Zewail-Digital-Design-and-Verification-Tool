from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from zddv.ai_contract import model_response_contract
from zddv.ai_provider import load_ai_context
from zddv.config import ProjectConfig


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_HYPOTHESES = 50
_MAX_REFS_PER_HYPOTHESIS = 20
_MAX_TEXT_ITEMS = 50
_MAX_TEXT_LENGTH = 4000


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    return dict(value)


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    text = value.strip()
    if len(text) > _MAX_TEXT_LENGTH:
        raise ValueError(f"{label} exceeds {_MAX_TEXT_LENGTH} characters")
    return text


def _text_list(value: Any, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON array")
    if len(value) > _MAX_TEXT_ITEMS:
        raise ValueError(f"{label} exceeds {_MAX_TEXT_ITEMS} items")
    return [_require_text(item, f"{label}[{index}]") for index, item in enumerate(value)]


def _allowed_keys(payload: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"{label} contains unsupported field(s): {', '.join(unknown)}")


def _candidate_by_rank(context: Mapping[str, Any], rank: int) -> Mapping[str, Any]:
    evidence = _require_mapping(context.get("evidence"), "context.evidence")
    candidates = evidence.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("context.evidence.candidates must be a JSON array")
    for item in candidates:
        if isinstance(item, Mapping) and item.get("rank") == rank:
            return item
    raise ValueError(f"evidence reference candidate rank does not exist: {rank}")


def _probe_by_rank(context: Mapping[str, Any], rank: int) -> Mapping[str, Any]:
    evidence = _require_mapping(context.get("evidence"), "context.evidence")
    probes = evidence.get("debug_probe_suggestions")
    if not isinstance(probes, list):
        raise ValueError("context.evidence.debug_probe_suggestions must be a JSON array")
    for item in probes:
        if isinstance(item, Mapping) and item.get("rank") == rank:
            return item
    raise ValueError(f"evidence reference debug_probe rank does not exist: {rank}")


def _resolve_evidence_ref(
    ref: Mapping[str, Any],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    ref_obj = dict(ref)
    kind = _require_text(ref_obj.get("kind"), "evidence_ref.kind")
    evidence = _require_mapping(context.get("evidence"), "context.evidence")

    if kind == "candidate":
        _allowed_keys(ref_obj, {"kind", "rank"}, "candidate evidence_ref")
        rank = ref_obj.get("rank")
        if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
            raise ValueError("candidate evidence_ref.rank must be an integer >= 1")
        candidate = _candidate_by_rank(context, rank)
        return {
            "kind": "candidate",
            "rank": rank,
            "candidate_kind": candidate.get("kind"),
            "subject": candidate.get("subject"),
            "evidence_score": candidate.get("evidence_score"),
        }

    if kind == "debug_probe":
        _allowed_keys(ref_obj, {"kind", "rank"}, "debug_probe evidence_ref")
        rank = ref_obj.get("rank")
        if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
            raise ValueError("debug_probe evidence_ref.rank must be an integer >= 1")
        probe = _probe_by_rank(context, rank)
        return {
            "kind": "debug_probe",
            "rank": rank,
            "signal": probe.get("signal"),
            "source_candidate_rank": probe.get("source_candidate_rank"),
        }

    if kind == "failure_signature":
        _allowed_keys(ref_obj, {"kind"}, "failure_signature evidence_ref")
        signature = evidence.get("failure_signature")
        if not isinstance(signature, str) or not signature:
            raise ValueError("context has no failure_signature to reference")
        return {"kind": "failure_signature", "value": signature}

    if kind == "limitation":
        _allowed_keys(ref_obj, {"kind", "index"}, "limitation evidence_ref")
        index = ref_obj.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise ValueError("limitation evidence_ref.index must be an integer >= 0")
        limitations = evidence.get("limitations")
        if not isinstance(limitations, list) or index >= len(limitations):
            raise ValueError(f"evidence reference limitation index does not exist: {index}")
        return {"kind": "limitation", "index": index, "value": limitations[index]}

    raise ValueError(
        "unsupported evidence_ref.kind: "
        f"{kind}; expected candidate, debug_probe, failure_signature, or limitation"
    )


def _parse_model_content(raw_response: Mapping[str, Any]) -> dict[str, Any]:
    response = _require_mapping(raw_response.get("response"), "raw response.response")
    content = response.get("content")
    if isinstance(content, Mapping):
        return dict(content)
    if not isinstance(content, str):
        raise ValueError(
            "raw provider response must expose model output as response.content"
        )
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("model response.content is not valid JSON") from exc
    return _require_mapping(parsed, "model response.content")


def validate_model_response(
    payload: Mapping[str, Any],
    *,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    model = dict(payload)
    _allowed_keys(
        model,
        {
            "schema_version",
            "analysis",
            "context_evidence_sha256",
            "hypotheses",
            "overall_unknowns",
            "next_checks",
        },
        "model response",
    )
    if model.get("schema_version") != 1:
        raise ValueError("unsupported model response schema_version")
    if model.get("analysis") != "zddv_ai_rca_response":
        raise ValueError("model response analysis must be zddv_ai_rca_response")

    context_sha = str(
        _require_mapping(context.get("provenance"), "context.provenance").get(
            "evidence_sha256"
        )
        or ""
    ).lower()
    response_sha = str(model.get("context_evidence_sha256") or "").lower()
    if not _SHA256_RE.fullmatch(response_sha):
        raise ValueError("model response context_evidence_sha256 is invalid")
    if response_sha != context_sha:
        raise ValueError("model response references a different evidence SHA-256")

    hypotheses = model.get("hypotheses")
    if not isinstance(hypotheses, list):
        raise ValueError("model response hypotheses must be a JSON array")
    if len(hypotheses) > _MAX_HYPOTHESES:
        raise ValueError(f"model response exceeds {_MAX_HYPOTHESES} hypotheses")

    normalized_hypotheses: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    total_refs = 0
    for index, item in enumerate(hypotheses):
        hypothesis = _require_mapping(item, f"hypotheses[{index}]")
        _allowed_keys(
            hypothesis,
            {"id", "summary", "evidence_refs", "unknowns", "next_checks"},
            f"hypotheses[{index}]",
        )
        hypothesis_id = _require_text(hypothesis.get("id"), f"hypotheses[{index}].id")
        if hypothesis_id in seen_ids:
            raise ValueError(f"duplicate hypothesis id: {hypothesis_id}")
        seen_ids.add(hypothesis_id)
        summary = _require_text(
            hypothesis.get("summary"),
            f"hypotheses[{index}].summary",
        )
        refs = hypothesis.get("evidence_refs")
        if not isinstance(refs, list) or not refs:
            raise ValueError(
                f"hypotheses[{index}].evidence_refs must be a non-empty JSON array"
            )
        if len(refs) > _MAX_REFS_PER_HYPOTHESIS:
            raise ValueError(
                f"hypotheses[{index}].evidence_refs exceeds "
                f"{_MAX_REFS_PER_HYPOTHESIS} items"
            )
        resolved = [
            _resolve_evidence_ref(
                _require_mapping(ref, f"hypotheses[{index}].evidence_refs[{ref_index}]"),
                context,
            )
            for ref_index, ref in enumerate(refs)
        ]
        total_refs += len(resolved)
        normalized_hypotheses.append(
            {
                "id": hypothesis_id,
                "summary": summary,
                "evidence_refs": [dict(ref) for ref in refs],
                "resolved_evidence": resolved,
                "unknowns": _text_list(
                    hypothesis.get("unknowns"),
                    f"hypotheses[{index}].unknowns",
                ),
                "next_checks": _text_list(
                    hypothesis.get("next_checks"),
                    f"hypotheses[{index}].next_checks",
                ),
            }
        )

    return {
        "schema_version": 1,
        "analysis": "zddv_ai_rca_response",
        "context_evidence_sha256": context_sha,
        "hypotheses": normalized_hypotheses,
        "overall_unknowns": _text_list(
            model.get("overall_unknowns"),
            "overall_unknowns",
        ),
        "next_checks": _text_list(model.get("next_checks"), "next_checks"),
        "summary": {
            "hypotheses": len(normalized_hypotheses),
            "resolved_evidence_refs": total_refs,
        },
    }


def ingest_provider_response(
    project: ProjectConfig,
    *,
    raw_response_path: str | Path = ".zddv/ai/provider-response.json",
    context_path: str | Path = ".zddv/debug/ai-rca-context.json",
    output: str | Path = ".zddv/ai/validated-response.json",
) -> dict[str, Any]:
    context = load_ai_context(project, context_path)
    context_sha = str(context["provenance"]["evidence_sha256"]).lower()

    raw_path = _project_path(project, raw_response_path)
    if not raw_path.is_file():
        raise FileNotFoundError(raw_path)
    try:
        raw_payload = json.loads(raw_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"raw provider response is not valid JSON: {raw_path}") from exc
    raw = _require_mapping(raw_payload, "raw provider response")
    if raw.get("analysis") != "ai_provider_response_raw":
        raise ValueError("input is not a ZDDV ai_provider_response_raw artifact")
    raw_context_sha = str(raw.get("context_evidence_sha256") or "").lower()
    if raw_context_sha != context_sha:
        raise ValueError("raw provider response does not match the supplied AI context")

    model_payload = _parse_model_content(raw)
    validated = validate_model_response(model_payload, context=context)

    result = {
        "schema_version": 1,
        "analysis": "ai_provider_response_validated",
        "project": project.name,
        "provider": raw.get("provider"),
        "request_sha256": raw.get("request_sha256"),
        "context_evidence_sha256": context_sha,
        "validated_response": validated,
        "response_contract": model_response_contract(),
        "policy": {
            "response_schema_validated": True,
            "evidence_references_resolved": True,
            "model_output_remains_untrusted": True,
            "automatic_generated_artifact_staging": False,
            "automatic_command_execution": False,
            "human_review_required": True,
        },
        "semantics": (
            "Schema validation proves only that model output matches the ZDDV response "
            "contract and references evidence that exists in the reviewed context. "
            "It does not make model hypotheses factual or approved for action."
        ),
        "provenance": {
            "raw_response_sha256": _canonical_sha256(raw),
            "model_payload_sha256": _canonical_sha256(model_payload),
            "validated_payload_sha256": _canonical_sha256(validated),
        },
    }

    destination = _project_path(project, output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**result, "path": str(destination)}
