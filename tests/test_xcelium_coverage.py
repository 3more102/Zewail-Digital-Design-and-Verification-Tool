from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zddv.cli import cmd_coverage_history, cmd_coverage_holes
from zddv.config import ProjectConfig
from zddv.coverage import (
    merge_coverage,
    merge_xcelium_coverage,
    parse_xcelium_imc_summary,
    parse_xcelium_imc_toggle_coverage_points,
)
from zddv.storage import list_coverage_score_snapshots


IMC_SUMMARY = """IMC(64): test build
Starting batch mode
Legend: Metric* means cumulative
name Overall* Average Overall* Covered Code* Average Code* Covered Fsm* Average Fsm* Covered Functional* Average Functional* Covered
--------------------------------------------------------------------------------------------------------------------------------
tb_top 86.25% 82.50% (33/40) 80.00% 75.00% (18/24) n/a n/a 92.50% 90.00% (9/10)
"""


IMC_TOGGLE_DETAIL = """IMC(64): test build
Coverage Report: Toggle Coverage

Instance name: tb_top.dut
Module/Entity name: dut
File name: /work/dut.sv
Number of signal bits fully toggled: 1 of 3
Number of signal bits partially toggled(rise): 1 of 3
Number of signal bits partially toggled(fall): 0 of 3

Hit(Full)  Hit(Rise)  Hit(Fall)   Signal
-----------------------------------------
0          0          0           idle
0          1          0           data[3]
1          1          1           ready
"""


def _project(tmp_path: Path, *, simulator: str = "xcelium") -> ProjectConfig:
    root = tmp_path / "demo"
    root.mkdir()
    return ProjectConfig(
        root=root,
        name="demo",
        top="tb_top",
        simulator=simulator,
        coverage=True,
        waveform=False,
    )


def _coverage_run(project: ProjectConfig, run_id: str) -> Path:
    path = (
        project.root
        / project.run_dir
        / run_id
        / "coverage"
        / run_id
    ).resolve()
    path.mkdir(parents=True)
    (path / f"{run_id}.ucd").write_text("fixture\n", encoding="utf-8")
    return path


def test_merge_xcelium_coverage_uses_native_union_imc_flow(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    first = _coverage_run(project, "run-a")
    second = _coverage_run(project, "run-b")

    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/cadence/bin/imc" if name == "imc" else None,
    )

    commands: list[list[str]] = []

    def fake_run(command, cwd):
        commands.append(list(command))
        cwd = Path(cwd)
        if "-load" not in command:
            assert command[:2] == ["/opt/cadence/bin/imc", "-execcmd"]
            script = command[2]
            assert "merge -overwrite -runfile" in script
            assert "-out merged -metrics all" in script
            assert "-initial_model union_all" in script
            assert "-message 1" in script

            merged = cwd / "cov_work" / "scope" / "merged"
            merged.mkdir(parents=True)
            (merged / "icc_merged.ucm").write_text("model\n", encoding="utf-8")
            (merged / "icc_merged.ucd").write_text("data\n", encoding="utf-8")
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "Doing model union\n"
                    "Total conflicts during target model creation: 0\n"
                    "Total items not merged : 0\n"
                ),
            )

        assert command[command.index("-load") + 1].endswith(
            "cov_work/scope/merged"
        )
        report_script = command[command.index("-execcmd") + 1]
        if "report -summary" in report_script:
            assert 'report -summary -inst "*..."' in report_script
            assert "-metrics all" in report_script
            assert "-cumulative on" in report_script
            assert "-showempty on" in report_script
            assert "-local off" in report_script
            return SimpleNamespace(returncode=0, stdout=IMC_SUMMARY)

        assert 'report -detail -inst "*..."' in report_script
        assert "-metrics all" in report_script
        assert "-all" in report_script
        assert "-showempty on" in report_script
        assert "-source on" in report_script
        return SimpleNamespace(
            returncode=0,
            stdout=IMC_TOGGLE_DETAIL,
        )

    monkeypatch.setattr("zddv.coverage._run", fake_run)

    result = merge_coverage(project)

    assert result["inputs"] == [str(first), str(second)]
    assert Path(result["merged"]).parts[-3:] == ("cov_work", "scope", "merged")
    assert Path(result["summary"]).read_text(encoding="utf-8") == IMC_SUMMARY
    assert Path(result["detail"]).read_text(encoding="utf-8") == IMC_TOGGLE_DETAIL
    assert result["detail_status"] == "captured"
    assert result["detail_returncode"] == 0
    assert result["toggle_detail_status"] == "normalized"
    assert result["toggle_detail_points"] == 3
    assert result["toggle_detail_holes"] == 2
    assert result["metrics_status"] == "normalized"
    assert result["metrics"]["tool_total_coverage"] == pytest.approx(82.50)
    assert result["metrics"]["by_metric"]["overall_average"] == pytest.approx(86.25)
    assert result["metrics"]["by_metric"]["code_covered"] == pytest.approx(75.0)
    assert result["metrics"]["by_metric"]["functional_covered"] == pytest.approx(90.0)
    assert result["metrics"]["by_metric_counts"]["overall_covered"]["covered"] == 33
    assert result["snapshot_id"] is not None
    assert len(commands) == 3

    runfile = Path(result["runfile"]).read_text(encoding="utf-8").splitlines()
    assert runfile == [
        str((first / "run-a.ucd").resolve()),
        str((second / "run-b.ucd").resolve()),
    ]
    merge_log = Path(result["merge_log"]).read_text(encoding="utf-8")
    assert "Doing model union" in merge_log
    assert "Total items not merged : 0" in merge_log

    manifest = json.loads(
        Path(result["metrics_path"]).read_text(encoding="utf-8")
    )
    assert manifest["status"] == "merged-report-captured"
    assert manifest["metrics_status"] == "normalized"
    assert manifest["merge_model"] == "union_all"
    assert manifest["runfile"] == result["runfile"]
    assert manifest["merge_log"] == result["merge_log"]
    assert manifest["detail"] == result["detail"]
    assert manifest["detail_status"] == "captured"
    assert manifest["detail_returncode"] == 0
    assert manifest["toggle_detail_status"] == "normalized"
    assert manifest["toggle_detail_points"] == 3
    assert manifest["toggle_detail_holes"] == 2
    assert "report -detail" in manifest["detail_command"][-1]
    assert len(manifest["merged_ucd_files"]) == 1
    assert len(manifest["merged_ucm_files"]) == 1
    assert manifest["metrics"]["scope"] == "tb_top"

    snapshots = list_coverage_score_snapshots(project, limit=5)
    assert len(snapshots) == 1
    assert snapshots[0]["snapshot_id"] == result["snapshot_id"]
    assert snapshots[0]["score"] == pytest.approx(82.50)
    assert snapshots[0]["by_metric"]["code_covered"] == pytest.approx(75.0)
    assert snapshots[0]["by_metric_counts"]["overall_covered"]["total"] == 40


def test_merge_xcelium_coverage_retains_detail_tool_error_as_evidence(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    _coverage_run(project, "run-a")
    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/cadence/bin/imc" if name == "imc" else None,
    )

    report_calls = 0

    def fake_run(command, cwd):
        nonlocal report_calls
        if "-load" not in command:
            merged = Path(cwd) / "cov_work" / "scope" / "merged"
            merged.mkdir(parents=True)
            (merged / "icc_merged.ucm").write_text("model\n", encoding="utf-8")
            (merged / "icc_merged.ucd").write_text("data\n", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="merge ok\n")

        report_calls += 1
        script = command[command.index("-execcmd") + 1]
        if "report -summary" in script:
            return SimpleNamespace(returncode=0, stdout=IMC_SUMMARY)
        return SimpleNamespace(returncode=2, stdout="detail unsupported\n")

    monkeypatch.setattr("zddv.coverage._run", fake_run)

    result = merge_xcelium_coverage(project)

    assert report_calls == 2
    assert result["metrics_status"] == "normalized"
    assert result["detail_status"] == "tool-error"
    assert result["detail_returncode"] == 2
    assert result["toggle_detail_status"] == "tool-error"
    assert result["toggle_detail_points"] == 0
    assert result["toggle_detail_holes"] == 0
    assert Path(result["detail"]).read_text(encoding="utf-8") == (
        "detail unsupported\n"
    )


def test_parse_xcelium_imc_toggle_detail_normalizes_bit_evidence():
    points = parse_xcelium_imc_toggle_coverage_points(IMC_TOGGLE_DETAIL)

    assert len(points) == 3
    assert points[0]["name"] == "tb_top.dut.idle"
    assert points[0]["hit"] is False
    assert points[0]["evidence"] == {"full": 0, "rise": 0, "fall": 0}
    assert points[0]["source_file"] == "/work/dut.sv"
    assert points[1]["name"] == "tb_top.dut.data[3]"
    assert points[1]["hit"] is False
    assert points[1]["evidence"] == {"full": 0, "rise": 1, "fall": 0}
    assert points[2]["name"] == "tb_top.dut.ready"
    assert points[2]["hit"] is True
    assert points[2]["count"] == 1


def test_xcelium_coverage_holes_cli_writes_toggle_holes(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    project = _project(tmp_path)
    detail_path = (
        project.root
        / ".zddv"
        / "coverage"
        / "xcelium"
        / "detail.txt"
    )
    detail_path.parent.mkdir(parents=True)
    detail_path.write_text(IMC_TOGGLE_DETAIL, encoding="utf-8")
    monkeypatch.setattr("zddv.cli.load_project", lambda path: project)

    rc = cmd_coverage_holes(
        SimpleNamespace(
            project=str(project.root),
            output=".zddv/coverage/holes.json",
            point_type="toggle",
            limit=200,
            show=20,
        )
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "Coverage holes (toggle): 2 unhit point(s); 2 written" in output

    payload = json.loads(
        (project.root / ".zddv" / "coverage" / "holes.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["total_holes"] == 2
    assert payload["by_type"] == {"toggle": 2}
    assert [hole["name"] for hole in payload["holes"]] == [
        "tb_top.dut.data[3]",
        "tb_top.dut.idle",
    ]
    assert payload["holes"][0]["evidence"] == {
        "full": 0,
        "rise": 1,
        "fall": 0,
    }


def test_merge_xcelium_coverage_requires_native_run_database(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/cadence/bin/imc" if name == "imc" else None,
    )

    with pytest.raises(RuntimeError, match=r"No Xcelium \.ucd run databases"):
        merge_xcelium_coverage(project)


def test_merge_xcelium_coverage_rejects_missing_native_merged_model(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    _coverage_run(project, "run-a")
    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/cadence/bin/imc" if name == "imc" else None,
    )

    def fake_run(command, cwd):
        if "-load" not in command:
            merged = Path(cwd) / "cov_work" / "scope" / "merged"
            merged.mkdir(parents=True)
            (merged / "icc_merged.ucd").write_text("data\n", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="merge returned success\n")
        pytest.fail("report must not run when the native merged model is incomplete")

    monkeypatch.setattr("zddv.coverage._run", fake_run)

    with pytest.raises(RuntimeError, match="IMC coverage merge failed"):
        merge_xcelium_coverage(project)


def test_merge_xcelium_coverage_requires_imc(tmp_path: Path, monkeypatch):
    project = _project(tmp_path)
    _coverage_run(project, "run-a")
    monkeypatch.setattr("zddv.coverage.shutil.which", lambda name: None)

    with pytest.raises(RuntimeError, match="Cadence IMC was not found"):
        merge_xcelium_coverage(project)



def test_parse_xcelium_imc_summary_requires_documented_header():
    with pytest.raises(ValueError, match="Overall Average/Covered"):
        parse_xcelium_imc_summary("name Other Columns\ntb_top 90.0%\n")



@pytest.mark.parametrize("simulator", ["xcelium", "xrun"])
def test_merge_coverage_dispatches_xcelium_aliases(
    tmp_path: Path,
    monkeypatch,
    simulator: str,
):
    project = _project(tmp_path, simulator=simulator)
    expected = {"inputs": [], "merged": "merged"}
    monkeypatch.setattr(
        "zddv.coverage.merge_xcelium_coverage",
        lambda loaded: expected if loaded is project else None,
    )

    assert merge_coverage(project) is expected

def test_xcelium_coverage_history_uses_score_snapshots(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    project = _project(tmp_path)
    from zddv.storage import record_coverage_score_snapshot

    record_coverage_score_snapshot(
        project,
        {
            "snapshot_id": "cov-score-xcelium-test",
            "created_at": "2026-09-22T10:00:00+00:00",
            "project": project.name,
            "simulator": project.simulator,
            "input_count": 1,
            "score": 82.5,
            "by_metric": {
                "overall_average": 86.25,
                "overall_covered": 82.5,
            },
            "by_metric_counts": {},
            "merged": "/tmp/merged",
            "summary": "/tmp/summary.txt",
            "metrics_path": "/tmp/metrics.json",
        },
    )
    monkeypatch.setattr("zddv.cli.load_project", lambda path: project)

    rc = cmd_coverage_history(
        SimpleNamespace(project=str(project.root), limit=5)
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "82.50%" in output
    assert "cov-score-xcelium-test" in output
    assert "overall_average=86.25%" in output
