from __future__ import annotations

import unittest

from agrefactor.compat.legacy_refactor import (
    _build_effective_legacy_llm_config,
    build_effective_legacy_llm_config,
)
from agrefactor.models import (
    ModelCallRole,
    pop_internal_call_evidence,
    resolve_model_runtime,
)


def _config(role):
    selection = resolve_model_runtime(None, reasoning_effort="auto")
    return build_effective_legacy_llm_config(
        selection.effective_config,
        role,
    )


class P40FBridgeTests(unittest.TestCase):
    def test_high_role_preserves_provider_max_without_ag2_schema_max(self):
        value = _config(ModelCallRole.REFACTOR_PLANNING)
        cleaned, evidence = pop_internal_call_evidence(value)
        transport = cleaned.pop("_agrefactor_legacy_transport_evidence")
        self.assertNotIn("reasoning_effort", cleaned)
        self.assertEqual(
            cleaned["extra_body"]["reasoning_effort"],
            "max",
        )
        self.assertEqual(
            cleaned["extra_body"]["thinking"],
            {"type": "enabled"},
        )
        self.assertEqual(
            evidence["effective_provider_reasoning_effort"],
            "max",
        )
        self.assertIsNone(transport["ag2_schema_reasoning_effort"])
        self.assertEqual(
            transport["provider_reasoning_location"],
            "extra_body",
        )
        self.assertIs(transport["provider_payload_preserved"], True)

    def test_medium_role_uses_ag2_supported_high(self):
        value = _config(ModelCallRole.PUBLIC_TEST_GENERATION)
        cleaned, evidence = pop_internal_call_evidence(value)
        transport = cleaned.pop("_agrefactor_legacy_transport_evidence")
        self.assertEqual(cleaned["reasoning_effort"], "high")
        self.assertEqual(
            cleaned["extra_body"]["thinking"],
            {"type": "enabled"},
        )
        self.assertEqual(
            evidence["effective_provider_reasoning_effort"],
            "high",
        )
        self.assertEqual(
            transport["ag2_schema_reasoning_effort"],
            "high",
        )

    def test_high_role_constructs_real_ag2_llm_config(self):
        try:
            import autogen
        except ImportError:
            self.skipTest("autogen is unavailable")
        cleaned, _ = pop_internal_call_evidence(
            _config(ModelCallRole.REFACTOR_SOURCE_GENERATION)
        )
        cleaned.pop("_agrefactor_legacy_transport_evidence")
        self.assertIsNotNone(autogen.LLMConfig(cleaned))

    def test_private_legacy_helper_strips_all_internal_evidence(self):
        selection = resolve_model_runtime(None, reasoning_effort="auto")
        value = _build_effective_legacy_llm_config(
            selection.effective_config
        )
        self.assertNotIn("_agrefactor_call_evidence", value)
        self.assertNotIn(
            "_agrefactor_legacy_transport_evidence",
            value,
        )


if __name__ == "__main__":
    unittest.main()
