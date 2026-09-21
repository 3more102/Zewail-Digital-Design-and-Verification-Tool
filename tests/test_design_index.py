from pathlib import Path

from zddv.config import initialize_project, save_project
from zddv.design_index import build_design_index, hierarchy_lines, write_design_index


def _project_with_sources(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    (project.root / "rtl" / "design.sv").write_text(
        """
package cfg_pkg;
endpackage

module leaf;
endmodule

module child;
    leaf u_leaf();
endmodule

module top;
    // child fake_comment_instance();
    initial $display("child fake_string_instance()");
    child #(.IGNORED(1)) u_child();
endmodule
""".lstrip(),
        encoding="utf-8",
    )
    project.rtl = ["rtl/*.sv"]
    project.top = "top"
    save_project(project)
    return project


def test_build_source_index_and_hierarchy(tmp_path: Path):
    project = _project_with_sources(tmp_path)

    index = build_design_index(project)

    assert index["schema_version"] == 1
    assert index["summary"] == {
        "files": 1,
        "units": 4,
        "instances": 2,
        "duplicate_unit_names": 0,
    }

    names = {(unit["kind"], unit["name"]) for unit in index["units"]}
    assert names == {
        ("package", "cfg_pkg"),
        ("module", "leaf"),
        ("module", "child"),
        ("module", "top"),
    }

    top = next(unit for unit in index["units"] if unit["name"] == "top")
    assert top["instances"] == [{"name": "u_child", "type": "child", "line": 13}]

    hierarchy = index["hierarchy"]
    assert hierarchy["path"] == "top"
    assert hierarchy["children"][0]["path"] == "top.u_child"
    assert hierarchy["children"][0]["children"][0]["path"] == "top.u_child.u_leaf"

    assert hierarchy_lines(hierarchy) == [
        "top: top",
        "  u_child: child",
        "    u_leaf: leaf",
    ]


def test_comments_and_strings_do_not_create_instances(tmp_path: Path):
    project = _project_with_sources(tmp_path)
    index = build_design_index(project)

    instance_names = {item["name"] for item in index["instances"]}
    assert instance_names == {"u_child", "u_leaf"}
    assert "fake_comment_instance" not in instance_names
    assert "fake_string_instance" not in instance_names


def test_recursive_hierarchy_is_bounded(tmp_path: Path):
    project = initialize_project(tmp_path / "recursive")
    (project.root / "rtl" / "recursive.sv").write_text(
        """
module a;
    b u_b();
endmodule

module b;
    a u_a();
endmodule
""".lstrip(),
        encoding="utf-8",
    )
    project.rtl = ["rtl/*.sv"]
    project.top = "a"
    save_project(project)

    hierarchy = build_design_index(project)["hierarchy"]

    recursive = hierarchy["children"][0]["children"][0]
    assert recursive["type"] == "a"
    assert recursive["recursive"] is True
    assert recursive["children"] == []


def test_write_design_index(tmp_path: Path):
    project = _project_with_sources(tmp_path)

    result = write_design_index(project)

    path = Path(result["path"])
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert '"schema_version": 1' in text
    assert '"u_child"' in text


def test_missing_top_is_reported_unresolved(tmp_path: Path):
    project = _project_with_sources(tmp_path)
    project.top = "missing_top"

    index = build_design_index(project)

    assert index["hierarchy"]["resolved"] is False
    assert hierarchy_lines(index["hierarchy"]) == [
        "missing_top: missing_top [UNRESOLVED]"
    ]
