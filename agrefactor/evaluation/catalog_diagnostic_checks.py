"""Bounded local checks selected by reviewed diagnostic catalog rules."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Any, Mapping

from agrefactor.cpp_interface import extract_top_type_contract


CHECK_ID = "csynth_top_argument_pointer_member_contract"
RULE_ID = "hls_214_298_struct_pointer_argument"
_MACRO_FLAG = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:=.*)?$")
_STANDARD_FLAG = re.compile(r"^-std=(?:gnu\+\+|c\+\+)(?:98|03|11|14|17|20|23)$")


def evaluate_catalog_check(
    work_dir: Path,
    invocation: Mapping[str, Any],
    item: Any,
) -> dict[str, str] | None:
    """Return a safe three-state summary for the one P5 pilot check."""

    if (item.metadata.get("diagnostic_id") != RULE_ID
            or CHECK_ID not in item.metadata.get("diagnostic_checks", ())):
        return None

    def missing(reason: str) -> dict[str, str]:
        return {
            "check_id": CHECK_ID,
            "result": "missing",
            "reason_code": reason,
            "summary": f"Required current-run evidence is unavailable ({reason}).",
        }

    if invocation.get("evaluation_split", "public") != "public":
        return None
    if not item.blocking or item.metadata.get("execution_evidence_complete") is not True:
        return missing("execution_or_log_identity_unproven")

    units = invocation.get("compile_units")
    source_files = invocation.get("source_files")
    provenance = invocation.get("source_provenance")
    if (not isinstance(units, list) or not isinstance(source_files, list)
            or units != source_files or not units
            or not isinstance(provenance, dict)):
        return missing("compile_unit_provenance_unavailable")

    candidate_units = [
        name for name in units
        if isinstance(name, str)
        and name in source_files
        and isinstance(provenance.get(name), dict)
        and provenance[name].get("role") == "candidate"
    ]
    if len(candidate_units) != 1:
        return missing("candidate_compile_unit_not_unique")
    candidate = candidate_units[0]
    identity = provenance[candidate]
    if (Path(candidate).is_absolute() or ".." in Path(candidate).parts
            or not _verify_source(work_dir, candidate, identity)):
        return missing("candidate_source_identity_unverified")
    source_path = (work_dir / candidate).resolve()
    if (work_dir / candidate).is_symlink():
        return missing("candidate_source_is_symlink")
    if any(
        not isinstance(name, str) or Path(name).is_absolute()
        or ".." in Path(name).parts
        or not isinstance(provenance.get(name), dict)
        or not _verify_source(work_dir, name, provenance[name])
        for name in units
    ):
        return missing("compile_unit_identity_incomplete")

    parse_context = invocation.get("catalog_parse_context")
    if not isinstance(parse_context, dict) or parse_context.get("schema_version") != 1:
        return missing("parse_context_unavailable")
    if parse_context.get("source_files") != units:
        return missing("parse_context_source_set_mismatch")
    compile_flags = parse_context.get("compile_flags")
    if parse_context.get("mode") != "libclang_default":
        return missing("unreviewed_parse_context")
    include_roots = parse_context.get("include_roots")
    normalized_flags, expected_roots = _reviewed_parse_inputs(
        work_dir, units, compile_flags
    )
    if (normalized_flags is None or not isinstance(include_roots, list)
            or include_roots != expected_roots
            or any(not Path(root).is_dir() or Path(root).is_symlink() for root in include_roots)):
        return missing("parse_inputs_unverified")

    top_kernel = invocation.get("top_kernel")
    if not isinstance(top_kernel, str) or not top_kernel.strip():
        return missing("top_function_unavailable")
    try:
        contract = extract_top_type_contract(
            source_path.read_text(encoding="utf-8"),
            top_kernel,
            source_path=source_path,
            include_dirs=tuple(include_roots),
            compile_flags=normalized_flags,
            compiler="libclang-default",
            compiler_context={
                "status": "confirmed",
                "flags": (),
                "language_standard": None,
                "include_vendor": False,
                "mode": "libclang_default",
            },
        )
    except (OSError, UnicodeError, TypeError, ValueError):
        return missing("libclang_contract_unavailable")

    entries = contract.get("entries", ())
    if (contract.get("status") != "confirmed"
            or contract.get("entry_status") != "confirmed"
            or contract.get("translation_unit_complete") is not True
            or contract.get("unresolved")
            or len(entries) != 1
            or entries[0].get("source_path") != str(source_path)
            or entries[0].get("signature_valid") is not True
            or entries[0].get("is_definition") is not True):
        return missing("top_type_contract_incomplete")

    provenance_paths = {
        str((work_dir / name).resolve())
        for name in units
        if isinstance(name, str) and not Path(name).is_absolute()
        and ".." not in Path(name).parts
        and isinstance(provenance.get(name), dict)
        and _verify_source(work_dir, name, provenance[name])
    }
    parameters = contract.get("top_parameters")
    if not isinstance(parameters, list):
        return missing("top_parameter_contract_unavailable")
    if any(
        source not in provenance_paths
        for parameter in parameters
        for source in parameter.get("type_source_paths", ())
    ):
        return missing("user_type_source_provenance_unverified")

    pointer_paths = [
        path
        for parameter in parameters
        for path in parameter.get("pointer_member_paths", ())
    ]
    if pointer_paths:
        return {
            "check_id": CHECK_ID,
            "result": "satisfied",
            "reason_code": "pointer_member_confirmed",
            "summary": "Confirmed pointer member path(s): " + ", ".join(pointer_paths[:8]),
        }
    return {
        "check_id": CHECK_ID,
        "result": "contradicted",
        "reason_code": "no_pointer_member_confirmed",
        "summary": "The complete top-argument type contract contains no pointer member.",
    }


def _verify_source(root: Path, source_file: str, identity: Mapping[str, Any]) -> bool:
    path = root / source_file
    if path.is_symlink() or not path.is_file():
        return False
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or identity.get("path") != str(resolved):
        return False
    return _verify_file_identity(resolved, identity.get("sha256"))


def _verify_file_identity(path: Path, expected_sha256: Any) -> bool:
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        return False
    try:
        if path.is_symlink() or not path.is_file():
            return False
        return hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha256
    except OSError:
        return False


def _reviewed_parse_inputs(
    work_dir: Path,
    source_files: list[Any],
    raw_flags: Any,
) -> tuple[tuple[str, ...] | None, list[str]]:
    if not isinstance(raw_flags, list) or not all(isinstance(flag, str) for flag in raw_flags):
        return None, []
    flags = tuple(raw_flags)
    roots = {str((work_dir / name).resolve().parent) for name in source_files}
    index = 0
    while index < len(flags):
        flag = flags[index]
        value = None
        if flag in {"-D", "-U"}:
            index += 1
            if index >= len(flags) or not _MACRO_FLAG.fullmatch(flags[index]):
                return None, []
        elif flag.startswith(("-D", "-U")) and len(flag) > 2:
            if not _MACRO_FLAG.fullmatch(flag[2:]):
                return None, []
        elif _STANDARD_FLAG.fullmatch(flag):
            pass
        elif flag in {"-I", "-isystem"}:
            index += 1
            if index >= len(flags):
                return None, []
            value = flags[index]
        elif flag.startswith("-I") and len(flag) > 2:
            value = flag[2:]
        elif flag.startswith("-isystem") and len(flag) > len("-isystem"):
            value = flag[len("-isystem"):]
        else:
            return None, []
        if value is not None:
            path = Path(value)
            resolved = (path if path.is_absolute() else work_dir / path).resolve()
            roots.add(str(resolved))
        index += 1
    return flags, sorted(roots)
