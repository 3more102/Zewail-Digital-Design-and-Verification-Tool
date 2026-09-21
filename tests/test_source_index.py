from pathlib import Path

from zddv.config import initialize_project, save_project
from zddv.source_index import build_source_index, hierarchy_lines, write_source_index


def _project_with_sources(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    project.rtl = ["rtl/*.sv"]
    project.tb = ["tb/*.sv"]
    project.top = "tb_top"
    save_project(project)

    (project.root / "rtl" / "child.sv").write_text(
        """
module child #(parameter int WIDTH = 8) (
    input logic clk
);
endmodule
""",
        encoding="utf-8",
    )
    (project.root / "tb" / "tb_top.sv").write_text(
        """
module tb_top;
    logic clk;

    // child fake_in_comment (.clk(clk));
    /*
      child another_fake (.clk(clk));
    */
    child #(
        .WIDTH(16)
    ) dut (
        .clk(clk)
    );
endmodule
""",
        encoding="utf-8",
    )
    return project


def test_build_source_index_and_hierarchy(tmp_path: Path):
    project = _project_with_sources(tmp_path)

    index = build_source_index(project)

    assert index["module_count"] == 2
    assert index["edge_count"] == 1
    assert index["roots"] == ["tb_top"]

    tb_top = next(item for item in index["modules"] if item["name"] == "tb_top")
    assert tb_top["instances"] == [
        {
            "module": "child",
            "instance": "dut",
            "line": 9,
        }
    ]

    assert hierarchy_lines(index) == [
        "tb_top",
        "└── dut: child",
    ]


def test_write_source_index_creates_json_artifact(tmp_path: Path):
    project = _project_with_sources(tmp_path)

    result = write_source_index(project)

    path = Path(result["path"])
    assert path.exists()
    assert path.name == "index.json"
    assert result["index"]["configured_top"] == "tb_top"


def test_duplicate_module_definitions_are_rejected(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    project.rtl = ["rtl/*.sv"]
    save_project(project)
    (project.root / "rtl" / "a.sv").write_text(
        "module duplicate; endmodule\n",
        encoding="utf-8",
    )
    (project.root / "rtl" / "b.sv").write_text(
        "module duplicate; endmodule\n",
        encoding="utf-8",
    )

    try:
        build_source_index(project)
    except ValueError as exc:
        assert "Duplicate module definitions" in str(exc)
    else:
        raise AssertionError("Expected duplicate module error")
