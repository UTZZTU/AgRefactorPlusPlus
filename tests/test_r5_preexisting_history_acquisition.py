from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from agrefactor.recovery.r5_historical_candidate import (
    R5HistoricalCandidateBundle,
    canonical_sha256,
    file_sha256,
)


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "r5_acquire_preexisting_history",
    ROOT / "scripts" / "r5_acquire_preexisting_history.py",
)
assert SPEC is not None and SPEC.loader is not None
ACQUIRE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ACQUIRE)


class R5PreexistingHistoryAcquisitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.plan_path = self.root / "plan.json"
        self.audit_path = self.root / "audit.json"
        self.plan_path.write_text("{}\n", encoding="utf-8")
        self.audit_path.write_text("{}\n", encoding="utf-8")
        candidate = R5HistoricalCandidateBundle(
            case_id="history-case",
            reference_top="top",
            candidate_top="top_hls",
            paths={
                "reference": "reference.cpp",
                "legacy_candidate": "candidate.cpp",
                "public_test": "public.cpp",
                "hidden_test": "hidden.cpp",
            },
            file_sha256s={
                "reference": "1" * 64,
                "legacy_candidate": "2" * 64,
                "public_test": "3" * 64,
                "hidden_test": "4" * 64,
            },
            public_runtime_contract={
                "schema_version": 2,
                "kind": "public_differential_self_check_v1",
                "candidate_mismatch_returncodes": [1],
                "cosim_interface_depths": {"out": 1},
            },
            reference_code="void top(int*);\n",
            legacy_candidate_code="void top(int*);\n",
            isolated_candidate_code="void top_hls(int*);\n",
            public_test_code="int main(){return 0;}\n",
            hidden_test_code="int main(){return 0;}\n",
            source_sha256="1" * 64,
            isolated_candidate_sha256="5" * 64,
            plan_sha256="6" * 64,
        )
        self.preflight = {
            "repository_head": "a" * 40,
            "candidate": candidate,
            "plan_path": self.plan_path,
            "audit_path": self.audit_path,
            "audit": {"audit_sha256": "7" * 64},
            "calibration_path": self.plan_path,
            "certificate": type(
                "Certificate",
                (),
                {"certificate_id": "certificate-id"},
            )(),
            "model_runtime": {
                "provider": "openai-compatible",
                "model": "deepseek-flash",
                "family": "deepseek",
                "base_url": "https://api.deepseek.com",
                "api_key_env": "DEEPSEEK_API_KEY",
                "request_parameters": {"temperature": 0.0},
            },
            "plan": {
                "future_holdout_case_ids": ["future-a", "future-b"],
            },
        }

    def test_manifest_is_content_addressed_and_memory_free(self) -> None:
        first = ACQUIRE.build_acquisition_manifest(
            self.preflight,
            run_id="history-run",
        )
        second = ACQUIRE.build_acquisition_manifest(
            self.preflight,
            run_id="history-run",
        )
        self.assertEqual(first, second)
        stored = first.pop("manifest_sha256")
        self.assertEqual(stored, canonical_sha256(first))
        self.assertEqual(first["arm"], "A2")
        self.assertEqual(first["authorization_mode"], "advisor_only")
        self.assertEqual(first["memory_mode"], "none")
        self.assertEqual(first["provider_call_upper_bound"], 2)
        self.assertEqual(first["vitis_launch_upper_bound"], 6)
        self.assertFalse(first["future_files_read"])

    def test_task_preserves_frozen_public_runtime_contract(self) -> None:
        candidate = self.preflight["candidate"]
        task = ACQUIRE._task(
            repository=self.root,
            candidate=candidate,
            run_id="history-runtime-contract",
        )
        public = next(
            suite for suite in task.test_suites if suite.split.value == "public"
        )
        self.assertEqual(public.runtime_contract["schema_version"], 2)
        self.assertEqual(
            public.runtime_contract["cosim_interface_depths"],
            {"out": 1},
        )

    def test_manifest_uses_audited_continuation_budget(self) -> None:
        preflight = dict(self.preflight)
        preflight["budget"] = {
            "provider_calls_before": 94,
            "vitis_launches_before": 35,
            "provider_call_upper_bound": 2,
            "vitis_launch_upper_bound": 6,
        }
        preflight["continuation"] = {
            "attempt_ordinal": 2,
            "stop_after_first_verified_positive": True,
        }
        manifest = ACQUIRE.build_acquisition_manifest(
            preflight,
            run_id="history-continuation-02",
        )
        self.assertEqual(manifest["provider_calls_before"], 94)
        self.assertEqual(manifest["vitis_launches_before"], 35)
        self.assertEqual(manifest["provider_call_upper_bound"], 2)
        self.assertEqual(manifest["vitis_launch_upper_bound"], 6)
        self.assertEqual(manifest["continuation"]["attempt_ordinal"], 2)
        unsigned = dict(manifest)
        stored = unsigned.pop("manifest_sha256")
        self.assertEqual(stored, canonical_sha256(unsigned))

    def test_protocol_audit_must_bind_current_head_and_files(self) -> None:
        state_path = self.root / "state.json"
        state_path.write_text("{}\n", encoding="utf-8")
        audit = {
            "status": "ready_for_preexisting_history_acquisition",
            "repository_head": "a" * 40,
            "plan_file_sha256": file_sha256(self.plan_path),
            "state_file_sha256": file_sha256(state_path),
            "provider_calls": 0,
            "vitis_launches": 0,
            "provider_call_upper_bound": 2,
            "vitis_launch_upper_bound": 6,
            "source_independence_verified": True,
            "future_files_read": False,
            "future_outcomes_observed": False,
            "critical_finding_count": 0,
            "findings": [],
        }
        audit["audit_sha256"] = canonical_sha256(audit)
        ACQUIRE._validate_protocol_audit(
            audit=audit,
            audit_path=self.audit_path,
            plan_path=self.plan_path,
            state_path=state_path,
            repository_head="a" * 40,
        )
        audit["repository_head"] = "b" * 40
        with self.assertRaisesRegex(
            ACQUIRE.PreexistingHistoryAcquisitionError,
            "protocol audit is incompatible",
        ):
            ACQUIRE._validate_protocol_audit(
                audit=audit,
                audit_path=self.audit_path,
                plan_path=self.plan_path,
                state_path=state_path,
                repository_head="a" * 40,
            )

    def test_persistence_guard_rejects_private_payloads(self) -> None:
        with self.assertRaisesRegex(
            ACQUIRE.PreexistingHistoryAcquisitionError,
            "raw private field",
        ):
            ACQUIRE._assert_safe(
                {"nested": {"raw_provider_response": "payload"}},
                secret=None,
            )
        with self.assertRaisesRegex(
            ACQUIRE.PreexistingHistoryAcquisitionError,
            "provider credential",
        ):
            ACQUIRE._assert_safe(
                {"value": "prefix-secret-suffix"},
                secret="secret",
            )

    def test_suite_ids_are_derived_from_case_identity(self) -> None:
        candidate = self.preflight["candidate"]
        public, hidden = ACQUIRE._suite_ids(candidate)
        self.assertEqual(public, "public-history-case-history")
        self.assertEqual(hidden, "hidden-history-case-history")
        self.assertNotIn("recursive-e2-dfs", public)


if __name__ == "__main__":
    unittest.main()
