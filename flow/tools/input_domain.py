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
    if not isinstance(value, Mapping):
        raise ValueError("input domain must be a JSON object")
    allowed = {"schema_version", "kind", "dimensions", "buffers", "origin", "finite_domain"}
    if set(value) - allowed:
        raise ValueError("input domain contains unexpected fields")
    dimensions = value.get("dimensions")
    buffers = value.get("buffers")
    if not isinstance(dimensions, Mapping) or not isinstance(buffers, Mapping):
        raise ValueError("input domain requires dimensions and buffers mappings")
    if value.get("finite_domain", True) is not True:
        raise ValueError("input domain must be finite")
    params = _source_parameters(source_code, kernel_name)
    normalized_dims: dict[str, dict[str, Any]] = {}
    for name, spec in dimensions.items():
        if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name):
            raise ValueError("dimension names must be C identifiers")
        if not isinstance(spec, Mapping):
            raise ValueError(f"dimension {name} must be an object")
        port = spec.get("port")
        lo = spec.get("min")
        hi = spec.get("max")
        if not isinstance(port, str) or not _IDENTIFIER.fullmatch(port):
            raise ValueError(f"dimension {name} must name a C identifier port")
        if params is not None and port not in params:
            raise ValueError(f"dimension {name} refers to unknown port")
        if isinstance(lo, bool) or not isinstance(lo, int):
            raise ValueError(f"dimension {name}.min must be an integer")
        if isinstance(hi, bool) or not isinstance(hi, int) or hi < lo:
            raise ValueError(f"dimension {name}.max must be an integer >= min")
        normalized_dims[name] = {"port": port, "min": lo, "max": hi}

    normalized_buffers: dict[str, dict[str, Any]] = {}
    for port, spec in buffers.items():
        if not isinstance(port, str) or not _IDENTIFIER.fullmatch(port):
            raise ValueError("buffer port names must be C identifiers")
        if params is not None and port not in params:
            raise ValueError(f"buffer refers to unknown port {port!r}")
        if not isinstance(spec, Mapping):
            raise ValueError(f"buffer {port} must be an object")
        extent = spec.get("extent", [])
        if not isinstance(extent, list) or any(
            not isinstance(item, str) or item not in normalized_dims
            for item in extent
        ):
            raise ValueError(f"buffer {port}.extent contains unknown dimensions")
        max_elements = spec.get("max_elements")
        if isinstance(max_elements, bool) or not isinstance(max_elements, int) or max_elements < 1:
            raise ValueError(f"buffer {port}.max_elements must be positive")
        if any(normalized_dims[item]["max"] < 1 for item in extent):
            raise ValueError(
                f"buffer {port}.extent dimensions must have a positive maximum"
            )
        expected = math.prod(normalized_dims[item]["max"] for item in extent) if extent else 1
        if max_elements < expected:
            raise ValueError(
                f"buffer {port}.max_elements must cover the declared maximum extent ({expected})"
            )
        if params is not None and not params[port] and extent:
            raise ValueError(f"scalar port {port} cannot have a non-empty extent")
        normalized_buffers[port] = {"extent": list(extent), "max_elements": max_elements}
    if params is not None:
        missing = [
            name
            for name, is_pointer in params.items()
            if is_pointer and name not in normalized_buffers
        ]
        if missing:
            raise ValueError(
                "missing buffer capacity for pointer ports: "
                + ", ".join(missing)
            )
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
    raise ValueError("model did not return a valid input-domain contract") from last_error
