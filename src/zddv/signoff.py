from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any

from zddv.config import ProjectConfig
from zddv.storage import (
    list_coverage_score_snapshots,
    list_coverage_snapshots,
    list_formal_result_snapshots,
    list_run_records,
    list_uvm_log_snapshots,
)


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _latest_coverage(project: ProjectConfig) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []

    score_rows = list_coverage_score_snapshots(project, limit=1)
    if score_rows:
        row = score_rows[0]
        candidates.append(
            {
                "kind": "score",
                "snapshot_id": row["snapshot_id"],
                "created_at": row["created_at"],
                "simulator": row["simulator"],
                "percent": float(row["score"]),
                "record": row,
            }
        )

    point_rows = list_coverage_snapshots(project, limit=1)
    if point_rows:
        row = point_rows[0]
        candidates.append(
            {
                "kind": "points",
                "snapshot_id": row["snapshot_id"],
                "created_at": row["created_at"],
                "simulator": row["simulator"],
                "percent": float(row["hit_rate"]),
                "record": row,
            }
        )

    if not candidates:
        return None
    return max(candidates, key=lambda item: str(item["created_at"]))


def _check(
    name: str,
    status: str,
    *,
    blocking: bool,
    details: dict[str, Any],
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "blocking": blocking,
        "details": details,
    }


def build_verification_signoff_bundle(
    project: ProjectConfig,
    *,
    run_limit: int = 100,
    require_coverage: bool = False,
    min_coverage: float | None = None,
    require_formal: bool = False,
    require_uvm: bool = False,
) -> dict[str, Any]:
    """Build a deterministic, evidence-backed verification signoff review bundle.

    READY_FOR_REVIEW means the selected evidence contains no blocking condition
    under the requested policy. It is not a claim that uncollected evidence passed.
    """

    if run_limit < 1:
        raise ValueError("run_limit must be >= 1")
    if min_coverage is not None and not 0.0 <= float(min_coverage) <= 100.0:
        raise ValueError("min_coverage must be between 0 and 100")

    coverage_required = bool(require_coverage or min_coverage is not None)

    runs = list_run_records(project, limit=run_limit)
    coverage = _latest_coverage(project)
    formal_rows = list_formal_result_snapshots(project, limit=1)
    uvm_rows = list_uvm_log_snapshots(project, limit=1)
    formal = formal_rows[0] if formal_rows else None
    uvm = uvm_rows[0] if uvm_rows else None

    evidence = {
        "runs": runs,
        "coverage": coverage,
        "formal": formal,
        "uvm": uvm,
    }
    policy = {
        "run_limit": int(run_limit),
        "coverage_required": coverage_required,
        "min_coverage": None if min_coverage is None else float(min_coverage),
        "formal_required": bool(require_formal),
        "uvm_required": bool(require_uvm),
    }

    checks: list[dict[str, Any]] = []

    status_counts = Counter(str(row["status"]) for row in runs)
    nonpass_runs = [
        str(row["run_id"])
        for row in runs
        if str(row["status"]).upper() != "PASS"
    ]
    if not runs:
        checks.append(
            _check(
                "simulation",
                "MISSING",
                blocking=True,
                details={
                    "selected_runs": 0,
                    "status_counts": {},
                    "nonpass_run_ids": [],
                },
            )
        )
    elif nonpass_runs:
        checks.append(
            _check(
                "simulation",
                "FAIL",
                blocking=True,
                details={
                    "selected_runs": len(runs),
                    "status_counts": dict(sorted(status_counts.items())),
                    "nonpass_run_ids": nonpass_runs,
                },
            )
        )
    else:
        checks.append(
            _check(
                "simulation",
                "PASS",
                blocking=False,
                details={
                    "selected_runs": len(runs),
                    "status_counts": dict(sorted(status_counts.items())),
                    "nonpass_run_ids": [],
                },
            )
        )

    if coverage is None:
        checks.append(
            _check(
                "coverage",
                "MISSING" if coverage_required else "NOT_PRESENT",
                blocking=coverage_required,
                details={
                    "required": coverage_required,
                    "min_coverage": policy["min_coverage"],
                },
            )
        )
    else:
        percent = float(coverage["percent"])
        below_threshold = (
            min_coverage is not None and percent < float(min_coverage)
        )
        checks.append(
            _check(
                "coverage",
                "FAIL"
                if below_threshold
                else ("PASS" if coverage_required else "PRESENT"),
                blocking=below_threshold,
                details={
                    "required": coverage_required,
                    "snapshot_id": coverage["snapshot_id"],
                    "kind": coverage["kind"],
                    "percent": percent,
                    "min_coverage": policy["min_coverage"],
                },
            )
        )

    if formal is None:
        checks.append(
            _check(
                "formal",
                "MISSING" if require_formal else "NOT_PRESENT",
                blocking=bool(require_formal),
                details={"required": bool(require_formal)},
            )
        )
    else:
        formal_status = str(formal["status"]).upper()
        formal_failed = formal_status != "PASS"
        checks.append(
            _check(
                "formal",
                "FAIL"
                if formal_failed
                else ("PASS" if require_formal else "PRESENT"),
                blocking=formal_failed,
                details={
                    "required": bool(require_formal),
                    "snapshot_id": formal["snapshot_id"],
                    "status": formal_status,
                    "mode": formal["mode"],
                    "backend": formal["backend"],
                },
            )
        )

    if uvm is None:
        checks.append(
            _check(
                "uvm",
                "MISSING" if require_uvm else "NOT_PRESENT",
                blocking=bool(require_uvm),
                details={"required": bool(require_uvm)},
            )
        )
    else:
        uvm_status = str(uvm["status"]).upper()
        uvm_failed = uvm_status != "PASS"
        checks.append(
            _check(
                "uvm",
                "FAIL"
                if uvm_failed
                else ("PASS" if require_uvm else "PRESENT"),
                blocking=uvm_failed,
                details={
                    "required": bool(require_uvm),
                    "snapshot_id": uvm["snapshot_id"],
                    "status": uvm_status,
                    "test_name": uvm.get("test_name"),
                    "run_id": uvm.get("run_id"),
                },
            )
        )

    blocking_checks = [
        item["name"] for item in checks if bool(item["blocking"])
    ]
    missing_optional = [
        item["name"] for item in checks if item["status"] == "NOT_PRESENT"
    ]
    review_state = "BLOCKED" if blocking_checks else "READY_FOR_REVIEW"

    summary = {
        "review_state": review_state,
        "blocking_checks": blocking_checks,
        "missing_optional_evidence": missing_optional,
        "selected_runs": len(runs),
        "run_status_counts": dict(sorted(status_counts.items())),
    }

    core = {
        "schema_version": 1,
        "analysis": "verification_signoff_bundle",
        "project": project.name,
        "simulator": project.simulator,
        "top": project.top,
        "semantics": (
            "READY_FOR_REVIEW means the selected persisted evidence contains no "
            "blocking condition under the requested policy. It does not imply "
            "that absent optional evidence passed or that every verification "
            "objective is complete."
        ),
        "policy": policy,
        "evidence": evidence,
        "checks": checks,
        "summary": summary,
    }
    provenance = {
        "evidence_sha256": _canonical_sha256(evidence),
        "policy_sha256": _canonical_sha256(policy),
        "signoff_sha256": _canonical_sha256(core),
        "deterministic": True,
    }
    return {**core, "provenance": provenance}


def write_verification_signoff_bundle(
    project: ProjectConfig,
    *,
    run_limit: int = 100,
    require_coverage: bool = False,
    min_coverage: float | None = None,
    require_formal: bool = False,
    require_uvm: bool = False,
    output: str | Path = ".zddv/signoff/signoff.json",
) -> dict[str, Any]:
    bundle = build_verification_signoff_bundle(
        project,
        run_limit=run_limit,
        require_coverage=require_coverage,
        min_coverage=min_coverage,
        require_formal=require_formal,
        require_uvm=require_uvm,
    )
    destination = Path(output)
    if not destination.is_absolute():
        destination = project.root / destination
    destination = destination.resolve()
    root = project.root.resolve()
    try:
        destination.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"Signoff output must remain inside the project root: {destination}"
        ) from exc
    if destination == root:
        raise ValueError("Signoff output must identify a file inside the project root")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(bundle, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**bundle, "path": str(destination)}
