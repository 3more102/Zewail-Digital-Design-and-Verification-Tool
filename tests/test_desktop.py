from __future__ import annotations

from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project
from zddv.desktop import _read_source, build_desktop_snapshot
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


def test_desktop_snapshot_loads_bounded_formal_and_uvm_details(
    tmp_path: Path,
    monkeypatch,
):
    project = initialize_project(tmp_path / "demo")
    formal = {
        "snapshot_id": "formal-detail",
        "status": "FAIL",
        "mode": "bmc",
        "property_count": 2,
    }
    uvm = {
        "snapshot_id": "uvm-detail",
        "status": "FAIL",
        "test_name": "stress",
        "error_count": 1,
        "fatal_count": 0,
    }
    calls = {}

    monkeypatch.setattr(
        "zddv.desktop.list_formal_result_snapshots",
        lambda project_arg, *, limit: [formal],
    )
    monkeypatch.setattr(
        "zddv.desktop.list_uvm_log_snapshots",
        lambda project_arg, *, limit: [uvm],
    )

    def fake_formal_properties(project_arg, snapshot_id, *, limit):
        calls["formal"] = (snapshot_id, limit)
        return [
            {
                "snapshot_id": snapshot_id,
                "property_index": 0,
                "name": "p_ready",
                "kind": "assert",
                "status": "FAIL",
                "interpretation": "COUNTEREXAMPLE",
                "depth": 12,
                "effective_depth": 12,
                "message": "counterexample found",
                "trace_path": "trace.vcd",
                "trace_role": "COUNTEREXAMPLE",
            }
        ]

    def fake_uvm_messages(project_arg, snapshot_id, *, limit):
        calls["uvm"] = (snapshot_id, limit)
        return [
            {
                "snapshot_id": snapshot_id,
                "event_index": 0,
                "severity": "UVM_ERROR",
                "report_id": "MISMATCH",
                "component": "uvm_test_top.env.scoreboard",
                "message": "expected 42 got 41",
                "time_text": "120ns",
                "source_location": "scoreboard.sv(88)",
                "log_line": 44,
                "raw": "UVM_ERROR ...",
            }
        ]

    monkeypatch.setattr(
        "zddv.desktop.list_formal_property_results",
        fake_formal_properties,
    )
    monkeypatch.setattr(
        "zddv.desktop.list_uvm_report_messages",
        fake_uvm_messages,
    )

    snapshot = build_desktop_snapshot(project, limit=7)

    assert calls == {
        "formal": ("formal-detail", 7),
        "uvm": ("uvm-detail", 7),
    }
    assert snapshot["latest_formal"] == formal
    assert snapshot["formal_properties"][0]["name"] == "p_ready"
    assert snapshot["latest_uvm"] == uvm
    assert snapshot["uvm_messages"][0]["report_id"] == "MISMATCH"


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


def test_desktop_snapshot_reads_persisted_elaborated_hierarchy_without_mutation(
    tmp_path: Path,
):
    project = initialize_project(tmp_path / "elaborated-demo")
    design_dir = project.root / ".zddv" / "design"
    design_dir.mkdir(parents=True, exist_ok=True)
    elaborated_path = design_dir / "elaborated.json"
    elaborated_path.write_text(
        """{
  "created_at": "2026-09-23T06:45:00+00:00",
  "simulator": "verilator",
  "simulator_version": "Verilator test",
  "source_format": "json",
  "summary": {"modules": 2, "instances": 2},
  "instances": [
    {
      "path": "tb_top",
      "name": "tb_top",
      "module": "tb_top",
      "top": true,
      "location": {"path": "tb/tb_top.sv", "line": 1}
    },
    {
      "path": "tb_top.dut",
      "name": "dut",
      "module": "dut",
      "top": false,
      "location": {"path": "rtl/dut.sv", "line": 12}
    }
  ]
}""",
        encoding="utf-8",
    )
    before = elaborated_path.read_bytes()

    snapshot = build_desktop_snapshot(project, limit=10)

    evidence = snapshot["elaborated_hierarchy"]
    assert evidence["status"] == "PRESENT"
    assert evidence["simulator"] == "verilator"
    assert evidence["instances"][1]["path"] == "tb_top.dut"
    assert evidence["instances"][1]["location"] == {
        "path": "rtl/dut.sv",
        "line": 12,
    }
    assert elaborated_path.read_bytes() == before
    assert not (design_dir / "elaborated-hierarchy.txt").exists()



def test_desktop_source_reader_resolves_only_configured_sources(tmp_path: Path):
    project = initialize_project(tmp_path / "source-preview")
    source = project.root / "rtl" / "preview.sv"
    source.write_text(
        "module preview;\n  logic value;\nendmodule\n",
        encoding="utf-8",
    )
    project.rtl = ["rtl/*.sv"]

    before = source.read_bytes()
    assert _read_source(project, "rtl/preview.sv") == (
        "module preview;\n  logic value;\nendmodule\n"
    )
    assert source.read_bytes() == before

    project_note = project.root / "notes.txt"
    project_note.write_text("not HDL evidence", encoding="utf-8")
    with pytest.raises(ValueError, match="not part of the configured ZDDV project"):
        _read_source(project, "notes.txt")

    outside = tmp_path / "outside.sv"
    outside.write_text("module outside; endmodule\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not part of the configured ZDDV project"):
        _read_source(project, str(outside))
