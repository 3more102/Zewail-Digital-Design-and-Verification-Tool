import json
from pathlib import Path

from zddv.coverage import (
    build_coverage_hole_report,
    parse_questa_coverage_summary,
    parse_questa_functional_coverage_report,
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



def test_parse_questa_coverage_summary_normalizes_code_types():
    text = """QuestaSim-64 vcover Coverage Utility
Coverage Report Totals BY INSTANCES: Number of Instances 23

    Enabled Coverage              Bins      Hits    Misses    Weight  Coverage
    ----------------              ----      ----    ------    ------  --------
    Branches                     3,044     2,982        62         1    97.96%
    Expressions                  1,665     1,143       522         1    68.64%
    Statements                   4,920     4,920         0         1   100.00%
    Toggles                     72,906    37,574    35,332         1    51.53%
Total coverage (filtered view): 79.53%
"""

    metrics = parse_questa_coverage_summary(text)

    assert metrics["total_points"] == 82535
    assert metrics["hit_points"] == 46619
    assert metrics["unhit_points"] == 35916
    assert metrics["by_type"]["branch"]["total"] == 3044
    assert metrics["by_type"]["statement"]["hit_rate"] == 100.0
    assert metrics["by_type"]["toggle"]["hit"] == 37574


def test_parse_questa_functional_coverage_report_normalizes_bins():
    text = """COVERGROUP COVERAGE:
--------------------
Covergroup                              Metric       Goal    Status
APB_seq_item_pkg::APB_cg               95.00%      100.00%   Uncovered

    Coverpoint APB_cg::type_cp          50.00%      100.00%   Uncovered
        bin write                         0          1         ZERO
        bin read                        154          1         Covered

    Cross APB_cg::write_x_data          40.00%      100.00%   Uncovered
        bin legal_pair                    2          2         Covered
        illegal bin bad_pair              1          1         Covered
"""

    payload = parse_questa_functional_coverage_report(text)

    assert payload["source"] == "questa-vcover"
    assert len(payload["bins"]) == 3
    assert payload["bins"][0] == {
        "scope": "APB_seq_item_pkg::APB_cg",
        "coverpoint": "APB_cg::type_cp",
        "bin": "write",
        "hits": 0,
        "goal": 1,
        "metadata": {
            "questa_status": "ZERO",
            "coverage_kind": "coverpoint",
        },
    }
    assert payload["bins"][2]["coverpoint"] == "APB_cg::write_x_data"
    assert payload["bins"][2]["metadata"]["coverage_kind"] == "cross"
