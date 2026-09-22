from __future__ import annotations

import argparse
from pathlib import Path
import platform
import sys

from zddv import __version__
from zddv.cdc import analyze_async_fifo_file
from zddv.config import initialize_project, load_project, save_project
from zddv.crossprobe import write_crossprobe_report
from zddv.connectivity import signal_navigation, write_connectivity_index
from zddv.coverage import (
    merge_coverage,
    parse_questa_code_coverage_report,
    parse_xcelium_imc_block_coverage,
    parse_xcelium_imc_expression_coverage,
    parse_xcelium_imc_toggle_coverage_points,
    parse_vcs_urg_condition_coverage_points,
    parse_verilator_coverage,
    write_coverage_hole_report,
    write_questa_statement_hole_report,
)
from zddv.dashboard import generate_html_report
from zddv.design_index import hierarchy_lines, write_design_index
from zddv.debug import write_assertion_waveform_report
from zddv.functional_coverage import ingest_functional_coverage
from zddv.formal import (
    FormalCheckRequest,
    SymbiYosysBackend,
    write_formal_cover_coverage_report,
)
from zddv.formal.counterexample import ingest_formal_counterexample
from zddv.formal.results import analyze_formal_result_file, persist_formal_result
from zddv.formal.sby_results import analyze_sby_log
from zddv.lint import lint_project
from zddv.protocols.apb import analyze_apb_file, analyze_apb_waveform
from zddv.protocols.axi4lite import analyze_axi4lite_file, analyze_axi4lite_waveform
from zddv.protocols.axi4 import analyze_axi4_file
from zddv.protocols.axi4_waveform import analyze_axi4_waveform
from zddv.protocols.ucie import analyze_ucie_file
from zddv.regression import run_regression
from zddv.reporting import write_junit_report
from zddv.simulator import get_backend
from zddv.storage import (
    assertion_statistics,
    database_path,
    list_assertion_events,
    list_coverage_score_snapshots,
    list_coverage_snapshots,
    list_functional_coverage_bins,
    list_functional_coverage_snapshots,
    list_formal_result_snapshots,
    list_run_records,
    list_runs,
    list_uvm_arbitration_snapshots,
    list_uvm_item_handshake_snapshots,
    list_uvm_item_handshake_violations,
    list_uvm_log_snapshots,
    list_uvm_sequence_lifecycle_snapshots,
)
from zddv.triage import group_failure_records, write_failure_report
from zddv.uvm import analyze_uvm_log
from zddv.uvm_arbitration import analyze_uvm_arbitration_file, analyze_uvm_arbitration_log
from zddv.uvm_item import analyze_uvm_item_file, analyze_uvm_item_log
from zddv.uvm_marker import analyze_uvm_marker_log
from zddv.uvm_item_instrumentation import write_uvm_item_instrumentation
from zddv.uvm_sequence import analyze_uvm_sequence_file, analyze_uvm_sequence_log
from zddv.uvm_sequence_instrumentation import write_uvm_sequence_instrumentation
from zddv.waveform import write_waveform_index
from zddv.waveform_probe import write_waveform_probe


def _backend(name: str):
    return get_backend(name)


def _project_arg(args) -> str:
    return getattr(args, "project", ".") or "."


def cmd_init(args) -> int:
    config = initialize_project(args.path)
    print(f"Initialized ZDDV project: {config.root}")
    print(f"Config: {config.config_path}")
    return 0


def cmd_add(args) -> int:
    config = load_project(_project_arg(args))
    target = config.rtl if args.kind == "rtl" else config.tb
    for pattern in args.patterns:
        if pattern not in target:
            target.append(pattern)
    save_project(config)
    print(f"Added {len(args.patterns)} {args.kind} source pattern(s).")
    return 0


def cmd_config(args) -> int:
    config = load_project(_project_arg(args))
    if args.key == "simulator":
        config.simulator = args.value
    elif args.key == "top":
        config.top = args.value
    else:
        raise RuntimeError(f"Unsupported configuration key: {args.key}")
    save_project(config)
    print(f"{args.key} = {args.value}")
    return 0


def cmd_doctor(args) -> int:
    print(f"ZDDV {__version__}")
    print(f"Python {platform.python_version()} ({sys.executable})")
    backend_name = getattr(args, "simulator", None) or "verilator"
    try:
        backend = _backend(backend_name)
        version = backend.version()
        print(f"[PASS] {version}")
        return 0
    except RuntimeError as exc:
        print(f"[FAIL] {exc}")
        return 1


def cmd_index(args) -> int:
    project = load_project(_project_arg(args))
    result = write_design_index(project)
    summary = result["summary"]
    print(
        f"DESIGN INDEX: {summary['files']} file(s), {summary['units']} unit(s), "
        f"{summary['instances']} instance(s)"
    )
    if summary["duplicate_unit_names"]:
        print(f"Duplicate unit names: {summary['duplicate_unit_names']}")
    print(f"Index: {result['path']}")
    return 0


def cmd_hierarchy(args) -> int:
    project = load_project(_project_arg(args))
    result = write_design_index(project)
    print(f"HIERARCHY: top={project.top}")
    for line in hierarchy_lines(result["hierarchy"]):
        print(line)
    print(f"Index: {result['path']}")
    return 0 if result["hierarchy"].get("resolved", False) else 1



def cmd_connectivity(args) -> int:
    project = load_project(_project_arg(args))
    result = write_connectivity_index(project, output=args.output)
    summary = result["summary"]

    if args.signal is None:
        print(
            f"CONNECTIVITY: {summary['units']} unit(s), "
            f"{summary['signals']} signal(s), "
            f"{summary['drivers']} driver edge(s), "
            f"{summary['loads']} load edge(s)"
        )
        print(
            "Analysis: source-level structural "
            f"({summary['unresolved_instance_connections']} unresolved instance connection(s))"
        )
        print(f"Index: {result['path']}")
        return 0

    unit = args.unit or project.top
    nav = signal_navigation(result, unit=unit, signal=args.signal)
    print(
        f"SIGNAL: {nav['unit']}.{nav['signal']}  "
        f"drivers={len(nav['drivers'])} loads={len(nav['loads'])}"
    )
    for label, entries in (("DRIVER", nav["drivers"]), ("LOAD", nav["loads"])):
        if not entries:
            print(f"{label}: none found by source-level analysis")
            continue
        for item in entries:
            print(
                f"{label} [{item['kind']}] "
                f"{item['file']}:{item['line']} {item['detail']}"
            )
    print(f"Index: {result['path']}")
    return 0

def cmd_waveform_index(args) -> int:
    project = load_project(_project_arg(args))
    result = write_waveform_index(
        project,
        run_id=args.run_id,
        input_path=args.input,
        output=args.output,
    )
    summary = result["summary"]
    print(
        f"WAVEFORM INDEX: {result['format'].upper()} "
        f"{summary['scopes']} scope(s), {summary['signals']} signal(s), "
        f"{summary['declared_bits']} declared bit(s)"
    )
    print(f"Parse status: {result['parse_status']}")
    if result.get("run_id"):
        print(f"Run: {result['run_id']}")
    print(f"Waveform: {result['artifact']['path']}")
    if result.get("timescale"):
        print(f"Timescale: {result['timescale']}")
    if result.get("note"):
        print(f"Note: {result['note']}")
    print(f"Index: {result['path']}")
    if result.get("latest_path"):
        print(f"Latest: {result['latest_path']}")
    return 0



def cmd_waveform_probe(args) -> int:
    project = load_project(_project_arg(args))
    result = write_waveform_probe(
        project,
        args.signals,
        run_id=args.run_id,
        input_path=args.input,
        start_time=args.start,
        end_time=args.end,
        max_changes=args.max_changes,
        output=args.output,
    )
    summary = result["summary"]
    print(
        f"WAVEFORM PROBE: {summary['signals']} signal(s), "
        f"{summary['total_changes']} change(s)"
    )
    if result.get("run_id"):
        print(f"Run: {result['run_id']}")
    if result.get("timescale"):
        print(f"Timescale: {result['timescale']}")
    for signal in result["signals"]:
        marker = " [TRUNCATED]" if signal["truncated"] else ""
        print(f"{signal['path']}: {len(signal['changes'])} change(s){marker}")
    print(f"Probe: {result['path']}")
    return 0


def cmd_crossprobe(args) -> int:
    project = load_project(_project_arg(args))
    result = write_crossprobe_report(
        project,
        args.signal,
        run_id=args.run_id,
        input_path=args.input,
        output=args.output,
    )
    signal = result["waveform"]["signal"]
    print(f"CROSSPROBE {result['status']}: {signal['path']}")
    if result.get("hierarchy"):
        hierarchy = result["hierarchy"]
        print(
            f"Hierarchy: {hierarchy['waveform_scope']} -> "
            f"{hierarchy['design_path']} ({hierarchy['type']})"
        )
    source = result.get("source")
    if source and source.get("declaration"):
        declaration = source["declaration"]
        print(f"Source: {declaration['file']}:{declaration['line']}")
        print(f"Declaration: {declaration['text']}")
    elif source:
        print(f"Source unit: {source['file']}:{source['unit_line']}")
    connectivity = result.get("connectivity")
    if connectivity is not None:
        print(
            f"Connectivity: drivers={len(connectivity['drivers'])} "
            f"loads={len(connectivity['loads'])}"
        )
    if result.get("note"):
        print(f"Note: {result['note']}")
    print(f"Report: {result['report_path']}")
    return 0


def cmd_assertion_waveform(args) -> int:
    project = load_project(_project_arg(args))
    result = write_assertion_waveform_report(
        project,
        run_id=args.run_id,
        status=args.status,
        assertion_name=args.name,
        limit=args.limit,
        signal_hint_limit=args.signal_limit,
        output=args.output,
    )
    summary = result["summary"]
    print(
        f"ASSERTION/WAVEFORM: {summary['events']} event(s), "
        f"{summary['with_waveform']} with waveform, "
        f"{summary['with_indexed_waveform']} fully indexed, "
        f"{summary['with_signal_hints']} with signal hint(s)"
    )
    for event in result["events"][: args.show]:
        waveform = event["waveform"]
        wave_label = "no-waveform"
        hints = ""
        if waveform is not None:
            wave_label = f"{waveform['format']}:{waveform['parse_status']}"
            paths = [item["path"] for item in waveform["signal_hints"]]
            if paths:
                hints = " signals=" + ",".join(paths)
        print(
            f"[{event['status']}] {event['assertion_name']} "
            f"run={event['run_id']} {wave_label}{hints}"
        )
    if len(result["events"]) > args.show:
        print(f"... {len(result['events']) - args.show} more event(s)")
    print(f"Report: {result['path']}")
    return 0


def cmd_lint(args) -> int:
    project = load_project(_project_arg(args))
    if project.simulator != "verilator":
        raise RuntimeError("Lint is currently implemented with Verilator only.")

    result = lint_project(project)
    print(
        f"LINT {result['status']}: "
        f"{result['errors']} error(s), {result['warnings']} warning(s)"
    )
    print(f"Log: {result['log']}")
    print(f"Summary: {result['summary']}")
    return 0 if result["status"] == "PASS" else 1


def cmd_build(args) -> int:
    project = load_project(_project_arg(args))
    backend = _backend(project.simulator)
    print(f"Simulator: {backend.version()}")
    print(f"Top: {project.top}")
    sources = project.source_files()
    print(f"Sources: {len(sources)}")
    result = backend.build(project)
    if result.passed:
        print(f"BUILD PASS: {result.executable or result.artifact}")
        return 0
    print(f"BUILD FAIL: {result.log_path}")
    try:
        diagnostics = result.log_path.read_text(encoding="utf-8").strip()
        if diagnostics:
            print("--- simulator diagnostics ---")
            print(diagnostics)
            print("--- end diagnostics ---")
    except OSError:
        pass
    return result.returncode or 1


def cmd_run(args) -> int:
    project = load_project(_project_arg(args))
    backend = _backend(project.simulator)
    result = backend.run(
        project,
        test_name=args.test,
        seed=args.seed,
        plusargs=args.plusarg,
        timeout_s=args.timeout,
    )
    print(f"RUN {result.status}: {result.run_id}")
    print(f"Log: {result.log_path}")
    if result.waveform_path:
        print(f"Waveform: {result.waveform_path}")
    if result.coverage_path:
        print(f"Coverage: {result.coverage_path}")
    return result.returncode


def cmd_regress(args) -> int:
    project = load_project(_project_arg(args))
    backend = _backend(project.simulator)
    summary = run_regression(project, backend, args.regression_file)
    print(
        f"REGRESSION {summary['status']}: "
        f"{summary['passed']}/{summary['total']} passed"
    )
    print(f"Summary: {summary['summary_path']}")
    return 0 if summary["status"] == "PASS" else 1




def cmd_formal_bmc(args) -> int:
    project = load_project(_project_arg(args))
    request = FormalCheckRequest(
        mode="bmc",
        depth=args.depth,
        timeout_s=args.timeout,
    )
    backend = SymbiYosysBackend()
    print(f"Formal backend: {backend.version()}")
    result = backend.check(project, request)
    record = persist_formal_result(
        project,
        result,
        input_path=result.log_path,
        output=result.run_dir / "zddv-formal-result.json",
    )
    print(
        f"FORMAL BMC {result.status}: depth={request.depth} "
        f"engine={result.engine or '-'}"
    )
    print("Scope: BOUNDED (finite-depth evidence; not an unbounded proof)")
    print(f"Run directory: {result.run_dir}")
    print(f"Log: {result.log_path}")
    print(f"Snapshot: {record['snapshot_id']}")
    print(f"Report: {record['report_path']}")
    return 0 if result.status == "PASS" else 1


def cmd_formal_cover(args) -> int:
    project = load_project(_project_arg(args))
    request = FormalCheckRequest(
        mode="cover",
        depth=args.depth,
        timeout_s=args.timeout,
    )
    backend = SymbiYosysBackend()
    print(f"Formal backend: {backend.version()}")
    result = backend.check(project, request)
    record = persist_formal_result(
        project,
        result,
        input_path=result.log_path,
        output=result.run_dir / "zddv-formal-result.json",
    )
    summary = record["summary"]
    print(
        f"FORMAL COVER {result.status}: depth={request.depth} "
        f"engine={result.engine or '-'}"
    )
    print(
        f"Coverage: covered={summary['covered_goals']} "
        f"unreached={summary['unreached_goals']}"
    )
    print("Scope: COVER (finite-depth reachability evidence)")
    print(f"Run directory: {result.run_dir}")
    print(f"Log: {result.log_path}")
    print(f"Snapshot: {record['snapshot_id']}")
    print(f"Report: {record['report_path']}")
    return 0 if result.status == "PASS" else 1


def cmd_formal_counterexample(args) -> int:
    project = load_project(_project_arg(args))
    result = ingest_formal_counterexample(
        project,
        args.path,
        source=args.source,
        output=args.output,
    )
    summary = result["summary"]
    print(
        f"FORMAL TRACE: {result['trace_kind']} "
        f"property={result['property']} kind={result['property_kind']} "
        f"source={result['source']}"
    )
    print(
        f"Signals/Steps: {summary['signals']}/{summary['steps']}  "
        f"complete/partial={summary['complete_signal_steps']}/"
        f"{summary['partial_signal_steps']}"
    )
    if summary["first_cycle"] is not None or summary["last_cycle"] is not None:
        print(f"Cycles: {summary['first_cycle']}..{summary['last_cycle']}")
    if summary["first_time"] is not None or summary["last_time"] is not None:
        unit = result.get("time_unit") or "(unspecified)"
        print(f"Time: {summary['first_time']}..{summary['last_time']} {unit}")
    print(f"Input SHA-256: {result['input_sha256']}")
    print(f"Normalized trace: {result['normalized_path']}")
    return 0


def cmd_formal_sby_analyze(args) -> int:
    project = load_project(_project_arg(args))
    result = analyze_sby_log(
        project,
        args.path,
        mode=args.mode,
        depth=args.depth,
        output=args.output,
    )
    summary = result["summary"]
    request = result["request"]
    print(
        f"FORMAL SBY {result['status']}: mode={request['mode']} "
        f"scope={request['scope']} properties={summary['properties']}"
    )
    print(
        f"counterexamples={summary['counterexamples']}  "
        f"covered={summary['covered_goals']}"
    )
    print(f"Snapshot: {result['snapshot_id']}")
    print(f"Report: {result['report_path']}")
    return 0 if result["status"] == "PASS" else 1


def cmd_formal_analyze(args) -> int:
    project = load_project(_project_arg(args))
    result = analyze_formal_result_file(
        project,
        args.path,
        output=args.output,
    )
    summary = result["summary"]
    request = result["request"]
    print(
        f"FORMAL {result['status']}: backend={result['backend']} "
        f"mode={request['mode']} scope={request['scope']} "
        f"properties={summary['properties']}"
    )
    print(
        f"Assertions/Covers: {summary['assertions']}/{summary['covers']}  "
        f"counterexamples={summary['counterexamples']}  "
        f"bounded-safe={summary['bounded_safe_assertions']}  "
        f"proved={summary['proved_assertions']}"
    )
    print(
        f"Covered/Unreached goals: "
        f"{summary['covered_goals']}/{summary['unreached_goals']}"
    )
    print(f"Snapshot: {result['snapshot_id']}")
    print(f"Report: {result['report_path']}")
    return 0 if result["status"] == "PASS" else 1


def cmd_formal_history(args) -> int:
    project = load_project(_project_arg(args))
    rows = list_formal_result_snapshots(
        project,
        limit=args.limit,
        status=args.status,
        mode=args.mode,
    )
    if not rows:
        print("No formal result snapshots found.")
        return 0

    print(
        f"{'STATUS':<7} {'MODE':<6} {'SCOPE':<9} {'PROP':>4} "
        f"{'CEX':>3} {'BSAFE':>5} {'PROVED':>6} {'BACKEND':<16} SNAPSHOT"
    )
    for row in rows:
        print(
            f"{row['status']:<7} {row['mode']:<6} {row['proof_scope']:<9} "
            f"{row['property_count']:>4} {row['counterexample_count']:>3} "
            f"{row['bounded_safe_count']:>5} {row['proved_count']:>6} "
            f"{row['backend'][:16]:<16} {row['snapshot_id']}"
        )
    return 0


def cmd_formal_coverage(args) -> int:
    project = load_project(_project_arg(args))
    result = write_formal_cover_coverage_report(
        project,
        limit=args.limit,
        backend=args.backend,
        depth=args.depth,
        output=args.output,
    )

    groups = result["groups"]
    print(
        "FORMAL COVERAGE: "
        f"groups={len(groups)} "
        f"eligible={result['eligible_snapshots']} "
        f"considered={result['considered_snapshots']}"
    )
    if result["excluded_snapshots"]:
        detail = ", ".join(
            f"{name}={count}"
            for name, count in sorted(result["excluded_snapshots"].items())
        )
        print(f"Excluded: {detail}")

    if not groups:
        print("No comparable complete cover snapshots found.")
    else:
        for group in groups:
            unresolved = group["unknown_goals"] + group["error_goals"]
            print(
                f"{group['configuration_id']} "
                f"backend={group['backend']} "
                f"engine={group['engine'] or '-'} "
                f"depth={group['depth']} "
                f"design={group['design_fingerprint'][:12]} "
                f"snapshots={group['snapshot_count']} "
                f"covered={group['covered_goals']}/{group['property_count']} "
                f"({group['coverage_rate']:.2f}%) "
                f"unreached={group['unreached_goals']} "
                f"unresolved={unresolved}"
            )
    print(f"Report: {result['report_path']}")
    return 0


def cmd_coverage(args) -> int:
    project = load_project(_project_arg(args))
    result = merge_coverage(project)
    print(f"Coverage inputs: {len(result['inputs'])}")
    print(f"Merged coverage: {result['merged']}")
    print(f"Summary: {result['summary']}")
    metrics = result.get("metrics")
    if metrics is not None:
        if {"hit_points", "total_points", "hit_rate"} <= metrics.keys():
            print(
                f"Coverage points: {metrics['hit_points']}/{metrics['total_points']} hit "
                f"({metrics['hit_rate']:.1f}%)"
            )
            if metrics.get("tool_total_coverage") is not None:
                print(
                    "Simulator-reported total coverage: "
                    f"{metrics['tool_total_coverage']:.2f}%"
                )
        elif metrics.get("tool_total_coverage") is not None:
            print(
                "Simulator-reported total coverage: "
                f"{metrics['tool_total_coverage']:.2f}%"
            )
            by_metric = metrics.get("by_metric") or {}
            if by_metric:
                detail = ", ".join(
                    f"{name}={value:.2f}%"
                    for name, value in sorted(by_metric.items())
                )
                print(f"Coverage metric scores: {detail}")
            by_metric_counts = metrics.get("by_metric_counts") or {}
            if by_metric_counts:
                count_detail = ", ".join(
                    (
                        f"{name}={values['covered']}/{values['total']}"
                        + (
                            f" ({values['hit_rate']:.2f}%)"
                            if values.get("hit_rate") is not None
                            else ""
                        )
                    )
                    for name, values in sorted(by_metric_counts.items())
                )
                print(f"Coverage object counts: {count_detail}")
        print(f"Metrics: {result['metrics_path']}")
        if result.get("snapshot_id"):
            print(f"Snapshot: {result['snapshot_id']}")
    else:
        print(
            "Coverage metrics: "
            f"{result.get('metrics_status', 'not-normalized')}"
        )
        print(f"Coverage evidence: {result['metrics_path']}")
    if result.get("functional_snapshot_id"):
        print(f"Functional coverage bins: {result.get('functional_bins', 0)}")
        print(f"Functional snapshot: {result['functional_snapshot_id']}")
        if result.get("functional_report"):
            print(f"Functional report: {result['functional_report']}")
    code_detail_status = result.get("code_detail_status")
    if code_detail_status is not None:
        print(
            "Normalized Questa statement/branch/condition/expression/FSM coverage: "
            f"{code_detail_status} "
            f"{result.get('code_detail_points', 0)} point(s), "
            f"{result.get('code_detail_holes', 0)} hole(s)"
        )
        if result.get("code_report"):
            print(f"Questa statement/branch/condition/expression/FSM detail: {result['code_report']}")
    multibit_expression_status = result.get("multibit_expression_status")
    if multibit_expression_status is not None:
        print(
            "Normalized Questa multibit expression coverage: "
            f"{multibit_expression_status} "
            f"{result.get('multibit_expression_points', 0)} point(s), "
            f"{result.get('multibit_expression_holes', 0)} hole(s)"
        )
        if result.get("multibit_expression_report"):
            print(
                "Questa multibit expression normalized detail: "
                f"{result['multibit_expression_report']}"
            )
    toggle_detail_status = result.get("toggle_detail_status")
    if toggle_detail_status is not None:
        print(
            "Xcelium toggle detail: "
            f"{toggle_detail_status} "
            f"{result.get('toggle_detail_points', 0)} point(s), "
            f"{result.get('toggle_detail_holes', 0)} hole(s)"
        )
        if result.get("detail"):
            print(f"Xcelium detail evidence: {result['detail']}")
        if result.get("toggle_detail_error"):
            print(f"Xcelium toggle note: {result['toggle_detail_error']}")
    brief_status = result.get("brief_status")
    if brief_status is not None:
        print(f"VCS uncovered-object evidence: {brief_status}")
        if result.get("brief_report_dir"):
            print(f"URG brief report: {result['brief_report_dir']}")
        if result.get("brief_error"):
            print(f"URG brief report note: {result['brief_error']}")
    detailed = result.get("detailed_code_coverage_evidence") or {}
    if detailed:
        xml = detailed.get("xml", {})
        zero_detail = detailed.get("zero_detail", {})
        print(
            "Detailed code coverage XML: "
            f"{xml.get('status', 'unknown')} {xml.get('path', '-')}"
        )
        print(
            "Zero-hit source detail: "
            f"{zero_detail.get('status', 'unknown')} {zero_detail.get('path', '-')}"
        )
        multibit_expression = detailed.get("multibit_expression", {})
        if multibit_expression:
            print(
                "Questa multibit expression detail: "
                f"{multibit_expression.get('status', 'unknown')} "
                f"{multibit_expression.get('path', '-')}"
            )
        toggle_detail = detailed.get("toggle_detail", {})
        if toggle_detail:
            print(
                "Questa toggle detail: "
                f"{toggle_detail.get('status', 'unknown')} "
                f"{toggle_detail.get('path', '-')}"
            )
        toggle_xml = detailed.get("toggle_xml", {})
        if toggle_xml:
            print(
                "Questa toggle XML: "
                f"{toggle_xml.get('status', 'unknown')} "
                f"{toggle_xml.get('path', '-')}"
            )
    report = result["report"].strip()
    if report:
        print(report)
    return 0


def cmd_coverage_history(args) -> int:
    project = load_project(_project_arg(args))
    if project.simulator.strip().lower() in {"vcs", "xcelium", "xrun"}:
        rows = list_coverage_score_snapshots(project, limit=args.limit)
        if not rows:
            print("No coverage score snapshots found.")
            return 0

        print(f"{'SCORE':>8} {'INPUTS':>6}  SNAPSHOT")
        for row in rows:
            print(
                f"{row['score']:>7.2f}% {row['input_count']:>6}  "
                f"{row['snapshot_id']}"
            )
            by_metric = row.get("by_metric") or {}
            if by_metric:
                detail = ", ".join(
                    f"{name}={value:.2f}%"
                    for name, value in sorted(by_metric.items())
                )
                print(f"         {detail}")
            by_metric_counts = row.get("by_metric_counts") or {}
            if by_metric_counts:
                count_detail = ", ".join(
                    (
                        f"{name}={values['covered']}/{values['total']}"
                        + (
                            f" ({values['hit_rate']:.2f}%)"
                            if values.get("hit_rate") is not None
                            else ""
                        )
                    )
                    for name, values in sorted(by_metric_counts.items())
                )
                print(f"         counts: {count_detail}")
        return 0

    rows = list_coverage_snapshots(project, limit=args.limit)
    if not rows:
        print("No coverage snapshots found.")
        return 0

    print(f"{'HIT RATE':>9} {'HIT/TOTAL':>15} {'INPUTS':>6}  SNAPSHOT")
    for row in rows:
        ratio = f"{row['hit_points']}/{row['total_points']}"
        print(
            f"{row['hit_rate']:>8.1f}% {ratio:>15} "
            f"{row['input_count']:>6}  {row['snapshot_id']}"
        )
    return 0


def cmd_coverage_holes(args) -> int:
    project = load_project(_project_arg(args))
    simulator = project.simulator.strip().lower()

    output = Path(args.output)
    if not output.is_absolute():
        output = project.root / output

    if simulator == "verilator":
        source_path = (
            project.root / ".zddv" / "coverage" / "coverage.dat"
        ).resolve()
        if not source_path.exists():
            raise RuntimeError(
                f"Merged coverage not found at {source_path}. "
                "Run 'zddv coverage' first."
            )
        points = parse_verilator_coverage(source_path)
        if not points:
            raise RuntimeError(
                f"No normalized coverage points found in {source_path}."
            )
        report = write_coverage_hole_report(
            points,
            output,
            point_type=args.point_type,
            limit=args.limit,
        )
    elif simulator in {"questa", "questasim"}:
        if args.point_type not in {
            None,
            "statement",
            "branch",
            "condition",
            "expression",
            "fsm",
        }:
            raise RuntimeError(
                "Questa item-level coverage currently supports "
                "--type statement, branch, condition, expression, or fsm."
            )
        if args.point_type == "statement":
            report = write_questa_statement_hole_report(
                project,
                output,
                limit=args.limit,
            )
        else:
            source_path = (
                project.root / ".zddv" / "coverage" / "code-details.txt"
            ).resolve()
            if not source_path.exists():
                raise RuntimeError(
                    f"Detailed Questa code coverage report not found at {source_path}. "
                    "Run 'zddv coverage' first."
                )
            points = parse_questa_code_coverage_report(
                source_path.read_text(encoding="utf-8", errors="replace")
            )
            if args.point_type in {None, "expression"}:
                multibit_source = (
                    project.root
                    / ".zddv"
                    / "coverage"
                    / "multibit-expression.txt"
                ).resolve()
                if multibit_source.exists():
                    multibit_points = parse_questa_code_coverage_report(
                        multibit_source.read_text(
                            encoding="utf-8",
                            errors="replace",
                        )
                    )
                    points.extend(
                        point
                        for point in multibit_points
                        if point.get("type") == "expression"
                        and point.get("multibit") is True
                    )
            if (
                args.point_type in {"condition", "expression"}
                and not any(
                    point.get("type") == args.point_type
                    for point in points
                )
            ):
                if args.point_type == "expression":
                    raise RuntimeError(
                        "No normalized Questa expression FEC rows found. "
                        "Scalar FEC rows and documented multibit expression "
                        "FEC rows are supported."
                    )
                raise RuntimeError(
                    f"No normalized Questa condition FEC rows found in "
                    f"{source_path}. Scalar condition FEC rows are supported; "
                    "multibit condition FEC rows remain unnormalized."
                )
            if (
                args.point_type == "fsm"
                and not any(point.get("type") == "fsm" for point in points)
            ):
                raise RuntimeError(
                    f"No normalized Questa FSM state/transition rows found in "
                    f"{source_path}."
                )
            if not points:
                raise RuntimeError(
                    f"No normalized coverage points found in {source_path}."
                )
            report = write_coverage_hole_report(
                points,
                output,
                point_type=args.point_type,
                limit=args.limit,
            )
    elif simulator in {"xcelium", "xrun"}:
        supported_types = {"block", "expression", "toggle"}
        if args.point_type is not None and args.point_type not in supported_types:
            raise RuntimeError(
                "Xcelium item-level coverage currently supports "
                "--type block, expression, or toggle."
            )
        source_path = (
            project.root / ".zddv" / "coverage" / "xcelium" / "detail.txt"
        ).resolve()
        if not source_path.exists():
            raise RuntimeError(
                f"Xcelium IMC detail report not found at {source_path}. "
                "Run 'zddv coverage' first."
            )
        detail_text = source_path.read_text(encoding="utf-8", errors="replace")
        parsers = {
            "block": parse_xcelium_imc_block_coverage,
            "expression": parse_xcelium_imc_expression_coverage,
            "toggle": parse_xcelium_imc_toggle_coverage_points,
        }
        if args.point_type is None:
            points = [
                point
                for point_type in ("block", "expression", "toggle")
                for point in parsers[point_type](detail_text)
            ]
        else:
            points = parsers[args.point_type](detail_text)
        if not points:
            requested = args.point_type or "block/expression/toggle"
            raise RuntimeError(
                f"No normalized Xcelium {requested} rows found in "
                f"{source_path}. Unrecognized IMC detail layouts remain "
                "evidence-only."
            )
        report = write_coverage_hole_report(
            points, output, point_type=args.point_type, limit=args.limit
        )
    elif simulator == "vcs":
        if args.point_type not in {None, "condition"}:
            raise RuntimeError(
                "VCS item-level coverage currently supports --type condition."
            )
        report_dir = (
            project.root / ".zddv" / "coverage" / "urg-report"
        ).resolve()
        if not report_dir.exists():
            raise RuntimeError(
                f"VCS URG detail report not found at {report_dir}. "
                "Run 'zddv coverage' first."
            )
        points = parse_vcs_urg_condition_coverage_points(report_dir)
        if not points:
            raise RuntimeError(
                f"No normalized VCS condition truth-table rows found in {report_dir}."
            )
        report = write_coverage_hole_report(
            points,
            output,
            point_type="condition",
            limit=args.limit,
        )
    else:
        raise RuntimeError(
            "Coverage-hole itemization is not implemented for simulator: "
            f"{project.simulator}"
        )

    filter_label = report.get("filter_type") or args.point_type or "all"
    print(
        f"Coverage holes ({filter_label}): {report['total_holes']} "
        f"unhit point(s); {report['reported_holes']} written"
    )
    if report["by_type"]:
        breakdown = ", ".join(
            f"{kind}={count}" for kind, count in report["by_type"].items()
        )
        print(f"By type: {breakdown}")

    for hole in report["holes"][: args.show]:
        print(f"[{hole['type']}] {hole['name']}")
    if report["reported_holes"] > args.show:
        print(f"... {report['reported_holes'] - args.show} more in report")
    if report.get("xml"):
        print(f"Questa XML: {report['xml']}")
    print(f"Report: {report['path']}")
    return 0


def cmd_fcov_import(args) -> int:
    project = load_project(_project_arg(args))
    result = ingest_functional_coverage(
        project,
        args.path,
        source=args.source,
    )
    print(
        f"FUNCTIONAL COVERAGE: {result['covered_bins']}/{result['total_bins']} bins "
        f"covered ({result['coverage_rate']:.1f}%)"
    )
    print(f"Source: {result['source']}")
    print(f"Snapshot: {result['snapshot_id']}")
    print(f"Normalized: {result['normalized_path']}")
    return 0


def cmd_fcov_history(args) -> int:
    project = load_project(_project_arg(args))
    rows = list_functional_coverage_snapshots(project, limit=args.limit)
    if not rows:
        print("No functional coverage snapshots found.")
        return 0

    print(f"{'COVERAGE':>9} {'COVERED/TOTAL':>15} {'SOURCE':<20} SNAPSHOT")
    for row in rows:
        ratio = f"{row['covered_bins']}/{row['total_bins']}"
        print(
            f"{row['coverage_rate']:>8.1f}% {ratio:>15} "
            f"{row['source'][:20]:<20} {row['snapshot_id']}"
        )
    return 0


def cmd_fcov_holes(args) -> int:
    project = load_project(_project_arg(args))
    snapshot_id = args.snapshot
    if snapshot_id is None:
        rows = list_functional_coverage_snapshots(project, limit=1)
        if not rows:
            print("No functional coverage snapshots found.")
            return 0
        snapshot_id = rows[0]["snapshot_id"]

    bins = list_functional_coverage_bins(
        project,
        snapshot_id,
        status="UNCOVERED",
    )
    print(f"FUNCTIONAL COVERAGE HOLES: {len(bins)} bin(s) in {snapshot_id}")
    for item in bins[: args.limit]:
        scope = f"{item['scope']}." if item['scope'] else ""
        print(
            f"{scope}{item['coverpoint']}.{item['bin_name']} "
            f"hits={item['hits']} goal={item['goal']}"
        )
    if len(bins) > args.limit:
        print(f"... {len(bins) - args.limit} more")
    return 0


def cmd_assertions(args) -> int:
    project = load_project(_project_arg(args))
    rows = list_assertion_events(
        project,
        limit=args.limit,
        status=args.status,
        assertion_name=args.name,
    )
    stats = assertion_statistics(project)
    print(
        f"ASSERTIONS: {stats['passed']}/{stats['total']} passed "
        f"({stats['pass_rate']:.1f}%), {stats['failed']} failed"
    )
    if not rows:
        print("No assertion events found.")
        return 0

    print(f"{'STATUS':<7} {'ASSERTION':<28} {'RUN ID':<32} MESSAGE")
    for row in rows:
        message = row["message"] or "-"
        print(
            f"{row['status']:<7} {row['assertion_name'][:28]:<28} "
            f"{row['run_id'][:32]:<32} {message}"
        )
    return 0


def cmd_uvm_analyze(args) -> int:
    project = load_project(_project_arg(args))
    result = analyze_uvm_log(
        project,
        args.path,
        source=args.source,
        output=args.output,
        run_id=args.run_id,
    )
    summary = result["summary"]
    test_name = result.get("test_name") or "-"
    print(
        f"UVM {result['status']}: test={test_name} "
        f"I/W/E/F={summary['infos']}/{summary['warnings']}/"
        f"{summary['errors']}/{summary['fatals']}"
    )
    if result.get("run_id"):
        print(
            f"Run: {result['run_id']} "
            f"(simulator-status={result['run_status']}, "
            f"returncode={result['run_returncode']})"
        )
    print(
        f"Counts: {result['count_source']}  "
        f"summary={'complete' if result['report_summary_complete'] else 'fallback'}"
    )
    lifecycle = result["lifecycle"]["summary"]
    if (
        lifecycle["phase_events"]
        or lifecycle["objection_events"]
        or lifecycle["sequence_events"]
    ):
        print(
            "Lifecycle: "
            f"phases={lifecycle['phase_events']} "
            f"objections={lifecycle['objection_events']} "
            f"sequences={lifecycle['sequence_events']} "
            f"phase-names={len(lifecycle['phases_seen'])} "
            f"sequence-names={len(lifecycle['sequences_seen'])} "
            f"max-objection-total={lifecycle['max_observed_total']}"
        )
    for event in result["messages"]:
        if event["severity"] not in {"UVM_WARNING", "UVM_ERROR", "UVM_FATAL"}:
            continue
        report_id = f"[{event['report_id']}] " if event.get("report_id") else ""
        message = event.get("message") or "-"
        print(
            f"{event['severity']} line={event['log_line']} "
            f"{report_id}{message}"
        )
    print(f"Report: {result['report_path']}")
    return 0 if result["status"] == "PASS" else 1


def cmd_uvm_history(args) -> int:
    project = load_project(_project_arg(args))
    rows = list_uvm_log_snapshots(
        project,
        limit=args.limit,
        status=args.status,
        run_id=args.run_id,
    )
    if not rows:
        print("No UVM log snapshots found.")
        return 0

    print(
        f"{'STATUS':<6} {'TEST':<24} {'I/W/E/F':<20} "
        f"{'P/O/S':<11} {'RUN':<24} {'COUNT SOURCE':<18} SNAPSHOT"
    )
    for row in rows:
        counts = (
            f"{row['info_count']}/{row['warning_count']}/"
            f"{row['error_count']}/{row['fatal_count']}"
        )
        test_name = row["test_name"] or "-"
        run_id = row["run_id"] or "-"
        lifecycle_counts = (
            f"{row['phase_events']}/{row['objection_events']}/"
            f"{row['sequence_events']}"
        )
        print(
            f"{row['status']:<6} {test_name[:24]:<24} {counts:<20} "
            f"{lifecycle_counts:<11} {run_id[:24]:<24} "
            f"{row['count_source']:<18} {row['snapshot_id']}"
        )
    return 0



def cmd_uvm_marker_analyze(args) -> int:
    project = load_project(_project_arg(args))
    result = analyze_uvm_marker_log(
        project,
        args.path,
        source=args.source,
        output=args.output,
        run_id=args.run_id,
    )
    summary = result["summary"]
    print(
        f"UVM MARKER {result['status']}: "
        f"sequence-markers={summary['sequence_marker_lines']} "
        f"item-markers={summary['item_marker_lines']} "
        f"analysis-errors={summary['analysis_errors']}"
    )
    if result.get("run_id"):
        print(
            f"Run: {result['run_id']} "
            f"(simulator-status={result['run_status']}, "
            f"returncode={result['run_returncode']})"
        )
    if result.get("sequence_snapshot_id"):
        print(f"Sequence snapshot: {result['sequence_snapshot_id']}")
    if result.get("item_snapshot_id"):
        print(f"Item snapshot: {result['item_snapshot_id']}")
    for error in result["analysis_errors"][:20]:
        print(f"[{error['kind'].upper()}] {error['message']}")
    print(f"Report: {result['report_path']}")
    return 0 if result["status"] == "PASS" else 1

def cmd_uvm_item_analyze(args) -> int:
    project = load_project(_project_arg(args))
    result = analyze_uvm_item_file(
        project,
        args.path,
        source=args.source,
        output=args.output,
        run_id=args.run_id,
        max_bypass=args.max_bypass,
    )
    summary = result["summary"]
    print(
        f"UVM ITEM {result['status']}: "
        f"{summary['items']} item(s), "
        f"{summary['events']} event(s), "
        f"{summary['violations']} violation(s)"
    )
    print(
        f"Handshake: granted={summary['granted']} "
        f"requested={summary['requested']} "
        f"completed={summary['completed']} "
        f"responded={summary['responded']} "
        f"active={summary['active']} partial={summary['partial']}"
    )
    arbitration = result["arbitration"]["summary"]
    print(
        f"Arbitration evidence: grants={arbitration['grant_events']} "
        f"sequencers={arbitration['sequencers_observed']} "
        f"sequence-ids={arbitration['sequence_ids_observed']} "
        f"switches={arbitration['sequence_switches']} "
        f"unscoped={arbitration['unscoped_grant_events']}"
    )
    request_evidence = result["arbitration"]["request_evidence"]
    if request_evidence["available"]:
        request_summary = request_evidence["summary"]
        print(
            "Arbitration requests: "
            f"requests={request_summary['requests']} "
            f"matched-grants={request_summary['matched_grants']} "
            f"contended-grants={request_summary['contended_grants']} "
            f"pending={request_summary['pending_requests']} "
            f"max-pending={request_summary['max_pending']} "
            f"max-bypass={request_summary['max_bypass']}"
        )
        if args.max_bypass is not None:
            print(f"Arbitration policy: max-bypass={args.max_bypass}")
    if result.get("run_id"):
        print(
            f"Run: {result['run_id']} "
            f"(simulator-status={result['run_status']}, "
            f"returncode={result['run_returncode']})"
        )
    for violation in result["violations"][: args.show]:
        print(
            f"[{violation['code']}] event={violation['event_index']} "
            f"item={violation['item_id']} {violation['message']}"
        )
    if len(result["violations"]) > args.show:
        print(f"... {len(result['violations']) - args.show} more violation(s)")
    print(f"Report: {result['report_path']}")
    return 0 if result["status"] == "PASS" else 1

def cmd_uvm_item_instrument(args) -> int:
    project = load_project(_project_arg(args))
    result = write_uvm_item_instrumentation(
        project,
        output=args.output,
        add_source=args.add_source,
        force=args.force,
    )
    print(f"UVM item instrumentation: {result['path']}")
    print(f"Marker: {result['marker']}")
    if result["project_path"] is not None:
        state = "added" if result["added_to_project"] else "already present"
        print(f"Project source: {result['project_path']} ({state})")
    return 0


def cmd_uvm_item_log_analyze(args) -> int:
    project = load_project(_project_arg(args))
    result = analyze_uvm_item_log(
        project,
        args.path,
        source=args.source,
        output=args.output,
        run_id=args.run_id,
    )
    summary = result["summary"]
    print(
        f"UVM ITEM {result['status']}: "
        f"{summary['items']} item(s), "
        f"{summary['events']} event(s), "
        f"{summary['violations']} violation(s)"
    )
    print(
        f"Handshake: granted={summary['granted']} "
        f"requested={summary['requested']} "
        f"completed={summary['completed']} "
        f"responded={summary['responded']} "
        f"active={summary['active']} partial={summary['partial']}"
    )
    print(f"Markers: {len(result.get('marker_lines', []))} explicit marker(s)")
    arbitration = result.get("arbitration", {}).get("summary", {})
    if arbitration:
        print(
            f"Observed grants: {arbitration.get('grant_events', 0)} "
            f"across {arbitration.get('sequencers_observed', 0)} sequencer(s)"
        )
    if result.get("run_id"):
        print(
            f"Run: {result['run_id']} "
            f"(simulator-status={result['run_status']}, "
            f"returncode={result['run_returncode']})"
        )
    for violation in result["violations"][: args.show]:
        print(
            f"[{violation['code']}] event={violation['event_index']} "
            f"item={violation['item_id']} {violation['message']}"
        )
    if len(result["violations"]) > args.show:
        print(f"... {len(result['violations']) - args.show} more violation(s)")
    print(f"Report: {result['report_path']}")
    return 0 if result["status"] == "PASS" else 1


def cmd_uvm_item_history(args) -> int:
    project = load_project(_project_arg(args))
    rows = list_uvm_item_handshake_snapshots(
        project,
        limit=args.limit,
        status=args.status,
        run_id=args.run_id,
    )
    if not rows:
        print("No UVM item handshake snapshots found.")
        return 0

    print(
        f"{'STATUS':<6} {'ITEMS':>5} {'EVENTS':>6} {'VIOL':>5} "
        f"{'G/R/D/RSP':<15} {'ACTIVE/PART':<12} {'RUN':<24} SNAPSHOT"
    )
    for row in rows:
        handshake = (
            f"{row['granted_count']}/"
            f"{row['requested_count']}/"
            f"{row['completed_count']}/"
            f"{row['responded_count']}"
        )
        activity = f"{row['active_count']}/{row['partial_count']}"
        run_id = row["run_id"] or "-"
        print(
            f"{row['status']:<6} {row['item_count']:>5} "
            f"{row['event_count']:>6} {row['violation_count']:>5} "
            f"{handshake:<15} {activity:<12} "
            f"{run_id[:24]:<24} {row['snapshot_id']}"
        )
    return 0


def cmd_uvm_item_violations(args) -> int:
    project = load_project(_project_arg(args))
    code = args.code.upper() if args.code else None
    rows = list_uvm_item_handshake_violations(
        project,
        args.snapshot_id,
        limit=args.limit,
        code=code,
        item_id=args.item_id,
    )
    if not rows:
        print("No UVM item handshake violations found.")
        return 0

    print(f"{'IDX':>4} {'EVENT':>5} {'CODE':<28} {'ITEM':<20} MESSAGE")
    for row in rows:
        print(
            f"{row['violation_index']:>4} {row['event_index']:>5} "
            f"{row['code']:<28} {row['item_id'][:20]:<20} "
            f"{row['message']}"
        )
    return 0


def cmd_uvm_arbitration_analyze(args) -> int:
    project = load_project(_project_arg(args))
    result = analyze_uvm_arbitration_file(
        project,
        args.path,
        source=args.source,
        fairness_bound=args.fairness_bound,
        output=args.output,
        run_id=args.run_id,
    )
    summary = result["summary"]
    print(
        f"UVM ARBITRATION {result['status']}: "
        f"{summary['decisions']} decision(s), "
        f"{summary['requests']} request(s), "
        f"{summary['violations']} violation(s)"
    )
    print(
        f"Fairness: bound={result['fairness_bound'] if result['fairness_bound'] is not None else '-'} "
        f"max-wait={summary['max_wait_decisions']} "
        f"fairness-violations={summary['fairness_violations']} "
        f"pending={summary['pending']}"
    )
    if result.get("run_id"):
        print(
            f"Run: {result['run_id']} "
            f"(simulator-status={result['run_status']}, "
            f"returncode={result['run_returncode']})"
        )
    for violation in result["violations"][: args.show]:
        request_id = violation.get("request_id") or "-"
        print(
            f"[{violation['code']}] decision={violation['decision_id']} "
            f"request={request_id} {violation['message']}"
        )
    if len(result["violations"]) > args.show:
        print(f"... {len(result['violations']) - args.show} more violation(s)")
    print(f"Report: {result['report_path']}")
    return 0 if result["status"] == "PASS" else 1


def cmd_uvm_arbitration_log_analyze(args) -> int:
    project = load_project(_project_arg(args))
    result = analyze_uvm_arbitration_log(
        project,
        args.path,
        source=args.source,
        fairness_bound=args.fairness_bound,
        output=args.output,
        run_id=args.run_id,
    )
    summary = result["summary"]
    print(
        f"UVM ARBITRATION {result['status']}: "
        f"{summary['decisions']} decision(s), "
        f"{summary['requests']} request(s), "
        f"{summary['violations']} violation(s)"
    )
    print(
        f"Fairness: bound={result['fairness_bound'] if result['fairness_bound'] is not None else '-'} "
        f"max-wait={summary['max_wait_decisions']} "
        f"fairness-violations={summary['fairness_violations']} "
        f"pending={summary['pending']}"
    )
    print(
        f"Input: {result['input_mode']} marker={result['marker']} "
        f"markers={len(result['marker_lines'])}"
    )
    if result.get("run_id"):
        print(
            f"Run: {result['run_id']} "
            f"(simulator-status={result['run_status']}, "
            f"returncode={result['run_returncode']})"
        )
    for violation in result["violations"][: args.show]:
        request_id = violation.get("request_id") or "-"
        print(
            f"[{violation['code']}] decision={violation['decision_id']} "
            f"request={request_id} {violation['message']}"
        )
    if len(result["violations"]) > args.show:
        print(f"... {len(result['violations']) - args.show} more violation(s)")
    print(f"Report: {result['report_path']}")
    return 0 if result["status"] == "PASS" else 1


def cmd_uvm_arbitration_history(args) -> int:
    project = load_project(_project_arg(args))
    rows = list_uvm_arbitration_snapshots(
        project,
        limit=args.limit,
        status=args.status,
        run_id=args.run_id,
    )
    if not rows:
        print("No UVM arbitration snapshots found.")
        return 0

    print(
        f"{'STATUS':<6} {'DEC':>4} {'REQ':>4} {'GRANT':>5} {'PEND':>4} "
        f"{'VIOL/FAIR':<10} {'MAXWAIT':>7} {'BOUND':>5} {'RUN':<24} SNAPSHOT"
    )
    for row in rows:
        pair = f"{row['violation_count']}/{row['fairness_violation_count']}"
        bound = "-" if row["fairness_bound"] is None else str(row["fairness_bound"])
        run_id = row["run_id"] or "-"
        print(
            f"{row['status']:<6} {row['decision_count']:>4} "
            f"{row['request_count']:>4} {row['grant_count']:>5} "
            f"{row['pending_count']:>4} {pair:<10} "
            f"{row['max_wait_decisions']:>7} {bound:>5} "
            f"{run_id[:24]:<24} {row['snapshot_id']}"
        )
    return 0


def cmd_uvm_sequence_instrument(args) -> int:
    project = load_project(_project_arg(args))
    result = write_uvm_sequence_instrumentation(
        project,
        output=args.output,
        add_source=args.add_source,
        force=args.force,
    )
    print(f"UVM sequence instrumentation: {result['path']}")
    print(f"Marker: {result['marker']}")
    if result["project_path"] is not None:
        state = "added" if result["added_to_project"] else "already present"
        print(f"Project source: {result['project_path']} ({state})")
    return 0


def cmd_uvm_sequence_analyze(args) -> int:
    project = load_project(_project_arg(args))
    result = analyze_uvm_sequence_file(
        project,
        args.path,
        source=args.source,
        output=args.output,
        run_id=args.run_id,
    )
    summary = result["summary"]
    print(
        f"UVM SEQUENCE {result['status']}: "
        f"{summary['sequences']} sequence(s), "
        f"{summary['events']} event(s), "
        f"{summary['violations']} violation(s)"
    )
    print(
        f"Terminal: finished={summary['finished']} stopped={summary['stopped']} "
        f"active={summary['active']} complete={summary['complete']}"
    )
    print(
        f"Hierarchy: nested={summary['nested']} "
        f"unresolved-parents={summary['unresolved_parents']}"
    )
    if result.get("run_id"):
        print(
            f"Run: {result['run_id']} "
            f"(simulator-status={result['run_status']}, "
            f"returncode={result['run_returncode']})"
        )
    for violation in result["violations"][: args.show]:
        print(
            f"[{violation['code']}] event={violation['event_index']} "
            f"sequence={violation['sequence_id']} {violation['message']}"
        )
    if len(result["violations"]) > args.show:
        print(f"... {len(result['violations']) - args.show} more violation(s)")
    print(f"Report: {result['report_path']}")
    return 0 if result["status"] == "PASS" else 1



def cmd_uvm_sequence_log_analyze(args) -> int:
    project = load_project(_project_arg(args))
    result = analyze_uvm_sequence_log(
        project,
        args.path,
        source=args.source,
        output=args.output,
        run_id=args.run_id,
    )
    summary = result["summary"]
    print(
        f"UVM SEQUENCE {result['status']}: "
        f"{summary['sequences']} sequence(s), "
        f"{summary['events']} event(s), "
        f"{summary['violations']} violation(s)"
    )
    print(
        f"Terminal: finished={summary['finished']} stopped={summary['stopped']} "
        f"active={summary['active']} complete={summary['complete']}"
    )
    print(
        f"Hierarchy: nested={summary['nested']} "
        f"unresolved-parents={summary['unresolved_parents']}"
    )
    print(f"Markers: {len(result.get('marker_lines', []))} explicit marker(s)")
    if result.get("run_id"):
        print(
            f"Run: {result['run_id']} "
            f"(simulator-status={result['run_status']}, "
            f"returncode={result['run_returncode']})"
        )
    for violation in result["violations"][: args.show]:
        print(
            f"[{violation['code']}] event={violation['event_index']} "
            f"sequence={violation['sequence_id']} {violation['message']}"
        )
    if len(result["violations"]) > args.show:
        print(f"... {len(result['violations']) - args.show} more violation(s)")
    print(f"Report: {result['report_path']}")
    return 0 if result["status"] == "PASS" else 1

def cmd_uvm_sequence_history(args) -> int:
    project = load_project(_project_arg(args))
    rows = list_uvm_sequence_lifecycle_snapshots(
        project,
        limit=args.limit,
        status=args.status,
        run_id=args.run_id,
    )
    if not rows:
        print("No UVM sequence lifecycle snapshots found.")
        return 0

    print(
        f"{'STATUS':<6} {'SEQ':>5} {'EVENTS':>6} {'VIOL':>5} "
        f"{'FIN/STOP/ACTIVE':<17} {'RUN':<24} SNAPSHOT"
    )
    for row in rows:
        terminal = (
            f"{row['finished_count']}/"
            f"{row['stopped_count']}/"
            f"{row['active_count']}"
        )
        run_id = row["run_id"] or "-"
        print(
            f"{row['status']:<6} {row['sequence_count']:>5} "
            f"{row['event_count']:>6} {row['violation_count']:>5} "
            f"{terminal:<17} {run_id[:24]:<24} {row['snapshot_id']}"
        )
    return 0


def cmd_async_fifo_analyze(args) -> int:
    project = load_project(_project_arg(args))
    result = analyze_async_fifo_file(project, args.path, output=args.output)
    summary = result["summary"]
    print(
        f"ASYNC FIFO CDC {result['status']}: "
        f"{summary['events']} event(s), "
        f"{summary['violations']} invariant violation(s)"
    )
    print(
        f"Accepted W/R: {summary['accepted_writes']}/{summary['accepted_reads']}  "
        f"Blocked W/R requests: "
        f"{summary['blocked_write_requests']}/{summary['blocked_read_requests']}"
    )
    for violation in result["violations"][: args.show]:
        print(
            f"[{violation['code']}] {violation['domain']} "
            f"cycle={violation['cycle']} {violation['message']}"
        )
    if len(result["violations"]) > args.show:
        print(f"... {len(result['violations']) - args.show} more violation(s)")
    print("Scope: dynamic local-domain FIFO invariants; not static CDC signoff")
    print(f"Report: {result['report_path']}")
    return 0 if result["status"] == "PASS" else 1


def cmd_apb_analyze(args) -> int:
    project = load_project(_project_arg(args))
    result = analyze_apb_file(
        project,
        args.path,
        output=args.output,
    )
    summary = result["summary"]
    print(
        f"APB {result['status']}: "
        f"{summary['completed_transactions']} completed transaction(s), "
        f"{summary['violations']} protocol violation(s)"
    )
    print(
        f"Reads/Writes: {summary['reads']}/{summary['writes']}  "
        f"Wait cycles: {summary['wait_cycles']}  "
        f"Error responses: {summary['error_responses']}"
    )
    for violation in result["violations"][: args.show]:
        print(
            f"[{violation['code']}] cycle={violation['cycle']} "
            f"{violation['message']}"
        )
    if len(result["violations"]) > args.show:
        print(f"... {len(result['violations']) - args.show} more violation(s)")
    print(f"Report: {result['report_path']}")
    return 0 if result["status"] == "PASS" else 1


def cmd_axi4lite_analyze(args) -> int:
    project = load_project(_project_arg(args))
    result = analyze_axi4lite_file(
        project,
        args.path,
        output=args.output,
    )
    summary = result["summary"]
    print(
        f"AXI4-Lite {result['status']}: "
        f"{summary['completed_transactions']} completed transaction(s), "
        f"{summary['violations']} protocol violation(s)"
    )
    print(
        f"Reads/Writes: {summary['reads']}/{summary['writes']}  "
        f"Error responses: {summary['error_responses']}"
    )
    stalls = summary["channel_stall_cycles"]
    print(
        "Channel stalls: "
        + " ".join(f"{name}={stalls[name]}" for name in ("AW", "W", "B", "AR", "R"))
    )
    for violation in result["violations"][: args.show]:
        print(
            f"[{violation['code']}] cycle={violation['cycle']} "
            f"{violation['message']}"
        )
    if len(result["violations"]) > args.show:
        print(f"... {len(result['violations']) - args.show} more violation(s)")
    print(f"Report: {result['report_path']}")
    return 0 if result["status"] == "PASS" else 1



def cmd_axi4_analyze(args) -> int:
    project = load_project(_project_arg(args))
    result = analyze_axi4_file(project, args.path, output=args.output)
    summary = result["summary"]
    print(
        f"AXI4 {result['status']}: "
        f"{summary['completed_transactions']} completed transaction(s), "
        f"{summary['violations']} protocol violation(s)"
    )
    print(
        f"Reads/Writes: {summary['completed_reads']}/{summary['completed_writes']}  "
        f"Read/Write beats: {summary['read_beats']}/{summary['write_beats']}"
    )
    print(
        f"Error responses: write={summary['write_error_responses']} "
        f"read-beats={summary['read_error_beats']}"
    )
    print(
        f"Exclusive R/W/matched: "
        f"{summary['exclusive_reads']}/{summary['exclusive_writes']}/"
        f"{summary['matched_exclusive_writes']}"
    )
    for violation in result["violations"][: args.show]:
        print(
            f"[{violation['code']}] cycle={violation['cycle']} "
            f"{violation['message']}"
        )
    if len(result["violations"]) > args.show:
        print(f"... {len(result['violations']) - args.show} more violation(s)")
    print(f"Report: {result['report_path']}")
    return 0 if result["status"] == "PASS" else 1


def cmd_ucie_analyze(args) -> int:
    project = load_project(_project_arg(args))
    result = analyze_ucie_file(project, args.path, output=args.output)
    summary = result["summary"]
    print(
        f"UCIe {result['status']}: health={result['health']}  "
        f"{summary['flits']} flit(s), {summary['violations']} trace violation(s)"
    )
    print(
        f"TX/RX: {summary['tx_flits']}/{summary['rx_flits']}  "
        f"ACK/NAK: {summary['ack_flits']}/{summary['nak_flits']}  "
        f"CRC errors: {summary['crc_error_flits']}"
    )
    print(
        "FLIT sizes: "
        f"68B={summary['flit_sizes']['68']} "
        f"256B={summary['flit_sizes']['256']}"
    )
    for violation in result["violations"][: args.show]:
        field = (
            f" field={violation['field']}"
            if violation.get("field") is not None
            else ""
        )
        print(
            f"[{violation['code']}] flit={violation['flit_index']}"
            f"{field} {violation['message']}"
        )
    if len(result["violations"]) > args.show:
        print(f"... {len(result['violations']) - args.show} more violation(s)")
    print(f"Report: {result['report_path']}")
    return 0 if result["status"] == "PASS" else 1


def cmd_axi4_waveform(args) -> int:
    project = load_project(_project_arg(args))
    result = analyze_axi4_waveform(
        project,
        run_id=args.run_id,
        input_path=args.input,
        scope=args.scope,
        clock=args.clock,
        edge=args.edge,
        trace_output=args.trace_output,
        output=args.output,
    )
    summary = result["summary"]
    waveform = result["waveform"]
    print(
        f"AXI4 WAVEFORM {result['status']}: "
        f"{summary['completed_transactions']} completed transaction(s), "
        f"{summary['violations']} protocol violation(s)"
    )
    print(
        f"Scope: {waveform['scope']}  Clock: {waveform['clock']} "
        f"({waveform['edge']})  Timescale: {waveform.get('timescale') or '-'}"
    )
    print(
        f"Reads/Writes: {summary['completed_reads']}/{summary['completed_writes']}  "
        f"Read/Write beats: {summary['read_beats']}/{summary['write_beats']}"
    )
    print(
        f"Error responses: write={summary['write_error_responses']} "
        f"read-beats={summary['read_error_beats']}"
    )
    print(
        f"Exclusive R/W/matched: "
        f"{summary['exclusive_reads']}/{summary['exclusive_writes']}/"
        f"{summary['matched_exclusive_writes']}"
    )
    stalls = summary["channel_stall_cycles"]
    print(
        "Channel stalls: "
        + " ".join(f"{name}={stalls[name]}" for name in ("AW", "W", "B", "AR", "R"))
    )
    for violation in result["violations"][: args.show]:
        when = (
            f"time={violation.get('time')} "
            if violation.get("time") is not None
            else ""
        )
        channel = f"[{violation.get('channel')}] " if violation.get("channel") else ""
        print(
            f"[{violation['code']}] {channel}{when}"
            f"cycle={violation['cycle']} {violation['message']}"
        )
    if len(result["violations"]) > args.show:
        print(f"... {len(result['violations']) - args.show} more violation(s)")
    print(f"Normalized trace: {result['trace_path']}")
    print(f"Report: {result['report_path']}")
    return 0 if result["status"] == "PASS" else 1


def cmd_apb_waveform(args) -> int:
    project = load_project(_project_arg(args))
    result = analyze_apb_waveform(
        project,
        run_id=args.run_id,
        input_path=args.input,
        scope=args.scope,
        clock=args.clock,
        edge=args.edge,
        trace_output=args.trace_output,
        output=args.output,
    )
    summary = result["summary"]
    waveform = result["waveform"]
    print(
        f"APB WAVEFORM {result['status']}: "
        f"{summary['completed_transactions']} completed transaction(s), "
        f"{summary['violations']} protocol violation(s)"
    )
    print(
        f"Scope: {waveform['scope']}  Clock: {waveform['clock']} "
        f"({waveform['edge']})  Timescale: {waveform.get('timescale') or '-'}"
    )
    print(
        f"Reads/Writes: {summary['reads']}/{summary['writes']}  "
        f"Wait cycles: {summary['wait_cycles']}  "
        f"Error responses: {summary['error_responses']}"
    )
    for violation in result["violations"][: args.show]:
        when = (
            f"time={violation.get('time')} "
            if violation.get("time") is not None
            else ""
        )
        print(
            f"[{violation['code']}] {when}cycle={violation['cycle']} "
            f"{violation['message']}"
        )
    if len(result["violations"]) > args.show:
        print(f"... {len(result['violations']) - args.show} more violation(s)")
    print(f"Normalized trace: {result['trace_path']}")
    print(f"Report: {result['report_path']}")
    return 0 if result["status"] == "PASS" else 1


def cmd_axi4lite_waveform(args) -> int:
    project = load_project(_project_arg(args))
    result = analyze_axi4lite_waveform(
        project,
        run_id=args.run_id,
        input_path=args.input,
        scope=args.scope,
        clock=args.clock,
        edge=args.edge,
        trace_output=args.trace_output,
        output=args.output,
    )
    summary = result["summary"]
    waveform = result["waveform"]
    print(
        f"AXI4-Lite WAVEFORM {result['status']}: "
        f"{summary['completed_transactions']} completed transaction(s), "
        f"{summary['violations']} protocol violation(s)"
    )
    print(
        f"Scope: {waveform['scope']}  Clock: {waveform['clock']} "
        f"({waveform['edge']})  Timescale: {waveform.get('timescale') or '-'}"
    )
    print(
        f"Reads/Writes: {summary['reads']}/{summary['writes']}  "
        f"Error responses: {summary['error_responses']}"
    )
    stalls = summary["channel_stall_cycles"]
    print(
        "Channel stalls: "
        + " ".join(f"{name}={stalls[name]}" for name in ("AW", "W", "B", "AR", "R"))
    )
    for violation in result["violations"][: args.show]:
        when = (
            f"time={violation.get('time')} "
            if violation.get("time") is not None
            else ""
        )
        channel = f"[{violation.get('channel')}] " if violation.get("channel") else ""
        print(
            f"[{violation['code']}] {channel}{when}"
            f"cycle={violation['cycle']} {violation['message']}"
        )
    if len(result["violations"]) > args.show:
        print(f"... {len(result['violations']) - args.show} more violation(s)")
    print(f"Normalized trace: {result['trace_path']}")
    print(f"Report: {result['report_path']}")
    return 0 if result["status"] == "PASS" else 1


def cmd_runs(args) -> int:
    project = load_project(_project_arg(args))
    rows = list_runs(project, limit=args.limit, status=args.status)
    print(f"Results DB: {database_path(project)}")
    if not rows:
        print("No runs found.")
        return 0

    print(f"{'STATUS':<8} {'TEST':<24} {'SEED':<10} {'TIME(ms)':>10}  RUN ID")
    for row in rows:
        test = row["test_name"] or "-"
        seed = "-" if row["seed"] is None else str(row["seed"])
        duration = "-"
        if row["duration_ms"] is not None:
            duration = f"{row['duration_ms']:.1f}"
        print(
            f"{row['status']:<8} {test[:24]:<24} {seed:<10} "
            f"{duration:>10}  {row['run_id']}"
        )
    return 0


def cmd_rerun(args) -> int:
    project = load_project(_project_arg(args))
    backend = _backend(project.simulator)
    statuses = tuple(args.status or ("FAIL", "TIMEOUT"))
    rows = list_run_records(project, limit=args.limit, statuses=statuses)

    print(f"Selected {len(rows)} run(s) for rerun: {', '.join(statuses)}")
    if not rows:
        return 0

    build = backend.build(project)
    if not build.passed:
        print(f"BUILD FAIL: {build.log_path}")
        return build.returncode or 1

    passed = 0
    for source in reversed(rows):
        result = backend.run(
            project,
            test_name=source["test_name"],
            seed=source["seed"],
            plusargs=list(source["plusargs"]),
            timeout_s=source["timeout_s"],
        )
        print(
            f"{source['run_id']} -> {result.status}: "
            f"{result.run_id}"
        )
        if result.status == "PASS":
            passed += 1

    total = len(rows)
    print(f"RERUN: {passed}/{total} passed")
    return 0 if passed == total else 1


def cmd_junit(args) -> int:
    project = load_project(_project_arg(args))
    statuses = tuple(args.status or ())
    rows = list_run_records(
        project,
        limit=args.limit,
        statuses=statuses or None,
    )

    output = Path(args.output)
    if not output.is_absolute():
        output = project.root / output

    report = write_junit_report(
        rows,
        output,
        suite_name=project.name,
    )
    print(f"JUnit: {report}")
    print(f"Tests: {len(rows)}")
    return 0


def cmd_failures(args) -> int:
    project = load_project(_project_arg(args))
    rows = list_run_records(
        project,
        limit=args.limit,
        statuses=("FAIL", "TIMEOUT"),
    )
    groups = group_failure_records(rows)

    output = Path(args.output)
    if not output.is_absolute():
        output = project.root / output
    report = write_failure_report(groups, output)

    total_runs = sum(group["count"] for group in groups)
    print(f"Failure groups: {len(groups)} from {total_runs} run(s)")
    if groups:
        print(f"{'COUNT':>5}  {'STATUS':<14} {'TESTS':<24} SIGNATURE")
        for group in groups[: args.show]:
            statuses = ",".join(group["statuses"])
            tests = ",".join(group["tests"]) or "-"
            print(
                f"{group['count']:>5}  {statuses[:14]:<14} "
                f"{tests[:24]:<24} {group['signature']}"
            )
    print(f"Report: {report}")
    return 0


def cmd_report(args) -> int:
    project = load_project(_project_arg(args))
    result = generate_html_report(project, limit=args.limit)
    stats = result["stats"]
    print(
        f"REPORT: {stats['passed']}/{stats['total']} passed "
        f"({stats['pass_rate']:.1f}%)"
    )
    print(f"HTML: {result['path']}")
    print(f"Failure groups: {len(result['failure_groups'])}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zddv",
        description="Zewail Digital Design and Verification Tool",
    )
    parser.add_argument(
        "--project",
        "-p",
        default=".",
        help="Project directory or zddv.toml path (default: current directory)",
    )
    parser.add_argument("--version", action="version", version=f"ZDDV {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Create a new ZDDV project")
    p_init.add_argument("path", nargs="?", default=".")
    p_init.set_defaults(func=cmd_init)

    p_add = sub.add_parser("add", help="Add RTL or testbench source patterns")
    p_add.add_argument("kind", choices=("rtl", "tb"))
    p_add.add_argument("patterns", nargs="+")
    p_add.set_defaults(func=cmd_add)

    p_config = sub.add_parser("config", help="Set project configuration")
    p_config.add_argument("key", choices=("simulator", "top"))
    p_config.add_argument("value")
    p_config.set_defaults(func=cmd_config)

    p_doctor = sub.add_parser("doctor", help="Check the local verification environment")
    p_doctor.add_argument(
        "--simulator",
        choices=("verilator", "questa", "questasim", "vcs", "xcelium", "xrun"),
        default=None,
        help="Simulator backend to check; defaults to verilator",
    )
    p_doctor.set_defaults(func=cmd_doctor)

    p_index = sub.add_parser(
        "index",
        help="Build the normalized source/design index",
    )
    p_index.set_defaults(func=cmd_index)

    p_hierarchy = sub.add_parser(
        "hierarchy",
        help="Build and print the source-level design hierarchy",
    )
    p_hierarchy.set_defaults(func=cmd_hierarchy)

    p_connectivity = sub.add_parser(
        "connectivity",
        help="Build/query source-level structural drivers and loads",
    )
    p_connectivity.add_argument(
        "signal",
        nargs="?",
        default=None,
        help="Optional signal name to query; omit to build/show index summary",
    )
    p_connectivity.add_argument(
        "--unit",
        default=None,
        help="Design unit containing the signal; defaults to the configured top",
    )
    p_connectivity.add_argument(
        "--output",
        default=".zddv/design/connectivity.json",
        help="Normalized connectivity JSON output path",
    )
    p_connectivity.set_defaults(func=cmd_connectivity)

    p_waveform_index = sub.add_parser(
        "waveform-index",
        help="Index scopes and signals from a waveform artifact",
    )
    waveform_source = p_waveform_index.add_mutually_exclusive_group()
    waveform_source.add_argument(
        "--run",
        dest="run_id",
        default=None,
        help="Run ID to index; defaults to the latest run with a waveform",
    )
    waveform_source.add_argument(
        "--input",
        default=None,
        help="Waveform path relative to the project, independent of run history",
    )
    p_waveform_index.add_argument(
        "--output",
        default=None,
        help="Optional JSON output path; default is .zddv/waveforms/<run>.json",
    )
    p_waveform_index.set_defaults(func=cmd_waveform_index)

    p_waveform_probe = sub.add_parser(
        "waveform-probe",
        help="Stream selected VCD signal value changes",
    )
    p_waveform_probe.add_argument(
        "signals",
        nargs="+",
        help="Signal full path or unique leaf name; repeat for multiple signals",
    )
    probe_source = p_waveform_probe.add_mutually_exclusive_group()
    probe_source.add_argument(
        "--run",
        dest="run_id",
        default=None,
        help="Run ID to probe; defaults to the latest run with a waveform",
    )
    probe_source.add_argument(
        "--input",
        default=None,
        help="VCD path relative to the project, independent of run history",
    )
    p_waveform_probe.add_argument(
        "--start",
        type=int,
        default=None,
        help="Optional inclusive VCD start timestamp",
    )
    p_waveform_probe.add_argument(
        "--end",
        type=int,
        default=None,
        help="Optional inclusive VCD end timestamp",
    )
    p_waveform_probe.add_argument(
        "--max-changes",
        type=int,
        default=10_000,
        help="Maximum value changes retained per selected signal",
    )
    p_waveform_probe.add_argument(
        "--output",
        default=None,
        help="Optional JSON output path; default is .zddv/waveforms/probes/<run>.json",
    )
    p_waveform_probe.set_defaults(func=cmd_waveform_probe)

    p_crossprobe = sub.add_parser(
        "crossprobe",
        help="Map a waveform signal to source-level hierarchy and RTL declaration",
    )
    p_crossprobe.add_argument(
        "signal",
        help="Waveform signal path or a unique signal name",
    )
    crossprobe_source = p_crossprobe.add_mutually_exclusive_group()
    crossprobe_source.add_argument(
        "--run",
        dest="run_id",
        default=None,
        help="Run ID to probe; defaults to the latest run with a waveform",
    )
    crossprobe_source.add_argument(
        "--input",
        default=None,
        help="Waveform path relative to the project, independent of run history",
    )
    p_crossprobe.add_argument(
        "--output",
        default=".zddv/debug/crossprobe.json",
        help="Cross-probe JSON report path",
    )
    p_crossprobe.set_defaults(func=cmd_crossprobe)

    p_assertion_waveform = sub.add_parser(
        "assertion-waveform",
        help="Correlate assertion events with their run waveform indexes",
    )
    p_assertion_waveform.add_argument(
        "--run",
        dest="run_id",
        default=None,
        help="Optional exact run ID",
    )
    p_assertion_waveform.add_argument(
        "--status",
        choices=("PASS", "FAIL"),
        default=None,
        help="Optional assertion status filter",
    )
    p_assertion_waveform.add_argument(
        "--name",
        default=None,
        help="Optional exact assertion-name filter",
    )
    p_assertion_waveform.add_argument("--limit", type=int, default=100)
    p_assertion_waveform.add_argument(
        "--signal-limit",
        type=int,
        default=20,
        help="Maximum exact-name/path waveform signal hints per assertion",
    )
    p_assertion_waveform.add_argument(
        "--show",
        type=int,
        default=20,
        help="Maximum correlated assertion events to print",
    )
    p_assertion_waveform.add_argument(
        "--output",
        default=".zddv/debug/assertion-waveform.json",
        help="JSON correlation report path",
    )
    p_assertion_waveform.set_defaults(func=cmd_assertion_waveform)

    p_lint = sub.add_parser("lint", help="Lint the configured SystemVerilog design")
    p_lint.set_defaults(func=cmd_lint)

    p_build = sub.add_parser("build", help="Compile/elaborate the configured project")
    p_build.set_defaults(func=cmd_build)

    p_run = sub.add_parser("run", help="Run the configured simulation")
    p_run.add_argument("--test", default=None, help="Logical test name")
    p_run.add_argument("--seed", type=int, default=None)
    p_run.add_argument(
        "--plusarg",
        action="append",
        default=[],
        help="Runtime plusarg; repeat for multiple arguments",
    )
    p_run.add_argument("--timeout", type=float, default=None, help="Timeout in seconds")
    p_run.set_defaults(func=cmd_run)

    p_regress = sub.add_parser("regress", help="Run a regression definition")
    p_regress.add_argument("regression_file", help="Regression TOML file")
    p_regress.set_defaults(func=cmd_regress)

    p_formal_bmc = sub.add_parser(
        "formal-bmc",
        help="Run a finite-depth SymbiYosys bounded model check",
    )
    p_formal_bmc.add_argument(
        "--depth",
        type=int,
        required=True,
        help="Maximum BMC depth in cycles (must be >= 1)",
    )
    p_formal_bmc.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Optional timeout in seconds",
    )
    p_formal_bmc.set_defaults(func=cmd_formal_bmc)

    p_formal_cover = sub.add_parser(
        "formal-cover",
        help="Run a finite-depth SymbiYosys cover reachability check",
    )
    p_formal_cover.add_argument(
        "--depth",
        type=int,
        required=True,
        help="Maximum cover search depth in cycles (must be >= 1)",
    )
    p_formal_cover.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Optional timeout in seconds",
    )
    p_formal_cover.set_defaults(func=cmd_formal_cover)

    p_formal_counterexample = sub.add_parser(
        "formal-counterexample",
        help="Normalize formal counterexample or witness JSON evidence",
    )
    p_formal_counterexample.add_argument(
        "path",
        help="Normalized formal counterexample/witness JSON file",
    )
    p_formal_counterexample.add_argument(
        "--source",
        default=None,
        help="Optional source/adapter label overriding the JSON source",
    )
    p_formal_counterexample.add_argument(
        "--output",
        default=".zddv/formal/counterexamples/latest.json",
        help="Normalized formal trace JSON output path",
    )
    p_formal_counterexample.set_defaults(func=cmd_formal_counterexample)

    p_formal_sby = sub.add_parser(
        "formal-sby-analyze",
        help="Import a completed native SymbiYosys logfile",
    )
    p_formal_sby.add_argument("path", help="SymbiYosys logfile path")
    p_formal_sby.add_argument(
        "--mode",
        choices=("bmc", "prove", "cover"),
        required=True,
        help="Formal mode associated with the native SBY run",
    )
    p_formal_sby.add_argument(
        "--depth",
        type=int,
        default=None,
        help="Optional explicit bound/depth retained as evidence",
    )
    p_formal_sby.add_argument(
        "--output",
        default=".zddv/formal/sby/latest.json",
        help="Normalized SBY evidence JSON report path",
    )
    p_formal_sby.set_defaults(func=cmd_formal_sby_analyze)

    p_formal = sub.add_parser(
        "formal-analyze",
        help="Import normalized formal result evidence and persist property outcomes",
    )
    p_formal.add_argument("path", help="Normalized formal result JSON file")
    p_formal.add_argument(
        "--output",
        default=".zddv/formal/latest.json",
        help="Normalized formal evidence JSON report path",
    )
    p_formal.set_defaults(func=cmd_formal_analyze)

    p_formal_history = sub.add_parser(
        "formal-history",
        help="Show persisted normalized formal result snapshots",
    )
    p_formal_history.add_argument("--limit", type=int, default=20)
    p_formal_history.add_argument(
        "--status",
        choices=("PASS", "FAIL", "UNKNOWN", "ERROR"),
        default=None,
    )
    p_formal_history.add_argument(
        "--mode",
        choices=("bmc", "prove", "cover"),
        default=None,
    )
    p_formal_history.set_defaults(func=cmd_formal_history)

    p_formal_coverage = sub.add_parser(
        "formal-coverage",
        help="Aggregate mutually comparable finite-depth formal cover snapshots",
    )
    p_formal_coverage.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Maximum persisted cover snapshots to consider",
    )
    p_formal_coverage.add_argument(
        "--backend",
        default=None,
        help="Optional exact formal backend filter",
    )
    p_formal_coverage.add_argument(
        "--depth",
        type=int,
        default=None,
        help="Optional exact finite cover depth filter",
    )
    p_formal_coverage.add_argument(
        "--output",
        default=".zddv/formal/coverage.json",
        help="Aggregate formal coverage JSON report path",
    )
    p_formal_coverage.set_defaults(func=cmd_formal_coverage)

    p_coverage = sub.add_parser("coverage", help="Merge and report collected coverage")
    p_coverage.set_defaults(func=cmd_coverage)

    p_coverage_history = sub.add_parser(
        "coverage-history",
        help="Show normalized coverage snapshot history",
    )
    p_coverage_history.add_argument("--limit", type=int, default=20)
    p_coverage_history.set_defaults(func=cmd_coverage_history)

    p_coverage_holes = sub.add_parser(
        "coverage-holes",
        help="Report normalized unhit coverage points",
    )
    p_coverage_holes.add_argument(
        "--type",
        dest="point_type",
        default=None,
        help="Optional normalized coverage point type filter, e.g. line or toggle",
    )
    p_coverage_holes.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Maximum number of holes to write to the report",
    )
    p_coverage_holes.add_argument(
        "--show",
        type=int,
        default=20,
        help="Maximum number of holes to print in the terminal",
    )
    p_coverage_holes.add_argument(
        "--output",
        default=".zddv/coverage/holes.json",
        help="JSON report path",
    )
    p_coverage_holes.set_defaults(func=cmd_coverage_holes)

    p_fcov_import = sub.add_parser(
        "fcov-import",
        help="Import normalized functional coverage bins from JSON",
    )
    p_fcov_import.add_argument("path", help="Functional coverage JSON file")
    p_fcov_import.add_argument(
        "--source",
        default=None,
        help="Optional source/adapter label overriding the JSON source",
    )
    p_fcov_import.set_defaults(func=cmd_fcov_import)

    p_fcov_history = sub.add_parser(
        "fcov-history",
        help="Show functional coverage snapshot history",
    )
    p_fcov_history.add_argument("--limit", type=int, default=20)
    p_fcov_history.set_defaults(func=cmd_fcov_history)

    p_fcov_holes = sub.add_parser(
        "fcov-holes",
        help="Show uncovered bins from a functional coverage snapshot",
    )
    p_fcov_holes.add_argument(
        "--snapshot",
        default=None,
        help="Snapshot ID; defaults to the latest snapshot",
    )
    p_fcov_holes.add_argument("--limit", type=int, default=50)
    p_fcov_holes.set_defaults(func=cmd_fcov_holes)

    p_uvm = sub.add_parser(
        "uvm-analyze",
        help="Normalize UVM report messages and final severity summary from a log",
    )
    p_uvm.add_argument(
        "path",
        nargs="?",
        default=None,
        help="UVM simulation log file; optional when --run is supplied",
    )
    p_uvm.add_argument(
        "--run",
        dest="run_id",
        default=None,
        help="Recorded ZDDV run ID; uses its simulation log when path is omitted",
    )
    p_uvm.add_argument(
        "--source",
        default=None,
        help="Optional simulator/adapter label, e.g. questa or vcs",
    )
    p_uvm.add_argument(
        "--output",
        default=".zddv/uvm/latest.json",
        help="Normalized UVM JSON report path",
    )
    p_uvm.set_defaults(func=cmd_uvm_analyze)

    p_uvm_history = sub.add_parser(
        "uvm-history",
        help="Show persisted normalized UVM log analysis snapshots",
    )
    p_uvm_history.add_argument("--limit", type=int, default=20)
    p_uvm_history.add_argument(
        "--status",
        choices=("PASS", "FAIL"),
        default=None,
    )
    p_uvm_history.add_argument(
        "--run",
        dest="run_id",
        default=None,
        help="Filter UVM snapshots linked to a recorded ZDDV run ID",
    )
    p_uvm_history.set_defaults(func=cmd_uvm_history)



    p_uvm_item_instrument = sub.add_parser(
        "uvm-item-instrument",
        help="Generate the portable ZDDV_UVM_ITEM SystemVerilog trace helper",
    )
    p_uvm_item_instrument.add_argument(
        "--output",
        default="tb/zddv_uvm_item_trace_pkg.sv",
        help="Generated SystemVerilog helper path",
    )
    p_uvm_item_instrument.add_argument(
        "--no-add-source",
        dest="add_source",
        action="store_false",
        help="Do not add the generated helper to testbench source order",
    )
    p_uvm_item_instrument.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing generated helper",
    )
    p_uvm_item_instrument.set_defaults(
        func=cmd_uvm_item_instrument,
        add_source=True,
    )

    p_uvm_marker = sub.add_parser(
        "uvm-marker-analyze",
        help="Analyze explicit ZDDV UVM sequence/item markers from a simulation log",
    )
    p_uvm_marker.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Simulation log file; optional when --run is supplied",
    )
    p_uvm_marker.add_argument(
        "--run",
        dest="run_id",
        default=None,
        help="Recorded ZDDV run ID; uses its simulation log when path is omitted",
    )
    p_uvm_marker.add_argument(
        "--source",
        default=None,
        help="Optional source label for marker evidence",
    )
    p_uvm_marker.add_argument(
        "--output",
        default=".zddv/uvm/markers/latest.json",
        help="Run-level UVM marker analysis JSON report path",
    )
    p_uvm_marker.set_defaults(func=cmd_uvm_marker_analyze)


    p_uvm_item = sub.add_parser(
        "uvm-item-analyze",
        help="Analyze normalized UVM sequence-item handshake events from JSON",
    )
    p_uvm_item.add_argument(
        "path",
        help="Normalized UVM sequence-item handshake event JSON file",
    )
    p_uvm_item.add_argument(
        "--run",
        dest="run_id",
        default=None,
        help="Optional recorded ZDDV run ID to correlate with this item snapshot",
    )
    p_uvm_item.add_argument(
        "--source",
        default=None,
        help="Optional adapter/source label overriding the JSON source",
    )
    p_uvm_item.add_argument(
        "--output",
        default=".zddv/uvm/items/latest.json",
        help="Normalized UVM item JSON report path",
    )
    p_uvm_item.add_argument(
        "--show",
        type=int,
        default=20,
        help="Maximum number of item-handshake violations to print",
    )
    p_uvm_item.add_argument(
        "--max-bypass",
        type=int,
        default=None,
        help=(
            "Optional user policy limiting competing grants while an explicit "
            "ARB_REQUEST waits"
        ),
    )
    p_uvm_item.set_defaults(func=cmd_uvm_item_analyze)

    p_uvm_item_log = sub.add_parser(
        "uvm-item-log-analyze",
        help="Analyze explicit ZDDV_UVM_ITEM JSON markers from a simulator/UVM log",
    )
    p_uvm_item_log.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Simulation/UVM log file; optional when --run is supplied",
    )
    p_uvm_item_log.add_argument(
        "--run",
        dest="run_id",
        default=None,
        help="Recorded ZDDV run ID; uses its simulation log when path is omitted",
    )
    p_uvm_item_log.add_argument(
        "--source",
        default=None,
        help="Optional adapter/source label overriding the log-marker source",
    )
    p_uvm_item_log.add_argument(
        "--output",
        default=".zddv/uvm/items/latest.json",
        help="Normalized UVM item JSON report path",
    )
    p_uvm_item_log.add_argument(
        "--show",
        type=int,
        default=20,
        help="Maximum number of item-handshake violations to print",
    )
    p_uvm_item_log.set_defaults(func=cmd_uvm_item_log_analyze)

    p_uvm_item_history = sub.add_parser(
        "uvm-item-history",
        help="Show persisted UVM sequence-item handshake snapshots",
    )
    p_uvm_item_history.add_argument("--limit", type=int, default=20)
    p_uvm_item_history.add_argument(
        "--status",
        choices=("PASS", "FAIL"),
        default=None,
    )
    p_uvm_item_history.add_argument(
        "--run",
        dest="run_id",
        default=None,
        help="Filter item-handshake snapshots linked to a recorded ZDDV run ID",
    )
    p_uvm_item_history.set_defaults(func=cmd_uvm_item_history)

    p_uvm_item_violations = sub.add_parser(
        "uvm-item-violations",
        help="Show persisted violations for one UVM item-handshake snapshot",
    )
    p_uvm_item_violations.add_argument(
        "snapshot_id",
        help="Persisted UVM item-handshake snapshot ID",
    )
    p_uvm_item_violations.add_argument("--limit", type=int, default=100)
    p_uvm_item_violations.add_argument(
        "--code",
        default=None,
        help="Filter by exact violation code, e.g. LATE_GRANT",
    )
    p_uvm_item_violations.add_argument(
        "--item",
        dest="item_id",
        default=None,
        help="Filter by exact normalized item ID",
    )
    p_uvm_item_violations.set_defaults(func=cmd_uvm_item_violations)

    p_uvm_arbitration = sub.add_parser(
        "uvm-arbitration-analyze",
        help="Analyze explicit UVM arbitration decisions and project-defined fairness bounds",
    )
    p_uvm_arbitration.add_argument(
        "path",
        help="Normalized UVM arbitration decision JSON file",
    )
    p_uvm_arbitration.add_argument(
        "--run",
        dest="run_id",
        default=None,
        help="Optional recorded ZDDV run ID to correlate with this arbitration snapshot",
    )
    p_uvm_arbitration.add_argument(
        "--source",
        default=None,
        help="Optional adapter/source label overriding the JSON source",
    )
    p_uvm_arbitration.add_argument(
        "--fairness-bound",
        type=int,
        default=None,
        help="Override JSON fairness_bound with a maximum observed losing-decision count",
    )
    p_uvm_arbitration.add_argument(
        "--output",
        default=".zddv/uvm/arbitration/latest.json",
        help="Normalized UVM arbitration JSON report path",
    )
    p_uvm_arbitration.add_argument(
        "--show",
        type=int,
        default=20,
        help="Maximum number of arbitration/fairness violations to print",
    )
    p_uvm_arbitration.set_defaults(func=cmd_uvm_arbitration_analyze)

    p_uvm_arbitration_log = sub.add_parser(
        "uvm-arbitration-log-analyze",
        help="Analyze explicit ZDDV_UVM_ARBITRATION JSON markers from a simulator/UVM log",
    )
    p_uvm_arbitration_log.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Simulation/UVM log file; optional when --run is supplied",
    )
    p_uvm_arbitration_log.add_argument(
        "--run",
        dest="run_id",
        default=None,
        help="Recorded ZDDV run ID; uses its simulation log when path is omitted",
    )
    p_uvm_arbitration_log.add_argument(
        "--source",
        default=None,
        help="Optional adapter/source label overriding the log-marker source",
    )
    p_uvm_arbitration_log.add_argument(
        "--fairness-bound",
        type=int,
        default=None,
        help="Optional maximum observed losing-decision count for an explicit request",
    )
    p_uvm_arbitration_log.add_argument(
        "--output",
        default=".zddv/uvm/arbitration/latest.json",
        help="Normalized UVM arbitration JSON report path",
    )
    p_uvm_arbitration_log.add_argument(
        "--show",
        type=int,
        default=20,
        help="Maximum number of arbitration/fairness violations to print",
    )
    p_uvm_arbitration_log.set_defaults(func=cmd_uvm_arbitration_log_analyze)

    p_uvm_arbitration_history = sub.add_parser(
        "uvm-arbitration-history",
        help="Show persisted UVM arbitration/fairness snapshots",
    )
    p_uvm_arbitration_history.add_argument("--limit", type=int, default=20)
    p_uvm_arbitration_history.add_argument(
        "--status",
        choices=("PASS", "FAIL"),
        default=None,
    )
    p_uvm_arbitration_history.add_argument(
        "--run",
        dest="run_id",
        default=None,
        help="Filter arbitration snapshots linked to a recorded ZDDV run ID",
    )
    p_uvm_arbitration_history.set_defaults(func=cmd_uvm_arbitration_history)

    p_uvm_sequence_instrument = sub.add_parser(
        "uvm-sequence-instrument",
        help="Generate the portable ZDDV_UVM_SEQUENCE SystemVerilog trace helper",
    )
    p_uvm_sequence_instrument.add_argument(
        "--output",
        default="tb/zddv_uvm_sequence_trace_pkg.sv",
        help="Generated SystemVerilog helper path",
    )
    p_uvm_sequence_instrument.add_argument(
        "--no-add-source",
        dest="add_source",
        action="store_false",
        help="Do not add the generated helper to testbench source order",
    )
    p_uvm_sequence_instrument.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing generated helper",
    )
    p_uvm_sequence_instrument.set_defaults(
        func=cmd_uvm_sequence_instrument,
        add_source=True,
    )

    p_uvm_sequence = sub.add_parser(
        "uvm-sequence-analyze",
        help="Analyze normalized UVM sequence state lifecycle events from JSON",
    )
    p_uvm_sequence.add_argument(
        "path",
        help="Normalized UVM sequence state lifecycle event JSON file",
    )
    p_uvm_sequence.add_argument(
        "--run",
        dest="run_id",
        default=None,
        help="Optional recorded ZDDV run ID to correlate with this sequence snapshot",
    )
    p_uvm_sequence.add_argument(
        "--source",
        default=None,
        help="Optional adapter/source label overriding the JSON source",
    )
    p_uvm_sequence.add_argument(
        "--output",
        default=".zddv/uvm/sequences/latest.json",
        help="Normalized UVM sequence JSON report path",
    )
    p_uvm_sequence.add_argument(
        "--show",
        type=int,
        default=20,
        help="Maximum number of lifecycle violations to print",
    )
    p_uvm_sequence.set_defaults(func=cmd_uvm_sequence_analyze)

    p_uvm_sequence_log = sub.add_parser(
        "uvm-sequence-log-analyze",
        help="Analyze explicit ZDDV_UVM_SEQUENCE JSON markers from a simulator/UVM log",
    )
    p_uvm_sequence_log.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Simulation/UVM log file; optional when --run is supplied",
    )
    p_uvm_sequence_log.add_argument(
        "--run",
        dest="run_id",
        default=None,
        help="Recorded ZDDV run ID; uses its simulation log when path is omitted",
    )
    p_uvm_sequence_log.add_argument(
        "--source",
        default=None,
        help="Optional adapter/source label overriding the log-marker source",
    )
    p_uvm_sequence_log.add_argument(
        "--output",
        default=".zddv/uvm/sequences/latest.json",
        help="Normalized UVM sequence JSON report path",
    )
    p_uvm_sequence_log.add_argument(
        "--show",
        type=int,
        default=20,
        help="Maximum number of lifecycle violations to print",
    )
    p_uvm_sequence_log.set_defaults(func=cmd_uvm_sequence_log_analyze)

    p_uvm_sequence_history = sub.add_parser(
        "uvm-sequence-history",
        help="Show persisted UVM sequence state lifecycle snapshots",
    )
    p_uvm_sequence_history.add_argument("--limit", type=int, default=20)
    p_uvm_sequence_history.add_argument(
        "--status",
        choices=("PASS", "FAIL"),
        default=None,
    )
    p_uvm_sequence_history.add_argument(
        "--run",
        dest="run_id",
        default=None,
        help="Filter sequence lifecycle snapshots linked to a recorded ZDDV run ID",
    )
    p_uvm_sequence_history.set_defaults(func=cmd_uvm_sequence_history)

    p_async_fifo = sub.add_parser(
        "async-fifo-analyze",
        help="Check normalized async-FIFO/CDC pointer and blocking invariants",
    )
    p_async_fifo.add_argument(
        "path",
        help="Normalized asynchronous FIFO CDC event trace JSON file",
    )
    p_async_fifo.add_argument(
        "--output",
        default=".zddv/cdc/async-fifo/latest.json",
        help="JSON CDC analysis report path",
    )
    p_async_fifo.add_argument(
        "--show",
        type=int,
        default=20,
        help="Maximum number of invariant violations to print",
    )
    p_async_fifo.set_defaults(func=cmd_async_fifo_analyze)

    p_apb = sub.add_parser(
        "apb-analyze",
        help="Reconstruct APB transactions and report protocol violations",
    )
    p_apb.add_argument("path", help="Normalized APB trace JSON file")
    p_apb.add_argument(
        "--output",
        default=".zddv/protocols/apb/latest.json",
        help="JSON protocol-analysis report path",
    )
    p_apb.add_argument(
        "--show",
        type=int,
        default=20,
        help="Maximum number of protocol violations to print",
    )
    p_apb.set_defaults(func=cmd_apb_analyze)

    p_axi4lite = sub.add_parser(
        "axi4lite-analyze",
        help="Reconstruct AXI4-Lite transactions and report protocol violations",
    )
    p_axi4lite.add_argument("path", help="Normalized AXI4-Lite trace JSON file")
    p_axi4lite.add_argument(
        "--output",
        default=".zddv/protocols/axi4lite/latest.json",
        help="JSON protocol-analysis report path",
    )
    p_axi4lite.add_argument(
        "--show",
        type=int,
        default=20,
        help="Maximum number of protocol violations to print",
    )
    p_axi4lite.set_defaults(func=cmd_axi4lite_analyze)

    p_axi4 = sub.add_parser(
        "axi4-analyze",
        help="Reconstruct burst-aware AXI4 transactions and report protocol violations",
    )
    p_axi4.add_argument("path", help="Normalized AXI4 trace JSON file")
    p_axi4.add_argument(
        "--output",
        default=".zddv/protocols/axi4/latest.json",
        help="JSON protocol-analysis report path",
    )
    p_axi4.add_argument(
        "--show",
        type=int,
        default=20,
        help="Maximum number of protocol violations to print",
    )
    p_axi4.set_defaults(func=cmd_axi4_analyze)

    p_ucie = sub.add_parser(
        "ucie-analyze",
        help="Analyze a normalized public UCIe FLIT trace and link-health evidence",
    )
    p_ucie.add_argument("path", help="Normalized UCIe FLIT trace JSON file")
    p_ucie.add_argument(
        "--output",
        default=".zddv/protocols/ucie/latest.json",
        help="JSON UCIe trace-analysis report path",
    )
    p_ucie.add_argument(
        "--show",
        type=int,
        default=20,
        help="Maximum number of normalized trace violations to print",
    )
    p_ucie.set_defaults(func=cmd_ucie_analyze)

    p_axi4_waveform = sub.add_parser(
        "axi4-waveform",
        help="Extract and analyze burst-aware AXI4 transactions directly from a VCD waveform",
    )
    axi4_waveform_source = p_axi4_waveform.add_mutually_exclusive_group()
    axi4_waveform_source.add_argument(
        "--run",
        dest="run_id",
        default=None,
        help="Run ID whose waveform should be decoded; defaults to latest waveform run",
    )
    axi4_waveform_source.add_argument(
        "--input",
        default=None,
        help="VCD path relative to the project, independent of run history",
    )
    p_axi4_waveform.add_argument(
        "--scope",
        default=None,
        help="AXI4 waveform scope; auto-detected when exactly one complete bus exists",
    )
    p_axi4_waveform.add_argument(
        "--clock",
        default="ACLK",
        help="AXI4 clock signal name inside the selected scope",
    )
    p_axi4_waveform.add_argument(
        "--edge",
        choices=("rising", "falling", "both"),
        default="rising",
        help="Clock edge used to sample AXI4 signals",
    )
    p_axi4_waveform.add_argument(
        "--trace-output",
        default=".zddv/protocols/axi4/waveform-trace.json",
        help="Normalized AXI4 trace generated from the waveform",
    )
    p_axi4_waveform.add_argument(
        "--output",
        default=".zddv/protocols/axi4/waveform-latest.json",
        help="JSON AXI4 protocol-analysis report path",
    )
    p_axi4_waveform.add_argument(
        "--show",
        type=int,
        default=20,
        help="Maximum number of protocol violations to print",
    )
    p_axi4_waveform.set_defaults(func=cmd_axi4_waveform)

    p_apb_waveform = sub.add_parser(
        "apb-waveform",
        help="Extract and analyze APB transactions directly from a VCD waveform",
    )
    apb_waveform_source = p_apb_waveform.add_mutually_exclusive_group()
    apb_waveform_source.add_argument(
        "--run",
        dest="run_id",
        default=None,
        help="Run ID whose waveform should be decoded; defaults to latest waveform run",
    )
    apb_waveform_source.add_argument(
        "--input",
        default=None,
        help="VCD path relative to the project, independent of run history",
    )
    p_apb_waveform.add_argument(
        "--scope",
        default=None,
        help="APB waveform scope; auto-detected when exactly one matching scope exists",
    )
    p_apb_waveform.add_argument(
        "--clock",
        default="PCLK",
        help="APB clock signal name inside the selected scope",
    )
    p_apb_waveform.add_argument(
        "--edge",
        choices=("rising", "falling", "both"),
        default="rising",
        help="Clock edge used to sample APB signals",
    )
    p_apb_waveform.add_argument(
        "--trace-output",
        default=".zddv/protocols/apb/waveform-trace.json",
        help="Normalized APB trace generated from the waveform",
    )
    p_apb_waveform.add_argument(
        "--output",
        default=".zddv/protocols/apb/waveform-latest.json",
        help="JSON protocol-analysis report path",
    )
    p_apb_waveform.add_argument(
        "--show",
        type=int,
        default=20,
        help="Maximum number of protocol violations to print",
    )
    p_apb_waveform.set_defaults(func=cmd_apb_waveform)

    p_axi4lite_waveform = sub.add_parser(
        "axi4lite-waveform",
        help="Extract and analyze AXI4-Lite transactions directly from a VCD waveform",
    )
    axi4lite_waveform_source = p_axi4lite_waveform.add_mutually_exclusive_group()
    axi4lite_waveform_source.add_argument(
        "--run",
        dest="run_id",
        default=None,
        help="Run ID whose waveform should be decoded; defaults to latest waveform run",
    )
    axi4lite_waveform_source.add_argument(
        "--input",
        default=None,
        help="VCD path relative to the project, independent of run history",
    )
    p_axi4lite_waveform.add_argument(
        "--scope",
        default=None,
        help="AXI4-Lite waveform scope; auto-detected when exactly one complete bus exists",
    )
    p_axi4lite_waveform.add_argument(
        "--clock",
        default="ACLK",
        help="AXI4-Lite clock signal name inside the selected scope",
    )
    p_axi4lite_waveform.add_argument(
        "--edge",
        choices=("rising", "falling", "both"),
        default="rising",
        help="Clock edge used to sample AXI4-Lite signals",
    )
    p_axi4lite_waveform.add_argument(
        "--trace-output",
        default=".zddv/protocols/axi4lite/waveform-trace.json",
        help="Normalized AXI4-Lite trace generated from the waveform",
    )
    p_axi4lite_waveform.add_argument(
        "--output",
        default=".zddv/protocols/axi4lite/waveform-latest.json",
        help="JSON protocol-analysis report path",
    )
    p_axi4lite_waveform.add_argument(
        "--show",
        type=int,
        default=20,
        help="Maximum number of protocol violations to print",
    )
    p_axi4lite_waveform.set_defaults(func=cmd_axi4lite_waveform)

    p_assertions = sub.add_parser(
        "assertions",
        help="Show normalized assertion result history",
    )
    p_assertions.add_argument("--limit", type=int, default=100)
    p_assertions.add_argument(
        "--status",
        choices=("PASS", "FAIL"),
        default=None,
        help="Optional assertion status filter",
    )
    p_assertions.add_argument(
        "--name",
        default=None,
        help="Optional exact assertion name filter",
    )
    p_assertions.set_defaults(func=cmd_assertions)

    p_runs = sub.add_parser("runs", help="Show verification run history")
    p_runs.add_argument("--limit", type=int, default=20)
    p_runs.add_argument(
        "--status",
        choices=("PASS", "FAIL", "TIMEOUT"),
        default=None,
    )
    p_runs.set_defaults(func=cmd_runs)

    p_rerun = sub.add_parser(
        "rerun",
        help="Rerun selected historical verification runs",
    )
    p_rerun.add_argument("--limit", type=int, default=20)
    p_rerun.add_argument(
        "--status",
        action="append",
        choices=("PASS", "FAIL", "TIMEOUT"),
        default=None,
        help="Historical status to select; repeat as needed. Defaults to FAIL and TIMEOUT.",
    )
    p_rerun.set_defaults(func=cmd_rerun)

    p_junit = sub.add_parser(
        "junit",
        help="Export verification run history as JUnit XML",
    )
    p_junit.add_argument("--output", default=".zddv/junit.xml")
    p_junit.add_argument("--limit", type=int, default=100)
    p_junit.add_argument(
        "--status",
        action="append",
        choices=("PASS", "FAIL", "TIMEOUT"),
        default=None,
        help="Optional status filter; repeat as needed.",
    )
    p_junit.set_defaults(func=cmd_junit)

    p_failures = sub.add_parser(
        "failures",
        help="Group historical failures by normalized signature",
    )
    p_failures.add_argument("--limit", type=int, default=200)
    p_failures.add_argument("--show", type=int, default=20)
    p_failures.add_argument(
        "--output",
        default=".zddv/failure-groups.json",
        help="JSON report path",
    )
    p_failures.set_defaults(func=cmd_failures)

    p_report = sub.add_parser(
        "report",
        help="Generate an HTML verification dashboard from run history",
    )
    p_report.add_argument("--limit", type=int, default=100)
    p_report.set_defaults(func=cmd_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (FileNotFoundError, FileExistsError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
