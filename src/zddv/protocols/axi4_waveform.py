from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zddv.config import ProjectConfig
from zddv.protocols.axi4 import analyze_axi4_trace
from zddv.vcd import sample_vcd_on_clock
from zddv.waveform import parse_vcd_header, select_waveform_run


_CANONICAL_SIGNALS = (
    "AWVALID", "AWREADY", "AWID", "AWADDR", "AWLEN", "AWSIZE", "AWBURST",
    "AWLOCK", "AWCACHE", "AWPROT", "AWQOS", "AWREGION", "AWUSER",
    "WVALID", "WREADY", "WDATA", "WSTRB", "WLAST", "WUSER",
    "BVALID", "BREADY", "BID", "BRESP", "BUSER",
    "ARVALID", "ARREADY", "ARID", "ARADDR", "ARLEN", "ARSIZE", "ARBURST",
    "ARLOCK", "ARCACHE", "ARPROT", "ARQOS", "ARREGION", "ARUSER",
    "RVALID", "RREADY", "RID", "RDATA", "RRESP", "RLAST", "RUSER",
)

_REQUIRED_SIGNALS = {
    "AWVALID", "AWREADY", "AWADDR", "AWLEN", "AWSIZE", "AWBURST",
    "WVALID", "WREADY", "WDATA", "WSTRB", "WLAST",
    "BVALID", "BREADY", "BRESP",
    "ARVALID", "ARREADY", "ARADDR", "ARLEN", "ARSIZE", "ARBURST",
    "RVALID", "RREADY", "RDATA", "RRESP", "RLAST",
}


def _scope_signal_names(header: dict[str, Any], scope: str) -> dict[str, str]:
    return {
        str(item["name"]).upper(): str(item["name"])
        for item in header.get("signals", [])
        if item.get("scope") == scope
    }


def _resolve_axi4_scope(
    path: str | Path,
    *,
    scope: str | None = None,
    clock: str = "ACLK",
) -> tuple[str, dict[str, str]]:
    header = parse_vcd_header(path)
    required = {clock.upper(), *_REQUIRED_SIGNALS}

    if scope is not None:
        names = _scope_signal_names(header, scope)
        missing = sorted(required - set(names))
        if missing:
            raise RuntimeError(
                f"VCD scope '{scope}' is not a complete AXI4 scope; missing: "
                + ", ".join(missing)
            )
        return scope, names

    candidates: list[tuple[str, dict[str, str]]] = []
    for item in header.get("scopes", []):
        candidate = str(item["path"])
        names = _scope_signal_names(header, candidate)
        if required.issubset(names):
            candidates.append((candidate, names))

    if not candidates:
        raise RuntimeError(
            "No AXI4 waveform scope was found. Expected one scope containing "
            f"{clock}, all five VALID/READY channel pairs, and required burst payloads; "
            "use --scope when needed."
        )
    if len(candidates) > 1:
        choices = ", ".join(candidate for candidate, _ in candidates)
        raise RuntimeError(
            "Multiple AXI4 waveform scopes were found; select one with --scope. "
            f"Candidates: {choices}"
        )
    return candidates[0]


def extract_axi4_trace_from_vcd(
    path: str | Path,
    *,
    scope: str | None = None,
    clock: str = "ACLK",
    edge: str = "rising",
) -> dict[str, Any]:
    source = Path(path).resolve()
    resolved_scope, available = _resolve_axi4_scope(
        source,
        scope=scope,
        clock=clock,
    )

    header = parse_vcd_header(source)
    scoped_signals = {
        str(item["name"]).upper(): item
        for item in header.get("signals", [])
        if item.get("scope") == resolved_scope
    }
    wdata_width = int(scoped_signals["WDATA"]["width"])
    rdata_width = int(scoped_signals["RDATA"]["width"])
    wstrb_width = int(scoped_signals["WSTRB"]["width"])
    legal_data_widths = {8, 16, 32, 64, 128, 256, 512, 1024}
    if wdata_width not in legal_data_widths:
        raise RuntimeError(
            "AXI4 data width must be one of "
            "8, 16, 32, 64, 128, 256, 512, or 1024 bits; "
            f"got WDATA={wdata_width}"
        )
    if rdata_width != wdata_width:
        raise RuntimeError(
            "AXI4 WDATA and RDATA widths must match; "
            f"got WDATA={wdata_width}, RDATA={rdata_width}"
        )
    expected_wstrb_width = wdata_width // 8
    if wstrb_width != expected_wstrb_width:
        raise RuntimeError(
            "WSTRB width must equal WDATA width / 8; "
            f"got WDATA={wdata_width}, WSTRB={wstrb_width}"
        )

    user_signal_widths = {
        name: int(scoped_signals[name]["width"])
        for name in ("AWUSER", "WUSER", "BUSER", "ARUSER", "RUSER")
        if name in scoped_signals
    }

    def id_width_property(
        property_name: str,
        request_signal: str,
        response_signal: str,
    ) -> int | None:
        # A VCD declaration is positive evidence that a signal is present, but
        # omission is not proof that the physical interface signal is absent.
        # Only promote a protocol width property when both associated signals
        # are declared and therefore provide complete width evidence.
        if request_signal not in scoped_signals or response_signal not in scoped_signals:
            return None

        request_width = int(scoped_signals[request_signal]["width"])
        response_width = int(scoped_signals[response_signal]["width"])
        if request_width != response_width:
            raise RuntimeError(
                f"AXI4 {property_name} requires matching {request_signal}/"
                f"{response_signal} widths; got {request_signal}={request_width}, "
                f"{response_signal}={response_width}"
            )
        if not 1 <= request_width <= 32:
            raise RuntimeError(
                f"AXI4 {property_name} must be in the range 0..32 bits when present; "
                f"got {request_width}"
            )
        return request_width

    id_widths = {
        name: width
        for name, width in (
            ("ID_W_WIDTH", id_width_property("ID_W_WIDTH", "AWID", "BID")),
            ("ID_R_WIDTH", id_width_property("ID_R_WIDTH", "ARID", "RID")),
        )
        if width is not None
    }

    actual_clock = available[clock.upper()]
    actual_to_canonical = {
        available[name]: name
        for name in _CANONICAL_SIGNALS
        if name in available
    }
    required_actual = tuple(available[name] for name in sorted(_REQUIRED_SIGNALS))

    sampled = sample_vcd_on_clock(
        source,
        scope=resolved_scope,
        clock=actual_clock,
        signals=actual_to_canonical,
        edge=edge,
        required=required_actual,
    )

    normalized_samples: list[dict[str, Any]] = []
    for raw in sampled["samples"]:
        sample: dict[str, Any] = {
            "cycle": raw["cycle"],
            "time": raw["time"],
        }
        for actual, canonical in actual_to_canonical.items():
            if actual in raw:
                sample[canonical] = raw[actual]
        normalized_samples.append(sample)

    waveform = dict(sampled["waveform"])
    waveform["clock"] = actual_clock
    waveform["data_width_bits"] = wdata_width
    waveform["rdata_width_bits"] = rdata_width
    waveform["wstrb_width"] = wstrb_width
    waveform["id_widths"] = dict(id_widths)
    waveform["user_signal_widths"] = dict(user_signal_widths)
    return {
        "source": "vcd-waveform",
        "data_width_bits": wdata_width,
        "id_widths": id_widths,
        "user_signal_widths": user_signal_widths,
        "waveform": waveform,
        "samples": normalized_samples,
    }


def analyze_axi4_waveform(
    project: ProjectConfig,
    *,
    run_id: str | None = None,
    input_path: str | Path | None = None,
    scope: str | None = None,
    clock: str = "ACLK",
    edge: str = "rising",
    trace_output: str | Path = ".zddv/protocols/axi4/waveform-trace.json",
    output: str | Path = ".zddv/protocols/axi4/waveform-latest.json",
) -> dict[str, Any]:
    if run_id is not None and input_path is not None:
        raise ValueError("run_id and input_path are mutually exclusive")

    selected_run: dict[str, Any] | None = None
    if input_path is None:
        selected_run = select_waveform_run(project, run_id=run_id)
        source = Path(str(selected_run["waveform_path"])).resolve()
    else:
        source = Path(input_path)
        if not source.is_absolute():
            source = project.root / source
        source = source.resolve()

    trace = extract_axi4_trace_from_vcd(
        source,
        scope=scope,
        clock=clock,
        edge=edge,
    )

    trace_path = Path(trace_output)
    if not trace_path.is_absolute():
        trace_path = project.root / trace_path
    trace_path = trace_path.resolve()
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(json.dumps(trace, indent=2) + "\n", encoding="utf-8")

    report = analyze_axi4_trace(trace)
    report["input_path"] = str(source)
    report["trace_path"] = str(trace_path)
    report["run_id"] = (
        str(selected_run["run_id"]) if selected_run is not None else None
    )

    destination = Path(output)
    if not destination.is_absolute():
        destination = project.root / destination
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    report["report_path"] = str(destination)
    return report
