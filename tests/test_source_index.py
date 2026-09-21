from __future__ import annotations

import json
from pathlib import Path

import pytest

from zddv.config import initialize_project, save_project
from zddv.source_index import (
    build_hierarchy,
    hierarchy_lines,
    scan_project_sources,
    write_source_index,
)


def _project(tmp_path: Path):
    root = tmp_path / "demo"
    config = initialize_project(root)
    (root / "rtl" / "design.sv").write_text(
        """
package common_pkg;
endpackage

module leaf #(parameter int WIDTH = 8) (
    input logic [WIDTH-1:0] a,
    output logic [WIDTH-1:0] y
);
    assign y = a;
endmodule

module bridge(input logic a, output logic y);
    leaf #(.WIDTH(1)) u_leaf (.a(a), .y(y));
endmodule

// module fake_from_comment; endmodule
""".lstrip(),
        encoding="utf-8",
    )
    (root / "tb" / "tb.sv").write_text(
        """
module tb_top;
    logic a;
    logic y;
    bridge dut (.a(a), .y(y));
    string ignored = "module fake_from_string; endmodule";
endmodule
""".lstrip(),
        encoding="utf-8",
    )
    config.rtl = ["rtl/*.sv"]
    config.tb = ["tb/*.sv"]
    config.top = "tb_top"
    save_project(config)
    return config


def test_scan_project_sources_finds_design_units_and_instances(tmp_path: Path):
    config = _project(tmp_path)
    index = scan_project_sources(config)

    names = {(item["kind"], item["name"]) for item in index["symbols"]}
    assert ("package", "common_pkg") in names
    assert ("module", "leaf") in names
    assert ("module", "bridge") in names
    assert ("module", "tb_top") in names
    assert all("fake_from" not in item["name"] for item in index["symbols"])

    modules = {item["name"]: item for item in index["modules"]}
    assert modules["bridge"]["instances"] == [
        {
            "module": "leaf",
            "instance": "u_leaf",
            "line": 12,
            "resolved": True,
        }
    ]
    assert modules["tb_top"]["instances"][0]["module"] == "bridge"
    assert modules["tb_top"]["instances"][0]["instance"] == "dut"


def test_build_hierarchy_resolves_top_to_leaf(tmp_path: Path):
    config = _project(tmp_path)
    hierarchy = build_hierarchy(scan_project_sources(config))

    assert hierarchy["path"] == "tb_top"
    bridge = hierarchy["children"][0]
    leaf = bridge["children"][0]
    assert bridge["path"] == "tb_top.dut"
    assert leaf["path"] == "tb_top.dut.u_leaf"
    assert leaf["module"] == "leaf"
    assert leaf["definition"]["file"] == "rtl/design.sv"
    assert leaf["instantiated_at"]["file"] == "rtl/design.sv"

    assert hierarchy_lines(hierarchy) == [
        "tb_top : tb_top",
        "└─ dut : bridge",
        "   └─ u_leaf : leaf",
    ]


def test_hierarchy_rejects_missing_top(tmp_path: Path):
    config = _project(tmp_path)
    index = scan_project_sources(config)

    with pytest.raises(ValueError, match="was not found"):
        build_hierarchy(index, top="does_not_exist")


def test_hierarchy_rejects_duplicate_module_definitions(tmp_path: Path):
    config = _project(tmp_path)
    (config.root / "rtl" / "duplicate.sv").write_text(
        "module leaf; endmodule\n",
        encoding="utf-8",
    )
    index = scan_project_sources(config)

    with pytest.raises(ValueError, match="Duplicate module definitions"):
        build_hierarchy(index)


def test_write_source_index_creates_json_artifacts(tmp_path: Path):
    config = _project(tmp_path)
    result = write_source_index(config)

    assert result["file_count"] == 2
    assert result["module_count"] == 3
    assert result["instance_count"] == 2
    assert result["index_path"].exists()
    assert result["hierarchy_path"].exists()

    index = json.loads(result["index_path"].read_text(encoding="utf-8"))
    hierarchy = json.loads(result["hierarchy_path"].read_text(encoding="utf-8"))
    assert index["schema_version"] == 1
    assert index["top"] == "tb_top"
    assert hierarchy["hierarchy"]["children"][0]["module"] == "bridge"
