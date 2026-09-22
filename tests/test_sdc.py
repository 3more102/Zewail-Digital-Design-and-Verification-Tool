from __future__ import annotations

import json
from pathlib import Path

import pytest

from zddv.config import initialize_project
from zddv.constraints.sdc import (
    SUPPORTED_SDC_COMMANDS,
    SdcParseError,
    analyze_sdc_file,
    lint_sdc,
    parse_sdc_text,
    sdc_document_to_dict,
)


def test_parse_opensta_style_clock_and_io_constraints():
    document = parse_sdc_text(
        """
        # basic timing intent
        create_clock -name core_clock -period 0.4600 [get_ports {clk}]
        set_input_delay 0.0920 -clock [get_clocks {core_clock}] \
            -add_delay [get_ports {req_msg[0] req_val reset}]
        set_output_delay 0.0920 -clock [get_clocks {core_clock}] \
            -add_delay [get_ports {req_rdy resp_msg[0]}]
        """
    )

    assert [item.name for item in document.commands] == [
        "create_clock",
        "set_input_delay",
        "set_output_delay",
    ]
    clock = document.commands[0]
    assert clock.option_value("-name") == "core_clock"
    assert clock.option_value("-period") == "0.4600"
    assert clock.queries[0].command == "get_ports"
    assert clock.queries[0].arguments == ("clk",)

    input_delay = document.commands[1]
    assert input_delay.positionals == (
        "0.0920",
        "[get_ports {req_msg[0] req_val reset}]",
    )
    assert input_delay.has_option("-add_delay")
    assert [query.command for query in input_delay.queries] == [
        "get_clocks",
        "get_ports",
    ]
    assert input_delay.queries[1].arguments == (
        "req_msg[0]",
        "req_val",
        "reset",
    )


def test_parse_semicolon_comments_and_clock_groups():
    document = parse_sdc_text(
        """
        create_clock -period 10 -name clk_a [get_ports clk_a]; create_clock -period 8 -name clk_b [get_ports clk_b]
        set_clock_groups -asynchronous \
          -group [get_clocks {clk_a}] \
          -group [get_clocks {clk_b}] # independent domains
        """
    )

    assert len(document.commands) == 3
    groups = document.commands[2]
    assert groups.name == "set_clock_groups"
    assert groups.has_option("-asynchronous")
    assert groups.option_values("-group") == (
        "[get_clocks {clk_a}]",
        "[get_clocks {clk_b}]",
    )
    assert [query.arguments for query in groups.queries] == [
        ("clk_a",),
        ("clk_b",),
    ]


@pytest.mark.parametrize(
    ("line", "name"),
    [
        ("create_clock -period 10 [get_ports clk]", "create_clock"),
        (
            "create_generated_clock -name g -source [get_ports clk] -divide_by 2 [get_pins u/q]",
            "create_generated_clock",
        ),
        ("set_clock_uncertainty 0.1 [get_clocks clk]", "set_clock_uncertainty"),
        ("set_clock_latency -source 0.2 [get_clocks clk]", "set_clock_latency"),
        ("set_input_delay 1 -clock clk [get_ports in]", "set_input_delay"),
        ("set_output_delay 1 -clock clk [get_ports out]", "set_output_delay"),
        ("set_false_path -from [get_ports rst]", "set_false_path"),
        (
            "set_multicycle_path 2 -setup -from [get_clocks a] -to [get_clocks b]",
            "set_multicycle_path",
        ),
        ("set_max_delay 4 -from [get_ports a] -to [get_ports b]", "set_max_delay"),
        ("set_min_delay 1 -from [get_ports a] -to [get_ports b]", "set_min_delay"),
        (
            "set_clock_groups -asynchronous -group [get_clocks a] -group [get_clocks b]",
            "set_clock_groups",
        ),
        ("set_input_transition 0.2 [get_ports in]", "set_input_transition"),
        ("set_load 0.05 [get_ports out]", "set_load"),
        (
            "set_driving_cell -lib_cell BUFX2 -pin Y [get_ports in]",
            "set_driving_cell",
        ),
    ],
)
def test_supported_command_surface(line: str, name: str):
    command = parse_sdc_text(line).commands[0]
    assert command.name == name
    assert command.supported is True
    assert name in SUPPORTED_SDC_COMMANDS


def test_unknown_commands_are_preserved_and_linted():
    document = parse_sdc_text(
        """
        create_clock -period 10 [get_ports clk]
        set_case_analysis 0 [get_ports test_mode]
        """
    )
    issues = lint_sdc(document)

    assert document.commands[1].supported is False
    assert any(item.code == "UNSUPPORTED_COMMAND" for item in issues)


@pytest.mark.parametrize(
    "text",
    [
        "create_clock -period 10 [get_ports clk",
        "create_clock -period 10 {clk",
        'create_clock -name "clk -period 10 [get_ports clk]',
    ],
)
def test_unbalanced_tcl_grouping_is_rejected(text: str):
    with pytest.raises(SdcParseError):
        parse_sdc_text(text)


def test_lint_detects_duplicate_and_invalid_clocks():
    document = parse_sdc_text(
        """
        create_clock -name clk -period 0 [get_ports clk]
        create_clock -name clk -period 10 [get_ports clk2]
        create_generated_clock -name gclk -divide_by 0 [get_pins u/q]
        """
    )

    codes = {item.code for item in lint_sdc(document)}
    assert "INVALID_CLOCK_PERIOD" in codes
    assert "DUPLICATE_CLOCK" in codes
    assert "MISSING_GENERATED_CLOCK_SOURCE" in codes
    assert "INVALID_GENERATED_CLOCK_SCALE" in codes


def test_lint_detects_conflicting_generated_clock_scale():
    document = parse_sdc_text(
        """
        create_clock -name clk -period 10 [get_ports clk]
        create_generated_clock -name gclk -source [get_ports clk] \
          -divide_by 2 -multiply_by 3 [get_pins u/q]
        """
    )

    codes = {item.code for item in lint_sdc(document)}
    assert "CONFLICTING_GENERATED_CLOCK_SCALE" in codes


@pytest.mark.parametrize("count", ["0", "-1", "abc"])
def test_lint_requires_positive_multicycle_count(count: str):
    document = parse_sdc_text(
        f"""
        create_clock -period 10 [get_ports clk]
        set_multicycle_path {count} -setup -from [get_clocks clk] -to [get_clocks clk]
        """
    )

    assert any(
        item.code == "INVALID_MULTICYCLE_COUNT"
        for item in lint_sdc(document)
    )


def test_lint_optional_known_port_resolution_handles_exact_bus_names():
    document = parse_sdc_text(
        """
        create_clock -period 10 [get_ports clk]
        set_input_delay 1 -clock clk [get_ports {req_msg[0] missing}]
        """
    )
    issues = lint_sdc(
        document,
        known_ports={"clk", "req_msg[0]", "req_msg[1]"},
    )

    unresolved = [
        item.message
        for item in issues
        if item.code == "UNRESOLVED_PORT_REFERENCE"
    ]
    assert unresolved == ["get_ports pattern 'missing' matches no known port"]


def test_no_clock_is_warning_not_timing_signoff():
    document = parse_sdc_text("set_load 0.1 [get_ports out]")
    report = sdc_document_to_dict(document, issues=lint_sdc(document))

    assert report["scope"] == "static-sdc-parse-and-lint"
    assert report["status"] == "LINT_WARNINGS"
    assert any(item["code"] == "NO_CLOCKS" for item in report["issues"])


def test_analyze_sdc_file_writes_normalized_json(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    sdc = project.root / "constraints.sdc"
    sdc.write_text(
        """
        create_clock -name clk -period 10 [get_ports clk]
        set_input_delay 1 -clock [get_clocks clk] [get_ports in]
        set_output_delay 1 -clock [get_clocks clk] [get_ports out]
        """,
        encoding="utf-8",
    )

    result = analyze_sdc_file(
        project,
        sdc,
        known_ports={"clk", "in", "out"},
    )

    report_path = Path(result["report_path"])
    assert report_path == project.root / ".zddv" / "constraints" / "sdc.json"
    assert report_path.is_file()
    persisted = json.loads(report_path.read_text(encoding="utf-8"))
    assert persisted["analysis"] == "sdc_constraints"
    assert persisted["scope"] == "static-sdc-parse-and-lint"
    assert persisted["status"] == "LINT_CLEAN"
    assert persisted["summary"]["commands"] == 3
    assert persisted["summary"]["supported_commands"] == 3
