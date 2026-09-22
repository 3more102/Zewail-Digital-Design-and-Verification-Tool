from __future__ import annotations

from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project, load_project, save_project
from zddv.uvm_auto_instrumentation import write_uvm_auto_instrumentation


def test_generator_writes_standard_uvm_adapter_and_orders_sources(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    project.tb = ["tb/*.sv"]
    save_project(project)

    result = write_uvm_auto_instrumentation(project)

    generated = Path(result["path"])
    assert generated.is_file()
    text = generated.read_text(encoding="utf-8")

    assert "virtual class zddv_instrumented_sequence" in text
    assert "extends uvm_sequence #(REQ, RSP)" in text
    assert "pure virtual task zddv_body();" in text
    assert "virtual task start(" in text
    assert "virtual task start_item(" in text
    assert "virtual task finish_item(" in text
    assert "virtual task get_response(" in text
    assert 'zddv_emit_item_event("ARB_REQUEST"' in text
    assert 'zddv_emit_item_event("GRANT"' in text
    assert 'zddv_emit_item_event("REQUEST"' in text
    assert 'zddv_emit_item_event("ITEM_DONE"' in text
    assert '"RESPONSE"' in text
    assert "zddv_transaction_ambiguous" in text
    assert "!zddv_transaction_ambiguous.exists(observed_transaction_id)" in text

    # UVM's own source documents get_sequence_id() as internal/private-use.
    assert "get_sequence_id()" not in text
    assert "get_inst_id()" in text
    assert "inspect hidden sequencer queues" in text
    assert any("Duplicate transaction IDs" in item for item in result["limitations"])

    assert Path(result["sequence_helper"]).is_file()
    assert Path(result["item_helper"]).is_file()
    assert result["sequence_helper_created"] is True
    assert result["item_helper_created"] is True

    loaded = load_project(project.root)
    assert loaded.tb == [
        "tb/zddv_uvm_sequence_trace_pkg.sv",
        "tb/zddv_uvm_item_trace_pkg.sv",
        "tb/zddv_uvm_auto_trace_pkg.sv",
        "tb/*.sv",
    ]


def test_generator_reuses_existing_marker_helpers_without_overwrite(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    first = write_uvm_auto_instrumentation(project)

    sequence_helper = Path(first["sequence_helper"])
    original = sequence_helper.read_text(encoding="utf-8")
    Path(first["path"]).unlink()

    second = write_uvm_auto_instrumentation(project)

    assert second["sequence_helper_created"] is False
    assert second["item_helper_created"] is False
    assert sequence_helper.read_text(encoding="utf-8") == original


def test_generator_rejects_incompatible_existing_marker_helper(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    helper = project.root / "tb" / "zddv_uvm_sequence_trace_pkg.sv"
    helper.parent.mkdir(parents=True, exist_ok=True)
    helper.write_text("package wrong_pkg; endpackage\n", encoding="utf-8")

    with pytest.raises(ValueError, match="incompatible"):
        write_uvm_auto_instrumentation(project)

    assert not (project.root / "tb" / "zddv_uvm_auto_trace_pkg.sv").exists()


def test_generator_refuses_adapter_overwrite_without_force(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    write_uvm_auto_instrumentation(project)

    with pytest.raises(FileExistsError, match="--force"):
        write_uvm_auto_instrumentation(project)

    result = write_uvm_auto_instrumentation(project, force=True)
    assert Path(result["path"]).is_file()


def test_generator_can_skip_source_registration(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    project.tb = ["tb/*.sv"]
    save_project(project)

    result = write_uvm_auto_instrumentation(
        project,
        output="generated/zddv_uvm_auto_trace_pkg.sv",
        add_source=False,
    )

    assert Path(result["path"]).is_file()
    assert result["project_path"] is None
    assert result["added_to_project"] is False
    assert result["source_order"] == []
    assert load_project(project.root).tb == ["tb/*.sv"]


@pytest.mark.parametrize(
    "output",
    [
        "tb/zddv_uvm_sequence_trace_pkg.sv",
        "tb/zddv_uvm_item_trace_pkg.sv",
    ],
)
def test_generator_rejects_adapter_helper_path_collision(
    tmp_path: Path,
    output: str,
):
    project = initialize_project(tmp_path / "demo")

    with pytest.raises(ValueError, match="distinct"):
        write_uvm_auto_instrumentation(project, output=output)

    assert not (project.root / output).exists()


def test_generator_rejects_external_registered_path_before_writing(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    outside = tmp_path / "outside.sv"

    with pytest.raises(ValueError, match="inside the project"):
        write_uvm_auto_instrumentation(
            project,
            output=outside,
            add_source=True,
        )

    assert not outside.exists()
    assert not (project.root / "tb" / "zddv_uvm_sequence_trace_pkg.sv").exists()
    assert not (project.root / "tb" / "zddv_uvm_item_trace_pkg.sv").exists()


def test_cli_generates_auto_adapter(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")

    rc = main(
        [
            "--project",
            str(project.root),
            "uvm-auto-instrument",
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "UVM automatic instrumentation adapter:" in output
    assert "Base class: zddv_instrumented_sequence" in output
    assert "Automatic execution: disabled" in output
    assert (
        project.root / "tb" / "zddv_uvm_auto_trace_pkg.sv"
    ).is_file()
