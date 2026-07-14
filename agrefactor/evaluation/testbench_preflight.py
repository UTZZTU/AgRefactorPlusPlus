from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Iterable

from agrefactor.evidence import (
    TestbenchDiagnostic,
    TestbenchFailureKind,
    TestbenchFailureOwner,
    TestbenchPreflightResult,
    TestbenchPreflightStatus,
    TestbenchStage,
)

_DIAGNOSTIC_RE = re.compile(
    r"^(?P<file>.*?):(?P<line>\d+):(?P<column>\d+):\s+"
    r"(?:fatal error|error):\s+(?P<message>.*)$"
)


_UNDEFINED_REFERENCE_RE = re.compile(
    r"undefined reference to [`'‘](?P<symbol>.+?)[`'’]"
)


_EXTERN_VARIABLE_RE = re.compile(
    r'^\s*extern\s+(?!"C"\s)(?P<body>[^;(){}]+);',
    re.MULTILINE,
)
_GLOBAL_VARIABLE_RE = re.compile(
    r'^\s*(?!extern\b|typedef\b|using\b|return\b|#)'
    r'(?P<body>[^;(){}]+);'
)


def _declarator_names(body: str) -> tuple[str, ...]:
    names: list[str] = []
    for chunk in body.split(","):
        declaration = chunk.split("=", 1)[0].strip()
        match = re.search(
            r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*$",
            declaration,
        )
        if match:
            names.append(match.group(1))
    return tuple(dict.fromkeys(names))


def _top_level_global_names(source: str) -> set[str]:
    cleaned = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    depth = 0
    names: set[str] = set()

    for raw_line in cleaned.splitlines():
        line = raw_line.split("//", 1)[0]
        if depth == 0:
            match = _GLOBAL_VARIABLE_RE.match(line)
            if match:
                names.update(_declarator_names(match.group("body")))

        depth += line.count("{") - line.count("}")
        depth = max(depth, 0)

    return names


def find_forbidden_internal_dependencies(
    *,
    testbench_code: str,
    original_code: str,
    candidate_code: str,
) -> tuple[tuple[str, int, str], ...]:
    implementation_globals = (
        _top_level_global_names(original_code)
        | _top_level_global_names(candidate_code)
    )
    findings: list[tuple[str, int, str]] = []

    for match in _EXTERN_VARIABLE_RE.finditer(testbench_code):
        line = testbench_code.count("\n", 0, match.start()) + 1
        raw = match.group(0).strip()
        for name in _declarator_names(match.group("body")):
            if name in implementation_globals:
                findings.append((name, line, raw))

    return tuple(dict.fromkeys(findings))


def _undefined_function_names(stderr: str) -> tuple[str, ...]:
    names: list[str] = []
    for match in _UNDEFINED_REFERENCE_RE.finditer(stderr):
        symbol = match.group("symbol").strip()
        base = symbol.split("(", 1)[0].strip()
        if re.fullmatch(r"[A-Za-z_]\w*", base):
            names.append(base)
    return tuple(dict.fromkeys(names))


def _declared_linkage(source: str, function_name: str) -> str | None:
    pattern = re.compile(
        rf"^\s*(?P<c>extern\s+\"C\"\s+)?"
        rf"(?:[A-Za-z_]\w*(?:::\w+)*(?:\s*[*&]\s*|\s+))+"
        rf"{re.escape(function_name)}\s*\([^;{{}}]*\)\s*;",
        re.MULTILINE,
    )
    match = pattern.search(source)
    if not match:
        return None
    return "c" if match.group("c") else "cpp"


def _defined_linkage(source: str, function_name: str) -> str | None:
    pattern = re.compile(
        rf"^\s*(?P<c>extern\s+\"C\"\s+)?"
        rf"(?:[A-Za-z_]\w*(?:::\w+)*(?:\s*[*&]\s*|\s+))+"
        rf"{re.escape(function_name)}\s*\([^;{{}}]*\)\s*\{{",
        re.MULTILINE,
    )
    match = pattern.search(source)
    if not match:
        return None
    return "c" if match.group("c") else "cpp"


def infer_linkage_mismatch(
    stderr: str,
    *,
    testbench_code: str,
    original_code: str,
    candidate_code: str,
) -> tuple[str, ...]:
    mismatches: list[str] = []
    for name in _undefined_function_names(stderr):
        declared = _declared_linkage(testbench_code, name)
        defined = (
            _defined_linkage(original_code, name)
            or _defined_linkage(candidate_code, name)
        )
        if declared and defined and declared != defined:
            mismatches.append(name)
    return tuple(dict.fromkeys(mismatches))


def classify_compile_failure(stderr: str) -> TestbenchFailureKind:
    lowered = stderr.lower()
    if "does not name a type" in lowered or "unknown type name" in lowered:
        return TestbenchFailureKind.UNDECLARED_TYPE
    if (
        "was not declared in this scope" in lowered
        or "use of undeclared identifier" in lowered
        or "undeclared" in lowered
    ):
        return TestbenchFailureKind.UNDECLARED_SYMBOL
    if (
        "undefined reference to" in lowered
        or "ld returned" in lowered
        or "linker command failed" in lowered
    ):
        return TestbenchFailureKind.LINK_ERROR
    if "error:" in lowered:
        return TestbenchFailureKind.SYNTAX_ERROR
    return TestbenchFailureKind.UNKNOWN


def parse_compiler_diagnostics(
    stderr: str,
    *,
    default_kind: TestbenchFailureKind,
) -> tuple[TestbenchDiagnostic, ...]:
    items: list[TestbenchDiagnostic] = []
    for line in stderr.splitlines():
        match = _DIAGNOSTIC_RE.match(line.strip())
        if not match:
            continue
        message = match.group("message").strip()
        kind = classify_compile_failure(message)
        if kind is TestbenchFailureKind.UNKNOWN:
            kind = default_kind
        items.append(
            TestbenchDiagnostic(
                kind=kind,
                message=message,
                file=match.group("file"),
                line=int(match.group("line")),
                column=int(match.group("column")),
                raw=line,
            )
        )
    return tuple(items)


def infer_failure_owner(
    diagnostics: tuple[TestbenchDiagnostic, ...],
) -> TestbenchFailureOwner:
    owners: set[TestbenchFailureOwner] = set()

    for diagnostic in diagnostics:
        if not diagnostic.file:
            continue
        name = Path(diagnostic.file).name
        if name == "testbench.cpp":
            owners.add(TestbenchFailureOwner.TESTBENCH)
        elif name == "orig_code.cpp":
            owners.add(TestbenchFailureOwner.ORIGINAL)
        elif name == "refactor_code.cpp":
            owners.add(TestbenchFailureOwner.CANDIDATE)
        else:
            owners.add(TestbenchFailureOwner.UNKNOWN)

    if len(owners) == 1:
        return next(iter(owners))
    return TestbenchFailureOwner.UNKNOWN


class TestbenchPreflight:
    def __init__(
        self,
        *,
        compiler: str = "g++",
        timeout_s: float = 60.0,
        extra_flags: Iterable[str] = (),
        include_dirs: Iterable[str | Path] = (),
    ) -> None:
        if not compiler.strip():
            raise ValueError("compiler must not be empty")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self._compiler = compiler.strip()
        self._timeout_s = float(timeout_s)
        self._extra_flags = tuple(str(item) for item in extra_flags)
        self._include_dirs = tuple(Path(item) for item in include_dirs)

    def compile_and_link(
        self,
        *,
        work_dir: str | Path,
        testbench_code: str,
        original_code: str,
        candidate_code: str,
    ) -> TestbenchPreflightResult:
        directory = Path(work_dir)
        directory.mkdir(parents=True, exist_ok=True)
        sources = {
            "testbench.cpp": testbench_code,
            "orig_code.cpp": original_code,
            "refactor_code.cpp": candidate_code,
        }
        for name, content in sources.items():
            if not isinstance(content, str) or not content.strip():
                raise ValueError(f"{name} source must not be empty")
            (directory / name).write_text(content, encoding="utf-8")

        forbidden = find_forbidden_internal_dependencies(
            testbench_code=testbench_code,
            original_code=original_code,
            candidate_code=candidate_code,
        )
        if forbidden:
            diagnostics = tuple(
                TestbenchDiagnostic(
                    kind=(
                        TestbenchFailureKind
                        .FORBIDDEN_INTERNAL_DEPENDENCY
                    ),
                    message=(
                        "testbench declares implementation-private "
                        f"file-scope variable: {name}"
                    ),
                    file="testbench.cpp",
                    line=line,
                    raw=raw,
                )
                for name, line, raw in forbidden
            )
            return TestbenchPreflightResult(
                status=TestbenchPreflightStatus.FAILED,
                stage=TestbenchStage.STATIC_CHECK,
                failure_kind=(
                    TestbenchFailureKind
                    .FORBIDDEN_INTERNAL_DEPENDENCY
                ),
                failure_owner=TestbenchFailureOwner.TESTBENCH,
                return_code=None,
                command=(),
                diagnostics=diagnostics,
                stderr="\n".join(
                    item.message for item in diagnostics
                ),
                artifacts=tuple(
                    str(directory / name) for name in sources
                ),
                duration_s=0.0,
            )

        command = [
            self._compiler,
            "-D__SYNTHESIS__",
            "-O2",
            "-Wno-unknown-pragmas",
        ]
        include_dirs = list(self._include_dirs)
        xilinx_hls = os.getenv("XILINX_HLS")
        if xilinx_hls:
            include_dirs.append(Path(xilinx_hls) / "include")
        for include_dir in dict.fromkeys(include_dirs):
            command.extend(["-I", str(include_dir)])
        command.extend(self._extra_flags)
        command.extend(sources)
        command.extend(["-o", "testbench_preflight"])

        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return self._error_result(
                command,
                directory,
                sources,
                TestbenchFailureKind.COMPILE_TIMEOUT,
                "testbench compile/link timed out",
                time.monotonic() - started,
            )
        except FileNotFoundError as exc:
            return self._error_result(
                command,
                directory,
                sources,
                TestbenchFailureKind.COMPILER_NOT_FOUND,
                str(exc),
                time.monotonic() - started,
            )

        duration = time.monotonic() - started
        artifacts = tuple(str(directory / name) for name in sources)

        if completed.returncode == 0:
            return TestbenchPreflightResult(
                status=TestbenchPreflightStatus.PASSED,
                stage=TestbenchStage.COMPILE_LINK,
                failure_kind=TestbenchFailureKind.NONE,
                failure_owner=TestbenchFailureOwner.NONE,
                return_code=0,
                command=tuple(command),
                stdout=completed.stdout,
                stderr=completed.stderr,
                artifacts=artifacts
                + (str(directory / "testbench_preflight"),),
                duration_s=duration,
            )

        kind = classify_compile_failure(completed.stderr)
        diagnostics = parse_compiler_diagnostics(
            completed.stderr,
            default_kind=kind,
        )
        if not diagnostics:
            diagnostics = (
                TestbenchDiagnostic(
                    kind=kind,
                    message="testbench compile/link failed",
                    raw=completed.stderr or None,
                ),
            )

        owner = infer_failure_owner(diagnostics)

        if kind is TestbenchFailureKind.LINK_ERROR:
            linkage_mismatches = infer_linkage_mismatch(
                completed.stderr,
                testbench_code=testbench_code,
                original_code=original_code,
                candidate_code=candidate_code,
            )
            if linkage_mismatches:
                kind = TestbenchFailureKind.LINKAGE_MISMATCH
                owner = TestbenchFailureOwner.TESTBENCH
                diagnostics = (
                    TestbenchDiagnostic(
                        kind=kind,
                        message=(
                            "testbench C/C++ language linkage does not "
                            "match implementation definitions: "
                            + ", ".join(linkage_mismatches)
                        ),
                        file="testbench.cpp",
                        raw=completed.stderr or None,
                    ),
                )

        return TestbenchPreflightResult(
            status=TestbenchPreflightStatus.FAILED,
            stage=TestbenchStage.COMPILE_LINK,
            failure_kind=kind,
            failure_owner=owner,
            return_code=completed.returncode,
            command=tuple(command),
            diagnostics=diagnostics,
            stdout=completed.stdout,
            stderr=completed.stderr,
            artifacts=artifacts,
            duration_s=duration,
        )

    @staticmethod
    def _error_result(
        command,
        directory,
        sources,
        kind,
        message,
        duration,
    ):
        diagnostic = TestbenchDiagnostic(
            kind=kind,
            message=message,
            raw=message,
        )
        return TestbenchPreflightResult(
            status=TestbenchPreflightStatus.ERROR,
            stage=TestbenchStage.COMPILE_LINK,
            failure_kind=kind,
            failure_owner=TestbenchFailureOwner.TOOLCHAIN,
            return_code=None,
            command=tuple(command),
            diagnostics=(diagnostic,),
            stderr=message,
            artifacts=tuple(str(directory / name) for name in sources),
            duration_s=duration,
        )
