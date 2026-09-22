from __future__ import annotations

from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project
from zddv.uvm_instrument import (
    DEFAULT_UVM_ITEM_INSTRUMENTATION_PATH,
    render_uvm_item_instrumentation,
    write_uvm_item_instrumentation,
)


def test_rendered_uvm_item_instrumentation_has_public_uvm_hooks_and_marker():
    source = render_uvm_item_instrumentation()

    assert source.startswith("`ifndef ZDDV_UVM_ITEM_INSTRUMENTATION_SVH")
    assert "package zddv_uvm_item_instrumentation_pkg;" in source
    assert "class zddv_instrumented_sequencer #(" in source
    assert "extends uvm_sequencer #(REQ, RSP);" in source
    assert "virtual task wait_for_grant(" in source
    assert "virtual function void send_request(" in source
    assert "virtual function void item_done(RSP response = null);" in source
    assert "virtual task get(output REQ item);" in source
    assert "virtual function void put_response(RSP response);" in source
    assert '"ZDDV_UVM_ITEM {' in source
    assert "super.send_request(sequence_ptr, item, rerandomize);" in source
    assert source.index("super.send_request(sequence_ptr, item, rerandomize);") < source.index(
        "record = zddv_make_record(sequence_ptr, item);"
    )


def test_write_uvm_item_instrumentation_uses_project_relative_default(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")

    destination = write_uvm_item_instrumentation(project)

    assert destination == (project.root / DEFAULT_UVM_ITEM_INSTRUMENTATION_PATH).resolve()
    assert destination.is_file()
    source = destination.read_text(encoding="utf-8")
    assert "ZDDV_UVM_ITEM" in source
    assert "zddv_instrumented_sequencer" in source


def test_cli_generates_uvm_item_instrumentation(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    output = "tb/generated/zddv_uvm_items.svh"

    rc = main(
        [
            "--project",
            str(project.root),
            "uvm-item-instrument",
            "--output",
            output,
        ]
    )

    assert rc == 0
    generated = project.root / output
    assert generated.is_file()
    source = generated.read_text(encoding="utf-8")
    assert "virtual task wait_for_grant(" in source
    assert "virtual function void item_done(RSP response = null);" in source

    stdout = capsys.readouterr().out
    assert "Generated UVM item instrumentation:" in stdout
    assert "zddv_instrumented_sequencer" in stdout
    assert "Marker: ZDDV_UVM_ITEM" in stdout
