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
