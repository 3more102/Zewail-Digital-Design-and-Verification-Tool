from zddv.assertions import assertion_summary, parse_assertion_events


def test_parse_structured_assertion_markers():
    events = parse_assertion_events(
        """
noise
ZDDV_ASSERT PASS p_req_grant :: request granted
ZDDV_ASSERT FAIL p_timeout :: response exceeded limit
"""
    )
    assert [event["status"] for event in events] == ["PASS", "FAIL"]
    assert events[0]["property_name"] == "p_req_grant"
    assert events[1]["message"] == "response exceeded limit"
    assert assertion_summary(events) == {"total": 2, "passed": 1, "failed": 1}


def test_parse_verilator_assertion_failure():
    events = parse_assertion_events(
        "[25] %Error: tb_counter.sv:41:7: Assertion failed in TOP.tb_counter: count mismatch\n"
    )
    assert len(events) == 1
    event = events[0]
    assert event["status"] == "FAIL"
    assert event["source_file"] == "tb_counter.sv"
    assert event["source_line"] == 41
    assert event["source_column"] == 7
    assert event["scope"] == "TOP.tb_counter"
    assert event["sim_time"] == "25"
    assert event["message"] == "count mismatch"
    assert event["parser"] == "verilator"


def test_ignore_non_assertion_lines():
    assert parse_assertion_events("ZDDV_PASS smoke\nnormal output\n") == []
