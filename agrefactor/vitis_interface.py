"""Read final interface facts emitted by Vitis HLS synthesis."""

from __future__ import annotations

from dataclasses import dataclass
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
