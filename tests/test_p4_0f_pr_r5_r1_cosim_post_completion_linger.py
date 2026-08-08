from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agrefactor.config import (
    EvaluationSplit,
    RunMode,
    TaskSpec,
    TestSuiteSpec,
    default_target_profile,
)
from agrefactor.runtime import (
    BudgetLimits,
    BudgetManager,
    RunContext,
    TraceRecorder,
)
from agrefactor.runtime.cosim_stage import (
    CosimStageInputs,
    CosimValidationStageHandler,
)
from flow.tools.vitis_cosim import run_vitis_cosim


SPECIAL_REASON = "cosim_passed_post_completion_process_linger"
COMPLETION_AUTHORITY = (
    "fresh_tcl_status_and_identity_bound_typed_outcome_v1"
)


class P4FPRR5R1CosimPostCompletionLingerTests(unittest.TestCase):
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

    def _low_level(
        self,
        *,
        command_status: str | None = "passed",
        typed_status: str | None = "passed",
        typed_wrong_identity: bool = False,
        timed_out: bool = True,
    ):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        calls = []

        def run_cmd(work_dir: str, command: str, timelimit: int):
            del work_dir, command, timelimit
            calls.append(len(calls) + 1)
            if len(calls) == 1:
                (root / "toolchain_version.txt").write_text(
                    "2023.2\n",
                    encoding="utf-8",
                )
                return {"returncode": 0, "timeout": False}

            if command_status is not None:
                (root / "cosim_command_status.json").write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "status": command_status,
                            "phase": "cosim",
                            "reason_code": (
                                "cosim_passed"
                                if command_status == "passed"
                                else "cosim_command_failed"
                            ),
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )

            if typed_status is not None:
                invocation = json.loads(
                    (root / "cosim_invocation.json").read_text(
                        encoding="utf-8"
                    )
                )
                identity = dict(
                    invocation["typed_outcome_identities"]["cosim"]
                )
                if typed_wrong_identity:
                    identity["candidate_sha256"] = "0" * 64
                (root / "agrefactor_cosim_outcome.json").write_text(
                    json.dumps(
                        {
                            "schema_version": 2,
                            **identity,
                            "status": typed_status,
                            "testbench_returncode": (
                                0 if typed_status == "passed" else 1
                            ),
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
            return {
                "returncode": None if timed_out else 0,
                "timeout": timed_out,
            }

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
                budget=BudgetManager(
                    BudgetLimits(max_tool_calls=2, max_cosim_calls=1)
                ),
                suite_id="public-1",
                runtime_contract={
                    "schema_version": 1,
                    "kind": "public_differential_self_check_v1",
                    "candidate_mismatch_returncodes": [1],
                },
            )
        invocation = json.loads(
            (root / "cosim_invocation.json").read_text(encoding="utf-8")
        )
        return outcome, invocation

    def _task(self) -> TaskSpec:
        return TaskSpec(
            task_id="pr-r5-r1",
            kernel_path="kernel.cpp",
            kernel_name="kernel",
            target=default_target_profile(),
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
            ),
        )

    def _handler_result(self, payload):
        task=self._task()
        with tempfile.TemporaryDirectory() as raw:
            context=RunContext(
                run_id="pr-r5-r1",
                task=task,
                budget=BudgetManager(),
                trace=TraceRecorder("pr-r5-r1",task_id=task.task_id),
            )
            report=CosimValidationStageHandler(
                CosimStageInputs(
                    work_dir=raw,
                    original_code="int reference;",
                    candidate_code="int candidate;",
                    suite_testbench_codes={
                        "public-1":"int main(){return 0;}"
                    },
                    candidate_top_function="kernel",
                    target_profile=task.target,
                    timelimit=30,
                ),
                executor=lambda **_: dict(payload),
            )(context)
            identity=json.loads(
                (
                    Path(raw)
                    / "suite_001"
                    / "cosim_suite_identity_evidence.json"
                ).read_text(encoding="utf-8")
            )
        return report,identity

    def _special_payload(self):
        return {
            "schema_version": 1,
            "status": "passed",
            "failure_kind": None,
            "failure_owner": "none",
            "reason_code": SPECIAL_REASON,
            "timed_out": True,
            "returncode": None,
            "tool_launched": True,
            "version_probe_launched": True,
            "cosim_launched": True,
            "evidence_sha256": "a" * 64,
            "post_completion_process_linger": True,
            "command_completion_proven": True,
            "process_exit_observed": False,
            "completion_authority": COMPLETION_AUTHORITY,
        }

    def test_low_level_timeout_with_fresh_typed_completion_is_special_pass(self):
        outcome,invocation=self._low_level()
        self.assertEqual(outcome["status"],"passed")
        self.assertEqual(outcome["reason_code"],SPECIAL_REASON)
        self.assertTrue(outcome["timed_out"])
        self.assertIsNone(outcome["returncode"])
        self.assertTrue(outcome["post_completion_process_linger"])
        self.assertTrue(outcome["command_completion_proven"])
        self.assertFalse(outcome["process_exit_observed"])
        self.assertEqual(
            outcome["completion_authority"],
            COMPLETION_AUTHORITY,
        )
        self.assertEqual(
            invocation["result_summary"]["reason_code"],
            SPECIAL_REASON,
        )

    def test_low_level_timeout_missing_command_status_stays_timeout(self):
        outcome,_=self._low_level(command_status=None)
        self.assertEqual(outcome["status"],"failed")
        self.assertEqual(outcome["reason_code"],"cosim_timeout")
        self.assertTrue(outcome["timed_out"])

    def test_low_level_timeout_missing_typed_outcome_stays_timeout(self):
        outcome,_=self._low_level(typed_status=None)
        self.assertEqual(outcome["status"],"failed")
        self.assertEqual(outcome["reason_code"],"cosim_timeout")

    def test_low_level_timeout_wrong_typed_identity_stays_timeout(self):
        outcome,_=self._low_level(typed_wrong_identity=True)
        self.assertEqual(outcome["status"],"failed")
        self.assertEqual(outcome["reason_code"],"cosim_timeout")

    def test_low_level_timeout_typed_failure_stays_timeout(self):
        outcome,_=self._low_level(typed_status="failed")
        self.assertEqual(outcome["status"],"failed")
        self.assertEqual(outcome["reason_code"],"cosim_timeout")

    def test_low_level_timeout_failed_command_status_stays_timeout(self):
        outcome,_=self._low_level(command_status="failed")
        self.assertEqual(outcome["status"],"failed")
        self.assertEqual(outcome["reason_code"],"cosim_timeout")

    def test_handler_accepts_only_complete_post_completion_authority(self):
        report,identity=self._handler_result(self._special_payload())
        self.assertFalse(report.blocking)
        self.assertEqual(identity["evaluation_status"],"passed")
        self.assertEqual(identity["reason_code"],SPECIAL_REASON)
        self.assertTrue(identity["post_completion_process_linger"])
        self.assertTrue(identity["command_completion_proven"])
        self.assertFalse(identity["process_exit_observed"])
        self.assertEqual(
            identity["completion_authority"],
            COMPLETION_AUTHORITY,
        )

    def test_handler_missing_completion_marker_fails_closed(self):
        payload=self._special_payload()
        del payload["command_completion_proven"]
        report,_=self._handler_result(payload)
        self.assertTrue(report.blocking)
        self.assertEqual(
            report.items[0].summary,
            "cosim_pass_missing_typed_execution_evidence",
        )

    def test_handler_missing_evidence_hash_fails_closed(self):
        payload=self._special_payload()
        payload["evidence_sha256"]=None
        report,_=self._handler_result(payload)
        self.assertTrue(report.blocking)
        self.assertEqual(
            report.items[0].summary,
            "cosim_pass_missing_typed_execution_evidence",
        )

    def test_normal_process_exit_pass_semantics_are_unchanged(self):
        payload={
            "status":"passed",
            "failure_kind":None,
            "failure_owner":"none",
            "reason_code":"cosim_passed",
            "timed_out":False,
            "returncode":0,
            "tool_launched":True,
            "cosim_launched":True,
            "evidence_sha256":"b"*64,
        }
        report,identity=self._handler_result(payload)
        self.assertFalse(report.blocking)
        self.assertEqual(identity["reason_code"],"cosim_passed")
        self.assertFalse(identity["post_completion_process_linger"])
        self.assertFalse(identity["command_completion_proven"])
        self.assertTrue(identity["process_exit_observed"])


if __name__=="__main__":
    unittest.main()
