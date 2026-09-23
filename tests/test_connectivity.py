from pathlib import Path

import pytest

from zddv.config import initialize_project, save_project
from zddv.connectivity import (
    build_connectivity_index,
    qualify_signal_navigation_with_elaboration,
    signal_navigation,
    write_connectivity_index,
)


def _connectivity_project(tmp_path: Path):
    project = initialize_project(tmp_path / "connectivity")
    (project.root / "rtl" / "design.sv").write_text(
        """
module child (
    input  logic a,
    input  logic d,
    output logic y,
    output logic q
);
    assign y = a;

    always_ff @(posedge a)
        q <= d;
endmodule

module top (
    input  logic src,
    input  logic data,
    output logic dst,
    output logic state
);
    logic mid;

    assign mid = src;
    child u_child (
        .a(mid),
        .d(data),
        .y(dst),
        .q(state)
    );
endmodule
""".lstrip(),
        encoding="utf-8",
    )
    project.rtl = ["rtl/*.sv"]
    project.top = "top"
    save_project(project)
    return project


def _roles(index, unit: str, signal: str):
    nav = signal_navigation(index, unit=unit, signal=signal)
    return (
        {item["kind"] for item in nav["drivers"]},
        {item["kind"] for item in nav["loads"]},
    )


def test_build_connectivity_index_tracks_assignment_and_instance_directions(tmp_path: Path):
    project = _connectivity_project(tmp_path)

    index = build_connectivity_index(project)

    assert index["analysis_level"] == "source_structural"
    assert index["summary"]["units"] == 2
    assert index["summary"]["unresolved_instance_connections"] == 0

    drivers, loads = _roles(index, "top", "src")
    assert "boundary_port" in drivers
    assert "continuous_assignment" in loads

    drivers, loads = _roles(index, "top", "mid")
    assert "continuous_assignment" in drivers
    assert "instance_port" in loads

    drivers, loads = _roles(index, "top", "dst")
    assert "instance_port" in drivers
    assert "boundary_port" in loads

    drivers, loads = _roles(index, "child", "q")
    assert "procedural_assignment" in drivers
    assert "boundary_port" in loads

    drivers, loads = _roles(index, "child", "d")
    assert "boundary_port" in drivers
    assert "procedural_assignment" in loads


def test_instance_navigation_keeps_port_evidence(tmp_path: Path):
    project = _connectivity_project(tmp_path)
    index = build_connectivity_index(project)

    nav = signal_navigation(index, unit="top", signal="mid")
    instance_load = next(
        item for item in nav["loads"] if item["kind"] == "instance_port"
    )

    assert instance_load["instance"] == "u_child"
    assert instance_load["child_type"] == "child"
    assert instance_load["port"] == "a"
    assert instance_load["direction"] == "input"
    assert instance_load["expression"] == "mid"
    assert instance_load["file"] == "rtl/design.sv"
    assert instance_load["line"] > 0


def test_instance_navigation_can_be_qualified_by_elaborated_scope(tmp_path: Path):
    project = _connectivity_project(tmp_path)
    index = build_connectivity_index(project)
    navigation = signal_navigation(index, unit="top", signal="mid")

    qualified = qualify_signal_navigation_with_elaboration(
        navigation,
        instance_path="top",
        elaborated_instances=[
            {
                "path": "top",
                "name": "top",
                "module": "top",
                "generate_scopes": [],
            },
            {
                "path": "top.u_child",
                "name": "u_child",
                "module": "child",
                "generate_scopes": [],
            },
        ],
        elaborated_ports=[
            {"module": "child", "name": "a", "direction": "input", "direction_field": "ioDirection"},
        ],
        port_evidence={"status": "NORMALIZED", "source_format": "json", "contract": "verilator_module_var_io_direction"},
        elaborated_pin_bindings=[
            {"instance_path": "top.u_child", "parent_instance_path": "top", "pin": "a", "status": "NORMALIZED", "signal": "mid", "expression_type": "VARREF"},
        ],
        pin_binding_evidence={"status": "NORMALIZED", "source_format": "json", "contract": "verilator_cell_pin_direct_varref_only"},
    )

    instance_load = next(
        item for item in qualified["loads"] if item["kind"] == "instance_port"
    )
    assert qualified["instance_path"] == "top"
    assert qualified["instance_qualification"] == "simulator_elaborated_scope"
    assert instance_load["instance_path"] == "top"
    assert instance_load["elaborated_child_resolution"] == "exact"
    assert instance_load["elaborated_child_path"] == "top.u_child"
    assert instance_load["elaborated_port_resolution"] == "matched"
    assert instance_load["elaborated_port_direction"] == "input"
    assert instance_load["elaborated_role_consistent"] is True
    assert instance_load["elaborated_pin_binding_resolution"] == "matched"
    assert instance_load["elaborated_pin_signal"] == "mid"
    assert instance_load["elaborated_pin_signal_consistent"] is True


def test_generated_child_qualification_preserves_ambiguity(tmp_path: Path):
    project = _connectivity_project(tmp_path)
    index = build_connectivity_index(project)
    navigation = signal_navigation(index, unit="top", signal="mid")

    qualified = qualify_signal_navigation_with_elaboration(
        navigation,
        instance_path="top",
        elaborated_instances=[
            {
                "path": "top.g[0].u_child",
                "name": "u_child",
                "module": "child",
                "generate_scopes": ["g[0]"],
            },
            {
                "path": "top.g[1].u_child",
                "name": "u_child",
                "module": "child",
                "generate_scopes": ["g[1]"],
            },
        ],
    )

    instance_load = next(
        item for item in qualified["loads"] if item["kind"] == "instance_port"
    )
    assert instance_load["elaborated_child_resolution"] == "ambiguous"
    assert instance_load["elaborated_child_candidates"] == [
        "top.g[0].u_child",
        "top.g[1].u_child",
    ]
    assert "elaborated_child_path" not in instance_load


def test_inout_is_both_driver_and_load(tmp_path: Path):
    project = initialize_project(tmp_path / "inout")
    (project.root / "rtl" / "io.sv").write_text(
        """
module pad (
    inout wire io
);
endmodule

module top;
    wire pad_net;
    pad u_pad (.io(pad_net));
endmodule
""".lstrip(),
        encoding="utf-8",
    )
    project.rtl = ["rtl/*.sv"]
    project.top = "top"
    save_project(project)

    index = build_connectivity_index(project)
    nav = signal_navigation(index, unit="top", signal="pad_net")

    assert [item["role"] for item in nav["drivers"]] == ["driver"]
    assert [item["role"] for item in nav["loads"]] == ["load"]
    assert nav["drivers"][0]["kind"] == "instance_port"
    assert nav["loads"][0]["kind"] == "instance_port"


def test_write_connectivity_index_and_unknown_signal_errors(tmp_path: Path):
    project = _connectivity_project(tmp_path)

    result = write_connectivity_index(project)

    path = Path(result["path"])
    assert path.exists()
    assert path.name == "connectivity.json"
    assert '"source_structural"' in path.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="Unknown design unit"):
        signal_navigation(result, unit="missing", signal="src")

    with pytest.raises(ValueError, match="Unknown signal"):
        signal_navigation(result, unit="top", signal="missing")



def test_relational_less_equal_is_not_misread_as_nonblocking_assignment(tmp_path: Path):
    project = initialize_project(tmp_path / "comparison")
    (project.root / "rtl" / "comparison.sv").write_text(
        """module top (
    input logic a,
    input logic b,
    input logic c,
    output logic y
);
    always_comb begin
        if (a <= b) y = c;
    end
endmodule
""",
        encoding="utf-8",
    )
    project.rtl = ["rtl/*.sv"]
    project.top = "top"
    save_project(project)

    index = build_connectivity_index(project)
    a_nav = signal_navigation(index, unit="top", signal="a")
    y_nav = signal_navigation(index, unit="top", signal="y")

    assert not any(
        item["kind"] == "procedural_assignment" for item in a_nav["drivers"]
    )
    assert any(
        item["kind"] == "procedural_assignment" for item in y_nav["drivers"]
    )


def test_non_ansi_positional_ports_preserve_header_order(tmp_path: Path):
    project = initialize_project(tmp_path / "non-ansi")
    (project.root / "rtl" / "non_ansi.sv").write_text(
        """module child(a, y, b);
    output y;
    input a, b;
    assign y = a;
endmodule

module top(
    input logic x,
    input logic w,
    output logic z
);
    child u_child(x, z, w);
endmodule
""",
        encoding="utf-8",
    )
    project.rtl = ["rtl/*.sv"]
    project.top = "top"
    save_project(project)

    index = build_connectivity_index(project)
    x_nav = signal_navigation(index, unit="top", signal="x")
    z_nav = signal_navigation(index, unit="top", signal="z")
    w_nav = signal_navigation(index, unit="top", signal="w")

    x_edge = next(item for item in x_nav["loads"] if item["kind"] == "instance_port")
    z_edge = next(item for item in z_nav["drivers"] if item["kind"] == "instance_port")
    w_edge = next(item for item in w_nav["loads"] if item["kind"] == "instance_port")

    assert (x_edge["port"], x_edge["direction"]) == ("a", "input")
    assert (z_edge["port"], z_edge["direction"]) == ("y", "output")
    assert (w_edge["port"], w_edge["direction"]) == ("b", "input")


def test_comma_separated_instance_declarations_are_all_indexed(tmp_path: Path):
    project = initialize_project(tmp_path / "multi-instance")
    (project.root / "rtl" / "multi_instance.sv").write_text(
        """module child(input logic a, output logic y);
    assign y = a;
endmodule

module top(
    input logic a,
    input logic b,
    output logic y1,
    output logic y2
);
    child u1(a, y1), u2(b, y2);
endmodule
""",
        encoding="utf-8",
    )
    project.rtl = ["rtl/*.sv"]
    project.top = "top"
    save_project(project)

    index = build_connectivity_index(project)
    b_nav = signal_navigation(index, unit="top", signal="b")
    y2_nav = signal_navigation(index, unit="top", signal="y2")

    b_edge = next(item for item in b_nav["loads"] if item["kind"] == "instance_port")
    y2_edge = next(item for item in y2_nav["drivers"] if item["kind"] == "instance_port")

    assert b_edge["instance"] == "u2"
    assert y2_edge["instance"] == "u2"
