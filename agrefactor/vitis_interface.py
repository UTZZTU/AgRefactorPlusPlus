"""Read final interface facts emitted by Vitis HLS synthesis."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Optional
import xml.etree.ElementTree as ET


@dataclass(frozen=True, slots=True)
class VitisInterfacePort:
    source_name: str
    source_type: str
    is_pointer: bool
    bit_width: int | None
    size_or_depth: int | None
    hardware_interface: str | None
    hardware_name: str | None


@dataclass(frozen=True, slots=True)
class VitisTopInterface:
    top_name: str
    ports: tuple[VitisInterfacePort, ...]
    evidence_path: str

    @property
    def maxi_pointer_ports(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                port.source_name
                for port in self.ports
                if port.is_pointer and port.hardware_interface == "MAXI"
            )
        )

    @property
    def source_to_maxi_hardware(self) -> dict[str, str]:
        """Return the synthesized source-port to hardware-bundle mapping."""

        return {
            port.source_name: (port.hardware_name or port.source_name)
            for port in self.ports
            if port.is_pointer and port.hardware_interface == "MAXI"
        }

    @property
    def maxi_hardware_ports(self) -> tuple[str, ...]:
        """Return unique hardware bundles used by MAXI pointer ports."""

        return tuple(sorted(set(self.source_to_maxi_hardware.values())))


def _optional_int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_top_io(
    path: str | Path,
    *,
    expected_top: str | None = None,
) -> Optional[VitisTopInterface]:
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        return None
    try:
        root = ET.parse(source).getroot()
    except (OSError, ET.ParseError):
        return None
    kernels = [
        element
        for element in root.iter("kernel")
        if expected_top is None
        or element.get("src_name") == expected_top
    ]
    if root.tag == "kernel" and root not in kernels and (
        expected_top is None or root.get("src_name") == expected_top
    ):
        kernels.append(root)
    if len(kernels) != 1:
        return None
    kernel = kernels[0]
    ports: list[VitisInterfacePort] = []
    for argument in kernel.findall("./args/arg"):
        name = argument.get("src_name")
        if not name:
            return None
        hardware = argument.find("./hw")
        ports.append(
            VitisInterfacePort(
                source_name=name,
                source_type=argument.get("src_type") or "",
                is_pointer=argument.get("src_isptr") == "1",
                bit_width=_optional_int(argument.get("src_bitwidth")),
                size_or_depth=_optional_int(
                    argument.get("src_size_or_depth")
                ),
                hardware_interface=(
                    None if hardware is None else hardware.get("hw_interface")
                ),
                hardware_name=(
                    None if hardware is None else hardware.get("hw_name")
                ),
            )
        )
    return VitisTopInterface(
        top_name=kernel.get("src_name") or expected_top or "",
        ports=tuple(ports),
        evidence_path=str(source.resolve()),
    )


def discover_top_io(
    work_dir: str | Path,
    *,
    expected_top: str,
) -> Optional[VitisTopInterface]:
    root = Path(work_dir).resolve()
    if not root.is_dir():
        return None
    matches: list[VitisTopInterface] = []
    for path in root.rglob("top-io-fe.xml"):
        parsed = parse_top_io(path, expected_top=expected_top)
        if parsed is not None:
            matches.append(parsed)
    if len(matches) != 1:
        return None
    return matches[0]


def parse_current_top_io(path: str | Path, *, expected_top: str) -> Optional[VitisTopInterface]:
    """Validate complete source-port flags before granting current-report authority."""
    parsed = parse_top_io(path, expected_top=expected_top)
    if parsed is None:
        return None
    try:
        root = ET.parse(path).getroot()
        kernel = next(element for element in root.iter("kernel")
                      if element.get("src_name") == expected_top)
        arguments = kernel.findall("./args/arg")
        names = [argument.get("src_name") for argument in arguments]
        if (len(names) != len(set(names))
                or any(argument.get("src_isptr") not in {"0", "1"} for argument in arguments)
                or any(port.is_pointer and (not port.source_type
                           or not port.hardware_interface or not port.hardware_name)
                       for port in parsed.ports)):
            return None
        return parsed
    except (OSError, ET.ParseError, StopIteration):
        return None


def current_csynth_interface(
    work_dir: str | Path, *, candidate_code: str,
    expected_top: str, target_profile: dict,
) -> dict:
    """Read only an identity-bound report produced by this CSYNTH invocation."""

    root = Path(work_dir).resolve()
    receipt_path = root / "csynth_invocation.json"
    failure = {"status": "incomplete", "reason_code": "current_csynth_interface_unproven"}
    try:
        if receipt_path.is_symlink():
            return failure
        raw = receipt_path.read_bytes()
        receipt = json.loads(raw)
        execution = receipt.get("execution", {})
        report = receipt.get("expected_report", {})
        if not (receipt.get("top_kernel") == expected_top
                and receipt.get("target_profile") == target_profile
                and execution.get("status") == "completed"
                and execution.get("returncode") == 0
                and execution.get("timeout") is False
                and report.get("produced_by_current_invocation") is True
                and receipt.get("toolchain_version_verification", {}).get("status")
                in {"matched", "detected"}):
            return failure
        source = receipt.get("source_provenance", {}).get(expected_top + ".cpp", {})
        candidate_sha = sha256(candidate_code.encode("utf-8")).hexdigest()
        source_path = Path(source.get("path", ""))
        if not (source.get("role") == "candidate"
                and source.get("sha256") == candidate_sha
                and source_path.resolve().is_relative_to(root)
                and not source_path.is_symlink()
                and sha256(source_path.read_bytes()).hexdigest() == candidate_sha):
            return failure
        for unit in receipt.get("source_provenance", {}).values():
            unit_path = Path(unit.get("path", ""))
            if not (unit_path.resolve().is_relative_to(root) and not unit_path.is_symlink()
                    and sha256(unit_path.read_bytes()).hexdigest() == unit.get("sha256")):
                return failure
        interface_receipt = receipt.get("top_io", {})
        artifacts = interface_receipt.get("artifacts", [])
        if interface_receipt.get("isolated_before_launch") is not True or len(artifacts) != 1:
            return failure
        artifact = artifacts[0]
        path = Path(artifact.get("path", ""))
        if not (artifact.get("produced_by_current_invocation") is True
                and path.resolve().is_relative_to(root / "csynth")
                and not path.is_symlink()
                and sha256(path.read_bytes()).hexdigest() == artifact.get("sha256")):
            return failure
        parsed = parse_current_top_io(path, expected_top=expected_top)
        if parsed is None:
            return failure
        return {
            "status": "verified", "reason_code": "current_csynth_interface_verified",
            "csynth_tool_launched": True,
            "candidate_sha256": candidate_sha, "top_name": expected_top,
            "receipt_path": str(receipt_path), "receipt_sha256": sha256(raw).hexdigest(),
            "report_path": str(path), "report_sha256": artifact["sha256"],
            "actual_maxi_ports": list(parsed.maxi_pointer_ports),
            "source_to_hardware": parsed.source_to_maxi_hardware,
            "target_profile": target_profile,
        }
    except (OSError, ValueError, TypeError, AttributeError, StopIteration, ET.ParseError):
        return failure
