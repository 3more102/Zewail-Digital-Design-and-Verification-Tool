from __future__ import annotations

from typing import Any


def model_response_contract() -> dict[str, Any]:
    """Machine-readable contract for model output accepted by ZDDV ingestion."""

    return {
        "schema_version": 1,
        "analysis": "zddv_ai_rca_response",
        "required_fields": [
            "schema_version",
            "analysis",
            "context_evidence_sha256",
            "hypotheses",
        ],
        "hypothesis_required_fields": [
            "id",
            "summary",
            "evidence_refs",
        ],
        "evidence_reference_kinds": {
            "candidate": {"selector": "rank"},
            "debug_probe": {"selector": "rank"},
            "failure_signature": {"selector": None},
            "limitation": {"selector": "index"},
        },
        "semantics": (
            "Every hypothesis must cite at least one resolvable evidence reference. "
            "Unknowns and next checks are proposals, not observed facts."
        ),
    }
