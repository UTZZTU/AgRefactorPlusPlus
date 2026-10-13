import copy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from flow.tools import result_mapping as mapping


ORIGINAL = """int source_task(int input, int *out) {
    *out = input * 2;
    return input < 0 ? 7 : 0;
}
"""
SHARED = """// AGREFACTOR_SHARED_RESULT_MAPPING_BEGIN
int source_task(int input, int *out);
void source_task_hls(int input, int *out, int *status);
struct Observation { int status; int output; };
Observation agrefactor_observe_original(int input) {
    Observation result{};
    result.status = source_task(input, &result.output);
    return result;
}
Observation agrefactor_observe_candidate(int input) {
    Observation result{};
    source_task_hls(input, &result.output, &result.status);
    return result;
}
bool agrefactor_compare_observations(Observation original, Observation candidate) {
    return original.status == candidate.status && original.output == candidate.output;
}
// AGREFACTOR_SHARED_RESULT_MAPPING_END"""
MANIFEST = {"observables": [
    {"id": "status", "kind": "return_value", "original": "Original call return status",
     "candidate": "status output parameter", "source_evidence": ["return input < 0 ? 7 : 0;"]},
    {"id": "output", "kind": "output_parameter", "original": "out after Original call",
     "candidate": "out after Candidate call", "source_evidence": ["*out = input * 2;"]},
]}


def testbench(inputs, manifest=MANIFEST):
    return (
        SHARED + "\n" + mapping.MANIFEST_BEGIN + "\n" + json.dumps(manifest) + "\n*/\n"
        + "int main() { int mismatch = 0; int inputs[] = {"
        + ",".join(map(str, inputs)) + "};\n"
        + "for (int input: inputs) { auto original = agrefactor_observe_original(input); "
        + "auto candidate = agrefactor_observe_candidate(input); "
        + "mismatch += !agrefactor_compare_observations(original, candidate); }\n"
        + "return mismatch ? 1 : 0; }\n"
    )


def interface(source, name, **_):
    if name in mapping.HELPERS:
        start = source.index(name)
        return SimpleNamespace(source_start=start, source_end=start + len(name), linker_symbol=name)
    return SimpleNamespace(
        canonical_result_type="int" if name == "source_task" else "void",
        parameters=(), linker_symbol=name,
    )


def compiler_facts(source, name, **_):
    calls = {
        "main": list(mapping.HELPERS),
        mapping.HELPERS[0]: ["source_task"],
        mapping.HELPERS[1]: ["source_task_hls"],
    }.get(name, [])
    return {"status": "confirmed", "reachable_calls": [{"linker_symbol": item} for item in calls]}


class PublicResultMappingTests(unittest.TestCase):
    def template_mapping_source(self):
        original = "template<int Extent, class Element> void transform(Element (&values)[Extent]) { values[0] += Element(2); }"
        shared = """// AGREFACTOR_SHARED_RESULT_MAPPING_BEGIN
template<int Extent, class Element> void transform(Element (&values)[Extent]);
void transform_hls(double *values, int extent);
struct Observation { double value; };
template<int Extent> Observation call_original() {
    double values[Extent] = {};
    transform<Extent, double>(values);
    return Observation{values[0]};
}
Observation agrefactor_observe_original() { return call_original<3>(); }
Observation agrefactor_observe_candidate() {
    double values[3] = {};
    transform_hls(values, 3);
    return Observation{values[0]};
}
bool agrefactor_compare_observations(Observation a, Observation b) { return a.value == b.value; }
// AGREFACTOR_SHARED_RESULT_MAPPING_END"""
        manifest = {"observables": [{"id": "values", "kind": "output_parameter", "original": "first output element",
                                    "candidate": "first output element", "source_evidence": ["values[0] += Element(2);"]}]}
        code = shared + "\n" + mapping.MANIFEST_BEGIN + "\n" + json.dumps(manifest) + "\n*/\n" + "int main(){return !agrefactor_compare_observations(agrefactor_observe_original(), agrefactor_observe_candidate());}"
        return original, code

    def test_template_mapping_uses_real_specialization_identity_and_compiles(self):
        from agrefactor.reference_source import isolate_reference_program_entry
        original, code = self.template_mapping_source()
        prepared = isolate_reference_program_entry(original, top_function="transform", testbench_code=code)
        frozen = mapping.freeze_result_mapping(code, prepared, "transform", "transform_hls")
        self.assertTrue(frozen["compiler_structure_verified"])
        mapping.validate_result_mapping_identity(frozen)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "all.cpp").write_text(prepared + code + "\nvoid transform_hls(double* values, int) { values[0] += 2; }")
            compiled = subprocess.run(["g++", "-std=c++11", "all.cpp", "-o", "check"], cwd=root, capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            self.assertEqual(subprocess.run([str(root / "check")]).returncode, 0)

    def test_template_mapping_rejects_nonidentical_original_declaration(self):
        original, code = self.template_mapping_source()
        changed = code.replace("template<int Extent, class Element> void transform(Element (&values)[Extent]);",
                               "template<int Extent, class Element> void transform(Element *values);")
        with self.assertRaisesRegex(ValueError, "template declaration differs from Original") as caught:
            mapping.freeze_result_mapping(changed, original, "transform", "transform_hls")
        self.assertIn('expected_usr', str(caught.exception))
        self.assertIn('actual_usr', str(caught.exception))

    def test_mapping_distinguishes_unavailable_calls_from_unproven_template_binding(self):
        original, code = self.template_mapping_source()
        with patch.object(mapping, "inspect_top_entry", return_value={"status": "unknown", "diagnostics": []}):
            with self.assertRaisesRegex(mapping.ResultMappingVerificationError, "helper_reachable_calls_unavailable"):
                mapping.freeze_result_mapping(code, original, "transform", "transform_hls")
        real_inspect = mapping.inspect_top_entry
        def without_binding(source, name, **context):
            facts = real_inspect(source, name, **context)
            if name == mapping.HELPERS[0]:
                facts["reachable_calls"] = []
            return facts
        with patch.object(mapping, "inspect_top_entry", side_effect=without_binding):
            with self.assertRaisesRegex(mapping.ResultMappingVerificationError, "template_binding_not_proven") as caught:
                mapping.freeze_result_mapping(code, original, "transform", "transform_hls")
            self.assertIn("reachable_calls", caught.exception.partial_mapping["verification_facts"])
            self.assertIn('template_usr', str(caught.exception))
            self.assertIn('reachable_calls', str(caught.exception))
            self.assertFalse(caught.exception.repair_eligible)

    def freeze(self, code=None, **kwargs):
        with patch.object(mapping, "extract_top_interface", side_effect=interface), patch.object(
            mapping, "inspect_top_entry", side_effect=compiler_facts
        ):
            return mapping.freeze_result_mapping(code or testbench([3, -2]), ORIGINAL,
                                                 "source_task", "source_task_hls", **kwargs)

    def test_frozen_mapping_preserves_return_status_and_output_as_distinct_observables(self):
        result = self.freeze()
        self.assertEqual([item["kind"] for item in result["observables"]], ["return_value", "output_parameter"])
        self.assertEqual(result["shared_cpp"], SHARED)
        self.assertEqual(result["semantic_status"], "source_grounded_execution_required")
        mapping.validate_result_mapping_identity(result)

    def test_legacy_without_new_fields_is_observed_without_gate(self):
        self.assertIsNone(mapping.freeze_result_mapping("int main(){return 0;}", ORIGINAL,
                                                       "source_task", "source_task_hls"))

    def test_unparsed_legacy_does_not_infer_an_interface_transform(self):
        with patch.object(mapping, "extract_top_interface", return_value=None):
            self.assertIsNone(mapping.freeze_result_mapping("int main(){return 0;}", ORIGINAL,
                                                           "source_task", "source_task_hls", require_explicit=True))

    def test_proven_interface_conversion_requires_explicit_observation(self):
        with self.assertRaisesRegex(ValueError, "no explicit common result mapping"):
            self.freeze("int main(){return 0;}", require_explicit=True)

    def test_provided_transformed_interface_without_mapping_remains_observational(self):
        # External/legacy Public suites do not acquire a new gate merely
        # because their compiler-resolved ABI differs; an explicit block, when
        # present, is still validated by the same resolver.
        self.assertIsNone(self.freeze("int main(){return 0;}", require_explicit=False))

    def test_omitted_original_status_is_not_replaced_by_candidate_output_count(self):
        manifest = copy.deepcopy(MANIFEST)
        manifest["observables"].pop(0)
        with self.assertRaisesRegex(ValueError, "return value has no observation"):
            self.freeze(testbench([3], manifest))

    def test_source_reference_is_checked_as_identity_not_semantic_approval(self):
        manifest = copy.deepcopy(MANIFEST)
        manifest["observables"][0]["source_evidence"] = ["return made_up_status;"]
        with self.assertRaisesRegex(ValueError, "source evidence does not match"):
            self.freeze(testbench([3], manifest))

    def test_observable_extra_and_missing_fields_are_rejected_with_precise_diagnostics(self):
        for added, removed in (({"read": "status capture", "preservation": "same status"}, ()),
                               ({}, ("candidate", "source_evidence"))):
            with self.subTest(added=added, removed=removed):
                manifest = copy.deepcopy(MANIFEST)
                item = manifest["observables"][0]
                item.update(added)
                for field in removed:
                    del item[field]
                with self.assertRaises(ValueError) as caught:
                    self.freeze(testbench([3], manifest))
                detail = str(caught.exception)
                self.assertIn("invalid observable channel; index=0", detail)
                self.assertIn("expected_keys=" + json.dumps(sorted(MANIFEST["observables"][0])), detail)
                self.assertIn("actual_type=dict", detail)
                self.assertIn("actual_keys=" + json.dumps(sorted(item)), detail)
                self.assertIn("missing_keys=" + json.dumps(sorted(removed)), detail)
                self.assertIn("extra_keys=" + json.dumps(sorted(added)), detail)

    def test_manifest_fields_and_nonobject_observable_are_rejected_with_schema_evidence(self):
        with self.assertRaisesRegex(ValueError, 'unexpected result mapping manifest fields.*extra_keys=\\["notes"\\]'):
            self.freeze(testbench([3], dict(MANIFEST, notes="unexpected")))
        manifest = copy.deepcopy(MANIFEST)
        manifest["observables"][0] = "not an object"
        with self.assertRaisesRegex(ValueError, "invalid observable channel; index=0.*actual_type=str.*missing_keys="):
            self.freeze(testbench([3], manifest))

    def test_generation_instruction_states_exact_schema_and_comment_safe_evidence(self):
        instruction = mapping.mapping_generation_instruction()
        self.assertIn("exactly one key: observables", instruction)
        self.assertIn("exactly these five keys: id, kind, original, candidate, source_evidence", instruction)
        self.assertIn("return_value, output_parameter, or post_call_state", instruction)
        self.assertIn("comments outside the manifest", instruction)
        self.assertIn("no explanations added to the excerpts", instruction)
        self.assertIn("encode such text as *\\u002f", instruction)

    def test_hidden_may_change_inputs_while_sharing_exact_adapters(self):
        result = self.freeze()
        with patch.object(mapping, "extract_top_interface", side_effect=interface), patch.object(
            mapping, "inspect_top_entry", side_effect=compiler_facts
        ):
            mapping.validate_frozen_result_mapping(testbench([5, -7]), result)
        self.assertNotEqual(testbench([3, -2]), testbench([5, -7]))

    def test_hidden_cannot_reinterpret_return_meaning_with_same_abi(self):
        result = self.freeze()
        changed = testbench([5]).replace("original.status == candidate.status && ", "")
        with self.assertRaisesRegex(ValueError, "observation changed"):
            mapping.validate_frozen_result_mapping(changed, result)

    def test_hidden_cannot_rewrite_frozen_manifest(self):
        result = self.freeze()
        changed_manifest = copy.deepcopy(MANIFEST)
        changed_manifest["observables"][0]["candidate"] = "different status channel"
        changed = testbench([5], changed_manifest)
        with self.assertRaisesRegex(ValueError, "manifest changed"):
            mapping.validate_frozen_result_mapping(changed, result)

    def test_unused_frozen_comparator_is_a_proven_contract_contradiction(self):
        result = self.freeze()
        with patch.object(mapping, "extract_top_interface", side_effect=interface), patch.object(
            mapping, "inspect_top_entry", return_value={"reachable_calls": []}
        ), self.assertRaisesRegex(ValueError, "bypasses frozen result observation"):
            mapping.validate_frozen_result_mapping(testbench([5]), result)

    def test_hash_change_cannot_promote_new_mapping_under_old_identity(self):
        result = self.freeze()
        result["observables"][0]["candidate"] = "different interpretation"
        with self.assertRaisesRegex(ValueError, "mapping identity differs"):
            mapping.validate_result_mapping_identity(result)

    def test_candidate_receives_read_only_public_mapping_not_testbench_generation_action(self):
        instruction = mapping.frozen_mapping_instruction(self.freeze(), for_candidate=True)
        self.assertIn("Do not copy Testbench helpers", instruction)
        self.assertNotIn("Keep the shared block verbatim and call", instruction)

    @unittest.skipUnless(shutil.which("g++"), "C++ host compiler unavailable")
    def test_same_compiled_helpers_preserve_status_and_output_on_independent_inputs(self):
        compiler = shutil.which("g++")
        candidate = """void source_task_hls(int input, int *out, int *status) {
            *out = input * 2; *status = input < 0 ? 7 : 0;
        }"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for split, values in (("public", [3, -2]), ("hidden", [5, -7])):
                source = root / f"{split}.cpp"
                executable = root / (split + ".exe")
                source.write_text(testbench(values) + ORIGINAL + candidate, encoding="utf-8")
                compile_result = subprocess.run([compiler, "-std=c++14", str(source), "-o", str(executable)], capture_output=True, text=True)
                self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
                self.assertEqual(subprocess.run([str(executable)], capture_output=True).returncode, 0)
            # Correct output with a wrong status must fail, even when the two
            # status/count values have identical C++ types.
            source.write_text(testbench([-7]) + ORIGINAL + candidate.replace("*status = input < 0 ? 7 : 0;", "*status = 1;"), encoding="utf-8")
            subprocess.run([compiler, "-std=c++14", str(source), "-o", str(executable)], check=True, capture_output=True)
            self.assertEqual(subprocess.run([str(executable)], capture_output=True).returncode, 1)

    def test_real_compiler_facts_validate_the_shared_helpers_when_available(self):
        code = testbench([3, -2])
        if mapping.extract_top_interface(code, mapping.HELPERS[0]) is None:
            self.skipTest("libclang unavailable")
        result = mapping.freeze_result_mapping(code, ORIGINAL, "source_task", "source_task_hls")
        self.assertIsNotNone(result)
        mapping.validate_frozen_result_mapping(testbench([5, -7]), result)
        bypassed = code.replace("mismatch += !agrefactor_compare_observations(original, candidate);", "mismatch += 0;")
        with self.assertRaisesRegex(ValueError, "bypasses frozen result observation"):
            mapping.validate_frozen_result_mapping(bypassed, result)

    def test_protocol_mentions_in_comments_strings_and_continuations_are_not_markers(self):
        prefix = (
            '/* Example: ' + mapping.BEGIN + '\n' + mapping.END + ' */\n'
            'const char *text = "' + mapping.BEGIN + '";\n'
            'const char *raw = R"tag(' + mapping.MANIFEST_BEGIN + '\n' + mapping.BEGIN + ')tag";\n'
            '// continued example \\\n' + mapping.BEGIN + '\n'
            'int ignored; ' + mapping.BEGIN + '\n'
        )
        result = self.freeze(prefix + testbench([3]))
        self.assertEqual(result["shared_cpp"], SHARED)

    def test_real_duplicate_and_reversed_protocol_markers_remain_invalid(self):
        with self.assertRaisesRegex(ValueError, "BEGIN must occur once.*found=2.*positions"):
            self.freeze(mapping.BEGIN + '\n' + testbench([3]))
        with self.assertRaisesRegex(ValueError, "END precedes BEGIN"):
            self.freeze(mapping.END + '\n' + mapping.BEGIN + '\n')

    def test_manifest_missing_duplicate_and_json_errors_are_distinct(self):
        code = testbench([3])
        with self.assertRaisesRegex(ValueError, "manifest must occur once.*found=0"):
            self.freeze(code.replace(mapping.MANIFEST_BEGIN, '/* misspelled_mapping_manifest'))
        with self.assertRaisesRegex(ValueError, "manifest must occur once.*found=2"):
            self.freeze(code + mapping.MANIFEST_BEGIN + '\n{}\n*/')
        with self.assertRaisesRegex(ValueError, "manifest JSON; line=.*column=.*reason="):
            self.freeze(code.replace(json.dumps(MANIFEST), '{"observables": ['))

    def test_receiver_gets_manifest_from_actual_frozen_payload(self):
        frozen = self.freeze()
        request = mapping.frozen_mapping_instruction(frozen)
        comments = mapping.extract_cpp_comments(request)["comments"]
        manifest_comment = next(item["text"] for item in comments
                                if item["text"].startswith(mapping.MANIFEST_BEGIN))
        # Construct the receiver from what was sent, not a prefilled Hidden TB.
        receiver = frozen["shared_cpp"] + '\n' + manifest_comment + '\nint main()' + testbench([5]).split('int main()', 1)[1]
        with patch.object(mapping, "extract_top_interface", side_effect=interface), patch.object(
            mapping, "inspect_top_entry", side_effect=compiler_facts
        ):
            mapping.validate_frozen_result_mapping(receiver, frozen)
        self.assertEqual(mapping._manifest_from_source(request)["observables"], MANIFEST["observables"])

    def test_manifest_already_inside_shared_block_is_not_duplicated(self):
        code = testbench([3])
        comment = mapping.MANIFEST_BEGIN + '\n' + json.dumps(MANIFEST) + '\n*/'
        code = code.replace(comment + '\n', '').replace(mapping.END, comment + '\n' + mapping.END)
        frozen = self.freeze(code)
        request = mapping.frozen_mapping_instruction(frozen)
        self.assertEqual(mapping._manifest_from_source(request), MANIFEST)

    def test_json_comment_terminator_is_escaped_without_changing_contract(self):
        frozen = self.freeze()
        frozen["observables"][0]["candidate"] = 'read status; text contains */'
        request = mapping.frozen_mapping_instruction(frozen)
        self.assertEqual(mapping._manifest_from_source(request)["observables"], frozen["observables"])
        self.assertIn('*\\u002f', request)

    def test_comment_safe_source_evidence_decodes_verbatim_and_preserves_mapping_identity(self):
        original = ORIGINAL.replace("return input < 0 ? 7 : 0;", "return input < 0 ? 7 : 0; /* exact evidence */")
        manifest = copy.deepcopy(MANIFEST)
        excerpt = "return input < 0 ? 7 : 0; /* exact evidence */"
        manifest["observables"][0]["source_evidence"] = [excerpt]
        unsafe = testbench([3], manifest)
        with self.assertRaisesRegex(ValueError, "manifest JSON;.*Unterminated string.*encode \\*/ inside JSON strings"):
            self.freeze(unsafe)
        safe = unsafe.replace(json.dumps(manifest), json.dumps(manifest).replace("*/", "*\\u002f"))
        with patch.object(mapping, "extract_top_interface", side_effect=interface), patch.object(
            mapping, "inspect_top_entry", side_effect=compiler_facts
        ):
            frozen = mapping.freeze_result_mapping(safe, original, "source_task", "source_task_hls")
            mapping.validate_frozen_result_mapping(safe, frozen)
        self.assertEqual(frozen["observables"], manifest["observables"])
        self.assertEqual(frozen["observables"][0]["source_evidence"], [excerpt])
        self.assertEqual(frozen["manifest_sha256"], mapping._json_sha(manifest))
        mapping.validate_result_mapping_identity(frozen)
        self.assertEqual(mapping._manifest_from_source(mapping.frozen_mapping_instruction(frozen)), manifest)

    def test_explicit_mapping_retains_identity_and_diagnostics_when_ast_facts_missing(self):
        failure = {"status": "unknown", "diagnostics": [{"severity": 3, "file": '/headers/stdio.h', "message": 'parse failure'}]}
        with patch.object(mapping, "extract_top_interface", side_effect=interface), patch.object(
            mapping, "inspect_top_entry", return_value=failure
        ), self.assertRaises(mapping.ResultMappingVerificationError) as caught:
            mapping.freeze_result_mapping(testbench([3]), ORIGINAL, 'source_task', 'source_task_hls')
        partial = caught.exception.partial_mapping
        self.assertEqual(partial["shared_cpp"], SHARED)
        self.assertFalse(partial["compiler_structure_verified"])
        self.assertEqual(partial["verification_diagnostics"], failure["diagnostics"])
        self.assertFalse(caught.exception.repair_eligible)
        mapping.validate_result_mapping_identity(partial)

    def test_utf8_and_crlf_source_keeps_original_shared_bytes(self):
        code = '// Unicode: \u4e2d\u6587\n' + testbench([3]).replace('\n', '\r\n')
        if mapping.extract_top_interface(code, mapping.HELPERS[0]) is None:
            self.skipTest('libclang unavailable')
        result = mapping.freeze_result_mapping(code, ORIGINAL, 'source_task', 'source_task_hls')
        self.assertEqual(result["shared_cpp"], SHARED.replace('\n', '\r\n'))

    def test_end_marker_trailing_whitespace_does_not_change_legacy_shared_identity(self):
        result = self.freeze(testbench([3]).replace(mapping.END, mapping.END + '   '))
        self.assertEqual(result['shared_cpp'], SHARED)

    def test_host_standard_headers_have_complete_real_mapping_facts(self):
        code = '#include <cstdio>\n#include <vector>\n' + testbench([3])
        result = mapping.freeze_result_mapping(code, ORIGINAL, 'source_task', 'source_task_hls')
        self.assertTrue(result["compiler_structure_verified"])

    def test_frozen_manifest_reports_exact_leaf_and_keeps_complete_evidence(self):
        result = self.freeze()
        manifest = copy.deepcopy(MANIFEST)
        manifest['observables'][1]['original'] = 'out before Original call'
        with self.assertRaises(ValueError) as caught:
            mapping.validate_frozen_result_mapping(testbench([5], manifest), result)
        detail = str(caught.exception)
        self.assertIn('/observables/1/original', detail)
        self.assertIn('out after Original call', detail)
        self.assertIn('out before Original call', detail)
        self.assertEqual(caught.exception.contract_evidence['differences'][0]['expected'], MANIFEST['observables'][1]['original'])
        self.assertEqual(caught.exception.contract_evidence['differences'][0]['actual'], manifest['observables'][1]['original'])

    def test_frozen_manifest_shape_order_and_missing_values_remain_rejected(self):
        for observables, expected_path in ((MANIFEST['observables'][:1], '/observables/1'),
                                          (list(reversed(MANIFEST['observables'])), '/observables/0/id'),
                                          ('changed', '/observables')):
            with self.subTest(observables=observables), self.assertRaises(ValueError) as caught:
                mapping.validate_frozen_result_mapping(testbench([5], {'observables': observables}), self.freeze())
            self.assertIn(expected_path, str(caught.exception))
        changed = copy.deepcopy(MANIFEST)
        del changed['observables'][0]['candidate']
        changed['observables'][0]['notes/a~b'] = None
        with self.assertRaises(ValueError) as caught:
            mapping.validate_frozen_result_mapping(testbench([5], changed), self.freeze())
        paths = {item['path'] for item in caught.exception.contract_evidence['differences']}
        self.assertIn('/observables/0/candidate', paths)
        self.assertIn('/observables/0/notes~1a~0b', paths)
        missing = next(item for item in caught.exception.contract_evidence['differences'] if item['path'].endswith('/candidate'))
        self.assertFalse(missing['actual_present'])

    def test_long_frozen_difference_is_bounded_but_not_lost(self):
        result = self.freeze()
        manifest = copy.deepcopy(MANIFEST)
        manifest['observables'][0]['candidate'] = 'different observation ' * 1000
        with self.assertRaises(ValueError) as caught:
            mapping.validate_frozen_result_mapping(testbench([5], manifest), result)
        detail = str(caught.exception)
        self.assertLess(len(detail), 2000)
        self.assertIn('/observables/0/candidate', detail)
        self.assertIn('truncated', detail)
        self.assertEqual(caught.exception.contract_evidence['differences'][0]['actual'], manifest['observables'][0]['candidate'])

    def test_frozen_shared_code_reports_local_change_and_hashes(self):
        result = self.freeze()
        changed = testbench([5]).replace('original.output == candidate.output', 'original.output != candidate.output')
        with self.assertRaises(ValueError) as caught:
            mapping.validate_frozen_result_mapping(changed, result)
        self.assertIn('expected_sha256', str(caught.exception))
        self.assertIn('actual_sha256', str(caught.exception))
        self.assertIn('original.output != candidate.output', str(caught.exception))
        self.assertEqual(caught.exception.contract_evidence['expected_shared_cpp'], SHARED)
        with self.assertRaises(ValueError) as caught:
            mapping.validate_frozen_result_mapping('int main(){return 0;}', result)
        self.assertIn('shared_block_present', str(caught.exception))
        self.assertIn(mapping.BEGIN, str(caught.exception))

    def test_frozen_baseline_identity_reports_claimed_and_computed_hash(self):
        for field in ('shared_cpp_sha256', 'sha256'):
            result = self.freeze()
            result[field] = 'invalid-identity'
            with self.subTest(field=field), self.assertRaises(ValueError) as caught:
                mapping.validate_result_mapping_identity(result)
            self.assertIn(field, str(caught.exception))
            self.assertIn('invalid-identity', str(caught.exception))
            self.assertIn('computed', str(caught.exception))
            self.assertEqual(caught.exception.contract_evidence['scope'], 'frozen_baseline')
        result = self.freeze()
        result['manifest_sha256'] = 'wrong-manifest-hash'
        with self.assertRaises(ValueError) as caught:
            mapping.validate_frozen_result_mapping(testbench([5]), result)
        self.assertIn('manifest_sha256', str(caught.exception))
        self.assertIn('wrong-manifest-hash', str(caught.exception))
        self.assertIn('computed', str(caught.exception))

    def test_channel_diagnostics_name_empty_duplicate_and_invalid_fields(self):
        mutations = ((0, 'original', '', '/observables/0/original'),
                     (1, 'id', 'status', '/observables/1/id'),
                     (1, 'kind', 'made_up_kind', '/observables/1/kind'))
        for index, field, value, path in mutations:
            manifest = copy.deepcopy(MANIFEST)
            manifest['observables'][index][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError) as caught:
                self.freeze(testbench([3], manifest))
            self.assertIn(path, str(caught.exception))
            self.assertIn('actual', str(caught.exception))
            self.assertTrue(caught.exception.contract_evidence)

    def test_helper_and_main_diagnostics_show_compiler_target_and_missing_calls(self):
        real_facts = compiler_facts
        def wrong_target(source, name, **context):
            facts = real_facts(source, name, **context)
            if name == mapping.HELPERS[0]:
                facts['reachable_calls'] = [{'linker_symbol': 'different_top'}]
            return facts
        with patch.object(mapping, 'extract_top_interface', side_effect=interface), patch.object(
            mapping, 'inspect_top_entry', side_effect=wrong_target
        ), self.assertRaises(ValueError) as caught:
            mapping.freeze_result_mapping(testbench([3]), ORIGINAL, 'source_task', 'source_task_hls')
        self.assertIn(mapping.HELPERS[0], str(caught.exception))
        self.assertIn('source_task', str(caught.exception))
        self.assertIn('different_top', str(caught.exception))
        result = self.freeze()
        with patch.object(mapping, 'extract_top_interface', side_effect=interface), patch.object(
            mapping, 'inspect_top_entry', return_value={'reachable_calls': [{'linker_symbol': mapping.HELPERS[0]}]}
        ), self.assertRaises(ValueError) as caught:
            mapping.validate_frozen_result_mapping(testbench([3]), result)
        self.assertIn('missing', str(caught.exception))
        self.assertIn(mapping.HELPERS[2], str(caught.exception))

    def test_missing_return_observation_and_outside_helper_have_precise_facts(self):
        manifest = copy.deepcopy(MANIFEST)
        manifest['observables'].pop(0)
        with self.assertRaises(ValueError) as caught:
            self.freeze(testbench([3], manifest))
        self.assertIn('return_type', str(caught.exception))
        self.assertIn('output_parameter', str(caught.exception))
        def outside(source, name, **context):
            result = interface(source, name, **context)
            if name == mapping.HELPERS[0]:
                result.source_end = len(source)
            return result
        with patch.object(mapping, 'extract_top_interface', side_effect=outside), self.assertRaises(ValueError) as caught:
            mapping.freeze_result_mapping(testbench([3]), ORIGINAL, 'source_task', 'source_task_hls')
        self.assertIn('actual_source_range', str(caught.exception))
        self.assertIn('expected_block_range', str(caught.exception))
        self.assertIn(mapping.HELPERS[0], str(caught.exception))

    def test_unavailable_compiler_facts_are_explained_without_new_repair_permission(self):
        facts = {'status': 'unknown', 'entries': [{'usr': 'known-template'}],
                 'reachable_calls': [], 'translation_unit_complete': False, 'diagnostics': []}
        error = mapping.ResultMappingVerificationError('template_binding_not_proven: arbitrary_helper', {}, facts)
        self.assertIn('known-template', str(error))
        self.assertIn('reachable_calls', str(error))
        self.assertFalse(error.repair_eligible)
        self.assertEqual(error.contract_evidence['compiler_facts']['entries'], facts['entries'])


if __name__ == "__main__":
    unittest.main()
