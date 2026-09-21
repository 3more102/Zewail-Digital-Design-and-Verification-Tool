import json
from pathlib import Path

from zddv.coverage import (
    build_coverage_hole_report,
    parse_verilator_coverage,
    summarize_coverage_points,
    write_coverage_hole_report,
)


def test_parse_and_summarize_verilator_coverage(tmp_path: Path):
    coverage = tmp_path / "coverage.dat"
    coverage.write_text(
        "# SystemC::Coverage-3\n"
        "C 'fdesign.svl10n1pagev_line/top' 4\n"
        "C 'fdesign.svl11n1pagev_line/top' 0\n"
        "C 'fdesign.svl12n1pagev_toggle/top.sig' 7\n"
        "C 'fdesign.svl13n1pagev_user/top.cover' 0\n",
        encoding="utf-8",
    )

    points = parse_verilator_coverage(coverage)
    summary = summarize_coverage_points(points)

    assert len(points) == 4
    assert points[0]["type"] == "line"
    assert points[2]["type"] == "toggle"
    assert summary["total_points"] == 4
    assert summary["hit_points"] == 2
    assert summary["unhit_points"] == 2
    assert summary["hit_rate"] == 50.0
    assert summary["by_type"]["line"]["hit"] == 1
    assert summary["by_type"]["user"]["hit_rate"] == 0.0


def test_unknown_coverage_type_is_preserved(tmp_path: Path):
    coverage = tmp_path / "coverage.dat"
    coverage.write_text("C 'custom-key' 3\n", encoding="utf-8")

    points = parse_verilator_coverage(coverage)

    assert points == [
        {"name": "custom-key", "count": 3, "hit": True, "type": "unknown"}
    ]


def test_coverage_hole_report_filters_sorts_and_writes_json(tmp_path: Path):
    points = [
        {"name": "z-toggle", "count": 0, "hit": False, "type": "toggle"},
        {"name": "b-line", "count": 0, "hit": False, "type": "line"},
        {"name": "a-line", "count": 0, "hit": False, "type": "line"},
        {"name": "hit-line", "count": 2, "hit": True, "type": "line"},
    ]

    report = build_coverage_hole_report(points, limit=2)
    assert report["total_holes"] == 3
    assert report["reported_holes"] == 2
    assert report["by_type"] == {"line": 2, "toggle": 1}
    assert [hole["name"] for hole in report["holes"]] == ["a-line", "b-line"]

    filtered = build_coverage_hole_report(points, point_type="toggle")
    assert filtered["total_holes"] == 1
    assert filtered["holes"][0]["name"] == "z-toggle"

    output = tmp_path / "coverage" / "holes.json"
    written = write_coverage_hole_report(points, output, point_type="line")
    assert written["path"] == str(output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["total_holes"] == 2
    assert payload["by_type"] == {"line": 2}
