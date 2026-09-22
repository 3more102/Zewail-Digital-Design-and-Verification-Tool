from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from zddv.config import ProjectConfig
from zddv.coverage import merge_vcs_coverage, parse_vcs_urg_module_counts
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
TOTAL 99 1 1.01

Branch Coverage for Module : tb
Line No. Total Covered Percent
Branches 2 1 50.00

Line Coverage for Module : dut
Line No. Total Covered Percent
TOTAL 10 8 80.00

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
    assert report["by_metric_counts"]["module_branch"] == {
        "covered": 4,
        "total": 6,
        "hit_rate": pytest.approx(66.6666667),
    }
    assert [(item["metric"], item["module"]) for item in report["modules"]] == [
        ("branch", "dut"),
        ("branch", "tb"),
        ("line", "dut"),
        ("line", "tb"),
    ]


def test_parse_vcs_urg_module_counts_normalizes_condition_toggle_and_fsm(
    tmp_path: Path,
):
    modinfo = tmp_path / "modinfo.txt"
    modinfo.write_text(
        """Cond Coverage for Module : dut
Total Covered Percent
Conditions 18 17 94.44
Logical 18 17 94.44

Toggle Coverage for Module : dut
Total Covered Percent
Totals 20 17 85.00
Total Bits 230 172 74.78
Total Bits 0->1 115 86 74.78
Total Bits 1->0 115 86 74.78

FSM Coverage for Module : dut
Summary for FSM :: state_q
Total Covered Percent
States 5 4 80.00
Transitions 7 4 57.14
Sequences 0 0 0.00
Summary for FSM :: aux_q
Total Covered Percent
States 3 3 100.00
Transitions 2 2 100.00
""",
        encoding="utf-8",
    )

    report = parse_vcs_urg_module_counts(modinfo)

    assert report["by_metric_counts"]["module_condition"] == {
        "covered": 17,
        "total": 18,
        "hit_rate": pytest.approx(94.4444444),
    }
    assert report["by_metric_counts"]["module_toggle"] == {
        "covered": 172,
        "total": 230,
        "hit_rate": pytest.approx(74.7826087),
    }
    assert report["by_metric_counts"]["module_fsm"] == {
        "covered": 6,
        "total": 9,
        "hit_rate": pytest.approx(66.6666667),
    }
    assert [(item["metric"], item["module"]) for item in report["modules"]] == [
        ("condition", "dut"),
        ("fsm", "dut"),
        ("toggle", "dut"),
    ]


def test_merge_vcs_coverage_persists_module_counts_without_replacing_scores(
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
        report_dir = out / command[command.index("-report") + 1]
        if "-show" in command:
            report_dir.mkdir()
            return SimpleNamespace(returncode=0, stdout="URG brief complete\n")
        (out / command[command.index("-dbname") + 1]).mkdir()
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
Conditions 10 7 70.00

Toggle Coverage for Module : dut
Total Covered Percent
Totals 4 3 75.00
Total Bits 20 12 60.00

FSM Coverage for Module : dut
Summary for FSM :: state_q
Total Covered Percent
States 4 3 75.00
Transitions 4 2 50.00

Branch Coverage for Module : dut
Line No. Total Covered Percent
Branches 5 2 40.00
""",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="URG merge complete\n")

    monkeypatch.setattr("zddv.coverage._run", fake_run)

    result = merge_vcs_coverage(project)

    assert result["metrics_status"] == "normalized"
    assert result["module_counts_status"] == "normalized"
    assert result["brief_status"] == "captured"
    assert Path(result["brief_report_dir"]).is_dir()
    assert result["metrics"]["by_metric"]["line"] == pytest.approx(80.0)
    assert result["metrics"]["by_metric_counts"]["module_line"] == {
        "covered": 8,
        "total": 10,
        "hit_rate": pytest.approx(80.0),
    }
    assert result["metrics"]["by_metric_counts"]["module_branch"] == {
        "covered": 2,
        "total": 5,
        "hit_rate": pytest.approx(40.0),
    }
    assert result["metrics"]["by_metric_counts"]["module_condition"]["covered"] == 7
    assert result["metrics"]["by_metric_counts"]["module_toggle"]["total"] == 20
    assert result["metrics"]["by_metric_counts"]["module_fsm"]["covered"] == 2

    snapshots = list_coverage_score_snapshots(project, limit=1)
    assert snapshots[0]["by_metric_counts"]["module_line"]["covered"] == 8
    assert snapshots[0]["by_metric_counts"]["module_branch"]["total"] == 5
    assert snapshots[0]["by_metric_counts"]["module_condition"]["covered"] == 7
    assert snapshots[0]["by_metric_counts"]["module_toggle"]["total"] == 20
    assert snapshots[0]["by_metric_counts"]["module_fsm"]["covered"] == 2


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
