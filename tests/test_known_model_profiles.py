import json
import unittest

from agrefactor.models import (
    DEEPSEEK_MODEL_FAMILY_PROFILE,
    GENERIC_OPENAI_COMPATIBLE_MODEL_FAMILY_PROFILE,
    GLM_MODEL_FAMILY_PROFILE,
    KIMI_MODEL_FAMILY_PROFILE,
    KNOWN_MODEL_FAMILY_PROFILE_NAMES,
    MINIMAX_MODEL_FAMILY_PROFILE,
    ModelFamilyProfile,
    ModelParameterAliasConflictError,
    ModelProfileVerificationStatus,
    ModelRegistry,
    QWEN_MODEL_FAMILY_PROFILE,
    ReasoningPolicy,
    RejectedModelParameterError,
    UnsupportedReasoningLevelError,
    UnknownModelFamilyProfileError,
)


EXPECTED_NAMES = (
    "deepseek",
    "kimi",
    "glm",
    "minimax",
    "qwen",
    "generic-openai-compatible",
)


class KnownModelProfilesTests(unittest.TestCase):
    def test_six_known_profiles_are_registered_by_default(self):
        registry = ModelRegistry()
        names = registry.family_profile_names()
        for expected in EXPECTED_NAMES:
            self.assertIn(expected, names)
        self.assertEqual(
            KNOWN_MODEL_FAMILY_PROFILE_NAMES,
            EXPECTED_NAMES,
        )

    def test_known_profiles_use_frozen_verification_states(self):
        for profile in (
            DEEPSEEK_MODEL_FAMILY_PROFILE,
            KIMI_MODEL_FAMILY_PROFILE,
            GLM_MODEL_FAMILY_PROFILE,
            MINIMAX_MODEL_FAMILY_PROFILE,
            QWEN_MODEL_FAMILY_PROFILE,
            GENERIC_OPENAI_COMPATIBLE_MODEL_FAMILY_PROFILE,
        ):
            with self.subTest(profile=profile.name):
                self.assertEqual(
                    profile.verification_status,
                    ModelProfileVerificationStatus.DETERMINISTICALLY_TESTED,
                )
                self.assertNotEqual(
                    profile.verification_status,
                    ModelProfileVerificationStatus.NETWORK_SMOKE_VERIFIED,
                )

    def test_deepseek_maps_stable_levels_conservatively(self):
        for requested in ("low", "medium", "high"):
            with self.subTest(requested=requested):
                effective = (
                    DEEPSEEK_MODEL_FAMILY_PROFILE.merge_parameters(
                        call_overrides={
                            "reasoning_effort": requested
                        }
                    )
                )
                self.assertEqual(
                    effective["reasoning_effort"],
                    "high",
                )

    def test_other_family_level_effort_is_omitted(self):
        profiles = (
            KIMI_MODEL_FAMILY_PROFILE,
            GLM_MODEL_FAMILY_PROFILE,
            MINIMAX_MODEL_FAMILY_PROFILE,
            QWEN_MODEL_FAMILY_PROFILE,
        )
        for profile in profiles:
            for requested in ("low", "medium", "high"):
                with self.subTest(
                    profile=profile.name,
                    requested=requested,
                ):
                    effective = profile.merge_parameters(
                        call_overrides={
                            "reasoning_effort": requested,
                            "max_tokens": 10,
                        }
                    )
                    self.assertNotIn(
                        "reasoning_effort",
                        effective,
                    )
                    self.assertEqual(effective["max_tokens"], 10)

    def test_generic_profile_rejects_reasoning_request(self):
        with self.assertRaises(
            UnsupportedReasoningLevelError
        ):
            (
                GENERIC_OPENAI_COMPATIBLE_MODEL_FAMILY_PROFILE
                .merge_parameters(
                    call_overrides={
                        "reasoning_effort": "high"
                    }
                )
            )

    def test_only_low_medium_high_are_user_facing(self):
        with self.assertRaisesRegex(
            UnsupportedReasoningLevelError,
            "low/medium/high",
        ):
            DEEPSEEK_MODEL_FAMILY_PROFILE.merge_parameters(
                call_overrides={"reasoning_effort": "max"}
            )

    def test_alias_is_normalized_before_provider(self):
        effective = (
            DEEPSEEK_MODEL_FAMILY_PROFILE.merge_parameters(
                call_overrides={
                    "max_completion_tokens": 4096
                }
            )
        )
        self.assertNotIn("max_completion_tokens", effective)
        self.assertEqual(effective["max_tokens"], 4096)

    def test_alias_conflict_is_rejected(self):
        with self.assertRaises(
            ModelParameterAliasConflictError
        ):
            DEEPSEEK_MODEL_FAMILY_PROFILE.merge_parameters(
                call_overrides={
                    "max_completion_tokens": 4096,
                    "max_tokens": 32768,
                }
            )

    def test_custom_rejected_parameter_fails_before_provider(self):
        profile = ModelFamilyProfile(
            name="reject-logprobs",
            reasoning_policy=ReasoningPolicy.omit_all(),
            rejected_parameters=frozenset({"logprobs"}),
        )
        with self.assertRaises(
            RejectedModelParameterError
        ):
            profile.merge_parameters(
                call_overrides={"logprobs": True}
            )

    def test_effective_parameters_reject_credentials(self):
        with self.assertRaisesRegex(
            ValueError,
            "credential-like",
        ):
            DEEPSEEK_MODEL_FAMILY_PROFILE.merge_parameters(
                call_overrides={"api_key": "must-not-leak"}
            )

    def test_precedence_is_preserved_before_policy(self):
        profile = ModelFamilyProfile(
            name="precedence",
            safe_default_parameters={
                "temperature": 0.1,
                "max_tokens": 100,
            },
            reasoning_policy=ReasoningPolicy.omit_all(),
        )
        effective = profile.merge_parameters(
            model_defaults={
                "temperature": 0.2,
                "top_p": 0.9,
            },
            call_overrides={
                "temperature": 0,
                "reasoning_effort": "low",
            },
        )
        self.assertEqual(
            effective,
            {
                "temperature": 0,
                "max_tokens": 100,
                "top_p": 0.9,
            },
        )

    def test_openai_compatibility_alias_resolves_generic_profile(self):
        registry = ModelRegistry()
        resolved = registry.get_family_profile("openai")
        self.assertIs(
            resolved,
            GENERIC_OPENAI_COMPATIBLE_MODEL_FAMILY_PROFILE,
        )
        self.assertEqual(
            registry.family_aliases()["openai"],
            "generic-openai-compatible",
        )

    def test_unknown_family_is_not_silently_aliased(self):
        with self.assertRaises(
            UnknownModelFamilyProfileError
        ):
            ModelRegistry().get_family_profile("unknown-family")

    def test_manifests_are_serializable_and_secret_free(self):
        registry = ModelRegistry()
        for name in EXPECTED_NAMES:
            profile = registry.get_family_profile(name)
            encoded = json.dumps(
                profile.to_manifest(),
                ensure_ascii=False,
                sort_keys=True,
            ).lower()
            self.assertNotIn("api_key", encoded)
            self.assertNotIn("credential", encoded)
            self.assertIn("verification_status", encoded)


if __name__ == "__main__":
    unittest.main()
