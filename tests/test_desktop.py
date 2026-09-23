from __future__ import annotations

from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project
from zddv.desktop import build_desktop_snapshot, build_waveform_navigation_snapshot
from zddv.storage import (
    record_coverage_score_snapshot,
    record_coverage_snapshot,
    record_run,
)


def _run_record(run_id: str, status: str, *, seed: int) -> dict:
    return {
        "run_id": run_id,
        "created_at": f"2026-09-23T06:{seed:02d}:00+00:00",
        "project": "demo",
        "simulator": "verilator",
        "simulator_version": "Verilator 5.x",
        "top": "tb_top",
        "test": "smoke",
        "seed": seed,
        "status": status,
        "returncode": 0 if status == "PASS" else 1,
        "duration_ms": 12.5,
        "run_dir": f".zddv/runs/{run_id}",
        "log": f".zddv/runs/{run_id}/simulation.log",
        "waveform": None,
        "coverage": None,
        "timeout_s": 30.0,
        "command": ["sim"],
        "plusargs": [],
    }


def test_desktop_snapshot_summarizes_persisted_evidence(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    record_run(project, _run_record("run-pass", "PASS", seed=1))
    record_run(project, _run_record("run-fail", "FAIL", seed=2))
    record_coverage_snapshot(
        project,
        {
            "snapshot_id": "cov-points",
            "created_at": "2026-09-23T06:10:00+00:00",
            "project": "demo",
            "simulator": "verilator",
            "input_count": 2,
            "total_points": 100,
            "hit_points": 80,
            "hit_rate": 80.0,
            "by_type": {"line": {"total": 100, "hit": 80, "hit_rate": 80.0}},
            "merged": ".zddv/coverage/merged.dat",
            "summary": ".zddv/coverage/summary.txt",
            "metrics_path": ".zddv/coverage/metrics.json",
        },
    )
    record_coverage_score_snapshot(
        project,
        {
            "snapshot_id": "cov-score",
            "created_at": "2026-09-23T06:20:00+00:00",
            "project": "demo",
            "simulator": "questa",
            "input_count": 2,
            "score": 93.5,
            "by_metric": {"total": 93.5},
            "by_metric_counts": {},
            "merged": ".zddv/coverage/coverage.ucdb",
            "summary": ".zddv/coverage/summary.txt",
            "metrics_path": ".zddv/coverage/metrics.json",
        },
    )

    snapshot = build_desktop_snapshot(project, limit=10)

    assert snapshot["stats"]["total"] == 2
    assert snapshot["stats"]["passed"] == 1
    assert snapshot["stats"]["failed"] == 1
    assert [row["run_id"] for row in snapshot["recent_runs"]] == [
        "run-fail",
        "run-pass",
    ]
    assert snapshot["failure_groups"][0]["count"] == 1
    assert snapshot["latest_coverage"]["snapshot_id"] == "cov-score"
    assert snapshot["latest_coverage"]["percent"] == 93.5
    assert snapshot["latest_formal"] is None
    assert snapshot["latest_uvm"] is None
    assert snapshot["policy"] == {
        "display_only": True,
        "executes_verification": False,
        "invokes_ai": False,
        "applies_generated_artifacts": False,
    }


def test_desktop_snapshot_rejects_nonpositive_limit(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    with pytest.raises(ValueError, match="limit must be >= 1"):
        build_desktop_snapshot(project, limit=0)


def test_gui_cli_launches_viewer_with_requested_limit(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    project = initialize_project(tmp_path / "demo")
    captured = {}

    def fake_launch(project_arg, *, limit):
        captured["root"] = project_arg.root
        captured["limit"] = limit
        return {
            "stats": {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "timed_out": 0,
                "pass_rate": 0.0,
            }
        }

    monkeypatch.setattr("zddv.cli.launch_desktop_gui", fake_launch)

    rc = main(["--project", str(project.root), "gui", "--limit", "7"])

    assert rc == 0
    assert captured == {"root": project.root, "limit": 7}
    assert "GUI CLOSED" in capsys.readouterr().out


def test_desktop_snapshot_indexes_sources_and_hierarchy_without_writing_artifact(
    tmp_path: Path,
):
    project = initialize_project(tmp_path / "demo")
    (project.root / "rtl" / "child.sv").write_text(
        """module child(
    input logic a,
    output logic y
);
assign y = a;
endmodule
""",
        encoding="utf-8",
    )
    (project.root / "tb" / "tb_top.sv").write_text(
        """module tb_top;
logic a;
logic y;
child dut (
    .a(a),
    .y(y)
);
endmodule
""",
        encoding="utf-8",
    )
    project.rtl = ["rtl/*.sv"]
    project.tb = ["tb/*.sv"]
    project.top = "tb_top"

    index_artifact = project.root / ".zddv" / "design" / "index.json"
    assert not index_artifact.exists()

    snapshot = build_desktop_snapshot(project, limit=10)
    design = snapshot["design"]

    assert design["summary"] == {
        "files": 2,
        "units": 2,
        "instances": 1,
        "duplicate_unit_names": 0,
    }
    assert [row["path"] for row in design["files"]] == [
        "rtl/child.sv",
        "tb/tb_top.sv",
    ]
    assert design["hierarchy"]["instance"] == "tb_top"
    assert design["hierarchy"]["resolved"] is True
    assert len(design["hierarchy"]["children"]) == 1
    child = design["hierarchy"]["children"][0]
    assert child["instance"] == "dut"
    assert child["type"] == "child"
    assert child["file"] == "rtl/child.sv"
    assert child["resolved"] is True

    # The desktop view uses the in-memory design index and must not create
    # the normal CLI design-index artifact merely by viewing the project.
    assert not index_artifact.exists()


def test_waveform_navigation_indexes_recorded_vcd_without_materializing_artifact(
    tmp_path: Path,
):
    project = initialize_project(tmp_path / "wave-demo")
    run_dir = project.root / ".zddv" / "runs" / "run-wave"
    run_dir.mkdir(parents=True)
    waveform = run_dir / "waveform.vcd"
    waveform.write_text(
        """$timescale 1ns $end
$scope module tb_top $end
$var wire 1 ! clk $end
$scope module dut $end
$var wire 4 # count [3:0] $end
$upscope $end
$upscope $end
$enddefinitions $end
#0
0!
b0000 #
""",
        encoding="utf-8",
    )

    row = _run_record("run-wave", "PASS", seed=3)
    row["run_dir"] = str(run_dir)
    row["log"] = str(run_dir / "simulation.log")
    row["waveform"] = str(waveform)
    record_run(project, row)

    state = build_waveform_navigation_snapshot(project, run_id="run-wave")

    assert state["waveform_state"] == "INDEXED"
    assert state["waveform"]["format"] == "vcd"
    assert state["waveform"]["summary"]["signals"] == 2
    assert [item["path"] for item in state["waveform"]["signals"]] == [
        "tb_top.clk",
        "tb_top.dut.count",
    ]
    assert state["probe_report"] is None
    assert not (project.root / ".zddv" / "waveforms").exists()


def test_waveform_navigation_surfaces_existing_probe_suggestions_without_execution(
    tmp_path: Path,
    monkeypatch,
):
    project = initialize_project(tmp_path / "probe-demo")
    run_dir = project.root / ".zddv" / "runs" / "run-fail"
    run_dir.mkdir(parents=True)
    waveform = run_dir / "waveform.vcd"
    waveform.write_text(
        """$timescale 1ns $end
$scope module tb_top $end
$var wire 1 ! clk $end
$enddefinitions $end
#0
0!
""",
        encoding="utf-8",
    )
    row = _run_record("run-fail", "FAIL", seed=4)
    row["run_dir"] = str(run_dir)
    row["log"] = str(run_dir / "simulation.log")
    row["waveform"] = str(waveform)
    record_run(project, row)

    calls = []

    def fake_suggest(project_arg, *, run_id):
        calls.append((project_arg.root, run_id))
        return {
            "suggestions": [
                {
                    "rank": 1,
                    "signal": "tb_top.clk",
                    "source_candidate_kind": "assertion_anchor",
                    "source_evidence_score": 7,
                    "reason": "explicit recorded signal evidence",
                    "argv": ["waveform-probe", "tb_top.clk", "--run", run_id],
                }
            ],
            "blockers": [],
        }

    monkeypatch.setattr("zddv.desktop.suggest_debug_probes", fake_suggest)

    state = build_waveform_navigation_snapshot(project, run_id="run-fail")

    assert calls == [(project.root, "run-fail")]
    assert state["waveform_state"] == "INDEXED"
    assert state["probe_error"] is None
    assert state["probe_report"]["suggestions"][0]["signal"] == "tb_top.clk"
    assert state["probe_report"]["suggestions"][0]["argv"] == [
        "waveform-probe",
        "tb_top.clk",
        "--run",
        "run-fail",
    ]
