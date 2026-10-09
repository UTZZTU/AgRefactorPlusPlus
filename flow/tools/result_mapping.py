"""Freeze explicit Public observation helpers, without interpreting C++ semantics."""

import hashlib
import json

from agrefactor.cpp_interface import extract_cpp_comments, extract_top_interface, inspect_top_entry


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


class ResultMappingVerificationError(ValueError):
    """An explicit mapping whose compiler facts have not been established."""

    def __init__(self, reason, mapping, facts, *, editable_testbench=True):
        self.partial_mapping = dict(mapping, compiler_structure_verified=False)
        self.partial_mapping.pop("sha256", None)
        self.partial_mapping["verification_status"] = "incomplete"
        self.diagnostics = facts.get("diagnostics", [])
        self.partial_mapping["verification_diagnostics"] = self.diagnostics
        self.partial_mapping["verification_reason"] = reason
        self.partial_mapping["sha256"] = _json_sha(self.partial_mapping)
        errors = [item for item in self.diagnostics if item.get("severity", 0) >= 3]
        source_path = facts.get("parse_context", {}).get("source_path")
        self.repair_eligible = bool(editable_testbench and errors and source_path and all(
            item.get("file") == source_path for item in errors
        ))
        detail = json.dumps(self.diagnostics, ensure_ascii=False)
        super().__init__("contract_incomplete: result mapping compiler facts unavailable: " + reason + "; diagnostics=" + detail)


def _comments(testbench_code, context=None):
    facts = extract_cpp_comments(testbench_code, source_path=(context or {}).get("source_path"))
    if facts.get("status") != "confirmed":
        raise ValueError("contract_incomplete: C++ comment tokenization unavailable; diagnostics="
                         + json.dumps(facts.get("diagnostics", []), ensure_ascii=False))
    return facts["comments"]


def _marker_error(name, expected, matches):
    positions = [{"line": item["line"], "column": item["column"]} for item in matches]
    raise ValueError("contract_incomplete: " + name + "; expected=" + expected
                     + "; found=" + str(len(matches)) + "; positions=" + json.dumps(positions))


def _fields_error(name, value, expected):
    actual = set(value) if isinstance(value, dict) else set()
    raise ValueError("contract_incomplete: " + name
                     + "; expected_keys=" + json.dumps(sorted(expected))
                     + "; actual_type=" + type(value).__name__
                     + "; actual_keys=" + json.dumps(sorted(actual))
                     + "; missing_keys=" + json.dumps(sorted(expected - actual))
                     + "; extra_keys=" + json.dumps(sorted(actual - expected)))


def _shared_from_source(testbench_code, comments):
    def formal_marker(item, marker):
        prefix = testbench_code[testbench_code.rfind("\n", 0, item["start"]) + 1:item["start"]]
        return item["kind"] == "line" and item["text"].rstrip() == marker and not prefix.strip()

    starts = [item for item in comments if formal_marker(item, BEGIN)]
    ends = [item for item in comments if formal_marker(item, END)]
    if not starts and not ends:
        return None
    if len(starts) != 1:
        _marker_error("common result mapping BEGIN must occur once", BEGIN, starts)
    if len(ends) != 1:
        _marker_error("common result mapping END must occur once", END, ends)
    start, end = starts[0], ends[0]
    if start["start"] >= end["start"]:
        raise ValueError("contract_incomplete: common result mapping END precedes BEGIN")
    end_character = end["start"] + len(END)
    return testbench_code[start["start"]:end_character], start["byte_start"], end["byte_start"] + len(END)


def _manifest_from_source(testbench_code, comments=None):
    matches = [item for item in (comments if comments is not None else _comments(testbench_code))
               if item["kind"] == "block" and item["text"].startswith(MANIFEST_BEGIN)
               and item["text"][len(MANIFEST_BEGIN):len(MANIFEST_BEGIN) + 1].isspace()]
    if len(matches) != 1:
        _marker_error("result mapping manifest must occur once", MANIFEST_BEGIN, matches)
    comment = matches[0]["text"]
    try:
        manifest = json.loads(comment[len(MANIFEST_BEGIN):-2])
    except json.JSONDecodeError as exc:
        raise ValueError("contract_incomplete: invalid result mapping manifest JSON; line="
                         + str(exc.lineno) + "; column=" + str(exc.colno) + "; reason=" + exc.msg
                         + "; C++ manifest comments end at the first literal */; encode */ inside JSON strings "
                         + "as *\\u002f so the decoded text remains exact") from exc
    if not isinstance(manifest, dict) or set(manifest) != {"observables"}:
        _fields_error("unexpected result mapping manifest fields", manifest, {"observables"})
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
        "containing one JSON object with exactly one key: observables. Each "
        "observable must contain exactly these five keys: id, kind, original, "
        "candidate, source_evidence. kind must be return_value, output_parameter, "
        "or post_call_state; source_evidence must be a nonempty list of exact "
        "Original-source excerpts, with no explanations added to the excerpts. "
        "Describe how each side is read and why the conversion preserves the "
        "observation in ordinary C++ comments outside the manifest, not in extra "
        "JSON fields such as read or preservation. A literal */ ends the C++ "
        "manifest comment even inside a JSON string; encode such text as *\\u002f "
        "so JSON decoding preserves the exact Original excerpt. JSON, matching "
        "types, and matching hashes are not proof "
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
    mentions_protocol = BEGIN in testbench_code or END in testbench_code or MANIFEST_BEGIN in testbench_code
    comments = _comments(testbench_code, context) if mentions_protocol else []
    shared_parts = _shared_from_source(testbench_code, comments)
    if shared_parts is None:
        if require_explicit and interface_transformed(
            original_code, testbench_code, original_name, candidate_name, **context
        ) is True:
            raise ValueError("contract_incomplete: adapted interface has no explicit common result mapping")
        return None
    shared, start_byte, end_byte = shared_parts
    manifest = _manifest_from_source(testbench_code, comments)
    observables = manifest["observables"]
    if not isinstance(observables, list) or not observables:
        raise ValueError("contract_incomplete: result mapping has no observable channels")
    seen = set()
    for channel_index, item in enumerate(observables):
        expected_fields = {"id", "kind", "original", "candidate", "source_evidence"}
        if not isinstance(item, dict) or set(item) != expected_fields:
            _fields_error("invalid observable channel; index=" + str(channel_index), item, expected_fields)
        if any(not isinstance(item[field], str) or not item[field].strip() for field in ("id", "kind", "original", "candidate")):
            raise ValueError("contract_incomplete: empty observable channel")
        if item["id"] in seen or item["kind"] not in {"return_value", "output_parameter", "post_call_state"}:
            raise ValueError("contract_incomplete: duplicate or invalid observable channel")
        seen.add(item["id"])
        evidence = item["source_evidence"]
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("contract_incomplete: result mapping source evidence does not match Original; channel="
                             + item["id"] + "; expected=nonempty list of exact excerpts")
        for evidence_index, excerpt in enumerate(evidence):
            if not isinstance(excerpt, str) or not excerpt.strip() or excerpt not in original_code:
                raise ValueError("contract_incomplete: result mapping source evidence does not match Original; channel="
                                 + item["id"] + "; evidence_index=" + str(evidence_index)
                                 + "; excerpt=" + json.dumps(excerpt, ensure_ascii=False))
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
    interfaces = {}
    for helper in HELPERS:
        interface = extract_top_interface(testbench_code, helper, **context)
        if interface is None:
            raise ResultMappingVerificationError("helper interface: " + helper, result,
                                                  inspect_top_entry(testbench_code, helper, **context))
        if not (start_byte <= interface.source_start < interface.source_end <= end_byte):
            raise ValueError("contract_contradiction: observation helper is outside the frozen block")
        interfaces[helper] = interface
    for helper, top in zip(HELPERS[:2], (original_name, candidate_name)):
        facts = inspect_top_entry(testbench_code, helper, **context)
        calls = facts.get("reachable_calls")
        top_interface = extract_top_interface(testbench_code, top, require_definition=False, **context)
        if not isinstance(calls, list) or top_interface is None:
            raise ResultMappingVerificationError("helper calls: " + helper, result, facts)
        if top_interface.linker_symbol not in {item.get("linker_symbol") for item in calls}:
            raise ValueError("contract_contradiction: observation helper does not call its real top")
    original_interface = extract_top_interface(original_code, original_name, **context)
    if original_interface is None:
        raise ResultMappingVerificationError("Original interface", result,
                                              inspect_top_entry(original_code, original_name, **context),
                                              editable_testbench=False)
    if original_interface.canonical_result_type != "void" and not any(
        item["kind"] == "return_value" for item in observables
    ):
        raise ValueError("contract_incomplete: Original return value has no observation mapping")
    facts = inspect_top_entry(testbench_code, "main", **context)
    calls = facts.get("reachable_calls")
    if not isinstance(calls, list):
        raise ResultMappingVerificationError("main reachable calls", result, facts)
    required = {
        interface.linker_symbol for interface in interfaces.values()
    }
    if not required.issubset({item.get("linker_symbol") for item in calls}):
        raise ValueError("contract_contradiction: main does not use every shared observation helper")
    result["sha256"] = _json_sha(result)
    return result


def shared_helper_identity(testbench_code):
    if BEGIN not in testbench_code and END not in testbench_code and MANIFEST_BEGIN not in testbench_code:
        return None
    comments = _comments(testbench_code)
    shared_parts = _shared_from_source(testbench_code, comments)
    if shared_parts is None:
        return None
    shared = shared_parts[0]
    manifest = _manifest_from_source(testbench_code, comments)
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
    comments = _comments(mapping["shared_cpp"])
    manifests = [item for item in comments if item["kind"] == "block"
                 and item["text"].startswith(MANIFEST_BEGIN)]
    if not manifests:
        # JSON escapes protect the surrounding C++ comment without changing the
        # decoded observation contract.
        payload = json.dumps({"observables": mapping["observables"]}, ensure_ascii=False, sort_keys=True)
        payload = payload.replace("*/", "*\\u002f")
        instruction += "\n" + MANIFEST_BEGIN + "\n" + payload + "\n*/\n"
    return instruction + (
        "\nKeep the shared block verbatim and call its three observation helpers. "
        "Preserve exactly one complete result mapping manifest comment. "
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
    identity = shared_helper_identity(testbench_code)
    if identity is None or identity["shared_cpp"] != mapping["shared_cpp"] or identity["shared_cpp_sha256"] != mapping["shared_cpp_sha256"]:
        raise ValueError("contract_contradiction: frozen common result observation changed")
    manifest = {"observables": identity["observables"]}
    if manifest["observables"] != mapping.get("observables"):
        raise ValueError("contract_contradiction: frozen result mapping manifest changed")
    expected_manifest_sha = mapping.get("manifest_sha256")
    if expected_manifest_sha and _json_sha(manifest) != expected_manifest_sha:
        raise ValueError("contract_contradiction: frozen result mapping manifest identity differs")
    facts = inspect_top_entry(testbench_code, "main", **context)
    calls = facts.get("reachable_calls")
    if not isinstance(calls, list):
        raise ResultMappingVerificationError("Testbench main reachable calls", mapping, facts)
    required = set()
    for helper in HELPERS:
        interface = extract_top_interface(testbench_code, helper, **context)
        if interface is None:
            raise ResultMappingVerificationError("Testbench helper interface: " + helper, mapping,
                                                  inspect_top_entry(testbench_code, helper, **context))
        required.add(interface.linker_symbol)
    if not required.issubset({item.get("linker_symbol") for item in calls}):
        raise ValueError("contract_contradiction: Testbench bypasses frozen result observation")
