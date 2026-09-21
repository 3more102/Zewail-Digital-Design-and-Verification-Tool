from __future__ import annotations

from pathlib import Path
import re

from zddv.config import ProjectConfig
from zddv.storage import record_functional_coverage_bins


_FUNCTIONAL_COVERAGE_MARKER = re.compile(
    r"^ZDDV_FCOV\s+"
    r"(?P<covergroup>\S+)\s+"
    r"(?P<coverpoint>\S+)\s+"
    r"(?P<bin_name>\S+)\s+"
    r"HITS=(?P<hits>\d+)"
    r"(?:\s+GOAL=(?P<goal>\d+))?"
    r"(?:\s+(?P<message>.*))?$"
)


def parse_functional_coverage_log(path: str | Path) -> list[dict]:
    """Parse normalized functional-coverage bin markers from a simulation log."""
    source = Path(path)
    bins: list[dict] = []

    for line_number, raw_line in enumerate(
        source.read_text(encoding="utf-8", errors="replace").splitlines(),
        start=1,
    ):
        match = _FUNCTIONAL_COVERAGE_MARKER.match(raw_line.strip())
        if not match:
            continue

        hits = int(match.group("hits"))
        goal = int(match.group("goal") or 1)
        if goal < 1:
            raise ValueError(
                f"Functional coverage goal must be >= 1 at {source}:{line_number}"
            )

        message = (match.group("message") or "").strip()
        bins.append(
            {
                "bin_index": len(bins),
                "covergroup": match.group("covergroup"),
                "coverpoint": match.group("coverpoint"),
                "bin_name": match.group("bin_name"),
                "hits": hits,
                "goal": goal,
                "message": message or None,
                "log_line": line_number,
            }
        )

    return bins


def ingest_functional_coverage_log(
    project: ProjectConfig,
    *,
    run_id: str,
    log_path: str | Path,
    created_at: str,
) -> list[dict]:
    """Ingest normalized per-run functional-coverage bin observations."""
    source = Path(log_path)
    bins = parse_functional_coverage_log(source)
    if not bins:
        return []

    normalized = [
        {
            **item,
            "run_id": run_id,
            "created_at": created_at,
            "log_path": str(source),
        }
        for item in bins
    ]
    record_functional_coverage_bins(project, normalized)
    return normalized
