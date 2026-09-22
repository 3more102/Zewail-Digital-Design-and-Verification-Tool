from __future__ import annotations

from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project, load_project, save_project
from zddv.uvm_sequence_instrumentation import write_uvm_sequence_instrumentation


def test_generator_writes_portable_helper_and_registers_first(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    project.tb = ["tb/*.sv"]
    save_project(project)

    result = write_uvm_sequence_instrumentation(project)

    generated = Path(result["path"])
    assert generated.is_file()
    text = generated.read_text(encoding="utf-8")
    assert "package zddv_uvm_sequence_trace_pkg;" in text
    assert r'ZDDV_UVM_SEQUENCE {\"sequence_id\"' in text
    assert "function automatic string zddv_json_escape" in text
    assert "zddv_uvm_sequence_emit" in text
    assert "zddv_uvm_sequence_created" in text
    assert "zddv_uvm_sequence_body" in text
    assert "zddv_uvm_sequence_finished" in text
    assert "UVM_STOPPED" in text
    assert result["marker"] == "ZDDV_UVM_SEQUENCE"
    assert result["added_to_project"] is True

    loaded = load_project(project.root)
    assert loaded.tb == [
        "tb/zddv_uvm_sequence_trace_pkg.sv",
        "tb/*.sv",
    ]


def test_generator_refuses_overwrite_without_force(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    write_uvm_sequence_instrumentation(project)

    with pytest.raises(FileExistsError, match="--force"):
        write_uvm_sequence_instrumentation(project)

    result = write_uvm_sequence_instrumentation(project, force=True)
    assert Path(result["path"]).is_file()
    assert result["added_to_project"] is False


def test_generator_can_skip_project_source_registration(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    project.tb = ["tb/*.sv"]
    save_project(project)

    result = write_uvm_sequence_instrumentation(
        project,
        output="generated/zddv_sequence_trace.sv",
        add_source=False,
    )

    assert Path(result["path"]).is_file()
    assert result["project_path"] is None
    assert result["added_to_project"] is False
    assert load_project(project.root).tb == ["tb/*.sv"]


def test_generator_rejects_external_registered_path_without_writing(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    outside = tmp_path / "outside.sv"

    with pytest.raises(ValueError, match="inside the project"):
        write_uvm_sequence_instrumentation(project, output=outside, add_source=True)

    assert not outside.exists()


def test_cli_generates_sequence_helper(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")

    rc = main(
        [
            "--project",
            str(project.root),
            "uvm-sequence-instrument",
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "UVM sequence instrumentation:" in output
    assert "Marker: ZDDV_UVM_SEQUENCE" in output
    assert "tb/zddv_uvm_sequence_trace_pkg.sv" in output
    assert (
        project.root / "tb" / "zddv_uvm_sequence_trace_pkg.sv"
    ).is_file()
