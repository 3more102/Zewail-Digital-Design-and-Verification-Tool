from pathlib import Path

from zddv.config import ProjectConfig
from zddv.source_index import analyze_source_files, build_design_index
from zddv.storage import (
    list_design_index_snapshots,
    list_design_instances,
    list_design_modules,
)


def test_source_index_reconstructs_project_local_hierarchy(tmp_path: Path):
    child = tmp_path / "child.sv"
    child.write_text(
        "module child(input logic a); endmodule\\n",
        encoding="utf-8",
    )
    top = tmp_path / "top.sv"
    top.write_text(
        """module top;
  child #(.WIDTH(1)) u0(.a(1'b0)), u1(.a(1'b1));
  // child fake_comment(.a(1'b0));
  initial $display("child fake_string(.a(1'b0))");
endmodule
""",
        encoding="utf-8",
    )

    result = analyze_source_files([child, top], "top")

    assert [item["module_name"] for item in result["modules"]] == ["child", "top"]
    assert [item["instance_path"] for item in result["instances"]] == [
        "top.u0",
        "top.u1",
    ]
    assert all(item["module_name"] == "child" for item in result["instances"])


def test_design_index_is_persisted_in_sqlite(tmp_path: Path):
    rtl = tmp_path / "rtl"
    tb = tmp_path / "tb"
    rtl.mkdir()
    tb.mkdir()

    (rtl / "leaf.sv").write_text(
        "module leaf(input logic a); endmodule\\n",
        encoding="utf-8",
    )
    (rtl / "wrapper.sv").write_text(
        """module wrapper;
  leaf u_leaf(.a(1'b0));
endmodule
""",
        encoding="utf-8",
    )
    (tb / "tb_top.sv").write_text(
        """module tb_top;
  wrapper dut();
endmodule
""",
        encoding="utf-8",
    )

    project = ProjectConfig(
        root=tmp_path,
        name="index-test",
        top="tb_top",
        rtl=["rtl/*.sv"],
        tb=["tb/*.sv"],
    )

    result = build_design_index(project)
    snapshots = list_design_index_snapshots(project, limit=1)
    modules = list_design_modules(project, result["snapshot_id"])
    instances = list_design_instances(project, result["snapshot_id"])

    assert Path(result["index_path"]).exists()
    assert result["module_count"] == 3
    assert result["instance_count"] == 2
    assert snapshots[0]["top"] == "tb_top"
    assert {item["module_name"] for item in modules} == {
        "leaf",
        "tb_top",
        "wrapper",
    }
    assert [item["instance_path"] for item in instances] == [
        "tb_top.dut",
        "tb_top.dut.u_leaf",
    ]
