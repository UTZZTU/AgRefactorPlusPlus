import json
from hashlib import sha256
import tempfile
import unittest
from dataclasses import dataclass, replace
from pathlib import Path

from agrefactor.config import (
    EvaluationSplit,
    SourcePackageSpec,
    TaskSpec,
    TestSuiteSpec,
)
from agrefactor.evaluation import ValidationState
from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackItem,
    FeedbackOwner,
    FeedbackReport,
    FeedbackSeverity,
    FeedbackStage,
    RootRunRecordStore,
)
from agrefactor.evidence.validation_capture import ValidationEvidenceRecorder
from agrefactor.runtime import (
    BudgetLimits,
    BudgetManager,
    RunContext,
    TraceRecorder,
    ValidationOrchestrator,
)


HIDDEN_PAYLOAD = "HIDDEN_EVIDENCE_MUST_NOT_BE_PERSISTED"


@dataclass
class StageInputs:
    work_dir: Path
    candidate_code: str
    original_code: str
    testbench_code: str
    suite_testbench_codes: dict[str, str]
    candidate_top_function: str = "top"
    original_top_function: str = "top"
    target_profile: object = None
    execution_backend: str = "native_vitis"
    timelimit: int = 300
    extra_sources: tuple[str, ...] = ()
    csynth_work_dir: Path | None = None


class FakeHandler:
    handler_version = 1
    semantics_version = 1

    def __init__(
        self,
        inputs,
        report,
        *,
        mutate_path=None,
        receipt_path=None,
        receipt_payload=None,
        suite_prefix="",
        fail=False,
    ):
        self.inputs = inputs
        self._report = report
        self._mutate_path = mutate_path
        self._receipt_path = receipt_path
        self._receipt_payload = receipt_payload or {"executor": "fixture"}
        self._suite_prefix = suite_prefix
        self._fail = fail
        self.calls = 0

    def _suite_work_dir(self, index):
        return (
            Path(self.inputs.work_dir)
            / self._suite_prefix
            / f"suite_{index:03d}"
        )

    def __call__(self, context):
        self.calls += 1
        if self._fail:
            raise RuntimeError("fake handler failure")
        self._write_actual_inputs()
        if self._receipt_path is not None:
            self._receipt_path.parent.mkdir(parents=True, exist_ok=True)
            self._receipt_path.write_text(
                json.dumps(self._receipt_payload),
                encoding="utf-8",
            )
        if self._mutate_path is not None:
            self._mutate_path.write_bytes(b"after")
        return self._report

    def _write_actual_inputs(self):
        if self._receipt_path is None:
            return
        root = Path(self.inputs.work_dir)
        name = self._receipt_path.name
        if name == "testbench_preflight_invocation.json":
            files = {
                "testbench.cpp": self.inputs.testbench_code,
                "orig_code.cpp": self.inputs.original_code,
                "refactor_code.cpp": self.inputs.candidate_code,
            }
            base = root
        elif name == "csim_invocation.json":
            files = {
                "candidate.cpp": self.inputs.candidate_code,
                "reference.cpp": self.inputs.original_code,
                "testbench.cpp": self.inputs.suite_testbench_codes[
                    "public-suite"
                ],
                "agrefactor_csim_wrapper.cpp": "int wrapper() { return 0; }",
                "agrefactor_case_evidence.hpp": "struct case_evidence {};",
            }
            base = self._receipt_path.parent
        elif name == "csynth_invocation.json":
            files = {"top.cpp": self.inputs.candidate_code}
            base = root
        elif name == "cosim_invocation.json":
            files = {
                "candidate.cpp": self.inputs.candidate_code,
                "reference.cpp": self.inputs.original_code,
                "public_testbench_original.cpp": self.inputs.suite_testbench_codes[
                    "public-suite"
                ],
                "public_testbench.cpp": "int public_tb_instrumented() { return 0; }",
                "agrefactor_cosim_wrapper.cpp": "int cosim_wrapper() { return 0; }",
            }
            base = self._receipt_path.parent
        else:
            return
        for relative, content in files.items():
            path = base / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        if name == "csynth_invocation.json":
            source = base / "top.cpp"
            self._receipt_payload["source_provenance"] = {
                "top.cpp": {
                    "path": str(source.resolve()),
                    "sha256": sha256(source.read_bytes()).hexdigest(),
                    "role": "candidate",
                }
            }


def safe_report(source, *, metadata=None, evidence=None):
    return FeedbackReport(
        report_id=f"{source}-report",
        source=source,
        items=(),
        source_evidence=evidence or {},
        metadata={"evidence_view": "agent_safe", **(metadata or {})},
    )


def hidden_report():
    return FeedbackReport(
        report_id=HIDDEN_PAYLOAD,
        source="test_evaluation",
        items=(),
        source_evidence={"hidden_payload": HIDDEN_PAYLOAD},
        metadata={
            "evidence_view": "operator_full",
            "evaluation_split": "hidden",
            "feedback_visible_to_agent": False,
        },
    )


class ValidationEvidenceCaptureTests(unittest.TestCase):
    def _task_and_handlers(self, root, *, fail_preflight=False, mutate=False):
        kernel_root = root / "kernel_inputs"
        kernel_root.mkdir(exist_ok=True)
        kernel = kernel_root / "kernel.cpp"
        kernel.write_text("int top() { return 1; }", encoding="utf-8")
        task = TaskSpec(
            task_id="capture-task",
            kernel_path=str(kernel),
            kernel_name="top",
            test_suites=(
                TestSuiteSpec(
                    suite_id="public-suite",
                    split=EvaluationSplit.PUBLIC,
                ),
                TestSuiteSpec(
                    suite_id="hidden-suite",
                    split=EvaluationSplit.HIDDEN,
                ),
            ),
        )
        suites = {
            "public-suite": "int public_tb() { return 0; }",
            "hidden-suite": HIDDEN_PAYLOAD,
        }
        reports = {
            ValidationState.PREFLIGHT: safe_report(
                "testbench_preflight",
                metadata={
                    "preflight_status": "passed",
                    "physical_execution": True,
                },
            ),
            ValidationState.PUBLIC_EVALUATION: safe_report(
                "test_evaluation",
                metadata={
                    "evaluation_split": "public",
                    "feedback_visible_to_agent": True,
                    "suite_id": "public-suite",
                    "native_vitis_csim": True,
                    "declared_suite_ids": ["public-suite"],
                    "attempted_suite_ids": ["public-suite"],
                    "stopped_early": False,
                },
            ),
            ValidationState.CSYNTH: safe_report(
                "csynth",
                metadata={
                    "physical_execution": True,
                    "operator_invocation_available": True,
                    "legacy_status": "succeeded",
                },
            ),
            ValidationState.PUBLIC_COSIM: safe_report(
                "public_rtl_cosim",
                metadata={
                    "physical_execution": True,
                    "declared_suite_ids": ["public-suite"],
                    "attempted_suite_ids": ["public-suite"],
                    "stopped_early": False,
                },
                evidence={
                    "suite_summaries": [
                        {
                            "suite_id": "public-suite",
                            "status": "passed",
                            "cosim_launched": True,
                        }
                    ]
                },
            ),
            ValidationState.HIDDEN_EVALUATION: hidden_report(),
        }
        states = (
            ValidationState.PREFLIGHT,
            ValidationState.PUBLIC_EVALUATION,
            ValidationState.CSYNTH,
            ValidationState.PUBLIC_COSIM,
            ValidationState.HIDDEN_EVALUATION,
        )
        handlers = {}
        for state in states:
            work_dir = root / state.value
            work_dir.mkdir()
            staged = work_dir / "staged.cpp"
            staged.write_bytes(b"before")
            inputs = StageInputs(
                work_dir=work_dir,
                candidate_code="int top() { return 2; }",
                original_code="int top() { return 1; }",
                testbench_code="int preflight_tb() { return 0; }",
                suite_testbench_codes=suites,
                target_profile=task.target,
            )
            receipt_paths = {
                ValidationState.PREFLIGHT: (
                    work_dir / "testbench_preflight_invocation.json"
                ),
                ValidationState.PUBLIC_EVALUATION: (
                    work_dir
                    / "public"
                    / "suite_001"
                    / "csim_invocation.json"
                ),
                ValidationState.CSYNTH: (
                    work_dir / "csynth_invocation.json"
                ),
                ValidationState.PUBLIC_COSIM: (
                    work_dir / "suite_001" / "cosim_invocation.json"
                ),
            }
            receipt_payloads = {
                ValidationState.PREFLIGHT: {
                    "compiler": "/bin/true",
                    "execution": {"status": "completed", "returncode": 0},
                    "reason_code": "passed",
                },
                ValidationState.PUBLIC_EVALUATION: {
                    "toolchain_version_verification": {
                        "status": "matched",
                        "actual": "2023.2",
                    }
                },
                ValidationState.CSYNTH: {
                    "toolchain_version_verification": {
                        "status": "matched",
                        "actual": "2023.2",
                    },
                    "execution": {"status": "completed", "returncode": 0},
                },
                ValidationState.PUBLIC_COSIM: {
                    "toolchain_version_verification": {
                        "status": "matched",
                        "actual": "2023.2",
                    },
                    "result_summary": {
                        "status": "passed",
                        "cosim_launched": True,
                    },
                },
            }
            handlers[state] = FakeHandler(
                inputs,
                reports[state],
                mutate_path=staged if state is ValidationState.PREFLIGHT and mutate else None,
                receipt_path=receipt_paths.get(state),
                receipt_payload=receipt_payloads.get(state),
                fail=state is ValidationState.PREFLIGHT and fail_preflight,
            )
        return task, handlers

    def test_public_evidence_is_root_linked_and_hidden_is_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task, handlers = self._task_and_handlers(root, mutate=True)
            store = RootRunRecordStore(root / "record", "run-1")
            context = RunContext(
                run_id="run-1",
                task=task,
                budget=BudgetManager(BudgetLimits()),
                trace=TraceRecorder("run-1", task_id=task.task_id),
                root_record=store,
            )

            initial = ValidationOrchestrator(handlers).run_detailed(
                context,
                validation_id="validation-initial",
            )
            self.assertTrue(initial.result.accepted)
            self.assertEqual(
                store.validation_sessions()[0]["attempt_id"],
                None,
            )
            handlers[ValidationState.PREFLIGHT]._mutate_path = None
            store.prepare_attempt(
                "attempt-1",
                {
                    "attempt_id": "attempt-1",
                    "run_id": "run-1",
                    "evidence_view": "agent_safe",
                },
            )
            store.mark_attempt_dispatched("attempt-1")
            sequence_before = store.attempt_record("attempt-1")["sequence"]
            repaired = ValidationOrchestrator(handlers).run_detailed(
                context,
                validation_id="validation-after",
                attempt_id="attempt-1",
            )
            self.assertTrue(repaired.result.accepted)
            sessions = store.validation_sessions()
            self.assertEqual(len(sessions), 2)
            self.assertEqual(sessions[1]["attempt_id"], "attempt-1")
            evidence = store.validation_evidence()
            self.assertEqual(
                {item["stage"] for item in evidence},
                {"preflight", "csim", "csynth", "cosim"},
            )
            completed_evidence = [
                item
                for item in evidence
                if item["execution_status"] == "completed"
            ]
            self.assertEqual(
                {item["evidence_verdict"] for item in completed_evidence},
                {"passed"},
                [
                    (
                        item["stage"],
                        item["evidence_verdict"],
                        item.get("environment_observation"),
                    )
                    for item in completed_evidence
                ],
            )
            self.assertFalse(any("hidden" in item["state"] for item in evidence))
            started_order = [
                item["stage"]
                for item in evidence
                if item["execution_status"] == "started"
            ]
            self.assertEqual(
                started_order,
                [
                    "preflight",
                    "csim",
                    "csynth",
                    "cosim",
                ]
                * 2,
            )
            self.assertNotIn(HIDDEN_PAYLOAD, json.dumps(store.snapshot()))
            self.assertEqual(
                store.attempt_record("attempt-1")["sequence"],
                sequence_before,
            )
            store.finish("failed")
            self.assertEqual(store.snapshot()["cases"], {})
            self.assertEqual(
                {
                    item["evidence_verdict"]
                    for item in store.validation_evidence()
                    if item.get("execution_status") == "completed"
                },
                {"passed"},
            )
            attempt_refs = store.attempt_record("attempt-1")["details"][
                "validation_evidence_refs"
            ]
            self.assertEqual(len(attempt_refs), 8)
            self.assertTrue(
                all("validation-after" in item for item in attempt_refs)
            )

            preflight_start = next(
                item
                for item in evidence
                if item["stage"] == "preflight"
                and item["execution_status"] == "started"
            )
            self.assertTrue(
                preflight_start["input_snapshot"]["replay_eligible"]
            )
            staged = preflight_start["input_snapshot"]["files"][0]
            self.assertEqual(
                store.read_validation_artifact(staged["artifact_ref"]),
                b"before",
            )
            all_artifacts = [
                item
                for evidence_item in evidence
                for item in (
                    evidence_item.get("input_snapshot", {}).get(
                        "input_contents", []
                    )
                    + evidence_item.get("input_snapshot", {}).get(
                        "files", []
                    )
                    + evidence_item.get("input_snapshot", {}).get(
                        "source_package_files", []
                    )
                    + evidence_item.get("output_artifacts", [])
                )
            ]
            payloads = [
                store.read_validation_artifact(item["artifact_ref"])
                for item in all_artifacts
                if "artifact_ref" in item
            ]
            self.assertFalse(
                any(HIDDEN_PAYLOAD.encode() in payload for payload in payloads)
            )
            timeline = store.timeline()
            self.assertEqual(timeline[0]["kind"], "validation_session")
            self.assertTrue(
                any(item["kind"] == "validation_session" for item in timeline)
            )
            post_attempt_session = next(
                item
                for item in timeline
                if item["kind"] == "validation_session"
                and item["id"] == "validation-after"
            )
            attempt_timeline = next(
                item for item in timeline if item["kind"] == "attempt"
            )
            self.assertLess(
                attempt_timeline["sequence"],
                post_attempt_session["sequence"],
            )

    def test_handler_error_records_interrupted_and_later_not_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task, handlers = self._task_and_handlers(
                root, fail_preflight=True
            )
            store = RootRunRecordStore(root / "record", "run-2")
            context = RunContext(
                run_id="run-2",
                task=task,
                budget=BudgetManager(BudgetLimits()),
                trace=TraceRecorder("run-2", task_id=task.task_id),
                root_record=store,
            )
            with self.assertRaisesRegex(RuntimeError, "fake handler failure"):
                ValidationOrchestrator(handlers).run_detailed(
                    context,
                    validation_id="validation-2",
                )
            evidence = store.validation_evidence()
            statuses = {
                (item["stage"], item["execution_status"])
                for item in evidence
            }
            self.assertIn(("preflight", "interrupted"), statuses)
            self.assertIn(("csim", "not_run"), statuses)
            self.assertIn(("csynth", "not_run"), statuses)
            self.assertIn(("cosim", "not_run"), statuses)
            self.assertFalse(
                any("hidden" in item["state"] for item in evidence)
            )

    def test_started_stage_survives_reload_without_invented_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task, handlers = self._task_and_handlers(root)
            store = RootRunRecordStore(root / "record", "interrupted-run")
            recorder = ValidationEvidenceRecorder(
                store,
                task=task,
                run_id="interrupted-run",
                validation_id="interrupted-validation",
            )
            recorder.start_session((ValidationState.PUBLIC_EVALUATION,))
            recorder.start_stage(
                state=ValidationState.PUBLIC_EVALUATION,
                step_id="interrupted-validation.step.1",
                handler=handlers[ValidationState.PUBLIC_EVALUATION],
            )

            recovered = RootRunRecordStore(
                root / "record", "interrupted-run"
            )
            evidence = recovered.validation_evidence()
            self.assertEqual(len(evidence), 1)
            self.assertEqual(evidence[0]["execution_status"], "started")
            self.assertIsNone(evidence[0]["evidence_verdict"])
            self.assertTrue(evidence[0]["input_snapshot"])
            self.assertFalse(
                any(item["evidence_id"].endswith(":result") for item in evidence)
            )

    def test_no_public_suite_records_public_stages_as_not_applicable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original_task, handlers = self._task_and_handlers(root)
            task = replace(original_task, test_suites=())
            store = RootRunRecordStore(root / "record", "no-public-run")
            context = RunContext(
                run_id="no-public-run",
                task=task,
                budget=BudgetManager(BudgetLimits()),
                trace=TraceRecorder("no-public-run", task_id=task.task_id),
                root_record=store,
            )

            ValidationOrchestrator(handlers).run_detailed(
                context,
                validation_id="no-public-validation",
            )

            public_statuses = {
                item["stage"]: (item["execution_status"], item["reason"])
                for item in store.validation_evidence()
                if item["stage"] in {"csim", "cosim"}
            }
            self.assertEqual(
                public_statuses,
                {
                    "csim": ("not_applicable", "no_public_suite_declared"),
                    "cosim": ("not_applicable", "no_public_suite_declared"),
                },
            )

    def test_capture_does_not_add_handler_executions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plain_root = root / "plain"
            plain_root.mkdir()
            task, without_capture = self._task_and_handlers(plain_root)
            plain_context = RunContext(
                run_id="plain-run",
                task=task,
                budget=BudgetManager(BudgetLimits()),
                trace=TraceRecorder("plain-run", task_id=task.task_id),
            )
            plain_result = ValidationOrchestrator(without_capture).run_detailed(
                plain_context,
                validation_id="plain-validation",
            )

            capture_root = root / "captured"
            capture_root.mkdir()
            captured_task, with_capture = self._task_and_handlers(
                capture_root
            )
            store = RootRunRecordStore(capture_root / "record", "captured-run")
            captured_context = RunContext(
                run_id="captured-run",
                task=captured_task,
                budget=BudgetManager(BudgetLimits()),
                trace=TraceRecorder(
                    "captured-run", task_id=captured_task.task_id
                ),
                root_record=store,
            )
            captured_result = ValidationOrchestrator(with_capture).run_detailed(
                captured_context,
                validation_id="captured-validation",
            )

            self.assertEqual(plain_result.result.accepted, captured_result.result.accepted)
            self.assertEqual(
                {state: handler.calls for state, handler in without_capture.items()},
                {state: handler.calls for state, handler in with_capture.items()},
            )
            self.assertTrue(
                all(handler.calls == 1 for handler in with_capture.values())
            )

    def test_legacy_validation_evidence_is_read_as_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record_root = root / "record"
            record_root.mkdir()
            legacy_payload = {
                "evidence_id": "legacy-csim-result",
                "root_run_id": "legacy-run",
                "validation_id": "legacy-validation",
                "stage": "csim",
                "state": "public_evaluation",
                "evidence_verdict": "passed",
            }
            record = {
                "schema_version": 1,
                "run_id": "legacy-run",
                "lifecycle": {
                    "status": "finished",
                    "started_at": "2026-10-01T00:00:00+00:00",
                    "finished_at": "2026-10-01T00:01:00+00:00",
                },
                "next_sequence": 2,
                "events": {},
                "attempts": {},
                "cases": {},
                "relation_history": [],
                "validation_evidence": {
                    "legacy-csim-result": {
                        "sequence": 1,
                        "payload": legacy_payload,
                    }
                },
            }
            (record_root / "root_run_record.json").write_text(
                json.dumps(record), encoding="utf-8"
            )

            store = RootRunRecordStore(record_root, "legacy-run")
            evidence = store.validation_evidence()[0]

            self.assertTrue(evidence["legacy_evidence_incomplete"])
            self.assertNotIn("input_snapshot", evidence)
            self.assertNotIn(
                "legacy_evidence_incomplete",
                store.snapshot()["validation_evidence"]["legacy-csim-result"]["payload"],
            )

    def test_csim_and_cosim_capture_actual_source_package_suite_copies(self):
        cases = (
            (ValidationState.PUBLIC_EVALUATION, "public", False),
            (ValidationState.PUBLIC_COSIM, "", False),
            (ValidationState.PUBLIC_COSIM, "", True),
        )
        for index, (state, suite_prefix, mismatch) in enumerate(cases):
            with self.subTest(state=state, mismatch=mismatch):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    package_root = root / "source"
                    package_root.mkdir()
                    target = package_root / "kernel.cpp"
                    header = package_root / "include" / "kernel.hpp"
                    header.parent.mkdir()
                    target.write_bytes(b"int top() { return 1; }")
                    header.write_bytes(b"struct input {};\n")
                    package = SourcePackageSpec(
                        root=package_root,
                        target=target,
                        extra_sources=(),
                    )
                    suite = TestSuiteSpec(
                        suite_id="public-suite",
                        split=EvaluationSplit.PUBLIC,
                    )
                    task = TaskSpec(
                        task_id=f"package-{index}",
                        kernel_path=str(target),
                        kernel_name="top",
                        source_package=package,
                        test_suites=(suite,),
                    )
                    stage_name = (
                        "csim"
                        if state is ValidationState.PUBLIC_EVALUATION
                        else "cosim"
                    )
                    work_dir = root / stage_name
                    work_dir.mkdir()
                    suite_dir = (
                        work_dir
                        / suite_prefix
                        / "suite_001"
                    )
                    package.stage_into(suite_dir)
                    inputs = StageInputs(
                        work_dir=work_dir,
                        candidate_code="int top() { return 2; }",
                        original_code="int top() { return 1; }",
                        testbench_code="int preflight_tb() { return 0; }",
                        suite_testbench_codes={
                            "public-suite": "int public_tb() { return 0; }"
                        },
                        target_profile=task.target,
                    )
                    receipt_name = (
                        "csim_invocation.json"
                        if stage_name == "csim"
                        else "cosim_invocation.json"
                    )
                    receipt_path = suite_dir / receipt_name
                    report = safe_report(
                        "test_evaluation"
                        if stage_name == "csim"
                        else "public_rtl_cosim",
                        metadata={
                            "evaluation_split": "public",
                            "feedback_visible_to_agent": True,
                            "physical_execution": True,
                            "native_vitis_csim": stage_name == "csim",
                            "native_vitis_cosim": stage_name == "cosim",
                            "declared_suite_ids": ["public-suite"],
                            "attempted_suite_ids": ["public-suite"],
                            "stopped_early": False,
                        },
                        evidence=(
                            {
                                "suite_summaries": [
                                    {
                                        "suite_id": "public-suite",
                                        "status": "passed",
                                        "cosim_launched": True,
                                    }
                                ]
                            }
                            if stage_name == "cosim"
                            else None
                        ),
                    )
                    handler = FakeHandler(
                        inputs,
                        report,
                        receipt_path=receipt_path,
                        receipt_payload={
                            "toolchain_version_verification": {
                                "status": "matched",
                                "actual": "2023.2",
                            }
                        },
                        suite_prefix=suite_prefix,
                    )
                    store = RootRunRecordStore(
                        root / "record", f"package-run-{index}"
                    )
                    recorder = ValidationEvidenceRecorder(
                        store,
                        task=task,
                        run_id=f"package-run-{index}",
                        validation_id=f"package-validation-{index}",
                    )
                    recorder.start_session((state,))
                    capture = recorder.start_stage(
                        state=state,
                        step_id=f"package-validation-{index}.step.1",
                        handler=handler,
                    )
                    if mismatch:
                        (suite_dir / "include" / "kernel.hpp").write_bytes(
                            b"struct changed {};\n"
                        )
                    stage_report = handler(None)
                    recorder.finish_stage(capture, report=stage_report)

                    evidence = next(
                        item
                        for item in store.validation_evidence()
                        if item["execution_status"] == "completed"
                    )
                    copies = [
                        item
                        for item in evidence["actual_consumed_inputs"]
                        if item.get("input_kind") == "source_package_copy"
                    ]
                    self.assertEqual(len(copies), 2)
                    self.assertEqual(
                        {item["suite_id"] for item in copies},
                        {"public-suite"},
                    )
                    self.assertEqual(
                        evidence["capture_status"],
                        "incomplete" if mismatch else "complete",
                    )
                    if mismatch:
                        self.assertIn(
                            "source_package_copy_incomplete",
                            evidence["capture_errors"],
                        )
                    header_copy = next(
                        item
                        for item in copies
                        if item["logical_path"].endswith("kernel.hpp")
                    )
                    self.assertEqual(
                        header_copy["matches_declared_input"],
                        not mismatch,
                    )
                    self.assertEqual(
                        sha256(
                            store.read_validation_artifact(
                                header_copy["artifact_ref"]
                            )
                        ).hexdigest(),
                        header_copy["sha256"],
                    )

    def test_cosim_snapshot_links_exact_csynth_producer_and_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kernel = root / "kernel.cpp"
            kernel.write_text("int top() { return 1; }", encoding="utf-8")
            task = TaskSpec(
                task_id="upstream-task",
                kernel_path=str(kernel),
                kernel_name="top",
                test_suites=(
                    TestSuiteSpec(
                        suite_id="public-suite",
                        split=EvaluationSplit.PUBLIC,
                    ),
                ),
            )
            csynth_dir = root / "csynth"
            csynth_dir.mkdir()
            interface_path = csynth_dir / "top_io.json"
            interface_path.write_text('{"top": "top"}\n', encoding="utf-8")
            interface_digest = sha256(interface_path.read_bytes()).hexdigest()
            csynth_inputs = StageInputs(
                work_dir=csynth_dir,
                candidate_code="int top() { return 2; }",
                original_code="int top() { return 1; }",
                testbench_code="int preflight_tb() { return 0; }",
                suite_testbench_codes={
                    "public-suite": "int public_tb() { return 0; }"
                },
                target_profile=task.target,
            )
            csynth_handler = FakeHandler(
                csynth_inputs,
                safe_report(
                    "csynth",
                    metadata={
                        "physical_execution": True,
                        "operator_invocation_available": True,
                        "legacy_status": "succeeded",
                    },
                ),
                receipt_path=csynth_dir / "csynth_invocation.json",
                receipt_payload={
                    "toolchain_version_verification": {
                        "status": "matched",
                        "actual": "2023.2",
                    },
                    "execution": {"status": "completed", "returncode": 0},
                    "top_io": {
                        "artifacts": [
                            {
                                "path": str(interface_path.resolve()),
                                "sha256": interface_digest,
                            }
                        ]
                    },
                },
            )
            store = RootRunRecordStore(root / "record", "upstream-run")
            recorder = ValidationEvidenceRecorder(
                store,
                task=task,
                run_id="upstream-run",
                validation_id="upstream-validation",
            )
            recorder.start_session(
                (ValidationState.CSYNTH, ValidationState.PUBLIC_COSIM)
            )
            csynth_capture = recorder.start_stage(
                state=ValidationState.CSYNTH,
                step_id="upstream-validation.step.1",
                handler=csynth_handler,
            )
            recorder.finish_stage(
                csynth_capture,
                report=csynth_handler(None),
            )
            producer = next(
                item
                for item in store.validation_evidence()
                if item["stage"] == "csynth"
                and item["evidence_verdict"] == "passed"
            )

            cosim_dir = root / "cosim"
            cosim_dir.mkdir()
            suite_dir = cosim_dir / "suite_001"
            cosim_inputs = StageInputs(
                work_dir=cosim_dir,
                candidate_code="int top() { return 2; }",
                original_code="int top() { return 1; }",
                testbench_code="int preflight_tb() { return 0; }",
                suite_testbench_codes={
                    "public-suite": "int public_tb() { return 0; }"
                },
                target_profile=task.target,
                csynth_work_dir=csynth_dir,
            )
            cosim_handler = FakeHandler(
                cosim_inputs,
                safe_report(
                    "public_rtl_cosim",
                    metadata={
                        "physical_execution": True,
                        "declared_suite_ids": ["public-suite"],
                        "attempted_suite_ids": ["public-suite"],
                        "stopped_early": False,
                    },
                ),
                receipt_path=suite_dir / "cosim_invocation.json",
                receipt_payload={
                    "toolchain_version_verification": {
                        "status": "matched",
                        "actual": "2023.2",
                    }
                },
            )
            cosim_capture = recorder.start_stage(
                state=ValidationState.PUBLIC_COSIM,
                step_id="upstream-validation.step.2",
                handler=cosim_handler,
            )

            upstream = cosim_capture and next(
                item
                for item in store.validation_evidence()
                if item["evidence_id"] == cosim_capture.started_evidence_id
            )["input_snapshot"]["upstream_artifacts"]
            self.assertTrue(upstream)
            self.assertTrue(
                all(
                    item["producer_validation_stage_id"]
                    == producer["evidence_id"]
                    for item in upstream
                )
            )
            self.assertTrue(
                any(
                    item["sha256"] == interface_digest
                    for item in upstream
                )
            )
            self.assertTrue(cosim_capture.snapshot_complete)

    def test_stage_snapshot_identity_is_idempotent_and_conflict_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task, handlers = self._task_and_handlers(root)
            store = RootRunRecordStore(root / "record", "snapshot-run")
            recorder = ValidationEvidenceRecorder(
                store,
                task=task,
                run_id="snapshot-run",
                validation_id="snapshot-validation-1",
            )
            recorder.start_session((ValidationState.PREFLIGHT,))
            handler = handlers[ValidationState.PREFLIGHT]
            first = recorder.start_stage(
                state=ValidationState.PREFLIGHT,
                step_id="snapshot-validation-1.step.1",
                handler=handler,
            )
            sequence_after_first = store.timeline()[-1]["sequence"]
            repeated = recorder.start_stage(
                state=ValidationState.PREFLIGHT,
                step_id="snapshot-validation-1.step.1",
                handler=handler,
            )
            self.assertEqual(first.snapshot_sha256, repeated.snapshot_sha256)
            self.assertEqual(store.timeline()[-1]["sequence"], sequence_after_first)

            handler.inputs.candidate_code = "int top() { return 3; }"
            with self.assertRaisesRegex(ValueError, "conflicting validation evidence"):
                recorder.start_stage(
                    state=ValidationState.PREFLIGHT,
                    step_id="snapshot-validation-1.step.1",
                    handler=handler,
                )

            recorder2 = ValidationEvidenceRecorder(
                store,
                task=task,
                run_id="snapshot-run",
                validation_id="snapshot-validation-2",
            )
            recorder2.start_session((ValidationState.PREFLIGHT,))
            changed = recorder2.start_stage(
                state=ValidationState.PREFLIGHT,
                step_id="snapshot-validation-2.step.1",
                handler=handler,
            )
            self.assertNotEqual(first.snapshot_sha256, changed.snapshot_sha256)

    def test_partial_public_suites_are_retained_without_false_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kernel = root / "kernel.cpp"
            kernel.write_text("int top() { return 1; }", encoding="utf-8")
            suites = (
                TestSuiteSpec(
                    suite_id="public-suite",
                    split=EvaluationSplit.PUBLIC,
                ),
                TestSuiteSpec(
                    suite_id="later-suite",
                    split=EvaluationSplit.PUBLIC,
                ),
            )
            task = TaskSpec(
                task_id="partial-suite-task",
                kernel_path=str(kernel),
                kernel_name="top",
                test_suites=suites,
            )
            work_dir = root / "csim"
            work_dir.mkdir()
            inputs = StageInputs(
                work_dir=work_dir,
                candidate_code="int top() { return 2; }",
                original_code="int top() { return 1; }",
                testbench_code="int preflight_tb() { return 0; }",
                suite_testbench_codes={
                    "public-suite": "int public_tb() { return 0; }",
                    "later-suite": "int later_tb() { return 0; }",
                },
                target_profile=task.target,
            )
            report = safe_report(
                "test_evaluation",
                metadata={
                    "evaluation_split": "public",
                    "feedback_visible_to_agent": True,
                    "native_vitis_csim": True,
                    "physical_execution": True,
                    "declared_suite_ids": ["public-suite", "later-suite"],
                    "attempted_suite_ids": ["public-suite"],
                    "stopped_early": True,
                    "stop_reason": "terminal_suite_failure",
                },
            )
            handler = FakeHandler(
                inputs,
                report,
                receipt_path=(
                    work_dir
                    / "public"
                    / "suite_001"
                    / "csim_invocation.json"
                ),
                receipt_payload={
                    "toolchain_version_verification": {
                        "status": "matched",
                        "actual": "2023.2",
                    }
                },
                suite_prefix="public",
            )
            store = RootRunRecordStore(root / "record", "partial-run")
            recorder = ValidationEvidenceRecorder(
                store,
                task=task,
                run_id="partial-run",
                validation_id="partial-validation",
            )
            recorder.start_session((ValidationState.PUBLIC_EVALUATION,))
            capture = recorder.start_stage(
                state=ValidationState.PUBLIC_EVALUATION,
                step_id="partial-validation.step.1",
                handler=handler,
            )
            recorder.finish_stage(capture, report=handler(None))

            result = next(
                item
                for item in store.validation_evidence()
                if item.get("execution_status") == "completed"
            )
            self.assertIsNone(result["evidence_verdict"])
            observed = result["observed_execution"]["metadata"]
            self.assertEqual(
                observed["declared_suite_ids"],
                ["public-suite", "later-suite"],
            )
            self.assertEqual(observed["attempted_suite_ids"], ["public-suite"])
            self.assertTrue(observed["stopped_early"])

    def test_blocking_feedback_without_complete_execution_is_not_confirmed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task, handlers = self._task_and_handlers(root)
            item = FeedbackItem(
                feedback_id="unproven-csim-failure",
                stage=FeedbackStage.CSIM,
                category=FeedbackCategory.FUNCTIONAL_MISMATCH,
                severity=FeedbackSeverity.ERROR,
                owner=FeedbackOwner.CANDIDATE,
                summary="candidate mismatch",
                metadata={"evidence_complete": True},
            )
            handler = handlers[ValidationState.PUBLIC_EVALUATION]
            handler._report = safe_report(
                "test_evaluation",
                metadata={
                    "evaluation_split": "public",
                    "feedback_visible_to_agent": True,
                    "physical_execution": False,
                    "declared_suite_ids": ["public-suite"],
                    "attempted_suite_ids": ["public-suite"],
                    "stopped_early": False,
                },
                evidence={"failure": "unverified"},
            )
            handler._report = FeedbackReport(
                report_id=handler._report.report_id,
                source=handler._report.source,
                items=(item,),
                source_evidence=handler._report.source_evidence,
                metadata=handler._report.metadata,
            )
            handler._receipt_path = None
            store = RootRunRecordStore(root / "record", "unproven-run")
            recorder = ValidationEvidenceRecorder(
                store,
                task=task,
                run_id="unproven-run",
                validation_id="unproven-validation",
            )
            recorder.start_session((ValidationState.PUBLIC_EVALUATION,))
            capture = recorder.start_stage(
                state=ValidationState.PUBLIC_EVALUATION,
                step_id="unproven-validation.step.1",
                handler=handler,
            )
            recorder.finish_stage(capture, report=handler(None))

            result = next(
                item
                for item in store.validation_evidence()
                if item.get("execution_status") != "started"
            )
            self.assertIsNone(result["evidence_verdict"])
            self.assertFalse(result["result_source_complete"])
            self.assertEqual(result["execution_status"], "blocked_before_launch")

    def test_complete_csim_failure_is_confirmed_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task, handlers = self._task_and_handlers(root)
            item = FeedbackItem(
                feedback_id="confirmed-csim-failure",
                stage=FeedbackStage.CSIM,
                category=FeedbackCategory.FUNCTIONAL_MISMATCH,
                severity=FeedbackSeverity.ERROR,
                owner=FeedbackOwner.CANDIDATE,
                summary="candidate mismatch",
                metadata={
                    "evidence_complete": True,
                    "tool_launched": True,
                },
            )
            handler = handlers[ValidationState.PUBLIC_EVALUATION]
            handler._report = FeedbackReport(
                report_id="confirmed-csim-report",
                source="test_evaluation",
                items=(item,),
                source_evidence={"suite": {"suite_id": "public-suite"}},
                metadata={
                    "evidence_view": "agent_safe",
                    "evaluation_split": "public",
                    "feedback_visible_to_agent": True,
                    "physical_execution": True,
                    "native_vitis_csim": True,
                    "declared_suite_ids": ["public-suite"],
                    "attempted_suite_ids": ["public-suite"],
                    "stopped_early": False,
                },
            )
            store = RootRunRecordStore(root / "record", "confirmed-run")
            recorder = ValidationEvidenceRecorder(
                store,
                task=task,
                run_id="confirmed-run",
                validation_id="confirmed-validation",
            )
            recorder.start_session((ValidationState.PUBLIC_EVALUATION,))
            capture = recorder.start_stage(
                state=ValidationState.PUBLIC_EVALUATION,
                step_id="confirmed-validation.step.1",
                handler=handler,
            )
            recorder.finish_stage(capture, report=handler(None))

            result = next(
                item
                for item in store.validation_evidence()
                if item.get("execution_status") == "completed"
            )
            self.assertEqual(result["capture_status"], "complete")
            self.assertTrue(result["result_source_complete"])
            self.assertEqual(result["evidence_verdict"], "confirmed_failed")

    def test_unexecuted_and_not_applicable_are_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = TaskSpec(
                task_id="no-public-task",
                kernel_path=str(root / "kernel.cpp"),
                kernel_name="top",
                test_suites=(),
            )
            store = RootRunRecordStore(root / "record", "unexecuted-run")
            recorder = ValidationEvidenceRecorder(
                store,
                task=task,
                run_id="unexecuted-run",
                validation_id="unexecuted-validation",
            )
            recorder.start_session(())
            recorder.record_unexecuted(
                state=ValidationState.PUBLIC_EVALUATION,
                reason="no_public_suite",
                not_applicable=True,
            )
            not_applicable = store.validation_evidence()[0]
            self.assertEqual(not_applicable["execution_status"], "not_applicable")
            self.assertIsNone(not_applicable["evidence_verdict"])

            recorder.record_unexecuted(
                state=ValidationState.PUBLIC_COSIM,
                reason="stopped_before_cosim",
            )
            not_run = store.validation_evidence()[1]
            self.assertEqual(not_run["execution_status"], "not_run")
            self.assertIsNone(not_run["evidence_verdict"])

    def test_policy_skipped_cosim_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task, handlers = self._task_and_handlers(root)
            cosim = handlers[ValidationState.PUBLIC_COSIM]
            cosim._report = safe_report(
                "public_rtl_cosim",
                metadata={
                    "evaluation_split": "public",
                    "cosim_skipped": True,
                    "cosim_skip_reason": "policy_disabled",
                    "physical_execution": False,
                    "declared_suite_ids": ["public-suite"],
                    "attempted_suite_ids": [],
                },
            )
            cosim._receipt_path = None
            store = RootRunRecordStore(root / "record", "skipped-cosim-run")
            context = RunContext(
                run_id="skipped-cosim-run",
                task=task,
                budget=BudgetManager(BudgetLimits()),
                trace=TraceRecorder(
                    "skipped-cosim-run", task_id=task.task_id
                ),
                root_record=store,
            )

            result = ValidationOrchestrator(handlers).run_detailed(
                context,
                validation_id="skipped-cosim-validation",
            )

            self.assertTrue(result.result.accepted)
            cosim_result = next(
                item
                for item in store.validation_evidence()
                if item["stage"] == "cosim"
                and item["execution_status"] != "started"
            )
            self.assertEqual(cosim_result["execution_status"], "not_run")
            self.assertIsNone(cosim_result["evidence_verdict"])

    def test_stage_contract_records_and_fingerprints_consumed_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task, handlers = self._task_and_handlers(root)
            store = RootRunRecordStore(root / "record", "contract-run")
            handler = handlers[ValidationState.PUBLIC_EVALUATION]
            recorder = ValidationEvidenceRecorder(
                store,
                task=task,
                run_id="contract-run",
                validation_id="contract-validation-1",
            )
            recorder.start_session((ValidationState.PUBLIC_EVALUATION,))
            first_capture = recorder.start_stage(
                state=ValidationState.PUBLIC_EVALUATION,
                step_id="contract-validation-1.step.1",
                handler=handler,
            )
            first = store.validation_evidence()[0]
            contract = first["stage_contract"]
            self.assertEqual(contract["business_stage"], "csim")
            self.assertEqual(
                contract["inputs"]["candidate_top_function"], "top"
            )
            self.assertEqual(
                contract["inputs"]["execution_backend"], "native_vitis"
            )
            self.assertIn("timelimit", contract["inputs"])
            self.assertEqual(
                contract["public_suites"][0]["suite_id"], "public-suite"
            )
            self.assertTrue(
                {
                    "candidate_code",
                    "original_code",
                    "public_suite_testbench",
                }.issubset(
                    {
                        item["role"]
                        for item in first["input_snapshot"]["input_contents"]
                    }
                )
            )

            handler.inputs.candidate_code = "int top() { return 3; }"
            handler.inputs.candidate_top_function = "renamed_top"
            handler.inputs.suite_testbench_codes["public-suite"] = (
                "int changed_public_tb() { return 0; }"
            )
            recorder2 = ValidationEvidenceRecorder(
                store,
                task=task,
                run_id="contract-run",
                validation_id="contract-validation-2",
            )
            recorder2.start_session((ValidationState.PUBLIC_EVALUATION,))
            second_capture = recorder2.start_stage(
                state=ValidationState.PUBLIC_EVALUATION,
                step_id="contract-validation-2.step.1",
                handler=handler,
            )
            second = next(
                item
                for item in store.validation_evidence()
                if item["evidence_id"] == second_capture.started_evidence_id
            )
            self.assertNotEqual(
                first_capture.snapshot_sha256,
                second_capture.snapshot_sha256,
            )
            self.assertNotEqual(
                first["stage_contract_sha256"],
                second["stage_contract_sha256"],
            )

    def test_capture_start_failure_is_persisted_without_changing_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task, handlers = self._task_and_handlers(root)
            record_root = root / "record"
            store = RootRunRecordStore(record_root, "capture-failure-run")
            outside = root / "outside-artifacts"
            outside.mkdir()
            (record_root / "validation_artifacts").symlink_to(
                outside,
                target_is_directory=True,
            )
            context = RunContext(
                run_id="capture-failure-run",
                task=task,
                budget=BudgetManager(BudgetLimits()),
                trace=TraceRecorder(
                    "capture-failure-run", task_id=task.task_id
                ),
                root_record=store,
            )

            result = ValidationOrchestrator(handlers).run_detailed(
                context,
                validation_id="capture-failure-validation",
            )

            self.assertTrue(result.result.accepted)
            evidence = store.validation_evidence()
            failures = [
                item
                for item in evidence
                if item.get("capture_status") == "failed"
            ]
            self.assertEqual(len(failures), 4)
            self.assertTrue(
                all(item["evidence_verdict"] is None for item in failures)
            )
            self.assertTrue(
                all(
                    item["capture_errors"]
                    == ["stage_start_capture_failed:ValueError"]
                    for item in failures
                )
            )

    def test_evidence_dispositions_append_without_overwriting_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task, handlers = self._task_and_handlers(root)
            handlers[ValidationState.PUBLIC_EVALUATION]._receipt_payload = {}
            store = RootRunRecordStore(root / "record", "disposition-run")
            context = RunContext(
                run_id="disposition-run",
                task=task,
                budget=BudgetManager(BudgetLimits()),
                trace=TraceRecorder("disposition-run", task_id=task.task_id),
                root_record=store,
            )
            ValidationOrchestrator(handlers).run_detailed(
                context,
                validation_id="disposition-validation",
            )
            csim = next(
                item
                for item in store.validation_evidence()
                if item["stage"] == "csim"
                and item["execution_status"] == "completed"
            )
            recorder = ValidationEvidenceRecorder(
                store,
                task=task,
                run_id="disposition-run",
                validation_id="disposition-validation",
            )
            recorder.record_evidence_disposition(
                source_evidence_id=csim["evidence_id"],
                disposition_id="case-needs-evidence",
                evidence_verdict="awaiting_post_run_evidence",
                reason_code="required_for_case_closure",
            )
            recorder.record_evidence_disposition(
                source_evidence_id=csim["evidence_id"],
                disposition_id="source-exhausted",
                evidence_verdict="unverifiable",
                reason_code="no_replay_source_available",
            )

            records = store.validation_evidence()
            original = next(
                item
                for item in records
                if item["evidence_id"] == csim["evidence_id"]
            )
            dispositions = [
                item
                for item in records
                if item.get("disposition_of_evidence_id")
                == csim["evidence_id"]
            ]
            self.assertIsNone(original["evidence_verdict"])
            self.assertEqual(
                [item["evidence_verdict"] for item in dispositions],
                ["awaiting_post_run_evidence", "unverifiable"],
            )
            self.assertEqual(len(dispositions), 2)
            self.assertTrue(
                all(
                    item["record_type"] == "evidence_disposition"
                    for item in dispositions
                )
            )

    def test_missing_toolchain_version_keeps_result_unqualified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task, handlers = self._task_and_handlers(root)
            handlers[
                ValidationState.PUBLIC_EVALUATION
            ]._receipt_payload = {}
            store = RootRunRecordStore(root / "record", "run-3")
            context = RunContext(
                run_id="run-3",
                task=task,
                budget=BudgetManager(BudgetLimits()),
                trace=TraceRecorder("run-3", task_id=task.task_id),
                root_record=store,
            )
            result = ValidationOrchestrator(handlers).run_detailed(
                context,
                validation_id="validation-3",
            )
            self.assertTrue(result.result.accepted)
            csim_result = next(
                item
                for item in store.validation_evidence()
                if item["stage"] == "csim"
                and item["execution_status"] == "completed"
            )
            self.assertIsNone(csim_result["evidence_verdict"])
            self.assertFalse(
                csim_result["environment_observation"]["verified"]
            )

    def test_execution_receipts_are_sanitized_before_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task, handlers = self._task_and_handlers(root)
            private_path = str(root / "private" / "settings.json")
            handlers[ValidationState.PREFLIGHT]._receipt_payload = {
                "compiler": "/bin/true",
                "execution": {"status": "completed", "returncode": 0},
                "api_token": "receipt-secret-value",
                "private_working_directory": private_path,
            }
            store = RootRunRecordStore(root / "record", "safe-receipt-run")
            context = RunContext(
                run_id="safe-receipt-run",
                task=task,
                budget=BudgetManager(BudgetLimits()),
                trace=TraceRecorder(
                    "safe-receipt-run", task_id=task.task_id
                ),
                root_record=store,
            )

            ValidationOrchestrator(handlers).run_detailed(
                context,
                validation_id="safe-receipt-validation",
            )

            preflight = next(
                item
                for item in store.validation_evidence()
                if item["stage"] == "preflight"
                and item.get("output_artifacts")
            )
            receipt = next(
                item
                for item in preflight["output_artifacts"]
                if item["logical_path"].endswith(
                    "testbench_preflight_invocation.json"
                )
            )
            payload = store.read_validation_artifact(
                receipt["artifact_ref"]
            ).decode("utf-8")
            self.assertNotIn("receipt-secret-value", payload)
            self.assertNotIn("api_token", payload)
            self.assertNotIn(private_path, payload)
            self.assertIn("compiler_identity", json.loads(payload))

    def test_source_package_manifest_captures_all_regular_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package_root = root / "source"
            package_root.mkdir()
            target = package_root / "kernel.cpp"
            header = package_root / "include" / "kernel.hpp"
            extra = package_root / "notes.dat"
            hidden_tb = package_root / "hidden_tb.cpp"
            linked_header = package_root / "linked.hpp"
            header.parent.mkdir()
            target.write_bytes(b"original target")
            header.write_bytes(b"header bytes")
            extra.write_bytes(b"non-target package file")
            hidden_tb.write_bytes(HIDDEN_PAYLOAD.encode())
            linked_header.symlink_to(header)
            package = SourcePackageSpec(
                root=package_root,
                target=target,
                extra_sources=(),
            )
            task = TaskSpec(
                task_id="package-capture-task",
                kernel_path=str(target),
                kernel_name="top",
                source_package=package,
                test_suites=(
                    TestSuiteSpec(
                        suite_id="public-suite",
                        split=EvaluationSplit.PUBLIC,
                    ),
                TestSuiteSpec(
                    suite_id="hidden-suite",
                    split=EvaluationSplit.HIDDEN,
                    testbench_path="hidden_tb.cpp",
                ),
                ),
            )
            work_dir = root / "preflight"
            package.stage_into(work_dir)
            inputs = StageInputs(
                work_dir=work_dir,
                candidate_code="int top() { return 2; }",
                original_code="int top() { return 1; }",
                testbench_code="int preflight_tb() { return 0; }",
                suite_testbench_codes={
                    "public-suite": "int public_tb() { return 0; }"
                },
                target_profile=task.target,
            )
            store = RootRunRecordStore(root / "record", "package-run")
            recorder = ValidationEvidenceRecorder(
                store,
                task=task,
                run_id="package-run",
                validation_id="package-validation",
            )
            recorder.start_session((ValidationState.PREFLIGHT,))
            recorder.start_stage(
                state=ValidationState.PREFLIGHT,
                step_id="package-validation.step.1",
                handler=FakeHandler(
                    inputs,
                    safe_report("testbench_preflight"),
                ),
            )
            started = store.validation_evidence()[0]
            manifest = started["input_snapshot"]["source_package_files"]
            self.assertEqual(
                {item["logical_path"] for item in manifest},
                {"kernel.cpp", "include/kernel.hpp", "notes.dat"},
            )
            self.assertFalse(started["input_snapshot"]["complete"])
            self.assertFalse(started["input_snapshot"]["replay_eligible"])
            recovered = {
                item["logical_path"]: store.read_validation_artifact(
                    item["artifact_ref"]
                )
                for item in manifest
            }
            self.assertEqual(recovered["kernel.cpp"], b"original target")
            self.assertEqual(
                recovered["include/kernel.hpp"],
                b"header bytes",
            )
            self.assertEqual(
                recovered["notes.dat"],
                b"non-target package file",
            )
            self.assertNotIn("hidden_tb.cpp", recovered)
            self.assertFalse(
                any(
                    HIDDEN_PAYLOAD.encode() in store.read_validation_artifact(
                        item["artifact_ref"]
                    )
                    for item in manifest
                )
            )


if __name__ == "__main__":
    unittest.main()
