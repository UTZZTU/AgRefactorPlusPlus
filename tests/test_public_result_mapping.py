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


if __name__ == "__main__":
    unittest.main()
