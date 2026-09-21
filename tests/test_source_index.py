from pathlib import Path

from zddv.config import initialize_project, save_project
from zddv.source_index import build_design_index, format_hierarchy, write_design_index


def _project(tmp_path: Path):
    root = tmp_path / "design"
    project = initialize_project(root)

    (root / "rtl" / "leaf.sv").write_text(
        """module leaf(input logic a, output logic y);
assign y = a;
endmodule
""",
        encoding="utf-8",
    )
    (root / "rtl" / "mid.sv").write_text(
        """module mid(input logic a, output logic y);
leaf #(.DUMMY(1)) u_leaf (.a(a), .y(y));
endmodule
""",
        encoding="utf-8",
    )
    (root / "tb" / "top.sv").write_text(
        """module top;
logic a;
logic y;
// leaf fake_instance (.a(a), .y(y));
string note = "mid fake_string (.a(a), .y(y));";
mid dut (.a(a), .y(y));
endmodule
""",
        encoding="utf-8",
    )

    project.rtl = ["rtl/*.sv"]
    project.tb = ["tb/*.sv"]
    project.top = "top"
    save_project(project)
    return project


def test_build_design_index_discovers_modules_and_hierarchy(tmp_path: Path):
    project = _project(tmp_path)
    index = build_design_index(project)

    assert index["stats"]["source_files"] == 3
    assert index["stats"]["modules"] == 3
    assert index["stats"]["module_instances"] == 2
    assert index["stats"]["hierarchy_nodes"] == 3

    modules = {item["name"]: item for item in index["modules"]}
    assert set(modules) == {"leaf", "mid", "top"}
    assert modules["top"]["instances"] == [
        {
            "name": "dut",
            "module": "mid",
            "source": "tb/top.sv",
            "line": 6,
        }
    ]

    root = index["hierarchy"]
    assert root["module"] == "top"
    assert root["children"][0]["instance"] == "dut"
    assert root["children"][0]["module"] == "mid"
    assert root["children"][0]["children"][0]["module"] == "leaf"


def test_write_and_format_design_index(tmp_path: Path):
    project = _project(tmp_path)
    index = build_design_index(project)

    path = write_design_index(project, index)
    assert path.exists()
    assert path.name == "design.json"

    tree = format_hierarchy(index)
    assert "top: top" in tree
    assert "dut: mid" in tree
    assert "u_leaf: leaf" in tree
