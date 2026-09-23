from __future__ import annotations

from pathlib import Path

import pytest

from zddv.config import initialize_project, save_project
from zddv.desktop_waveform import (
    build_desktop_waveform_snapshot,
    crossprobe_desktop_waveform_signal,
    desktop_crossprobe_evidence_rows,
    probe_desktop_waveform_signal,
)
from zddv.storage import record_run


def _run_record(
    run_id: str,
    *,
    seed: int,
    waveform: str | None,
) -> dict:
    return {
        "run_id": run_id,
        "created_at": f"2026-09-23T07:{seed:02d}:00+00:00",
        "project": "demo",
        "simulator": "verilator",
        "simulator_version": "Verilator 5.x",
        "top": "tb_top",
        "test": "smoke",
        "seed": seed,
        "status": "PASS",
        "returncode": 0,
        "duration_ms": 12.5,
        "run_dir": f".zddv/runs/{run_id}",
        "log": f".zddv/runs/{run_id}/simulation.log",
        "waveform": waveform,
        "coverage": None,
        "timeout_s": 30.0,
        "command": ["sim"],
        "plusargs": [],
    }


def test_desktop_waveform_navigation_and_probe_are_in_memory(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    vcd = project.root / "trace.vcd"
    vcd.write_text(
        """$date today $end
$version zddv-test $end
$timescale 1ns $end
$scope module tb $end
$var wire 1 ! clk $end
$var wire 8 " data [7:0] $end
$upscope $end
$enddefinitions $end
#0
0!
b00000000 "
#5
1!
b00000001 "
#10
0!
b00000010 "
""",
        encoding="utf-8",
    )
    record_run(
        project,
        _run_record("run-wave", seed=3, waveform=str(vcd)),
    )

    navigation = build_desktop_waveform_snapshot(project)

    assert navigation["run_id"] == "run-wave"
    assert navigation["format"] == "vcd"
    assert navigation["parse_status"] == "indexed"
    assert navigation["timescale"] == "1ns"
    assert navigation["summary"]["signals"] == 2
    assert [signal["path"] for signal in navigation["signals"]] == [
        "tb.clk",
        "tb.data",
    ]

    probe = probe_desktop_waveform_signal(
        project,
        "tb.data",
        run_id="run-wave",
        start_time=0,
        end_time=10,
        max_changes=10,
    )

    assert probe["run_id"] == "run-wave"
    assert probe["summary"]["signals"] == 1
    assert probe["signals"][0]["changes"] == [
        {"time": 0, "value": "00000000"},
        {"time": 5, "value": "00000001"},
        {"time": 10, "value": "00000010"},
    ]
    assert not (project.root / ".zddv" / "waveforms").exists()


def test_desktop_waveform_navigation_reports_missing_evidence(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    record_run(
        project,
        _run_record("run-no-wave", seed=4, waveform=None),
    )

    with pytest.raises(RuntimeError, match="No run with an existing waveform artifact"):
        build_desktop_waveform_snapshot(project)


def test_desktop_waveform_crossprobe_is_read_only(tmp_path: Path):
    project = initialize_project(tmp_path / "crossprobe")
    rtl = project.root / "rtl"
    tb = project.root / "tb"
    rtl.mkdir(exist_ok=True)
    tb.mkdir(exist_ok=True)
    (rtl / "counter.sv").write_text(
        """module counter(
    input logic clk,
    output logic [3:0] count
);
    always_ff @(posedge clk)
        count <= count + 1'b1;
endmodule
""",
        encoding="utf-8",
    )
    (tb / "tb_top.sv").write_text(
        """module tb_top;
    logic clk;
    logic [3:0] count;
    counter dut(.clk(clk), .count(count));
endmodule
""",
        encoding="utf-8",
    )
    project.rtl = ["rtl/*.sv"]
    project.tb = ["tb/*.sv"]
    project.top = "tb_top"
    save_project(project)

    vcd = project.root / "crossprobe.vcd"
    vcd.write_text(
        """$timescale 1ns $end
$scope module TOP $end
$scope module tb_top $end
$scope module dut $end
$var wire 4 ! count [3:0] $end
$upscope $end
$upscope $end
$upscope $end
$enddefinitions $end
#0
b0000 !
#5
b0001 !
""",
        encoding="utf-8",
    )
    record_run(
        project,
        _run_record("run-crossprobe", seed=5, waveform=str(vcd)),
    )

    result = crossprobe_desktop_waveform_signal(
        project,
        "TOP.tb_top.dut.count",
        run_id="run-crossprobe",
    )

    assert result["status"] == "MATCHED"
    assert result["hierarchy_resolution"] == "source_structural"
    assert result["hierarchy"]["design_path"] == "tb_top.dut"
    assert result["source"]["file"] == "rtl/counter.sv"
    assert result["source"]["declaration"]["line"] == 3
    assert result["connectivity"]["analysis_level"] == "source_structural"
    assert len(result["connectivity"]["drivers"]) == 1
    assert result["elaborated_evidence"]["status"] == "NOT_PRESENT"
    assert not (project.root / ".zddv" / "design" / "connectivity.json").exists()
    assert not (project.root / ".zddv" / "debug" / "crossprobe.json").exists()


def test_desktop_crossprobe_rows_surface_elaborated_connectivity_without_inference():
    report = {
        "hierarchy_resolution": "simulator_elaborated",
        "hierarchy": {"design_path": "tb_top.dut", "type": "counter"},
        "source": {
            "unit": "counter",
            "file": "rtl/counter.sv",
            "declaration": {"line": 3},
        },
        "connectivity": {
            "drivers": [{"kind": "procedural_assignment"}],
            "loads": [],
        },
        "elaborated_evidence": {"status": "PRESENT"},
        "elaborated_port": {
            "status": "MATCHED",
            "signal": "count",
            "port": {
                "module": "counter",
                "name": "count",
                "direction": "output",
            },
        },
        "elaborated_connectivity": {
            "analysis_level": "simulator_elaborated_direct_pin_varref",
            "parent_signal_bindings": [],
            "instance_port_bindings": [
                {
                    "instance_path": "tb_top.dut",
                    "pin": "count",
                    "parent_instance_path": "tb_top",
                    "parent_signal": "count",
                    "port_direction": "output",
                    "relationship": "child_output_to_parent_signal",
                }
            ],
        },
        "elaborated_boundary": {
            "status": "MATCHED",
            "flow": "child_to_parent",
        },
    }

    rows = dict(desktop_crossprobe_evidence_rows(report))

    assert rows["Hierarchy"] == "simulator_elaborated · tb_top.dut · counter"
    assert rows["RTL source"] == "counter · rtl/counter.sv:3"
    assert rows["Drivers"] == "1 source-structural item(s)"
    assert rows["Loads"] == "0 source-structural item(s)"
    assert rows["Elaboration"] == "PRESENT"
    assert rows["Elaborated port"] == "MATCHED · counter.count · direction=output"
    assert rows["Elaborated connectivity"] == (
        "simulator_elaborated_direct_pin_varref · parent-signal bindings=0 · "
        "instance-port bindings=1 · relationships=child_output_to_parent_signal"
    )
    assert rows["Elaborated boundary"] == "MATCHED · flow=child_to_parent"



def test_desktop_crossprobe_rows_render_exact_pin_relationships_and_unsupported_evidence():
    rows = desktop_crossprobe_evidence_rows(
        {
            "elaborated_evidence": {"status": "PRESENT"},
            "elaborated_connectivity": {
                "analysis_level": "simulator_elaborated_direct_pin_varref",
                "parent_signal_bindings": [
                    {
                        "instance_path": "tb_top.u_in",
                        "pin": "clk",
                        "parent_instance_path": "tb_top",
                        "parent_signal": "clk",
                        "port_direction": "input",
                        "relationship": "parent_signal_to_child_input",
                    }
                ],
                "instance_port_bindings": [
                    {
                        "instance_path": "tb_top.u_out",
                        "pin": "count",
                        "parent_instance_path": "tb_top",
                        "parent_signal": "count",
                        "port_direction": "output",
                        "relationship": "child_output_to_parent_signal",
                    }
                ],
                "unsupported_instance_port_bindings": [
                    {
                        "status": "UNSUPPORTED",
                        "instance_path": "tb_top.u_expr",
                        "pin": "ready",
                        "port_direction": "input",
                        "expression_type": "AND",
                    }
                ],
            },
        }
    )

    pin_rows = [detail for kind, detail in rows if kind == "Elaborated pin"]
    assert pin_rows == [
        "tb_top.clk -> tb_top.u_in.clk · input · parent_signal_to_child_input",
        "tb_top.u_out.count -> tb_top.count · output · child_output_to_parent_signal",
        "tb_top.u_expr.ready · UNSUPPORTED · direction=input · "
        "expression=AND · no direct VARREF relation",
    ]
    summary = next(
        detail for kind, detail in rows if kind == "Elaborated connectivity"
    )
    assert "unsupported=1" in summary


def test_desktop_crossprobe_rows_fail_closed_on_unknown_elaborated_contract():
    rows = desktop_crossprobe_evidence_rows(
        {
            "elaborated_evidence": {"status": "PRESENT"},
            "elaborated_connectivity": {
                "analysis_level": "future_unverified_contract",
                "parent_signal_bindings": [
                    {
                        "instance_path": "tb_top.dut",
                        "pin": "count",
                        "parent_instance_path": "tb_top",
                        "parent_signal": "count",
                    }
                ],
            },
        }
    )

    assert not any(
        kind in {"Elaborated connectivity", "Elaborated pin"}
        for kind, _detail in rows
    )


def test_desktop_crossprobe_rows_bound_elaborated_pin_details():
    bindings = [
        {
            "instance_path": f"tb_top.dut{i}",
            "pin": "clk",
            "parent_instance_path": "tb_top",
            "parent_signal": "clk",
            "port_direction": "input",
            "relationship": "parent_signal_to_child_input",
        }
        for i in range(3)
    ]
    rows = desktop_crossprobe_evidence_rows(
        {
            "elaborated_connectivity": {
                "analysis_level": "simulator_elaborated_direct_pin_varref",
                "parent_signal_bindings": bindings,
                "instance_port_bindings": [],
                "unsupported_instance_port_bindings": [],
            }
        },
        elaborated_limit=2,
    )

    pin_rows = [detail for kind, detail in rows if kind == "Elaborated pin"]
    assert pin_rows == [
        "tb_top.clk -> tb_top.dut0.clk · input · parent_signal_to_child_input",
        "tb_top.clk -> tb_top.dut1.clk · input · parent_signal_to_child_input",
        "1 additional evidence item(s) not shown",
    ]
    with pytest.raises(ValueError, match="row limit must be > 0"):
        desktop_crossprobe_evidence_rows({}, elaborated_limit=0)


def test_desktop_crossprobe_rows_surface_bounded_boundary_role_evidence():
    rows = desktop_crossprobe_evidence_rows(
        {
            "elaborated_connectivity": {
                "analysis_level": "simulator_elaborated_direct_pin_varref",
                "parent_signal_bindings": [],
                "instance_port_bindings": [],
                "unsupported_instance_port_bindings": [],
                "boundary_drivers": [
                    {
                        "instance_path": "tb_top.u_out",
                        "pin": "count",
                        "parent_instance_path": "tb_top",
                        "parent_signal": "count",
                        "port_direction": "output",
                        "relationship": "child_output_to_parent_signal",
                        "query_side": "parent_signal",
                    }
                ],
                "boundary_loads": [
                    {
                        "instance_path": "tb_top.u_in",
                        "pin": "clk",
                        "parent_instance_path": "tb_top",
                        "parent_signal": "clk",
                        "port_direction": "input",
                        "relationship": "parent_signal_to_child_input",
                        "query_side": "parent_signal",
                    }
                ],
                "boundary_unclassified_bindings": [
                    {
                        "instance_path": "tb_top.u_unknown",
                        "pin": "ready",
                        "parent_instance_path": "tb_top",
                        "parent_signal": "ready",
                        "port_direction": None,
                        "relationship": "direct_pin_varref",
                        "query_side": "parent_signal",
                    }
                ],
            }
        },
        elaborated_limit=2,
    )

    summary = next(
        detail for kind, detail in rows if kind == "Elaborated boundary roles"
    )
    assert summary == "drivers=1 · loads=1 · unclassified=1"

    role_rows = [detail for kind, detail in rows if kind == "Elaborated role"]
    assert role_rows == [
        "DRIVER · query_side=parent_signal · parent=tb_top.count · "
        "child=tb_top.u_out.count · direction=output",
        "LOAD · query_side=parent_signal · parent=tb_top.clk · "
        "child=tb_top.u_in.clk · direction=input",
        "1 additional boundary role item(s) not shown",
    ]


def test_desktop_crossprobe_rows_do_not_interpret_boundary_roles_on_unknown_contract():
    rows = desktop_crossprobe_evidence_rows(
        {
            "elaborated_connectivity": {
                "analysis_level": "future_unverified_contract",
                "boundary_drivers": [
                    {
                        "instance_path": "tb_top.u_out",
                        "pin": "count",
                        "parent_instance_path": "tb_top",
                        "parent_signal": "count",
                        "port_direction": "output",
                        "query_side": "parent_signal",
                    }
                ],
            }
        }
    )

    assert not any(
        kind in {"Elaborated boundary roles", "Elaborated role"}
        for kind, _detail in rows
    )


def test_desktop_crossprobe_rows_fail_closed_on_partial_or_malformed_boundary_role_lists():
    base = {
        "analysis_level": "simulator_elaborated_direct_pin_varref",
        "parent_signal_bindings": [],
        "instance_port_bindings": [],
        "unsupported_instance_port_bindings": [],
    }
    driver = {
        "instance_path": "tb_top.u_out",
        "pin": "count",
        "parent_instance_path": "tb_top",
        "parent_signal": "count",
        "port_direction": "output",
        "relationship": "child_output_to_parent_signal",
        "query_side": "parent_signal",
    }

    partial_rows = desktop_crossprobe_evidence_rows(
        {
            "elaborated_connectivity": {
                **base,
                "boundary_drivers": [driver],
            }
        }
    )
    assert not any(
        kind in {"Elaborated boundary roles", "Elaborated role"}
        for kind, _detail in partial_rows
    )

    malformed_container_rows = desktop_crossprobe_evidence_rows(
        {
            "elaborated_connectivity": {
                **base,
                "boundary_drivers": [driver],
                "boundary_loads": {},
                "boundary_unclassified_bindings": [],
            }
        }
    )
    assert not any(
        kind in {"Elaborated boundary roles", "Elaborated role"}
        for kind, _detail in malformed_container_rows
    )

    malformed_item_rows = desktop_crossprobe_evidence_rows(
        {
            "elaborated_connectivity": {
                **base,
                "boundary_drivers": [driver, "invalid"],
                "boundary_loads": [],
                "boundary_unclassified_bindings": [],
            }
        }
    )
    assert not any(
        kind in {"Elaborated boundary roles", "Elaborated role"}
        for kind, _detail in malformed_item_rows
    )




def test_desktop_crossprobe_rows_surface_trusted_internal_assignw_evidence():
    rows = desktop_crossprobe_evidence_rows(
        {
            "elaborated_internal_connectivity": {
                "status": "PARTIAL",
                "analysis_level": (
                    "simulator_elaborated_module_root_assignw_direct_varref"
                ),
                "role_semantics": "direct_continuous_assignment",
                "evidence_contract": (
                    "verilator_module_root_assignw_direct_varref_only"
                ),
                "query_instance_path": "tb_top.dut",
                "query_module": "passthrough",
                "query_signal": "dst",
                "drivers": [
                    {
                        "kind": "continuous_assignment",
                        "assignment_type": "ASSIGNW",
                        "instance_path": "tb_top.dut",
                        "module": "passthrough",
                        "source_signal": "src",
                        "target_signal": "dst",
                        "location": {"path": "rtl/passthrough.sv", "line": 6},
                    }
                ],
                "loads": [
                    {
                        "kind": "continuous_assignment",
                        "assignment_type": "ASSIGNW",
                        "instance_path": "tb_top.dut",
                        "module": "passthrough",
                        "source_signal": "dst",
                        "target_signal": "tap",
                        "location": {"path": "rtl/passthrough.sv", "line": 7},
                    }
                ],
                "unresolved_assignments": [
                    {
                        "status": "UNSUPPORTED",
                        "assignment_type": "ASSIGNW",
                        "instance_path": "tb_top.dut",
                        "module": "passthrough",
                        "query_references": ["rhs"],
                        "lhs_expression_type": "VARREF",
                        "rhs_expression_type": "AND",
                        "lhs_varrefs": ["complex_dst"],
                        "rhs_varrefs": ["dst", "other"],
                        "location": {"path": "rtl/passthrough.sv", "line": 8},
                    }
                ],
            }
        }
    )

    row_map = dict(rows)
    assert row_map["Elaborated internal connectivity"] == (
        "simulator_elaborated_module_root_assignw_direct_varref · "
        "status=PARTIAL · drivers=1 · loads=1 · unresolved=1"
    )
    edge_rows = [
        detail for kind, detail in rows
        if kind == "Elaborated internal edge"
    ]
    assert edge_rows == [
        "DRIVER · tb_top.dut.src -> tb_top.dut.dst · ASSIGNW · "
        "location=rtl/passthrough.sv:6",
        "LOAD · tb_top.dut.dst -> tb_top.dut.tap · ASSIGNW · "
        "location=rtl/passthrough.sv:7",
    ]
    unresolved_rows = [
        detail for kind, detail in rows
        if kind == "Elaborated internal unresolved"
    ]
    assert unresolved_rows == [
        "UNRESOLVED · instance=tb_top.dut · query_references=rhs · "
        "lhs=VARREF · rhs=AND · location=rtl/passthrough.sv:8"
    ]


def test_desktop_crossprobe_rows_bound_internal_assignw_evidence():
    drivers = [
        {
            "kind": "continuous_assignment",
            "assignment_type": "ASSIGNW",
            "instance_path": "tb_top.dut",
            "module": "passthrough",
            "source_signal": f"src{index}",
            "target_signal": "dst",
        }
        for index in range(3)
    ]
    rows = desktop_crossprobe_evidence_rows(
        {
            "elaborated_internal_connectivity": {
                "status": "NORMALIZED",
                "analysis_level": (
                    "simulator_elaborated_module_root_assignw_direct_varref"
                ),
                "role_semantics": "direct_continuous_assignment",
                "evidence_contract": (
                    "verilator_module_root_assignw_direct_varref_only"
                ),
                "query_instance_path": "tb_top.dut",
                "query_module": "passthrough",
                "query_signal": "dst",
                "drivers": drivers,
                "loads": [],
                "unresolved_assignments": [],
            }
        },
        elaborated_limit=2,
    )

    edge_rows = [
        detail for kind, detail in rows
        if kind == "Elaborated internal edge"
    ]
    assert len(edge_rows) == 3
    assert edge_rows[-1] == "1 additional internal evidence item(s) not shown"


def test_desktop_crossprobe_rows_fail_closed_on_untrusted_internal_assignw_evidence():
    unknown_contract_rows = desktop_crossprobe_evidence_rows(
        {
            "elaborated_internal_connectivity": {
                "status": "NORMALIZED",
                "analysis_level": "future_internal_connectivity_contract",
                "role_semantics": "direct_continuous_assignment",
                "evidence_contract": (
                    "verilator_module_root_assignw_direct_varref_only"
                ),
                "query_instance_path": "tb_top.dut",
                "query_module": "passthrough",
                "query_signal": "dst",
                "drivers": [],
                "loads": [],
                "unresolved_assignments": [],
            }
        }
    )
    assert not any(
        kind.startswith("Elaborated internal")
        for kind, _detail in unknown_contract_rows
    )

    malformed_rows = desktop_crossprobe_evidence_rows(
        {
            "elaborated_internal_connectivity": {
                "status": "NORMALIZED",
                "analysis_level": (
                    "simulator_elaborated_module_root_assignw_direct_varref"
                ),
                "role_semantics": "direct_continuous_assignment",
                "evidence_contract": (
                    "verilator_module_root_assignw_direct_varref_only"
                ),
                "query_instance_path": "tb_top.dut",
                "query_module": "passthrough",
                "query_signal": "dst",
                "drivers": [],
                "loads": {},
                "unresolved_assignments": [],
            }
        }
    )
    assert not any(
        kind.startswith("Elaborated internal")
        for kind, _detail in malformed_rows
    )


def test_desktop_crossprobe_rows_fail_closed_on_semantically_invalid_internal_assignw_evidence():
    base = {
        "status": "NORMALIZED",
        "analysis_level": "simulator_elaborated_module_root_assignw_direct_varref",
        "evidence_contract": "verilator_module_root_assignw_direct_varref_only",
        "role_semantics": "direct_continuous_assignment",
        "query_instance_path": "tb_top.dut",
        "query_module": "passthrough",
        "query_signal": "dst",
        "drivers": [],
        "loads": [],
        "unresolved_assignments": [],
    }
    valid_edge = {
        "kind": "continuous_assignment",
        "assignment_type": "ASSIGNW",
        "instance_path": "tb_top.dut",
        "module": "passthrough",
        "source_signal": "src",
        "target_signal": "dst",
    }
    invalid_unresolved = {
        "status": "UNSUPPORTED",
        "assignment_type": "ASSIGNW",
        "instance_path": "tb_top.dut",
        "module": "passthrough",
        "query_references": ["rhs"],
        "lhs_expression_type": "VARREF",
        "rhs_expression_type": "AND",
        "lhs_varrefs": ["complex_dst"],
        "rhs_varrefs": ["other"],
    }
    invalid_payloads = [
        {**base, "evidence_contract": "future_contract"},
        {**base, "drivers": [{**valid_edge, "kind": "procedural"}]},
        {**base, "drivers": [{**valid_edge, "module": "other_module"}]},
        {**base, "status": "PARTIAL"},
        {
            **base,
            "status": "PARTIAL",
            "unresolved_assignments": [invalid_unresolved],
        },
        {
            **base,
            "status": "PARTIAL",
            "unresolved_assignments": [
                {
                    **invalid_unresolved,
                    "query_references": [{"side": "rhs"}],
                }
            ],
        },
    ]

    for payload in invalid_payloads:
        rows = desktop_crossprobe_evidence_rows(
            {"elaborated_internal_connectivity": payload}
        )
        assert not any(
            kind.startswith("Elaborated internal")
            for kind, _detail in rows
        )


def test_desktop_crossprobe_rows_surface_only_trusted_source_correlation():
    report = {
        "elaborated_source_correlation": {
            "analysis_level": "simulator_elaborated_to_source_structural_correlation",
            "elaborated_analysis_level": "simulator_elaborated_direct_pin_varref",
            "source_analysis_level": "source_structural",
            "role_semantics": "source_structural_only",
            "correlations": [
                {
                    "status": "MATCHED",
                    "binding_side": "instance_port",
                    "instance_path": "tb_top.dut",
                    "pin": "count",
                    "parent_instance_path": "tb_top",
                    "parent_signal": "count",
                    "source_unit": "tb_top",
                    "source_roles": ["driver"],
                    "match_basis": [
                        "direct_pin_resolves_source_generated_candidate"
                    ],
                    "source_edge": {
                        "kind": "instance_port",
                        "unit": "tb_top",
                        "signal": "count",
                        "file": "tb/tb_top.sv",
                        "line": 4,
                        "instance": "dut",
                        "child_type": "counter",
                        "port": "count",
                        "direction": "output",
                        "expression": "count",
                        "instance_path": "tb_top",
                        "elaborated_child_resolution": "ambiguous",
                        "elaborated_child_candidates": [
                            "tb_top.dut",
                            "tb_top.genblk1[0].dut",
                        ],
                    },
                }
            ],
        }
    }

    rows = dict(desktop_crossprobe_evidence_rows(report))

    assert rows["Elaborated/source correlation"] == (
        "simulator_elaborated_to_source_structural_correlation · correlations=1 · "
        "matched=1 · not-found=0 · ambiguous=0 · unavailable=0 · "
        "roles=source_structural_only"
    )
    assert rows["Correlated source edge"] == (
        "tb_top.count ↔ tb_top.dut.count · binding_side=instance_port · "
        "source=tb_top · source_roles=driver · "
        "basis=direct_pin_resolves_source_generated_candidate · "
        "location=tb/tb_top.sv:4"
    )


def test_desktop_crossprobe_rows_do_not_promote_uncertain_or_untrusted_correlation():
    uncertain = desktop_crossprobe_evidence_rows(
        {
            "elaborated_source_correlation": {
                "analysis_level": "simulator_elaborated_to_source_structural_correlation",
                "elaborated_analysis_level": "simulator_elaborated_direct_pin_varref",
            "source_analysis_level": "source_structural",
            "role_semantics": "source_structural_only",
                "correlations": [
                    {
                        "status": "AMBIGUOUS",
                        "binding_side": "instance_port",
                        "instance_path": "tb_top.dut",
                        "pin": "count",
                        "parent_instance_path": "tb_top",
                        "parent_signal": "count",
                        "source_unit": "tb_top",
                        "candidate_count": 2,
                    },
                    {
                        "status": "NOT_FOUND",
                        "binding_side": "instance_port",
                        "instance_path": "tb_top.dut",
                        "pin": "count",
                        "parent_instance_path": "tb_top",
                        "parent_signal": "count",
                        "source_unit": "tb_top",
                        "reason": "no_exact_source_instance_port_edge",
                    },
                    {
                        "status": "UNAVAILABLE",
                        "binding_side": "instance_port",
                        "instance_path": "tb_top.dut",
                        "pin": "count",
                        "parent_instance_path": "tb_top",
                        "parent_signal": "count",
                        "reason": "parent_elaborated_instance_not_found",
                    },
                ],
            }
        }
    )
    uncertain_map = dict(uncertain)
    assert uncertain_map["Elaborated/source correlation"] == (
        "simulator_elaborated_to_source_structural_correlation · correlations=3 · "
        "matched=0 · not-found=1 · ambiguous=1 · unavailable=1 · "
        "roles=source_structural_only"
    )
    assert "Correlated source edge" not in uncertain_map

    untrusted = desktop_crossprobe_evidence_rows(
        {
            "elaborated_source_correlation": {
                "analysis_level": "future_correlation_contract",
                "elaborated_analysis_level": "simulator_elaborated_direct_pin_varref",
            "source_analysis_level": "source_structural",
            "role_semantics": "source_structural_only",
                "correlations": [{"status": "MATCHED", "source_edge": {}}],
            }
        }
    )
    assert not any(
        kind in {"Elaborated/source correlation", "Correlated source edge"}
        for kind, _detail in untrusted
    )

    wrong_envelope = desktop_crossprobe_evidence_rows(
        {
            "elaborated_source_correlation": {
                "analysis_level": (
                    "simulator_elaborated_to_source_structural_correlation"
                ),
                "elaborated_analysis_level": "future_elaborated_contract",
                "source_analysis_level": "source_structural",
                "role_semantics": "source_structural_only",
                "correlations": [
                    {
                        "status": "UNAVAILABLE",
                        "binding_side": "instance_port",
                        "instance_path": "tb_top.dut",
                        "pin": "count",
                        "parent_instance_path": "tb_top",
                        "parent_signal": "count",
                        "reason": "parent_elaborated_instance_not_found",
                    }
                ],
            }
        }
    )
    assert not any(
        kind in {"Elaborated/source correlation", "Correlated source edge"}
        for kind, _detail in wrong_envelope
    )

    wrong_roles = desktop_crossprobe_evidence_rows(
        {
            "elaborated_source_correlation": {
                "analysis_level": "simulator_elaborated_to_source_structural_correlation",
                "role_semantics": "simulator_elaborated",
                "correlations": [{"status": "MATCHED", "source_edge": {}}],
            }
        }
    )
    assert not any(
        kind in {"Elaborated/source correlation", "Correlated source edge"}
        for kind, _detail in wrong_roles
    )



def test_desktop_crossprobe_rows_fail_closed_on_malformed_current_source_correlation():
    malformed = desktop_crossprobe_evidence_rows(
        {
            "elaborated_source_correlation": {
                "analysis_level": (
                    "simulator_elaborated_to_source_structural_correlation"
                ),
                "elaborated_analysis_level": "simulator_elaborated_direct_pin_varref",
            "source_analysis_level": "source_structural",
            "role_semantics": "source_structural_only",
                "correlations": [
                    {
                        "status": "MATCHED",
                        "binding_side": "instance_port",
                        "instance_path": "tb_top.dut",
                        "pin": "count",
                        "parent_instance_path": "tb_top",
                        "parent_signal": "count",
                        "source_unit": "tb_top",
                        "source_roles": ["driver"],
                        "source_edge": {
                            "file": "tb/tb_top.sv",
                            "line": 4,
                        },
                    }
                ],
            }
        }
    )
    wrong_role = desktop_crossprobe_evidence_rows(
        {
            "elaborated_source_correlation": {
                "analysis_level": (
                    "simulator_elaborated_to_source_structural_correlation"
                ),
                "elaborated_analysis_level": "simulator_elaborated_direct_pin_varref",
            "source_analysis_level": "source_structural",
            "role_semantics": "source_structural_only",
                "correlations": [
                    {
                        "status": "MATCHED",
                        "binding_side": "instance_port",
                        "instance_path": "tb_top.dut",
                        "pin": "count",
                        "parent_instance_path": "tb_top",
                        "parent_signal": "count",
                        "source_unit": "tb_top",
                        "source_roles": ["load"],
                        "match_basis": ["source_edge_exact_child_path"],
                        "source_edge": {
                            "kind": "instance_port",
                            "unit": "tb_top",
                            "signal": "count",
                            "file": "tb/tb_top.sv",
                            "line": 4,
                            "instance": "dut",
                            "child_type": "counter",
                            "port": "count",
                            "direction": "output",
                            "expression": "count",
                            "instance_path": "tb_top",
                            "elaborated_child_resolution": "exact",
                            "elaborated_child_path": "tb_top.dut",
                        },
                    }
                ],
            }
        }
    )
    wrong_provenance = desktop_crossprobe_evidence_rows(
        {
            "elaborated_source_correlation": {
                "analysis_level": (
                    "simulator_elaborated_to_source_structural_correlation"
                ),
                "elaborated_analysis_level": "simulator_elaborated_direct_pin_varref",
            "source_analysis_level": "source_structural",
            "role_semantics": "source_structural_only",
                "correlations": [
                    {
                        "status": "MATCHED",
                        "binding_side": "instance_port",
                        "instance_path": "tb_top.dut",
                        "pin": "count",
                        "parent_instance_path": "tb_top",
                        "parent_signal": "count",
                        "source_unit": "tb_top",
                        "source_roles": ["driver"],
                        "match_basis": [
                            "direct_pin_resolves_source_generated_candidate"
                        ],
                        "source_edge": {
                            "kind": "instance_port",
                            "unit": "tb_top",
                            "signal": "count",
                            "file": "tb/tb_top.sv",
                            "line": 4,
                            "instance": "dut",
                            "child_type": "counter",
                            "port": "count",
                            "direction": "output",
                            "expression": "count",
                            "instance_path": "tb_top",
                            "elaborated_child_resolution": "exact",
                            "elaborated_child_path": "tb_top.dut",
                        },
                    }
                ],
            }
        }
    )
    malformed_provenance_type = desktop_crossprobe_evidence_rows(
        {
            "elaborated_source_correlation": {
                "analysis_level": (
                    "simulator_elaborated_to_source_structural_correlation"
                ),
                "elaborated_analysis_level": "simulator_elaborated_direct_pin_varref",
            "source_analysis_level": "source_structural",
            "role_semantics": "source_structural_only",
                "correlations": [
                    {
                        "status": "MATCHED",
                        "binding_side": "instance_port",
                        "instance_path": "tb_top.dut",
                        "pin": "count",
                        "parent_instance_path": "tb_top",
                        "parent_signal": "count",
                        "source_unit": "tb_top",
                        "source_roles": ["driver"],
                        "match_basis": [{}],
                        "source_edge": {
                            "kind": "instance_port",
                            "unit": "tb_top",
                            "signal": "count",
                            "file": "tb/tb_top.sv",
                            "line": 4,
                            "instance": "dut",
                            "child_type": "counter",
                            "port": "count",
                            "direction": "output",
                            "expression": "count",
                            "instance_path": "tb_top",
                            "elaborated_child_resolution": "ambiguous",
                            "elaborated_child_candidates": ["tb_top.dut"],
                        },
                    }
                ],
            }
        }
    )

    empty = desktop_crossprobe_evidence_rows(
        {
            "elaborated_source_correlation": {
                "analysis_level": (
                    "simulator_elaborated_to_source_structural_correlation"
                ),
                "elaborated_analysis_level": "simulator_elaborated_direct_pin_varref",
            "source_analysis_level": "source_structural",
            "role_semantics": "source_structural_only",
                "correlations": [],
            }
        }
    )
    unknown_status = desktop_crossprobe_evidence_rows(
        {
            "elaborated_source_correlation": {
                "analysis_level": (
                    "simulator_elaborated_to_source_structural_correlation"
                ),
                "elaborated_analysis_level": "simulator_elaborated_direct_pin_varref",
            "source_analysis_level": "source_structural",
            "role_semantics": "source_structural_only",
                "correlations": [{"status": "FUTURE_STATUS"}],
            }
        }
    )

    for rows in (
        malformed,
        wrong_role,
        wrong_provenance,
        malformed_provenance_type,
        empty,
        unknown_status,
    ):
        assert not any(
            kind in {
                "Elaborated/source correlation",
                "Correlated source edge",
            }
            for kind, _detail in rows
        )


def test_desktop_crossprobe_rows_fail_closed_on_semantically_invalid_boundary_roles():
    base = {
        "analysis_level": "simulator_elaborated_direct_pin_varref",
        "parent_signal_bindings": [],
        "instance_port_bindings": [],
        "unsupported_instance_port_bindings": [],
    }
    valid_driver = {
        "instance_path": "tb_top.u_out",
        "pin": "count",
        "parent_instance_path": "tb_top",
        "parent_signal": "count",
        "port_direction": "output",
        "relationship": "child_output_to_parent_signal",
        "query_side": "parent_signal",
    }
    invalid_drivers = [
        {**valid_driver, "query_side": "unknown"},
        {
            **valid_driver,
            "port_direction": "input",
            "relationship": "parent_signal_to_child_input",
        },
        {**valid_driver, "relationship": "direct_pin_varref"},
        {key: value for key, value in valid_driver.items() if key != "parent_signal"},
    ]

    for invalid_driver in invalid_drivers:
        rows = desktop_crossprobe_evidence_rows(
            {
                "elaborated_connectivity": {
                    **base,
                    "boundary_drivers": [invalid_driver],
                    "boundary_loads": [],
                    "boundary_unclassified_bindings": [],
                }
            }
        )
        assert not any(
            kind in {"Elaborated boundary roles", "Elaborated role"}
            for kind, _detail in rows
        )

    rows = desktop_crossprobe_evidence_rows(
        {
            "elaborated_connectivity": {
                **base,
                "boundary_drivers": [],
                "boundary_loads": [],
                "boundary_unclassified_bindings": [
                    {
                        **valid_driver,
                        "port_direction": None,
                        "relationship": "child_output_to_parent_signal",
                    }
                ],
            }
        }
    )
    assert not any(
        kind in {"Elaborated boundary roles", "Elaborated role"}
        for kind, _detail in rows
    )
