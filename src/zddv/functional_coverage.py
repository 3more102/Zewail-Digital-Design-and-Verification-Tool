from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from zddv.config import ProjectConfig
from zddv.storage import record_functional_coverage_snapshot


def normalize_functional_coverage(payload: dict) -> dict:
    bins = payload.get("bins")
    if not isinstance(bins, list) or not bins:
        raise ValueError("Functional coverage input must contain a non-empty 'bins' list.")

    normalized_bins: list[dict] = []
    for index, raw in enumerate(bins):
        if not isinstance(raw, dict):
            raise ValueError(f"Functional coverage bin {index} must be an object.")

        scope = str(raw.get("scope") or "").strip()
        coverpoint = str(raw.get("coverpoint") or "").strip()
        bin_name = str(raw.get("bin") or "").strip()
        if not coverpoint:
            raise ValueError(f"Functional coverage bin {index} is missing 'coverpoint'.")
        if not bin_name:
            raise ValueError(f"Functional coverage bin {index} is missing 'bin'.")

        hits = raw.get("hits", 0)
        goal = raw.get("goal", 1)
        if isinstance(hits, bool) or not isinstance(hits, int) or hits < 0:
            raise ValueError(f"Functional coverage bin {index} has invalid 'hits'.")
        if isinstance(goal, bool) or not isinstance(goal, int) or goal < 1:
            raise ValueError(f"Functional coverage bin {index} has invalid 'goal'.")

        metadata = raw.get("metadata", {})
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise ValueError(f"Functional coverage bin {index} has invalid 'metadata'.")

        normalized_bins.append(
            {
                "bin_index": index,
                "scope": scope,
                "coverpoint": coverpoint,
                "bin_name": bin_name,
                "hits": hits,
                "goal": goal,
                "status": "COVERED" if hits >= goal else "UNCOVERED",
                "metadata": metadata,
            }
        )

    covered = sum(item["status"] == "COVERED" for item in normalized_bins)
    total = len(normalized_bins)
    return {
        "source": str(payload.get("source") or "unknown"),
        "total_bins": total,
        "covered_bins": covered,
        "uncovered_bins": total - covered,
        "coverage_rate": 100.0 * covered / total if total else 0.0,
        "bins": normalized_bins,
    }


def ingest_functional_coverage(
    project: ProjectConfig,
    path: str | Path,
    *,
    source: str | None = None,
) -> dict:
    input_path = Path(path)
    if not input_path.is_absolute():
        input_path = (project.root / input_path).resolve()
    if not input_path.exists():
        raise FileNotFoundError(input_path)

    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Functional coverage input root must be a JSON object.")

    normalized = normalize_functional_coverage(payload)
    if source is not None:
        normalized["source"] = source

    created_at = datetime.now(timezone.utc).isoformat()
    snapshot_id = (
        datetime.now(timezone.utc).strftime("fcov-%Y%m%dT%H%M%S")
        + "-"
        + uuid.uuid4().hex[:8]
    )

    out_dir = (project.root / ".zddv" / "functional_coverage").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    normalized_path = out_dir / f"{snapshot_id}.json"

    record = {
        "snapshot_id": snapshot_id,
        "created_at": created_at,
        "project": project.name,
        "source": normalized["source"],
        "input_path": str(input_path),
        **normalized,
    }
    normalized_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    record["normalized_path"] = str(normalized_path)
    record_functional_coverage_snapshot(project, record)
    return record
