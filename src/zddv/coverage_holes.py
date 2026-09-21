from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
from typing import Iterable

from zddv.config import ProjectConfig
from zddv.coverage import parse_verilator_coverage


_COMPACT_LOCATION = re.compile(
    r"(?:^|[^A-Za-z0-9_])f(?P<file>.+?)l(?P<line>\d+)n\d+pagev_"
)
_PRIORITY = {
    "branch": 0,
    "line": 1,
    "toggle": 2,
    "user": 3,
}


def _decode_metadata(name: str) -> dict[str, str]:
    if "\x01" not in name:
        return {}

    parts = name.split("\x01")
    metadata: dict[str, str] = {}
    for index in range(1, len(parts) - 1, 2):
        key = parts[index]
        value = parts[index + 1]
        if key:
            metadata[key] = value
    return metadata


def coverage_point_location(name: str) -> dict[str, object]:
    metadata = _decode_metadata(name)
    filename = metadata.get("f")
    line_text = metadata.get("l")
    hierarchy = metadata.get("hier")

    if filename or line_text or hierarchy:
        location: dict[str, object] = {}
        if filename:
            location["file"] = filename
        if line_text and line_text.isdigit():
            location["line"] = int(line_text)
        if hierarchy:
            location["hierarchy"] = hierarchy
        return location

    compact = _COMPACT_LOCATION.search(name)
    location: dict[str, object] = {}
    if compact:
        location["file"] = compact.group("file")
        location["line"] = int(compact.group("line"))

    hierarchy_match = re.search(
        r"pagev_[A-Za-z0-9_]+/(?P<hier>.+)$",
        name,
    )
    if hierarchy_match:
        location["hierarchy"] = hierarchy_match.group("hier")
    return location


def analyze_coverage_holes(
    points: Iterable[dict],
    *,
    limit: int = 50,
    kinds: Iterable[str] | None = None,
) -> dict:
    if limit < 1:
        raise ValueError("limit must be >= 1")

    selected_kinds = {str(kind) for kind in kinds or ()}
    holes = [
        point
        for point in points
        if not bool(point.get("hit"))
        and (
            not selected_kinds
            or str(point.get("type") or "unknown") in selected_kinds
        )
    ]
    holes.sort(
        key=lambda point: (
            _PRIORITY.get(str(point.get("type") or "unknown"), 99),
            str(point.get("type") or "unknown"),
            str(point.get("name") or ""),
        )
    )

    by_type = Counter(str(point.get("type") or "unknown") for point in holes)
    rows = []
    for rank, point in enumerate(holes[:limit], start=1):
        rows.append(
            {
                "rank": rank,
                "type": str(point.get("type") or "unknown"),
                "name": str(point.get("name") or ""),
                "count": int(point.get("count") or 0),
                "location": coverage_point_location(
                    str(point.get("name") or "")
                ),
            }
        )

    return {
        "total_holes": len(holes),
        "returned_holes": len(rows),
        "by_type": dict(sorted(by_type.items())),
        "holes": rows,
    }


def generate_coverage_hole_report(
    project: ProjectConfig,
    *,
    limit: int = 50,
    kinds: Iterable[str] | None = None,
) -> dict:
    coverage_dir = (project.root / ".zddv" / "coverage").resolve()
    merged_path = coverage_dir / "coverage.dat"
    if not merged_path.exists():
        raise RuntimeError(
            f"Merged coverage not found at {merged_path}. "
            "Run 'zddv coverage' first."
        )

    points = parse_verilator_coverage(merged_path)
    analysis = analyze_coverage_holes(
        points,
        limit=limit,
        kinds=kinds,
    )

    coverage_dir.mkdir(parents=True, exist_ok=True)
    json_path = coverage_dir / "holes.json"
    text_path = coverage_dir / "holes.txt"

    payload = {
        "project": project.name,
        "simulator": project.simulator,
        "coverage": str(merged_path),
        **analysis,
    }
    json_path.write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )

    lines = [
        f"Coverage holes: {analysis['total_holes']}",
        "",
        f"{'RANK':>4} {'TYPE':<12} {'LOCATION':<36} POINT",
    ]
    for hole in analysis["holes"]:
        location = hole["location"]
        if location.get("file") and location.get("line"):
            display_location = (
                f"{location['file']}:{location['line']}"
            )
        elif location.get("hierarchy"):
            display_location = str(location["hierarchy"])
        else:
            display_location = "-"

        lines.append(
            f"{hole['rank']:>4} {hole['type'][:12]:<12} "
            f"{display_location[:36]:<36} {hole['name']}"
        )

    text_path.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    return {
        **analysis,
        "json_path": str(json_path),
        "text_path": str(text_path),
        "coverage_path": str(merged_path),
    }
