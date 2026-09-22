from __future__ import annotations

from datetime import datetime, timezone
import math
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid

from zddv.config import ProjectConfig

from .base import FormalBackend, FormalCheckRequest, FormalCheckResult


_SBY_RESULT_RE = re.compile(
    r"(?:Status returned by engine(?: for [^:\n]+)?\s*:|"
    r"\bDONE\s*\(|\bstatus\s*=)\s*"
    r"(?P<status>PASS|FAIL|UNKNOWN|ERROR|TIMEOUT)\b",
    re.IGNORECASE,
)
_SAFE_TOP_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_FORMAL_SOURCE_SUFFIXES = {".v", ".sv"}
_SBY_DEFAULT_DEPTH = 20


def parse_sby_status(text: str, *, returncode: int) -> str:
    """Conservatively map SBY output into ZDDV's run-level formal status."""
    matches = list(_SBY_RESULT_RE.finditer(text or ""))
    if matches:
        status = matches[-1].group("status").upper()
        return "UNKNOWN" if status == "TIMEOUT" else status
    return "ERROR" if returncode != 0 else "UNKNOWN"


class SymbiYosysBackend(FormalBackend):
    """Initial SBY execution adapter using the documented smtbmc engine."""

    name = "symbiyosys"
    engine = "smtbmc"

    def _tool(self) -> str:
        tool = shutil.which("sby")
        if tool is None:
            raise RuntimeError(
                "SymbiYosys 'sby' was not found in PATH. Install SBY and retry."
            )
        return tool

    def version(self) -> str:
        completed = subprocess.run(
            [self._tool(), "--version"],
            check=False,
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                completed.stderr.strip()
                or completed.stdout.strip()
                or "Unable to query SymbiYosys."
            )
        return completed.stdout.strip() or completed.stderr.strip()

    @staticmethod
    def _validate_top(top: str) -> str:
        value = str(top).strip()
        if not _SAFE_TOP_RE.fullmatch(value):
            raise RuntimeError(
                "SymbiYosys backend currently requires a simple Verilog/SystemVerilog "
                "top-module identifier."
            )
        return value

    @staticmethod
    def _source_entries(project: ProjectConfig) -> list[tuple[str, Path]]:
        sources = project.source_files()
        if not sources:
            raise RuntimeError("No RTL/testbench sources matched the project configuration.")

        entries: list[tuple[str, Path]] = []
        for index, source in enumerate(sources):
            path = source.resolve()
            suffix = path.suffix.lower()
            if suffix not in _FORMAL_SOURCE_SUFFIXES:
                raise RuntimeError(
                    "SymbiYosys backend currently accepts only .v/.sv compilation units; "
                    f"unsupported source: {path}"
                )
            if any(ch.isspace() for ch in str(path)):
                raise RuntimeError(
                    "SymbiYosys backend currently requires source paths without whitespace: "
                    f"{path}"
                )
            entries.append((f"src_{index:04d}{suffix}", path))
        return entries

    @staticmethod
    def _effective_request(request: FormalCheckRequest) -> FormalCheckRequest:
        if request.properties:
            raise RuntimeError(
                "SymbiYosys named property filtering is not implemented yet; "
                "run without FormalCheckRequest.properties."
            )
        if request.mode not in {"bmc", "prove", "cover"}:
            raise RuntimeError(f"Unsupported SymbiYosys mode: {request.mode}")
        return FormalCheckRequest(
            mode=request.mode,
            depth=request.depth if request.depth is not None else _SBY_DEFAULT_DEPTH,
            properties=(),
            timeout_s=request.timeout_s,
        )

    def render_config(
        self,
        project: ProjectConfig,
        request: FormalCheckRequest,
    ) -> tuple[str, FormalCheckRequest]:
        effective = self._effective_request(request)
        top = self._validate_top(project.top)
        entries = self._source_entries(project)

        option_lines = [f"mode {effective.mode}", f"depth {effective.depth}"]
        if effective.timeout_s is not None:
            option_lines.append(f"timeout {max(1, math.ceil(effective.timeout_s))}")

        aliases = " ".join(alias for alias, _ in entries)
        file_lines = [f"{alias} {path}" for alias, path in entries]
        text = "\n".join(
            [
                "[options]",
                *option_lines,
                "",
                "[engines]",
                self.engine,
                "",
                "[script]",
                f"read -formal {aliases}",
                f"prep -top {top}",
                "",
                "[files]",
                *file_lines,
                "",
            ]
        )
        return text, effective

    @staticmethod
    def _artifacts(config_path: Path, work_dir: Path) -> tuple[Path, ...]:
        artifacts: list[Path] = [config_path]
        if work_dir.is_dir():
            for pattern in ("trace.vcd", "trace.fst", "trace.smtc", "trace_tb.v"):
                artifacts.extend(sorted(work_dir.rglob(pattern)))
        seen: set[Path] = set()
        ordered: list[Path] = []
        for path in artifacts:
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                ordered.append(resolved)
        return tuple(ordered)

    def check(
        self,
        project: ProjectConfig,
        request: FormalCheckRequest,
    ) -> FormalCheckResult:
        config_text, effective = self.render_config(project, request)

        now = datetime.now(timezone.utc)
        run_id = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
        run_dir = (project.root / ".zddv" / "formal" / self.name / run_id).resolve()
        run_dir.mkdir(parents=True, exist_ok=False)
        config_path = run_dir / "zddv.sby"
        log_path = run_dir / "formal.log"
        work_dir = run_dir / "work"
        config_path.write_text(config_text, encoding="utf-8")

        command = (
            self._tool(),
            "-f",
            "-d",
            str(work_dir),
            str(config_path),
        )

        timed_out = False
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                list(command),
                cwd=project.root,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=effective.timeout_s,
            )
            returncode = completed.returncode
            output = completed.stdout or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            returncode = 124
            output = exc.stdout or ""
            if isinstance(output, bytes):
                output = output.decode(errors="replace")
            output += f"\nZDDV_FORMAL_TIMEOUT after {effective.timeout_s} seconds\n"

        runtime_ms = (time.perf_counter() - started) * 1000.0
        log_path.write_text(output, encoding="utf-8")
        status = "UNKNOWN" if timed_out else parse_sby_status(output, returncode=returncode)

        return FormalCheckResult(
            backend=self.name,
            engine=self.engine,
            request=effective,
            command=command,
            returncode=returncode,
            status=status,
            run_dir=run_dir,
            log_path=log_path,
            properties=(),
            artifacts=self._artifacts(config_path, work_dir),
            runtime_ms=runtime_ms,
        )
