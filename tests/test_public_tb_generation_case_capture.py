from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agrefactor.evidence import CaseRecorder, CaseStore, CaseStatus
from flow.tools import tb_optimizer


class PublicGenerationCaseCaptureTests(unittest.TestCase):
    def test_candidate_route_and_ledger_decisions_are_separate_snapshots(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "cases"
            recorder = CaseRecorder(root, "validation")
            self.assertTrue(recorder.start_case(self.event("diagnostic-1")))
            route = SimpleNamespace(
                decision_id="route-1",
                action=SimpleNamespace(value="repair_candidate"),
                reason="one repair owner is established",
                source_report_id="report-1",
                selected_feedback_ids=("feedback-1",),
            )
            ledger_event = SimpleNamespace(
                to_dict=lambda: {
                    "sequence": 4,
                    "accepted": False,
                    "count_key": "lineage:run:candidate:total",
                    "decision": {
                        "status": "denied",
                        "reason_code": "recovery_limit_exhausted",
                        "stage": "public_csynth",
                        "evidence_view": "agent_safe",
                    },
                }
            )

            self.assertTrue(
                recorder.record_candidate_dispatch(1, "int top(){}", route)
            )
            self.assertTrue(recorder.record_candidate_ledger_event(1, ledger_event))

            case = CaseStore(root).read_case(CaseStore(root).open_cases()[0].case_id)
            attempt = case.attempts[0]
            eligibility = next(
                item
                for item in case.eligibility_snapshots
                if item.decision_ref == attempt.authorization_ref
            )
            store = CaseStore(root)
            self.assertEqual(eligibility.authorization_ref, "route-1")
            self.assertEqual(
                store.read_decision(case.case_id, "route-1")["decision_type"],
                "feedback_route",
            )
            ledger = store.read_decision(case.case_id, attempt.ledger_event_ref)
            self.assertEqual(ledger["decision_type"], "recovery_ledger_event")
            self.assertFalse(ledger["ledger_event"]["accepted"])

    def test_public_trajectories_share_case_and_record_component_diffs(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "cases"
            experience_root = Path(directory) / "experiences"
            recorder = CaseRecorder(
                root,
                "generation-run",
                case_scope_id="generation-run",
                experience_root=experience_root,
            )

            def record_trajectory(index, component):
                diagnostic = self.public_event(
                    f"diagnostic-{index}",
                    event_kind="coverage_validation",
                    trajectory_idx=index,
                    owner=component,
                    accepted=False,
                    diagnostic_terms=("coverage_failed",),
                )
                recorder.record_public_generation_diagnostic(diagnostic)
                attempt_key = f"trajectory:{index}:action:1"
                request = self.public_event(
                    f"request-{index}",
                    event_kind="provider_request",
                    trajectory_idx=index,
                    request_role="repair",
                    dispatched=True,
                )
                recorder.record_public_generation_request(
                    request,
                    attempt_key=attempt_key,
                    component=component,
                    authorization={"repair_eligible": True, "split": "public"},
                )
                proposal = self.public_event(
                    f"proposal-{index}",
                    event_kind="artifact_proposal",
                    trajectory_idx=index,
                    request_role="repair",
                    dispatched=True,
                )
                before = f"int {component}() {{ return 0; }}\n"
                after = f"int {component}() {{ return {index + 1}; }}\n"
                recorder.record_public_generation_request(
                    proposal,
                    attempt_key=attempt_key,
                    component=component,
                    before_code=before,
                    after_code=after,
                    authorization={"repair_eligible": True, "split": "public"},
                )
                validation = self.public_event(
                    f"validation-{index}",
                    event_kind="coverage_validation",
                    trajectory_idx=index,
                    stage="public_generation_qualification",
                    accepted=True,
                )
                self.assertTrue(
                    recorder.record_public_generation_validation(
                        attempt_key, validation, result="validated"
                    )
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(lambda pair: record_trajectory(*pair), ((0, "testbench"), (1, "stub"))))

            self.assertTrue(recorder.close_case("resolved", "qualified"))
            store = CaseStore(root)
            cases = list(root.glob("*/case.json"))
            self.assertEqual(len(cases), 1)
            case = store.read_case(cases[0].parent.name)
            self.assertEqual(case.status, CaseStatus.CLOSED)
            self.assertEqual(len(case.attempts), 2)
            self.assertEqual(
                {bucket for attempt in case.attempts for bucket in attempt.quota_buckets},
                {"testbench_public", "stub_public"},
            )
            changes = store.read_changes(case.case_id)
            self.assertEqual(len(changes), 2)
            self.assertEqual(
                {component for change in changes.values() for component in change.components},
                {"testbench", "stub"},
            )
            self.assertEqual(len(list(experience_root.glob("records/*.json"))), 2)

    def test_format_retry_is_only_a_request_event_and_hidden_is_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "cases"
            recorder = CaseRecorder(root, "generation-run", case_scope_id="generation-run")
            recorder.record_public_generation_request(
                self.public_event(
                    "request-format",
                    event_kind="provider_request",
                    request_role="format_retry",
                    dispatched=True,
                )
            )
            self.assertFalse(
                recorder.record_public_generation_diagnostic(
                    {
                        **self.public_event("hidden-diagnostic", event_kind="diagnostic"),
                        "split": "hidden",
                    }
                )
            )
            recorder.record_public_generation_diagnostic(
                self.public_event("diagnostic-public", event_kind="diagnostic")
            )
            case_id = CaseStore(root).open_cases()[0].case_id
            case = CaseStore(root).read_case(case_id)
            self.assertEqual(case.attempts, ())
            self.assertIn("request-format", CaseStore(root).read_events(case_id))

    def test_dispatched_component_is_charged_without_a_code_diff(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "cases"
            recorder = CaseRecorder(root, "generation-run", case_scope_id="generation-run")
            recorder.record_public_generation_diagnostic(
                self.public_event("diagnostic-no-diff", event_kind="diagnostic")
            )
            attempt_key = "trajectory:0:action:1"
            recorder.record_public_generation_request(
                self.public_event(
                    "request-no-diff",
                    event_kind="provider_request",
                    request_role="repair",
                    dispatched=True,
                ),
                attempt_key=attempt_key,
                component="testbench",
            )
            recorder.record_public_generation_request(
                self.public_event(
                    "request-stub-no-diff",
                    event_kind="provider_request",
                    request_role="repair",
                    dispatched=True,
                ),
                attempt_key=attempt_key,
                component="stub",
            )
            recorder.record_public_generation_request(
                self.public_event(
                    "proposal-no-diff",
                    event_kind="artifact_proposal",
                    request_role="repair",
                    dispatched=True,
                ),
                attempt_key=attempt_key,
                component="testbench",
                before_code="int tb() { return 0; }\n",
                after_code="int tb() { return 0; }\n",
            )
            recorder.record_public_generation_validation(
                attempt_key,
                self.public_event(
                    "validation-no-diff",
                    event_kind="coverage_validation",
                    accepted=False,
                ),
                result="validation_failed",
            )
            case_id = CaseStore(root).open_cases()[0].case_id
            attempt = CaseStore(root).read_case(case_id).attempts[0]

            self.assertEqual(
                set(attempt.quota_buckets),
                {"testbench_public", "stub_public"},
            )
            self.assertFalse(attempt.changed)

    def test_finalizer_reports_empty_stub_retry_and_synthesis_result(self):
        proposals = []
        validations = []
        rounds = [
            {
                "status": "ok",
                "cov_pct": 80.0,
                "round": 1,
                "tb_code": "int top_hls(int x) { return x; }",
                "stub_code": "int top_hls(int x) { return x; }",
                "frozen_public_hls_decl": "int top_hls(int x)",
                "frozen_public_type_contract": {"types": []},
            }
        ]

        def observe_proposal(component, role, code, *, contract_valid, artifact_kind=None):
            proposals.append((component, role, code, contract_valid, artifact_kind))

        with (
            patch.object(
                tb_optimizer,
                "_request_cpp_artifact",
                side_effect=(
                    "int top_hls(int x) { return x; }",
                    "int top_hls(int x) { return x + 1; }",
                ),
            ),
            patch.object(tb_optimizer, "validate_stub_contract"),
            patch.object(tb_optimizer, "freeze_public_type_contract", return_value={"types": []}),
            patch.object(
                tb_optimizer,
                "_synth_check",
                side_effect=((False, "probe failed"), (True, "")),
            ),
        ):
            result = tb_optimizer._finalize_trajectory(
                agent=None,
                rounds=rounds,
                want_sig_spec=False,
                trajectory_idx=0,
                expected_hls_name="top_hls",
                orig_code="int top(int x) { return x; }",
                emit_final_text=False,
                synth_retry_budget=1,
                allow_abi_correction=False,
                on_artifact_proposal=observe_proposal,
                on_qualification_result=lambda kind, value: validations.append(
                    (kind, value["status"], value["accepted"])
                ),
            )

        self.assertTrue(result["qualified"])
        self.assertEqual(
            [
                (component, role, artifact_kind or component)
                for component, role, _, _, artifact_kind in proposals
            ],
            [("stub", "initial", "empty_stub"), ("stub", "repair", "empty_stub")],
        )
        self.assertEqual(
            validations,
            [("synth_validation", "synth_failed", False), ("synth_validation", "ok", True)],
        )

    def test_finalizer_reports_joint_testbench_and_stub_repair(self):
        proposals = []
        validations = []
        rounds = [
            {
                "status": "ok",
                "cov_pct": 80.0,
                "round": 1,
                "tb_code": "int top_hls(int x) { return x; }",
                "stub_code": "int top_hls(int x) { return x; }",
                "frozen_public_hls_decl": "int top_hls(int x)",
                "frozen_public_type_contract": {"types": []},
            }
        ]

        def observe_proposal(component, role, code, *, contract_valid, artifact_kind=None):
            proposals.append((component, role, code, contract_valid, artifact_kind))

        def append_round(target_rounds, **kwargs):
            record = {
                "status": "ok",
                "cov_pct": 90.0,
                "round": kwargs["round_index"],
                "tb_code": kwargs["tb_code"],
                "stub_code": kwargs["stub_code"],
                "frozen_public_hls_decl": kwargs["frozen_public_hls_decl"],
                "frozen_public_type_contract": kwargs["frozen_public_type_contract"],
                "uncovered_lines": [],
            }
            target_rounds.append(record)
            return record

        artifacts = (
            "int top_hls(int x) { return x; }",
            "int top_hls(int x) { return x + 1; }",
            "int top_hls(int x) { return x + 2; }",
            "int top_hls(int x) { return x; }",
        )
        with (
            patch.object(tb_optimizer, "_request_cpp_artifact", side_effect=artifacts),
            patch.object(tb_optimizer, "validate_stub_contract"),
            patch.object(tb_optimizer, "validate_testbench_top_contract"),
            patch.object(tb_optimizer, "extract_hls_decl_from_testbench", return_value="int top_hls(int x)"),
            patch.object(tb_optimizer, "freeze_public_type_contract", return_value={"types": []}),
            patch.object(tb_optimizer, "_ensure_original_forward_declaration", side_effect=lambda code, *args, **kwargs: code),
            patch.object(tb_optimizer, "_synth_check", side_effect=((False, "probe failed"), (True, ""))),
            patch.object(tb_optimizer, "_measure_qualified_coverage", return_value={"status": "ok"}),
            patch.object(tb_optimizer, "_freeze_public_contract", return_value=("int top_hls(int x)", ())),
            patch.object(tb_optimizer, "_append_round", side_effect=append_round),
            patch.object(tb_optimizer, "_failure_history", return_value="prior failure"),
        ):
            result = tb_optimizer._finalize_trajectory(
                agent=None,
                rounds=rounds,
                want_sig_spec=False,
                trajectory_idx=0,
                expected_hls_name="top_hls",
                orig_code="int top(int x) { return x; }",
                emit_final_text=False,
                synth_retry_budget=1,
                allow_abi_correction=True,
                on_artifact_proposal=observe_proposal,
                on_qualification_result=lambda kind, value: validations.append(
                    (kind, value["status"], value["accepted"])
                ),
            )

        self.assertTrue(result["qualified"])
        self.assertEqual(
            [
                (component, role, artifact_kind or component)
                for component, role, _, _, artifact_kind in proposals
            ],
            [
                ("stub", "initial", "empty_stub"),
                ("testbench", "repair", "testbench"),
                ("stub", "repair", "stub"),
                ("stub", "repair", "empty_stub"),
            ],
        )
        self.assertEqual(
            validations,
            [
                ("synth_validation", "synth_failed", False),
                ("coverage_validation", "ok", True),
                ("synth_validation", "ok", True),
            ],
        )

    def test_optimizer_passes_one_recorder_to_concurrent_trajectories(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "cases"
            recorder = CaseRecorder(root, "optimizer-run", case_scope_id="optimizer-run")

            def fake_trajectory(**kwargs):
                trajectory_idx = kwargs["trajectory_idx"]
                current = kwargs["public_case_recorder"]
                self.assertIs(current, recorder)
                current.record_public_generation_diagnostic(
                    self.public_event(
                        f"optimizer-diagnostic-{trajectory_idx}",
                        event_kind="coverage_validation",
                        trajectory_idx=trajectory_idx,
                        owner="testbench",
                        accepted=False,
                    )
                )
                attempt_key = f"trajectory:{trajectory_idx}:action:1"
                current.record_public_generation_request(
                    self.public_event(
                        f"optimizer-request-{trajectory_idx}",
                        event_kind="provider_request",
                        trajectory_idx=trajectory_idx,
                        request_role="repair",
                        dispatched=True,
                    ),
                    attempt_key=attempt_key,
                    component="testbench",
                    authorization={"repair_eligible": True, "split": "public"},
                )
                current.record_public_generation_request(
                    self.public_event(
                        f"optimizer-proposal-{trajectory_idx}",
                        event_kind="artifact_proposal",
                        trajectory_idx=trajectory_idx,
                        request_role="repair",
                        dispatched=True,
                    ),
                    attempt_key=attempt_key,
                    component="testbench",
                    before_code="int tb() { return 0; }\n",
                    after_code=f"int tb() {{ return {trajectory_idx + 1}; }}\n",
                    authorization={"repair_eligible": True, "split": "public"},
                )
                current.record_public_generation_validation(
                    attempt_key,
                    self.public_event(
                        f"optimizer-validation-{trajectory_idx}",
                        event_kind="coverage_validation",
                        trajectory_idx=trajectory_idx,
                        stage="public_generation_qualification",
                        accepted=True,
                    ),
                    result="validated",
                )
                return {
                    "trajectory_idx": trajectory_idx,
                    "qualified": True,
                    "synth_ok": True,
                    "best_tb": "int tb() { return 1; }",
                    "best_stub": "",
                    "best_cov": 90.0,
                    "best_round": 1,
                    "rounds": [],
                    "final_text": "instruction",
                    "trajectory_status": "qualified",
                }

            with patch.object(tb_optimizer, "run_trajectory", side_effect=fake_trajectory):
                result = tb_optimizer.optimize_tb_public(
                    "int top() { return 0; }",
                    "top",
                    M=2,
                    public_case_recorder=recorder,
                    generation_id="optimizer-run",
                )

            self.assertEqual(len(result["trajectories"]), 2)
            case_files = list(root.glob("*/case.json"))
            self.assertEqual(len(case_files), 1)
            case = CaseStore(root).read_case(case_files[0].parent.name)
            self.assertEqual(len(case.attempts), 2)
            self.assertEqual(case.status, CaseStatus.CLOSED)

    @staticmethod
    def event(event_id):
        return {
            "event_id": event_id,
            "run_id": "root-run",
            "validation_id": "validation",
            "stage": "csynth",
            "owner": "candidate",
            "route_action": "repair_candidate",
            "evidence_refs": ["report:1"],
            "evidence_view": "agent_safe",
            "accepted": False,
        }

    @staticmethod
    def public_event(event_id, *, event_kind, trajectory_idx=0, **fields):
        return {
            "event_id": event_id,
            "run_id": "generation-run",
            "validation_id": "generation-run",
            "trajectory_idx": trajectory_idx,
            "stage": "public_testbench_generation",
            "split": "public",
            "component": "testbench",
            "event_kind": event_kind,
            "request_role": "initial",
            "dispatched": False,
            "outcome": "test",
            "evidence_refs": [f"evidence:{event_id}"],
            "summary": "safe event",
            "evidence_view": "agent_safe",
            "hidden_content_persisted": False,
            **fields,
        }


if __name__ == "__main__":
    unittest.main()
