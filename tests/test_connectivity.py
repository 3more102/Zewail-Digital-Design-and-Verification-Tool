from pathlib import Path

from zddv.config import initialize_project, save_project
from zddv.connectivity import (
    build_connectivity_index,
    query_net,
    write_connectivity_index,
)


def _project(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    (project.root / "rtl" / "design.sv").write_text(
        """
module child(
    input  logic a,
    input  logic b,
    output logic y
);
    assign y = a & b;
endmodule

module top(
    input  logic in_a,
    input  logic in_b,
    output logic out_y
);
    logic mid;

    child u_child (
        .a(in_a),
        .b(in_b),
        .y(mid)
    );

    always_comb begin
        out_y = mid;
    end
endmodule
""".lstrip(),
        encoding="utf-8",
    )
    project.rtl = ["rtl/*.sv"]
    project.top = "top"
    save_project(project)
    return project


def test_connectivity_tracks_assignments_boundaries_and_instances(
    tmp_path: Path,
):
    project = _project(tmp_path)
    index = build_connectivity_index(project)

    assert index["schema_version"] == 1
    assert index["summary"]["units"] == 2
    assert index["summary"]["signals"] == 7

    top = next(
        unit
        for unit in index["units"]
        if unit["name"] == "top"
    )
    nets = {
        net["name"]: net
        for net in top["nets"]
    }

    assert any(
        item["kind"] == "port"
        and "input boundary" in item["detail"]
        for item in nets["in_a"]["drivers"]
    )
    assert any(
        item["kind"] == "instance"
        and "u_child.a" in item["detail"]
        for item in nets["in_a"]["loads"]
    )
    assert any(
        item["kind"] == "instance"
        and "u_child.y" in item["detail"]
        for item in nets["mid"]["drivers"]
    )
    assert any(
        item["kind"] == "procedural"
        for item in nets["mid"]["loads"]
    )
    assert any(
        item["kind"] == "procedural"
        for item in nets["out_y"]["drivers"]
    )
    assert any(
        item["kind"] == "port"
        and "output boundary" in item["detail"]
        for item in nets["out_y"]["loads"]
    )


def test_child_continuous_assignment_relations(
    tmp_path: Path,
):
    project = _project(tmp_path)
    index = build_connectivity_index(project)

    child = next(
        unit
        for unit in index["units"]
        if unit["name"] == "child"
    )
    nets = {
        net["name"]: net
        for net in child["nets"]
    }

    assert any(
        item["kind"] == "assign"
        for item in nets["y"]["drivers"]
    )
    assert any(
        item["kind"] == "assign"
        for item in nets["a"]["loads"]
    )
    assert any(
        item["kind"] == "assign"
        for item in nets["b"]["loads"]
    )


def test_query_net_returns_selected_role(
    tmp_path: Path,
):
    project = _project(tmp_path)
    index = build_connectivity_index(project)

    drivers = query_net(
        index,
        unit_name="top",
        signal="mid",
        role="drivers",
    )
    loads = query_net(
        index,
        unit_name="top",
        signal="mid",
        role="loads",
    )

    assert any(
        "u_child.y" in item["detail"]
        for item in drivers
    )
    assert any(
        item["kind"] == "procedural"
        for item in loads
    )


def test_write_connectivity_index(
    tmp_path: Path,
):
    project = _project(tmp_path)
    result = write_connectivity_index(project)

    path = Path(result["path"])
    assert path.is_file()
    assert path.name == "connectivity.json"
    assert '"driver_edges"' in path.read_text(
        encoding="utf-8",
    )
