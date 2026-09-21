from __future__ import annotations

import argparse
from pathlib import Path
import platform
import sys

from zddv import __version__
from zddv.config import initialize_project, load_project, save_project
from zddv.crossprobe import write_crossprobe_report
from zddv.connectivity import signal_navigation, write_connectivity_index
from zddv.coverage import (
    merge_verilator_coverage,
    parse_verilator_coverage,
    write_coverage_hole_report,
)
from zddv.dashboard import generate_html_report
from zddv.design_index import hierarchy_lines, write_design_index
from zddv.elaboration import hierarchy_lines as elaborated_hierarchy_lines
from zddv.elaboration import write_elaborated_index
from zddv.debug import write_assertion_waveform_report
from zddv.functional_coverage import ingest_functional_coverage
from zddv.lint import lint_project
from zddv.protocols.apb import analyze_apb_file
from zddv.regression import run_regression
from zddv.reporting import write_junit_report
from zddv.simulator import VerilatorBackend
from zddv.storage import (
    assertion_statistics,
    database_path,
    list_assertion_events,
    list_coverage_snapshots,
    list_functional_coverage_bins,
    list_functional_coverage_snapshots,
    list_run_records,
    list_runs,
)
from zddv.triage import group_failure_records, write_failure_report
from zddv.waveform import write_waveform_index


def _backend(name: str):
    if name == "verilator":
        return VerilatorBackend()
    raise RuntimeError(f"Unsupported simulator backend: {name}")


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
    try:
        version = VerilatorBackend().version()
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
    if args.elaborated:
        result = write_elaborated_index(project)
        print(
            f"ELABORATED HIERARCHY: top={project.top} "
            f"format={result['source_format']}"
        )
        for line in elaborated_hierarchy_lines(result):
            print(line)
        print(f"Index: {result['path']}")
        return 0

    result = write_design_index(project)
    print(f"HIERARCHY: top={project.top}")
    for line in hierarchy_lines(result["hierarchy"]):
        print(line)
    print(f"Index: {result['path']}")
    return 0 if result["hierarchy"].get("resolved", False) else 1




def cmd_elaborate(args) -> int:
    project = load_project(_project_arg(args))
    result = write_elaborated_index(project)
    summary = result["summary"]
    print(
        f"ELABORATION: {summary['modules']} module(s), "
        f"{summary['instances']} instance(s)"
    )
    print(f"Simulator: {result['simulator_version']}")
    print(f"Format: {result['source_format']}")
    print(f"Index: {result['path']}")
    print(f"Hierarchy: {result['hierarchy_path']}")
    return 0


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
        print(f"BUILD PASS: {result.executable}")
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


def cmd_coverage(args) -> int:
    project = load_project(_project_arg(args))
    if project.simulator != "verilator":
        raise RuntimeError(
            "Coverage reporting is currently implemented for Verilator only."
        )
    result = merge_verilator_coverage(project)
    print(f"Coverage inputs: {len(result['inputs'])}")
    print(f"Merged coverage: {result['merged']}")
    print(f"Summary: {result['summary']}")
    metrics = result["metrics"]
    print(
        f"Coverage points: {metrics['hit_points']}/{metrics['total_points']} hit "
        f"({metrics['hit_rate']:.1f}%)"
    )
    print(f"Metrics: {result['metrics_path']}")
    print(f"Snapshot: {result['snapshot_id']}")
    report = result["report"].strip()
    if report:
        print(report)
    return 0


def cmd_coverage_history(args) -> int:
    project = load_project(_project_arg(args))
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
    merged_path = (project.root / ".zddv" / "coverage" / "coverage.dat").resolve()
    if not merged_path.exists():
        raise RuntimeError(
            f"Merged coverage not found at {merged_path}. Run 'zddv coverage' first."
        )

    points = parse_verilator_coverage(merged_path)
    if not points:
        raise RuntimeError(f"No normalized coverage points found in {merged_path}.")

    output = Path(args.output)
    if not output.is_absolute():
        output = project.root / output

    report = write_coverage_hole_report(
        points,
        output,
        point_type=args.point_type,
        limit=args.limit,
    )
    filter_label = args.point_type or "all"
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
    p_doctor.set_defaults(func=cmd_doctor)

    p_index = sub.add_parser(
        "index",
        help="Build the normalized source/design index",
    )
    p_index.set_defaults(func=cmd_index)

    p_hierarchy = sub.add_parser(
        "hierarchy",
        help="Build and print the design hierarchy",
    )
    p_hierarchy.add_argument(
        "--elaborated",
        action="store_true",
        help="Use simulator-resolved elaborated hierarchy instead of source-level hierarchy",
    )
    p_hierarchy.set_defaults(func=cmd_hierarchy)

    p_elaborate = sub.add_parser(
        "elaborate",
        help="Build the simulator-resolved normalized design hierarchy",
    )
    p_elaborate.set_defaults(func=cmd_elaborate)

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
