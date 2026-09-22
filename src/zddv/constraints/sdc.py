from __future__ import annotations

from dataclasses import dataclass
import fnmatch
import json
from pathlib import Path
import re
from typing import Iterable

from zddv.config import ProjectConfig


SUPPORTED_SDC_COMMANDS = frozenset(
    {
        "create_clock",
        "create_generated_clock",
        "set_clock_uncertainty",
        "set_clock_latency",
        "set_input_delay",
        "set_output_delay",
        "set_false_path",
        "set_multicycle_path",
        "set_max_delay",
        "set_min_delay",
        "set_clock_groups",
        "set_input_transition",
        "set_load",
        "set_driving_cell",
    }
)

_COMMAND_FLAGS: dict[str, frozenset[str]] = {
    "create_clock": frozenset({"-add"}),
    "create_generated_clock": frozenset(
        {"-add", "-invert", "-combinational"}
    ),
    "set_clock_uncertainty": frozenset(
        {"-setup", "-hold", "-rise", "-fall"}
    ),
    "set_clock_latency": frozenset(
        {
            "-source",
            "-network",
            "-rise",
            "-fall",
            "-min",
            "-max",
            "-early",
            "-late",
        }
    ),
    "set_input_delay": frozenset(
        {
            "-clock_fall",
            "-level_sensitive",
            "-rise",
            "-fall",
            "-min",
            "-max",
            "-add_delay",
            "-source_latency_included",
            "-network_latency_included",
        }
    ),
    "set_output_delay": frozenset(
        {
            "-clock_fall",
            "-level_sensitive",
            "-rise",
            "-fall",
            "-min",
            "-max",
            "-add_delay",
            "-source_latency_included",
            "-network_latency_included",
        }
    ),
    "set_false_path": frozenset(
        {"-setup", "-hold", "-rise", "-fall"}
    ),
    "set_multicycle_path": frozenset(
        {
            "-setup",
            "-hold",
            "-rise",
            "-fall",
            "-start",
            "-end",
        }
    ),
    "set_max_delay": frozenset(
        {"-rise", "-fall", "-ignore_clock_latency"}
    ),
    "set_min_delay": frozenset(
        {"-rise", "-fall", "-ignore_clock_latency"}
    ),
    "set_clock_groups": frozenset(
        {
            "-asynchronous",
            "-logically_exclusive",
            "-physically_exclusive",
            "-allow_paths",
        }
    ),
    "set_input_transition": frozenset(
        {"-rise", "-fall", "-min", "-max"}
    ),
    "set_load": frozenset(
        {
            "-min",
            "-max",
            "-pin_load",
            "-wire_load",
            "-subtract_pin_load",
        }
    ),
    "set_driving_cell": frozenset(
        {
            "-rise",
            "-fall",
            "-min",
            "-max",
            "-dont_scale",
            "-no_design_rule",
        }
    ),
}

_COMMAND_VALUE_OPTIONS: dict[str, frozenset[str]] = {
    "create_clock": frozenset(
        {"-name", "-period", "-waveform", "-comment"}
    ),
    "create_generated_clock": frozenset(
        {
            "-name",
            "-source",
            "-master_clock",
            "-divide_by",
            "-multiply_by",
            "-duty_cycle",
            "-edges",
            "-edge_shift",
            "-comment",
        }
    ),
    "set_clock_uncertainty": frozenset(
        {
            "-from",
            "-rise_from",
            "-fall_from",
            "-to",
            "-rise_to",
            "-fall_to",
        }
    ),
    "set_clock_latency": frozenset(),
    "set_input_delay": frozenset({"-clock", "-reference_pin"}),
    "set_output_delay": frozenset({"-clock", "-reference_pin"}),
    "set_false_path": frozenset(
        {
            "-from",
            "-rise_from",
            "-fall_from",
            "-through",
            "-rise_through",
            "-fall_through",
            "-to",
            "-rise_to",
            "-fall_to",
            "-comment",
        }
    ),
    "set_multicycle_path": frozenset(
        {
            "-from",
            "-rise_from",
            "-fall_from",
            "-through",
            "-rise_through",
            "-fall_through",
            "-to",
            "-rise_to",
            "-fall_to",
            "-comment",
        }
    ),
    "set_max_delay": frozenset(
        {
            "-from",
            "-rise_from",
            "-fall_from",
            "-through",
            "-rise_through",
            "-fall_through",
            "-to",
            "-rise_to",
            "-fall_to",
            "-comment",
        }
    ),
    "set_min_delay": frozenset(
        {
            "-from",
            "-rise_from",
            "-fall_from",
            "-through",
            "-rise_through",
            "-fall_through",
            "-to",
            "-rise_to",
            "-fall_to",
            "-comment",
        }
    ),
    "set_clock_groups": frozenset({"-name", "-group", "-comment"}),
    "set_input_transition": frozenset(),
    "set_load": frozenset(),
    "set_driving_cell": frozenset(
        {
            "-lib_cell",
            "-library",
            "-pin",
            "-from_pin",
            "-input_transition_rise",
            "-input_transition_fall",
        }
    ),
}

_NUMBER = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$"
)


class SdcParseError(ValueError):
    def __init__(self, message: str, *, line: int) -> None:
        super().__init__(f"SDC line {line}: {message}")
        self.line = line


@dataclass(frozen=True)
class SdcOption:
    name: str
    value: str | None = None


@dataclass(frozen=True)
class SdcQuery:
    command: str
    arguments: tuple[str, ...]
    raw: str


@dataclass(frozen=True)
class SdcCommand:
    name: str
    positionals: tuple[str, ...]
    options: tuple[SdcOption, ...]
    queries: tuple[SdcQuery, ...]
    line: int
    raw: str
    supported: bool

    def option_values(self, name: str) -> tuple[str, ...]:
        return tuple(
            item.value
            for item in self.options
            if item.name == name and item.value is not None
        )

    def option_value(self, name: str) -> str | None:
        values = self.option_values(name)
        return values[-1] if values else None

    def has_option(self, name: str) -> bool:
        return any(item.name == name for item in self.options)


@dataclass(frozen=True)
class SdcDocument:
    commands: tuple[SdcCommand, ...]
    source: str | None = None


@dataclass(frozen=True)
class SdcIssue:
    code: str
    severity: str
    message: str
    line: int | None = None
    command: str | None = None


def _split_commands(text: str) -> list[tuple[int, str]]:
    commands: list[tuple[int, str]] = []
    buffer: list[str] = []
    line = 1
    start_line: int | None = None
    brace_depth = 0
    bracket_depth = 0
    quoted = False
    comment = False
    index = 0

    def flush() -> None:
        nonlocal buffer, start_line
        raw = "".join(buffer).strip()
        if raw:
            commands.append((start_line or line, raw))
        buffer = []
        start_line = None

    while index < len(text):
        char = text[index]

        if char == "\\" and index + 1 < len(text) and text[index + 1] == "\n":
            if not comment:
                buffer.append(" ")
            index += 2
            line += 1
            continue

        if comment:
            if char == "\n":
                comment = False
                if brace_depth == 0 and bracket_depth == 0 and not quoted:
                    flush()
                line += 1
            index += 1
            continue

        top_level = brace_depth == 0 and bracket_depth == 0 and not quoted
        previous = text[index - 1] if index else "\n"
        if char == "#" and top_level and (
            not "".join(buffer).strip() or previous.isspace() or previous == ";"
        ):
            comment = True
            index += 1
            continue

        if start_line is None and not char.isspace():
            start_line = line

        if char == '"' and brace_depth == 0:
            quoted = not quoted
            buffer.append(char)
        elif not quoted and char == "{":
            brace_depth += 1
            buffer.append(char)
        elif not quoted and char == "}":
            if brace_depth == 0:
                raise SdcParseError("unmatched '}'", line=line)
            brace_depth -= 1
            buffer.append(char)
        elif not quoted and brace_depth == 0 and char == "[":
            bracket_depth += 1
            buffer.append(char)
        elif not quoted and brace_depth == 0 and char == "]":
            if bracket_depth == 0:
                raise SdcParseError("unmatched ']'", line=line)
            bracket_depth -= 1
            buffer.append(char)
        elif (char == "\n" or char == ";") and top_level:
            flush()
            if char == "\n":
                line += 1
        else:
            buffer.append(char)
            if char == "\n":
                line += 1

        index += 1

    if quoted:
        raise SdcParseError("unterminated double-quoted string", line=start_line or line)
    if brace_depth:
        raise SdcParseError("unterminated brace group", line=start_line or line)
    if bracket_depth:
        raise SdcParseError("unterminated bracket command", line=start_line or line)
    flush()
    return commands


def _split_words(text: str, *, line: int) -> list[str]:
    words: list[str] = []
    buffer: list[str] = []
    brace_depth = 0
    bracket_depth = 0
    quoted = False
    escaped = False

    def flush() -> None:
        nonlocal buffer
        token = "".join(buffer).strip()
        if token:
            words.append(token)
        buffer = []

    for char in text:
        if escaped:
            buffer.append(char)
            escaped = False
            continue
        if char == "\\" and quoted:
            buffer.append(char)
            escaped = True
            continue

        if char == '"' and brace_depth == 0:
            quoted = not quoted
            buffer.append(char)
            continue

        if not quoted:
            if char == "{":
                brace_depth += 1
            elif char == "}":
                if brace_depth == 0:
                    raise SdcParseError("unmatched '}'", line=line)
                brace_depth -= 1
            elif brace_depth == 0 and char == "[":
                bracket_depth += 1
            elif brace_depth == 0 and char == "]":
                if bracket_depth == 0:
                    raise SdcParseError("unmatched ']'", line=line)
                bracket_depth -= 1

        if char.isspace() and brace_depth == 0 and bracket_depth == 0 and not quoted:
            flush()
        else:
            buffer.append(char)

    if quoted or brace_depth or bracket_depth:
        raise SdcParseError("unbalanced token grouping", line=line)
    flush()
    return words


def _clean_token(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] == '"':
        value = token[1:-1]
        return value.replace('\\"', '"').replace("\\\\", "\\")
    return token


def _expand_brace_token(token: str, *, line: int) -> tuple[str, ...]:
    if len(token) >= 2 and token[0] == "{" and token[-1] == "}":
        inner = token[1:-1].strip()
        if not inner:
            return ()
        return tuple(_clean_token(item) for item in _split_words(inner, line=line))
    return (_clean_token(token),)


def _parse_query(token: str, *, line: int) -> SdcQuery | None:
    stripped = token.strip()
    if not (stripped.startswith("[") and stripped.endswith("]")):
        return None
    inner = stripped[1:-1].strip()
    words = _split_words(inner, line=line)
    if not words:
        return None
    arguments: list[str] = []
    for word in words[1:]:
        arguments.extend(_expand_brace_token(word, line=line))
    return SdcQuery(
        command=_clean_token(words[0]),
        arguments=tuple(arguments),
        raw=stripped,
    )


def _looks_like_option(token: str) -> bool:
    return token.startswith("-") and not bool(_NUMBER.fullmatch(token))


def _parse_command(raw: str, *, line: int) -> SdcCommand:
    words = _split_words(raw, line=line)
    if not words:
        raise SdcParseError("empty command", line=line)

    name = _clean_token(words[0])
    flags = _COMMAND_FLAGS.get(name, frozenset())
    value_options = _COMMAND_VALUE_OPTIONS.get(name, frozenset())
    positionals: list[str] = []
    options: list[SdcOption] = []
    query_tokens: list[str] = []
    index = 1

    while index < len(words):
        raw_token = words[index]
        token = _clean_token(raw_token)

        if _looks_like_option(token):
            if token in flags:
                options.append(SdcOption(token))
                index += 1
                continue

            if token in value_options:
                if index + 1 >= len(words):
                    options.append(SdcOption(token))
                    index += 1
                    continue
                value = _clean_token(words[index + 1])
                options.append(SdcOption(token, value))
                query_tokens.append(words[index + 1])
                index += 2
                continue

            value: str | None = None
            if index + 1 < len(words):
                next_token = _clean_token(words[index + 1])
                if not _looks_like_option(next_token):
                    value = next_token
                    query_tokens.append(words[index + 1])
                    index += 1
            options.append(SdcOption(token, value))
            index += 1
            continue

        positionals.append(token)
        query_tokens.append(raw_token)
        index += 1

    queries = tuple(
        query
        for query in (
            _parse_query(token, line=line)
            for token in query_tokens
        )
        if query is not None
    )
    return SdcCommand(
        name=name,
        positionals=tuple(positionals),
        options=tuple(options),
        queries=queries,
        line=line,
        raw=raw,
        supported=name in SUPPORTED_SDC_COMMANDS,
    )


def parse_sdc_text(text: str, *, source: str | None = None) -> SdcDocument:
    commands = tuple(
        _parse_command(raw, line=line)
        for line, raw in _split_commands(text)
    )
    return SdcDocument(commands=commands, source=source)


def parse_sdc_file(path: str | Path) -> SdcDocument:
    source = Path(path).resolve()
    return parse_sdc_text(
        source.read_text(encoding="utf-8", errors="replace"),
        source=str(source),
    )


def _as_float(value: str | None) -> float | None:
    if value is None or not _NUMBER.fullmatch(value.strip()):
        return None
    return float(value)


def _as_positive_int(value: str | None) -> int | None:
    if value is None or not re.fullmatch(r"[+]?[0-9]+", value.strip()):
        return None
    parsed = int(value)
    return parsed if parsed >= 1 else None


def _query_patterns(query: SdcQuery) -> tuple[str, ...]:
    return tuple(
        item
        for item in query.arguments
        if item and not item.startswith("-")
    )


def _infer_clock_name(command: SdcCommand) -> str | None:
    explicit = command.option_value("-name")
    if explicit:
        return explicit
    for query in command.queries:
        if query.command in {"get_ports", "get_pins", "get_clocks"}:
            patterns = _query_patterns(query)
            if len(patterns) == 1 and not any(
                char in patterns[0] for char in "*?[]"
            ):
                return patterns[0]
    if len(command.positionals) == 1:
        token = command.positionals[0]
        if not token.startswith("[") and not token.startswith("{"):
            return token
    return None


def lint_sdc(
    document: SdcDocument,
    *,
    known_ports: Iterable[str] | None = None,
) -> list[SdcIssue]:
    issues: list[SdcIssue] = []
    known_port_set = set(known_ports) if known_ports is not None else None

    clock_names: dict[str, int] = {}
    for command in document.commands:
        if not command.supported:
            issues.append(
                SdcIssue(
                    code="UNSUPPORTED_COMMAND",
                    severity="INFO",
                    message=f"Command {command.name!r} is preserved but not normalized",
                    line=command.line,
                    command=command.name,
                )
            )
            continue

        if command.name == "create_clock":
            period_text = command.option_value("-period")
            period = _as_float(period_text)
            if period_text is None:
                issues.append(
                    SdcIssue(
                        "MISSING_CLOCK_PERIOD",
                        "ERROR",
                        "create_clock requires an explicit -period",
                        command.line,
                        command.name,
                    )
                )
            elif period is None or period <= 0:
                issues.append(
                    SdcIssue(
                        "INVALID_CLOCK_PERIOD",
                        "ERROR",
                        f"Clock period must be a positive number, got {period_text!r}",
                        command.line,
                        command.name,
                    )
                )

            name = _infer_clock_name(command)
            if name:
                if name in clock_names:
                    issues.append(
                        SdcIssue(
                            "DUPLICATE_CLOCK",
                            "ERROR",
                            (
                                f"Clock {name!r} is created more than once "
                                f"(first at line {clock_names[name]})"
                            ),
                            command.line,
                            command.name,
                        )
                    )
                else:
                    clock_names[name] = command.line

        elif command.name == "create_generated_clock":
            name = _infer_clock_name(command)
            if name:
                if name in clock_names:
                    issues.append(
                        SdcIssue(
                            "DUPLICATE_CLOCK",
                            "ERROR",
                            (
                                f"Clock {name!r} is created more than once "
                                f"(first at line {clock_names[name]})"
                            ),
                            command.line,
                            command.name,
                        )
                    )
                else:
                    clock_names[name] = command.line

            if command.option_value("-source") is None:
                issues.append(
                    SdcIssue(
                        "MISSING_GENERATED_CLOCK_SOURCE",
                        "ERROR",
                        "create_generated_clock requires -source",
                        command.line,
                        command.name,
                    )
                )

            divide = command.option_value("-divide_by")
            multiply = command.option_value("-multiply_by")
            if divide is not None and multiply is not None:
                issues.append(
                    SdcIssue(
                        "CONFLICTING_GENERATED_CLOCK_SCALE",
                        "ERROR",
                        "Use either -divide_by or -multiply_by, not both",
                        command.line,
                        command.name,
                    )
                )
            for option_name, value in (
                ("-divide_by", divide),
                ("-multiply_by", multiply),
            ):
                if value is not None:
                    numeric = _as_float(value)
                    if numeric is None or numeric <= 0:
                        issues.append(
                            SdcIssue(
                                "INVALID_GENERATED_CLOCK_SCALE",
                                "ERROR",
                                f"{option_name} must be positive, got {value!r}",
                                command.line,
                                command.name,
                            )
                        )

        elif command.name == "set_multicycle_path":
            count = command.positionals[0] if command.positionals else None
            if _as_positive_int(count) is None:
                issues.append(
                    SdcIssue(
                        "INVALID_MULTICYCLE_COUNT",
                        "ERROR",
                        "set_multicycle_path requires a positive integer count",
                        command.line,
                        command.name,
                    )
                )

        elif command.name in {"set_max_delay", "set_min_delay"}:
            value = command.positionals[0] if command.positionals else None
            if _as_float(value) is None:
                issues.append(
                    SdcIssue(
                        "INVALID_PATH_DELAY",
                        "ERROR",
                        f"{command.name} requires a numeric delay value",
                        command.line,
                        command.name,
                    )
                )

        if known_port_set is not None:
            for query in command.queries:
                if query.command != "get_ports":
                    continue
                for pattern in _query_patterns(query):
                    matches = [
                        port
                        for port in known_port_set
                        if port == pattern or fnmatch.fnmatchcase(port, pattern)
                    ]
                    if not matches:
                        issues.append(
                            SdcIssue(
                                "UNRESOLVED_PORT_REFERENCE",
                                "WARNING",
                                f"get_ports pattern {pattern!r} matches no known port",
                                command.line,
                                command.name,
                            )
                        )

    if not any(
        command.name in {"create_clock", "create_generated_clock"}
        for command in document.commands
    ):
        issues.append(
            SdcIssue(
                code="NO_CLOCKS",
                severity="WARNING",
                message="No create_clock/create_generated_clock command was found",
            )
        )

    return issues


def sdc_document_to_dict(
    document: SdcDocument,
    *,
    issues: Iterable[SdcIssue] = (),
) -> dict:
    issue_list = list(issues)
    severity_counts = {
        severity: sum(1 for item in issue_list if item.severity == severity)
        for severity in ("ERROR", "WARNING", "INFO")
    }
    return {
        "analysis": "sdc_constraints",
        "scope": "static-sdc-parse-and-lint",
        "source": document.source,
        "status": (
            "LINT_ERRORS"
            if severity_counts["ERROR"]
            else ("LINT_WARNINGS" if severity_counts["WARNING"] else "LINT_CLEAN")
        ),
        "summary": {
            "commands": len(document.commands),
            "supported_commands": sum(
                1 for item in document.commands if item.supported
            ),
            "unsupported_commands": sum(
                1 for item in document.commands if not item.supported
            ),
            "errors": severity_counts["ERROR"],
            "warnings": severity_counts["WARNING"],
            "info": severity_counts["INFO"],
        },
        "commands": [
            {
                "name": command.name,
                "line": command.line,
                "supported": command.supported,
                "positionals": list(command.positionals),
                "options": [
                    {"name": item.name, "value": item.value}
                    for item in command.options
                ],
                "queries": [
                    {
                        "command": query.command,
                        "arguments": list(query.arguments),
                        "raw": query.raw,
                    }
                    for query in command.queries
                ],
                "raw": command.raw,
            }
            for command in document.commands
        ],
        "issues": [
            {
                "code": issue.code,
                "severity": issue.severity,
                "message": issue.message,
                "line": issue.line,
                "command": issue.command,
            }
            for issue in issue_list
        ],
    }


def analyze_sdc_file(
    project: ProjectConfig,
    path: str | Path,
    *,
    known_ports: Iterable[str] | None = None,
    output: str | Path = ".zddv/constraints/sdc.json",
) -> dict:
    input_path = Path(path)
    if not input_path.is_absolute():
        input_path = project.root / input_path
    input_path = input_path.resolve()

    document = parse_sdc_file(input_path)
    issues = lint_sdc(document, known_ports=known_ports)
    report = sdc_document_to_dict(document, issues=issues)
    report["project"] = project.name
    report["input_path"] = str(input_path)

    report_path = Path(output)
    if not report_path.is_absolute():
        report_path = project.root / report_path
    report_path = report_path.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report["report_path"] = str(report_path)
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report
