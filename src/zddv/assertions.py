from __future__ import annotations

from dataclasses import dataclass
import re


_VERILATOR_ASSERTION = re.compile(
    r"^(?:\[(?P<time>\d+)\]\s*)?"
    r"%Error(?:-[A-Za-z0-9_]+)?:\s*"
    r"(?:(?P<file>.*?):(?P<line>\d+):(?P<column>\d+):\s*)?"
    r"(?P<message>.*Assertion failed.*)$"
)
_GENERIC_ASSERTION = re.compile(r"(?i)\bassertion failed\b")
_SCOPE = re.compile(r"Assertion failed in\s+(?P<scope>[^:]+)")
_LABEL = re.compile(r"(?P<label>[A-Za-z_][A-Za-z0-9_$]*)$")


@dataclass(frozen=True)
class AssertionEvent:
    severity: str
    message: str
    raw_line: str
    source_file: str | None = None
    source_line: int | None = None
    source_column: int | None = None
    simulation_time: int | None = None
    scope: str | None = None
    assertion_name: str | None = None

    def as_dict(self) -> dict:
        return {
            "severity": self.severity,
            "message": self.message,
            "raw_line": self.raw_line,
            "source_file": self.source_file,
            "source_line": self.source_line,
            "source_column": self.source_column,
            "simulation_time": self.simulation_time,
            "scope": self.scope,
            "assertion_name": self.assertion_name,
        }


def _scope_and_name(message: str) -> tuple[str | None, str | None]:
    match = _SCOPE.search(message)
    if not match:
        return None, None
    scope = match.group("scope").strip()
    tail = scope.rsplit(".", 1)[-1]
    label_match = _LABEL.fullmatch(tail)
    assertion_name = label_match.group("label") if label_match else None
    return scope, assertion_name


def parse_verilator_assertions(text: str) -> list[AssertionEvent]:
    events: list[AssertionEvent] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        match = _VERILATOR_ASSERTION.match(line)
        if match:
            message = match.group("message").strip()
            scope, name = _scope_and_name(message)
            events.append(
                AssertionEvent(
                    severity="ERROR",
                    message=message,
                    raw_line=line,
                    source_file=match.group("file"),
                    source_line=(
                        int(match.group("line")) if match.group("line") else None
                    ),
                    source_column=(
                        int(match.group("column"))
                        if match.group("column")
                        else None
                    ),
                    simulation_time=(
                        int(match.group("time")) if match.group("time") else None
                    ),
                    scope=scope,
                    assertion_name=name,
                )
            )
            continue

        if _GENERIC_ASSERTION.search(line):
            scope, name = _scope_and_name(line)
            events.append(
                AssertionEvent(
                    severity="ERROR",
                    message=line,
                    raw_line=line,
                    scope=scope,
                    assertion_name=name,
                )
            )
    return events
