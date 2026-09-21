from __future__ import annotations

import argparse
from pathlib import Path
import platform
import sys

from zddv import __version__
from zddv.config import initialize_project, load_project, save_project
from zddv.coverage import (
    merge_verilator_coverage,
    parse_verilator_coverage,
    write_coverage_hole_report,
)
from zddv.dashboard import generate_html_report
from zddv.lint import lint_project
from zddv.regression import run_regression
from zddv.reporting import write_junit_report
from zddv.simulator import VerilatorBackend
from zddv.storage import (
    assertion_statistics,
    database_path,
    list_assertion_events,
    list_coverage_snapshots,
    list_run_records,
    list_runs,
)
from zddv.triage import group_failure_records, write_failure_report


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
    if result.assertion_count:
        print(
            f"Assertions: {result.assertion_count} event(s), "
            f"{result.assertion_failures} failed"
        )
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


def cmd_assertions(args) -> int:
    project = load_project(_project_arg(args))
    stats = assertion_statistics(project)
    rows = list_assertion_events(
        project,
        limit=args.limit,
        status=args.status,
        run_id=args.run_id,
    )
    print(
        f"ASSERTIONS: {stats['total']} event(s), "
        f"{stats['failed']} failed, {stats['passed']} passed, "
        f"{stats['named_properties']} named properties"
    )
    if not rows:
        print("No assertion events found.")
        return 0

    print(
        f"{'STATUS':<7} {'PROPERTY':<24} {'TEST':<18} "
        f"{'SEED':<8} {'SOURCE':<28} MESSAGE"
    )
    for row in rows:
        prop = str(row["property_name"] or "-")
        test = str(row["test_name"] or "-")
        seed = "-" if row["seed"] is None else str(row["seed"])
        source = "-"
        if row["source_file"]:
            source = str(row["source_file"])
            if row["source_line"] is not None:
                source += f":{row['source_line']}"
        print(
            f"{row['status']:<7} {prop[:24]:<24} {test[:18]:<18} "
            f"{seed:<8} {source[-28:]:<28} {str(row['message'] or '-')}"
        )
    return 0


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

    p_assertions = sub.add_parser(
        "assertions",
        help="Show normalized assertion events from verification runs",
    )
    p_assertions.add_argument("--limit", type=int, default=100)
    p_assertions.add_argument("--status", choices=("PASS", "FAIL"), default=None)
    p_assertions.add_argument("--run-id", default=None, help="Optional run ID filter")
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
