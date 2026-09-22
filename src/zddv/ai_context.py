from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from zddv.config import ProjectConfig
from zddv.debug_probes import suggest_debug_probes_from_ranking
from zddv.root_cause import rank_root_cause_candidates


def _canonical_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_ai_rca_context(
    project: ProjectConfig,
    *,
    run_id: str,
    candidate_limit: int = 10,
    event_limit: int = 100,
    signal_limit: int = 20,
) -> dict[str, Any]:
    """Build a provider-neutral, evidence-only context bundle for assisted RCA."""

    if candidate_limit < 1:
        raise ValueError("candidate_limit must be >= 1")

    ranking = rank_root_cause_candidates(
        project,
        run_id=run_id,
        event_limit=event_limit,
        signal_limit=signal_limit,
    )
    probes = suggest_debug_probes_from_ranking(
        ranking,
        run_id=run_id,
        candidate_limit=candidate_limit,
    )

    candidates = ranking["candidates"][:candidate_limit]
    evidence = {
        "run": ranking["run"],
        "failure_signature": ranking["failure_signature"],
        "candidates": candidates,
        "limitations": ranking["limitations"],
        "debug_probe_suggestions": probes["suggestions"],
        "debug_probe_blockers": probes["blockers"],
    }
    evidence_sha256 = _canonical_sha256(evidence)

    return {
        "schema_version": 1,
        "analysis": "ai_rca_context",
        "project": project.name,
        "run": ranking["run"],
        "semantics": (
            "This file is a deterministic evidence bundle for optional downstream "
            "AI-assisted debugging. ZDDV does not contact a model provider, transmit "
            "project data, execute suggested commands, or treat evidence scores as a "
            "causal probability."
        ),
        "policy": {
            "provider_neutral": True,
            "external_transmission": False,
            "automatic_model_invocation": False,
            "automatic_command_execution": False,
            "review_required_before_external_use": True,
        },
        "prompt_contract": [
            "Use only evidence present in this bundle.",
            "Treat evidence_score as evidence richness, not causal probability.",
            "Separate observed facts, hypotheses, unknowns, and proposed next checks.",
            "Reference candidate rank/kind and concrete evidence when making a hypothesis.",
            "Do not invent signal values, source lines, protocol behavior, or tool results.",
            "Keep any proposed generated assertion/test subject to ZDDV review-gated staging.",
        ],
        "provenance": {
            "evidence_sha256": evidence_sha256,
            "source_analyses": [
                "root_cause_candidates",
                "debug_probe_suggestions",
            ],
            "deterministic": True,
        },
        "evidence": evidence,
        "summary": {
            "candidates_included": len(candidates),
            "candidates_available": ranking["summary"]["candidates"],
            "probe_suggestions": probes["summary"]["suggestions"],
            "probe_blockers": probes["summary"]["blockers"],
            "limitations": len(ranking["limitations"]),
        },
    }


def write_ai_rca_context(
    project: ProjectConfig,
    *,
    run_id: str,
    candidate_limit: int = 10,
    event_limit: int = 100,
    signal_limit: int = 20,
    output: str | Path = ".zddv/debug/ai-rca-context.json",
) -> dict[str, Any]:
    bundle = build_ai_rca_context(
        project,
        run_id=run_id,
        candidate_limit=candidate_limit,
        event_limit=event_limit,
        signal_limit=signal_limit,
    )

    destination = Path(output)
    if not destination.is_absolute():
        destination = project.root / destination
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(bundle, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**bundle, "path": str(destination)}
