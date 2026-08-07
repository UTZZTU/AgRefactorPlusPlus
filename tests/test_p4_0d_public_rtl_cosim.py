from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agrefactor.cli import build_parser
from agrefactor.config import (
    EvaluationSplit,
    RunMode,
    TaskSpec,
    TestSuiteSpec,
    default_target_profile,
)
from agrefactor.evaluation import (
    FeedbackRouteAction,
    ValidationFeedbackCoordinator,
    ValidationState,
)
from agrefactor.evidence import FeedbackOwner
from agrefactor.optimization import (
    BudgetIncrement,
    QualificationStage,
)
from agrefactor.optimization.cache import VALIDATION_PIPELINE_VERSION
from agrefactor.optimization.qualification import (
    QUALIFICATION_PIPELINE_VERSION,
)
from agrefactor.runtime import (
    BudgetLimits,
    BudgetManager,
    RunContext,
    TraceRecorder,
    ValidationOrchestrator,
)
from agrefactor.runtime.cosim_stage import (
    CosimStageInputs,
    CosimValidationStageHandler,
)
from agrefactor.runtime.execution_identity import (
    _HARD_USAGE_FIELDS,
    _suite_identity,
    _toolchain_identity,
)
from flow.tools.vitis_cosim import (
    _cosim_argv_value,
    make_vitis_cosim_tcl,
    run_vitis_cosim,
)


class P4DPublicRtlCosimTests(unittest.TestCase):
    def _task(self) -> TaskSpec:
        profile = default_target_profile()
        return TaskSpec(
            task_id="p4d",
            kernel_path="kernel.cpp",
            kernel_name="kernel",
            target=profile,
            mode=RunMode.REFACTOR,
            test_suites=(
                TestSuiteSpec(
                    suite_id="public-1",
                    split=EvaluationSplit.PUBLIC,
                    testbench_path="public_tb.cpp",
                    runtime_contract={
                        "schema_version": 1,
                        "kind": "public_differential_self_check_v1",
                        "candidate_mismatch_returncodes": [1],
                    },
                ),
                TestSuiteSpec(
                    suite_id="hidden-1",
                    split=EvaluationSplit.HIDDEN,
                    testbench_path="hidden_tb.cpp",
                ),
            ),
        )

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

    def test_budget_charges_probe_and_cosim_separately(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            calls: list[str] = []

            def run_cmd(work_dir: str, command: str, timelimit: int):
                del command, timelimit
                calls.append(work_dir)
                if len(calls) == 1:
                    (root / "toolchain_version.txt").write_text(
                        "2023.2\n",
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
                        (root / "cosim_invocation.json").read_text(encoding="utf-8")
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

            budget = BudgetManager(
                BudgetLimits(max_tool_calls=2, max_cosim_calls=1)
            )
            with patch(
                "flow.tools.vitis_cosim.resolve_csynth_command",
                return_value=self._resolution(),
            ), patch(
                "flow.tools.vitis_cosim.tools.general.run_cmd",
                side_effect=run_cmd,
            ):
                outcome = run_vitis_cosim(
                    work_dir=root,
                    original_code="int reference;",
                    candidate_code="int candidate;",
                    testbench_code="int main(){return 0;}",
                    candidate_top_function="kernel",
                    target_profile=default_target_profile(),
                    timelimit=30,
                    budget=budget,
                )
            usage = budget.snapshot()
            self.assertEqual(outcome["status"], "passed")
            self.assertEqual(usage.tool_calls, 2)
            self.assertEqual(usage.cosim_calls, 1)
            self.assertEqual(len(calls), 2)
            self.assertTrue(outcome["version_probe_launched"])
            self.assertTrue(outcome["cosim_launched"])
            self.assertRegex(outcome["evidence_sha256"], r"^[0-9a-f]{64}$")

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            calls = []

            def command_without_typed_pass(
                work_dir: str, command: str, timelimit: int
            ):
                del command, timelimit
                calls.append(work_dir)
                if len(calls) == 1:
                    (root / "toolchain_version.txt").write_text(
                        "2023.2\n", encoding="utf-8"
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
                side_effect=command_without_typed_pass,
            ):
                outcome = run_vitis_cosim(
                    work_dir=root,
                    original_code="int reference;",
                    candidate_code="int candidate;",
                    testbench_code="int main(){return 0;}",
                    candidate_top_function="kernel",
                    target_profile=default_target_profile(),
                    timelimit=30,
                    budget=BudgetManager(
                        BudgetLimits(max_tool_calls=2, max_cosim_calls=1)
                    ),
                )
            self.assertEqual(outcome["status"], "failed")
            self.assertEqual(
                outcome["reason_code"],
                "cosim_failed_without_typed_owner",
            )

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            calls = []

            def probe_only(work_dir: str, command: str, timelimit: int):
                del command, timelimit
                calls.append(work_dir)
                (root / "toolchain_version.txt").write_text(
                    "2023.2\n",
                    encoding="utf-8",
                )
                return {"returncode": 0, "timeout": False}

            budget = BudgetManager(
                BudgetLimits(max_tool_calls=1, max_cosim_calls=1)
            )
            with patch(
                "flow.tools.vitis_cosim.resolve_csynth_command",
                return_value=self._resolution(),
            ), patch(
                "flow.tools.vitis_cosim.tools.general.run_cmd",
                side_effect=probe_only,
            ):
                outcome = run_vitis_cosim(
                    work_dir=root,
                    original_code="int reference;",
                    candidate_code="int candidate;",
                    testbench_code="int main(){return 0;}",
                    candidate_top_function="kernel",
                    target_profile=default_target_profile(),
                    timelimit=30,
                    budget=budget,
                )
            usage = budget.snapshot()
            self.assertEqual(outcome["failure_kind"], "budget_exhausted")
            self.assertEqual(usage.tool_calls, 1)
            self.assertEqual(usage.cosim_calls, 0)
            self.assertEqual(len(calls), 1)
            self.assertFalse(outcome["cosim_launched"])

    def test_tcl_roles_and_frozen_internal_order(self) -> None:
        profile = default_target_profile()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            files = {
                name: root / f"{name}.cpp"
                for name in ("candidate", "reference", "testbench")
            }
            for path in files.values():
                path.write_text("int value;\n", encoding="utf-8")
            tcl = make_vitis_cosim_tcl(
                root=root,
                top="kernel",
                files=files,
                profile=profile,
            )
        candidate_line = next(
            line for line in tcl.splitlines() if "candidate.cpp" in line
        )
        reference_line = next(
            line for line in tcl.splitlines() if "reference.cpp" in line
        )
        testbench_line = next(
            line for line in tcl.splitlines() if "testbench.cpp" in line
        )
        self.assertNotIn("-tb", candidate_line)
        self.assertIn("-tb", reference_line)
        self.assertIn("-tb", testbench_line)
        outcome_path = root / "agrefactor_cosim_outcome.json"
        self.assertEqual(_cosim_argv_value(outcome_path), str(outcome_path))
        self.assertNotIn("AGREFACTOR_COSIM_OUTCOME_PATH", tcl)
        self.assertIn("set ag_csim_argv [list ", tcl)
        self.assertIn("set ag_cosim_argv [list ", tcl)
        self.assertIn("csim_design -clean -argv $ag_csim_argv", tcl)
        self.assertIn(
            "cosim_design -tool xsim -rtl verilog -argv $ag_cosim_argv",
            tcl,
        )
        self.assertNotIn("-DAGREFACTOR_COSIM_OUTCOME_PATH", reference_line)
        self.assertNotIn("-DAGREFACTOR_COSIM_OUTCOME_PATH", testbench_line)
        with self.assertRaises(ValueError):
            _cosim_argv_value(Path("/unsafe path/outcome.json"))
        self.assertLess(tcl.index("csim_design"), tcl.index("csynth_design"))
        self.assertIn("file delete -force $ag_typed", tcl)
        self.assertLess(tcl.index("csynth_design"), tcl.index("cosim_design"))
        self.assertNotIn("hidden", tcl.casefold())

    def test_handler_requires_exact_public_mapping_and_typed_pass(self) -> None:
        task = self._task()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            context = RunContext(
                run_id="p4d-pass",
                task=task,
                budget=BudgetManager(),
                trace=TraceRecorder("p4d-pass", task_id=task.task_id),
            )
            handler = CosimValidationStageHandler(
                CosimStageInputs(
                    work_dir=root,
                    original_code="int reference;",
                    candidate_code="int candidate;",
                    suite_testbench_codes={
                        "public-1": "int main(){return 0;}"
                    },
                    candidate_top_function="kernel",
                    target_profile=task.target,
                    timelimit=30,
                ),
                executor=lambda **_: {
                    "schema_version": 1,
                    "status": "passed",
                    "failure_kind": None,
                    "failure_owner": "none",
                    "reason_code": "cosim_passed",
                    "timed_out": False,
                    "returncode": 0,
                    "tool_launched": True,
                    "cosim_launched": True,
                    "evidence_sha256": "a" * 64,
                },
            )
            report = handler(context)
            self.assertFalse(report.blocking)
            identity = (
                root
                / "suite_001"
                / "cosim_suite_identity_evidence.json"
            )
            self.assertTrue(identity.is_file())
            payload = json.loads(identity.read_text(encoding="utf-8"))
            self.assertEqual(payload["suite_id"], "public-1")
            self.assertEqual(payload["evaluation_status"], "passed")
            self.assertFalse(payload["hidden_evidence_exposed"])

            fail_closed = CosimValidationStageHandler(
                handler.inputs,
                executor=lambda **_: {
                    "status": "passed",
                    "returncode": 0,
                    "tool_launched": True,
                    "cosim_launched": True,
                },
            )(context)
            self.assertTrue(fail_closed.blocking)
            self.assertEqual(
                fail_closed.items[0].owner,
                FeedbackOwner.UNKNOWN,
            )

            with self.assertRaisesRegex(ValueError, "suite mapping mismatch"):
                CosimValidationStageHandler(
                    CosimStageInputs(
                        work_dir=root / "extra",
                        original_code="int reference;",
                        candidate_code="int candidate;",
                        suite_testbench_codes={
                            "public-1": "int main(){return 0;}",
                            "hidden-1": "int main(){return 0;}",
                        },
                        candidate_top_function="kernel",
                        target_profile=task.target,
                        timelimit=30,
                    ),
                    executor=lambda **_: {},
                )(context)

    def test_candidate_cosim_failure_enters_bounded_repair(self) -> None:
        task = self._task()
        with tempfile.TemporaryDirectory() as raw:
            report = CosimValidationStageHandler(
                CosimStageInputs(
                    work_dir=raw,
                    original_code="int reference;",
                    candidate_code="int candidate;",
                    suite_testbench_codes={
                        "public-1": "int main(){return 1;}"
                    },
                    candidate_top_function="kernel",
                    target_profile=task.target,
                    timelimit=30,
                ),
                executor=lambda **_: {
                    "status": "failed",
                    "failure_kind": "candidate_rtl_functional_failure",
                    "failure_owner": "candidate",
                    "owner_authority": "deterministic_proven",
                    "testbench_returncode": 1,
                    "reason_code": "public_rtl_mismatch",
                    "timed_out": False,
                    "returncode": 23,
                    "tool_launched": True,
                    "cosim_launched": True,
                    "evidence_sha256": "b" * 64,
                },
            )(
                RunContext(
                    run_id="p4d-recovery",
                    task=task,
                    budget=BudgetManager(),
                    trace=TraceRecorder("p4d-recovery", task_id=task.task_id),
                )
            )
        coordinated = ValidationFeedbackCoordinator(task).coordinate(
            report,
            ValidationState.PUBLIC_COSIM,
            coordination_id="p4d-recovery.cosim",
        )
        self.assertEqual(
            coordinated.route_action,
            FeedbackRouteAction.REPAIR_CANDIDATE,
        )
        self.assertEqual(
            coordinated.transition.next_state,
            ValidationState.REPAIR_PENDING,
        )
        self.assertTrue(coordinated.transition.repair_allowed)
        self.assertTrue(coordinated.transition.agent_feedback_allowed)
        self.assertEqual(
            coordinated.transition.resume_state,
            ValidationState.PUBLIC_COSIM,
        )
        self.assertEqual(len(coordinated.selected_feedback_items), 1)
        self.assertEqual(
            coordinated.selected_feedback_items[0].owner,
            FeedbackOwner.CANDIDATE,
        )

    def test_untrusted_owner_pair_and_timeout_are_unknown_safe(self) -> None:
        task = self._task()
        context = RunContext(
            run_id="p4d-unknown",
            task=task,
            budget=BudgetManager(),
            trace=TraceRecorder("p4d-unknown", task_id=task.task_id),
        )
        with tempfile.TemporaryDirectory() as raw:
            handler = CosimValidationStageHandler(
                CosimStageInputs(
                    work_dir=raw,
                    original_code="int reference;",
                    candidate_code="int candidate;",
                    suite_testbench_codes={
                        "public-1": "int main(){return 1;}"
                    },
                    candidate_top_function="kernel",
                    target_profile=task.target,
                    timelimit=30,
                ),
                executor=lambda **_: {
                    "status": "failed",
                    "failure_kind": "candidate_rtl_functional_failure",
                    "failure_owner": "testbench",
                    "reason_code": "untrusted_pair",
                    "timed_out": False,
                    "returncode": 1,
                    "tool_launched": True,
                    "cosim_launched": True,
                    "evidence_sha256": "c" * 64,
                },
            )
            report = handler(context)
        self.assertEqual(report.items[0].owner, FeedbackOwner.UNKNOWN)
        coordinated = ValidationFeedbackCoordinator(task).coordinate(
            report,
            ValidationState.PUBLIC_COSIM,
            coordination_id="p4d-unknown.cosim",
        )
        self.assertEqual(
            coordinated.route_action,
            FeedbackRouteAction.REVIEW_UNKNOWN,
        )
        self.assertEqual(
            coordinated.transition.next_state,
            ValidationState.REVIEW_REQUIRED,
        )

    def test_frozen_order_cli_pipeline_and_budget_increment(self) -> None:
        task = self._task()
        context = RunContext(
            run_id="p4d-order",
            task=task,
            budget=BudgetManager(),
            trace=TraceRecorder("p4d-order", task_id=task.task_id),
        )
        self.assertEqual(
            ValidationOrchestrator._required_states(context),
            (
                ValidationState.PREFLIGHT,
                ValidationState.PUBLIC_EVALUATION,
                ValidationState.CSYNTH,
                ValidationState.PUBLIC_COSIM,
                ValidationState.HIDDEN_EVALUATION,
            ),
        )
        self.assertEqual(QualificationStage.PUBLIC_COSIM.value, "public_cosim")
        self.assertEqual(
            VALIDATION_PIPELINE_VERSION,
            "prestage4-public-rtl-cosim-v1",
        )
        self.assertEqual(
            QUALIFICATION_PIPELINE_VERSION,
            "prestage4-public-rtl-cosim-v1",
        )
        self.assertNotIn("cosim_calls", BudgetIncrement().to_kwargs())
        self.assertEqual(
            BudgetIncrement(cosim_calls=2).to_kwargs()["cosim_calls"],
            2,
        )
        args = build_parser().parse_args(
            [
                "refactor",
                "kernel.cpp",
                "--top",
                "kernel",
                "--model",
                "deepseek-chat",
                "--max-cosim-calls",
                "3",
                "--cosim-timeout-s",
                "123",
                "--cosim-policy",
                "off",
            ]
        )
        self.assertEqual(args.max_cosim_calls, 3)
        self.assertEqual(args.cosim_timeout_s, 123)
        self.assertEqual(args.cosim_policy, "off")

    def test_execution_identity_carries_cosim_budget_and_evidence(self) -> None:
        self.assertEqual(_HARD_USAGE_FIELDS["max_cosim_calls"], "cosim_calls")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            testbench = root / "public_tb.cpp"
            testbench.write_text("int main(){return 0;}\n", encoding="utf-8")
            digest = sha256(testbench.read_bytes()).hexdigest()
            manifest = {
                "suite_id": "public-1",
                "suite_version": "1",
                "split": "public",
                "testbench_path": str(testbench),
                "source": {
                    "source_id": "provided-public",
                    "source_kind": "provided",
                    "expected_content_sha256": digest,
                },
                "evaluation_status": "passed",
                "public_rtl_cosim_required": True,
                "public_rtl_cosim_status": "passed",
                "public_rtl_cosim_evidence_sha256": "d" * 64,
            }
            identity = _suite_identity(manifest)
            self.assertTrue(identity["public_rtl_cosim_required"])
            self.assertEqual(identity["public_rtl_cosim_status"], "passed")
            self.assertEqual(
                identity["public_rtl_cosim_evidence_sha256"],
                "d" * 64,
            )
            broken = dict(manifest)
            broken["public_rtl_cosim_evidence_sha256"] = None
            with self.assertRaisesRegex(ValueError, "without evidence hash"):
                _suite_identity(broken)

            invocation_dir = root / "work" / "suite_001"
            invocation_dir.mkdir(parents=True)
            (invocation_dir / "cosim_invocation.json").write_text(
                json.dumps(
                    {
                        "phase": "public_rtl_cosim",
                        "profile_name": "vitis-2023.2-default",
                        "requested_toolchain_version": "2023.2",
                        "toolchain_version_verification": {
                            "status": "matched",
                            "requested": "2023.2",
                            "actual": "2023.2",
                            "evidence_sha256": "e" * 64,
                        },
                        "resolved_executable": None,
                        "resolved_settings_path": None,
                        "command_source": "test",
                        "probe_source": "test",
                        "effective_value_provenance": {},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            toolchain = _toolchain_identity(
                default_target_profile().to_effective_dict(),
                root / "work",
            )
            self.assertTrue(toolchain["actual_version_recorded"])
            self.assertEqual(
                toolchain["invocations"][0]["phase"],
                "public_rtl_cosim",
            )
            self.assertEqual(
                toolchain["invocations"][0]["version_output_sha256"],
                "e" * 64,
            )


if __name__ == "__main__":
    unittest.main()
