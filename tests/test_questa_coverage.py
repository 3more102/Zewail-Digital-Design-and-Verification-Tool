from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zddv.cli import cmd_coverage, cmd_coverage_holes
from zddv.config import ProjectConfig
from zddv.coverage import (
    merge_questa_coverage,
    parse_questa_code_coverage_report,
    parse_questa_coverage_summary,
    parse_questa_fec_coverage_report,
    parse_questa_functional_coverage_report,
    parse_questa_statement_coverage_xml,
    write_questa_statement_hole_report,
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

QUESTA_CODE_DETAILS = """Coverage Report by file with details

=================================================================================
=== File: top.v
=================================================================================
Statement Coverage:
    Enabled Coverage            Active      Hits    Misses % Covered
    ----------------            ------      ----    ------ ---------
    Stmts                            3         2         1     66.67

================================Statement Details================================

Statement Coverage for file top.v --

    8               1                          1
    9               1                    ***0***
    10              2                     100001

Branch Coverage:
    Enabled Coverage            Bins      Hits    Misses % Covered
    ----------------            ----      ----    ------ ---------
    Branches                         5         3         2     60.00

================================Branch Details================================

Branch Coverage for file top.v --

    12                                         3  Count coming in to IF
    12              1                    ***0***  if (i == 16)
    14              1                          1  else if (i == 2)
    16              1                          1  else if (i == 10)
    18              1                          1  else if (i == 18)
    20              1                    ***0***  else

Branch totals: 3 hits of 5 branches = 60.0%

Expression Coverage:
    Enabled Coverage            Bins      Hits    Misses % Covered
    ----------------            ----      ----    ------ ---------
    Expressions                     4         3         1     75.00

==============================Expression Details==============================

Expression Coverage for file top.v --

----------------Focused Expression View-----------------
Line 30 Item 1 assign y = (a | b);
Expression totals: 3 hits of 4 rows = 75.0%
Truth Table: a b | (a | b)
Row 1: 1 1-1
Row 2: ***0*** -11
Row 3: 2 000
unknown: 1
Rows: Hits FEC Target Matching input patterns
Row 1: 2 a_0 { 00 }
Row 2: 1 a_1 { 10 }
Row 3: 2 b_0 { 00 }
Row 4: ***0*** b_1 { 01 }

Condition Coverage:
    Enabled Coverage            Bins      Hits    Misses % Covered
    ----------------            ----      ----    ------ ---------
    Conditions                      4         3         1     75.00

===============================Condition Details===============================

Condition Coverage for file top.v --

----------------Focused Expression View-----------------
Line 40 Item 1 if (req && ready)
Condition totals: 3 hits of 4 rows = 75.0%
Rows: Hits FEC Target Matching input patterns
Row 1: 3 req_0 { 00 }
Row 2: 3 req_1 { 11 }
Row 3: ***0*** ready_0 { 10 }
Row 4: 2 ready_1 { 11 }
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


def test_parse_questa_code_coverage_normalizes_statement_and_branch_items():
    points = parse_questa_code_coverage_report(QUESTA_CODE_DETAILS)

    assert len(points) == 8
    assert points[0] == {
        "name": "top.v:8:1",
        "count": 1,
        "hit": True,
        "type": "statement",
        "source_file": "top.v",
        "line": 8,
        "item": 1,
        "detail": "",
    }
    assert points[1]["name"] == "top.v:9:1"
    assert points[1]["hit"] is False

    branch_points = [point for point in points if point["type"] == "branch"]
    assert len(branch_points) == 5
    assert [point["line"] for point in branch_points if not point["hit"]] == [12, 20]
    assert branch_points[0]["detail"] == "if (i == 16)"
    assert all("Count coming in to IF" not in point["detail"] for point in branch_points)


def test_parse_questa_fec_coverage_normalizes_scalar_rows():
    points = parse_questa_fec_coverage_report(QUESTA_CODE_DETAILS)

    assert len(points) == 8
    assert points[0] == {
        "name": "top.v:30:1:row1:a_0",
        "count": 2,
        "hit": True,
        "type": "expression",
        "source_file": "top.v",
        "line": 30,
        "item": 1,
        "row": 1,
        "fec_target": "a_0",
        "detail": "assign y = (a | b);",
        "evidence": "{ 00 }",
    }
    expression_holes = [
        point
        for point in points
        if point["type"] == "expression" and not point["hit"]
    ]
    condition_holes = [
        point
        for point in points
        if point["type"] == "condition" and not point["hit"]
    ]
    assert [point["fec_target"] for point in expression_holes] == ["b_1"]
    assert [point["fec_target"] for point in condition_holes] == ["ready_0"]


def test_parse_questa_fec_coverage_ignores_udp_rows_before_fec_table():
    points = parse_questa_fec_coverage_report(QUESTA_CODE_DETAILS)

    assert len(points) == 8
    names = {point["name"] for point in points}
    assert "top.v:30:1:row2:-11" not in names
    assert "top.v:30:1:row4:b_1" in names


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
        if command[1:5] == ["report", "-details", "-code", "sbce"]:
            return SimpleNamespace(returncode=0, stdout=QUESTA_CODE_DETAILS)
        if command[1:4] == ["report", "-cvg", "-details"]:
            return SimpleNamespace(returncode=0, stdout=QUESTA_FUNCTIONAL)
        if command[1:3] == ["report", "-xml"]:
            output = Path(command[command.index("-output") + 1])
            output.write_text("<coverage/>\n", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="xml report written\n")
        if command[1:4] == ["report", "-zeros", "-details"]:
            output = Path(command[command.index("-output") + 1])
            output.write_text("rtl/dut.sv:42 ZERO\n", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="zero report written\n")
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
        "-details",
        "-code",
        "sbce",
        result["merged"],
    ]
    assert commands[3] == [
        "/opt/questa/bin/vcover",
        "report",
        "-cvg",
        "-details",
        result["merged"],
    ]
    assert commands[4] == [
        "/opt/questa/bin/vcover",
        "report",
        "-xml",
        "-codeAll",
        "-output",
        str(Path(result["merged"]).with_name("details.xml")),
        result["merged"],
    ]
    assert commands[5] == [
        "/opt/questa/bin/vcover",
        "report",
        "-zeros",
        "-details",
        "-codeAll",
        "-output",
        str(Path(result["merged"]).with_name("zeros.txt")),
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
    assert payload["code_detail_status"] == "ok"
    assert payload["code_detail_points"] == 8
    assert payload["code_detail_holes"] == 3
    assert payload["fec_detail_status"] == "ok"
    assert payload["fec_detail_points"] == 8
    assert payload["fec_detail_holes"] == 2
    assert Path(result["code_report"]).read_text(encoding="utf-8") == QUESTA_CODE_DETAILS
    assert payload["functional_bins"] == 3
    assert payload["functional_snapshot_id"] == result["functional_snapshot_id"]
    evidence = payload["detailed_code_coverage_evidence"]
    assert evidence["xml"]["status"] == "captured"
    assert evidence["zero_detail"]["status"] == "captured"
    assert Path(evidence["xml"]["path"]).read_text(encoding="utf-8") == "<coverage/>\n"
    assert Path(evidence["zero_detail"]["path"]).read_text(encoding="utf-8") == "rtl/dut.sv:42 ZERO\n"
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


def test_coverage_holes_cli_supports_questa_statement_and_branch_items(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    project = _project(tmp_path)
    report_path = project.root / ".zddv" / "coverage" / "code-details.txt"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(QUESTA_CODE_DETAILS, encoding="utf-8")

    monkeypatch.setattr("zddv.cli.load_project", lambda path: project)
    args = SimpleNamespace(
        project=str(project.root),
        output=".zddv/coverage/holes.json",
        point_type=None,
        limit=50,
        show=10,
    )

    rc = cmd_coverage_holes(args)

    assert rc == 0
    output = capsys.readouterr().out
    assert "Coverage holes (all): 5 unhit point(s)" in output
    assert "[statement] top.v:9:1" in output
    assert "[branch] top.v:12:1 if (i == 16)" in output
    assert "[expression] top.v:30:1:row4:b_1" in output
    assert "[condition] top.v:40:1:row3:ready_0" in output

    payload = json.loads(
        (project.root / ".zddv" / "coverage" / "holes.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["by_type"] == {
        "branch": 2,
        "condition": 1,
        "expression": 1,
        "statement": 1,
    }
    statement = next(
        hole for hole in payload["holes"] if hole["type"] == "statement"
    )
    assert statement["source_file"] == "top.v"
    assert statement["line"] == 9
    assert statement["item"] == 1

    args.point_type = "expression"
    rc = cmd_coverage_holes(args)
    assert rc == 0
    expression_payload = json.loads(
        (project.root / ".zddv" / "coverage" / "holes.json").read_text(
            encoding="utf-8"
        )
    )
    assert expression_payload["by_type"] == {"expression": 1}
    assert expression_payload["holes"][0]["fec_target"] == "b_1"
    assert expression_payload["holes"][0]["row"] == 4


def test_coverage_cli_surfaces_questa_functional_snapshot(tmp_path: Path, monkeypatch, capsys):
    project = _project(tmp_path)
    monkeypatch.setattr("zddv.cli.load_project", lambda path: project)
    monkeypatch.setattr(
        "zddv.cli.merge_coverage",
        lambda loaded: {
            "inputs": ["run-a/coverage.ucdb"],
            "merged": "/tmp/coverage.ucdb",
            "summary": "/tmp/summary.txt",
            "metrics_path": "/tmp/metrics.json",
            "metrics": {
                "hit_points": 2,
                "total_points": 3,
                "hit_rate": 100.0 * 2 / 3,
                "tool_total_coverage": 66.67,
            },
            "snapshot_id": "cov-test",
            "report": "",
            "functional_bins": 3,
            "functional_snapshot_id": "fcov-test",
            "functional_report": "/tmp/functional.txt",
            "code_report": "/tmp/code-details.txt",
            "code_detail_status": "ok",
            "code_detail_points": 8,
            "code_detail_holes": 3,
            "fec_detail_status": "ok",
            "fec_detail_points": 8,
            "fec_detail_holes": 2,
            "detailed_code_coverage_evidence": {
                "xml": {"status": "captured", "path": "/tmp/details.xml"},
                "zero_detail": {"status": "captured", "path": "/tmp/zeros.txt"},
            },
        },
    )

    rc = cmd_coverage(SimpleNamespace(project=str(project.root)))

    assert rc == 0
    output = capsys.readouterr().out
    assert "Functional coverage bins: 3" in output
    assert "Functional snapshot: fcov-test" in output
    assert "Functional report: /tmp/functional.txt" in output
    assert (
        "Normalized Questa statement/branch coverage: ok 8 point(s), 3 hole(s)"
        in output
    )
    assert "Questa statement/branch detail: /tmp/code-details.txt" in output
    assert (
        "Normalized Questa condition/expression FEC coverage: "
        "ok 8 row(s), 2 hole(s)"
        in output
    )
    assert "Detailed code coverage XML: captured /tmp/details.xml" in output
    assert "Zero-hit source detail: captured /tmp/zeros.txt" in output


def test_questa_detailed_evidence_failure_is_nonfatal_and_does_not_reuse_stale_files(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    run_dir = (project.root / project.run_dir / "run-a").resolve()
    run_dir.mkdir(parents=True)
    (run_dir / "coverage.ucdb").write_text("fixture\n", encoding="utf-8")

    out_dir = (project.root / ".zddv" / "coverage").resolve()
    out_dir.mkdir(parents=True)
    (out_dir / "details.xml").write_text("stale\n", encoding="utf-8")
    (out_dir / "zeros.txt").write_text("stale\n", encoding="utf-8")

    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/questa/bin/vcover" if name == "vcover" else None,
    )

    def fake_run(command, cwd):
        if command[1] == "merge":
            output = Path(command[command.index("-out") + 1])
            output.write_text("merged\n", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="merge complete\n")
        if command[1:3] == ["report", "-summary"]:
            return SimpleNamespace(returncode=0, stdout=QUESTA_SUMMARY)
        if command[1:5] == ["report", "-details", "-code", "sbce"]:
            return SimpleNamespace(returncode=0, stdout="")
        if command[1:4] == ["report", "-cvg", "-details"]:
            return SimpleNamespace(returncode=0, stdout="")
        if "-xml" in command or "-zeros" in command:
            return SimpleNamespace(returncode=2, stdout="unsupported fixture\n")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("zddv.coverage._run", fake_run)

    result = merge_questa_coverage(project)
    evidence = result["detailed_code_coverage_evidence"]

    assert evidence["xml"]["status"] == "failed"
    assert evidence["xml"]["returncode"] == 2
    assert evidence["xml"]["diagnostic"] == "unsupported fixture"
    assert evidence["zero_detail"]["status"] == "failed"
    assert evidence["zero_detail"]["diagnostic"] == "unsupported fixture"
    assert not Path(evidence["xml"]["path"]).exists()
    assert not Path(evidence["zero_detail"]["path"]).exists()


def test_parse_questa_statement_xml_keeps_file_maps_scoped_per_instance(
    tmp_path: Path,
):
    xml_path = tmp_path / "statement.xml"
    xml_path.write_text(
        """<?xml version="1.0"?>
<coverage_report>
  <code_coverage_report lines="1" byInstance="1">
    <instanceData path="/tb/a" du="a">
      <sourceTable files="1"><fileMap fn="0" path="rtl/a.sv" /></sourceTable>
      <stmt fn="0" ln="5" st="1" hits="0" />
    </instanceData>
    <instanceData path="/tb/b" du="b">
      <sourceTable files="1"><fileMap fn="0" path="rtl/b.sv" /></sourceTable>
      <stmt fn="0" ln="7" st="1" hits="3" />
      <stmt fn="0" ln="8" st="2" hits="0" />
    </instanceData>
  </code_coverage_report>
</coverage_report>
""",
        encoding="utf-8",
    )

    points = parse_questa_statement_coverage_xml(xml_path)

    assert [point["name"] for point in points] == [
        "/tb/a|rtl/a.sv:5:stmt1",
        "/tb/b|rtl/b.sv:7:stmt1",
        "/tb/b|rtl/b.sv:8:stmt2",
    ]
    assert points[0]["hit"] is False
    assert points[1]["hit"] is True
    assert points[2]["file"] == "rtl/b.sv"


def test_write_questa_statement_holes_requests_by_instance_xml(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    merged = project.root / ".zddv" / "coverage" / "coverage.ucdb"
    merged.parent.mkdir(parents=True)
    merged.write_text("ucdb fixture\n", encoding="utf-8")

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/questa/bin/vcover" if name == "vcover" else None,
    )

    def fake_run(command, cwd):
        captured["command"] = list(command)
        xml_path = Path(command[command.index("-output") + 1])
        xml_path.write_text(
            """<?xml version="1.0"?>
<coverage_report>
  <code_coverage_report lines="1" byInstance="1">
    <instanceData path="/tb/dut" du="dut">
      <sourceTable files="1"><fileMap fn="0" path="rtl/dut.sv" /></sourceTable>
      <stmt fn="0" ln="20" st="1" hits="0" />
      <stmt fn="0" ln="21" st="1" hits="9" />
    </instanceData>
  </code_coverage_report>
</coverage_report>
""",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="report complete\n")

    monkeypatch.setattr("zddv.coverage._run", fake_run)

    output = project.root / ".zddv" / "coverage" / "statement-holes.json"
    report = write_questa_statement_hole_report(project, output, limit=20)

    command = captured["command"]
    assert command[:8] == [
        "/opt/questa/bin/vcover",
        "report",
        "-xml",
        "-notimestamps",
        "-setdefault",
        "byinstance",
        "-code",
        "s",
    ]
    assert command[-1] == str(merged.resolve())
    assert report["source"] == "questa-vcover-xml-byinstance"
    assert report["total_holes"] == 1
    assert report["holes"][0]["scope"] == "/tb/dut"
    assert report["holes"][0]["file"] == "rtl/dut.sv"
    assert report["holes"][0]["line"] == 20
    assert report["holes"][0]["statement"] == 1


def test_coverage_holes_cli_routes_statement_to_by_instance_xml(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    project = _project(tmp_path)
    monkeypatch.setattr("zddv.cli.load_project", lambda path: project)
    captured: dict[str, object] = {}

    def fake_statement_report(project_arg, output, *, limit):
        captured["project"] = project_arg
        captured["output"] = Path(output)
        captured["limit"] = limit
        return {
            "filter_type": "statement",
            "total_holes": 1,
            "reported_holes": 1,
            "by_type": {"statement": 1},
            "holes": [
                {
                    "type": "statement",
                    "name": "/tb/dut|rtl/dut.sv:20:stmt1",
                    "count": 0,
                }
            ],
            "xml": "/tmp/statement-by-instance.xml",
            "path": str(project.root / ".zddv/coverage/holes.json"),
        }

    monkeypatch.setattr(
        "zddv.cli.write_questa_statement_hole_report",
        fake_statement_report,
    )

    rc = cmd_coverage_holes(
        SimpleNamespace(
            project=str(project.root),
            output=".zddv/coverage/holes.json",
            point_type="statement",
            limit=7,
            show=2,
        )
    )

    assert rc == 0
    assert captured["project"] is project
    assert captured["limit"] == 7
    output = capsys.readouterr().out
    assert "Coverage holes (statement): 1 unhit point(s)" in output
    assert "Questa XML: /tmp/statement-by-instance.xml" in output


def test_coverage_holes_cli_rejects_missing_scalar_fec_rows(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    report_path = project.root / ".zddv" / "coverage" / "code-details.txt"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(
        """Expression Coverage for file top.v --

Line 50 Item 1 assign y = bus_a & bus_b;
FEC Table for multibit expression
Bit 0: ***0*** 1
Bit 1: 2 ***0***
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("zddv.cli.load_project", lambda path: project)

    with pytest.raises(
        RuntimeError,
        match="No normalized Questa expression FEC rows found",
    ):
        cmd_coverage_holes(
            SimpleNamespace(
                project=str(project.root),
                output=".zddv/coverage/holes.json",
                point_type="expression",
                limit=10,
                show=2,
            )
        )


def test_coverage_holes_cli_rejects_unimplemented_questa_item_type(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    monkeypatch.setattr("zddv.cli.load_project", lambda path: project)

    with pytest.raises(
        RuntimeError,
        match="supports --type statement, branch, condition, or expression",
    ):
        cmd_coverage_holes(
            SimpleNamespace(
                project=str(project.root),
                output=".zddv/coverage/holes.json",
                point_type="toggle",
                limit=10,
                show=2,
            )
        )
