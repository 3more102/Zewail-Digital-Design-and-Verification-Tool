from __future__ import annotations

from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project, load_project
from zddv.storage import list_uvm_item_handshake_snapshots, record_run
from zddv.uvm_item_instrumentation import (
    analyze_uvm_item_instrumentation_log,
    parse_uvm_item_instrumentation_log,
    write_uvm_item_instrumentation,
)


def _line(
    event: str,
    item_id: str,
    *,
    sequence_id: str = "seq-1",
    sequence: str = "axi_seq",
    sequencer: str = "uvm_test_top.env.seqr",
    item: str = "axi_item",
    transaction_id: str = "7",
    time: str = "10",
) -> str:
    return (
        "ZDDV_UVM_ITEM_V1|"
        f"{event}|{item_id}|{sequence_id}|{sequence}|{sequencer}|"
        f"{item}|{transaction_id}|{time}"
    )


def test_parses_instrumentation_records_from_decorated_log():
    text = "\n".join(
        [
            "simulator banner",
            "# " + _line("GRANT", "item-1", time="10"),
            "UVM_INFO x.sv(1) @ 10: reporter [TRACE] "
            + _line("REQUEST", "item-1", time="10"),
            _line("ITEM_DONE", "item-1", time="20"),
        ]
    )

    payload = parse_uvm_item_instrumentation_log(text, source="unit-log")

    assert payload["source"] == "unit-log"
    assert [event["event"] for event in payload["events"]] == [
        "GRANT",
        "REQUEST",
        "ITEM_DONE",
    ]
    assert payload["events"][0]["metadata"]["log_line"] == 2
    assert payload["events"][1]["transaction_id"] == "7"
    assert payload["events"][2]["time"] == "20"


def test_parses_missing_optional_fields_without_inventing_context():
    payload = parse_uvm_item_instrumentation_log(
        _line(
            "GRANT",
            "item-unknown",
            sequence_id="-",
            sequence="-",
            sequencer="-",
            item="-",
            transaction_id="-",
        )
    )

    event = payload["events"][0]
    assert event["sequence_id"] is None
    assert event["sequencer"] is None
    assert event["transaction_id"] is None


def test_rejects_malformed_instrumentation_record():
    with pytest.raises(ValueError, match="expected 8 fields"):
        parse_uvm_item_instrumentation_log(
            "ZDDV_UVM_ITEM_V1|GRANT|item-1|seq-1"
        )


def test_generator_writes_portable_package_and_adds_it_first(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    project.tb = ["tb/*.sv"]
    from zddv.config import save_project

    save_project(project)

    result = write_uvm_item_instrumentation(project)

    generated = Path(result["path"])
    assert generated.is_file()
    text = generated.read_text(encoding="utf-8")
    assert "package zddv_uvm_item_trace_pkg;" in text
    assert "ZDDV_UVM_ITEM_V1|" in text
    assert "`define ZDDV_UVM_ITEM_GRANT" in text

    loaded = load_project(project.root)
    assert loaded.tb[0] == "tb/zddv_uvm_item_trace_pkg.sv"
    assert loaded.tb[1] == "tb/*.sv"

    with pytest.raises(FileExistsError):
        write_uvm_item_instrumentation(loaded)


def test_analyzes_instrumented_run_log_and_persists_snapshot(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    run_id = "run-instrumented"
    run_dir = project.root / ".zddv" / "runs" / run_id
    run_dir.mkdir(parents=True)
    log_path = run_dir / "simulation.log"
    log_path.write_text(
        "\n".join(
            [
                _line("GRANT", "item-1", time="10"),
                _line("REQUEST", "item-1", time="10"),
                _line("ITEM_DONE", "item-1", time="20"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    record_run(
        project,
        {
            "run_id": run_id,
            "created_at": "2026-09-22T10:00:00+00:00",
            "project": project.name,
            "simulator": "questa",
            "simulator_version": "Questa test",
            "top": "tb_top",
            "test": "instrumented_case",
            "seed": 31,
            "status": "PASS",
            "returncode": 0,
            "duration_ms": 1.0,
            "run_dir": str(run_dir),
            "log": str(log_path),
            "waveform": None,
            "coverage": None,
            "timeout_s": 10.0,
            "command": ["vsim"],
            "plusargs": [],
        },
    )

    result = analyze_uvm_item_instrumentation_log(
        project,
        None,
        run_id=run_id,
        source="questa-zddv-item",
    )

    assert result["status"] == "PASS"
    assert result["run_id"] == run_id
    assert result["summary"]["completed"] == 1
    assert result["events"][0]["metadata"]["instrumentation"] == "ZDDV_UVM_ITEM_V1"

    rows = list_uvm_item_handshake_snapshots(
        project,
        limit=10,
        run_id=run_id,
    )
    assert len(rows) == 1
    assert rows[0]["completed_count"] == 1


def test_cli_generates_and_analyzes_instrumentation_log(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")

    rc = main(
        [
            "--project",
            str(project.root),
            "uvm-item-instrument",
        ]
    )
    assert rc == 0
    generated_output = capsys.readouterr().out
    assert "ZDDV_UVM_ITEM_V1" in generated_output

    log_path = project.root / "instrumented.log"
    log_path.write_text(
        "\n".join(
            [
                _line("GRANT", "item-cli", time="1"),
                _line("REQUEST", "item-cli", time="1"),
                _line("ITEM_DONE", "item-cli", time="5"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    rc = main(
        [
            "--project",
            str(project.root),
            "uvm-item-log-analyze",
            str(log_path),
            "--source",
            "cli-instrumented",
        ]
    )
    assert rc == 0
    analyzed_output = capsys.readouterr().out
    assert "UVM ITEM LOG PASS" in analyzed_output
    assert "completed=1" in analyzed_output
