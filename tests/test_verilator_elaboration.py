import json
from pathlib import Path
import subprocess

import pytest

from zddv.config import initialize_project, save_project
from zddv.simulator import verilator_elaboration
from zddv.simulator.verilator_elaboration import (
    normalize_verilator_elaboration,
    write_verilator_elaboration,
)


TREE = {
    "type": "NETLIST",
    "name": "$root",
    "modulesp": [
        {
            "type": "MODULE",
            "name": "top",
            "origName": "top",
            "verilogName": "top",
            "level": 1,
            "depth": 2,
            "loc": "d,1:8,1:11",
            "stmtsp": [
                {
                    "type": "GENBLOCK",
                    "name": "g[0]",
                    "loc": "d,4:3,4:4",
                    "itemsp": [
                        {
                            "type": "CELL",
                            "name": "u_leaf",
                            "origName": "u_leaf",
                            "verilogName": "u_leaf",
                            "modName": "leaf",
                            "loc": "d,5:5,5:11",
                        }
                    ],
                },
                {
                    "type": "CELL",
                    "name": "u_missing",
                    "origName": "u_missing",
                    "verilogName": "u_missing",
                    "modName": "missing",
                    "loc": "d,7:3,7:12",
                },
            ],
        },
        {
            "type": "PACKAGE",
            "name": "$unit",
            "origName": "__024unit",
            "verilogName": "\\$unit ",
            "level": 2,
            "depth": 3,
            "loc": "a,0:0,0:0",
        },
        {
            "type": "MODULE",
            "name": "leaf",
            "origName": "leaf",
            "verilogName": "leaf",
            "level": 2,
            "depth": 2,
            "loc": "d,10:8,10:12",
        },
    ],
}
META = {
    "files": {
        "d": {
            "filename": "rtl/design.sv",
            "realpath": "/workspace/rtl/design.sv",
            "language": "1800-2023",
        }
    }
}


def test_normalize_verilator_elaboration_keeps_generate_and_source_evidence():
    result = normalize_verilator_elaboration(
        TREE,
        META,
        project_name="demo",
        top="top",
        simulator_version="Verilator 5.052",
        tree_sha256="tree-sha",
        meta_sha256="meta-sha",
    )

    assert result["analysis_level"] == "verilator_elaborated_json"
    assert result["summary"] == {
        "modules": 2,
        "cells": 2,
        "generated_cells": 1,
        "hierarchy_nodes": 3,
        "unresolved_hierarchy_nodes": 1,
    }
    assert result["provenance"] == {
        "tree_sha256": "tree-sha",
        "meta_sha256": "meta-sha",
    }

    generated = result["hierarchy"]["children"][0]
    assert generated["instance"] == "u_leaf"
    assert generated["type"] == "leaf"
    assert generated["path"] == "top.g[0].u_leaf"
    assert generated["generate_scopes"] == ["g[0]"]
    assert generated["source"]["file"] == "rtl/design.sv"
    assert generated["source"]["line"] == 5

    unresolved = result["hierarchy"]["children"][1]
    assert unresolved["instance"] == "u_missing"
    assert unresolved["resolved"] is False


def test_normalizer_rejects_unknown_json_root():
    with pytest.raises(ValueError, match="expected NETLIST"):
        normalize_verilator_elaboration(
            {"type": "UNKNOWN"},
            {},
            project_name="demo",
            top="top",
        )


def test_writer_uses_supported_json_only_contract(tmp_path: Path, monkeypatch):
    project = initialize_project(tmp_path / "demo")
    (project.root / "rtl" / "design.sv").write_text(
        "module leaf; endmodule\n"
        "module top; leaf u_leaf(); endmodule\n",
        encoding="utf-8",
    )
    project.rtl = ["rtl/*.sv"]
    project.tb = []
    project.top = "top"
    save_project(project)

    tree = {
        "type": "NETLIST",
        "name": "$root",
        "modulesp": [
            {
                "type": "MODULE",
                "name": "top",
                "origName": "top",
                "verilogName": "top",
                "level": 1,
                "depth": 2,
                "loc": "d,2:8,2:11",
                "stmtsp": [
                    {
                        "type": "CELL",
                        "name": "u_leaf",
                        "origName": "u_leaf",
                        "verilogName": "u_leaf",
                        "modName": "leaf",
                        "loc": "d,2:18,2:24",
                    }
                ],
            },
            {
                "type": "MODULE",
                "name": "leaf",
                "origName": "leaf",
                "verilogName": "leaf",
                "level": 2,
                "depth": 2,
                "loc": "d,1:8,1:12",
            },
        ],
    }
    meta = {
        "files": {
            "d": {
                "filename": "rtl/design.sv",
                "realpath": str(project.root / "rtl" / "design.sv"),
                "language": "1800-2023",
            }
        }
    }

    monkeypatch.setattr(
        verilator_elaboration,
        "_verilator_tool",
        lambda: "/tools/verilator",
    )
    monkeypatch.setattr(
        verilator_elaboration,
        "_verilator_version",
        lambda tool: "Verilator 5.052",
    )

    def fake_run(command, **kwargs):
        assert "--json-only" in command
        assert "--no-json-edit-nums" in command
        assert command[command.index("--top-module") + 1] == "top"
        tree_path = Path(command[command.index("--json-only-output") + 1])
        meta_path = Path(command[command.index("--json-only-meta-output") + 1])
        tree_path.write_text(json.dumps(tree), encoding="utf-8")
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr(verilator_elaboration.subprocess, "run", fake_run)

    result = write_verilator_elaboration(project)

    assert result["summary"]["modules"] == 2
    assert result["summary"]["unresolved_hierarchy_nodes"] == 0
    assert result["hierarchy"]["children"][0]["path"] == "top.u_leaf"
    assert result["simulator_version"] == "Verilator 5.052"
    assert len(result["provenance"]["tree_sha256"]) == 64
    assert len(result["provenance"]["meta_sha256"]) == 64
    assert Path(result["path"]).exists()
    assert Path(result["artifacts"]["tree"]).exists()
    assert Path(result["artifacts"]["meta"]).exists()


def test_writer_rejects_verilator_before_json_only_support(tmp_path: Path, monkeypatch):
    project = initialize_project(tmp_path / "demo")
    (project.root / "rtl" / "design.sv").write_text(
        "module top; endmodule\\n",
        encoding="utf-8",
    )
    project.rtl = ["rtl/*.sv"]
    project.tb = []
    project.top = "top"
    save_project(project)

    monkeypatch.setattr(
        verilator_elaboration,
        "_verilator_tool",
        lambda: "/tools/verilator",
    )
    monkeypatch.setattr(
        verilator_elaboration,
        "_verilator_version",
        lambda tool: "Verilator 5.020",
    )

    with pytest.raises(RuntimeError, match="5.044 or newer"):
        write_verilator_elaboration(project)


def test_writer_rejects_output_outside_project(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    (project.root / "rtl" / "design.sv").write_text(
        "module top; endmodule\n",
        encoding="utf-8",
    )
    project.rtl = ["rtl/*.sv"]
    project.tb = []
    project.top = "top"
    save_project(project)

    with pytest.raises(ValueError, match="inside the project root"):
        write_verilator_elaboration(
            project,
            output=tmp_path / "outside.json",
        )
