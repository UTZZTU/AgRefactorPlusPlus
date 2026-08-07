from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agrefactor.config import default_target_profile
from agrefactor.product.run_output import (
    _failed_stage,
    _public_cosim_evidence_present,
    _public_cosim_status,
    _suite_status,
)
from flow.tools.vitis_cosim import (
    _build_typed_outcome_adapter,
    make_vitis_cosim_tcl,
    run_vitis_cosim,
)
from flow.tools.typed_testbench_outcome import make_typed_outcome_identity


class P40DR1CosimContractTests(unittest.TestCase):
    @staticmethod
    def _resolution() -> dict[str, object]:
        return {
            "command": "vitis-run --mode hls --tcl --input_file vitis.tcl",
            "command_source": "test",
            "executable": "vitis-run",
            "resolved_executable": "/opt/vitis/vitis-run",
            "settings_path": None,
            "resolved_settings_path": None,
            "probe_source": "test",
            "profile_name": "vitis-2023.2-default",
            "effective_value_provenance": {},
        }

    @staticmethod
    def _identity(testbench: str) -> dict[str, str]:
        return make_typed_outcome_identity(
            phase="csim_prerequisite",
            suite_id="public",
            candidate_code="void kernel_hls(){}",
            testbench_code=testbench,
            execution_id="1" * 32,
        )

    def test_plain_main_gets_deterministic_pass_adapter(self) -> None:
        testbench = (
            "int helper(){return 0;}\n"
            "int main(){ return helper(); }\n"
        )
        instrumented, wrapper, evidence = _build_typed_outcome_adapter(
            testbench,
            base_identity=self._identity(testbench),
        )
        self.assertIn(
            "int agrefactor_public_testbench_main()",
            instrumented,
        )
        self.assertNotIn("int main()", instrumented)
        self.assertIn(
            "const int testbench_status = "
            "agrefactor_public_testbench_main();",
            wrapper,
        )
        self.assertIn(
            "const int evidence_status = agrefactor_write_outcome",
            wrapper,
        )
        self.assertLess(
            wrapper.index("const int evidence_status"),
            wrapper.index("return testbench_status"),
        )
        self.assertIn('\\"schema_version\\":2', wrapper)
        self.assertNotIn("failure_owner", wrapper)
        self.assertEqual(evidence["schema_version"], 2)
        self.assertEqual(evidence["main_contract"], "no_args")
        self.assertTrue(evidence["records_only_raw_returncode"])
        self.assertTrue(evidence["atomic_replace"])

    def test_argc_argv_main_is_supported_without_hidden_input(self) -> None:
        testbench = (
            "int main(int argc, char **argv) { "
            "return argc > 0 && argv ? 0 : 1; }\n"
        )
        instrumented, wrapper, evidence = _build_typed_outcome_adapter(
            testbench,
            base_identity=self._identity(testbench),
        )
        self.assertIn(
            "agrefactor_public_testbench_main(int argc, char **argv)",
            instrumented,
        )
        self.assertIn(
            "agrefactor_public_testbench_main(argc, argv)",
            wrapper,
        )
        combined = (
            instrumented.casefold()
            + wrapper.casefold()
            + json.dumps(evidence).casefold()
        )
        for forbidden in (
            "hidden_testbench",
            "hidden_source",
            "hidden_code",
            "generated_hidden",
        ):
            self.assertNotIn(forbidden, combined)
        self.assertEqual(evidence["hidden_input_count"], 0)
        self.assertEqual(evidence["main_contract"], "argc_argv")

    def test_unsupported_or_ambiguous_main_fails_closed(self) -> None:
        for testbench in (
            "int helper(){return 0;}",
            "int main(double x){return 0;}",
            "int main(){return 0;}\n"
            "int main(int,char**){return 0;}",
        ):
            with self.subTest(testbench=testbench):
                with self.assertRaises(ValueError):
                    _build_typed_outcome_adapter(
                        testbench,
                        base_identity=self._identity(testbench),
                    )

    def test_tcl_compiles_instrumented_testbench_and_wrapper(self) -> None:
        profile = default_target_profile()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            files = {
                name: root / f"{name}.cpp"
                for name in (
                    "candidate",
                    "reference",
                    "testbench_original",
                    "testbench",
                    "wrapper",
                )
            }
            tcl = make_vitis_cosim_tcl(
                root=root,
                top="kernel_hls",
                files=files,
                profile=profile,
            )
        self.assertNotIn(str(files["testbench_original"]), tcl)
        self.assertIn(str(files["testbench"]), tcl)
        self.assertIn(str(files["wrapper"]), tcl)
        self.assertLess(
            tcl.index(str(files["testbench"])),
            tcl.index(str(files["wrapper"])),
        )
        self.assertLess(tcl.index("csim_design"), tcl.index("csynth_design"))
        self.assertLess(tcl.index("csynth_design"), tcl.index("cosim_design"))
        self.assertIn("file delete -force $ag_typed", tcl)
        self.assertNotIn("hidden", tcl.casefold())

    def test_existing_three_role_tcl_helper_contract_is_preserved(self) -> None:
        profile = default_target_profile()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            files = {
                name: root / f"{name}.cpp"
                for name in ("candidate", "reference", "testbench")
            }
            tcl = make_vitis_cosim_tcl(
                root=root,
                top="kernel_hls",
                files=files,
                profile=profile,
            )
        self.assertIn(str(files["testbench"]), tcl)
        self.assertNotIn("agrefactor_cosim_wrapper.cpp", tcl)
        self.assertIn("cosim_design", tcl)

    def test_real_runner_accepts_only_three_way_typed_pass(self) -> None:
        profile = default_target_profile()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            calls: list[str] = []

            def fake(work_dir: str, command: str, timelimit: int):
                del command, timelimit
                calls.append(work_dir)
                if len(calls) == 1:
                    (root / "toolchain_version.txt").write_text(
                        (profile.toolchain_version or "2023.2") + "\n",
                        encoding="utf-8",
                    )
                else:
                    (root / "cosim_command_status.json").write_text(
                        json.dumps(
                            {
                                "schema_version": 1,
                                "status": "passed",
                                "phase": "cosim",
                                "reason_code": "cosim_passed",
                            }
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    identity = json.loads(
                        (root / "cosim_invocation.json").read_text(
                            encoding="utf-8"
                        )
                    )["typed_outcome_identities"]["cosim"]
                    (root / "agrefactor_cosim_outcome.json").write_text(
                        json.dumps(
                            {
                                "schema_version": 2,
                                **identity,
                                "status": "passed",
                                "testbench_returncode": 0,
                            }
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                return {"returncode": 0, "timeout": False}

            with patch(
                "flow.tools.vitis_cosim.resolve_csynth_command",
                return_value=self._resolution(),
            ), patch(
                "flow.tools.vitis_cosim.tools.general.run_cmd",
                side_effect=fake,
            ):
                result = run_vitis_cosim(
                    work_dir=root,
                    original_code="void ref(){}",
                    candidate_code="void kernel_hls(){}",
                    testbench_code="int main(){return 0;}",
                    candidate_top_function="kernel_hls",
                    target_profile=profile,
                    timelimit=30,
                )
            self.assertEqual(result["status"], "passed")
            invocation = json.loads(
                (root / "cosim_invocation.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                invocation["outcome_transport"],
                "testbench_argv",
            )
            self.assertEqual(
                invocation["typed_outcome_adapter"]["kind"],
                "raw_runtime_atomic_wrapper_v2",
            )
            adapter = json.loads(
                (root / "typed_outcome_adapter.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(adapter["records_only_raw_returncode"])
            self.assertTrue(adapter["atomic_replace"])
            self.assertEqual(
                adapter["argv_contract"],
                ["outcome_path", "execution_id", "phase"],
            )

    def test_zero_return_without_typed_outcome_still_fails_closed(self) -> None:
        profile = default_target_profile()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            calls: list[str] = []

            def fake(work_dir: str, command: str, timelimit: int):
                del command, timelimit
                calls.append(work_dir)
                if len(calls) == 1:
                    (root / "toolchain_version.txt").write_text(
                        (profile.toolchain_version or "2023.2") + "\n",
                        encoding="utf-8",
                    )
                else:
                    (root / "cosim_command_status.json").write_text(
                        json.dumps(
                            {
                                "schema_version": 1,
                                "status": "passed",
                                "phase": "cosim",
                                "reason_code": "cosim_passed",
                            }
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                return {"returncode": 0, "timeout": False}

            with patch(
                "flow.tools.vitis_cosim.resolve_csynth_command",
                return_value=self._resolution(),
            ), patch(
                "flow.tools.vitis_cosim.tools.general.run_cmd",
                side_effect=fake,
            ):
                result = run_vitis_cosim(
                    work_dir=root,
                    original_code="void ref(){}",
                    candidate_code="void kernel_hls(){}",
                    testbench_code="int main(){return 0;}",
                    candidate_top_function="kernel_hls",
                    target_profile=profile,
                    timelimit=30,
                )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(
                result["reason_code"],
                "cosim_failed_without_typed_owner",
            )
            self.assertEqual(result["failure_owner"], "unknown")

    def test_product_summary_distinguishes_cosim_and_hidden_not_run(self) -> None:
        suites = [
            {
                "split": "public",
                "evaluation_status": "passed",
                "public_rtl_cosim_required": True,
                "public_rtl_cosim_status": "failed",
            },
            {
                "split": "hidden",
                "evaluation_status": None,
                "public_rtl_cosim_required": False,
                "public_rtl_cosim_status": None,
            },
        ]
        self.assertEqual(_suite_status(suites, "public"), "passed")
        self.assertEqual(_suite_status(suites, "hidden"), "not_run")
        self.assertEqual(_public_cosim_status(suites), "failed")
        self.assertEqual(
            _failed_stage(
                {"suites": suites},
                {"last_validation_state": "review_required"},
            ),
            "public_cosim",
        )

    def test_public_cosim_field_is_evidence_conditional(self) -> None:
        optimize_suites = [
            {
                "split": "public",
                "evaluation_status": "passed",
            },
            {
                "split": "hidden",
                "evaluation_status": "passed",
            },
        ]
        self.assertFalse(
            _public_cosim_evidence_present(optimize_suites)
        )
        cosim_suites = [
            {
                "split": "public",
                "evaluation_status": "passed",
                "public_rtl_cosim_required": True,
                "public_rtl_cosim_status": "passed",
            }
        ]
        self.assertTrue(
            _public_cosim_evidence_present(cosim_suites)
        )
        self.assertEqual(
            _public_cosim_status(cosim_suites),
            "passed",
        )



if __name__ == "__main__":
    unittest.main()
