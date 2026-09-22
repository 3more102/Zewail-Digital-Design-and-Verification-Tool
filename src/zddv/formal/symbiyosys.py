from __future__ import annotations

from datetime import datetime, timezone
import math
from pathlib import Path
import re
import shutil
import subprocess
import uuid

from zddv.config import ProjectConfig
from zddv.formal.base import FormalBackend, FormalCheckRequest, FormalCheckResult


_DONE_RE = re.compile(
    r"\bDONE\s*\((PASS|FAIL|UNKNOWN|ERROR|TIMEOUT)(?:,\s*rc=\d+)?\)",
    re.IGNORECASE,
)
_TOP_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_FORMAL_SOURCE_SUFFIXES = {".v", ".sv"}


class SymbiYosysBackend(FormalBackend):
    """Evidence-first SymbiYosys execution adapter.

    This first execution slice intentionally normalizes only task-level SBY
    status. Per-property results remain empty until SBY exposes sufficiently
    explicit property evidence for a dedicated ingestion layer.
    """

    name = "symbiyosys"

    def __init__(self, *, engine: str = "smtbmc") -> None:
        normalized = str(engine).strip()
        if not normalized:
            raise ValueError("SymbiYosys engine must not be empty")
        self.engine = normalized

    def _tool(self) -> str:
        tool = shutil.which("sby")
        if tool is None:
            raise RuntimeError(
                "SymbiYosys 'sby' was not found in PATH. "
                "Install SymbiYosys/OSS CAD Suite and retry."
            )
        return tool

    def version(self) -> str:
        completed = subprocess.run(
            [self._tool(), "--version"],
            check=False,
            text=True,
            capture_output=True,
        )
        output = (completed.stdout or completed.stderr or "").strip()
        if completed.returncode != 0:
            raise RuntimeError(output or "Unable to query SymbiYosys version.")
        return output

    @staticmethod
    def _safe_run_id(mode: str) -> str:
        now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"{now}-{mode}-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _quote_yosys_path(path: Path) -> str:
        value = path.resolve().as_posix().replace("\\", "\\\\").replace('"', '\\"')
        return f'"{value}"'

    @staticmethod
    def _formal_sources(project: ProjectConfig) -> list[Path]:
        return [
            path
            for path in project.source_files()
            if path.suffix.lower() in _FORMAL_SOURCE_SUFFIXES
        ]

    def _config_text(
        self,
        project: ProjectConfig,
        request: FormalCheckRequest,
        sources: list[Path],
    ) -> str:
        if not _TOP_RE.fullmatch(project.top):
            raise ValueError(
                "SymbiYosys backend currently requires a simple Verilog/SystemVerilog "
                "top identifier"
            )

        lines = [
            "[options]",
            f"mode {request.mode}",
            "expect pass,fail,unknown,error,timeout",
        ]
        if request.depth is not None:
            lines.append(f"depth {request.depth}")
        if request.timeout_s is not None:
            lines.append(f"timeout {max(1, math.ceil(request.timeout_s))}")

        lines.extend(
            [
                "",
                "[engines]",
                self.engine,
                "",
                "[script]",
            ]
        )
        lines.extend(
            f"read -formal {self._quote_yosys_path(path)}"
            for path in sources
        )
        lines.append(f"prep -top {project.top}")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _status_from_output(output: str) -> str:
        matches = list(_DONE_RE.finditer(output))
        if not matches:
            return "ERROR"
        status = matches[-1].group(1).upper()
        if status == "TIMEOUT":
            return "UNKNOWN"
        return status

    @staticmethod
    def _artifacts(config_path: Path, run_dir: Path) -> tuple[Path, ...]:
        found: list[Path] = [config_path]
        for name in ("status", "logfile.txt", "config.sby"):
            candidate = run_dir / name
            if candidate.is_file():
                found.append(candidate)
        for pattern in (
            "**/trace.vcd",
            "**/trace.fst",
            "**/trace.yw",
            "**/trace.smtc",
            "**/trace_tb.v",
        ):
            found.extend(path for path in run_dir.glob(pattern) if path.is_file())

        ordered: list[Path] = []
        seen: set[Path] = set()
        for path in found:
            resolved = path.resolve()
            if resolved not in seen:
                ordered.append(resolved)
                seen.add(resolved)
        return tuple(ordered)

    def check(
        self,
        project: ProjectConfig,
        request: FormalCheckRequest,
    ) -> FormalCheckResult:
        if request.properties:
            raise NotImplementedError(
                "SymbiYosys property filtering is not normalized yet; "
                "run the complete formal property set instead."
            )

        sources = self._formal_sources(project)
        if not sources:
            raise RuntimeError(
                "No .v/.sv sources matched the project configuration for formal checking."
            )

        run_id = self._safe_run_id(request.mode)
        formal_root = (project.root / ".zddv" / "formal").resolve()
        configs_dir = formal_root / "configs"
        run_dir = formal_root / "runs" / run_id
        configs_dir.mkdir(parents=True, exist_ok=True)

        config_path = configs_dir / f"{run_id}.sby"
        config_path.write_text(
            self._config_text(project, request, sources),
            encoding="utf-8",
        )

        command = [
            self._tool(),
            "-f",
            "-d",
            str(run_dir),
            str(config_path),
        ]
        completed = subprocess.run(
            command,
            cwd=project.root,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        output = completed.stdout or ""

        run_dir.mkdir(parents=True, exist_ok=True)
        log_path = run_dir / "formal.log"
        log_path.write_text(output, encoding="utf-8")

        return FormalCheckResult(
            backend=self.name,
            engine=self.engine,
            request=request,
            command=tuple(command),
            returncode=completed.returncode,
            status=self._status_from_output(output),
            run_dir=run_dir,
            log_path=log_path,
            properties=(),
            artifacts=self._artifacts(config_path, run_dir),
        )
