from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zddv.cli import cmd_coverage, cmd_coverage_history
from zddv.config import ProjectConfig
from zddv.coverage import (
    merge_vcs_coverage,
    parse_vcs_urg_dashboard,
    parse_vcs_urg_instance_counts,
    parse_vcs_urg_module_counts,
)
from zddv.storage import list_coverage_score_snapshots


VCS_MODINFO = """Line Coverage for Module : dut
TOTAL 120 110 91.67
Cond Coverage for Module : dut
Conditions 20 18 90.00
Toggle Coverage for Module : dut
Total Bits 40 30 75.00
FSM Coverage for Module : dut
Transitions 5 4 80.00
Transitions 3 2 66.67
Branch Coverage for Module : dut
Branches 10 8 80.00
Line Coverage for Module : sub
TOTAL 30 25 83.33
Condition Coverage for Module : sub
Conditions 6 5 83.33
Toggle Coverage for Module : sub
Total Bits 12 9 75.00
FSM Coverage for Module : sub
Transitions 2 1 50.00
Branch Coverage for Module : sub
Branches 4 3 75.00
"""


def _project(tmp_path: Path) -> ProjectConfig:
    root = tmp_path / "demo"
    (root / "rtl").mkdir(parents=True)
    (root / "tb").mkdir()
    (root / "rtl" / "dut.sv").write_text(
        "module dut; endmodule\n",
        encoding="utf-8",
    )
    (root / "tb" / "tb_top.sv").write_text(
        "module tb_top; dut u_dut(); endmodule\n",
        encoding="utf-8",
    )
    return ProjectConfig(
        root=root,
        name="demo",
        top="tb_top",
        simulator="vcs",
        rtl=["rtl/*.sv"],
        tb=["tb/*.sv"],
        waveform=False,
        coverage=True,
    )


def test_parse_vcs_urg_module_counts_normalizes_all_scored_code_metrics(
    tmp_path: Path,
):
    modinfo = tmp_path / "modinfo.txt"
    modinfo.write_text(VCS_MODINFO, encoding="utf-8")

    result = parse_vcs_urg_module_counts(modinfo)

    assert result["source"] == "urg-modinfo"
    assert len(result["modules"]) == 10
    counts = result["by_metric_counts"]
    assert counts["module_line"] == {
        "covered": 135,
        "total": 150,
        "hit_rate": pytest.approx(90.0),
    }
    assert counts["module_condition"] == {
        "covered": 23,
        "total": 26,
        "hit_rate": pytest.approx(100.0 * 23 / 26),
    }
    assert counts["module_toggle"] == {
        "covered": 39,
        "total": 52,
        "hit_rate": pytest.approx(75.0),
    }
    assert counts["module_fsm"] == {
        "covered": 7,
        "total": 10,
        "hit_rate": pytest.approx(70.0),
    }
    assert counts["module_branch"] == {
        "covered": 11,
        "total": 14,
        "hit_rate": pytest.approx(100.0 * 11 / 14),
    }
    dut_fsm = next(
        record
        for record in result["modules"]
        if record["metric"] == "fsm" and record["module"] == "dut"
    )
    assert dut_fsm["covered"] == 6
    assert dut_fsm["total"] == 8
    assert dut_fsm["hit_rate"] == pytest.approx(75.0)


def test_parse_vcs_urg_module_counts_rejects_invalid_covered_total(
    tmp_path: Path,
):
    modinfo = tmp_path / "modinfo.txt"
    modinfo.write_text(
        """Toggle Coverage for Module : dut
Total Bits 4 5 125.00
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Invalid URG toggle module total"):
        parse_vcs_urg_module_counts(modinfo)


def test_parse_vcs_urg_instance_counts_aggregates_reported_instance_rows(
    tmp_path: Path,
):
    report_dir = tmp_path / "urg-report"
    report_dir.mkdir()
    (report_dir / "mod0.html").write_text(
        """<html><body>
<h2>Line Coverage for Module : dut</h2>
<table><tr><th></th><th>Line No.</th><th>Total</th><th>Covered</th><th>Percent</th></tr>
<tr><td>TOTAL</td><td></td><td>99</td><td>99</td><td>100.00</td></tr></table>
<h2>Line Coverage for Instance : tb.dut</h2>
<table><tr><th></th><th>Line No.</th><th>Total</th><th>Covered</th><th>Percent</th></tr>
<tr><td>TOTAL</td><td></td><td>12</td><td>9</td><td>75.00</td></tr></table>
<h2>Cond Coverage for Instance : tb.dut</h2>
<table><tr><th></th><th>Total</th><th>Covered</th><th>Percent</th></tr>
<tr><td>Conditions</td><td>20</td><td>18</td><td>90.00</td></tr></table>
<h2>Toggle Coverage for Instance : tb.dut</h2>
<table><tr><th></th><th>Total</th><th>Covered</th><th>Percent</th></tr>
<tr><td>Total Bits</td><td>40</td><td>30</td><td>75.00</td></tr></table>
<h2>FSM Coverage for Instance : tb.dut</h2>
Summary for FSM :: state_q
<table><tr><th></th><th>Total</th><th>Covered</th><th>Percent</th></tr>
<tr><td>States</td><td>4</td><td>4</td><td>100.00</td></tr>
<tr><td>Transitions</td><td>5</td><td>4</td><td>80.00</td></tr>
<tr><td>Sequences</td><td>0</td><td>0</td><td></td></tr></table>
<h2>Branch Coverage for Instance : tb.dut</h2>
<table><tr><th></th><th>Line No.</th><th>Total</th><th>Covered</th><th>Percent</th></tr>
<tr><td>Branches</td><td></td><td>10</td><td>8</td><td>80.00</td></tr></table>
</body></html>
""",
        encoding="utf-8",
    )
    (report_dir / "mod1.html").write_text(
        """<html><body>
<h2>Line Coverage for Instance : tb.dut.u_sub</h2>
<table><tr><th></th><th>Line No.</th><th>Total</th><th>Covered</th><th>Percent</th></tr>
<tr><td>TOTAL</td><td></td><td>3</td><td>2</td><td>66.67</td></tr></table>
</body></html>
""",
        encoding="utf-8",
    )
    (report_dir / "mod0_1.html").write_text(
        """<html><body>
<h2>Line Coverage for Instance : tb.dut</h2>
<table><tr><th></th><th>Line No.</th><th>Total</th><th>Covered</th><th>Percent</th></tr>
<tr><td>TOTAL</td><td></td><td>12</td><td>9</td><td>75.00</td></tr></table>
</body></html>
""",
        encoding="utf-8",
    )

    result = parse_vcs_urg_instance_counts(report_dir)

    assert result["files_scanned"] == 3
    assert result["instance_sections"] == 7
    assert result["unique_records"] == 8
    assert result["duplicate_records"] == 1
    assert result["by_metric_counts"]["line"] == {
        "covered": 11,
        "total": 15,
        "hit_rate": pytest.approx(73.33333333333333),
    }
    assert result["by_metric_counts"]["condition"]["covered"] == 18
    assert result["by_metric_counts"]["condition"]["total"] == 20
    assert result["by_metric_counts"]["toggle"]["covered"] == 30
    assert result["by_metric_counts"]["branch"]["covered"] == 8
    assert result["by_metric_counts"]["fsm_state"]["covered"] == 4
    assert result["by_metric_counts"]["fsm_transition"] == {
        "covered": 4,
        "total": 5,
        "hit_rate": pytest.approx(80.0),
    }
    assert result["by_metric_counts"]["fsm_sequence"] == {
        "covered": 0,
        "total": 0,
        "hit_rate": None,
    }


def test_merge_vcs_coverage_uses_urg_and_retains_report_evidence(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    run_root = (project.root / project.run_dir).resolve()
    inputs = []
    for name in ("run-a", "run-b"):
        coverage = run_root / name / "coverage.vdb"
        coverage.mkdir(parents=True)
        (coverage / "fixture.txt").write_text(name, encoding="utf-8")
        inputs.append(str(coverage))

    out_dir = (project.root / ".zddv" / "coverage").resolve()
    stale_merged = out_dir / "coverage.vdb"
    stale_report = out_dir / "urg-report"
    stale_brief = out_dir / "urg-brief"
    stale_merged.mkdir(parents=True)
    stale_report.mkdir()
    stale_brief.mkdir()
    (stale_merged / "stale").write_text("old", encoding="utf-8")
    (stale_report / "stale").write_text("old", encoding="utf-8")
    (stale_brief / "stale").write_text("old", encoding="utf-8")

    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/synopsys/bin/urg" if name == "urg" else None,
    )
    captured: dict[str, object] = {"commands": []}

    def fake_run(command, cwd):
        captured["commands"].append(list(command))
        captured["cwd"] = Path(cwd)
        report_dir = Path(cwd) / command[command.index("-report") + 1]
        if "-show" in command:
            assert stale_merged.exists()
            assert command[command.index("-dir") + 1] == "coverage.vdb"
            report_dir.mkdir()
            (report_dir / "mod0.txt").write_text(
                "Line Coverage for Module dut\nuncovered line 12\n",
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=0, stdout="URG brief complete\n")
        assert not stale_merged.exists()
        assert not stale_report.exists()
        assert not stale_brief.exists()
        (Path(cwd) / command[command.index("-dbname") + 1]).mkdir()
        report_dir.mkdir()
        (report_dir / "dashboard.txt").write_text(
            """Unified Coverage Report

Total Coverage Summary
SCORE  LINE  COND  TOGGLE  FSM  BRANCH  ASSERT  GROUP
97.74  99.03  97.75  98.53  100.00  99.01  98.63  91.19

Total Groups Coverage Summary
COVERED  EXPECTED  SCORE  COVERED  EXPECTED  INST SCORE  WEIGHT
491  528  92.99  490  527  92.98  1
""",
            encoding="utf-8",
        )
        (report_dir / "mod0.html").write_text(
            """<html><body>
<h2>Line Coverage for Instance : tb.dut</h2>
<table><tr><th></th><th>Line No.</th><th>Total</th><th>Covered</th><th>Percent</th></tr>
<tr><td>TOTAL</td><td></td><td>198</td><td>190</td><td>95.96</td></tr></table>
<h2>Cond Coverage for Instance : tb.dut</h2>
<table><tr><th></th><th>Total</th><th>Covered</th><th>Percent</th></tr>
<tr><td>Conditions</td><td>180</td><td>168</td><td>93.33</td></tr></table>
</body></html>
""",
            encoding="utf-8",
        )
        (report_dir / "modinfo.txt").write_text(VCS_MODINFO, encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="URG merge complete\n")

    monkeypatch.setattr("zddv.coverage._run", fake_run)

    result = merge_vcs_coverage(project)

    commands = captured["commands"]
    assert commands[0] == [
        "/opt/synopsys/bin/urg",
        "-dir",
        *inputs,
        "-dbname",
        "coverage.vdb",
        "-report",
        "urg-report",
        "-format",
        "both",
    ]
    assert commands[1] == [
        "/opt/synopsys/bin/urg",
        "-dir",
        "coverage.vdb",
        "-report",
        "urg-brief",
        "-format",
        "text",
        "-show",
        "brief",
        "-metric",
        "line+cond+fsm+tgl+branch",
    ]
    command = commands[0]
    assert captured["cwd"] == out_dir
    assert Path(result["merged"]).is_dir()
    assert Path(result["report_dir"]).is_dir()
    assert result["brief_status"] == "captured"
    assert Path(result["brief_report_dir"]).is_dir()
    assert result["brief_error"] is None
    assert result["metrics_status"] == "normalized"
    assert result["metrics"]["tool_total_coverage"] == pytest.approx(97.74)
    assert result["metrics"]["by_metric"]["line"] == pytest.approx(99.03)
    assert result["metrics"]["by_metric"]["group"] == pytest.approx(91.19)
    assert result["metrics"]["by_metric_counts"]["group"] == {
        "covered": 491,
        "total": 528,
        "hit_rate": pytest.approx(92.99),
    }
    assert result["metrics"]["by_metric_counts"]["group_instance"] == {
        "covered": 490,
        "total": 527,
        "hit_rate": pytest.approx(92.98),
    }
    assert result["metrics"]["count_status"] == "normalized"
    assert result["metrics"]["code_count_status"] == "normalized"
    assert result["metrics"]["by_metric_counts"]["line"]["covered"] == 190
    assert result["metrics"]["by_metric_counts"]["line"]["total"] == 198
    assert result["metrics"]["by_metric_counts"]["condition"]["covered"] == 168
    assert result["module_counts_status"] == "normalized"
    assert result["module_counts_error"] is None
    assert result["metrics"]["by_metric_counts"]["module_line"]["covered"] == 135
    assert result["metrics"]["by_metric_counts"]["module_condition"]["total"] == 26
    assert result["metrics"]["by_metric_counts"]["module_toggle"]["covered"] == 39
    assert result["metrics"]["by_metric_counts"]["module_fsm"] == {
        "covered": 7,
        "total": 10,
        "hit_rate": pytest.approx(70.0),
    }
    assert result["metrics"]["by_metric_counts"]["module_branch"]["total"] == 14
    assert result["snapshot_id"] is not None
    assert Path(result["summary"]).name == "dashboard.txt"

    snapshots = list_coverage_score_snapshots(project, limit=5)
    assert len(snapshots) == 1
    assert snapshots[0]["snapshot_id"] == result["snapshot_id"]
    assert snapshots[0]["score"] == pytest.approx(97.74)
    assert snapshots[0]["by_metric"]["branch"] == pytest.approx(99.01)
    assert snapshots[0]["by_metric_counts"]["group"]["covered"] == 491
    assert snapshots[0]["by_metric_counts"]["group"]["total"] == 528
    assert snapshots[0]["by_metric_counts"]["group_instance"]["covered"] == 490
    assert snapshots[0]["by_metric_counts"]["line"]["covered"] == 190
    assert snapshots[0]["by_metric_counts"]["condition"]["total"] == 180
    assert snapshots[0]["by_metric_counts"]["module_condition"]["covered"] == 23
    assert snapshots[0]["by_metric_counts"]["module_toggle"]["total"] == 52
    assert snapshots[0]["by_metric_counts"]["module_fsm"]["covered"] == 7

    manifest = json.loads(
        Path(result["metrics_path"]).read_text(encoding="utf-8")
    )
    assert manifest["status"] == "merged-report-captured"
    assert manifest["metrics_status"] == "normalized"
    assert manifest["input_count"] == 2
    assert manifest["inputs"] == inputs
    assert manifest["command"] == command
    assert manifest["brief_status"] == "captured"
    assert manifest["module_counts_status"] == "normalized"
    assert Path(manifest["modinfo"]).name == "modinfo.txt"
    assert Path(manifest["brief_report_dir"]).name == "urg-brief"
    assert manifest["brief_command"] == commands[1]
    assert manifest["metrics"]["tool_total_coverage"] == pytest.approx(97.74)
    assert manifest["metrics"]["by_metric_counts"]["group"]["covered"] == 491
    assert manifest["metrics"]["by_metric_counts"]["group_instance"]["total"] == 527
    assert manifest["metrics"]["by_metric_counts"]["line"]["covered"] == 190


def test_merge_vcs_coverage_requires_per_run_vdb(tmp_path: Path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/synopsys/bin/urg" if name == "urg" else None,
    )

    with pytest.raises(RuntimeError, match="No coverage.vdb directories"):
        merge_vcs_coverage(project)


def test_vcs_coverage_cli_reports_pending_normalization(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    project = _project(tmp_path)
    monkeypatch.setattr("zddv.cli.load_project", lambda path: project)
    monkeypatch.setattr(
        "zddv.cli.merge_coverage",
        lambda loaded: {
            "inputs": ["run-a/coverage.vdb", "run-b/coverage.vdb"],
            "merged": "/tmp/coverage.vdb",
            "summary": "/tmp/urg-report",
            "metrics_path": "/tmp/vcs-coverage.json",
            "metrics": None,
            "snapshot_id": None,
            "metrics_status": "pending-normalization",
            "report": "URG merge complete\n",
        },
    )

    rc = cmd_coverage(SimpleNamespace(project=str(project.root)))

    assert rc == 0
    output = capsys.readouterr().out
    assert "Coverage inputs: 2" in output
    assert "Merged coverage: /tmp/coverage.vdb" in output
    assert "Summary: /tmp/urg-report" in output
    assert "Coverage metrics: pending-normalization" in output
    assert "Coverage evidence: /tmp/vcs-coverage.json" in output
    assert "Snapshot: None" not in output


def test_merge_vcs_coverage_requires_urg(tmp_path: Path, monkeypatch):
    project = _project(tmp_path)
    coverage = (project.root / project.run_dir / "run-a" / "coverage.vdb").resolve()
    coverage.mkdir(parents=True)
    monkeypatch.setattr("zddv.coverage.shutil.which", lambda name: None)

    with pytest.raises(RuntimeError, match="Synopsys URG was not found"):
        merge_vcs_coverage(project)


def test_merge_vcs_coverage_requires_expected_outputs(tmp_path: Path, monkeypatch):
    project = _project(tmp_path)
    coverage = (project.root / project.run_dir / "run-a" / "coverage.vdb").resolve()
    coverage.mkdir(parents=True)

    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/synopsys/bin/urg" if name == "urg" else None,
    )
    monkeypatch.setattr(
        "zddv.coverage._run",
        lambda command, cwd: SimpleNamespace(
            returncode=0,
            stdout="URG returned success without artifacts\n",
        ),
    )

    with pytest.raises(RuntimeError, match="VCS coverage merge/report failed"):
        merge_vcs_coverage(project)



def test_parse_vcs_urg_dashboard_accepts_missing_metric_placeholder(tmp_path: Path):
    dashboard = tmp_path / "dashboard.txt"
    dashboard.write_text(
        """Unified Coverage Report

Total Coverage Summary
SCORE LINE COND TOGGLE FSM BRANCH ASSERT GROUP
89.75 99.33 93.67 100.00 -- 98.40 99.51 47.58
""",
        encoding="utf-8",
    )

    metrics = parse_vcs_urg_dashboard(dashboard)

    assert metrics["tool_total_coverage"] == pytest.approx(89.75)
    assert "fsm" not in metrics["by_metric"]
    assert metrics["by_metric"]["branch"] == pytest.approx(98.40)
    assert metrics["by_metric"]["group"] == pytest.approx(47.58)


def test_parse_vcs_urg_dashboard_parses_documented_group_counts(tmp_path: Path):
    dashboard = tmp_path / "dashboard.txt"
    dashboard.write_text(
        """Unified Coverage Report

Total Coverage Summary
SCORE LINE COND TOGGLE FSM BRANCH ASSERT GROUP
95.90 98.23 93.91 97.02 93.02 96.33 99.77 92.99

Total Groups Coverage Summary
COVERED EXPECTED SCORE COVERED EXPECTED INST SCORE WEIGHT
491 528 92.99 487 529 92.06 1
""",
        encoding="utf-8",
    )

    metrics = parse_vcs_urg_dashboard(dashboard)

    assert metrics["by_metric_counts"]["group"] == {
        "covered": 491,
        "total": 528,
        "hit_rate": pytest.approx(92.99),
    }
    assert metrics["by_metric_counts"]["group_instance"] == {
        "covered": 487,
        "total": 529,
        "hit_rate": pytest.approx(92.06),
    }
    assert metrics["count_status"] == "normalized"


def test_parse_vcs_urg_dashboard_preserves_blank_pipe_metric(tmp_path: Path):
    dashboard = tmp_path / "dashboard.txt"
    dashboard.write_text(
        """Unified Coverage Report

Total Coverage Summary
SCORE | LINE | COND | TOGGLE | FSM | BRANCH | ASSERT | GROUP
95.96 | 95.39 | 93.47 | 95.36 |  | 94.22 | 97.71 | 99.60

Total Groups Coverage Summary
COVERED | EXPECTED | SCORE | COVERED | EXPECTED | INST SCORE | WEIGHT
491 | 528 | 92.99 | 487 | 529 | 92.06 | 1
""",
        encoding="utf-8",
    )

    metrics = parse_vcs_urg_dashboard(dashboard)

    assert metrics["tool_total_coverage"] == pytest.approx(95.96)
    assert metrics["by_metric"]["toggle"] == pytest.approx(95.36)
    assert "fsm" not in metrics["by_metric"]
    assert metrics["by_metric"]["branch"] == pytest.approx(94.22)
    assert metrics["by_metric"]["group"] == pytest.approx(99.60)
    assert metrics["by_metric_counts"]["group"]["covered"] == 491
    assert metrics["by_metric_counts"]["group_instance"]["total"] == 529


def test_parse_vcs_urg_dashboard_preserves_blank_fixed_width_metric(tmp_path: Path):
    dashboard = tmp_path / "dashboard.txt"
    header = "SCORE   LINE    COND    TOGGLE   FSM     BRANCH   ASSERT   GROUP"
    starts = [
        header.index(name)
        for name in (
            "SCORE",
            "LINE",
            "COND",
            "TOGGLE",
            "FSM",
            "BRANCH",
            "ASSERT",
            "GROUP",
        )
    ]
    values = [
        "95.96",
        "95.39",
        "93.47",
        "95.36",
        "",
        "94.22",
        "97.71",
        "99.60",
    ]
    row = [" "] * len(header)
    for start, value in zip(starts, values, strict=True):
        row[start : start + len(value)] = value
    dashboard.write_text(
        "Unified Coverage Report\n\n"
        "Total Coverage Summary\n"
        + header
        + "\n"
        + "".join(row).rstrip()
        + "\n",
        encoding="utf-8",
    )

    metrics = parse_vcs_urg_dashboard(dashboard)

    assert metrics["tool_total_coverage"] == pytest.approx(95.96)
    assert "fsm" not in metrics["by_metric"]
    assert metrics["by_metric"]["branch"] == pytest.approx(94.22)
    assert metrics["by_metric"]["group"] == pytest.approx(99.60)


def test_parse_vcs_urg_dashboard_rejects_ambiguous_summary(tmp_path: Path):
    dashboard = tmp_path / "dashboard.txt"
    dashboard.write_text(
        """Total Coverage Summary
SCORE LINE COND
not-a-score-row
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="score row could not be parsed"):
        parse_vcs_urg_dashboard(dashboard)


def test_merge_vcs_coverage_keeps_evidence_when_dashboard_is_missing(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    coverage = (project.root / project.run_dir / "run-a" / "coverage.vdb").resolve()
    coverage.mkdir(parents=True)

    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/synopsys/bin/urg" if name == "urg" else None,
    )

    def fake_run(command, cwd):
        report_dir = Path(cwd) / command[command.index("-report") + 1]
        if "-show" in command:
            report_dir.mkdir()
            return SimpleNamespace(returncode=0, stdout="URG brief complete\n")
        (Path(cwd) / command[command.index("-dbname") + 1]).mkdir()
        report_dir.mkdir()
        return SimpleNamespace(returncode=0, stdout="URG merge complete\n")

    monkeypatch.setattr("zddv.coverage._run", fake_run)

    result = merge_vcs_coverage(project)

    assert result["metrics"] is None
    assert result["snapshot_id"] is None
    assert result["metrics_status"] == "dashboard-missing"
    assert Path(result["report_dir"]).is_dir()
    assert result["brief_status"] == "captured"
    assert Path(result["brief_report_dir"]).is_dir()
    assert list_coverage_score_snapshots(project, limit=5) == []


def test_merge_vcs_coverage_keeps_dashboard_when_brief_report_fails(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    coverage = (project.root / project.run_dir / "run-a" / "coverage.vdb").resolve()
    coverage.mkdir(parents=True)
    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/synopsys/bin/urg" if name == "urg" else None,
    )

    def fake_run(command, cwd):
        report_dir = Path(cwd) / command[command.index("-report") + 1]
        if "-show" in command:
            return SimpleNamespace(returncode=1, stdout="brief report unavailable\n")
        (Path(cwd) / command[command.index("-dbname") + 1]).mkdir()
        report_dir.mkdir()
        (report_dir / "dashboard.txt").write_text(
            """Unified Coverage Report

Total Coverage Summary
SCORE LINE COND TOGGLE FSM BRANCH ASSERT GROUP
97.74 99.03 97.75 98.53 100.00 99.01 98.63 91.19
""",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="URG merge complete\n")

    monkeypatch.setattr("zddv.coverage._run", fake_run)
    result = merge_vcs_coverage(project)

    assert result["metrics_status"] == "normalized"
    assert result["metrics"]["tool_total_coverage"] == pytest.approx(97.74)
    assert result["snapshot_id"] is not None
    assert result["brief_status"] == "failed"
    assert result["brief_report_dir"] is None
    assert "brief report unavailable" in result["brief_error"]


def test_vcs_coverage_cli_surfaces_normalized_urg_scores(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    project = _project(tmp_path)
    monkeypatch.setattr("zddv.cli.load_project", lambda path: project)
    monkeypatch.setattr(
        "zddv.cli.merge_coverage",
        lambda loaded: {
            "inputs": ["run-a/coverage.vdb", "run-b/coverage.vdb"],
            "merged": "/tmp/coverage.vdb",
            "summary": "/tmp/urg-report/dashboard.txt",
            "metrics_path": "/tmp/vcs-coverage.json",
            "metrics": {
                "tool_total_coverage": 97.74,
                "by_metric": {
                    "line": 99.03,
                    "condition": 97.75,
                    "branch": 99.01,
                },
                "by_metric_counts": {
                    "group": {
                        "covered": 491,
                        "total": 528,
                        "hit_rate": 92.99,
                    }
                },
            },
            "snapshot_id": "cov-score-test",
            "metrics_status": "normalized",
            "brief_status": "captured",
            "brief_report_dir": "/tmp/urg-brief",
            "brief_error": None,
            "report": "",
        },
    )

    rc = cmd_coverage(SimpleNamespace(project=str(project.root)))

    assert rc == 0
    output = capsys.readouterr().out
    assert "Simulator-reported total coverage: 97.74%" in output
    assert "branch=99.01%" in output
    assert "condition=97.75%" in output
    assert "line=99.03%" in output
    assert "Coverage object counts: group=491/528 (92.99%)" in output
    assert "VCS uncovered-object evidence: captured" in output
    assert "URG brief report: /tmp/urg-brief" in output
    assert "Snapshot: cov-score-test" in output
    assert "Coverage points:" not in output


def test_vcs_coverage_history_uses_score_native_snapshots(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    project = _project(tmp_path)
    monkeypatch.setattr("zddv.cli.load_project", lambda path: project)
    monkeypatch.setattr(
        "zddv.cli.list_coverage_score_snapshots",
        lambda loaded, limit: [
            {
                "snapshot_id": "cov-score-test",
                "score": 97.74,
                "input_count": 2,
                "by_metric": {"line": 99.03, "branch": 99.01},
                "by_metric_counts": {
                    "group": {
                        "covered": 491,
                        "total": 528,
                        "hit_rate": 92.99,
                    }
                },
            }
        ],
    )

    rc = cmd_coverage_history(
        SimpleNamespace(project=str(project.root), limit=5)
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "97.74%" in output
    assert "cov-score-test" in output
    assert "branch=99.01%" in output
    assert "line=99.03%" in output
    assert "counts: group=491/528 (92.99%)" in output
    assert "HIT/TOTAL" not in output
