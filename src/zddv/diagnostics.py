from __future__ import annotations

from dataclasses import dataclass
import re


_VERILATOR_DIAGNOSTIC = re.compile(
    r"^%(?P<severity>Warning|Error)"
    r"(?:-(?P<code>[A-Za-z0-9_]+))?:\s*"
    r"(?:(?P<file>.*?):(?P<line>\d+):(?P<column>\d+):\s*)?"
    r"(?P<message>.*)$"
)

_NUMBER = re.compile(r"(?<![A-Za-z_])(?:0x[0-9A-Fa-f]+|\d+)(?![A-Za-z_])")


@dataclass(frozen=True)
class Diagnostic:
    severity: str
    code: str | None
    message: str
    file: str | None = None
    line: int | None = None
    column: int | None = None

    def as_dict(self) -> dict:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "column": self.column,
        }


def parse_verilator_diagnostics(text: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for raw_line in text.splitlines():
        match = _VERILATOR_DIAGNOSTIC.match(raw_line.strip())
        if not match:
            continue
        diagnostics.append(
            Diagnostic(
                severity=match.group("severity").upper(),
                code=match.group("code"),
                message=match.group("message").strip(),
                file=match.group("file"),
                line=(
                    int(match.group("line"))
                    if match.group("line") is not None
                    else None
                ),
                column=(
                    int(match.group("column"))
                    if match.group("column") is not None
                    else None
                ),
            )
        )
    return diagnostics


def failure_signature(text: str) -> str:
    diagnostics = parse_verilator_diagnostics(text)
    for diagnostic in diagnostics:
        if diagnostic.severity == "ERROR":
            prefix = diagnostic.code or "ERROR"
            message = _NUMBER.sub("<n>", diagnostic.message)
            return f"{prefix}: {message}"[:240]

    interesting = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        upper = line.upper()
        if line and any(token in upper for token in ("FATAL", "ERROR", "FAIL", "ASSERT")):
            interesting.append(line)

    if interesting:
        return _NUMBER.sub("<n>", interesting[-1])[:240]

    nonempty = [line.strip() for line in text.splitlines() if line.strip()]
    if nonempty:
        return _NUMBER.sub("<n>", nonempty[-1])[:240]
    return "Unknown failure"
