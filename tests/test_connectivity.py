from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project, save_project
from zddv.connectivity import (
    build_connectivity_index,
    query_connectivity,
    write_connectivity_index,
)


def _project(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    (project.root / "rtl" / "design.sv").write_text(
        """
module child(
    input logic a,
    output logic y
);
    assign y = a;
endmodule

module top;
    logic a = 1'b0;
    logic y;

    child u_child(
        .a(a),
        .y(y)
    );

    always #5 a = ~a;
endmodule
""".lstrip(),
        encoding="utf-8",
    )
    project.rtl = ["rtl/*.sv"]
    project.top = "top"
    save_project(project)
    return project


def test_build_source_level_driver_load_connectivity(tmp_path: Path):
    project = _project(tmp_path)

    index = build_connectivity_index(project)

    child_a = query_connectivity(index, "a", unit="child")[0]
    assert {ref["kind"] for ref in child_a["drivers"]} == {"input-port"}
    assert {ref["kind"] for ref in child_a["loads"]} == {"continuous-assignment"}

    child_y = query_connectivity(index, "y", unit="child")[0]
    assert {ref["kind"] for ref in child_y["drivers"]} == {"continuous-assignment"}
    assert {ref["kind"] for ref in child_y["loads"]} == {"output-port"}

    top_a = query_connectivity(index, "a", unit="top")[0]
    assert "procedural-assignment" in {ref["kind"] for ref in top_a["drivers"]}
    assert "instance-port" in {ref["kind"] for ref in top_a["loads"]}

    top_y = query_connectivity(index, "y", unit="top")[0]
    assert {ref["kind"] for ref in top_y["drivers"]} == {"instance-port"}


def test_query_without_unit_returns_all_matching_units(tmp_path: Path):
    project = _project(tmp_path)
    index = build_connectivity_index(project)

    matches = query_connectivity(index, "a")

    assert [item["unit"] for item in matches] == ["child", "top"]


def test_write_connectivity_index(tmp_path: Path):
    project = _project(tmp_path)

    result = write_connectivity_index(project)

    path = Path(result["path"])
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert '"analysis": "source-level-conservative"' in text
    assert '"instance-port"' in text


def test_net_cli_prints_drivers_and_loads(tmp_path: Path, capsys):
    project = _project(tmp_path)

    rc = main([
        "--project",
        str(project.root),
        "net",
        "a",
        "--unit",
        "top",
    ])

    assert rc == 0
    output = capsys.readouterr().out
    assert "NET top.a" in output
    assert "DRIVERS" in output
    assert "LOADS" in output
    assert "u_child.a (child input)" in output
    assert (project.root / ".zddv" / "design" / "connectivity.json").is_file()
