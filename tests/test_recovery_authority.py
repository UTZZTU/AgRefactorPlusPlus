import unittest

from agrefactor.recovery import (
    RecoveryAuthority,
    normalize_recovery_authority,
)


class RecoveryAuthorityNormalizationTests(unittest.TestCase):
    def test_known_evidence_aliases_are_deterministic(self):
        for value in (
            "deterministic_proven",
            "preflight_component_completed",
            "source_compile_unit",
            "source_span",
            "source_entry_contract",
            "differential_isolation_proven",
            "evaluated_case_failure",
            "original_entry_not_executed",
            "original_only_completed",
        ):
            with self.subTest(value=value):
                self.assertIs(
                    normalize_recovery_authority(value),
                    RecoveryAuthority.DETERMINISTIC_PROVEN,
                )

    def test_policy_authorities_are_preserved(self):
        self.assertIs(
            normalize_recovery_authority("public_reference_qualified"),
            RecoveryAuthority.PUBLIC_REFERENCE_QUALIFIED,
        )
        self.assertIs(
            normalize_recovery_authority("public_csynth_bounded_trial"),
            RecoveryAuthority.PUBLIC_CSYNTH_BOUNDED_TRIAL,
        )
        self.assertIs(
            normalize_recovery_authority("llm_advisory"),
            RecoveryAuthority.LLM_ADVISORY,
        )
        self.assertIs(
            normalize_recovery_authority(RecoveryAuthority.UNKNOWN),
            RecoveryAuthority.UNKNOWN,
        )

    def test_unknown_values_do_not_gain_repair_authority(self):
        for value in ("new_authority", "runtime_not_isolated", None, 3):
            with self.subTest(value=value):
                self.assertIs(
                    normalize_recovery_authority(value),
                    RecoveryAuthority.UNKNOWN,
                )


if __name__ == "__main__":
    unittest.main()
