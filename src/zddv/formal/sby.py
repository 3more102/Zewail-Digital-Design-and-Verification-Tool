from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import math
import re
import shutil
import subprocess
import time

from zddv.config import ProjectConfig

from .base import FormalBackend, FormalCheckRequest, FormalCheckResult


_SBY_DONE = re.compile(
    r"\bDONE\s*\(\s*(?P<status>[A-Z_]+)\s*,\s*rc=(?P<rc>-?\d+)\s*\)",
    re.IGNORECASE,
)
_HDL_SUFFIXES = {".v", ".sv"}
_AUX_SUFFIXES = {".vh", ".svh"}


@dataclass(frozen=True)
class _ProcessOutcome:
    returncode: int
    output: str
    timed_out: bool = False


def _run_process(
    command: list[str],
    *,
    cwd: Path,
    timeout_s: float | None,
) -> _ProcessOutcome:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        return _ProcessOutcome(
            returncode=int(completed.returncode),
            output=completed.stdout or "",
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        return _ProcessOutcome(returncode=-1, output=str(output), timed_out=True)


def _staged_source_name(project: ProjectConfig, source: Path, index: int) -> str:
    root = project.root.resolve()
    resolved = source.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        relative = Path("external") / f"{index:04d}_{resolved.name}"

    parts = []
    for part in relative.parts:
        clean = re.sub(r"[^A-Za-z0-9_.-]", "_", part)
        parts.append(clean or "_")
    return Path(*parts).as_posix()


def render_sby_bmc_config(
    project: ProjectConfig,
    request: FormalCheckRequest,
) -> str:
    """Render one bounded SymbiYosys job without inferring proof semantics."""
    if request.mode != "bmc":
        raise ValueError("SymbiYosys bounded execution currently supports bmc mode only")
    if request.depth is None:
        raise ValueError("SymbiYosys bounded checks require an explicit depth")
    if request.properties:
        raise ValueError(
            "SymbiYosys property filters are not supported by the bounded backend yet"
        )

    sources = project.source_files()
    if not sources:
        raise RuntimeError("No project sources were found for formal checking")

    staged: list[tuple[Path, str]] = []
    seen_destinations: set[str] = set()
    for index, source in enumerate(sources):
        destination = _staged_source_name(project, source, index)
        if destination in seen_destinations:
            destination = f"source_{index:04d}_{Path(destination).name}"
        seen_destinations.add(destination)
        staged.append((source.resolve(), destination))

    hdl = [
        (source, destination)
        for source, destination in staged
        if source.suffix.lower() in _HDL_SUFFIXES
    ]
    if not hdl:
        raise RuntimeError("No .v or .sv sources were found for formal checking")

    script_lines: list[str] = []
    for source, destination in hdl:
        if source.suffix.lower() == ".sv":
            script_lines.append(f"read_verilog -formal -sv {destination}")
        else:
            script_lines.append(f"read_verilog -formal {destination}")
    script_lines.append(f"prep -top {project.top}")

    file_lines = [
        f"{destination} {source.as_posix()}"
        for source, destination in staged
        if source.suffix.lower() in (_HDL_SUFFIXES | _AUX_SUFFIXES)
    ]

    return "\n".join(
        [
            "[options]",
            "mode bmc",
            f"depth {request.depth}",
            *(
                [f"timeout {max(1, math.ceil(request.timeout_s))}"]
                if request.timeout_s is not None
                else []
            ),
            "",
            "[engines]",
            "smtbmc",
            "",
            "[script]",
            *script_lines,
            "",
            "[files]",
            *file_lines,
            "",
        ]
    )


def _normalized_sby_status(outcome: _ProcessOutcome) -> str:
    if outcome.timed_out:
        return "UNKNOWN"

    matches = list(_SBY_DONE.finditer(outcome.output))
    if not matches:
        return "ERROR"

    status = matches[-1].group("status").upper()
    if status in {"PASS", "FAIL", "UNKNOWN", "ERROR"}:
        return status
    return "UNKNOWN"


class SymbiYosysBackend(FormalBackend):
    """First concrete ZDDV formal backend: finite-depth SymbiYosys BMC."""

    name = "sby"

    def _executable(self) -> str:
        executable = shutil.which("sby")
        if executable is None:
            raise RuntimeError("SymbiYosys 'sby' was not found in PATH")
        return executable

    def version(self) -> str:
        executable = self._executable()
        completed = subprocess.run(
            [executable, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        output = (completed.stdout or "").strip()
        if completed.returncode != 0:
            raise RuntimeError(
                f"Unable to query SymbiYosys version (rc={completed.returncode})"
            )
        return output.splitlines()[0] if output else "sby"

    def check(
        self,
        project: ProjectConfig,
        request: FormalCheckRequest,
    ) -> FormalCheckResult:
        executable = self._executable()
        config_text = render_sby_bmc_config(project, request)

        root = (project.root / ".zddv" / "formal" / "sby").resolve()
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        run_id = f"bmc-{stamp}"
        config_path = root / f"{run_id}.sby"
        run_dir = root / run_id
        config_path.write_text(config_text, encoding="utf-8")

        command = [
            executable,
            "-f",
            "-d",
            str(run_dir),
            str(config_path),
        ]
        started = time.perf_counter()
        outcome = _run_process(
            command,
            cwd=project.root,
            timeout_s=request.timeout_s,
        )
        runtime_ms = (time.perf_counter() - started) * 1000.0

        run_dir.mkdir(parents=True, exist_ok=True)
        log_path = run_dir / "formal.log"
        output = outcome.output
        if outcome.timed_out:
            if output and not output.endswith("\n"):
                output += "\n"
            output += "ZDDV: formal check timed out before SymbiYosys completed\n"
        log_path.write_text(output, encoding="utf-8")

        status = _normalized_sby_status(outcome)
        properties = ()
        native_artifacts: tuple[Path, ...] = ()
        engine = "smtbmc"

        if not outcome.timed_out and _SBY_DONE.search(output):
            from .sby_results import parse_sby_log

            try:
                native = parse_sby_log(
                    log_path,
                    mode=request.mode,
                    depth=request.depth,
                )
            except ValueError:
                native = None
            if native is not None:
                properties = native.properties
                native_artifacts = native.artifacts
                engine = native.engine or engine

        artifacts = tuple(dict.fromkeys((config_path, *native_artifacts)))
        return FormalCheckResult(
            backend=self.name,
            engine=engine,
            request=request,
            command=tuple(command),
            returncode=outcome.returncode,
            status=status,
            run_dir=run_dir,
            log_path=log_path,
            properties=properties,
            artifacts=artifacts,
            runtime_ms=runtime_ms,
        )
