from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

from zddv.config import ProjectConfig


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resolve_project_file(
    project: ProjectConfig,
    value: str | Path,
    *,
    label: str,
    must_exist: bool,
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
    if must_exist and not path.is_file():
        raise FileNotFoundError(path)
    return path


def _load_signoff(
    project: ProjectConfig,
    value: str | Path,
    *,
    label: str,
) -> tuple[Path, dict[str, Any]]:
    path = _resolve_project_file(
        project,
        value,
        label=label,
        must_exist=True,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain a JSON object")
    _validate_signoff_payload(payload, label=label)
    return path, payload


def _validate_signoff_payload(payload: dict[str, Any], *, label: str) -> None:
    if payload.get("analysis") != "verification_signoff_bundle":
        raise ValueError(f"{label} is not a verification signoff bundle")
    if payload.get("schema_version") != 1:
        raise ValueError(f"{label} uses an unsupported signoff schema version")
    for key in ("policy", "evidence", "checks", "summary", "provenance"):
        if key not in payload:
            raise ValueError(f"{label} is missing required field: {key}")

    provenance = payload["provenance"]
    if not isinstance(provenance, dict):
        raise ValueError(f"{label} provenance must be an object")

    expected_evidence = _canonical_sha256(payload["evidence"])
    expected_policy = _canonical_sha256(payload["policy"])
    core = {key: value for key, value in payload.items() if key != "provenance"}
    expected_signoff = _canonical_sha256(core)

    for field, expected in (
        ("evidence_sha256", expected_evidence),
        ("policy_sha256", expected_policy),
        ("signoff_sha256", expected_signoff),
    ):
        actual = str(provenance.get(field, "")).lower()
        if not hmac.compare_digest(actual, expected):
            raise ValueError(f"{label} provenance mismatch: {field}")


def _signoff_ref(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": str(path),
        "signoff_sha256": payload["provenance"]["signoff_sha256"],
        "evidence_sha256": payload["provenance"]["evidence_sha256"],
        "policy_sha256": payload["provenance"]["policy_sha256"],
        "review_state": payload["summary"]["review_state"],
    }


def _require_same_scope(
    baseline: dict[str, Any],
    current: dict[str, Any],
) -> None:
    for field in ("project", "simulator", "top"):
        if baseline.get(field) != current.get(field):
            raise ValueError(
                "Signoff comparison requires the same verification scope: "
                f"{field} differs ({baseline.get(field)!r} != {current.get(field)!r})"
            )


def _top_level_changes(before: Any, after: Any) -> list[str]:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return ["<root>"] if before != after else []
    keys = sorted(set(before) | set(after))
    return [
        key
        for key in keys
        if _canonical_sha256(before.get(key)) != _canonical_sha256(after.get(key))
    ]


def _run_map(payload: dict[str, Any], *, label: str) -> dict[str, dict[str, Any]]:
    rows = payload["evidence"].get("runs") or []
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"{label} contains a non-object run record")
        run_id = str(row.get("run_id", "")).strip()
        if not run_id:
            raise ValueError(f"{label} contains a run record without run_id")
        if run_id in result:
            raise ValueError(f"{label} contains duplicate run_id: {run_id}")
        result[run_id] = row
    return result


def _run_diff(
    baseline: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    before = _run_map(baseline, label="Baseline signoff")
    after = _run_map(current, label="Current signoff")
    before_ids = set(before)
    after_ids = set(after)
    changed_ids = sorted(
        run_id
        for run_id in before_ids & after_ids
        if _canonical_sha256(before[run_id]) != _canonical_sha256(after[run_id])
    )
    return {
        "changed": bool(before_ids != after_ids or changed_ids),
        "added_run_ids": sorted(after_ids - before_ids),
        "removed_run_ids": sorted(before_ids - after_ids),
        "changed_run_ids": changed_ids,
        "unchanged_run_ids": sorted((before_ids & after_ids) - set(changed_ids)),
    }


def _evidence_ref(value: Any, *, kind: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        return {"sha256": _canonical_sha256(value)}
    result: dict[str, Any] = {"sha256": _canonical_sha256(value)}
    for key in ("snapshot_id", "status", "kind", "percent", "mode", "backend", "test_name", "run_id"):
        if key in value:
            result[key] = value[key]
    result["evidence_kind"] = kind
    return result


def _single_evidence_diff(
    baseline: dict[str, Any],
    current: dict[str, Any],
    *,
    kind: str,
) -> dict[str, Any]:
    before = baseline["evidence"].get(kind)
    after = current["evidence"].get(kind)
    changed = _canonical_sha256(before) != _canonical_sha256(after)
    result: dict[str, Any] = {
        "changed": changed,
        "baseline": _evidence_ref(before, kind=kind),
        "current": _evidence_ref(after, kind=kind),
    }
    if kind == "coverage":
        before_percent = before.get("percent") if isinstance(before, dict) else None
        after_percent = after.get("percent") if isinstance(after, dict) else None
        result["percent_delta"] = (
            float(after_percent) - float(before_percent)
            if before_percent is not None and after_percent is not None
            else None
        )
    return result


def _checks_by_name(payload: dict[str, Any], *, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in payload.get("checks") or []:
        if not isinstance(item, dict):
            raise ValueError(f"{label} contains a non-object check")
        name = str(item.get("name", "")).strip()
        if not name:
            raise ValueError(f"{label} contains a check without name")
        if name in result:
            raise ValueError(f"{label} contains duplicate check name: {name}")
        result[name] = item
    return result


def _check_diff(
    baseline: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    before = _checks_by_name(baseline, label="Baseline signoff")
    after = _checks_by_name(current, label="Current signoff")
    changes: list[dict[str, Any]] = []
    for name in sorted(set(before) | set(after)):
        old = before.get(name)
        new = after.get(name)
        if _canonical_sha256(old) == _canonical_sha256(new):
            continue
        changes.append(
            {
                "name": name,
                "baseline_status": None if old is None else old.get("status"),
                "current_status": None if new is None else new.get("status"),
                "baseline_blocking": None if old is None else bool(old.get("blocking")),
                "current_blocking": None if new is None else bool(new.get("blocking")),
                "details_changed": (
                    old is None
                    or new is None
                    or _canonical_sha256(old.get("details"))
                    != _canonical_sha256(new.get("details"))
                ),
            }
        )
    return {
        "changed": bool(changes),
        "changes": changes,
    }


def build_verification_signoff_diff(
    baseline_path: Path,
    baseline: dict[str, Any],
    current_path: Path,
    current: dict[str, Any],
) -> dict[str, Any]:
    _require_same_scope(baseline, current)

    policy_changed_keys = _top_level_changes(
        baseline["policy"],
        current["policy"],
    )
    runs = _run_diff(baseline, current)
    coverage = _single_evidence_diff(baseline, current, kind="coverage")
    formal = _single_evidence_diff(baseline, current, kind="formal")
    uvm = _single_evidence_diff(baseline, current, kind="uvm")
    checks = _check_diff(baseline, current)

    baseline_blockers = set(baseline["summary"].get("blocking_checks") or [])
    current_blockers = set(current["summary"].get("blocking_checks") or [])

    evidence_changed = any(
        (
            runs["changed"],
            coverage["changed"],
            formal["changed"],
            uvm["changed"],
        )
    )
    policy_changed = bool(policy_changed_keys)
    review_state_changed = (
        baseline["summary"].get("review_state")
        != current["summary"].get("review_state")
    )

    summary = {
        "changed": bool(
            policy_changed
            or evidence_changed
            or checks["changed"]
            or review_state_changed
        ),
        "policy_changed": policy_changed,
        "evidence_changed": evidence_changed,
        "checks_changed": checks["changed"],
        "review_state_changed": review_state_changed,
        "baseline_review_state": baseline["summary"].get("review_state"),
        "current_review_state": current["summary"].get("review_state"),
        "blocking_checks_added": sorted(current_blockers - baseline_blockers),
        "blocking_checks_removed": sorted(baseline_blockers - current_blockers),
    }

    core = {
        "schema_version": 1,
        "analysis": "verification_signoff_diff",
        "project": baseline.get("project"),
        "simulator": baseline.get("simulator"),
        "top": baseline.get("top"),
        "semantics": (
            "This report is a deterministic structural comparison of two validated "
            "ZDDV signoff bundles. It records evidence/policy/check changes and does "
            "not infer that a changed result is better, worse, or specification-complete."
        ),
        "baseline": _signoff_ref(baseline_path, baseline),
        "current": _signoff_ref(current_path, current),
        "policy": {
            "changed": policy_changed,
            "changed_keys": policy_changed_keys,
            "baseline_sha256": baseline["provenance"]["policy_sha256"],
            "current_sha256": current["provenance"]["policy_sha256"],
        },
        "evidence": {
            "changed": evidence_changed,
            "baseline_sha256": baseline["provenance"]["evidence_sha256"],
            "current_sha256": current["provenance"]["evidence_sha256"],
            "runs": runs,
            "coverage": coverage,
            "formal": formal,
            "uvm": uvm,
        },
        "checks": checks,
        "summary": summary,
    }
    return {
        **core,
        "provenance": {
            "diff_sha256": _canonical_sha256(core),
            "deterministic": True,
            "baseline_validated": True,
            "current_validated": True,
        },
    }


def compare_verification_signoff_bundles(
    project: ProjectConfig,
    baseline: str | Path,
    current: str | Path,
) -> dict[str, Any]:
    baseline_path, baseline_payload = _load_signoff(
        project,
        baseline,
        label="Baseline signoff",
    )
    current_path, current_payload = _load_signoff(
        project,
        current,
        label="Current signoff",
    )
    return build_verification_signoff_diff(
        baseline_path,
        baseline_payload,
        current_path,
        current_payload,
    )


def write_verification_signoff_diff(
    project: ProjectConfig,
    baseline: str | Path,
    current: str | Path,
    *,
    output: str | Path = ".zddv/signoff/diff.json",
) -> dict[str, Any]:
    result = compare_verification_signoff_bundles(project, baseline, current)
    destination = _resolve_project_file(
        project,
        output,
        label="Signoff diff output",
        must_exist=False,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**result, "path": str(destination)}
