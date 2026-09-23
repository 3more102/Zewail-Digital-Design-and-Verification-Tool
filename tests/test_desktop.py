from __future__ import annotations

import json
from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project
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


def test_desktop_snapshot_indexes_current_sources_without_writing_design_artifacts(
    tmp_path: Path,
):
    project = initialize_project(tmp_path / "demo")
    project.top = "top"
    project.rtl = ["rtl/*.sv"]

    (project.root / "rtl" / "child.sv").write_text(
        """module child(input logic a, output logic y);
  assign y = a;
endmodule
""",
        encoding="utf-8",
    )
    (project.root / "rtl" / "top.sv").write_text(
        """module top(input logic a, output logic y);
  child u_child (
    .a(a),
    .y(y)
  );
endmodule
""",
        encoding="utf-8",
    )

    snapshot = build_desktop_snapshot(project, limit=10)

    design = snapshot["design"]
    assert design["summary"]["files"] == 2
    assert design["summary"]["units"] == 2
    assert design["summary"]["instances"] == 1
    assert design["hierarchy"]["instance"] == "top"
    assert design["hierarchy"]["children"][0]["instance"] == "u_child"
    assert design["hierarchy"]["children"][0]["type"] == "child"
    assert snapshot["persisted_elaboration"]["status"] == "NOT_PRESENT"
    assert not (project.root / ".zddv" / "design" / "index.json").exists()


def test_desktop_snapshot_reads_persisted_elaboration_without_running_tools(
    tmp_path: Path,
):
    project = initialize_project(tmp_path / "demo")
    project.top = "top"
    design_dir = project.root / ".zddv" / "design"
    design_dir.mkdir(parents=True)
    elaborated_path = design_dir / "elaborated.json"
    elaborated_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "created_at": "2026-09-23T06:40:00+00:00",
                "project": "demo",
                "top": "top",
                "simulator": "verilator",
                "simulator_version": "Verilator 5.x",
                "source_format": "json",
                "modules": [{"name": "top", "top": True, "location": None}],
                "instances": [
                    {
                        "path": "top",
                        "name": "top",
                        "module": "top",
                        "top": True,
                        "location": {
                            "path": "rtl/top.sv",
                            "line": 1,
                            "column": 1,
                            "end_line": 1,
                            "end_column": 3,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    snapshot = build_desktop_snapshot(project, limit=10)

    elaboration = snapshot["persisted_elaboration"]
    assert elaboration["status"] == "PRESENT"
    assert elaboration["simulator"] == "verilator"
    assert elaboration["simulator_version"] == "Verilator 5.x"
    assert elaboration["instances"][0]["path"] == "top"
    assert elaboration["path"] == str(elaborated_path.resolve())


def test_desktop_snapshot_marks_malformed_persisted_elaboration_invalid(
    tmp_path: Path,
):
    project = initialize_project(tmp_path / "demo")
    design_dir = project.root / ".zddv" / "design"
    design_dir.mkdir(parents=True)
    (design_dir / "elaborated.json").write_text("{bad json", encoding="utf-8")

    snapshot = build_desktop_snapshot(project, limit=10)

    assert snapshot["persisted_elaboration"]["status"] == "INVALID"
    assert "error" in snapshot["persisted_elaboration"]
