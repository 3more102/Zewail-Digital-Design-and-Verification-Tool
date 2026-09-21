from zddv.diagnostics import parse_verilator_diagnostics


def test_parse_verilator_diagnostics():
    text = (
        "%Warning-WIDTH: rtl/a.sv:12:7: Operator expects 8 bits\n"
        "%Error: tb/tb.sv:20:3: syntax error, unexpected end\n"
    )

    rows = parse_verilator_diagnostics(text)

    assert len(rows) == 2
    assert rows[0].severity == "WARNING"
    assert rows[0].code == "WIDTH"
    assert rows[0].line == 12
    assert rows[0].column == 7
    assert rows[1].severity == "ERROR"
    assert rows[1].code is None
