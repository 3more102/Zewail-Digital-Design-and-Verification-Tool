from __future__ import annotations

import json
from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project, save_project
from zddv.desktop import build_desktop_snapshot
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
    assert snapshot["design"]["summary"] == {
        "files": 0,
        "units": 0,
        "instances": 0,
        "duplicate_unit_names": 0,
    }
    assert snapshot["policy"] == {
        "display_only": True,
        "executes_verification": False,
        "invokes_ai": False,
        "applies_generated_artifacts": False,
    }


def test_desktop_snapshot_builds_source_hierarchy_without_running_simulator(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    project.top = "top"
    project.rtl = ["rtl/*.sv"]
    save_project(project)

    (project.root / "rtl" / "child.sv").write_text(
        "module child(input logic a, output logic y); assign y = a; endmodule\n",
        encoding="utf-8",
    )
    (project.root / "rtl" / "top.sv").write_text(
        "module top(input logic a, output logic y);\n"
        "  child u_child(.a(a), .y(y));\n"
        "endmodule\n",
        encoding="utf-8",
    )

    snapshot = build_desktop_snapshot(project, limit=10)

    design = snapshot["design"]
    assert design["summary"]["files"] == 2
    assert design["summary"]["units"] == 2
    assert design["summary"]["instances"] == 1
    assert design["hierarchy"]["instance"] == "top"
    assert design["hierarchy"]["file"] == "rtl/top.sv"
    assert design["hierarchy"]["children"][0]["instance"] == "u_child"
    assert design["hierarchy"]["children"][0]["type"] == "child"
    assert design["hierarchy"]["children"][0]["file"] == "rtl/child.sv"


def test_desktop_snapshot_reads_only_matching_persisted_elaboration(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    design_dir = project.root / ".zddv" / "design"
    design_dir.mkdir(parents=True)
    path = design_dir / "elaborated.json"
    path.write_text(
        json.dumps(
            {
                "project": project.name,
                "top": project.top,
                "simulator": "verilator",
                "simulator_version": "Verilator 5.x",
                "source_format": "json",
                "modules": [{"name": project.top, "top": True, "location": None}],
                "instances": [
                    {
                        "path": project.top,
                        "name": project.top,
                        "module": project.top,
                        "top": True,
                        "location": None,
                    }
                ],
                "summary": {"modules": 1, "instances": 1},
            }
        ),
        encoding="utf-8",
    )

    snapshot = build_desktop_snapshot(project)

    assert snapshot["persisted_elaboration"]["path"] == str(path)
    assert snapshot["persisted_elaboration"]["summary"] == {
        "modules": 1,
        "instances": 1,
    }


def test_desktop_snapshot_rejects_nonpositive_limit(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    with pytest.raises(ValueError, match="limit must be >= 1"):
        build_desktop_snapshot(project, limit=0)


def test_gui_cli_launches_viewer_with_requested_limit(
    tmp_path: Path,
    monkeypatch,
):
    project = initialize_project(tmp_path / "demo")
    captured = {}

    def fake_launch(project_arg, *, limit):
        captured["root"] = project_arg.root
        captured["limit"] = limit
        return {"stats": {"total": 0}}

    monkeypatch.setattr("zddv.cli.launch_desktop_gui", fake_launch)

    rc = main(["--project", str(project.root), "gui", "--limit", "7"])

    assert rc == 0
    assert captured == {"root": project.root, "limit": 7}
