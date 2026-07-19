from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agrefactor.config import TaskSpec
from agrefactor.evaluation import ValidationState
from agrefactor.evidence import (
    TestbenchFailureKind,
    TestbenchFailureOwner,
    TestbenchPreflightResult,
    TestbenchPreflightStatus,
    TestbenchStage,
)
from agrefactor.models import (
    ModelProvider,
    ModelRegistry,
    ModelResponse,
    ModelSpec,
    TokenUsage,
)
from agrefactor.repair import (
    CandidateRepairAttempt,
    CandidateRepairAttemptStatus,
    CandidateRepairPayload,
    CandidateRepairLoopResult,
    CandidateRepairStopReason,
    CandidateValidationResult,
    RepairArtifactRole,
    RepairArtifactWriter,
    RepairAttemptRecord,
    RepairModelObservation,
    RepairObservedUsage,
    RepairRunRecord,
    RepairTerminalStatus,
    TestbenchRepairPayload,
    repair_attempt_id,
    repair_proposal_id,
)
from agrefactor.runtime import BudgetManager, BudgetUsage
from agrefactor.testing import (
    ModelTestbenchRepairer,
    TestbenchRepairLoop,
    TestbenchRepairRequest,
)
from agrefactor.testing.testbench_repair import (
    TestbenchRepairAttempt,
    TestbenchRepairStatus,
)


def usage(
    *,
    llm=0,
    tool=0,
    compile_calls=0,
    csynth=0,
    csim=0,
    tokens=0,
    cost=0.0,
    elapsed=0.0,
):
    return BudgetUsage(
        llm_calls=llm,
        tool_calls=tool,
        compile_calls=compile_calls,
        csim_calls=csim,
        csynth_calls=csynth,
        tokens=tokens,
        cost_usd=cost,
        elapsed_s=elapsed,
    )


def response(text="```cpp\nint main(){return 0;}\n```"):
    return ModelResponse(
        text=text,
        model="fake-model",
        usage=TokenUsage(
            prompt_tokens=7,
            completion_tokens=5,
            cost_usd=0.02,
        ),
        finish_reason="stop",
        metadata={"request_id": "safe-id"},
    )


def observation(with_response=True):
    return RepairModelObservation.from_response(
        prompt_manifest={
            "purpose": "testbench_repair",
            "editable_artifacts": ["testbench"],
        },
        response=response() if with_response else None,
        model_call_observed=True,
    )


def attempt_record(
    *,
    role=RepairArtifactRole.CANDIDATE,
    index=1,
):
    attempt_id = repair_attempt_id("run-one", index)
    if role is RepairArtifactRole.CANDIDATE:
        payload = CandidateRepairPayload(
            validation_summary={"passed": True},
            model_result_available=True,
        )
    else:
        payload = TestbenchRepairPayload(
            preflight_summary={
                "status": "passed",
                "stage": "compile_link",
            },
            legacy_preflight_artifact_available=True,
        )
    return RepairAttemptRecord(
        attempt_id=attempt_id,
        proposal_id=repair_proposal_id(attempt_id),
        artifact_role=role,
        sequence_index=index,
        action="validated",
        status="validated",
        changed=True,
        model_observation=observation(),
        observed_usage=RepairObservedUsage(
            llm_calls=1,
            tokens=12,
            cost_usd=0.02,
        ),
        payload=payload,
        terminal_status=RepairTerminalStatus.SUCCEEDED,
        metadata={"safe": True},
    )


def run_record(
    *,
    role=RepairArtifactRole.CANDIDATE,
):
    return RepairRunRecord(
        run_id="run-one",
        artifact_role=role,
        terminal_status=RepairTerminalStatus.SUCCEEDED,
        stop_reason="validated",
        attempts=(
            attempt_record(role=role),
        ),
        metadata={"attempt_count": 1},
    )


def failed_preflight():
    return TestbenchPreflightResult(
        status=TestbenchPreflightStatus.FAILED,
        stage=TestbenchStage.COMPILE_LINK,
        failure_kind=TestbenchFailureKind.UNDECLARED_TYPE,
        failure_owner=TestbenchFailureOwner.TESTBENCH,
        return_code=1,
        command=("g++", "/secret/test.cpp"),
        stdout="",
        stderr="/secret/test.cpp: error",
        artifacts=("/secret/test.o",),
    )


def passed_preflight():
    return TestbenchPreflightResult(
        status=TestbenchPreflightStatus.PASSED,
        stage=TestbenchStage.COMPILE_LINK,
        failure_kind=TestbenchFailureKind.NONE,
        failure_owner=TestbenchFailureOwner.NONE,
        return_code=0,
        command=("g++", "/secret/test.cpp"),
        stdout="passed",
        stderr="",
        artifacts=("/secret/test.exe",),
    )


class SequencePreflight:
    def __init__(self, values):
        self.values = list(values)

    def compile_and_link(self, **kwargs):
        budget = kwargs.get("budget")
        if budget is not None:
            budget.consume(
                tool_calls=1,
                compile_calls=1,
            )
        return self.values.pop(0)


class FakeProvider(ModelProvider):
    def __init__(self, value):
        self.value = value

    @property
    def name(self):
        return "fake"

    def generate(self, model, request):
        if isinstance(self.value, BaseException):
            raise self.value
        return self.value


def model_repairer(value):
    registry = ModelRegistry()
    registry.register_provider(
        FakeProvider(value)
    )
    registry.register_model(
        ModelSpec(
            name="repair-model",
            provider="fake",
            model="fake-model",
        )
    )
    return ModelTestbenchRepairer(
        registry=registry,
        model_name="repair-model",
    )


CURRENT_TB = (
    'extern "C" int candidate_top(int);\n'
    "int main(){return candidate_top(1)==1?0:1;}\n"
)
REPAIRED_TB = (
    'extern "C" int candidate_top(int);\n'
    "int main(){return candidate_top(1)==1?0:1;}\n"
    "// repaired declaration context\n"
)
TASK = TaskSpec(
    task_id="repair-protocol-task",
    kernel_path="candidate.cpp",
    kernel_name="candidate_top",
)


class RepairProtocolTests(unittest.TestCase):
    def test_role_values_are_generic(self):
        self.assertEqual(
            [item.value for item in RepairArtifactRole],
            ["candidate", "testbench"],
        )

    def test_terminal_values_are_generic(self):
        self.assertIn(
            "blocked",
            [item.value for item in RepairTerminalStatus],
        )

    def test_attempt_id_is_stable(self):
        self.assertEqual(
            repair_attempt_id("run-one", 3),
            "run-one.attempt-003",
        )

    def test_attempt_id_rejects_bad_run_id(self):
        with self.assertRaises(ValueError):
            repair_attempt_id("../secret", 1)

    def test_proposal_id_is_stable(self):
        self.assertEqual(
            repair_proposal_id("run-one.attempt-001"),
            "run-one.attempt-001.proposal",
        )

    def test_model_observation_serializes_response(self):
        payload = observation().to_safe_dict()
        self.assertEqual(
            payload["model_response"]["usage"]["total_tokens"],
            12,
        )

    def test_model_observation_rejects_unobserved_response(self):
        with self.assertRaises(ValueError):
            RepairModelObservation(
                model_response={"text": "x"},
                model_call_observed=False,
            )

    def test_usage_projects_budget_delta_without_elapsed(self):
        projected = RepairObservedUsage.from_observations(
            usage(elapsed=100),
            usage(
                tool=2,
                compile_calls=1,
                elapsed=500,
            ),
        )
        self.assertEqual(projected.tool_calls, 2)
        self.assertEqual(projected.compile_calls, 1)
        self.assertNotIn(
            "elapsed_s",
            projected.to_dict(),
        )

    def test_usage_uses_model_observation_when_budget_lacks_llm(self):
        projected = RepairObservedUsage.from_observations(
            usage(),
            usage(compile_calls=1, tool=1),
            observation(),
        )
        self.assertEqual(projected.llm_calls, 1)
        self.assertEqual(projected.tokens, 12)
        self.assertEqual(projected.cost_usd, 0.02)

    def test_usage_rejects_negative_delta(self):
        with self.assertRaises(ValueError):
            RepairObservedUsage.from_observations(
                usage(tool=1),
                usage(),
            )

    def test_attempt_record_is_json_serializable(self):
        encoded = json.dumps(
            attempt_record().to_safe_dict(),
            sort_keys=True,
        )
        self.assertIn("prompt_manifest", encoded)
        self.assertIn("observed_usage", encoded)

    def test_attempt_record_requires_agent_safe_view(self):
        with self.assertRaises(ValueError):
            replace(
                attempt_record(),
                evidence_view="operator_full",
            )

    def test_attempt_record_copies_typed_payload_mappings(self):
        source = {"nested": {"value": 1}}
        record = replace(
            attempt_record(),
            payload=CandidateRepairPayload(
                validation_summary=source,
                model_result_available=True,
            ),
        )
        source["nested"]["value"] = 9
        self.assertEqual(
            record.payload.validation_summary[
                "nested"
            ]["value"],
            1,
        )

    def test_run_record_rejects_mixed_roles_and_payloads(self):
        with self.assertRaises(ValueError):
            RepairRunRecord(
                run_id="mixed-run",
                artifact_role=RepairArtifactRole.CANDIDATE,
                terminal_status=RepairTerminalStatus.FAILED,
                stop_reason="mixed",
                attempts=(
                    attempt_record(
                        role=RepairArtifactRole.TESTBENCH
                    ),
                ),
            )
        with self.assertRaises(ValueError):
            replace(
                attempt_record(),
                payload=TestbenchRepairPayload(
                    preflight_summary={"status": "passed"},
                ),
            )

    def test_run_record_rejects_duplicate_attempt_ids(self):
        first = attempt_record()
        with self.assertRaises(ValueError):
            RepairRunRecord(
                run_id="run-one",
                artifact_role=RepairArtifactRole.CANDIDATE,
                terminal_status=RepairTerminalStatus.FAILED,
                stop_reason="duplicate",
                attempts=(first, first),
            )

    def test_run_record_rejects_unordered_indices(self):
        first = attempt_record(index=2)
        second = attempt_record(index=1)
        with self.assertRaises(ValueError):
            RepairRunRecord(
                run_id="run-one",
                artifact_role=RepairArtifactRole.CANDIDATE,
                terminal_status=RepairTerminalStatus.FAILED,
                stop_reason="unordered",
                attempts=(first, second),
            )

    def test_run_record_is_json_serializable(self):
        encoded = json.dumps(
            run_record().to_safe_dict(),
            sort_keys=True,
        )
        self.assertIn('"attempt_count": 1', encoded)


class RepairArtifactWriterTests(unittest.TestCase):
    def test_writer_creates_run_attempt_and_manifest(self):
        with TemporaryDirectory() as temporary:
            result = RepairArtifactWriter(
                Path(temporary) / "bundle"
            ).write(run_record())
            self.assertTrue(
                Path(result.run_record_path).is_file()
            )
            self.assertEqual(len(result.attempt_paths), 1)
            self.assertTrue(
                Path(
                    result.artifact_manifest_path
                ).is_file()
            )

    def test_writer_manifest_hashes_match_files(self):
        with TemporaryDirectory() as temporary:
            result = RepairArtifactWriter(
                Path(temporary) / "bundle"
            ).write(run_record())
            manifest = json.loads(
                Path(
                    result.artifact_manifest_path
                ).read_text(encoding="utf-8")
            )
            for item in manifest["files"]:
                path = (
                    Path(result.root)
                    / item["relative_path"]
                )
                self.assertEqual(
                    hashlib.sha256(
                        path.read_bytes()
                    ).hexdigest(),
                    item["sha256"],
                )

    def test_writer_manifest_is_agent_safe(self):
        with TemporaryDirectory() as temporary:
            result = RepairArtifactWriter(
                Path(temporary) / "bundle"
            ).write(run_record())
            encoded = Path(
                result.artifact_manifest_path
            ).read_text(encoding="utf-8")
            self.assertIn('"agent_safe"', encoded)
            self.assertNotIn("operator_full", encoded)

    def test_writer_rejects_nonempty_root(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "bundle"
            root.mkdir()
            (root / "existing.txt").write_text(
                "x",
                encoding="utf-8",
            )
            with self.assertRaises(FileExistsError):
                RepairArtifactWriter(root).write(
                    run_record()
                )

    def test_writer_leaves_no_temporary_files(self):
        with TemporaryDirectory() as temporary:
            result = RepairArtifactWriter(
                Path(temporary) / "bundle"
            ).write(run_record())
            self.assertEqual(
                tuple(Path(result.root).rglob("*.tmp")),
                (),
            )

    def test_writer_refuses_second_write(self):
        with TemporaryDirectory() as temporary:
            writer = RepairArtifactWriter(
                Path(temporary) / "bundle"
            )
            writer.write(run_record())
            with self.assertRaises(FileExistsError):
                writer.write(run_record())


class CandidateRepairProtocolIntegrationTests(unittest.TestCase):
    def _candidate_attempt(self):
        validation = CandidateValidationResult(
            passed=True,
            completed_stages=(
                ValidationState.PREFLIGHT,
                ValidationState.CSYNTH,
            ),
            summary="validated",
        )
        return CandidateRepairAttempt(
            attempt=1,
            status=CandidateRepairAttemptStatus.VALIDATED,
            input_candidate="int candidate_top(){return 0;}",
            proposal="int candidate_top(){return 1;}",
            model_response=response(
                "```cpp\nint candidate_top(){return 1;}\n```"
            ),
            model_result=None,
            validation_result=validation,
            error_type=None,
            error_message=None,
            budget_before=usage(),
            budget_after=usage(
                llm=1,
                tokens=12,
                cost=0.02,
            ),
            prompt_manifest={
                "purpose": "candidate_csynth_repair",
                "editable_artifacts": ["candidate_kernel"],
            },
        )

    def test_candidate_attempt_emits_shared_vocabulary(self):
        record = self._candidate_attempt().to_protocol_record(
            "candidate-run"
        )
        payload = record.to_safe_dict()
        self.assertEqual(
            payload["artifact_role"],
            "candidate",
        )
        self.assertEqual(
            payload["observed_usage"]["llm_calls"],
            1,
        )
        blocked = replace(
            self._candidate_attempt(),
            status=(
                CandidateRepairAttemptStatus.BUDGET_BLOCKED
            ),
            proposal=None,
            model_response=None,
            prompt_manifest={
                "purpose": "candidate_csynth_repair"
            },
            budget_after=usage(),
        ).to_protocol_record(
            "candidate-blocked"
        ).to_safe_dict()
        self.assertFalse(
            blocked["model_call_observed"]
        )

    def test_candidate_result_maps_terminal_status(self):
        result = CandidateRepairLoopResult(
            stop_reason=CandidateRepairStopReason.VALIDATED,
            initial_candidate="int candidate_top(){return 0;}",
            current_candidate="int candidate_top(){return 1;}",
            last_validated_candidate="int candidate_top(){return 1;}",
            last_proposal="int candidate_top(){return 1;}",
            attempts=(self._candidate_attempt(),),
            budget_usage=usage(
                llm=1,
                tokens=12,
                cost=0.02,
            ),
        )
        run = result.to_repair_run_record(
            "candidate-run"
        )
        self.assertIs(
            run.terminal_status,
            RepairTerminalStatus.SUCCEEDED,
        )

    def test_candidate_result_writes_shared_artifacts(self):
        result = CandidateRepairLoopResult(
            stop_reason=CandidateRepairStopReason.VALIDATED,
            initial_candidate="int candidate_top(){return 0;}",
            current_candidate="int candidate_top(){return 1;}",
            last_validated_candidate="int candidate_top(){return 1;}",
            last_proposal="int candidate_top(){return 1;}",
            attempts=(self._candidate_attempt(),),
            budget_usage=usage(llm=1, tokens=12),
        )
        with TemporaryDirectory() as temporary:
            paths = result.write_artifacts(
                Path(temporary) / "candidate",
                run_id="candidate-run",
            )
            self.assertTrue(
                Path(paths.artifact_manifest_path).is_file()
            )


class TestbenchRepairProtocolIntegrationTests(unittest.TestCase):
    def test_model_repairer_records_prompt_and_response(self):
        repairer = model_repairer(
            response(
                f"```cpp\n{REPAIRED_TB}\n```"
            )
        )
        request = TestbenchRepairRequest(
            attempt=1,
            max_attempts=1,
            current_testbench=CURRENT_TB,
            original_code="int original_top(){return 1;}",
            candidate_code="int candidate_top(int x){return x;}",
            preflight=failed_preflight(),
            task=TASK,
        )
        repaired = repairer.repair(request)
        self.assertIn("repaired", repaired)
        self.assertEqual(len(repairer.audit_events), 1)
        event = repairer.audit_events[0]
        self.assertTrue(event.model_call_observed)
        self.assertIsNotNone(event.model_response)

    def test_model_repairer_records_prompt_on_provider_error(self):
        repairer = model_repairer(
            RuntimeError("provider failed")
        )
        request = TestbenchRepairRequest(
            attempt=1,
            max_attempts=1,
            current_testbench=CURRENT_TB,
            original_code="int original_top(){return 1;}",
            candidate_code="int candidate_top(int x){return x;}",
            preflight=failed_preflight(),
            task=TASK,
        )
        with self.assertRaises(RuntimeError):
            repairer.repair(request)
        self.assertEqual(len(repairer.audit_events), 1)
        self.assertIsNone(
            repairer.audit_events[0].model_response
        )

    def test_testbench_attempt_safe_summary_omits_raw_fields(self):
        attempt = TestbenchRepairAttempt(
            index=0,
            action="initial_preflight",
            changed=False,
            preflight=failed_preflight(),
        )
        payload = attempt.to_protocol_record(
            "testbench-run"
        ).to_safe_dict()
        summary = payload["payload"]["preflight_summary"]
        self.assertNotIn("command", summary)
        self.assertNotIn("stdout", summary)
        self.assertNotIn("stderr", summary)
        self.assertNotIn("artifacts", summary)

    def test_testbench_loop_writes_legacy_and_shared_artifacts(self):
        repairer = model_repairer(
            response(
                f"```cpp\n{REPAIRED_TB}\n```"
            )
        )
        loop = TestbenchRepairLoop(
            preflight=SequencePreflight(
                [
                    failed_preflight(),
                    passed_preflight(),
                ]
            ),
            repairer=repairer,
            max_repair_attempts=1,
        )
        with TemporaryDirectory() as temporary:
            result = loop.run(
                work_dir=Path(temporary) / "run",
                testbench_code=CURRENT_TB,
                original_code="int original_top(){return 1;}",
                candidate_code="int candidate_top(int x){return x;}",
                budget=BudgetManager(),
                task=TASK,
            )
            self.assertTrue(result.succeeded)
            self.assertTrue(
                Path(result.artifact_path).is_file()
            )
            self.assertTrue(
                Path(
                    result.repair_artifact_manifest_path
                ).is_file()
            )
            self.assertEqual(
                result.repair_run.artifact_role,
                RepairArtifactRole.TESTBENCH,
            )

    def test_testbench_loop_provider_error_still_writes_prompt_manifest(self):
        repairer = model_repairer(
            RuntimeError("provider failed")
        )
        loop = TestbenchRepairLoop(
            preflight=SequencePreflight(
                [failed_preflight()]
            ),
            repairer=repairer,
            max_repair_attempts=1,
        )
        with TemporaryDirectory() as temporary:
            result = loop.run(
                work_dir=Path(temporary) / "run",
                testbench_code=CURRENT_TB,
                original_code="int original_top(){return 1;}",
                candidate_code="int candidate_top(int x){return x;}",
                budget=BudgetManager(),
                task=TASK,
            )
            self.assertIs(
                result.status,
                TestbenchRepairStatus.EXHAUSTED,
            )
            run = json.loads(
                Path(result.repair_run_path).read_text(
                    encoding="utf-8"
                )
            )
            repair_attempt = run["attempts"][1]
            self.assertTrue(
                repair_attempt["prompt_manifest"]
            )
            self.assertIsNone(
                repair_attempt["model_response"]
            )

    def test_candidate_and_testbench_share_envelope_not_business_payload(self):
        candidate = CandidateRepairProtocolIntegrationTests()._candidate_attempt()
        candidate_payload = candidate.to_protocol_record(
            "candidate-run"
        ).to_safe_dict()
        testbench_payload = TestbenchRepairAttempt(
            index=1,
            action="repair_and_preflight",
            changed=True,
            preflight=passed_preflight(),
            model_observation=observation(),
            observed_usage=RepairObservedUsage(
                llm_calls=1,
                tokens=12,
                cost_usd=0.02,
            ),
        ).to_protocol_record(
            "testbench-run"
        ).to_safe_dict()
        self.assertEqual(
            set(candidate_payload),
            set(testbench_payload),
        )
        self.assertEqual(
            candidate_payload["payload_type"],
            "candidate_repair",
        )
        self.assertEqual(
            testbench_payload["payload_type"],
            "testbench_repair",
        )
        self.assertNotEqual(
            set(candidate_payload["payload"]),
            set(testbench_payload["payload"]),
        )

    def test_protocol_modules_do_not_import_orchestrators_or_tools(self):
        import agrefactor.repair.artifacts as artifacts_module
        import agrefactor.repair.protocol as protocol_module

        sources = (
            Path(protocol_module.__file__).read_text(
                encoding="utf-8"
            ),
            Path(artifacts_module.__file__).read_text(
                encoding="utf-8"
            ),
        )
        for source in sources:
            self.assertNotIn(
                "ValidationOrchestrator",
                source,
            )
            self.assertNotIn(
                "flow.tools",
                source,
            )
            self.assertNotIn(
                ".generate(",
                source,
            )


if __name__ == "__main__":
    unittest.main()
