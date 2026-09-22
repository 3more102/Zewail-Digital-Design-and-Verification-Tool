from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from zddv.config import ProjectConfig
from zddv.coverage import (
    merge_vcs_coverage,
    parse_vcs_urg_instance_counts,
    parse_vcs_urg_module_counts,
)
from zddv.storage import list_coverage_score_snapshots


def _project(tmp_path: Path) -> ProjectConfig:
    root = tmp_path / "demo"
    (root / "rtl").mkdir(parents=True)
    (root / "tb").mkdir()
    (root / "rtl" / "dut.sv").write_text("module dut; endmodule\n", encoding="utf-8")
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


def test_parse_vcs_urg_module_counts_keeps_module_scope_explicit(tmp_path: Path):
    modinfo = tmp_path / "modinfo.txt"
    modinfo.write_text(
        """Line Coverage for Module : tb
Line No. Total Covered Percent
TOTAL 5 4 80.00

Condition Coverage for Module : tb
Total Covered Percent
Conditions 4 3 75.00

Toggle Coverage for Module : tb
Total Covered Percent
Totals 10 7 70.00

FSM Coverage for Module : tb
Summary for FSM :: state_q
Total Covered Percent
States 4 4 100.00
Transitions 5 4 80.00
Sequences 0 0

Branch Coverage for Module : tb
Line No. Total Covered Percent
Branches 2 1 50.00

Line Coverage for Module : dut
Line No. Total Covered Percent
TOTAL 10 8 80.00

Cond Coverage for Module : dut
Total Covered Percent
Conditions 8 6 75.00

Toggle Coverage for Module : dut
Total Covered Percent
Totals 12 9 75.00

FSM Coverage for Module : dut
Summary for FSM :: ctrl_q
Total Covered Percent
States 4 3 75.00
Transitions 3 2 66.67
Sequences 2 1 50.00

Branch Coverage for Module : dut
Line No. Total Covered Percent
Branches 4 3 75.00
""",
        encoding="utf-8",
    )

    report = parse_vcs_urg_module_counts(modinfo)

    assert report["source"] == "urg-modinfo"
    assert report["by_metric_counts"]["module_line"] == {
        "covered": 12,
        "total": 15,
        "hit_rate": pytest.approx(80.0),
    }
    assert report["by_metric_counts"]["module_condition"] == {
        "covered": 9,
        "total": 12,
        "hit_rate": pytest.approx(75.0),
    }
    assert report["by_metric_counts"]["module_toggle"] == {
        "covered": 16,
        "total": 22,
        "hit_rate": pytest.approx(72.7272727),
    }
    assert report["by_metric_counts"]["module_branch"] == {
        "covered": 4,
        "total": 6,
        "hit_rate": pytest.approx(66.6666667),
    }
    assert report["by_metric_counts"]["module_fsm_state"] == {
        "covered": 7,
        "total": 8,
        "hit_rate": pytest.approx(87.5),
    }
    assert report["by_metric_counts"]["module_fsm_transition"] == {
        "covered": 6,
        "total": 8,
        "hit_rate": pytest.approx(75.0),
    }
    assert report["by_metric_counts"]["module_fsm_sequence"] == {
        "covered": 1,
        "total": 2,
        "hit_rate": pytest.approx(50.0),
    }
    assert ("line", "dut") in [
        (item["metric"], item["module"]) for item in report["modules"]
    ]
    assert ("condition", "tb") in [
        (item["metric"], item["module"]) for item in report["modules"]
    ]


def test_parse_vcs_urg_instance_counts_aggregates_explicit_instance_rows(
    tmp_path: Path,
):
    report_dir = tmp_path / "urg-report"
    report_dir.mkdir()
    (report_dir / "mod0.html").write_text(
        """<html><body>
<h2>Line Coverage for Instance : tb.dut</h2>
<table>
<tr><th></th><th>Line No.</th><th>Total</th><th>Covered</th><th>Percent</th></tr>
<tr><td>TOTAL</td><td></td><td>12</td><td>9</td><td>75.00</td></tr>
</table>
<h2>Cond Coverage for Instance : tb.dut</h2>
<table>
<tr><th></th><th>Total</th><th>Covered</th><th>Percent</th></tr>
<tr><td>Conditions</td><td>8</td><td>6</td><td>75.00</td></tr>
</table>
<h2>Toggle Coverage for Instance : tb.dut</h2>
<table>
<tr><th></th><th>Total</th><th>Covered</th><th>Percent</th></tr>
<tr><td>Totals</td><td>10</td><td>7</td><td>70.00</td></tr>
</table>
<h2>FSM Coverage for Instance : tb.dut</h2>
<p>Summary for FSM :: state_q</p>
<table>
<tr><th></th><th>Total</th><th>Covered</th><th>Percent</th></tr>
<tr><td>States</td><td>4</td><td>4</td><td>100.00</td></tr>
<tr><td>Transitions</td><td>5</td><td>4</td><td>80.00</td></tr>
<tr><td>Sequences</td><td>0</td><td>0</td><td></td></tr>
</table>
<h2>Branch Coverage for Instance : tb.dut</h2>
<table>
<tr><th></th><th>Line No.</th><th>Total</th><th>Covered</th><th>Percent</th></tr>
<tr><td>Branches</td><td></td><td>6</td><td>5</td><td>83.33</td></tr>
</table>
</body></html>
""",
        encoding="utf-8",
    )
    (report_dir / "mod1.html").write_text(
        """<html><body>
<h2>Line Coverage for Instance : tb.dut</h2>
<table>
<tr><th></th><th>Line No.</th><th>Total</th><th>Covered</th><th>Percent</th></tr>
<tr><td>TOTAL</td><td></td><td>12</td><td>9</td><td>75.00</td></tr>
</table>
</body></html>
""",
        encoding="utf-8",
    )

    report = parse_vcs_urg_instance_counts(report_dir)

    assert report["source"] == "urg-instance-detail-html"
    assert report["files_scanned"] == 2
    assert report["instance_sections"] == 6
    assert report["duplicate_records"] == 1
    assert report["by_metric_counts"]["instance_line"] == {
        "covered": 9,
        "total": 12,
        "hit_rate": pytest.approx(75.0),
    }
    assert report["by_metric_counts"]["instance_condition"] == {
        "covered": 6,
        "total": 8,
        "hit_rate": pytest.approx(75.0),
    }
    assert report["by_metric_counts"]["instance_toggle"] == {
        "covered": 7,
        "total": 10,
        "hit_rate": pytest.approx(70.0),
    }
    assert report["by_metric_counts"]["instance_branch"] == {
        "covered": 5,
        "total": 6,
        "hit_rate": pytest.approx(83.3333333),
    }
    assert report["by_metric_counts"]["instance_fsm_state"]["covered"] == 4
    assert report["by_metric_counts"]["instance_fsm_transition"]["total"] == 5
    assert report["by_metric_counts"]["instance_fsm_sequence"] == {
        "covered": 0,
        "total": 0,
        "hit_rate": None,
    }


def test_merge_vcs_coverage_persists_module_and_instance_counts_without_replacing_scores(
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
        out = Path(cwd)
        (out / command[command.index("-dbname") + 1]).mkdir()
        report_dir = out / command[command.index("-report") + 1]
        report_dir.mkdir()
        (report_dir / "dashboard.txt").write_text(
            """Unified Coverage Report

Total Coverage Summary
SCORE LINE COND TOGGLE FSM BRANCH ASSERT GROUP
90.00 80.00 70.00 60.00 50.00 40.00 30.00 20.00
""",
            encoding="utf-8",
        )
        (report_dir / "modinfo.txt").write_text(
            """Line Coverage for Module : dut
Line No. Total Covered Percent
TOTAL 10 8 80.00

Cond Coverage for Module : dut
Total Covered Percent
Conditions 8 6 75.00

Toggle Coverage for Module : dut
Total Covered Percent
Totals 12 9 75.00

FSM Coverage for Module : dut
Summary for FSM :: state_q
Total Covered Percent
States 4 3 75.00
Transitions 3 2 66.67
Sequences 2 1 50.00

Branch Coverage for Module : dut
Line No. Total Covered Percent
Branches 5 2 40.00
""",
            encoding="utf-8",
        )
        (report_dir / "mod0.html").write_text(
            """<html><body>
<h2>Line Coverage for Instance : tb.dut</h2>
<table>
<tr><th></th><th>Line No.</th><th>Total</th><th>Covered</th><th>Percent</th></tr>
<tr><td>TOTAL</td><td></td><td>10</td><td>8</td><td>80.00</td></tr>
</table>
<h2>Cond Coverage for Instance : tb.dut</h2>
<table>
<tr><th></th><th>Total</th><th>Covered</th><th>Percent</th></tr>
<tr><td>Conditions</td><td>8</td><td>6</td><td>75.00</td></tr>
</table>
</body></html>
""",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="URG merge complete\n")

    monkeypatch.setattr("zddv.coverage._run", fake_run)

    result = merge_vcs_coverage(project)

    assert result["metrics_status"] == "normalized"
    assert result["module_counts_status"] == "normalized"
    assert result["instance_counts_status"] == "normalized"
    assert result["metrics"]["by_metric"]["line"] == pytest.approx(80.0)
    assert result["metrics"]["by_metric_counts"]["module_line"]["covered"] == 8
    assert result["metrics"]["by_metric_counts"]["module_condition"]["total"] == 8
    assert result["metrics"]["by_metric_counts"]["module_toggle"]["covered"] == 9
    assert result["metrics"]["by_metric_counts"]["module_fsm_state"]["covered"] == 3
    assert result["metrics"]["by_metric_counts"]["module_fsm_transition"]["total"] == 3
    assert result["metrics"]["by_metric_counts"]["module_fsm_sequence"]["covered"] == 1
    assert result["metrics"]["by_metric_counts"]["module_branch"]["total"] == 5
    assert result["metrics"]["by_metric_counts"]["instance_line"]["covered"] == 8
    assert result["metrics"]["by_metric_counts"]["instance_condition"]["total"] == 8

    snapshots = list_coverage_score_snapshots(project, limit=1)
    assert snapshots[0]["by_metric_counts"]["module_line"]["covered"] == 8
    assert snapshots[0]["by_metric_counts"]["module_condition"]["covered"] == 6
    assert snapshots[0]["by_metric_counts"]["instance_line"]["total"] == 10
    assert snapshots[0]["by_metric_counts"]["instance_condition"]["covered"] == 6


def test_parse_vcs_urg_module_counts_rejects_conflicting_duplicate_sections(
    tmp_path: Path,
):
    modinfo = tmp_path / "modinfo.txt"
    modinfo.write_text(
        """Line Coverage for Module : dut
TOTAL 10 8 80.00
Line Coverage for Module : dut
TOTAL 10 9 90.00
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Conflicting URG line module totals"):
        parse_vcs_urg_module_counts(modinfo)
