from pathlib import Path

from zddv.config import initialize_project
from zddv.gui import build_debug_gui_snapshot
from zddv.storage import record_coverage_snapshot, record_run


def _run(run_id: str, status: str, seed: int, log: Path) -> dict:
    return {
        "run_id": run_id,
        "created_at": f"2026-09-23T06:30:0{seed}+00:00",
        "project": "demo",
        "simulator": "verilator",
        "simulator_version": "Verilator test",
        "top": "tb_top",
        "test": "smoke",
        "seed": seed,
        "status": status,
        "returncode": 0 if status == "PASS" else 1,
        "duration_ms": 12.0 + seed,
        "run_dir": str(log.parent),
        "log": str(log),
        "waveform": None,
        "coverage": None,
        "timeout_s": 10.0,
        "command": ["zddv_sim"],
        "plusargs": [],
    }


def test_debug_gui_snapshot_is_read_only_backend_view(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    pass_log = tmp_path / "pass.log"
    fail_log = tmp_path / "fail.log"
    pass_log.write_text("PASS\n", encoding="utf-8")
    fail_log.write_text("ASSERT mismatch packet=41 expected=42 actual=43\n", encoding="utf-8")

    record_run(project, _run("run-1", "PASS", 1, pass_log))
    record_run(project, _run("run-2", "FAIL", 2, fail_log))
    record_coverage_snapshot(
        project,
        {
            "snapshot_id": "cov-gui",
            "created_at": "2026-09-23T06:31:00+00:00",
            "project": "demo",
            "simulator": "verilator",
            "input_count": 2,
            "total_points": 20,
            "hit_points": 18,
            "hit_rate": 90.0,
            "by_type": {
                "line": {"total": 20, "hit": 18, "hit_rate": 90.0},
            },
            "merged": "/tmp/coverage.dat",
            "summary": "/tmp/summary.txt",
            "metrics_path": "/tmp/metrics.json",
        },
    )

    snapshot = build_debug_gui_snapshot(project, limit=10)

    assert snapshot["project"] == "demo"
    assert snapshot["stats"]["total"] == 2
    assert snapshot["stats"]["passed"] == 1
    assert snapshot["stats"]["failed"] == 1
    assert snapshot["stats"]["pass_rate"] == 50.0
    assert [row["run_id"] for row in snapshot["runs"]] == ["run-2", "run-1"]
    assert len(snapshot["failure_groups"]) == 1
    assert snapshot["failure_groups"][0]["signature"] == (
        "ASSERT mismatch packet=# expected=# actual=#"
    )
    assert snapshot["latest_coverage"]["snapshot_id"] == "cov-gui"
    assert snapshot["latest_coverage"]["percent"] == 90.0
    assert snapshot["policy"] == {
        "display_only": True,
        "executes_verification": False,
        "invokes_ai": False,
        "applies_generated_artifacts": False,
    }


def test_debug_gui_snapshot_rejects_nonpositive_limit(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    try:
        build_debug_gui_snapshot(project, limit=0)
    except ValueError as exc:
        assert str(exc) == "limit must be >= 1"
    else:
        raise AssertionError("expected ValueError")


def test_debug_gui_snapshot_indexes_sources_and_source_hierarchy_without_writing_index(
    tmp_path: Path,
):
    project = initialize_project(tmp_path / "demo")
    rtl_dir = project.root / "rtl"
    tb_dir = project.root / "tb"
    rtl_dir.mkdir(exist_ok=True)
    tb_dir.mkdir(exist_ok=True)

    (rtl_dir / "child.sv").write_text(
        """module child(
    input logic a,
    output logic y
);
assign y = a;
endmodule
""",
        encoding="utf-8",
    )
    (tb_dir / "tb_top.sv").write_text(
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

    design_artifact = project.root / ".zddv" / "design" / "index.json"
    assert not design_artifact.exists()

    snapshot = build_debug_gui_snapshot(project)
    design = snapshot["design"]

    assert design["summary"] == {
        "files": 2,
        "units": 2,
        "instances": 1,
        "duplicate_unit_names": 0,
    }
    assert [item["path"] for item in design["files"]] == [
        "rtl/child.sv",
        "tb/tb_top.sv",
    ]
    assert design["hierarchy"]["instance"] == "tb_top"
    assert design["hierarchy"]["type"] == "tb_top"
    assert design["hierarchy"]["resolved"] is True
    assert len(design["hierarchy"]["children"]) == 1
    child = design["hierarchy"]["children"][0]
    assert child["instance"] == "dut"
    assert child["type"] == "child"
    assert child["file"] == "rtl/child.sv"
    assert child["resolved"] is True

    # The GUI consumes the in-memory source index. It must not materialize
    # the normal CLI design-index artifact as a side effect of viewing.
    assert not design_artifact.exists()
