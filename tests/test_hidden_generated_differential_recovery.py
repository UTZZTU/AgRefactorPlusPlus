from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from flow.tools import tb_coverage, tb_optimizer


class HiddenGeneratedDifferentialRecoveryTests(unittest.TestCase):
    ORIGINAL = "int step(int value) { return value + 1; }\n"
    PINNED = "int step_hls(int value);"
    TB = (
        "#include <cstdio>\n"
        "int step(int value);\n"
        "int step_hls(int value);\n"
        "int main() {\n"
        "    const int inputs[] = {4, -3, 8};\n"
        "    for (int value : inputs) {\n"
        "        int expected = step(value);\n"
        "        int actual = step_hls(value);\n"
        "        if (expected != actual) {\n"
        '            std::printf("expected=%d actual=%d\\n", expected, actual);\n'
        "            return 1;\n"
        "        }\n"
        "    }\n"
        "    return 0;\n"
        "}\n"
    )
    BAD_STUB = "int step(int value);\nint step_hls(int value) { return 0; }\n"
    GOOD_STUB = (
        "int step(int value);\nint step_hls(int value) { return value + 1; }\n"
    )

    def failure(self):
        # Obtain receipts from the existing compiler/runtime/gcov path rather
        # than synthesizing a trusted ownership flag in the test fixture.
        result = tb_optimizer._measure_qualified_coverage(
            self.ORIGINAL, self.TB, self.BAD_STUB, "step",
            require_original_execution=True,
        )
        self.assertEqual(result["status"], "run_failed", result)
        self.assertEqual(result["failure_owner"], "unknown", result)
        return result

    def run_loop(self, *, failures=None, repair_stub=None, K=1,
                 max_repairs=3, hidden=True, artifact_root=None):
        loader = Mock()
        loader.load_agent.return_value = object()
        stub_requests = 0

        def artifact(_agent, _message, **kwargs):
            nonlocal stub_requests
            kind = kwargs["artifact_kind"]
            if kind == "testbench":
                return self.TB
            if kind == "empty_stub":
                return self.BAD_STUB
            self.assertEqual(kind, "stub")
            stub_requests += 1
            if stub_requests > 1 and repair_stub is not None:
                return repair_stub
            return self.BAD_STUB

        with ExitStack() as stack:
            stack.enter_context(patch.object(
                tb_optimizer, "HLSAgentLoader", return_value=loader
            ))
            requests = stack.enter_context(patch.object(
                tb_optimizer, "_request_cpp_artifact", side_effect=artifact
            ))
            if failures is None:
                measure = stack.enter_context(patch.object(
                    tb_optimizer, "_measure_qualified_coverage",
                    wraps=tb_optimizer._measure_qualified_coverage,
                ))
            else:
                measure = stack.enter_context(patch.object(
                    tb_optimizer, "_measure_qualified_coverage",
                    side_effect=deepcopy(failures),
                ))
            stack.enter_context(patch.object(
                tb_optimizer, "_synth_check", return_value=(True, "")
            ))
            result = tb_optimizer.run_trajectory(
                self.ORIGINAL, "step", K=K, target_pct=100.0,
                llm_config=None, want_sig_spec=False,
                pinned_hls_decl=self.PINNED, hidden_generation=hidden,
                emit_final_text=False, max_repairs=max_repairs,
                artifact_root=artifact_root,
            )
        return result, requests, measure

    def test_real_original_execution_allows_stub_only_requalification(self):
        with tempfile.TemporaryDirectory() as directory:
            result, requests, measure = self.run_loop(
                repair_stub=self.GOOD_STUB, artifact_root=directory
            )
            self.assertTrue(result["qualified"], result)
            self.assertEqual(measure.call_count, 2)
            self.assertEqual(len(result["rounds"]), 2)
            first, corrected = result["rounds"]
            self.assertEqual(first["failure_owner"], "unknown")
            self.assertEqual(first["next_action"], "review_unknown")
            self.assertFalse(first["evidence_complete"])
            original_receipt = first["original_qualification_evidence"]
            self.assertEqual(original_receipt["status"], "ok")
            self.assertTrue(original_receipt["original_entry_observed"])
            self.assertTrue(original_receipt["original_entry_executed"])
            # The qualification run uses a no-op Candidate. Its comparison
            # exit code is not proof that Original crashed or never ran.
            self.assertEqual(original_receipt["run_returncode"], 1)
            self.assertEqual(first["original_qualification_status"], "passed")
            self.assertEqual(corrected["ownership_action"], "regenerate_stub")
            self.assertTrue(corrected["testbench_reused"])
            self.assertEqual(first["tb_code"], self.TB)
            self.assertEqual(corrected["tb_code"], self.TB)
            self.assertEqual(corrected["status"], "ok")
            kinds = [call.kwargs["artifact_kind"]
                     for call in requests.call_args_list]
            self.assertEqual(kinds, ["testbench", "stub", "stub", "empty_stub"])
            persisted = json.loads((
                Path(directory) / "trajectory_000" / "round_001" / "coverage.json"
            ).read_text(encoding="utf-8"))
            self.assertEqual(persisted["failure_owner"], "unknown")
            self.assertEqual(persisted["next_action"], "review_unknown")
            self.assertEqual(persisted["original_qualification_evidence"],
                             original_receipt)
            identities = first["qualification_identity"]
            self.assertEqual(persisted["qualification_identity"], identities)
            self.assertEqual(identities["original_sha256"], hashlib.sha256(
                self.ORIGINAL.encode("utf-8")
            ).hexdigest())
            self.assertEqual(identities["testbench_sha256"], hashlib.sha256(
                self.TB.encode("utf-8")
            ).hexdigest())
            self.assertEqual(identities["stub_sha256"], hashlib.sha256(
                first["stub_code"].encode("utf-8")
            ).hexdigest())
            self.assertEqual(original_receipt["source_identity"], {
                "original_sha256": identities["original_sha256"],
                "testbench_sha256": identities["testbench_sha256"],
            })
            self.assertTrue(original_receipt["executions"])

    def test_unknown_differential_recovery_remains_hidden_generation_only(self):
        failure = self.failure()
        result, requests, measure = self.run_loop(
            failures=[failure], hidden=False
        )
        self.assertFalse(result["qualified"])
        self.assertEqual(measure.call_count, 1)
        self.assertEqual([call.kwargs["artifact_kind"]
                          for call in requests.call_args_list],
                         ["testbench", "stub"])
        self.assertEqual(result["rounds"][0]["failure_owner"], "unknown")
        self.assertEqual(result["rounds"][0]["next_action"], "review_unknown")

    def test_incomplete_or_conflicting_receipts_do_not_regenerate(self):
        failure = self.failure()
        cases = {}
        missing = deepcopy(failure)
        missing.pop("original_qualification_evidence")
        cases["missing_original_receipt"] = missing
        stale = deepcopy(failure)
        stale["original_qualification_evidence"]["source_identity"][
            "original_sha256"
        ] = "a" * 64
        cases["stale_original"] = stale
        stale_stub = deepcopy(failure)
        stale_stub["qualification_identity"]["stub_sha256"] = "b" * 64
        cases["stale_stub"] = stale_stub
        never_called = deepcopy(failure)
        never_called["original_qualification_evidence"][
            "original_entry_executed"
        ] = False
        cases["original_never_called"] = never_called
        timeout = deepcopy(failure)
        timeout["executions"][-1].update(
            status="timeout", timeout=True, returncode=None
        )
        cases["differential_timeout"] = timeout
        original_timeout = deepcopy(failure)
        original_runtime = next(
            item for item in original_timeout["original_qualification_evidence"][
                "executions"
            ] if item["component"] == "runtime"
        )
        original_runtime.update(status="timeout", timeout=True, returncode=None)
        cases["original_timeout_with_stale_pass_flag"] = original_timeout
        contradictory = deepcopy(failure)
        contradictory["run_returncode"] = 2
        cases["contradictory_runtime_receipt"] = contradictory
        incomplete_compile = deepcopy(failure)
        incomplete_compile["executions"] = [
            item for item in incomplete_compile["executions"]
            if item["component"] != "compile"
        ]
        cases["missing_compile_receipt"] = incomplete_compile

        for name, record in cases.items():
            with self.subTest(name=name):
                result, requests, measure = self.run_loop(failures=[record])
                self.assertFalse(result["qualified"])
                self.assertEqual(measure.call_count, 1)
                self.assertEqual(len(result["rounds"]), 1)
                self.assertEqual([call.kwargs["artifact_kind"]
                                  for call in requests.call_args_list],
                                 ["testbench", "stub"])
                self.assertEqual(result["rounds"][0]["failure_owner"], "unknown")
                self.assertEqual(result["rounds"][0]["next_action"], "review_unknown")

    def test_repeated_differential_failure_shares_budget_and_preserves_history(self):
        failure = self.failure()
        failures = []
        for index in range(1, 5):
            record = deepcopy(failure)
            record["run_stderr"] += f"\nGENERIC_COMPARISON_FAILURE_{index}"
            failures.append(record)
        for K in (1, 6):
            with self.subTest(K=K):
                result, requests, measure = self.run_loop(
                    failures=failures, K=K, max_repairs=3
                )
                self.assertEqual(measure.call_count, 4)
                self.assertEqual(len(result["rounds"]), 4)
                self.assertFalse(result["qualified"])
                self.assertTrue(all(record["failure_owner"] == "unknown"
                                    for record in result["rounds"]))
                self.assertTrue(all(record["next_action"] == "review_unknown"
                                    for record in result["rounds"]))
                self.assertTrue(all(record["tb_code"] == self.TB
                                    for record in result["rounds"]))
                self.assertEqual(
                    [record["ownership_action"] for record in result["rounds"][1:]],
                    ["regenerate_stub", "repair_testbench", "regenerate_stub"],
                )
                self.assertEqual([call.kwargs["artifact_kind"]
                                  for call in requests.call_args_list],
                                 ["testbench", "stub", "stub", "testbench", "stub", "stub"])
                repair_messages = [call.args[1] for call in requests.call_args_list
                                   if call.kwargs["artifact_kind"] == "stub"][1:]
                for number, message in enumerate(repair_messages, 1):
                    for prior in range(1, number + 1):
                        self.assertIn(f"GENERIC_COMPARISON_FAILURE_{prior}", message)

    def test_unknown_differential_can_switch_to_testbench_with_shared_budget(self):
        failure = self.failure()
        failures = []
        for index in range(1, 5):
            record = deepcopy(failure)
            record["run_stderr"] += f"\\nSWITCH_FAILURE_{index}"
            failures.append(record)
        result, _requests, _measure = self.run_loop(
            failures=failures, K=6, max_repairs=3
        )
        self.assertEqual(
            [record["ownership_action"] for record in result["rounds"][1:]],
            ["regenerate_stub", "repair_testbench", "regenerate_stub"],
        )

    def test_zero_repair_budget_stops_after_initial_qualification(self):
        result, requests, measure = self.run_loop(
            failures=[self.failure()], max_repairs=0
        )
        self.assertFalse(result["qualified"])
        self.assertEqual(measure.call_count, 1)
        self.assertEqual([call.kwargs["artifact_kind"]
                          for call in requests.call_args_list],
                         ["testbench", "stub"])

    def test_qualification_retains_original_and_differential_programs(self):
        with tempfile.TemporaryDirectory() as directory:
            result = tb_optimizer._measure_qualified_coverage(
                self.ORIGINAL, self.TB, self.BAD_STUB, "step",
                require_original_execution=True, qualification_dir=directory,
            )
            self.assertEqual(result["status"], "run_failed", result)
            root = Path(directory)
            root = next(root.glob("qualification_*"))
            self.assertIn(self.ORIGINAL, (
                root / "original_only" / "orig_code.cpp"
            ).read_text(encoding="utf-8"))
            self.assertEqual((root / "coverage" / "refactor_code.cpp").read_text(
                encoding="utf-8"
            ), self.BAD_STUB)
            self.assertTrue(result["original_qualification_evidence"][
                "original_entry_executed"
            ])
            for receipt in (result, result["original_qualification_evidence"]):
                for execution in receipt["executions"]:
                    for stream in ("stdout", "stderr"):
                        log = Path(execution[f"{stream}_path"]).read_bytes()
                        self.assertEqual(hashlib.sha256(log).hexdigest(),
                                         execution[f"{stream}_sha256"])
            record = tb_optimizer._append_round(
                [], trajectory_idx=0, round_index=1, tb_code=self.TB,
                stub_code=self.BAD_STUB, cov=result,
            )
            self.assertIn("expected=5 actual=0", tb_optimizer._failure_history([record]))
            not_called = self.TB.replace("int expected = step(value);", "int expected = value + 1;")
            repeated = tb_optimizer._measure_qualified_coverage(
                self.ORIGINAL, not_called, self.BAD_STUB, "step",
                require_original_execution=True, qualification_dir=directory,
            )
            self.assertEqual(repeated["status"], "qualification_failed", repeated)
            self.assertFalse(repeated["original_entry_executed"])
            self.assertEqual(len(list(Path(directory).glob("qualification_*"))), 2)

    def test_original_sanitizer_evidence_before_excerpt_is_not_lost(self):
        real_run = tb_coverage.subprocess.run
        full_stderr = "runtime error: invalid memory access\n" + "context\n" * 1000

        def execute(command, **kwargs):
            if command == ["./original_only"]:
                return SimpleNamespace(returncode=1, stdout="", stderr=full_stderr)
            return real_run(command, **kwargs)

        with tempfile.TemporaryDirectory() as directory, patch.object(
            tb_coverage.subprocess, "run", side_effect=execute
        ):
            result = tb_optimizer._measure_qualified_coverage(
                self.ORIGINAL, self.TB, self.BAD_STUB, "step",
                require_original_execution=True, qualification_dir=directory,
            )
            self.assertEqual(result["status"], "original_run_failed", result)
            self.assertEqual(result["failure_owner"], "unknown")
            runtime = next(item for item in result["executions"]
                           if item["component"] == "runtime")
            self.assertEqual(Path(runtime["stderr_path"]).read_text(), full_stderr)

    def test_noop_definition_preserves_attributed_forward_declarations(self):
        for attributes in ('weak', 'visibility("default"), aligned(16)', 'deprecated("old(interface)")'):
            with self.subTest(attributes=attributes), tempfile.TemporaryDirectory() as directory:
                declaration = f'int step_hls(int value) __attribute__(({attributes}));'
                testbench = self.TB.replace('int step_hls(int value);', declaration)
                result = tb_optimizer._measure_qualified_coverage(
                    self.ORIGINAL, testbench, self.BAD_STUB, 'step',
                    require_original_execution=True, qualification_dir=directory,
                )
                self.assertEqual(result['status'], 'run_failed', result)
                original = result['original_qualification_evidence']
                self.assertEqual(original['status'], 'ok', original)
                self.assertTrue(original['original_entry_executed'])
                compile_receipt = next(item for item in original['executions'] if item['component'] == 'compile')
                root = Path(compile_receipt['work_dir'])
                self.assertEqual((root / 'testbench.cpp').read_text(), testbench)
                self.assertIn('__attribute__', (root / 'original_only_candidate.cpp').read_text())
                if attributes == 'weak':
                    symbols = tb_coverage.subprocess.check_output(['nm', str(root / 'original_only')], text=True)
                    self.assertIn(' W _Z8step_hlsi', symbols)

    def test_noop_definition_retains_type_attributes_and_comment_syntax(self):
        for declaration in (
            'int vec(int value) __attribute__((vector_size(16)));',
            'int __attribute__((vector_size(16))) vec(int value);',
            'int vec(int value) __attribute__ /* documented */ ((vector_size(16)));',
            'extern "C" int vec(int value) __attribute__((vector_size(16)));',
        ):
            with self.subTest(declaration=declaration), tempfile.TemporaryDirectory() as directory:
                definition = tb_coverage._noop_candidate_definition(
                    declaration, '__typeof__(vec(value)) result = {}; return result;', 'vec'
                )
                path = Path(directory) / 'interface.cpp'
                path.write_text(declaration + '\n' + definition)
                compiled = tb_coverage.subprocess.run(['g++', '-fsyntax-only', str(path)], capture_output=True, text=True)
                self.assertEqual(compiled.returncode, 0, compiled.stderr)


if __name__ == "__main__":
    unittest.main()
