"""Freeze explicit Public observation helpers, without interpreting C++ semantics."""

import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

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


def _differences(expected, actual, path=""):
    if expected == actual:
        return []
    if isinstance(expected, dict) and isinstance(actual, dict):
        result = []
        for key in sorted(set(expected) | set(actual)):
            child = path + "/" + str(key).replace("~", "~0").replace("/", "~1")
            if key not in expected or key not in actual:
                result.append({"path": child, "expected": expected.get(key), "actual": actual.get(key),
                               "expected_present": key in expected, "actual_present": key in actual})
            else:
                result.extend(_differences(expected[key], actual[key], child))
        return result
    if isinstance(expected, list) and isinstance(actual, list):
        result = []
        for index in range(max(len(expected), len(actual))):
            child = path + "/" + str(index)
            if index >= len(expected) or index >= len(actual):
                result.append({"path": child,
                               "expected": expected[index] if index < len(expected) else None,
                               "actual": actual[index] if index < len(actual) else None,
                               "expected_present": index < len(expected), "actual_present": index < len(actual)})
            else:
                result.extend(_differences(expected[index], actual[index], child))
        return result
    return [{"path": path, "expected": expected, "actual": actual}]


def _brief(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(encoded) <= 320:
        return value
    return {"preview": encoded[:240], "encoded_length": len(encoded),
            "sha256": _sha(encoded), "truncated": True}


def _contract_error(reason, **evidence):
    """Explain an existing failure; retain complete facts for operator artifacts."""
    evidence = dict(check=reason, **evidence)
    summary = {key: _brief(value) for key, value in evidence.items()
               if key not in {"check", "differences", "expected_shared_cpp", "actual_shared_cpp"}}
    if "differences" in evidence:
        differences = evidence["differences"]
        summary["differences"] = [{key: _brief(value) for key, value in item.items()} for item in differences[:2]]
        summary["difference_count"] = len(differences)
        summary["omitted_difference_count"] = max(0, len(differences) - 2)
    error = ValueError(reason + "; details=" + json.dumps(summary, ensure_ascii=False, sort_keys=True))
    error.contract_evidence = evidence
    return error


class ResultMappingVerificationError(ValueError):
    """An explicit mapping whose compiler facts have not been established."""

    def __init__(self, reason, mapping, facts, *, editable_testbench=True, expected=None):
        self.partial_mapping = dict(mapping, compiler_structure_verified=False)
        self.partial_mapping.pop("sha256", None)
        self.partial_mapping["verification_status"] = "incomplete"
        self.diagnostics = facts.get("diagnostics", [])
        self.partial_mapping["verification_diagnostics"] = self.diagnostics
        self.partial_mapping["verification_reason"] = reason
        self.partial_mapping["verification_facts"] = {
            key: facts[key] for key in ("parse_context", "entries", "reachable_calls", "translation_unit_complete")
            if key in facts
        }
        self.partial_mapping["sha256"] = _json_sha(self.partial_mapping)
        errors = [item for item in self.diagnostics if item.get("severity", 0) >= 3]
        source_path = facts.get("parse_context", {}).get("source_path")
        self.repair_eligible = bool(editable_testbench and errors and source_path and all(
            item.get("file") == source_path for item in errors
        ))
        self.contract_evidence = {"check": "result_mapping_compiler_facts", "reason": reason,
                                  "expected": expected or {}, "diagnostics": self.diagnostics,
                                  "compiler_facts": dict(self.partial_mapping["verification_facts"], status=facts.get("status"))}
        detail = {key: _brief(value) for key, value in self.contract_evidence.items() if key not in {"check", "compiler_facts"}}
        detail["compiler_facts"] = {key: _brief(value) for key, value in self.contract_evidence["compiler_facts"].items()
                                    if key != "parse_context"}
        super().__init__("contract_incomplete: result mapping compiler facts unavailable: " + reason
                         + "; details=" + json.dumps(detail, ensure_ascii=False, sort_keys=True))


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
        raise _contract_error("contract_incomplete: result mapping has no observable channels",
                              path="/observables", expected="nonempty list", actual=manifest["observables"])
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
        raise _contract_error("contract_incomplete: result mapping has no observable channels",
                              path="/observables", expected="nonempty list", actual=observables)
    seen = {}
    for channel_index, item in enumerate(observables):
        expected_fields = {"id", "kind", "original", "candidate", "source_evidence"}
        if not isinstance(item, dict) or set(item) != expected_fields:
            _fields_error("invalid observable channel; index=" + str(channel_index), item, expected_fields)
        invalid = [{"path": "/observables/" + str(channel_index) + "/" + field,
                    "expected": "nonempty string", "actual": item[field]}
                   for field in ("id", "kind", "original", "candidate")
                   if not isinstance(item[field], str) or not item[field].strip()]
        if invalid:
            raise _contract_error("contract_incomplete: empty observable channel", differences=invalid)
        if item["id"] in seen:
            raise _contract_error("contract_incomplete: duplicate or invalid observable channel",
                                  path="/observables/" + str(channel_index) + "/id", actual=item["id"],
                                  expected="unique observable id", duplicate_index=seen[item["id"]])
        if item["kind"] not in {"return_value", "output_parameter", "post_call_state"}:
            raise _contract_error("contract_incomplete: duplicate or invalid observable channel",
                                  path="/observables/" + str(channel_index) + "/kind", actual=item["kind"],
                                  expected=["return_value", "output_parameter", "post_call_state"])
        seen[item["id"]] = channel_index
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
            raise _contract_error("contract_contradiction: observation helper is outside the frozen block",
                                  helper=helper, expected_block_range=[start_byte, end_byte],
                                  actual_source_range=[interface.source_start, interface.source_end])
        interfaces[helper] = interface
    for helper, top in zip(HELPERS[:2], (original_name, candidate_name)):
        facts = inspect_top_entry(testbench_code, helper, **context)
        calls = facts.get("reachable_calls")
        top_interface = extract_top_interface(testbench_code, top, require_definition=False, **context)
        if not isinstance(calls, list):
            raise ResultMappingVerificationError("helper_reachable_calls_unavailable: " + helper, result, facts)
        if top_interface is None:
            top_facts = inspect_top_entry(testbench_code, top, require_definition=False, **context)
            kind = "template" if any(item.get("kind") == "template" for item in top_facts.get("entries", ())) else "ordinary"
            raise ResultMappingVerificationError(kind + "_top_interface_unavailable: " + top, result, top_facts)
        if getattr(top_interface, "template_parameters", ()):
            original_top = extract_top_interface(original_code, original_name, **context) if top == original_name else None
            if original_top is not None and top_interface.usr != original_top.usr:
                raise _contract_error("contract_contradiction: Testbench template declaration differs from Original",
                                      helper=helper, top=top, expected_usr=original_top.usr,
                                      actual_usr=top_interface.usr)
            if not top_interface.usr or not any(
                item.get("template_usr") == top_interface.usr and item.get("usr") and item.get("linker_symbol")
                for item in calls
            ):
                raise ResultMappingVerificationError("template_binding_not_proven: " + helper, result, facts,
                                                     expected={"top": top, "template_usr": top_interface.usr,
                                                               "requires_specialization_usr_and_linker_symbol": True})
        elif not top_interface.linker_symbol or top_interface.linker_symbol not in {item.get("linker_symbol") for item in calls}:
            raise _contract_error("contract_contradiction: observation helper does not call its real top",
                                  helper=helper, expected_top=top, expected_linker_symbol=top_interface.linker_symbol,
                                  actual_calls=calls)
    original_interface = extract_top_interface(original_code, original_name, **context)
    if original_interface is None:
        raise ResultMappingVerificationError("Original interface", result,
                                              inspect_top_entry(original_code, original_name, **context),
                                              editable_testbench=False)
    if original_interface.canonical_result_type != "void" and not any(
        item["kind"] == "return_value" for item in observables
    ):
        raise _contract_error("contract_incomplete: Original return value has no observation mapping",
                              original_top=original_name, return_type=original_interface.canonical_result_type,
                              expected_kind="return_value", actual_kinds=[item["kind"] for item in observables])
    facts = inspect_top_entry(testbench_code, "main", **context)
    calls = facts.get("reachable_calls")
    if not isinstance(calls, list):
        raise ResultMappingVerificationError("main reachable calls", result, facts)
    required = {
        interface.linker_symbol for interface in interfaces.values()
    }
    if not required.issubset({item.get("linker_symbol") for item in calls}):
        raise _contract_error("contract_contradiction: main does not use every shared observation helper",
                              required_helpers={helper: interface.linker_symbol for helper, interface in interfaces.items()},
                              actual_calls=calls, missing=sorted(required - {item.get("linker_symbol") for item in calls}, key=str))
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
        raise _contract_error("contract_contradiction: shared result mapping code identity differs", scope="frozen_baseline",
                              field="shared_cpp_sha256", claimed=mapping.get("shared_cpp_sha256"),
                              computed=_sha(mapping["shared_cpp"]) if isinstance(mapping.get("shared_cpp"), str) else None,
                              actual_type=type(mapping.get("shared_cpp")).__name__)
    payload = dict(mapping)
    claimed = payload.pop("sha256", None)
    if _json_sha(payload) != claimed:
        raise _contract_error("contract_contradiction: public result mapping identity differs", scope="frozen_baseline",
                              field="sha256", claimed=claimed, computed=_json_sha(payload))


def validate_frozen_result_mapping(testbench_code, mapping, **context):
    """Check an explicitly frozen helper identity, not guessed semantics."""
    if not mapping:
        return
    identity = shared_helper_identity(testbench_code)
    if identity is None or identity["shared_cpp"] != mapping["shared_cpp"] or identity["shared_cpp_sha256"] != mapping["shared_cpp_sha256"]:
        expected = mapping["shared_cpp"]
        actual = identity["shared_cpp"] if identity else ""
        raise _contract_error("contract_contradiction: frozen common result observation changed",
                              shared_block_present=identity is not None, expected_markers=[BEGIN, END],
                              expected_sha256=mapping["shared_cpp_sha256"],
                              actual_sha256=identity["shared_cpp_sha256"] if identity else None,
                              expected_shared_cpp=expected, actual_shared_cpp=actual,
                              differences=_differences(expected.splitlines(keepends=True), actual.splitlines(keepends=True), "/shared_cpp/lines"))
    manifest = {"observables": identity["observables"]}
    if manifest["observables"] != mapping.get("observables"):
        differences = _differences(mapping.get("observables"), manifest["observables"], "/observables")
        for item in differences:
            parts = item["path"].split("/")
            if len(parts) > 2 and parts[2].isdigit():
                index = int(parts[2])
                for key, values in (("expected_channel_id", mapping.get("observables")),
                                    ("actual_channel_id", manifest["observables"])):
                    if isinstance(values, list) and index < len(values) and isinstance(values[index], dict):
                        item[key] = values[index].get("id")
        raise _contract_error("contract_contradiction: frozen result mapping manifest changed", differences=differences)
    expected_manifest_sha = mapping.get("manifest_sha256")
    if expected_manifest_sha and _json_sha(manifest) != expected_manifest_sha:
        raise _contract_error("contract_contradiction: frozen result mapping manifest identity differs",
                              field="manifest_sha256", claimed=expected_manifest_sha, computed=_json_sha(manifest))
    facts = inspect_top_entry(testbench_code, "main", **context)
    calls = facts.get("reachable_calls")
    if not isinstance(calls, list):
        raise ResultMappingVerificationError("Testbench main reachable calls", mapping, facts)
    required = set()
    required_helpers = {}
    for helper in HELPERS:
        interface = extract_top_interface(testbench_code, helper, **context)
        if interface is None:
            raise ResultMappingVerificationError("Testbench helper interface: " + helper, mapping,
                                                  inspect_top_entry(testbench_code, helper, **context))
        required.add(interface.linker_symbol)
        required_helpers[helper] = interface.linker_symbol
    if not required.issubset({item.get("linker_symbol") for item in calls}):
        raise _contract_error("contract_contradiction: Testbench bypasses frozen result observation",
                              required_helpers=required_helpers, actual_calls=calls,
                              missing=sorted(required - {item.get("linker_symbol") for item in calls}, key=str))


def _body_nodes(node):
    yield node
    for child in node.get("children", ()):
        yield from _body_nodes(child)


def _kind(node):
    # Stable libclang cursor IDs; the project uses its existing ctypes binding.
    return {9: "VAR_DECL", 10: "PARM_DECL", 100: "UNEXPOSED_EXPR", 101: "DECL_REF_EXPR",
            102: "MEMBER_REF_EXPR", 103: "CALL_EXPR", 111: "PAREN_EXPR", 112: "UNARY_OPERATOR",
            114: "BINARY_OPERATOR", 115: "COMPOUND_ASSIGNMENT_OPERATOR", 117: "CSTYLE_CAST_EXPR",
            124: "CXX_STATIC_CAST_EXPR", 127: "CXX_CONST_CAST_EXPR", 130: "CXX_BOOL_LITERAL_EXPR",
            134: "CXX_NEW_EXPR", 135: "CXX_DELETE_EXPR", 202: "COMPOUND_STMT", 205: "IF_STMT",
            214: "RETURN_STMT"}.get(node.get("kind"), node.get("kind_name"))


def _expression(node):
    while _kind(node) in {"UNEXPOSED_EXPR", "PAREN_EXPR"}:
        children = node.get("children", ())
        if len(children) != 1:
            break
        node = children[0]
    return node


def _trivial_transfer(node):
    return bool(node.get("trivial_record_transfer") or (
        _kind(node) == "CALL_EXPR" and node.get("is_pod_type") is True
        and node.get("referenced", {}).get("kind") == 24 and len(node.get("children", ())) == 1
    ))


def _member_path(node):
    """Only compiler-resolved declaration/member reads, with no getters/aliases."""
    node = _expression(node)
    if _kind(node) == "DECL_REF_EXPR":
        return node.get("referenced", {}).get("usr"), ()
    if _kind(node) == "MEMBER_REF_EXPR" and len(node.get("children", ())) == 1:
        root, members = _member_path(node["children"][0])
        member = node.get("referenced", {}).get("usr")
        if root and member:
            return root, (*members, member)
    return None, ()


def _member_read(node):
    node = _expression(node)
    if (node.get("canonical_type_kind", node.get("type_kind")) in {101, 103, 104}
            or "volatile" in node.get("canonical_type", "").split()):
        return None
    if _kind(node) == "DECL_REF_EXPR":
        return node.get("referenced", {}).get("name") or node.get("spelling")
    if _kind(node) == "MEMBER_REF_EXPR" and len(node.get("children", ())) == 1:
        parent = _member_read(node["children"][0])
        name = node.get("referenced", {}).get("name") or node.get("spelling")
        if parent and name:
            return parent + "." + name
    return None


def _referenced_roots(node):
    return {item.get("referenced", {}).get("usr") for item in _body_nodes(node)
            if _kind(item) == "DECL_REF_EXPR"} - {None, ""}


def _scalar(node):
    if (node.get("canonical_type_kind", node.get("type_kind", 0)) not in range(3, 24)
            or "volatile" in node.get("canonical_type", "").split()):
        return False
    while _kind(node) in {"UNEXPOSED_EXPR", "PAREN_EXPR"} and len(node.get("children", ())) == 1:
        child = node["children"][0]
        if node.get("canonical_type_kind") != child.get("canonical_type_kind"):
            return False
        node = child
    return True


def _top_call(body, top_usr):
    if any(node.get("kind") in {210, 211} for statement in body["statements"]
           for node in _body_nodes(statement)):
        return None
    calls = [node for statement in body["statements"] for node in _body_nodes(statement)
             if _kind(node) == "CALL_EXPR" and node.get("callee_usr") == top_usr]
    if len(calls) != 1:
        return None
    for statement in body["statements"]:
        if _expression(statement) == calls[0]:
            return calls[0]
        if statement.get("kind") == 231 and len(statement.get("children", ())) == 1:
            declaration = statement["children"][0]
            if (_kind(declaration) == "VAR_DECL" and declaration.get("children")
                    and _expression(declaration["children"][-1]) == calls[0]):
                return calls[0]
    return None


def _comparison_predicates(body):
    """Recognize only mandatory equality returns or monotone mismatch flags."""
    nodes = [node for statement in body["statements"] for node in _body_nodes(statement)]
    returns = [node for node in nodes if _kind(node) == "RETURN_STMT"]
    if any(node.get("kind") in {144, 210, 211} for node in nodes):
        return []

    def conjuncts(node):
        node = _expression(node)
        if _kind(node) == "BINARY_OPERATOR" and node.get("operator") == "==":
            return [node]
        if _kind(node) == "BINARY_OPERATOR" and node.get("operator") == "&&":
            parts = [conjuncts(child) for child in node.get("children", ())]
            return sum(parts, []) if len(parts) == 2 and all(parts) else []
        return []

    if len(returns) == 1 and returns[0] in body["statements"] and returns[0].get("children"):
        predicates = conjuncts(returns[0]["children"][0])
        if predicates:
            return predicates
    verdict = None
    if len(returns) == 1 and returns[0] in body["statements"] and returns[0].get("children"):
        verdict, fields = _member_path(returns[0]["children"][0])
        if fields:
            verdict = None
    if verdict:
        declaration = next((node for node in nodes if _kind(node) == "VAR_DECL" and node.get("usr") == verdict), None)
        if declaration is None or not any(child.get("bool_value") is True for child in declaration.get("children", ())):
            return []
        for node in nodes:
            if (_kind(node) == "VAR_DECL" and node.get("canonical_type_kind") in {101, 103, 104}
                    and verdict in _referenced_roots(node)):
                return []
            if (_kind(node) == "UNARY_OPERATOR" and node.get("operator") == "&"
                    and verdict in _referenced_roots(node)):
                return []
            if _kind(node) == "CALL_EXPR":
                for index, argument in enumerate(node.get("arguments", ())):
                    parameters = node.get("parameters", ())
                    if verdict in _referenced_roots(argument) and (
                            index >= len(parameters) or parameters[index].get("mutable_output") is not False):
                        return []
            children = node.get("children", ())
            if not children or _member_path(children[0])[0] != verdict:
                continue
            if (_kind(node) in {"UNARY_OPERATOR", "COMPOUND_ASSIGNMENT_OPERATOR"}
                    or (_kind(node) == "BINARY_OPERATOR" and (node.get("operator") != "="
                        or len(children) != 2 or _expression(children[1]).get("bool_value") is not False))):
                return []
    else:
        terminal = body["statements"][-1] if body["statements"] else {}
        if (_kind(terminal) != "RETURN_STMT" or not terminal.get("children")
                or _expression(terminal["children"][0]).get("bool_value") is not True
                or any(node is not terminal and (not node.get("children")
                    or _expression(node["children"][0]).get("bool_value") is not False) for node in returns)):
            return []

    def unconditional(statements):
        for statement in statements:
            yield statement
            if _kind(statement) == "COMPOUND_STMT":
                yield from unconditional(statement.get("children", ()))
            elif statement.get("kind") == 208 and statement.get("children"):
                # A do-body executes before its condition. Other conditional
                # branches/loops need dynamic predicate recording, out of scope.
                if not any(node.get("kind") in {212, 213}
                           for node in _body_nodes(statement["children"][0])):
                    yield from unconditional(statement["children"][:1])

    predicates = []
    for node in unconditional(body["statements"]):
        if _kind(node) != "IF_STMT" or len(node.get("children", ())) < 2:
            continue
        condition, branch = node["children"][:2]
        condition = _expression(condition)
        if _kind(condition) != "BINARY_OPERATOR" or condition.get("operator") != "!=":
            continue
        direct = branch.get("children", ()) if _kind(branch) == "COMPOUND_STMT" else [branch]
        fails = any((_kind(child) == "RETURN_STMT" and len(child.get("children", ())) == 1
                     and _expression(child["children"][0]).get("bool_value") is False)
                    or (verdict and _kind(child) == "BINARY_OPERATOR" and child.get("operator") == "="
                        and len(child.get("children", ())) == 2 and _member_path(child["children"][0]) == (verdict, ())
                        and _expression(child["children"][1]).get("bool_value") is False) for child in direct)
        if fails:
            predicates.append(condition)
    return predicates


def _return_captures(body, call):
    returns = [node for statement in body["statements"] for node in _body_nodes(statement)
               if _kind(node) == "RETURN_STMT"]
    if len(returns) != 1 or len(returns[0].get("children", ())) != 1:
        return {}
    returned = returns[0]["children"][0]
    # clang represents returning a local POD record as an implicit move/copy
    # constructor.  Unwrap only the compiler-marked trivial transfer; never
    # infer through an arbitrary helper or user constructor.
    while _trivial_transfer(returned) and returned.get("children"):
        children = returned["children"]
        if len(children) != 1:
            return {}
        returned = children[0]
    returned_root, members = _member_path(returned)
    if not returned_root or members:
        return {}
    assignments = {}
    all_nodes = [node for statement in body["statements"] for node in _body_nodes(statement)]
    if any(_kind(node) == "VAR_DECL" and node.get("canonical_type_kind") in {101, 103, 104}
           and returned_root in _referenced_roots(node) for node in all_nodes):
        return {}
    for statement in body["statements"]:
        # Captures inside branches/loops need a dataflow analysis, outside this scope.
        node = _expression(statement)
        if (_kind(node) != "BINARY_OPERATOR" or node.get("operator") != "="
                or len(node.get("children", ())) != 2 or node["source_start"] < call["source_end"]):
            continue
        left, right = node["children"]
        root, fields = _member_path(left)
        if root == returned_root and fields and _scalar(left):
            # Unresolved aliases or later mutations cannot certify this capture.
            affected = False
            for other in all_nodes:
                if other is node or other.get("source_start", -1) <= node["source_start"]:
                    continue
                children = other.get("children", ())
                if _kind(other) in {"BINARY_OPERATOR", "COMPOUND_ASSIGNMENT_OPERATOR", "UNARY_OPERATOR"} and children:
                    other_root, other_fields = _member_path(children[0])
                    if other_root == returned_root and (not other_fields or other_fields[:len(fields)] == fields):
                        affected = True
                if _kind(other) == "CALL_EXPR":
                    for index, argument in enumerate(other.get("arguments", ())):
                        if returned_root not in _referenced_roots(argument):
                            continue
                        argument_root, argument_fields = _member_path(argument)
                        params = other.get("parameters", ())
                        writable = index >= len(params) or params[index].get("mutable_output") is not False
                        if writable and (argument_root != returned_root or not argument_fields
                                         or argument_fields[:len(fields)] == fields):
                            affected = True
            if not affected:
                assignments[fields] = (node, right)
    return assignments


def observation_qualification_probe(testbench_code, original_code, mapping, original_name, candidate_name,
                                    *, recording_execution_id=None, **context):
    """Prepare optional read-only recording for provable input-proxy comparisons.

    Absence or partial compiler facts is observational, never a new qualification gate.
    No manifest prose, field names, dimensions, or expected values decide semantics.
    """
    result = {"status": "unproven", "channels": []}
    if not mapping:
        return testbench_code, result
    from agrefactor.cpp_interface import inspect_function_body, extract_top_type_contract
    bodies = {}
    for name in (*HELPERS, "main"):
        facts = inspect_function_body(testbench_code, name, **context)
        if facts.get("status") != "confirmed" or not facts.get("translation_unit_complete") or not facts.get("body"):
            return testbench_code, dict(result, reason="bounded_body_facts_unavailable", helper=name)
        bodies[name] = facts["body"]
    original = extract_top_interface(testbench_code, original_name, require_definition=False, **context)
    candidate = extract_top_interface(testbench_code, candidate_name, require_definition=False, **context)
    if original is None or candidate is None:
        return testbench_code, dict(result, reason="top_interface_unavailable")
    # Const or by-value records may contain writable pointers. Reuse the
    # existing layout graph and decline this optional proof when alias-free
    # storage cannot be established; this never rejects the Public suite.
    for name in (candidate_name, HELPERS[1]):
        types = extract_top_type_contract(testbench_code, name, **context)
        if types.get("status") != "confirmed" or not types.get("translation_unit_complete"):
            return testbench_code, dict(result, reason="candidate_record_alias_facts_unavailable")
        if any(layer.get("kind") in {"pointer", "lvalue_reference", "rvalue_reference"}
               for record in types.get("types", ()) for field in record.get("fields", ())
               for layer in field.get("layers", ())):
            return testbench_code, dict(result, reason="embedded_pointer_alias_unproven")
    orig_call = _top_call(bodies[HELPERS[0]], original.usr)
    cand_call = _top_call(bodies[HELPERS[1]], candidate.usr)
    if not orig_call or not cand_call:
        return testbench_code, dict(result, reason="single_direct_top_call_unproven")
    captures = [_return_captures(bodies[name], call) for name, call in zip(HELPERS[:2], (orig_call, cand_call))]
    comparator = bodies[HELPERS[2]]
    parameters = comparator.get("parameters", ())
    if len(parameters) < 2:
        return testbench_code, dict(result, reason="comparison_parameter_binding_unproven")
    comparison_roots = [item.get("usr") for item in parameters[:2]]
    helper_interfaces = {name: extract_top_interface(testbench_code, name, **context) for name in HELPERS}
    if any(item is None for item in helper_interfaces.values()):
        return testbench_code, dict(result, reason="helper_interface_unavailable")
    # Exact main-call arguments must be values returned by the two observers.
    main_nodes = [node for statement in bodies["main"]["statements"] for node in _body_nodes(statement)]
    observation_variables = {}
    for node in main_nodes:
        if _kind(node) == "VAR_DECL":
            calls = [child for child in _body_nodes(node) if _kind(child) == "CALL_EXPR" and not _trivial_transfer(child)]
            if len(calls) == 1:
                for name in HELPERS[:2]:
                    if calls[0].get("callee_usr") == helper_interfaces[name].usr:
                        observation_variables[node.get("usr")] = name
    comparisons = [node for node in main_nodes if _kind(node) == "CALL_EXPR"
                   and node.get("callee_usr") == helper_interfaces[HELPERS[2]].usr]
    if not comparisons:
        return testbench_code, dict(result, reason="comparison_invocation_unproven")
    observed_roots = set(observation_variables)
    for node in main_nodes:
        children = node.get("children", ())
        if (_kind(node) in {"BINARY_OPERATOR", "COMPOUND_ASSIGNMENT_OPERATOR", "UNARY_OPERATOR"}
                and children and _member_path(children[0])[0] in observed_roots
                and node.get("operator") not in {"==", "!="}):
            return testbench_code, dict(result, reason="observation_changed_before_comparison")
        if _kind(node) == "CALL_EXPR":
            for index, argument in enumerate(node.get("arguments", ())):
                params = node.get("parameters", ())
                if _referenced_roots(argument) & observed_roots and (
                        index >= len(params) or params[index].get("mutable_output") is not False):
                    return testbench_code, dict(result, reason="observation_writable_alias_unproven")
    for comparison in comparisons:
        arguments = comparison.get("arguments", ())
        if len(arguments) < 2 or any(observation_variables.get(_member_path(arguments[index])[0]) != HELPERS[index]
                                     for index in range(2)):
            return testbench_code, dict(result, reason="comparison_observer_arguments_unproven")
    for statement in comparator["statements"]:
        for node in _body_nodes(statement):
            children = node.get("children", ())
            if (_kind(node) in {"BINARY_OPERATOR", "COMPOUND_ASSIGNMENT_OPERATOR", "UNARY_OPERATOR"}
                    and children and _member_path(children[0])[0] in comparison_roots
                    and node.get("operator") not in {"==", "!=", "&&", "||", "<", ">", "<=", ">="}):
                return testbench_code, dict(result, reason="comparison_capture_mutation_unproven")
    candidate_inputs = {item.get("usr") for item in bodies[HELPERS[1]].get("parameters", ())}
    candidate_nodes = [node for statement in bodies[HELPERS[1]]["statements"] for node in _body_nodes(statement)]
    if any(_kind(node) == "CXX_CONST_CAST_EXPR" or (
            _kind(node) == "VAR_DECL" and node.get("canonical_type_kind") in {101, 103, 104}
            and _referenced_roots(node) & candidate_inputs) or (
            _kind(node) == "BINARY_OPERATOR" and node.get("operator") == "=" and len(node.get("children", ())) == 2
            and node["children"][0].get("canonical_type_kind") in {101, 103, 104}
            and _referenced_roots(node["children"][1]) & candidate_inputs) for node in candidate_nodes):
        return testbench_code, dict(result, reason="input_alias_or_const_escape_unproven")
    # A writable path from a Candidate/helper call to the observer input defeats independence.
    for statement in bodies[HELPERS[1]]["statements"]:
        for call in _body_nodes(statement):
            if _kind(call) != "CALL_EXPR":
                continue
            for index, argument in enumerate(call.get("arguments", ())):
                if _referenced_roots(argument) & candidate_inputs:
                    params = call.get("parameters", ())
                    if index >= len(params) or params[index].get("mutable_output") is not False:
                        return testbench_code, dict(result, reason="input_writable_alias_unproven")
    for predicate in _comparison_predicates(comparator):
        if _kind(predicate) != "BINARY_OPERATOR" or predicate.get("operator") not in {"==", "!="}:
            continue
        operands = predicate.get("children", ())
        if len(operands) != 2:
            continue
        paths = [_member_path(item) for item in operands]
        if [item[0] for item in paths] == comparison_roots[::-1]:
            operands, paths = operands[::-1], paths[::-1]
        if [item[0] for item in paths] != comparison_roots or not all(_scalar(item) for item in operands):
            continue
        left_capture, right_capture = (captures[index].get(paths[index][1]) for index in range(2))
        if not left_capture or not right_capture:
            continue
        input_root, input_fields = _member_path(right_capture[1])
        if input_root not in candidate_inputs or not input_fields:
            continue
        # The same scalar input reaches the top by value, not writable reference.
        source_path = _member_path(right_capture[1])
        passed_by_value = any(_member_path(argument) == source_path
                              and not parameter.get("pointer_like")
                              and parameter.get("reference_target_kind") is None
                              and "&" not in parameter.get("canonical_type", "")
                              for argument, parameter in zip(cand_call.get("arguments", ()), cand_call.get("parameters", ())))
        # Original read is a pure post-call scalar load from real writable output storage.
        original_roots = _referenced_roots(left_capture[1])
        writable_roots = set().union(*(_referenced_roots(argument)
            for argument, parameter in zip(orig_call.get("arguments", ()), orig_call.get("parameters", ()))
            if parameter.get("mutable_output"))) if orig_call.get("arguments") else set()
        pure_original = all(_kind(item) not in {"CALL_EXPR", "CXX_NEW_EXPR", "CXX_DELETE_EXPR"}
                            for item in _body_nodes(left_capture[1]))
        if not passed_by_value or not pure_original or not original_roots or not original_roots <= writable_roots:
            continue
        original_expression, candidate_expression = (_member_read(item) for item in operands)
        if not original_expression or not candidate_expression:
            continue
        channel = {"comparison": {"line": predicate.get("line"), "source_start": predicate["source_start"],
                                  "operator": predicate["operator"]},
                   "original_capture": left_capture[0], "candidate_capture": right_capture[0],
                   "original_expression": original_expression, "candidate_expression": candidate_expression,
                   "input_binding": {"root_usr": input_root, "member_usrs": input_fields},
                   "original_top_usr": original.usr, "candidate_top_usr": candidate.usr}
        channel["channel_id"] = _json_sha(channel)
        if not any(item["channel_id"] == channel["channel_id"] for item in result["channels"]):
            result["channels"].append(channel)
    if not result["channels"]:
        return testbench_code, dict(result, reason="no_proven_input_proxy_comparison")
    result.update(status="recording_prepared", mapping_sha256=mapping["sha256"],
                  original_sha256=_sha(original_code), testbench_sha256=_sha(testbench_code),
                  candidate_declaration_sha256=_sha(candidate.source_declaration),
                  comparison_invocations=[{"line": node.get("line"), "source_start": node["source_start"]} for node in comparisons])
    recording_execution_id = recording_execution_id or _sha(testbench_code)[:32]
    if not isinstance(recording_execution_id, str) or not recording_execution_id or any(
            character not in "0123456789abcdef" for character in recording_execution_id):
        return testbench_code, dict(result, status="unproven", reason="recording_identity_unavailable")
    result["recording_execution_id"] = recording_execution_id
    result["recording_filename"] = "agrefactor_observation_probe_" + recording_execution_id + ".tsv"
    result["probe_sha256"] = _json_sha(result)
    prefix = "agrefactor_observation_probe_" + result["probe_sha256"][:12]
    # Read existing scalar captures at the comparator entry, not tops/getters/oracles.
    recorder = ('\n#include <fstream>\n#include <sstream>\n#include <iomanip>\n#include <limits>\n'
                'namespace ' + prefix + ' { template<class T> std::string scalar(const T& value) {'
                ' std::ostringstream s; s << std::setprecision(std::numeric_limits<T>::max_digits10) << +value; return s.str(); } }\n')
    lines = ["\n{ static unsigned long long " + prefix + "_invocation = 0; ++" + prefix + "_invocation;",
             'std::ofstream ' + prefix + '_out("' + result["recording_filename"] + '", std::ios::app);']
    for channel in result["channels"]:
        lines.append(prefix + '_out << "' + recording_execution_id + '\\t" << ' + prefix + '_invocation << "\\t' + channel["channel_id"]
                     + '\\t" << ' + prefix + '::scalar(' + channel["original_expression"] + ') << "\\t" << '
                     + prefix + '::scalar(' + channel["candidate_expression"] + ') << "\\n";')
    lines.append("}\n")
    position = comparator["source_start"] + 1
    encoded = testbench_code.encode("utf-8")
    if encoded[position - 1:position] != b"{":
        return testbench_code, dict(result, status="unproven", reason="comparator_body_range_unproven")
    instrumented = recorder + (encoded[:position] + "\n".join(lines).encode("utf-8") + encoded[position:]).decode("utf-8")
    result["instrumented_source_sha256"] = _sha(instrumented)
    return instrumented, result


def complete_observation_qualification(probe, *, work_dir, execution_complete, original_executed,
                                       case_counts=None):
    """An input proxy is wrong only with an actual qualified counterexample."""
    evidence = dict(probe)
    if probe.get("status") != "recording_prepared":
        return evidence
    path = Path(work_dir) / probe["recording_filename"]
    evidence.update(status="unproven", execution_complete=bool(execution_complete),
                    original_executed=bool(original_executed), case_counts=case_counts)
    if (not execution_complete or not case_counts
            or case_counts.get("complete") is not True or case_counts.get("identity_verified") is not True
            or path.is_symlink() or not path.is_file()):
        return dict(evidence, reason="qualification_execution_incomplete")
    expected = {item["channel_id"] for item in probe["channels"]}
    invocations, counterexamples = {}, []
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            execution_id, index, channel, original, proxy = raw.split("\t")
            if execution_id != probe["recording_execution_id"]:
                raise ValueError("recording execution identity differs")
            index = int(index)
            if index < 1 or channel not in expected or channel in invocations.setdefault(index, set()):
                raise ValueError("ambiguous comparison invocation")
            invocations[index].add(channel)
            left, right = Decimal(original), Decimal(proxy)
            if not left.is_finite() or not right.is_finite():
                raise ValueError("nonfinite scalar recording")
            if left != right:
                counterexamples.append({"comparison_invocation": index, "channel_id": channel,
                                        "original_value": original, "input_proxy_value": proxy})
        if not invocations or set(invocations) != set(range(1, len(invocations) + 1)) or any(value != expected for value in invocations.values()):
            raise ValueError("incomplete comparison recording")
        if len(invocations) != case_counts.get("evaluated_cases"):
            raise ValueError("comparison count differs from completed case evidence")
    except (OSError, ValueError, InvalidOperation) as exc:
        return dict(evidence, reason=str(exc))
    # The bound observer must return to this actual comparator invocation,
    # after its direct Original top call. Static preparation alone is not
    # execution evidence; the verified nonempty recording completes it.
    evidence.update(original_executed=True,
                    original_execution_authority=("gcov_function_execution" if original_executed
                        else "compiler_bound_observer_return_and_runtime_comparison"))
    evidence.update(status="contradiction_proven" if counterexamples else "no_contradiction_observed",
                    recording_path=str(path), recording_sha256=_sha(path.read_text(encoding="utf-8")),
                    comparison_count=len(invocations), counterexamples=counterexamples)
    evidence["evidence_sha256"] = _json_sha(evidence)
    return evidence


def validated_observation_contradiction(evidence, *, testbench_code, original_code):
    """A stale/partial optional receipt must not override existing typed authority."""
    if not isinstance(evidence, dict) or evidence.get("status") != "contradiction_proven":
        return None
    payload = dict(evidence)
    claimed = payload.pop("evidence_sha256", None)
    if (claimed != _json_sha(payload) or evidence.get("testbench_sha256") != _sha(testbench_code)
            or evidence.get("original_sha256") != _sha(original_code) or not evidence.get("counterexamples")
            or not evidence.get("execution_complete") or not evidence.get("original_executed")):
        return None
    identity = evidence.get("execution_identity", {})
    if identity.get("execution_id") != evidence.get("recording_execution_id") or identity.get("testbench_sha256") != _sha(testbench_code):
        return None
    from flow.tools.case_evidence import read_case_counts
    count_path = Path(evidence.get("case_evidence_path", ""))
    case_counts = read_case_counts(count_path, expected_identity=identity)
    if (case_counts is None or case_counts != evidence.get("case_counts")
            or case_counts.get("evaluated_cases") != evidence.get("comparison_count")
            or _sha(count_path.read_text(encoding="utf-8")) != evidence.get("case_evidence_sha256")):
        return None
    path = Path(evidence.get("recording_path", ""))
    if path.is_symlink() or not path.is_file() or _sha(path.read_text(encoding="utf-8")) != evidence.get("recording_sha256"):
        return None
    for artifact, digest in (("compiled_testbench_path", "compiled_testbench_sha256"),
                             ("prepared_reference_path", "prepared_reference_sha256")):
        artifact_path = Path(evidence.get(artifact, ""))
        if (artifact_path.is_symlink() or not artifact_path.is_file()
                or _sha(artifact_path.read_text(encoding="utf-8")) != evidence.get(digest)):
            return None
    return dict(evidence)
