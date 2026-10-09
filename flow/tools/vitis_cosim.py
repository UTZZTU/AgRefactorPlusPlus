"""Real Vitis HLS Public RTL COSIM with typed, fail-closed evidence."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

import flow.tools as tools
from flow.tools.typed_testbench_outcome import (
    build_typed_testbench_adapter,
    make_typed_outcome_identity,
    read_typed_testbench_outcome,
    read_typed_testbench_started,
)
from agrefactor.config import TargetProfile, resolve_target_profile
from agrefactor.cpp_interface import extract_top_interface
from agrefactor.vitis_interface import discover_top_io
from agrefactor.runtime.budget import (
    BudgetExceededError,
    BudgetManager,
    BudgetUsage,
)
from flow.tools.csynth import resolve_csynth_command


VERSION_PROBE_BUDGET_INCREMENT = {"tool_calls": 1}
COSIM_BUDGET_INCREMENT = {"tool_calls": 1, "cosim_calls": 1}
COSIM_SCHEMA_VERSION = 1
_VERSION_PROBE_TIMEOUT_S = 60
_PUBLIC_DIFFERENTIAL_RUNTIME_CONTRACT_KIND = (
    "public_differential_self_check_v1"
)


def _normalize_cosim_interface_depths(
    value: Mapping[str, Any] | None,
) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError("COSIM interface depths must be a mapping")
    out: dict[str, int] = {}
    for raw_port, raw_depth in value.items():
        if not isinstance(raw_port, str):
            raise TypeError("COSIM depth port names must be strings")
        port = raw_port.strip()
        if (
            not port
            or port != raw_port
            or not (port[0].isalpha() or port[0] == "_")
            or not all(ch.isalnum() or ch == "_" for ch in port)
        ):
            raise ValueError(
                "COSIM depth port names must be exact C identifier names"
            )
        if isinstance(raw_depth, bool) or not isinstance(raw_depth, int):
            raise TypeError("COSIM interface depth must be an integer")
        if raw_depth <= 0:
            raise ValueError("COSIM interface depth must be positive")
        out[port] = raw_depth
    return dict(sorted(out.items()))


_M_AXI_PRAGMA_RE = re.compile(
    r"#\s*pragma\s+HLS\s+INTERFACE\b(?P<options>[^\r\n]*)",
    flags=re.IGNORECASE,
)
_PRAGMA_PORT_RE = re.compile(
    r"\bport\s*=\s*([A-Za-z_][A-Za-z0-9_]*)",
    flags=re.IGNORECASE,
)
_PRAGMA_BUNDLE_RE = re.compile(
    r"\bbundle\s*=\s*([A-Za-z_][A-Za-z0-9_]*)",
    flags=re.IGNORECASE,
)
_M_AXI_MODE_RE = re.compile(
    r"\b(?:mode\s*=\s*)?m_axi\b",
    flags=re.IGNORECASE,
)
_CPP_COMMENT_RE = re.compile(r"//[^\r\n]*|/\*.*?\*/", flags=re.DOTALL)


def _mask_cpp_comments(source: str) -> str:
    return _CPP_COMMENT_RE.sub(
        lambda match: "".join("\n" if char == "\n" else " " for char in match.group()),
        source,
    )


def _top_function_source(source: str, top: str | None) -> str:
    if not top:
        return source
    signature = re.compile(
        rf"\b{re.escape(top)}\s*\([^;{{}}]*\)\s*\{{",
        flags=re.DOTALL,
    )
    match = signature.search(_mask_cpp_comments(source))
    if match is None:
        return source
    brace = source.find("{", match.start(), match.end())
    depth = 0
    state = "normal"
    escaped = False
    index = brace
    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""
        if state == "normal" and char == "/" and next_char == "/":
            state = "line_comment"
            index += 2
            continue
        if state == "normal" and char == "/" and next_char == "*":
            state = "block_comment"
            index += 2
            continue
        if state == "line_comment":
            if char in "\r\n":
                state = "normal"
            index += 1
            continue
        if state == "block_comment":
            if char == "*" and next_char == "/":
                state = "normal"
                index += 2
                continue
            index += 1
            continue
        if state in {"string", "char"}:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif (state == "string" and char == '"') or (
                state == "char" and char == "'"
            ):
                state = "normal"
            index += 1
            continue
        if char == '"':
            state = "string"
        elif char == "'":
            state = "char"
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : index + 1]
        index += 1
    return source[match.start() :]


def _infer_maxi_hardware_depths(
    source: str,
    source_depths: Mapping[str, int],
    top: str | None = None,
) -> tuple[dict[str, int], dict[str, str], str, str | None]:
    """Map source-port depths to Vitis MAXI bundles from HLS pragmas.

    Vitis assigns unbundled ``m_axi`` ports to its default memory bundle.
    This parser records that compiler convention without referring to a
    particular kernel or symbol.  Conflicting declarations remain a
    contract error instead of being guessed away.
    """

    if not isinstance(source, str) or not source.strip():
        return {}, {}, "unknown", "source_not_available"
    mapping: dict[str, str] = {}
    scoped_source = _top_function_source(source, top)
    for line in scoped_source.splitlines():
        if not line.lstrip().startswith("#"):
            continue
        match = _M_AXI_PRAGMA_RE.search(line)
        if match is None:
            continue
        options = match.group("options").split("//", 1)[0]
        if _M_AXI_MODE_RE.search(options) is None:
            continue
        port_match = _PRAGMA_PORT_RE.search(options)
        if port_match is None:
            continue
        port = port_match.group(1)
        bundle_match = _PRAGMA_BUNDLE_RE.search(options)
        bundle = bundle_match.group(1) if bundle_match else "gmem"
        previous = mapping.get(port)
        if previous is not None and previous != bundle:
            return {}, mapping, "conflicting", f"port={port}"
        mapping[port] = bundle
    if not mapping:
        # A generated design may omit pragmas while the runtime contract still
        # identifies all MAXI pointer ports. Unbundled MAXI ports use Vitis'
        # documented default memory bundle.
        return (
            {"gmem": max(source_depths.values())},
            {port: "gmem" for port in source_depths},
            "default_bundle",
            "no_m_axi_pragmas",
        )
    hardware_depths: dict[str, int] = {}
    mapped_ports = sorted(set(source_depths) & set(mapping))
    for source_port in mapped_ports:
        depth = source_depths[source_port]
        bundle = mapping[source_port]
        hardware_depths[bundle] = max(hardware_depths.get(bundle, 0), depth)
    unmapped_ports = sorted(set(source_depths) - set(mapping))
    reason = (
        "unmapped_non_maxi_ports=" + ",".join(unmapped_ports)
        if unmapped_ports
        else None
    )
    return dict(sorted(hardware_depths.items())), mapping, "inferred", reason


def _candidate_pointer_depth_directives(
    source: str,
    top: str,
    source_depths: Mapping[str, int],
    maxi_ports: Mapping[str, str] | None = None,
) -> dict[str, int]:
    """Build source-port directives from the candidate's actual ABI.

    Runtime contracts describe logical source ports, while Vitis may later
    combine those ports into one hardware MAXI bundle.  The source-port
    directive still needs each port's own contracted depth: using the bundle
    maximum for every pointer can make the cosimulation wrapper read past
    small fixed-size pointer arguments.  Candidate parameter names are part
    of the frozen testbench ABI, so an unbound name is left without a
    directive instead of being assigned a guessed depth.
    """

    interface = extract_top_interface(
        source,
        top,
        require_definition=False,
    )
    if interface is None:
        return {
            name: source_depths[name]
            for name in source_depths
            if not maxi_ports or name in maxi_ports
        }
    pointer_names = [
        parameter.name
        for parameter in interface.parameters
        if parameter.pointer_like and parameter.name
    ]
    if not pointer_names:
        return {}
    if maxi_ports:
        directive_names = [name for name in pointer_names if name in maxi_ports]
    else:
        directive_names = pointer_names
    if set(directive_names) != set(source_depths) and not maxi_ports:
        return {}
    return {
        name: source_depths[name]
        for name in directive_names
        if name in source_depths
    }


def _runtime_contract_shape_valid(
    contract: Mapping[str, Any] | None,
) -> bool:
    if not isinstance(contract, Mapping):
        return False
    version = contract.get("schema_version")
    base = {"schema_version", "kind", "candidate_mismatch_returncodes"}
    expected = (
        base
        if version == 1
        else (base | {"cosim_interface_depths"} if version == 2 else None)
    )
    if expected is None or set(contract) != expected:
        return False
    if contract.get("kind") != _PUBLIC_DIFFERENTIAL_RUNTIME_CONTRACT_KIND:
        return False
    if not isinstance(contract.get("candidate_mismatch_returncodes"), (list, tuple)):
        return False
    if version == 2:
        try:
            depths = _normalize_cosim_interface_depths(
                contract.get("cosim_interface_depths")
            )
        except (TypeError, ValueError):
            return False
        if not depths:
            return False
    return True


def _candidate_returncode_authorized(
    contract: Mapping[str, Any] | None,
    returncode: int,
) -> bool:
    return bool(
        _runtime_contract_shape_valid(contract)
        and returncode in contract.get("candidate_mismatch_returncodes", ())
    )


def _cosim_interface_depths(
    contract: Mapping[str, Any] | None,
) -> dict[str, int]:
    if contract is None:
        return {}
    if not _runtime_contract_shape_valid(contract):
        raise ValueError("invalid Public runtime contract for COSIM")
    if contract.get("schema_version") == 1:
        return {}
    return _normalize_cosim_interface_depths(
        contract.get("cosim_interface_depths")
    )


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    copied = json.loads(
        json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(
                copied,
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _atomic_text(path: Path, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"text must not be empty: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(value)
            if not value.endswith("\n"):
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _file_sha256(path: Path) -> str | None:
    if path.is_symlink() or not path.is_file():
        return None
    return sha256(path.read_bytes()).hexdigest()


def _bounded_text(value: Any, limit: int = 12000) -> str:
    return str(value or "")[-limit:]


def _invocation_diagnostic(invocation: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for section_name in (
        "version_probe_execution",
        "execution",
        "command_status",
        "typed_outcome",
    ):
        section = invocation.get(section_name)
        if isinstance(section, Mapping):
            for field in ("stdout", "stderr", "error", "reason_code"):
                value = section.get(field)
                if value:
                    parts.append(f"{section_name}.{field}: {value}")
    return "\n".join(parts)[-12000:]


def _usage(value: BudgetUsage) -> dict[str, Any]:
    return value.to_dict()


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must not be empty")
    if any(character in cleaned for character in ("\x00", "\r", "\n")):
        raise ValueError(f"{name} must not contain NUL or newline characters")
    return cleaned


def _tcl_quote(value: str, name: str) -> str:
    cleaned = _required_text(value, name)
    escaped = (
        cleaned.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )
    return f'"{escaped}"'


def _cosim_argv_value(path: Path) -> str:
    """Return one fail-closed testbench argument for Vitis HLS 2023.2.

    Both ``csim_design -argv`` and ``cosim_design -argv`` are documented to
    pass the supplied argument string to the C/C++ testbench ``main``.  The
    typed outcome path therefore travels as runtime data rather than through
    the Tcl/config/Make/C-preprocessor flag pipeline.
    """

    if not isinstance(path, Path):
        raise TypeError("COSIM outcome path must be Path")
    value = _required_text(str(path), "COSIM outcome argv path")
    allowed = frozenset(
        "abcdefghijklmnopqrstuvwxyz"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789/._-:+@%"
    )
    if any(character not in allowed for character in value):
        raise ValueError(
            "COSIM outcome argv path contains a character unsafe for the "
            "Vitis 2023.2 testbench argument contract"
        )
    return value

_WRAPPED_MAIN_NAME = "agrefactor_public_testbench_main"
def _build_typed_outcome_adapter(
    testbench_code: str,
    *,
    base_identity: Mapping[str, str],
) -> tuple[str, str, dict[str, Any]]:
    return build_typed_testbench_adapter(
        testbench_code,
        wrapped_main_name=_WRAPPED_MAIN_NAME,
        base_identity=base_identity,
        allowed_phases=("csim_prerequisite", "cosim"),
    )

def _normalize_version(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if cleaned.casefold().startswith("v"):
        cleaned = cleaned[1:]
    return cleaned or None


def _write_sources(
    root: Path,
    *,
    original_code: str,
    candidate_code: str,
    testbench_code: str,
    base_identity: Mapping[str, str],
) -> dict[str, Path]:
    instrumented, wrapper, adapter = _build_typed_outcome_adapter(
        testbench_code,
        base_identity=base_identity,
    )
    files = {
        "candidate": root / "candidate.cpp",
        "reference": root / "reference.cpp",
        "testbench_original": root / "public_testbench_original.cpp",
        "testbench": root / "public_testbench.cpp",
        "wrapper": root / "agrefactor_cosim_wrapper.cpp",
    }
    for role, code in (
        ("candidate", candidate_code),
        ("reference", original_code),
        ("testbench_original", testbench_code),
        ("testbench", instrumented),
        ("wrapper", wrapper),
    ):
        if not isinstance(code, str) or not code.strip():
            raise ValueError(f"{role} source must not be empty")
        _atomic_text(files[role], code.rstrip() + "\n")
    adapter_path = root / "typed_outcome_adapter.json"
    adapter["original_testbench_sha256"] = _file_sha256(
        files["testbench_original"]
    )
    adapter["instrumented_testbench_sha256"] = _file_sha256(
        files["testbench"]
    )
    adapter["wrapper_sha256"] = _file_sha256(files["wrapper"])
    _atomic_json(adapter_path, adapter)
    return files


def _version_probe_tcl(path: Path) -> str:
    return "\n".join(
        (
            f"set f [open {_tcl_quote(str(path), 'version evidence path')} w]",
            "puts $f [version -short]",
            "close $f",
            "exit 0",
            "",
        )
    )


def make_vitis_cosim_tcl(
    *,
    root: Path,
    top: str,
    files: Mapping[str, Path],
    profile: TargetProfile,
    typed_execution_id: str | None = None,
    interface_depths: Mapping[str, int] | None = None,
    hardware_depths: Mapping[str, int] | None = None,
) -> str:
    """Build a Vitis Tcl chain with structured per-command outcome phases."""

    if not isinstance(root, Path):
        raise TypeError("root must be Path")
    top = _required_text(top, "top")
    if not isinstance(profile, TargetProfile):
        raise TypeError("profile must be TargetProfile")
    if profile.toolchain != "vitis_hls":
        raise ValueError("COSIM requires toolchain='vitis_hls'")
    if profile.device is None or profile.clock_period_ns is None:
        raise ValueError("COSIM requires a concrete device and clock")
    required_roles = {"candidate", "reference", "testbench"}
    adapter_roles = {"testbench_original", "wrapper"}
    observed_roles = set(files)
    if not required_roles.issubset(observed_roles):
        raise ValueError("files must contain candidate/reference/testbench")
    extra_roles = {
        role for role in observed_roles if role.startswith("extra_source_")
    }
    unexpected_roles = observed_roles - required_roles - adapter_roles - extra_roles
    if unexpected_roles:
        raise ValueError("files contain unexpected COSIM roles: " + ", ".join(sorted(unexpected_roles)))
    if ("wrapper" in files) != ("testbench_original" in files):
        raise ValueError("deterministic COSIM wrapper roles must be supplied together")
    execution_id = typed_execution_id or ("0" * 32)
    if re.fullmatch(r"[0-9a-f]{32}", execution_id) is None:
        raise ValueError("typed_execution_id must be 32 lowercase hex characters")
    normalized_depths = _normalize_cosim_interface_depths(interface_depths)
    normalized_hardware_depths = (
        _normalize_cosim_interface_depths(hardware_depths)
        if hardware_depths is not None
        else normalized_depths
    )

    status_path = root / "cosim_command_status.json"
    typed_outcome_path = root / "agrefactor_cosim_outcome.json"
    outcome_argv = _cosim_argv_value(typed_outcome_path)
    compile_flags = " ".join(profile.compile_flags)
    testbench_flags = compile_flags

    def add_line(path: Path, *, testbench: bool, flags: str) -> str:
        line = "add_files "
        if testbench:
            line += "-tb "
        line += _tcl_quote(str(path), "source path")
        if flags:
            line += " -cflags " + _tcl_quote(flags, "compile flags")
        return line

    lines = [
        "proc ag_write_status {path status phase reason} {",
        "  set f [open $path w]",
        '  puts $f "{\\"schema_version\\":1,\\"status\\":\\"$status\\",\\"phase\\":\\"$phase\\",\\"reason_code\\":\\"$reason\\"}"',
        "  close $f",
        "}",
        f"set ag_status {_tcl_quote(str(status_path), 'status path')}",
        f"set ag_typed {_tcl_quote(str(typed_outcome_path), 'typed outcome path')}",
        f"set ag_csim_argv [list {_tcl_quote(outcome_argv, 'typed outcome argv')} {_tcl_quote(execution_id, 'execution id')} {_tcl_quote('csim_prerequisite', 'phase')}]",
        f"set ag_cosim_argv [list {_tcl_quote(outcome_argv, 'typed outcome argv')} {_tcl_quote(execution_id, 'execution id')} {_tcl_quote('cosim', 'phase')}]",
        "open_project -reset agrefactor_public_cosim",
        f"set_top {_tcl_quote(top, 'top')}",
        add_line(files["candidate"], testbench=False, flags=compile_flags),
        add_line(files["reference"], testbench=True, flags=testbench_flags),
        add_line(files["testbench"], testbench=True, flags=testbench_flags),
        *([add_line(files["wrapper"], testbench=True, flags=testbench_flags)] if "wrapper" in files else []),
        *[
            add_line(files[role], testbench=True, flags=testbench_flags)
            for role in sorted(extra_roles)
        ],
        "open_solution -reset -flow_target vitis solution",
        f"set_part {_tcl_quote(profile.device, 'target device')}",
        f"create_clock -period {profile.clock_period_ns} -name default",
        *[
            "set_directive_interface -mode m_axi -depth "
            f"{depth} {_tcl_quote(top, 'top')} "
            f"{_tcl_quote(port, 'COSIM depth port')}"
            for port, depth in normalized_hardware_depths.items()
        ],
        (
            "if {[catch {csim_design -clean -argv $ag_csim_argv} ag_msg]} { "
            "ag_write_status $ag_status failed csim_prerequisite "
            "cosim_csim_prerequisite_failed; close_project; exit 21 }"
        ),
        "file delete -force $ag_typed",
        (
            "if {[catch {csynth_design} ag_msg]} { "
            "ag_write_status $ag_status failed csynth_prerequisite "
            "cosim_csynth_prerequisite_failed; close_project; exit 22 }"
        ),
        (
            "if {[catch {cosim_design -tool xsim -rtl verilog -argv $ag_cosim_argv} ag_msg]} { "
            "ag_write_status $ag_status failed cosim "
            "cosim_command_failed; close_project; exit 23 }"
        ),
        "ag_write_status $ag_status passed cosim cosim_passed",
        "close_project",
        "exit 0",
    ]
    rendered = "\n".join(lines) + "\n"
    if "cosim_design" not in rendered:
        raise AssertionError("COSIM Tcl must invoke cosim_design")
    return rendered

def _read_json_object(path: Path) -> dict[str, Any] | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return dict(value) if isinstance(value, Mapping) else None


def _typed_outcome(
    path: Path,
    *,
    expected_identity: Mapping[str, str],
) -> dict[str, Any] | None:
    return read_typed_testbench_outcome(
        path,
        expected_identity=expected_identity,
    )


def _typed_started(
    path: Path,
    *,
    expected_identity: Mapping[str, str],
) -> dict[str, Any] | None:
    return read_typed_testbench_started(
        path,
        expected_identity=expected_identity,
    )


def _cosim_report_text(root: Path, execution: Mapping[str, Any]) -> tuple[str, list[str]]:
    # Collect bounded, generic COSIM phase evidence from this invocation.
    parts = [
        str(execution.get("stdout") or ""),
        str(execution.get("stderr") or ""),
    ]
    sources = ["execution.stdout", "execution.stderr"]
    seen: set[str] = set()
    candidate_roots = [
        root / "agrefactor_public_cosim" / "solution" / "sim" / "report",
        root / "agrefactor_public_cosim" / "solution" / "sim",
    ]
    for base in candidate_roots:
        if not base.is_dir() or base.is_symlink():
            continue
        for candidate in sorted(base.rglob("*")):
            if len(sources) >= 24 or not candidate.is_file() or candidate.is_symlink():
                continue
            if candidate.suffix.casefold() not in {".log", ".rpt", ".txt", ".jou", ".tcl"}:
                continue
            try:
                relative = str(candidate.relative_to(root))
                if relative in seen:
                    continue
                seen.add(relative)
                size = candidate.stat().st_size
                with candidate.open("rb") as handle:
                    if size > 32768:
                        handle.seek(-32768, 2)
                    data = handle.read(32768)
                content = data.decode("utf-8", errors="replace")
                parts.append(f"SOURCE {relative}\n{content}")
                sources.append(relative)
            except OSError:
                continue
    return "\n".join(parts)[-180000:], sources


def _derive_cosim_subphase_evidence(
    root: Path,
    execution: Mapping[str, Any],
    command_status: Mapping[str, Any] | None,
    typed_started: Mapping[str, Any] | None,
    typed: Mapping[str, Any] | None,
) -> dict[str, Any]:
    # Classify only observed COSIM subphases; unknown remains explicit.
    text, sources = _cosim_report_text(root, execution)
    lowered = text.casefold()
    c_tb_started = bool(
        typed_started is not None
        or typed is not None
        or re.search(r"starting\s+c\s*tb|c\s*tb\s+testing", lowered)
    )
    c_tb_completed = bool(
        typed is not None
        or re.search(r"c\s*tb\s+testing\s+(?:passed|failed)|testbench\s+return", lowered)
    )
    c_tb_failed = bool(
        (typed is not None and typed.get("status") == "failed")
        or re.search(
            r"c\s*tb\s+(?:simulation\s+)?failed|c/rtl\s+co-?simulation\s+file\s+generation\s+failed|stop\s+generating\s+test\s+vectors",
            lowered,
        )
    )
    rtl_started = bool(
        re.search(
            r"using\s+xsim\s+for\s+rtl\s+simulation|starting\s+(?:verilog\s+)?simulation|starting\s+xsim|rtl\s+simulation\s*:|#\s*xsim\s+v|\#\s*command\s+line:\s*xsim|\bxsim\b[^\n]*(?:start|run|launch)",
            lowered,
        )
    )
    rtl_completed = bool(
        re.search(
            r"rtl\s+simulation\s*:\s*[^\n]*(?:pass|fail|complete|finish)|\$finish\s+called|cosim[^\n]*(?:finished|completed):\s*pass",
            lowered,
        )
    )
    command_started = command_status is not None or bool(execution.get("cosim_launched"))
    if rtl_completed or rtl_started:
        phase = "rtl"
    elif c_tb_failed or c_tb_completed or c_tb_started:
        phase = "c_testbench"
    else:
        phase = "unconfirmed"
    return {
        "schema_version": 1,
        "phase": phase,
        "cosim_command_started": command_started,
        "c_testbench_started": c_tb_started,
        "c_testbench_completed": c_tb_completed,
        "c_testbench_failed": c_tb_failed,
        "rtl_started": rtl_started,
        "rtl_completed": rtl_completed,
        "postprocess_completed": bool(
            command_status is not None and command_status.get("status") == "passed"
        ),
        "evidence_sources": sources,
    }


def _command_status(path: Path) -> dict[str, Any] | None:
    value = _read_json_object(path)
    if value is None or set(value) != {
        "schema_version",
        "status",
        "phase",
        "reason_code",
    }:
        return None
    if value.get("schema_version") != 1:
        return None
    if value.get("status") not in {"passed", "failed"}:
        return None
    if value.get("phase") not in {
        "csim_prerequisite",
        "csynth_prerequisite",
        "cosim",
    }:
        return None
    reason = value.get("reason_code")
    if not isinstance(reason, str) or not reason.strip():
        return None
    return value


def _budget_block(
    invocation: dict[str, Any],
    *,
    section: str,
    checkpoint: str,
    increment: Mapping[str, int],
    exc: BudgetExceededError,
) -> None:
    invocation["budget"]["status"] = "blocked"
    invocation["budget"][section] = {
        "status": "blocked",
        "checkpoint": checkpoint,
        "requested_increment": dict(increment),
        "resource": exc.resource,
        "limit": exc.limit,
        "attempted": exc.attempted,
    }


def _finalize_result(
    invocation_path: Path,
    invocation: dict[str, Any],
    *,
    status: str,
    failure_kind: str | None,
    failure_owner: str,
    reason_code: str,
    timed_out: bool,
    returncode: int | None,
    version_probe_launched: bool,
    cosim_launched: bool,
) -> dict[str, Any]:
    summary = {
        "schema_version": COSIM_SCHEMA_VERSION,
        "status": status,
        "failure_kind": failure_kind,
        "failure_owner": failure_owner,
        "reason_code": reason_code,
        "timed_out": timed_out,
        "returncode": returncode,
        "tool_launched": version_probe_launched or cosim_launched,
        "version_probe_launched": version_probe_launched,
        "cosim_launched": cosim_launched,
        "subphase_evidence": invocation.get(
            "subphase_evidence",
            {"schema_version": 1, "phase": "unconfirmed"},
        ),
    }
    invocation["result_summary"] = summary
    _atomic_json(invocation_path, invocation)
    evidence_sha = _file_sha256(invocation_path)
    if evidence_sha is None:
        raise RuntimeError("COSIM invocation evidence was not persisted")
    return {
        **summary,
        "diagnostic": _invocation_diagnostic(invocation) or None,
        "evidence_sha256": evidence_sha,
        "invocation_path": str(invocation_path),
    }


def run_vitis_cosim(
    *,
    work_dir: str | os.PathLike[str],
    original_code: str,
    candidate_code: str,
    testbench_code: str,
    candidate_top_function: str,
    target_profile: TargetProfile | Mapping[str, Any] | str | None,
    timelimit: int,
    budget: BudgetManager | None = None,
    suite_id: str = "public",
    runtime_contract: Mapping[str, Any] | None = None,
    extra_sources: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Run one Public suite through a real Vitis RTL COSIM chain."""

    if isinstance(timelimit, bool) or not isinstance(timelimit, int):
        raise TypeError("timelimit must be an integer")
    if timelimit <= 0:
        raise ValueError("timelimit must be positive")
    if budget is not None and not isinstance(budget, BudgetManager):
        raise TypeError("budget must be BudgetManager or None")
    suite_id = _required_text(suite_id, "suite_id")
    top = _required_text(candidate_top_function, "candidate_top_function")
    interface_depths = _cosim_interface_depths(runtime_contract)
    hardware_depths: dict[str, int] = {}
    directive_depths: dict[str, int] = {}
    source_to_hardware: dict[str, str] = {}
    interface_mapping_status = "not_attempted"
    interface_mapping_reason: str | None = None

    root = Path(work_dir).expanduser().resolve()
    if root.exists() and (root.is_symlink() or not root.is_dir()):
        raise ValueError("work_dir must be a real directory")
    root.mkdir(parents=True, exist_ok=True)
    normalized_extra_sources: list[str] = []
    for raw_path in extra_sources or ():
        path = str(raw_path)
        normalized = path.replace("\\", "/")
        if (
            not path
            or os.path.isabs(path)
            or ".." in normalized.split("/")
        ):
            raise ValueError(
                "extra_sources must contain relative package paths"
            )
        if path not in normalized_extra_sources:
            normalized_extra_sources.append(path)
        if not (root / path).is_file():
            raise FileNotFoundError(
                f"extra source not found in COSIM work directory: {path}"
            )

    invocation_path = root / "cosim_invocation.json"
    version_path = root / "toolchain_version.txt"
    tcl_path = root / "vitis.tcl"
    status_path = root / "cosim_command_status.json"
    typed_path = root / "agrefactor_cosim_outcome.json"
    typed_started_path = root / "agrefactor_cosim_outcome.json.started"
    for stale in (version_path, status_path, typed_path, typed_started_path):
        if stale.exists():
            if stale.is_symlink() or not stale.is_file():
                raise ValueError(f"unsafe stale COSIM evidence path: {stale}")
            stale.unlink()

    profile = resolve_target_profile(target_profile)
    resolution = resolve_csynth_command(profile)
    csim_identity = make_typed_outcome_identity(
        phase="csim_prerequisite",
        suite_id=suite_id,
        candidate_code=candidate_code,
        testbench_code=testbench_code,
    )
    cosim_identity = make_typed_outcome_identity(
        phase="cosim",
        suite_id=suite_id,
        candidate_code=candidate_code,
        testbench_code=testbench_code,
        execution_id=csim_identity["execution_id"],
    )
    files = _write_sources(
        root,
        original_code=original_code,
        candidate_code=candidate_code,
        testbench_code=testbench_code,
        base_identity=csim_identity,
    )
    for index, path in enumerate(normalized_extra_sources, start=1):
        files[f"extra_source_{index:03d}"] = root / path

    if interface_depths:
        (
            hardware_depths,
            source_to_hardware,
            interface_mapping_status,
            interface_mapping_reason,
        ) = _infer_maxi_hardware_depths(
            candidate_code,
            interface_depths,
            top,
        )
        directive_depths = _candidate_pointer_depth_directives(
            candidate_code,
            top,
            interface_depths,
            source_to_hardware,
        )
    else:
        interface_mapping_status = "not_required"

    invocation: dict[str, Any] = {
        "schema_version": COSIM_SCHEMA_VERSION,
        "phase": "public_rtl_cosim",
        "suite_id": suite_id,
        "execution_backend": "native_vitis",
        "native_vitis_cosim": True,
        "outcome_transport": "testbench_argv",
        "typed_outcome_identities": {
            "csim_prerequisite": csim_identity,
            "cosim": cosim_identity,
        },
        "runtime_contract": runtime_contract,
        "cosim_interface_depths": interface_depths,
        "cosim_hardware_depths": hardware_depths,
        "cosim_directive_depths": directive_depths,
        "source_to_maxi_hardware": source_to_hardware,
        "interface_mapping": {
            "status": interface_mapping_status,
            "reason": interface_mapping_reason,
        },
        "typed_outcome_adapter": {
            "kind": "raw_runtime_atomic_wrapper_v2",
            "evidence_path": str(
                root / "typed_outcome_adapter.json"
            ),
            "evidence_sha256": _file_sha256(
                root / "typed_outcome_adapter.json"
            ),
            "failure_owner_inferred": False,
        },
        "rtl_language": "verilog",
        "simulator": "xsim",
        "work_dir": str(root),
        "top_kernel": top,
        "source_roles": {
            "design": str(files["candidate"]),
            "testbench_reference": str(files["reference"]),
            "testbench_driver": str(files["testbench"]),
            **{
                role: str(files[role])
                for role in sorted(files)
                if role.startswith("extra_source_")
            },
        },
        "target_profile": profile.to_effective_dict(),
        "requested_toolchain_version": profile.toolchain_version,
        "toolchain_version_verification": {
            "status": "pending",
            "requested": profile.toolchain_version,
            "actual": None,
            "evidence_source": "vitis_tcl_version_file",
            "evidence_sha256": None,
        },
        "command": resolution.get("command"),
        "command_source": resolution.get("command_source"),
        "executable": resolution.get("executable"),
        "resolved_executable": resolution.get("resolved_executable"),
        "settings_path": resolution.get("settings_path"),
        "resolved_settings_path": resolution.get("resolved_settings_path"),
        "probe_source": resolution.get("probe_source"),
        "profile_name": resolution.get("profile_name"),
        "effective_value_provenance": resolution.get(
            "effective_value_provenance",
            {},
        ),
        "timeout_seconds": timelimit,
        "tcl_path": str(tcl_path),
        "tcl_sha256": None,
        "budget": {
            "status": "not_configured" if budget is None else "pending",
            "version_probe": {
                "status": "not_configured" if budget is None else "pending",
                "requested_increment": dict(VERSION_PROBE_BUDGET_INCREMENT),
            },
            "cosim_launch": {
                "status": "not_configured" if budget is None else "pending",
                "requested_increment": dict(COSIM_BUDGET_INCREMENT),
            },
        },
        "version_probe_execution": {
            "status": "pending",
            "returncode": None,
            "timeout": False,
        },
        "execution": {
            "status": "pending",
            "returncode": None,
            "timeout": False,
            "cosim_launched": False,
        },
        "command_status": {"status": "pending"},
        "typed_outcome": {"status": "pending"},
        "typed_outcome_started": {"status": "pending"},
        "subphase_evidence": {
            "schema_version": 1,
            "phase": "unconfirmed",
            "cosim_command_started": False,
            "c_testbench_started": False,
            "c_testbench_completed": False,
            "c_testbench_failed": False,
            "rtl_started": False,
            "rtl_completed": False,
            "postprocess_completed": False,
            "evidence_sources": [],
        },
    }
    _atomic_json(invocation_path, invocation)

    version_probe_launched = False
    if interface_mapping_status in {"conflicting", "incomplete"}:
        return _finalize_result(
            invocation_path,
            invocation,
            status="failed",
            failure_kind="runtime_contract_interface_mismatch",
            failure_owner="configuration",
            reason_code="cosim_source_to_hardware_mapping_unresolved",
            timed_out=False,
            returncode=None,
            version_probe_launched=False,
            cosim_launched=False,
        )
    if budget is not None:
        try:
            usage_before = budget.snapshot()
            budget.ensure_available(**VERSION_PROBE_BUDGET_INCREMENT)
            usage_after = budget.consume(**VERSION_PROBE_BUDGET_INCREMENT)
        except BudgetExceededError as exc:
            _budget_block(
                invocation,
                section="version_probe",
                checkpoint="before_version_probe_launch",
                increment=VERSION_PROBE_BUDGET_INCREMENT,
                exc=exc,
            )
            invocation["version_probe_execution"]["status"] = (
                "blocked_by_budget"
            )
            invocation["execution"]["status"] = "blocked_by_budget"
            return _finalize_result(
                invocation_path,
                invocation,
                status="blocked",
                failure_kind="budget_exhausted",
                failure_owner="configuration",
                reason_code="version_probe_tool_budget_exhausted",
                timed_out=False,
                returncode=None,
                version_probe_launched=False,
                cosim_launched=False,
            )
        invocation["budget"]["status"] = "partially_consumed"
        invocation["budget"]["version_probe"] = {
            "status": "consumed",
            "checkpoint": "before_version_probe_launch",
            "requested_increment": dict(VERSION_PROBE_BUDGET_INCREMENT),
            "usage_before": _usage(usage_before),
            "usage_after": _usage(usage_after),
        }

    _atomic_text(tcl_path, _version_probe_tcl(version_path))
    invocation["tcl_sha256"] = _file_sha256(tcl_path)
    _atomic_json(invocation_path, invocation)
    try:
        version_probe_launched = True
        probe = tools.general.run_cmd(
            str(root),
            resolution["command"],
            min(timelimit, _VERSION_PROBE_TIMEOUT_S),
        )
    except Exception as exc:
        invocation["version_probe_execution"] = {
            "status": "launch_error",
            "returncode": None,
            "timeout": False,
            "error_type": type(exc).__name__,
        }
        invocation["execution"]["status"] = "blocked_before_cosim"
        invocation["toolchain_version_verification"]["status"] = (
            "probe_launch_failed"
        )
        return _finalize_result(
            invocation_path,
            invocation,
            status="error",
            failure_kind="toolchain_failure",
            failure_owner="toolchain",
            reason_code="cosim_toolchain_probe_launch_failed",
            timed_out=False,
            returncode=None,
            version_probe_launched=True,
            cosim_launched=False,
        )

    probe_returncode = (
        probe.get("returncode")
        if isinstance(probe.get("returncode"), int)
        else None
    )
    probe_timeout = probe.get("timeout") is True
    invocation["version_probe_execution"] = {
        "status": "completed",
        "returncode": probe_returncode,
        "timeout": probe_timeout,
        "stdout": _bounded_text(probe.get("stdout")),
        "stderr": _bounded_text(probe.get("stderr")),
    }
    actual = None
    if version_path.is_file() and not version_path.is_symlink():
        actual = _normalize_version(
            version_path.read_text(encoding="utf-8", errors="replace")
        )
    requested = _normalize_version(profile.toolchain_version)
    matched = (
        not probe_timeout
        and probe_returncode == 0
        and actual is not None
        and (requested is None or actual == requested)
    )
    invocation["toolchain_version_verification"] = {
        "status": "matched" if matched else "unverified",
        "requested": requested,
        "actual": actual,
        "probe_returncode": probe_returncode,
        "probe_timeout": probe_timeout,
        "evidence_source": "vitis_tcl_version_file",
        "evidence_sha256": _file_sha256(version_path),
    }
    if not matched:
        invocation["execution"]["status"] = "blocked_before_cosim"
        return _finalize_result(
            invocation_path,
            invocation,
            status="error",
            failure_kind=("timeout" if probe_timeout else "toolchain_failure"),
            failure_owner=("unknown" if probe_timeout else "toolchain"),
            reason_code=(
                "cosim_version_probe_timeout"
                if probe_timeout
                else "cosim_toolchain_version_unverified"
            ),
            timed_out=probe_timeout,
            returncode=probe_returncode,
            version_probe_launched=True,
            cosim_launched=False,
        )

    for stale in (status_path, typed_path, typed_started_path):
        stale.unlink(missing_ok=True)
    _atomic_text(
        tcl_path,
        make_vitis_cosim_tcl(
            root=root,
            top=top,
            files=files,
            profile=profile,
            typed_execution_id=csim_identity["execution_id"],
            interface_depths=directive_depths or interface_depths,
        ),
    )
    invocation["tcl_sha256"] = _file_sha256(tcl_path)

    if budget is not None:
        try:
            usage_before = budget.snapshot()
            budget.ensure_available(**COSIM_BUDGET_INCREMENT)
            usage_after = budget.consume(**COSIM_BUDGET_INCREMENT)
        except BudgetExceededError as exc:
            _budget_block(
                invocation,
                section="cosim_launch",
                checkpoint="before_cosim_launch",
                increment=COSIM_BUDGET_INCREMENT,
                exc=exc,
            )
            invocation["execution"] = {
                "status": "blocked_by_budget",
                "returncode": None,
                "timeout": False,
                "cosim_launched": False,
            }
            return _finalize_result(
                invocation_path,
                invocation,
                status="blocked",
                failure_kind="budget_exhausted",
                failure_owner="configuration",
                reason_code="cosim_launch_budget_exhausted",
                timed_out=False,
                returncode=None,
                version_probe_launched=True,
                cosim_launched=False,
            )
        invocation["budget"]["status"] = "consumed"
        invocation["budget"]["cosim_launch"] = {
            "status": "consumed",
            "checkpoint": "before_cosim_launch",
            "requested_increment": dict(COSIM_BUDGET_INCREMENT),
            "usage_before": _usage(usage_before),
            "usage_after": _usage(usage_after),
        }
    _atomic_json(invocation_path, invocation)

    try:
        result = tools.general.run_cmd(
            str(root),
            resolution["command"],
            timelimit,
        )
    except Exception as exc:
        invocation["execution"] = {
            "status": "launch_error",
            "returncode": None,
            "timeout": False,
            "cosim_launched": False,
            "error_type": type(exc).__name__,
        }
        return _finalize_result(
            invocation_path,
            invocation,
            status="error",
            failure_kind="toolchain_failure",
            failure_owner="toolchain",
            reason_code="cosim_launch_failed",
            timed_out=False,
            returncode=None,
            version_probe_launched=True,
            cosim_launched=False,
        )

    returncode = (
        result.get("returncode")
        if isinstance(result.get("returncode"), int)
        else None
    )
    timed_out = result.get("timeout") is True
    invocation["execution"] = {
        "status": "completed",
        "returncode": returncode,
        "timeout": timed_out,
        "cosim_launched": True,
        "stdout": _bounded_text(result.get("stdout")),
        "stderr": _bounded_text(result.get("stderr")),
    }
    command_status = _command_status(status_path)
    typed = _typed_outcome(
        typed_path,
        expected_identity=cosim_identity,
    )
    typed_started = _typed_started(
        typed_started_path,
        expected_identity=cosim_identity,
    )
    invocation["command_status"] = (
        command_status
        if command_status is not None
        else {"status": "missing_or_invalid"}
    )
    invocation["typed_outcome"] = (
        typed if typed is not None else {"status": "missing_or_invalid"}
    )
    invocation["typed_outcome_started"] = (
        typed_started if typed_started is not None else {"status": "missing_or_invalid"}
    )
    invocation["subphase_evidence"] = _derive_cosim_subphase_evidence(
        root,
        invocation["execution"],
        command_status,
        typed_started,
        typed,
    )
    synthesized_interface = discover_top_io(root, expected_top=top)
    interface_contract_mismatch = False
    if synthesized_interface is None:
        invocation["synthesized_interface"] = {"status": "unknown"}
    else:
        actual_depth_ports = set(synthesized_interface.maxi_pointer_ports)
        declared_depth_ports = set(interface_depths)
        declared_maxi_depth_ports = set(
            source_to_hardware
            if interface_mapping_status == "inferred"
            else declared_depth_ports
        )
        actual_source_to_hardware = synthesized_interface.source_to_maxi_hardware
        actual_hardware_ports = set(synthesized_interface.maxi_hardware_ports)
        declared_hardware_ports = set(
            hardware_depths
            if interface_mapping_status in {"inferred", "default_bundle"}
            else interface_depths
        )
        source_mapping_mismatch = (
            interface_mapping_status == "inferred"
            and actual_source_to_hardware != source_to_hardware
        )
        source_port_mismatch = (
            interface_mapping_status != "default_bundle"
            and actual_depth_ports != declared_maxi_depth_ports
        )
        interface_contract_mismatch = (
            source_port_mismatch
            or actual_hardware_ports != declared_hardware_ports
            or source_mapping_mismatch
        )
        invocation["synthesized_interface"] = {
            "status": "observed",
            "top_name": synthesized_interface.top_name,
            "evidence_path": synthesized_interface.evidence_path,
            "maxi_pointer_ports": sorted(actual_depth_ports),
            "maxi_hardware_ports": sorted(actual_hardware_ports),
            "source_to_maxi_hardware": actual_source_to_hardware,
            "contract_depth_ports": sorted(declared_depth_ports),
            "source_port_names_verified": not (
                interface_mapping_status == "default_bundle"
            ),
            "contract_hardware_depth_ports": sorted(declared_hardware_ports),
            "contract_matches": not interface_contract_mismatch,
            "ports": [
                {
                    "source_name": port.source_name,
                    "source_type": port.source_type,
                    "is_pointer": port.is_pointer,
                    "bit_width": port.bit_width,
                    "size_or_depth": port.size_or_depth,
                    "hardware_interface": port.hardware_interface,
                    "hardware_name": port.hardware_name,
                }
                for port in synthesized_interface.ports
            ],
        }

    if interface_contract_mismatch:
        return _finalize_result(
            invocation_path,
            invocation,
            status="failed",
            failure_kind="runtime_contract_interface_mismatch",
            failure_owner="testbench",
            reason_code="cosim_depth_ports_do_not_match_synthesized_interface",
            timed_out=timed_out,
            returncode=returncode,
            version_probe_launched=True,
            cosim_launched=True,
        )

    post_completion_pass = (
        timed_out
        and command_status is not None
        and command_status.get("status") == "passed"
        and command_status.get("phase") == "cosim"
        and command_status.get("reason_code") == "cosim_passed"
        and typed is not None
        and typed.get("status") == "passed"
        and typed.get("identity_verified") is True
        and typed.get("testbench_returncode") == 0
    )
    if post_completion_pass:
        result = _finalize_result(
            invocation_path,
            invocation,
            status="passed",
            failure_kind=None,
            failure_owner="none",
            reason_code="cosim_passed_post_completion_process_linger",
            timed_out=True,
            returncode=returncode,
            version_probe_launched=True,
            cosim_launched=True,
        )
        completion = {
            "completion_authority": (
                "fresh_tcl_status_and_identity_bound_typed_outcome_v1"
            ),
            "command_completion_proven": True,
            "post_completion_process_linger": True,
            "process_exit_observed": False,
        }
        result.update(completion)
        invocation["result_summary"].update(completion)
        _atomic_json(invocation_path, invocation)
        evidence_sha = _file_sha256(invocation_path)
        if evidence_sha is None:
            raise RuntimeError(
                "COSIM post-completion evidence was not persisted"
            )
        result["evidence_sha256"] = evidence_sha
        return result

    if timed_out:
        return _finalize_result(
            invocation_path,
            invocation,
            status="failed",
            failure_kind="timeout",
            failure_owner="unknown",
            reason_code="cosim_timeout",
            timed_out=True,
            returncode=returncode,
            version_probe_launched=True,
            cosim_launched=True,
        )

    passed = (
        returncode == 0
        and command_status is not None
        and command_status.get("status") == "passed"
        and command_status.get("phase") == "cosim"
        and command_status.get("reason_code") == "cosim_passed"
        and typed is not None
        and typed.get("status") == "passed"
    )
    if passed:
        return _finalize_result(
            invocation_path,
            invocation,
            status="passed",
            failure_kind=None,
            failure_owner="none",
            reason_code="cosim_passed",
            timed_out=False,
            returncode=0,
            version_probe_launched=True,
            cosim_launched=True,
        )

    typed_returncode = (
        typed.get("testbench_returncode")
        if isinstance(typed, Mapping)
        else None
    )
    deterministic_candidate = (
        typed is not None
        and typed.get("status") == "failed"
        and isinstance(typed_returncode, int)
        and not isinstance(typed_returncode, bool)
        and command_status is not None
        and command_status.get("status") == "failed"
        and command_status.get("phase") == "cosim"
        and _candidate_returncode_authorized(runtime_contract, typed_returncode)
        and (
            invocation["subphase_evidence"].get("rtl_started") is True
            or invocation["subphase_evidence"].get("rtl_completed") is True
        )
    )
    if deterministic_candidate:
        result = _finalize_result(
            invocation_path,
            invocation,
            status="failed",
            failure_kind="candidate_rtl_functional_failure",
            failure_owner="candidate",
            reason_code="public_rtl_mismatch",
            timed_out=False,
            returncode=returncode,
            version_probe_launched=True,
            cosim_launched=True,
        )
        result["owner_authority"] = "deterministic_proven"
        result["testbench_returncode"] = typed_returncode
        invocation["result_summary"]["owner_authority"] = "deterministic_proven"
        invocation["result_summary"]["testbench_returncode"] = typed_returncode
        _atomic_json(invocation_path, invocation)
        result["evidence_sha256"] = _file_sha256(invocation_path)
        return result

    fallback_reason = (
        "cosim_c_testbench_failure_before_rtl"
        if invocation["subphase_evidence"].get("c_testbench_failed") is True
        and not invocation["subphase_evidence"].get("rtl_started")
        else "cosim_failed_without_subphase_evidence"
    )
    return _finalize_result(
        invocation_path,
        invocation,
        status="failed",
        failure_kind="ownership_unknown",
        failure_owner="unknown",
        reason_code=fallback_reason,
        timed_out=False,
        returncode=returncode,
        version_probe_launched=True,
        cosim_launched=True,
    )
