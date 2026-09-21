from __future__ import annotations

from dataclasses import dataclass
import re


_VERILATOR_DIAGNOSTIC = re.compile(
    r"^%(?P<severity>Warning|Error)"
    r"(?:-(?P<code>[A-Za-z0-9_]+))?:\s*"
    r"(?:(?P<file>.*?):(?P<line>\d+):(?P<column>\d+):\s*)?"
    r"(?P<message>.*)$"
)


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
                line=int(match.group("line")) if match.group("line") else None,
                column=(
                    int(match.group("column"))
                    if match.group("column")
                    else None
                ),
            )
        )
    return diagnostics
