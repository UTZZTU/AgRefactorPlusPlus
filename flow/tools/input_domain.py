"""One finite input-domain contract shared by Public, Candidate, and Hidden."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from typing import Any, Optional

from agrefactor.cpp_interface import extract_top_interface
from flow.base_agent import HLSAgentLoader
import flow.tools as tools


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RETRIES = 3


def _source_parameters(
    source: str,
    kernel_name: str,
) -> Optional[dict[str, bool]]:
    interface = extract_top_interface(source, kernel_name)
    if interface is None:
        return None
    return {
        parameter.name: parameter.pointer_like
        for parameter in interface.parameters
    }


def _json_response(agent: Any, message: str, *, first_turn: bool) -> str:
    response = agent.run(
        message=message,
        max_turns=1,
        **({} if first_turn else {"clear_history": False}),
    )
    response.process()
    messages = getattr(response, "messages", None)
    if not isinstance(messages, list) or not messages:
        raise ValueError("input-domain agent returned no messages")
    content = messages[-1].get("content") if isinstance(messages[-1], dict) else None
    if not isinstance(content, str) or not content.strip():
        raise ValueError("input-domain agent returned empty content")
    return tools.general.strip_thinking(content).strip()


def normalize_input_domain_contract(
    value: Mapping[str, Any],
    *,
    source_code: str,
    kernel_name: str,
) -> dict[str, Any]:
    """Validate and normalize the small, deliberately finite contract."""
    errors: list[str] = []

    def report(reason: str, path: str, actual: Any, expected: str) -> None:
        try:
            rendered = json.dumps(actual, ensure_ascii=False, default=repr)
        except (TypeError, ValueError):
            rendered = repr(actual)
        errors.append(f"{reason}; path={path}; actual={rendered}; expected={expected}")

    def pointer_key(key: Any) -> str:
        return str(key).replace("~", "~0").replace("/", "~1")

    if not isinstance(value, Mapping):
        report("input domain must be a JSON object", "/", value, "JSON object")
        raise ValueError("\n".join(errors))
    allowed = {"schema_version", "kind", "dimensions", "buffers", "origin", "finite_domain"}
    if set(value) - allowed:
        for key in value:
            if key not in allowed:
                report("input domain contains unexpected fields", "/" + pointer_key(key),
                       value[key], "allowed fields " + json.dumps(sorted(allowed)))
    dimensions = value.get("dimensions")
    buffers = value.get("buffers")
    for field, mapping in (("dimensions", dimensions), ("buffers", buffers)):
        if not isinstance(mapping, Mapping):
            report("input domain requires dimensions and buffers mappings", "/" + field,
                   mapping, "JSON object")
    if value.get("finite_domain", True) is not True:
        report("input domain must be finite", "/finite_domain", value["finite_domain"], "true")
    if not isinstance(dimensions, Mapping) or not isinstance(buffers, Mapping):
        raise ValueError("\n".join(errors))
    params = _source_parameters(source_code, kernel_name)
    port_facts = (
        "allowed_ports=" + json.dumps(sorted(params))
        if params is not None else "interface_facts=unavailable"
    )
    normalized_dims: dict[str, dict[str, Any]] = {}
    for name, spec in dimensions.items():
        before = len(errors)
        path = "/dimensions/" + pointer_key(name)
        if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name):
            report("dimension names must be C identifiers", path, name, "C identifier")
        if not isinstance(spec, Mapping):
            report(f"dimension {name} must be an object", path, spec, "JSON object")
            continue
        port = spec.get("port")
        lo = spec.get("min")
        hi = spec.get("max")
        if not isinstance(port, str) or not _IDENTIFIER.fullmatch(port):
            report(f"dimension {name} must name a C identifier port", path + "/port",
                   port, "existing top-level C identifier port; " + port_facts)
        elif params is not None and port not in params:
            report(f"dimension {name} refers to unknown port", path + "/port",
                   port, "existing top-level port; " + port_facts)
        lo_valid = not isinstance(lo, bool) and isinstance(lo, int)
        hi_valid = not isinstance(hi, bool) and isinstance(hi, int)
        if not lo_valid:
            report(f"dimension {name}.min must be an integer", path + "/min", lo, "integer")
        if not hi_valid or (lo_valid and hi < lo):
            report(f"dimension {name}.max must be an integer >= min", path + "/max", hi,
                   "integer >= min " + json.dumps(lo) if lo_valid else "integer")
        if len(errors) == before:
            normalized_dims[name] = {"port": port, "min": lo, "max": hi}

    normalized_buffers: dict[str, dict[str, Any]] = {}
    for port, spec in buffers.items():
        before = len(errors)
        path = "/buffers/" + pointer_key(port)
        if not isinstance(port, str) or not _IDENTIFIER.fullmatch(port):
            report("buffer port names must be C identifiers", path, port,
                   "existing top-level C identifier port; " + port_facts)
        elif params is not None and port not in params:
            report(f"buffer refers to unknown port {port!r}", path, port,
                   "existing top-level port; " + port_facts)
        if not isinstance(spec, Mapping):
            report(f"buffer {port} must be an object", path, spec, "JSON object")
            continue
        extent = spec.get("extent", [])
        extent_valid = isinstance(extent, list)
        if not extent_valid:
            report(f"buffer {port}.extent contains unknown dimensions", path + "/extent",
                   extent, "list of declared dimension names")
        else:
            for index, item in enumerate(extent):
                if not isinstance(item, str) or item not in dimensions:
                    report(f"buffer {port}.extent contains unknown dimensions",
                           path + f"/extent/{index}", item,
                           "declared dimension; allowed_dimensions=" + json.dumps(sorted(normalized_dims)))
                if not isinstance(item, str) or item not in normalized_dims:
                    extent_valid = False
        max_elements = spec.get("max_elements")
        capacity_valid = not isinstance(max_elements, bool) and isinstance(max_elements, int) and max_elements > 0
        if not capacity_valid:
            report(f"buffer {port}.max_elements must be positive", path + "/max_elements",
                   max_elements, "positive integer")
        if extent_valid:
            for index, item in enumerate(extent):
                if normalized_dims[item]["max"] < 1:
                    report(f"buffer {port}.extent dimensions must have a positive maximum",
                           path + f"/extent/{index}", normalized_dims[item]["max"],
                           f"dimension {item}.max >= 1")
                    extent_valid = False
            if extent_valid and capacity_valid:
                expected = math.prod(normalized_dims[item]["max"] for item in extent) if extent else 1
                if max_elements < expected:
                    report(f"buffer {port}.max_elements must cover the declared maximum extent ({expected})",
                           path + "/max_elements", max_elements, f"integer >= {expected}")
        if params is not None and port in params and not params[port] and isinstance(extent, list) and extent:
            report(f"scalar port {port} cannot have a non-empty extent", path + "/extent",
                   extent, "empty extent for scalar port")
        if len(errors) == before and extent_valid:
            normalized_buffers[port] = {"extent": list(extent), "max_elements": max_elements}
    if params is not None:
        missing = [
            name
            for name, is_pointer in params.items()
            if is_pointer and name not in buffers
        ]
        if missing:
            for name in missing:
                report("missing buffer capacity for pointer ports: " + name,
                       "/buffers/" + pointer_key(name), "<missing>",
                       "buffer capacity for pointer port; " + port_facts)
    if errors:
        raise ValueError("\n".join(errors))
    return {
        "schema_version": 1,
        "kind": "input_domain_v1",
        "dimensions": dict(sorted(normalized_dims.items())),
        "buffers": dict(sorted(normalized_buffers.items())),
        "origin": str(value.get("origin") or "model_proposed"),
        "finite_domain": True,
    }


def input_domain_sha256(contract: Mapping[str, Any]) -> str:
    payload = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def generate_input_domain_contract(
    *,
    orig_code: str,
    kernel_name: str,
    llm_config: Optional[dict[str, Any]] = None,
    budget: Any = None,
) -> dict[str, Any]:
    """Ask the model for the range once, before any testbench is generated."""
    loader = HLSAgentLoader(
        "flow/agents/input_domain.yaml",
        llm_config_override=llm_config,
        budget=budget,
    )
    agent = loader.load_agent("input_domain_planner")
    params = _source_parameters(orig_code, kernel_name)
    interface_facts = (
        "Compiler interface facts: allowed_ports=" + json.dumps(sorted(params))
        + "; pointer_array_ports=" + json.dumps(sorted(name for name, pointer in params.items() if pointer))
        if params is not None else "Compiler interface facts: interface_facts=unavailable; no port list was inferred."
    )
    message = (
        "Read the original HLS top below and freeze one finite legal input-domain contract "
        "before any Public test, Candidate code, or Hidden test is generated. "
        "Use only named top-level ports. Choose conservative but useful finite integer bounds "
        "that cover the intended normal workflow, and give every pointer/array port a "
        "fixed maximum capacity. max_elements is the number of top-level pointed objects "
        "the function may access. Bounds may be negative when the source semantics allow it; "
        "a dimension used as a buffer extent must still have a positive maximum. "
        "Do not infer a pointer capacity from one spelling or one visible access pattern; "
        "derive it from the complete source behavior. "
        "Return exactly one JSON object with only dimensions and buffers.\n\n"
        f"{interface_facts}\n"
        f"Top function: {kernel_name}\nOriginal source:\n```cpp\n{orig_code.rstrip()}\n```\n\n"
        "Required shape: {\"dimensions\": {\"n\": {\"port\": \"n\", \"min\": 1, \"max\": 128}}, "
        "\"buffers\": {\"data\": {\"extent\": [\"n\"], \"max_elements\": 128}}}"
    )
    initial_message = message
    failures: list[str] = []
    last_error: Exception | None = None
    for attempt in range(_RETRIES + 1):
        try:
            raw = _json_response(agent, message, first_turn=attempt == 0)
            payload = json.loads(raw)
            return normalize_input_domain_contract(payload, source_code=orig_code, kernel_name=kernel_name)
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            failures.append(f"Attempt {attempt}: {exc}")
            if attempt == _RETRIES:
                break
            message = (
                "Your previous input-domain response was invalid: "
                + "\n".join(failures)
                + "\nRe-read every top-level pointer access and fixed loop bound. "
                "Use max_elements for the maximum number of top-level pointed objects accessed, "
                "even when no scalar length parameter exists. Return one strict JSON object "
                "matching the requested shape, with no markdown.\n\n" + initial_message
            )
    raise ValueError(
        "model did not return a valid input-domain contract\n" + "\n".join(failures)
    ) from last_error
