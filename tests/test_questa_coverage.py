from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zddv.config import ProjectConfig
from zddv.coverage import (
    load_normalized_coverage_points,
    merge_questa_coverage,
    parse_questa_code_coverage_xml,
    parse_questa_coverage_summary,
    parse_questa_functional_coverage_report,
)
from zddv.storage import (
    list_coverage_snapshots,
    list_functional_coverage_bins,
    list_functional_coverage_snapshots,
)


QUESTA_SUMMARY = """QuestaSim-64 vcover 2024.2 Coverage Utility 2024.05 May 20 2024
Coverage Report Totals BY INSTANCES: Number of Instances 23

    Enabled Coverage              Bins      Hits    Misses    Weight  Coverage
    ----------------              ----      ----    ------    ------  --------
    Branches                      3044      2982        62         1    97.96%
    Expressions                   1665      1143       522         1    68.64%
    Statements                    4920      4920         0         1   100.00%
    Toggles                      72906     37574     35332         1    51.53%
Total coverage (filtered view): 79.53%
"""

QUESTA_CODE_XML = """<?xml version="1.0"?>
<coverage_report>
  <code_coverage_report lines="1" byInstance="1">
    <instanceData path="/tb/dut" du="dut">
      <sourceTable files="1">
        <fileMap fn="0" path="rtl/dut.sv" />
      </sourceTable>
      <statement_data>
        <stmt fn="0" ln="10" st="1" hits="3" />
        <stmt fn="0" ln="11" st="1" hits="0" />
      </statement_data>
    </instanceData>
  </code_coverage_report>
</coverage_report>
"""

QUESTA_FUNCTIONAL = """COVERGROUP COVERAGE:
--------------------
Covergroup                              Metric       Goal    Bins    Status
TYPE /top/dut/APB_cg                    66.67%        100       -    Uncovered
    covered/total bins:                     2          3
    missing/total bins:                     1          3
    % Hit:                              66.67%        100

    Coverpoint APB_cg::type_cp          50.00%        100       -    Uncovered
        covered/total bins:                 1          2
        missing/total bins:                 1          2
        % Hit:                          50.00%        100
        bin write                         0          1         ZERO
        bin read                        154          1         Covered

    Cross APB_cg::write_x_data         100.00%        100       -    Covered
        bin legal_pair                    2          2         Covered
        illegal bin bad_pair              1          1         Covered
"""


def _project(tmp_path: Path) -> ProjectConfig:
    root = tmp_path / "demo"
    root.mkdir()
    return ProjectConfig(
        root=root,
        name="demo",
        top="tb_top",
        simulator="questa",
        rtl=[],
        tb=[],
        waveform=False,
        coverage=True,
    )


def test_parse_questa_code_coverage_xml_uses_explicit_item_hits(tmp_path: Path):
    report = tmp_path / "code.xml"
    report.write_text(QUESTA_CODE_XML, encoding="utf-8")

    points = parse_questa_code_coverage_xml(report)

    assert len(points) == 2
    assert [point["type"] for point in points] == ["statement", "statement"]
    assert points[0]["count"] == 3
    assert points[0]["hit"] is True
    assert points[0]["metadata"]["scope"] == "/tb/dut"
    assert points[0]["metadata"]["file"] == "rtl/dut.sv"
    assert points[0]["metadata"]["line"] == 10
    assert points[1]["count"] == 0
    assert points[1]["hit"] is False
    assert "rtl/dut.sv::line:11::statement:1" in points[1]["name"]


def test_parse_questa_summary_preserves_tool_score_separately():
    metrics = parse_questa_coverage_summary(QUESTA_SUMMARY)

    assert metrics["total_points"] == 82535
    assert metrics["hit_points"] == 46619
    assert metrics["unhit_points"] == 35916
    assert metrics["hit_rate"] == pytest.approx(100.0 * 46619 / 82535)
    assert metrics["tool_total_coverage"] == 79.53
    assert metrics["by_type"]["branch"] == {
        "total": 3044,
        "hit": 2982,
        "hit_rate": 97.96,
    }
    assert metrics["by_type"]["statement"]["hit_rate"] == 100.0
    assert metrics["by_type"]["toggle"]["hit"] == 37574



def test_parse_questa_summary_accepts_comma_grouped_counts():
    text = QUESTA_SUMMARY.replace("3044", "3,044").replace(
        "2982", "2,982"
    ).replace("1665", "1,665").replace("1143", "1,143").replace(
        "4920", "4,920"
    ).replace("72906", "72,906").replace("37574", "37,574").replace(
        "35332", "35,332"
    )

    metrics = parse_questa_coverage_summary(text)

    assert metrics["total_points"] == 82535
    assert metrics["hit_points"] == 46619
    assert metrics["by_type"]["branch"]["total"] == 3044


def test_parse_questa_functional_coverage_keeps_only_ordinary_bins():
    payload = parse_questa_functional_coverage_report(QUESTA_FUNCTIONAL)

    assert payload["source"] == "questa-vcover"
    assert len(payload["bins"]) == 3
    assert payload["bins"][0] == {
        "scope": "/top/dut/APB_cg",
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


def test_merge_questa_coverage_merges_reports_and_persists_snapshot(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    run_root = (project.root / project.run_dir).resolve()
    for name in ("run-a", "run-b"):
        run_dir = run_root / name
        run_dir.mkdir(parents=True)
        (run_dir / "coverage.ucdb").write_text(
            f"{name} fixture\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/questa/bin/vcover" if name == "vcover" else None,
    )

    commands: list[list[str]] = []

    def fake_run(command, cwd):
        commands.append(list(command))
        if command[1] == "merge":
            out_path = Path(command[command.index("-out") + 1])
            out_path.write_text("merged fixture\n", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="merge complete\n")
        if command[1:3] == ["report", "-summary"]:
            return SimpleNamespace(returncode=0, stdout=QUESTA_SUMMARY)
        if command[1:3] == ["report", "-xml"]:
            out_path = Path(command[command.index("-output") + 1])
            out_path.write_text(QUESTA_CODE_XML, encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="")
        if command[1:4] == ["report", "-cvg", "-details"]:
            return SimpleNamespace(returncode=0, stdout=QUESTA_FUNCTIONAL)
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("zddv.coverage._run", fake_run)

    result = merge_questa_coverage(project)

    assert commands[0][:4] == [
        "/opt/questa/bin/vcover",
        "merge",
        "-out",
        result["merged"],
    ]
    assert commands[1] == [
        "/opt/questa/bin/vcover",
        "report",
        "-summary",
        result["merged"],
    ]
    assert commands[2] == [
        "/opt/questa/bin/vcover",
        "report",
        "-xml",
        "-details",
        "-zeros",
        "-codeAll",
        "-output",
        result["code_report"],
        result["merged"],
    ]
    assert commands[3] == [
        "/opt/questa/bin/vcover",
        "report",
        "-cvg",
        "-details",
        result["merged"],
    ]
    assert len(result["inputs"]) == 2
    assert Path(result["merged"]).exists()
    assert Path(result["summary"]).read_text(encoding="utf-8") == QUESTA_SUMMARY

    payload = json.loads(Path(result["metrics_path"]).read_text(encoding="utf-8"))
    assert payload["simulator"] == "questa"
    assert payload["input_count"] == 2
    assert payload["tool_total_coverage"] == 79.53
    assert payload["by_type"]["expression"]["hit"] == 1143
    assert payload["functional_bins"] == 3
    assert payload["functional_snapshot_id"] == result["functional_snapshot_id"]
    assert payload["code_capture"] == "xml"
    assert payload["code_points"] == 2
    assert payload["points"] == result["points_path"]
    assert Path(result["code_report"]).read_text(encoding="utf-8") == QUESTA_CODE_XML

    code_points = load_normalized_coverage_points(project)
    assert len(code_points) == 2
    assert sum(not point["hit"] for point in code_points) == 1
    assert Path(result["functional_report"]).read_text(
        encoding="utf-8"
    ) == QUESTA_FUNCTIONAL

    snapshots = list_coverage_snapshots(project, limit=5)
    assert len(snapshots) == 1
    assert snapshots[0]["simulator"] == "questa"
    assert snapshots[0]["total_points"] == 82535
    assert snapshots[0]["hit_points"] == 46619


    functional_snapshots = list_functional_coverage_snapshots(project, limit=5)
    assert len(functional_snapshots) == 1
    assert (
        functional_snapshots[0]["snapshot_id"]
        == result["functional_snapshot_id"]
    )
    assert functional_snapshots[0]["source"] == "questa-vcover"
    assert functional_snapshots[0]["coverage_rate"] == pytest.approx(2 * 100.0 / 3)

    holes = list_functional_coverage_bins(
        project,
        result["functional_snapshot_id"],
        status="UNCOVERED",
    )
    assert len(holes) == 1
    assert holes[0]["coverpoint"] == "APB_cg::type_cp"
    assert holes[0]["bin_name"] == "write"



def test_merge_questa_coverage_keeps_summary_when_code_xml_is_unavailable(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    run_dir = (project.root / project.run_dir / "run-a").resolve()
    run_dir.mkdir(parents=True)
    (run_dir / "coverage.ucdb").write_text("run fixture\n", encoding="utf-8")

    out_dir = (project.root / ".zddv" / "coverage").resolve()
    out_dir.mkdir(parents=True)
    stale_points = out_dir / "points.json"
    stale_points.write_text(
        '[{"name":"stale","count":0,"hit":false,"type":"statement"}]\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/questa/bin/vcover" if name == "vcover" else None,
    )

    def fake_run(command, cwd):
        if command[1] == "merge":
            out_path = Path(command[command.index("-out") + 1])
            out_path.write_text("merged fixture\n", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="merge complete\n")
        if command[1:3] == ["report", "-summary"]:
            return SimpleNamespace(returncode=0, stdout=QUESTA_SUMMARY)
        if command[1:3] == ["report", "-xml"]:
            return SimpleNamespace(
                returncode=1,
                stdout="detailed XML unavailable\n",
            )
        if command[1:4] == ["report", "-cvg", "-details"]:
            return SimpleNamespace(returncode=0, stdout=QUESTA_FUNCTIONAL)
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("zddv.coverage._run", fake_run)

    result = merge_questa_coverage(project)

    assert result["metrics"]["total_points"] == 82535
    assert result["code_capture"] == "unavailable"
    assert result["code_points"] == 0
    assert result["points_path"] is None
    assert result["code_error"] == "detailed XML unavailable"
    assert not stale_points.exists()
    assert result["functional_bins"] == 3

    with pytest.raises(RuntimeError, match="Normalized code-coverage points"):
        load_normalized_coverage_points(project)
