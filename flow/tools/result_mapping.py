"""Freeze explicit Public observation helpers, without interpreting C++ semantics."""

import hashlib
import json

from agrefactor.cpp_interface import extract_top_interface, inspect_top_entry


BEGIN = "// AGREFACTOR_SHARED_RESULT_MAPPING_BEGIN"
END = "// AGREFACTOR_SHARED_RESULT_MAPPING_END"
MANIFEST_BEGIN = "/* AGREFACTOR_RESULT_MAPPING_MANIFEST"
HELPERS = (
    "agrefactor_observe_original",
    "agrefactor_observe_candidate",
    "agrefactor_compare_observations",
)


def _sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_sha(value):
    return _sha(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _manifest_from_source(testbench_code):
    if testbench_code.count(MANIFEST_BEGIN) != 1:
        raise ValueError("contract_incomplete: result mapping manifest is not unique")
    manifest_start = testbench_code.index(MANIFEST_BEGIN) + len(MANIFEST_BEGIN)
    try:
        manifest_end = testbench_code.index("*/", manifest_start)
        manifest = json.loads(testbench_code[manifest_start:manifest_end])
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("contract_incomplete: invalid result mapping manifest") from exc
    if not isinstance(manifest, dict) or set(manifest) != {"observables"}:
        raise ValueError("contract_incomplete: unexpected result mapping manifest fields")
    if not isinstance(manifest["observables"], list) or not manifest["observables"]:
        raise ValueError("contract_incomplete: result mapping has no observable channels")
    return manifest


def mapping_generation_instruction():
    return (
        "PUBLIC COMMON RESULT OBSERVATION: When adapting the Original interface, "
        "make the observable-result mapping explicit before Candidate generation. "
        "Put the reusable, input-independent C++ types, declarations and helpers "
        "between these exact lines:\n" + BEGIN + "\n...\n" + END + "\n"
        "Define agrefactor_observe_original and agrefactor_observe_candidate to "
        "call the actual read-only tops on separate storage and capture all required "
        "return values, public output parameters and post-call public state; define "
        "agrefactor_compare_observations to compare those captures with the existing "
        "oracle semantics. main must use all three helpers. Keep input generation, "
        "testcase constants and case selection outside this shared block. Do not "
        "copy Original implementation logic into the helpers, invent an oracle, "
        "omit a status return in favor of an output count, loosen tolerance, or "
        "guess meaning from names/types/dimensions. Only conversions supported by "
        "Original source and the public task contract are valid. Then emit a C++ "
        "comment starting with '" + MANIFEST_BEGIN + "' and ending with '*/', "
        "containing one JSON object with only observables. Each observable has "
        "id, kind (return_value/output_parameter/post_call_state), original, "
        "candidate, and source_evidence (a nonempty list of exact Original-source "
        "excerpts). Describe how each side is read and why the conversion preserves "
        "that observation. JSON, matching types, and matching hashes are not proof "
        "of semantic correctness; the complete Testbench must still compile and "
        "pass the existing Original-only and differential Stub qualification. "
        "For an unchanged interface, existing Testbench code remains valid; do not "
        "claim an identity mapping proves comparator correctness."
    )


def interface_transformed(original_code, testbench_code, original_name, candidate_name, **context):
    original = extract_top_interface(original_code, original_name, **context)
    candidate = extract_top_interface(testbench_code, candidate_name, require_definition=False, **context)
    if original is None or candidate is None:
        return None
    return (
        original.canonical_result_type != candidate.canonical_result_type
        or tuple(item.canonical_type for item in original.parameters)
        != tuple(item.canonical_type for item in candidate.parameters)
    )


def freeze_result_mapping(testbench_code, original_code, original_name, candidate_name, *, require_explicit=False, **context):
    """Bind an explicit mapping to compiler facts and verbatim source evidence.

    This validates identity/structure, never interprets the evidence excerpts or
    certifies semantics. Real generation qualification remains authoritative.
    Legacy code without an explicit mapping is observed rather than gated.
    """
    if BEGIN not in testbench_code:
        if require_explicit and interface_transformed(
            original_code, testbench_code, original_name, candidate_name, **context
        ) is True:
            raise ValueError("contract_incomplete: adapted interface has no explicit common result mapping")
        return None
    if testbench_code.count(BEGIN) != 1 or testbench_code.count(END) != 1:
        raise ValueError("contract_incomplete: common result mapping block is not unique")
    start = testbench_code.index(BEGIN)
    end = testbench_code.index(END, start) + len(END)
    shared = testbench_code[start:end]
    start_byte = len(testbench_code[:start].encode("utf-8"))
    end_byte = len(testbench_code[:end].encode("utf-8"))
    manifest = _manifest_from_source(testbench_code)
    observables = manifest["observables"]
    if not isinstance(observables, list) or not observables:
        raise ValueError("contract_incomplete: result mapping has no observable channels")
    seen = set()
    for item in observables:
        if not isinstance(item, dict) or set(item) != {"id", "kind", "original", "candidate", "source_evidence"}:
            raise ValueError("contract_incomplete: invalid observable channel")
        if any(not isinstance(item[field], str) or not item[field].strip() for field in ("id", "kind", "original", "candidate")):
            raise ValueError("contract_incomplete: empty observable channel")
        if item["id"] in seen or item["kind"] not in {"return_value", "output_parameter", "post_call_state"}:
            raise ValueError("contract_incomplete: duplicate or invalid observable channel")
        seen.add(item["id"])
        evidence = item["source_evidence"]
        if not isinstance(evidence, list) or not evidence or any(
            not isinstance(excerpt, str) or not excerpt.strip() or excerpt not in original_code
            for excerpt in evidence
        ):
            raise ValueError("contract_incomplete: result mapping source evidence does not match Original")
    interfaces = {}
    for helper in HELPERS:
        interface = extract_top_interface(testbench_code, helper, **context)
        if interface is None:
            # An unavailable compiler cannot turn a legacy artifact into a new
            # failure. A new explicit block is still not certified executable.
            return None
        if not (start_byte <= interface.source_start < interface.source_end <= end_byte):
            raise ValueError("contract_contradiction: observation helper is outside the frozen block")
        interfaces[helper] = interface
    for helper, top in zip(HELPERS[:2], (original_name, candidate_name)):
        calls = inspect_top_entry(testbench_code, helper, **context).get("reachable_calls")
        top_interface = extract_top_interface(testbench_code, top, require_definition=False, **context)
        if not isinstance(calls, list) or top_interface is None:
            return None
        if top_interface.linker_symbol not in {item.get("linker_symbol") for item in calls}:
            raise ValueError("contract_contradiction: observation helper does not call its real top")
    original_interface = extract_top_interface(original_code, original_name, **context)
    if original_interface is not None and original_interface.canonical_result_type != "void" and not any(
        item["kind"] == "return_value" for item in observables
    ):
        raise ValueError("contract_incomplete: Original return value has no observation mapping")
    facts = inspect_top_entry(testbench_code, "main", **context)
    calls = facts.get("reachable_calls")
    if not isinstance(calls, list):
        return None
    required = {
        interface.linker_symbol for interface in interfaces.values()
    }
    if not required.issubset({item.get("linker_symbol") for item in calls}):
        raise ValueError("contract_contradiction: main does not use every shared observation helper")
    result = {
        "schema_version": 1,
        "kind": "public_result_mapping_v1",
        "shared_cpp": shared,
        "shared_cpp_sha256": _sha(shared),
        "observables": observables,
        "manifest_sha256": _json_sha(manifest),
        "original_sha256": _sha(original_code),
        "compiler_structure_verified": True,
        "semantic_status": "source_grounded_execution_required",
    }
    result["sha256"] = _json_sha(result)
    return result


def shared_helper_identity(testbench_code):
    if BEGIN not in testbench_code:
        return None
    if testbench_code.count(BEGIN) != 1 or testbench_code.count(END) != 1:
        raise ValueError("contract_incomplete: common result mapping block is not unique")
    start = testbench_code.index(BEGIN)
    end = testbench_code.index(END, start) + len(END)
    shared = testbench_code[start:end]
    manifest = _manifest_from_source(testbench_code)
    return {
        "shared_cpp": shared,
        "shared_cpp_sha256": _sha(shared),
        "observables": manifest["observables"],
        "manifest_sha256": _json_sha(manifest),
    }


def frozen_mapping_instruction(mapping, *, for_candidate=False):
    if not mapping:
        return ""
    instruction = (
        "\nFROZEN PUBLIC COMMON RESULT OBSERVATION (inputs remain independent):\n"
        + mapping["shared_cpp"]
        + "\nObservables: " + json.dumps(mapping["observables"], ensure_ascii=False, sort_keys=True)
    )
    if for_candidate:
        return instruction + (
            "\nThis is read-only Testbench adapter/comparison code documenting the "
            "public result contract. Emit only the Candidate implementation with "
            "the frozen ABI. Do not copy Testbench helpers into the Candidate, "
            "call the Original, or change any observable meaning."
        )
    return instruction + (
        "\nKeep the shared block verbatim and call its three observation helpers. "
        "Do not regenerate the adapters/comparator or infer a different return "
        "meaning. Generate independent legal inputs outside the block. Do not "
        "change the input domain, omit observations, or weaken comparison."
    )


def validate_result_mapping_identity(mapping):
    if not isinstance(mapping, dict) or mapping.get("schema_version") != 1 or mapping.get("kind") != "public_result_mapping_v1":
        raise ValueError("contract_incomplete: invalid public result mapping")
    if not isinstance(mapping.get("shared_cpp"), str) or _sha(mapping["shared_cpp"]) != mapping.get("shared_cpp_sha256"):
        raise ValueError("contract_contradiction: shared result mapping code identity differs")
    payload = dict(mapping)
    claimed = payload.pop("sha256", None)
    if _json_sha(payload) != claimed:
        raise ValueError("contract_contradiction: public result mapping identity differs")


def validate_frozen_result_mapping(testbench_code, mapping, **context):
    """Check an explicitly frozen helper identity, not guessed semantics."""
    if not mapping:
        return
    shared = mapping["shared_cpp"]
    if testbench_code.count(shared) != 1 or _sha(shared) != mapping["shared_cpp_sha256"]:
        raise ValueError("contract_contradiction: frozen common result observation changed")
    manifest = _manifest_from_source(testbench_code)
    if manifest["observables"] != mapping.get("observables"):
        raise ValueError("contract_contradiction: frozen result mapping manifest changed")
    expected_manifest_sha = mapping.get("manifest_sha256")
    if expected_manifest_sha and _json_sha(manifest) != expected_manifest_sha:
        raise ValueError("contract_contradiction: frozen result mapping manifest identity differs")
    facts = inspect_top_entry(testbench_code, "main", **context)
    calls = facts.get("reachable_calls")
    if not isinstance(calls, list):
        return
    required = set()
    for helper in HELPERS:
        interface = extract_top_interface(testbench_code, helper, **context)
        if interface is None:
            return
        required.add(interface.linker_symbol)
    if not required.issubset({item.get("linker_symbol") for item in calls}):
        raise ValueError("contract_contradiction: Testbench bypasses frozen result observation")
