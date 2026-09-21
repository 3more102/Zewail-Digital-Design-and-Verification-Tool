from __future__ import annotations

import argparse
from pathlib import Path
import platform
import sys

from zddv import __version__
from zddv.config import initialize_project, load_project, save_project
from zddv.simulator import VerilatorBackend


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
    return result.returncode or 1


def cmd_run(args) -> int:
    project = load_project(_project_arg(args))
    backend = _backend(project.simulator)
    result = backend.run(project)
    print(f"RUN {result.status}: {result.run_id}")
    print(f"Log: {result.log_path}")
    if result.waveform_path:
        print(f"Waveform: {result.waveform_path}")
    return result.returncode


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

    p_build = sub.add_parser("build", help="Compile/elaborate the configured project")
    p_build.set_defaults(func=cmd_build)

    p_run = sub.add_parser("run", help="Run the configured simulation")
    p_run.set_defaults(func=cmd_run)

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
