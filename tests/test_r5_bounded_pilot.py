from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from agrefactor.campaign import R5Arm


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "r5_bounded_pilot",
    ROOT / "scripts" / "r5_bounded_pilot.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class R5BoundedPilotTests(unittest.TestCase):
    def test_arm_schedule_contains_every_arm_and_is_repeat_bound(self):
        schedules = [
            MODULE.arm_schedule("future-case", repeat)
            for repeat in range(1, 4)
        ]
        for schedule in schedules:
            self.assertEqual(set(schedule), set(R5Arm))
            self.assertEqual(len(schedule), 7)
        self.assertGreater(len(set(schedules)), 1)

    def test_run_repeat_rejects_repeat_outside_frozen_range_before_work(self):
        with self.assertRaisesRegex(ValueError, "frozen range"):
            MODULE.run_repeat(
                output=Path("/unused"),
                pilot={},
                case={},
                runtime_value={},
                runtime=None,
                certificate_value=None,
                revision=None,
                snapshot=None,
                payload=None,
                reduction=None,
                repeat=4,
            )

    def test_manifest_binds_budget_reserve_and_runtime_context_mode(self):
        state = {
            "R4_ACCEPTED": True,
            "R5_ACCEPTED": False,
            "R6_STARTED": False,
            "R5_AUTHORIZED_CANDIDATE_REVALIDATION_REVISION_SHA256": "a" * 64,
            "R5_AUTHORIZED_CANDIDATE_REVALIDATION_SNAPSHOT_SHA256": "b" * 64,
            "R5_AUTHORIZED_CANDIDATE_REVALIDATION_PAYLOAD_SHA256": "c" * 64,
            "R5_CONSUMED_PROVIDER_CALLS": 104,
            "R5_CONSUMED_VITIS_LAUNCHES": 59,
        }
        case = {
            "case_id": MODULE.PILOT_CASE_ID,
            "period": "future",
            "control_role": "positive",
            "top": "top",
            "candidate_top": "top_hls",
            "hashes": {
                "source": "d" * 64,
                "public_test": "e" * 64,
                "hidden_test": "f" * 64,
            },
        }
        protocol = {
            "dataset_manifest_sha256": "1" * 64,
            "split_manifest_sha256": "2" * 64,
            "protocol_audit_sha256": "3" * 64,
            "budget_plan_sha256": "4" * 64,
            "_budget_plan": {
                "provider_recovery_reserve": 39,
                "vitis_recovery_reserve": 44,
            },
        }
        with mock.patch.object(MODULE, "git_head", return_value="9" * 40), mock.patch.object(
            MODULE, "sha_bytes", return_value="8" * 64
        ):
            manifest = MODULE.build_pilot_manifest(case, state, protocol)
        self.assertTrue(manifest["r4_accepted"])
        self.assertFalse(manifest["r5_accepted"])
        self.assertFalse(manifest["r6_started"])
        self.assertEqual(manifest["provider_recovery_reserve"], 39)
        self.assertEqual(manifest["vitis_recovery_reserve"], 44)
        self.assertEqual(
            manifest["context_signature_binding"],
            "derived_from_each_common_baseline_before_arm_execution",
        )
        self.assertEqual(manifest["provider_upper_bound"], 60)
        self.assertEqual(manifest["vitis_upper_bound"], 72)

    def test_file_only_privacy_check_allows_false_flags_and_rejects_payloads(self):
        audit_spec = importlib.util.spec_from_file_location(
            "r5_audit_bounded_pilot",
            ROOT / "scripts" / "r5_audit_bounded_pilot.py",
        )
        assert audit_spec is not None and audit_spec.loader is not None
        audit_module = importlib.util.module_from_spec(audit_spec)
        audit_spec.loader.exec_module(audit_module)
        self.assertFalse(
            audit_module.unsafe_persisted_content(
                {
                    "private_reasoning_persisted": False,
                    "raw_provider_response_persisted": False,
                }
            )
        )
        self.assertTrue(
            audit_module.unsafe_persisted_content(
                {"reasoning_content": "must never persist"}
            )
        )

    def test_memory_arms_pass_rendered_snippets_to_existing_prompt(self):
        capture = SimpleNamespace(formal_request=object())
        fake_config = SimpleNamespace(profile=SimpleNamespace(arm=R5Arm.A4))
        with mock.patch.object(
            MODULE,
            "build_execution_identity_and_canary",
            return_value=({}, object()),
        ), mock.patch.object(
            MODULE,
            "resolve_r5_profile",
            return_value=SimpleNamespace(arm=R5Arm.A4),
        ), mock.patch.object(
            MODULE,
            "render_candidate_memory_snippets",
            return_value=("trusted-memory",),
        ) as render, mock.patch.object(
            MODULE,
            "R5CandidatePromptFactory",
            return_value="prompt-factory",
        ) as prompt, mock.patch.object(
            MODULE,
            "CandidateModelR4MutationAdapter",
            return_value="mutation",
        ), mock.patch.object(
            MODULE,
            "R5IntegrationConfig",
            return_value=fake_config,
        ) as config, mock.patch.object(
            MODULE,
            "ExistingOrchestratorR5Integration",
            side_effect=lambda value: value,
        ):
            factory = MODULE.integration_factory(
                certificate_value=object(),
                revision=object(),
                snapshot=object(),
                payload=object(),
                reduction=object(),
                pilot_hash="0" * 64,
            )
            value = factory(
                capture,
                R5Arm.A4,
                SimpleNamespace(budget=object()),
                Path("/tmp/episodes"),
                object(),
            )
        render.assert_called_once()
        prompt.assert_called_once_with(
            request=capture.formal_request,
            approved_memory_snippets=("trusted-memory",),
        )
        self.assertIs(value, fake_config)
        self.assertEqual(config.call_args.kwargs["memory_payloads"], (config.call_args.kwargs["memory_payloads"][0],))

    def test_partial_audit_reconciles_completed_and_interrupted_repeats(self):
        audit_spec = importlib.util.spec_from_file_location(
            "r5_audit_bounded_pilot_partial",
            ROOT / "scripts" / "r5_audit_bounded_pilot.py",
        )
        assert audit_spec is not None and audit_spec.loader is not None
        audit_module = importlib.util.module_from_spec(audit_spec)
        audit_spec.loader.exec_module(audit_module)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = {
                "schema_version": 1,
                "status": "frozen_before_pilot_outcome_observation",
                "repository_head": "a" * 40,
                "r5_accepted": False,
                "r6_started": False,
                "repeats": 3,
                "provider_used_before": 104,
                "vitis_used_before": 59,
                "provider_upper_bound": 60,
                "vitis_upper_bound": 72,
            }
            manifest["pilot_manifest_sha256"] = hashlib.sha256(
                audit_module.canonical(manifest).encode()
            ).hexdigest()
            (root / "pilot_manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            for repeat in range(1, 4):
                repeat_root = root / "pairs" / ("repeat-%02d" % repeat)
                common = repeat_root / "common-product"
                paired = repeat_root / "paired" / "case-test" / ("repeat-%02d" % repeat)
                common.mkdir(parents=True)
                paired.mkdir(parents=True)
                provider = 9
                vitis = 4 if repeat == 3 else 0
                (common / "run_result.json").write_text(
                    json.dumps(
                        {
                            "budget_usage": {
                                "llm_calls": provider,
                                "csim_calls": 2 if repeat == 3 else 0,
                                "csynth_calls": 1 if repeat == 3 else 0,
                                "cosim_calls": 1 if repeat == 3 else 0,
                            }
                        }
                    ),
                    encoding="utf-8",
                )
                baseline_id = "baseline-%d" % repeat
                (paired / "common_baseline.json").write_text(
                    json.dumps(
                        {
                            "baseline_id": baseline_id,
                            "provider_calls": provider,
                            "vitis_launches": vitis,
                        }
                    ),
                    encoding="utf-8",
                )
                if repeat < 3:
                    for index, arm in enumerate(("A0", "A1", "A2", "A3", "A4", "A5", "A6")):
                        arm_root = paired / "arms" / ("%02d-%s" % (index, arm))
                        arm_root.mkdir(parents=True)
                        (arm_root / "arm_result.json").write_text(
                            json.dumps(
                                {
                                    "arm": arm,
                                    "baseline_id": baseline_id,
                                    "status": "abstained",
                                    "provider_calls": 0,
                                    "vitis_launches": 0,
                                    "hidden_input_count": 0,
                                    "cross_arm_cache_used": False,
                                }
                            ),
                            encoding="utf-8",
                        )

            audit = audit_module.audit_partial(
                root,
                state={"head": "a" * 40},
                manifest=manifest,
            )
            self.assertEqual(audit["status"], "clean_partial")
            self.assertEqual(audit["complete_repeats"], [1, 2])
            self.assertEqual(audit["resume_repeats"], [3])
            self.assertEqual(audit["provider_calls"], 27)
            self.assertEqual(audit["vitis_launches"], 4)
            self.assertEqual(audit["provider_calls_after"], 131)
            self.assertEqual(audit["vitis_launches_after"], 63)


if __name__ == "__main__":
    unittest.main()
