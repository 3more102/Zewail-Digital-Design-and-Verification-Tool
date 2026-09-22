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
    Conditions                       4         3         1     75.00

==============================Condition Details===============================

Condition Coverage for file top.v --

Line 24 Item 1 ((ready && valid) || retry)
Condition totals: 3 hits of 4 rows = 75.0%

Rows: hits FEC Targets Non-Masking Condition(s)
Row 1: 4 ready_0 (valid || retry)
Row 2: 2 ready_1 (valid || retry)
Row 3: ***0*** valid_0 (ready && ~retry)
Row 4: 1 valid_1 (ready && ~retry)

FSM Coverage:
    Enabled Coverage            Bins      Hits    Misses % Covered
    ----------------            ----      ----    ------ ---------
    FSM States                      3         2         1     66.67
    FSM Transitions                 2         1         1     50.00

==================================FSM Details===================================

FSM Coverage for file top.v --

FSM_ID: state
Current State Object : state
----------------------
State Value MapInfo :
--------------------
Line        State Name            Value
----        ----------            -----
59          IDLE                  1
60          RUN                   2
61          ERROR                 4

Covered States :
----------------
                State         Hit_count
                -----         ---------
                IDLE                  5
                RUN                   3

Covered Transitions :
--------------------
Line          Trans_ID          Hit_count          Transition
----          --------          ---------          ----------
42                0                    5             IDLE -> RUN

Uncovered States :
------------------
                State
                -----
                ERROR

Uncovered Transitions :
----------------------
Line          Trans_ID          Transition
----          --------          ----------
44                1             RUN -> ERROR
"""

QUESTA_MULTIBIT_EXPRESSION_DETAILS = """Coverage Report by file with details

=================================================================================
=== File: ttest.sv
=================================================================================
Expression Coverage:
    Enabled Coverage            Bins      Hits    Misses % Covered
    ----------------            ----      ----    ------ ---------
    Expressions                    12         2        10     16.66

==============================Expression Details==============================

Expression Coverage for file ttest.sv --

Focused Expression View
Line 28 Item 1 ((a & b) | (c & d))
Expression totals: 2 of 12 input terms covered = 16.66%

Rows: FEC Target                  Hits
                                  i = <0> <1> <2>
Row 1: a[i]_0                     1 ***0*** ***0*** (~(c[i] & d[i]) && b[i])
Row 2: a[i]_1                ***0*** 1 ***0*** (~(c[i] & d[i]) && b[i])
Row 3: b[i]_0                ***0*** 1 ***0*** (~(c[i] & d[i]) && a[i])
Row 4: b[i]_1                ***0*** 1 ***0*** (~(c[i] & d[i]) && a[i])
Row 5: c[i]_0                     1 ***0*** ***0*** (~(a[i] & b[i]) && d[i])
Row 6: c[i]_1                     1 ***0*** ***0*** (~(a[i] & b[i]) && d[i])
Row 7: d[i]_0                ***0*** 1 ***0*** (~(a[i] & b[i]) && c[i])
Row 8: d[i]_1                     1 ***0*** ***0*** (~(a[i] & b[i]) && c[i])
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


def test_parse_questa_code_coverage_normalizes_statement_branch_condition_expression_items():
    points = parse_questa_code_coverage_report(QUESTA_CODE_DETAILS)

    assert len(points) == 21
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

    condition_points = [point for point in points if point["type"] == "condition"]
    assert len(condition_points) == 4
    assert condition_points[0]["name"] == (
        "top.v:24:1:row1 ready_0 (valid || retry)"
    )
    assert condition_points[0]["condition"] == "((ready && valid) || retry)"
    assert condition_points[0]["row"] == 1
    assert condition_points[0]["fec_target"] == "ready_0"
    assert condition_points[0]["detail"] == "(valid || retry)"
    assert condition_points[2]["hit"] is False
    assert condition_points[2]["fec_target"] == "valid_0"
    assert condition_points[2]["detail"] == "(ready && ~retry)"

    expression_points = [point for point in points if point["type"] == "expression"]
    assert len(expression_points) == 4
    assert expression_points[0]["name"] == "top.v:30:1:row1 a_0 { 00 }"
    assert expression_points[0]["expression"] == "assign y = (a | b);"
    assert expression_points[0]["fec_context"] == "assign y = (a | b);"
    assert expression_points[3]["hit"] is False
    assert expression_points[3]["fec_target"] == "b_1"
    assert expression_points[3]["evidence"] == "{ 01 }"
    assert all(point["fec_target"] != "-11" for point in expression_points)

    fsm_points = [point for point in points if point["type"] == "fsm"]
    assert len(fsm_points) == 5
    fsm_states = [point for point in fsm_points if point["fsm_kind"] == "state"]
    assert [point["state"] for point in fsm_states] == ["IDLE", "RUN", "ERROR"]
    assert fsm_states[2]["hit"] is False
    assert fsm_states[2]["count"] == 0
    fsm_transitions = [
        point for point in fsm_points if point["fsm_kind"] == "transition"
    ]
    assert len(fsm_transitions) == 2
    assert fsm_transitions[0]["line"] == 42
    assert fsm_transitions[0]["transition_id"] == 0
    assert fsm_transitions[0]["transition"] == "IDLE -> RUN"
    assert fsm_transitions[1]["hit"] is False
    assert fsm_transitions[1]["line"] == 44
    assert fsm_transitions[1]["transition_id"] == 1
    assert fsm_transitions[1]["transition"] == "RUN -> ERROR"


def test_parse_questa_multibit_expression_normalizes_input_term_bits():
    points = parse_questa_code_coverage_report(QUESTA_MULTIBIT_EXPRESSION_DETAILS)

    expression_points = [
        point
        for point in points
        if point["type"] == "expression" and point.get("multibit") is True
    ]
    assert len(expression_points) == 12
    assert sum(point["hit"] for point in expression_points) == 2

    by_target = {point["fec_target"]: point for point in expression_points}
    assert by_target["b[1]"]["hit"] is True
    assert by_target["c[0]"]["hit"] is True
    assert by_target["a[0]"]["hit"] is False
    assert by_target["a[0]"]["fec_hits"] == {"0": 1, "1": 0}
    assert by_target["b[1]"]["fec_hits"] == {"0": 1, "1": 1}
    assert by_target["b[1]"]["bit"] == 1
    assert by_target["b[1]"]["expression"] == "((a & b) | (c & d))"
    assert by_target["b[1]"]["name"] == "ttest.sv:28:1:b[1]"


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
        if command[1:6] == ["report", "-details", "-dumptables", "-code", "sbcef"]:
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
        if command[1:4] == ["report", "-details", "-multibitverbose"]:
            output = Path(command[command.index("-output") + 1])
            output.write_text(
                QUESTA_MULTIBIT_EXPRESSION_DETAILS,
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=0, stdout="multibit report written\n")
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
        "-dumptables",
        "-code",
        "sbcef",
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
    assert commands[6] == [
        "/opt/questa/bin/vcover",
        "report",
        "-details",
        "-multibitverbose",
        "-code",
        "e",
        "-output",
        str(Path(result["merged"]).with_name("multibit-expression.txt")),
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
    assert payload["code_detail_points"] == 21
    assert payload["code_detail_holes"] == 7
    assert payload["multibit_expression_status"] == "ok"
    assert payload["multibit_expression_points"] == 12
    assert payload["multibit_expression_holes"] == 10
    assert Path(result["code_report"]).read_text(encoding="utf-8") == QUESTA_CODE_DETAILS
    assert payload["functional_bins"] == 3
    assert payload["functional_snapshot_id"] == result["functional_snapshot_id"]
    evidence = payload["detailed_code_coverage_evidence"]
    assert evidence["xml"]["status"] == "captured"
    assert evidence["zero_detail"]["status"] == "captured"
    assert evidence["multibit_expression"]["status"] == "captured"
    assert Path(evidence["xml"]["path"]).read_text(encoding="utf-8") == "<coverage/>\n"
    assert Path(evidence["zero_detail"]["path"]).read_text(encoding="utf-8") == "rtl/dut.sv:42 ZERO\n"
    assert Path(evidence["multibit_expression"]["path"]).read_text(
        encoding="utf-8"
    ) == QUESTA_MULTIBIT_EXPRESSION_DETAILS
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


def test_coverage_holes_cli_supports_questa_statement_branch_condition_expression_fsm_items(
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
    assert "Coverage holes (all): 7 unhit point(s)" in output
    assert "[statement] top.v:9:1" in output
    assert "[branch] top.v:12:1 if (i == 16)" in output
    assert "[condition] top.v:24:1:row3 valid_0 (ready && ~retry)" in output
    assert "[expression] top.v:30:1:row4 b_1 { 01 }" in output
    assert "[fsm] top.v:state:state:ERROR" in output
    assert "[fsm] top.v:44:state:transition:1 RUN -> ERROR" in output

    payload = json.loads(
        (project.root / ".zddv" / "coverage" / "holes.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["by_type"] == {
        "branch": 2,
        "condition": 1,
        "expression": 1,
        "fsm": 2,
        "statement": 1,
    }
    statement = next(
        hole for hole in payload["holes"] if hole["type"] == "statement"
    )
    assert statement["source_file"] == "top.v"
    assert statement["line"] == 9
    assert statement["item"] == 1

    condition = next(
        hole for hole in payload["holes"] if hole["type"] == "condition"
    )
    assert condition["source_file"] == "top.v"
    assert condition["line"] == 24
    assert condition["item"] == 1
    assert condition["row"] == 3
    assert condition["condition"] == "((ready && valid) || retry)"
    assert condition["fec_target"] == "valid_0"

    condition_args = SimpleNamespace(
        project=str(project.root),
        output=".zddv/coverage/condition-holes.json",
        point_type="condition",
        limit=50,
        show=10,
    )
    assert cmd_coverage_holes(condition_args) == 0
    filtered_output = capsys.readouterr().out
    assert "Coverage holes (condition): 1 unhit point(s)" in filtered_output
    filtered = json.loads(
        (project.root / ".zddv" / "coverage" / "condition-holes.json").read_text(
            encoding="utf-8"
        )
    )
    assert filtered["by_type"] == {"condition": 1}
    assert filtered["holes"][0]["row"] == 3
    assert filtered["holes"][0]["fec_target"] == "valid_0"

    expression_args = SimpleNamespace(
        project=str(project.root),
        output=".zddv/coverage/expression-holes.json",
        point_type="expression",
        limit=50,
        show=10,
    )
    assert cmd_coverage_holes(expression_args) == 0
    expression_output = capsys.readouterr().out
    assert "Coverage holes (expression): 1 unhit point(s)" in expression_output
    expression_report = json.loads(
        (project.root / ".zddv" / "coverage" / "expression-holes.json").read_text(
            encoding="utf-8"
        )
    )
    assert expression_report["by_type"] == {"expression": 1}
    assert expression_report["holes"][0]["row"] == 4
    assert expression_report["holes"][0]["fec_target"] == "b_1"
    assert expression_report["holes"][0]["expression"] == "assign y = (a | b);"

    fsm_args = SimpleNamespace(
        project=str(project.root),
        output=".zddv/coverage/fsm-holes.json",
        point_type="fsm",
        limit=50,
        show=10,
    )
    assert cmd_coverage_holes(fsm_args) == 0
    fsm_output = capsys.readouterr().out
    assert "Coverage holes (fsm): 2 unhit point(s)" in fsm_output
    fsm_report = json.loads(
        (project.root / ".zddv" / "coverage" / "fsm-holes.json").read_text(
            encoding="utf-8"
        )
    )
    assert fsm_report["by_type"] == {"fsm": 2}
    assert {hole["fsm_kind"] for hole in fsm_report["holes"]} == {
        "state",
        "transition",
    }
    transition_hole = next(
        hole for hole in fsm_report["holes"] if hole["fsm_kind"] == "transition"
    )
    assert transition_hole["line"] == 44
    assert transition_hole["transition_id"] == 1
    assert transition_hole["transition"] == "RUN -> ERROR"


def test_coverage_holes_cli_combines_scalar_and_multibit_expression_evidence(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    project = _project(tmp_path)
    coverage_dir = project.root / ".zddv" / "coverage"
    coverage_dir.mkdir(parents=True)
    (coverage_dir / "code-details.txt").write_text(
        QUESTA_CODE_DETAILS,
        encoding="utf-8",
    )
    (coverage_dir / "multibit-expression.txt").write_text(
        QUESTA_MULTIBIT_EXPRESSION_DETAILS,
        encoding="utf-8",
    )
    monkeypatch.setattr("zddv.cli.load_project", lambda path: project)

    rc = cmd_coverage_holes(
        SimpleNamespace(
            project=str(project.root),
            output=".zddv/coverage/expression-holes.json",
            point_type="expression",
            limit=50,
            show=20,
        )
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "Coverage holes (expression): 11 unhit point(s)" in output
    payload = json.loads(
        (coverage_dir / "expression-holes.json").read_text(encoding="utf-8")
    )
    assert payload["by_type"] == {"expression": 11}
    multibit_holes = [
        hole for hole in payload["holes"] if hole.get("multibit") is True
    ]
    assert len(multibit_holes) == 10
    a0 = next(hole for hole in multibit_holes if hole["fec_target"] == "a[0]")
    assert a0["fec_hits"] == {"0": 1, "1": 0}
    assert a0["bit"] == 0


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
            "code_detail_points": 16,
            "code_detail_holes": 5,
            "multibit_expression_report": "/tmp/multibit-expression.txt",
            "multibit_expression_status": "ok",
            "multibit_expression_points": 12,
            "multibit_expression_holes": 10,
            "detailed_code_coverage_evidence": {
                "xml": {"status": "captured", "path": "/tmp/details.xml"},
                "zero_detail": {"status": "captured", "path": "/tmp/zeros.txt"},
                "multibit_expression": {
                    "status": "captured",
                    "path": "/tmp/multibit-expression.txt",
                },
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
        "Normalized Questa statement/branch/condition/expression/FSM coverage: "
        "ok 16 point(s), 5 hole(s)"
        in output
    )
    assert (
        "Questa statement/branch/condition/expression/FSM detail: /tmp/code-details.txt"
        in output
    )
    assert (
        "Normalized Questa multibit expression coverage: "
        "ok 12 point(s), 10 hole(s)"
        in output
    )
    assert "Questa multibit expression detail: /tmp/multibit-expression.txt" in output
    assert "Detailed code coverage XML: captured /tmp/details.xml" in output
    assert "Zero-hit source detail: captured /tmp/zeros.txt" in output
    assert (
        "Questa multibit expression detail: captured /tmp/multibit-expression.txt"
        in output
    )


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
    (out_dir / "multibit-expression.txt").write_text(
        "stale\n", encoding="utf-8"
    )

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
        if command[1:6] == ["report", "-details", "-dumptables", "-code", "sbcef"]:
            return SimpleNamespace(returncode=0, stdout="")
        if command[1:4] == ["report", "-cvg", "-details"]:
            return SimpleNamespace(returncode=0, stdout="")
        if "-xml" in command or "-zeros" in command or "-multibitverbose" in command:
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
    assert evidence["multibit_expression"]["status"] == "failed"
    assert evidence["multibit_expression"]["diagnostic"] == "unsupported fixture"
    assert not Path(evidence["xml"]["path"]).exists()
    assert not Path(evidence["zero_detail"]["path"]).exists()
    assert not Path(evidence["multibit_expression"]["path"]).exists()


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


def test_coverage_holes_cli_rejects_missing_scalar_expression_fec_rows(
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
        match=r"supports --type statement, branch, condition, expression, or fsm",
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
