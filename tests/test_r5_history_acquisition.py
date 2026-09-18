from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from agrefactor.recovery import (
    CalibrationCertificate,
    R5EpisodeEnvelope,
    R5EpisodeOutcome,
)


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "r5_history_acquisition",
    ROOT / "scripts" / "r5_history_acquisition.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _certificate() -> CalibrationCertificate:
    return CalibrationCertificate(
        split_id="split",
        split_sha256="1" * 64,
        report_sha256="2" * 64,
        policy_sha256="3" * 64,
        provider_identity_sha256="4" * 64,
        prompt_contract_version="r2-shadow-output-v4",
        strict_parser="r2-v1",
        input_contract_version="r2-agent-safe-diagnostic-evidence-v2",
        eligible_confidence_labels=("high",),
        accepted=True,
        reasons=(),
    )


def _history(case_id: str, source: str) -> dict:
    return {
        "case_id": case_id,
        "period": "history",
        "control_role": "positive",
        "expected_r2_failure_class": "unsupported_construct",
        "expected_r2_entry_boundary": "unknown_or_mixed_review",
        "deterministic_repair_expected": False,
        "outcome_observed": False,
        "hashes": {"source": source},
    }


class R5HistoryAcquisitionTests(unittest.TestCase):
    def test_run_namespace_is_stable_per_output_and_isolates_retries(self):
        first = MODULE._run_namespace(Path("/tmp/run-a"), "a" * 64)
        self.assertEqual(first, MODULE._run_namespace(Path("/tmp/run-a"), "a" * 64))
        self.assertNotEqual(
            first,
            MODULE._run_namespace(Path("/tmp/run-b"), "a" * 64),
        )

    def test_history_selection_keeps_future_outcomes_out(self):
        manifest = {
            "cases": [
                _history("h2", "2" * 64),
                {
                    "case_id": "future",
                    "period": "future",
                    "outcome_observed": False,
                },
                _history("h1", "1" * 64),
            ]
        }
        selected = MODULE._history_cases(manifest)
        self.assertEqual([item["case_id"] for item in selected], ["h1", "h2"])

    def test_history_manifest_freezes_bounded_a2_only_acquisition(self):
        contracts = {
            "repository": {"head": "a" * 40},
            "state": {
                "R5_CONSUMED_PROVIDER_CALLS": 12,
                "R5_CONSUMED_VITIS_LAUNCHES": 0,
                "R5_PROVIDER_CALL_HARD_CAP": 500,
                "R5_VITIS_LAUNCH_HARD_CAP": 500,
            },
            "file_hashes": {
                "adapter_manifest": "a" * 64,
                "protocol_audit": "b" * 64,
                "calibration_bundle": "c" * 64,
            },
            "certificate": _certificate(),
            "cases": (
                _history("h1", "1" * 64),
                _history("h2", "2" * 64),
            ),
        }
        manifest = MODULE.build_history_manifest(
            contracts,
            max_attempts_per_case=3,
        )
        self.assertEqual(manifest["arm"], "A2")
        self.assertEqual(manifest["entrypoint"], "refactor")
        self.assertEqual(manifest["provider_call_upper_bound"], 66)
        self.assertEqual(manifest["vitis_launch_upper_bound"], 48)
        self.assertEqual(manifest["provider_calls_before"], 12)
        self.assertFalse(manifest["future_outcomes_observed"])
        unsigned = dict(manifest)
        digest = unsigned.pop("manifest_sha256")
        self.assertEqual(digest, MODULE._canonical_sha256(unsigned))

    def test_continuation_manifest_authorizes_only_unobserved_case(self):
        contracts = {
            "repository": {"head": "a" * 40},
            "state": {
                "R5_CONSUMED_PROVIDER_CALLS": 66,
                "R5_CONSUMED_VITIS_LAUNCHES": 24,
                "R5_PROVIDER_CALL_HARD_CAP": 500,
                "R5_VITIS_LAUNCH_HARD_CAP": 500,
            },
            "file_hashes": {
                "adapter_manifest": "a" * 64,
                "protocol_audit": "b" * 64,
                "calibration_bundle": "c" * 64,
            },
            "certificate": _certificate(),
            "audit": {
                "status": "ready_for_history_continuation",
                "continuation_provider_upper_bound": 33,
                "continuation_vitis_upper_bound": 24,
            },
            "cases": (_history("h2", "2" * 64),),
        }
        manifest = MODULE.build_history_manifest(
            contracts,
            max_attempts_per_case=3,
        )
        self.assertEqual(manifest["history_case_ids"], ["h2"])
        self.assertEqual(manifest["provider_call_upper_bound"], 33)
        self.assertEqual(manifest["vitis_launch_upper_bound"], 24)
        self.assertEqual(manifest["protocol_audit_status"], "ready_for_history_continuation")

    def test_accepted_baseline_without_diagnostic_is_data_insufficient(self):
        capture = SimpleNamespace(
            formal_result=SimpleNamespace(
                accepted=True,
                metadata={"diagnostic_events": []},
            )
        )
        self.assertEqual(
            MODULE._baseline_diagnostic_state(capture),
            ("accepted_without_diagnostic", 0),
        )

    def test_single_diagnostic_remains_eligible_for_r2_r4(self):
        capture = SimpleNamespace(
            formal_result=SimpleNamespace(
                accepted=False,
                metadata={"diagnostic_events": [{"event_id": "d1"}]},
            )
        )
        self.assertEqual(
            MODULE._baseline_diagnostic_state(capture),
            ("eligible_single_diagnostic", 1),
        )

    def test_initial_audit_selection_does_not_hide_frozen_cases(self):
        cases = (_history("h1", "1" * 64), _history("h2", "2" * 64))
        selected = MODULE._authorized_history_cases(
            cases,
            {"status": "ready_for_history_acquisition"},
        )
        self.assertEqual([item["case_id"] for item in selected], ["h1", "h2"])

    def test_continuation_audit_rejects_unknown_case(self):
        with self.assertRaisesRegex(
            MODULE.HistoryAcquisitionError,
            "unknown history case",
        ):
            MODULE._authorized_history_cases(
                (_history("h1", "1" * 64), _history("h2", "2" * 64)),
                {
                    "status": "ready_for_history_continuation",
                    "authorized_history_case_ids": ["h3"],
                },
            )

    def test_partial_product_usage_is_read_from_authoritative_result(self):
        with tempfile.TemporaryDirectory() as root:
            product = Path(root)
            (product / "run_result.json").write_text(
                json.dumps(
                    {
                        "budget_usage": {
                            "llm_calls": 4,
                            "csim_calls": 0,
                            "csynth_calls": 0,
                            "cosim_calls": 0,
                        }
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(MODULE._read_product_budget_usage(product), (4, 0))

    def test_positive_episode_is_read_back_from_file(self):
        with tempfile.TemporaryDirectory() as root:
            episode = R5EpisodeEnvelope(
                episode_kind="r4_repair",
                episode_id="episode-1",
                payload_schema_version="r5-repair-episode-v1",
                payload={"outcome_reason": "accepted"},
                execution_identity_sha256="1" * 64,
                source_sha256="2" * 64,
                context_signature="3" * 64,
                created_at="2026-09-19T00:00:00Z",
                observed_at="2026-09-19T00:00:00Z",
                lineage=("diagnostic-1",),
                agent_safe_summary={
                    "failure_family": "unsupported_construct",
                    "stage": "csynth",
                    "owner": "candidate",
                    "false_repair": False,
                    "unsafe_scope": False,
                    "critical_safety_violation": False,
                },
                outcome=R5EpisodeOutcome.VERIFIED_POSITIVE,
                manifest_sha256="4" * 64,
            )
            path = Path(root) / "episode.json"
            path.write_text(
                json.dumps(episode.to_dict(), sort_keys=True) + "\n",
                encoding="utf-8",
            )
            observation = {
                "status": "verified_positive",
                "integration": {
                    "episode_path": str(path),
                    "main_result_unchanged": True,
                },
            }
            loaded = MODULE.verify_positive_observation(
                observation,
                source_sha256="2" * 64,
                manifest_sha256="4" * 64,
            )
            self.assertEqual(loaded.envelope_sha256, episode.envelope_sha256)

    def test_non_positive_history_fails_closed(self):
        with self.assertRaisesRegex(
            MODULE.HistoryAcquisitionError,
            "did not produce verified_positive",
        ):
            MODULE.verify_positive_observation(
                {"status": "abstained"},
                source_sha256="2" * 64,
                manifest_sha256="4" * 64,
            )


if __name__ == "__main__":
    unittest.main()
