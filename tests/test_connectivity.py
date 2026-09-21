from pathlib import Path

import pytest

from zddv.config import initialize_project, save_project
from zddv.connectivity import (
    build_connectivity_index,
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
