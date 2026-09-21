from pathlib import Path

from zddv.config import initialize_project
from zddv.coverage import (
    analyze_coverage_holes,
    parse_verilator_coverage,
    summarize_coverage_points,
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


def test_analyze_coverage_holes_with_type_filter(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    coverage_dir = project.root / ".zddv" / "coverage"
    coverage_dir.mkdir(parents=True, exist_ok=True)
    merged = coverage_dir / "coverage.dat"
    merged.write_text(
        "# SystemC::Coverage-3\n"
        "C 'fdesign.svl10n1pagev_line/top' 4\n"
        "C 'fdesign.svl11n1pagev_line/top' 0\n"
        "C 'fdesign.svl12n1pagev_toggle/top.sig' 0\n"
        "C 'fdesign.svl13n1pagev_user/top.cover' 0\n",
        encoding="utf-8",
    )

    result = analyze_coverage_holes(
        project,
        point_type="line",
        limit=10,
    )
    report = result["report"]

    assert Path(result["path"]).exists()
    assert report["total_points"] == 4
    assert report["total_holes"] == 3
    assert report["matching_holes"] == 1
    assert report["shown_holes"] == 1
    assert report["by_type"] == {"line": 1, "toggle": 1, "user": 1}
    assert report["holes"][0]["type"] == "line"
    assert "svl11" in report["holes"][0]["name"]


def test_coverage_hole_limit_validation(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")

    try:
        analyze_coverage_holes(project, limit=0)
    except ValueError as exc:
        assert "limit" in str(exc)
    else:
        raise AssertionError("Expected ValueError")
