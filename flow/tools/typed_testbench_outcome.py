"""Schema-v2 transport for one self-checking Public testbench invocation.

This module is intentionally policy-free.  It owns only identity construction,
source adaptation, atomic serialization and strict parsing.  It never decides
failure ownership, repair eligibility, validation transitions, or acceptance.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any
from uuid import uuid4


TYPED_TESTBENCH_OUTCOME_SCHEMA_VERSION = 2
_MAIN_DEFINITION_RE = re.compile(
    r"(?m)^(?P<indent>[ \t]*)(?P<return_type>int|auto)[ \t]+"
    r"main[ \t]*\((?P<params>[^)]*)\)"
    r"(?P<suffix>[ \t]*(?:->[ \t]*int[ \t]*)?\{)"
)
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_EXECUTION_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_PHASE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must not be empty")
    return cleaned


def _required_source_text(value: Any, name: str) -> str:
    """Validate source presence without normalizing identity-bearing bytes."""

    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value


def _sha256_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def make_typed_outcome_identity(
    *,
    phase: str,
    suite_id: str,
    candidate_code: str,
    testbench_code: str,
    execution_id: str | None = None,
) -> dict[str, str]:
    """Build one strict invocation identity; no ownership semantics are added."""

    phase = _required_text(phase, "phase")
    if _PHASE_RE.fullmatch(phase) is None:
        raise ValueError("phase must be a lowercase structured phase code")
    suite_id = _required_text(suite_id, "suite_id")
    candidate_code = _required_source_text(candidate_code, "candidate_code")
    testbench_code = _required_source_text(testbench_code, "testbench_code")
    if execution_id is None:
        execution_id = uuid4().hex
    execution_id = _required_text(execution_id, "execution_id")
    if _EXECUTION_ID_RE.fullmatch(execution_id) is None:
        raise ValueError("execution_id must be 32 lowercase hex characters")
    return {
        "execution_id": execution_id,
        "phase": phase,
        "suite_id_sha256": _sha256_text(suite_id),
        "candidate_sha256": _sha256_text(candidate_code),
        "testbench_sha256": _sha256_text(testbench_code),
    }


def _main_call_contract(parameters: str, wrapped_main_name: str) -> tuple[str, str, str]:
    cleaned = re.sub(r"\s+", " ", parameters.strip())
    if cleaned in {"", "void"}:
        return "no_args", f"int {wrapped_main_name}();", f"{wrapped_main_name}()"
    parts = [part.strip() for part in cleaned.split(",")]
    if (
        len(parts) == 2
        and re.search(r"\bint\b", parts[0])
        and re.search(r"\bchar\b", parts[1])
        and ("*" in parts[1] or "[" in parts[1])
    ):
        return (
            "argc_argv",
            f"int {wrapped_main_name}(int, char **);",
            f"{wrapped_main_name}(argc, argv)",
        )
    raise ValueError(
        "Public Testbench main signature is unsupported by the typed-outcome "
        "adapter; expected main(), main(void), or main(int, char **)."
    )


def _cpp_literal(value: str, name: str) -> str:
    cleaned = _required_text(value, name)
    if any(character in cleaned for character in ('"', "\\", "\n", "\r", "\x00")):
        raise ValueError(f"{name} contains an unsafe C++ literal character")
    return cleaned


def _validate_identity_shape(identity: Mapping[str, str]) -> dict[str, str]:
    required = {
        "execution_id",
        "phase",
        "suite_id_sha256",
        "candidate_sha256",
        "testbench_sha256",
    }
    if not isinstance(identity, Mapping) or set(identity) != required:
        raise ValueError("typed outcome identity has unexpected fields")
    value = {key: _required_text(identity[key], key) for key in required}
    if _EXECUTION_ID_RE.fullmatch(value["execution_id"]) is None:
        raise ValueError("execution_id must be 32 lowercase hex characters")
    if _PHASE_RE.fullmatch(value["phase"]) is None:
        raise ValueError("phase must be a lowercase structured phase code")
    for key in ("suite_id_sha256", "candidate_sha256", "testbench_sha256"):
        if _HEX64_RE.fullmatch(value[key]) is None:
            raise ValueError(f"{key} must be 64 lowercase hex characters")
    return value


def build_typed_testbench_adapter(
    testbench_code: str,
    *,
    wrapped_main_name: str,
    base_identity: Mapping[str, str],
    allowed_phases: Iterable[str],
) -> tuple[str, str, dict[str, Any]]:
    """Wrap ``main`` and atomically serialize only raw runtime facts.

    Runtime argv is exactly: OUTCOME_PATH EXECUTION_ID PHASE.  The wrapper
    rejects an execution id or phase that is not part of its compiled identity.
    """

    if not isinstance(testbench_code, str) or not testbench_code.strip():
        raise ValueError("testbench source must not be empty")
    wrapped_main_name = _required_text(wrapped_main_name, "wrapped_main_name")
    if not wrapped_main_name.isidentifier():
        raise ValueError("wrapped_main_name must be an identifier")
    if wrapped_main_name in testbench_code:
        raise ValueError("Public Testbench collides with the reserved wrapper symbol")

    identity = _validate_identity_shape(base_identity)
    phases = tuple(dict.fromkeys(_required_text(item, "allowed phase") for item in allowed_phases))
    if not phases:
        raise ValueError("allowed_phases must not be empty")
    if identity["phase"] not in phases:
        raise ValueError("base identity phase must be one of allowed_phases")
    for phase in phases:
        if _PHASE_RE.fullmatch(phase) is None:
            raise ValueError("allowed phase must be a structured phase code")

    execution_id = _cpp_literal(identity["execution_id"], "execution_id")
    suite_hash = _cpp_literal(identity["suite_id_sha256"], "suite_id_sha256")
    candidate_hash = _cpp_literal(identity["candidate_sha256"], "candidate_sha256")
    testbench_hash = _cpp_literal(identity["testbench_sha256"], "testbench_sha256")
    phase_conditions = " || ".join(
        f'phase == "{_cpp_literal(item, "phase")}"' for item in phases
    )

    matches = list(_MAIN_DEFINITION_RE.finditer(testbench_code))
    if len(matches) != 1:
        raise ValueError(
            "Public Testbench must contain exactly one supported main definition "
            f"for typed-outcome adaptation; found {len(matches)}"
        )
    match = matches[0]
    main_kind, declaration, call = _main_call_contract(
        match.group("params"), wrapped_main_name
    )
    replacement = (
        f"{match.group('indent')}{match.group('return_type')} "
        f"{wrapped_main_name}({match.group('params')}){match.group('suffix')}"
    )
    instrumented = testbench_code[: match.start()] + replacement + testbench_code[match.end() :]

    wrapper = f'''#include <cstdio>\n#include <fstream>\n#include <string>\n\n{declaration}\n\nnamespace {{\nint agrefactor_write_outcome(const char *path, const std::string &phase, int status) {{\n    if (path == nullptr || path[0] == '\\0') {{\n        return 90;\n    }}\n    const std::string final_path(path);\n    const std::string temporary_path = final_path + ".{execution_id}.tmp";\n    std::ofstream outcome(temporary_path, std::ios::out | std::ios::trunc);\n    if (!outcome.is_open()) {{\n        return 91;\n    }}\n    outcome\n        << "{{\\\"schema_version\\\":{TYPED_TESTBENCH_OUTCOME_SCHEMA_VERSION},"\n        << "\\\"execution_id\\\":\\\"{execution_id}\\\","\n        << "\\\"phase\\\":\\\"" << phase << "\\\","\n        << "\\\"suite_id_sha256\\\":\\\"{suite_hash}\\\","\n        << "\\\"candidate_sha256\\\":\\\"{candidate_hash}\\\","\n        << "\\\"testbench_sha256\\\":\\\"{testbench_hash}\\\","\n        << "\\\"status\\\":\\\"" << (status == 0 ? "passed" : "failed") << "\\\","\n        << "\\\"testbench_returncode\\\":" << status << "}}\\n";\n    outcome.flush();\n    if (!outcome.good()) {{\n        outcome.close();\n        std::remove(temporary_path.c_str());\n        return 92;\n    }}\n    outcome.close();\n    if (std::rename(temporary_path.c_str(), final_path.c_str()) != 0) {{\n        std::remove(temporary_path.c_str());\n        return 93;\n    }}\n    return 0;\n}}\n}}  // namespace\n\nint main(int argc, char **argv) {{\n    if (\n        argc != 4 || argv == nullptr || argv[1] == nullptr ||\n        argv[2] == nullptr || argv[3] == nullptr ||\n        argv[1][0] == '\\0' || argv[2][0] == '\\0' || argv[3][0] == '\\0'\n    ) {{\n        return 90;\n    }}\n    const std::string execution_id(argv[2]);\n    const std::string phase(argv[3]);\n    if (execution_id != "{execution_id}") {{\n        return 94;\n    }}\n    if (!({phase_conditions})) {{\n        return 95;\n    }}\n    const int testbench_status = {call};\n    const int evidence_status = agrefactor_write_outcome(argv[1], phase, testbench_status);\n    if (evidence_status != 0) {{\n        return evidence_status;\n    }}\n    return testbench_status;\n}}\n'''

    adapter = {
        "schema_version": TYPED_TESTBENCH_OUTCOME_SCHEMA_VERSION,
        "adapter": "raw_runtime_atomic_wrapper_v2",
        "wrapped_symbol": wrapped_main_name,
        "main_contract": main_kind,
        "base_identity": dict(identity),
        "allowed_phases": list(phases),
        "argv_contract": ["outcome_path", "execution_id", "phase"],
        "records_only_raw_returncode": True,
        "atomic_replace": True,
        "hidden_input_count": 0,
    }
    return instrumented, wrapper, adapter


def read_typed_testbench_outcome(
    path: Path,
    *,
    expected_identity: Mapping[str, str],
) -> dict[str, Any] | None:
    """Return one raw outcome only when schema and full identity match."""

    if not isinstance(path, Path):
        raise TypeError("path must be Path")
    expected = _validate_identity_shape(expected_identity)
    if path.is_symlink() or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, Mapping):
        return None
    required = {
        "schema_version",
        "execution_id",
        "phase",
        "suite_id_sha256",
        "candidate_sha256",
        "testbench_sha256",
        "status",
        "testbench_returncode",
    }
    if set(value) != required:
        return None
    if value.get("schema_version") != TYPED_TESTBENCH_OUTCOME_SCHEMA_VERSION:
        return None
    for key in (
        "execution_id",
        "phase",
        "suite_id_sha256",
        "candidate_sha256",
        "testbench_sha256",
    ):
        if value.get(key) != expected[key]:
            return None
    returncode = value.get("testbench_returncode")
    if isinstance(returncode, bool) or not isinstance(returncode, int):
        return None
    status = value.get("status")
    if status == "passed" and returncode != 0:
        return None
    if status == "failed" and returncode == 0:
        return None
    if status not in {"passed", "failed"}:
        return None
    return {
        "schema_version": TYPED_TESTBENCH_OUTCOME_SCHEMA_VERSION,
        **expected,
        "status": status,
        "testbench_returncode": returncode,
        "identity_verified": True,
    }
