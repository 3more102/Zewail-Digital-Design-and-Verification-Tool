from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from zddv.config import ProjectConfig


_FORMAL_MODES = {"bmc", "prove", "cover"}
_FORMAL_RESULT_STATUSES = {"PASS", "FAIL", "UNKNOWN", "ERROR"}
_FORMAL_PROPERTY_STATUSES = {
    "assert": {"PASS", "FAIL", "UNKNOWN", "ERROR"},
    "cover": {"COVERED", "UNCOVERED", "UNKNOWN", "ERROR"},
}


@dataclass(frozen=True)
class FormalCheckRequest:
    """Simulator-independent request passed to a formal backend."""

    mode: str
    depth: int | None = None
    properties: tuple[str, ...] = ()
    timeout_s: float | None = None

    def __post_init__(self) -> None:
        mode = str(self.mode).strip().lower()
        if mode not in _FORMAL_MODES:
            supported = ", ".join(sorted(_FORMAL_MODES))
            raise ValueError(f"unsupported formal mode {self.mode!r}; expected {supported}")
        object.__setattr__(self, "mode", mode)

        if self.depth is not None and int(self.depth) < 1:
            raise ValueError("formal depth must be >= 1 when provided")
        if self.depth is not None:
            object.__setattr__(self, "depth", int(self.depth))

        properties = tuple(str(name).strip() for name in self.properties)
        if any(not name for name in properties):
            raise ValueError("formal property filters must not be empty")
        object.__setattr__(self, "properties", properties)

        if self.timeout_s is not None:
            timeout = float(self.timeout_s)
            if timeout <= 0:
                raise ValueError("formal timeout must be > 0 when provided")
            object.__setattr__(self, "timeout_s", timeout)


@dataclass(frozen=True)
class FormalPropertyResult:
    """Normalized result for one assertion or cover property."""

    name: str
    kind: str
    status: str
    depth: int | None = None
    trace_path: Path | None = None
    message: str | None = None

    def __post_init__(self) -> None:
        name = str(self.name).strip()
        if not name:
            raise ValueError("formal property name must not be empty")
        object.__setattr__(self, "name", name)

        kind = str(self.kind).strip().lower()
        if kind not in _FORMAL_PROPERTY_STATUSES:
            raise ValueError(f"unsupported formal property kind: {self.kind!r}")
        object.__setattr__(self, "kind", kind)

        status = str(self.status).strip().upper()
        if status not in _FORMAL_PROPERTY_STATUSES[kind]:
            allowed = ", ".join(sorted(_FORMAL_PROPERTY_STATUSES[kind]))
            raise ValueError(
                f"unsupported {kind} property status {self.status!r}; expected {allowed}"
            )
        object.__setattr__(self, "status", status)

        if self.depth is not None:
            depth = int(self.depth)
            if depth < 0:
                raise ValueError("formal property depth must be >= 0 when provided")
            object.__setattr__(self, "depth", depth)

        if self.trace_path is not None:
            object.__setattr__(self, "trace_path", Path(self.trace_path))


@dataclass(frozen=True)
class FormalCheckResult:
    """Normalized backend result without assuming a vendor-specific report format."""

    backend: str
    request: FormalCheckRequest
    command: tuple[str, ...]
    returncode: int
    status: str
    run_dir: Path
    log_path: Path
    properties: tuple[FormalPropertyResult, ...] = ()
    artifacts: tuple[Path, ...] = ()
    engine: str | None = None
    runtime_ms: float | None = None
    property_set_complete: bool = False

    def __post_init__(self) -> None:
        backend = str(self.backend).strip()
        if not backend:
            raise ValueError("formal backend name must not be empty")
        object.__setattr__(self, "backend", backend)

        status = str(self.status).strip().upper()
        if status not in _FORMAL_RESULT_STATUSES:
            allowed = ", ".join(sorted(_FORMAL_RESULT_STATUSES))
            raise ValueError(f"unsupported formal result status {self.status!r}; expected {allowed}")
        object.__setattr__(self, "status", status)

        object.__setattr__(self, "command", tuple(str(part) for part in self.command))
        object.__setattr__(self, "run_dir", Path(self.run_dir))
        object.__setattr__(self, "log_path", Path(self.log_path))
        object.__setattr__(self, "properties", tuple(self.properties))
        object.__setattr__(
            self,
            "artifacts",
            tuple(Path(path) for path in self.artifacts),
        )

        if self.engine is not None:
            engine = str(self.engine).strip()
            object.__setattr__(self, "engine", engine or None)

        if self.runtime_ms is not None:
            runtime_ms = float(self.runtime_ms)
            if runtime_ms < 0:
                raise ValueError("formal runtime_ms must be >= 0 when provided")
            object.__setattr__(self, "runtime_ms", runtime_ms)

        if not isinstance(self.property_set_complete, bool):
            raise ValueError("formal property_set_complete must be a boolean")

    @property
    def passed(self) -> bool:
        return self.status == "PASS"

    def property_summary(self) -> dict[str, int]:
        return dict(sorted(Counter(item.status for item in self.properties).items()))


class FormalBackend(ABC):
    """Execution boundary implemented by concrete formal tools."""

    name: str

    @abstractmethod
    def version(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def check(
        self,
        project: ProjectConfig,
        request: FormalCheckRequest,
    ) -> FormalCheckResult:
        raise NotImplementedError
