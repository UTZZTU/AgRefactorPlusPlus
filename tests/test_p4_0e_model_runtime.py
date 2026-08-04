from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agrefactor.cli import build_parser
from agrefactor.models import (
    ChatMessage,
    DEFAULT_MODEL_ID,
    ModelCallRole,
    ModelRequest,
    UnsupportedReasoningLevelError,
    credential_presence_evidence,
    load_invocation_dotenv,
    resolve_model_runtime,
)
from agrefactor.models.call_policy import pop_internal_call_evidence
from agrefactor.models.openai_compatible import (
    OpenAICompatibleResponseError,
)
from agrefactor.product.source_bootstrap import (
    SourceCommandRejected,
    run_source_command,
)


class P4EModelRuntimeTests(unittest.TestCase):
    def test_cli_defaults_model_and_auto_reasoning(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["refactor", "kernel.cpp", "--top", "top"]
        )
        self.assertEqual(args.model, DEFAULT_MODEL_ID)
        self.assertEqual(args.reasoning_effort, "auto")
        self.assertFalse(getattr(args, "model_explicit", False))
        explicit = parser.parse_args(
            [
                "refactor",
                "kernel.cpp",
                "--top",
                "top",
                "--model",
                "kimi-test",
            ]
        )
        self.assertTrue(explicit.model_explicit)

    def test_dotenv_process_environment_wins_without_secret_evidence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            Path(raw, ".env").write_text(
                "P4E_KEY=dotenv-secret\nP4E_ONLY=from-dotenv\n",
                encoding="utf-8",
            )
            environment = {"P4E_KEY": "process-secret"}
            evidence = load_invocation_dotenv(
                raw,
                environ=environment,
            )
            self.assertEqual(environment["P4E_KEY"], "process-secret")
            self.assertEqual(environment["P4E_ONLY"], "from-dotenv")
            self.assertFalse(evidence.override)
            serialized = json.dumps(evidence.to_dict(), sort_keys=True)
            self.assertNotIn("process-secret", serialized)
            self.assertNotIn("dotenv-secret", serialized)
            self.assertNotIn("from-dotenv", serialized)

    def test_missing_credential_is_typed_prelaunch_and_value_free(
        self,
    ) -> None:
        helper = credential_presence_evidence(
            "DEEPSEEK_API_KEY",
            environ={},
        )
        self.assertFalse(helper["credential_present"])
        self.assertFalse(helper["credential_value_persisted"])

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "kernel.cpp"
            source.write_text(
                'extern "C" void top() {}\n',
                encoding="utf-8",
            )
            output = root / "artifacts"
            args = build_parser().parse_args(
                [
                    "refactor",
                    str(source),
                    "--top",
                    "top",
                    "--output-dir",
                    str(output),
                    "--run-id",
                    "p4e-missing-credential",
                ]
            )
            previous = Path.cwd()
            try:
                os.chdir(root)
                with patch.dict(
                    os.environ,
                    {"HOME": raw},
                    clear=True,
                ):
                    with self.assertRaises(SourceCommandRejected):
                        run_source_command(args)
            finally:
                os.chdir(previous)

            rejection = json.loads(
                (output / "request_rejection.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                rejection["reason_code"],
                "model_credential_missing_prelaunch",
            )
            self.assertFalse(rejection["provider_call_observed"])
            serialized = json.dumps(rejection, sort_keys=True)
            self.assertNotIn("dotenv-secret", serialized)
            self.assertNotIn("process-secret", serialized)

    def test_deepseek_auto_medium_role_maps_high_and_enables_thinking(
        self,
    ) -> None:
        config = resolve_model_runtime(
            None,
            reasoning_effort="auto",
        ).effective_config
        parameters, evidence = config.parameterize_call(
            ModelCallRole.PUBLIC_TEST_GENERATION
        )
        self.assertNotIn("_agrefactor_call_evidence", parameters)
        self.assertEqual(parameters["reasoning_effort"], "high")
        self.assertEqual(
            parameters["extra_body"]["thinking"],
            {"type": "enabled"},
        )
        self.assertTrue(evidence.thinking_effective)
        self.assertEqual(evidence.call_role, "public_test_generation")

        from agrefactor.compat.legacy_refactor import (
            _build_effective_legacy_llm_config,
            build_effective_legacy_llm_config,
        )
        with patch.dict(
            os.environ,
            {"DEEPSEEK_API_KEY": "in-memory-only-key"},
            clear=False,
        ):
            runtime_config = build_effective_legacy_llm_config(
                config,
                ModelCallRole.PUBLIC_TEST_GENERATION,
            )
            compatibility_config = (
                _build_effective_legacy_llm_config(config)
            )
        self.assertEqual(runtime_config["api_key"], "in-memory-only-key")
        self.assertIn("_agrefactor_call_evidence", runtime_config)
        # AG2 configs may contain imported Python objects such as a response
        # format class. Evidence stripping must preserve them without JSON
        # serialization.
        runtime_config["response_format"] = dict
        transport, stored = pop_internal_call_evidence(runtime_config)
        self.assertIs(transport["response_format"], dict)
        self.assertEqual(stored["call_role"], "public_test_generation")
        self.assertNotIn("api_key", compatibility_config)
        self.assertNotIn(
            "_agrefactor_call_evidence",
            compatibility_config,
        )

    def test_deepseek_auto_high_role_maps_max(self) -> None:
        config = resolve_model_runtime(
            None,
            reasoning_effort="auto",
        ).effective_config
        parameters, evidence = config.parameterize_call(
            ModelCallRole.REFACTOR_SOURCE_GENERATION
        )
        self.assertEqual(parameters["reasoning_effort"], "max")
        self.assertEqual(
            evidence.effective_project_reasoning_effort,
            "high",
        )

    def test_user_override_model_endpoint_and_key_env_remain_truthful(
        self,
    ) -> None:
        selection = resolve_model_runtime(
            "kimi-test",
            family="kimi",
            base_url="https://example.invalid/v1",
            api_key_env="MY_KEY",
            reasoning_effort="auto",
        )
        self.assertEqual(
            selection.defaults_source,
            "family_inference_and_transport_defaults",
        )
        self.assertEqual(selection.effective_config.api_key_env, "MY_KEY")
        self.assertEqual(
            selection.effective_config.base_url,
            "https://example.invalid/v1",
        )
        transport, evidence = selection.effective_config.parameterize_call(
            ModelCallRole.CANDIDATE_REPAIR
        )
        self.assertEqual(
            transport,
            selection.effective_config.parameters_for_call(
                ModelCallRole.CANDIDATE_REPAIR
            ),
        )
        self.assertNotIn("reasoning_effort", transport)
        self.assertNotIn("_agrefactor_call_evidence", transport)
        self.assertEqual(evidence.requested_reasoning_effort, "auto")
        self.assertEqual(
            evidence.effective_project_reasoning_effort,
            "high",
        )
        self.assertIsNone(
            evidence.effective_provider_reasoning_effort
        )
        self.assertFalse(evidence.thinking_requested)
        self.assertFalse(evidence.thinking_effective)

    def test_generic_explicit_reasoning_rejects_instead_of_guessing(
        self,
    ) -> None:
        # Generic deployments advertise no verified reasoning transport.
        # Explicit medium/high therefore fails while resolving the immutable
        # effective configuration, before a provider request can exist.
        with self.assertRaises(UnsupportedReasoningLevelError):
            resolve_model_runtime(
                "custom",
                family="generic-openai-compatible",
                reasoning_effort="high",
            )

    def test_provider_strips_internal_evidence_and_private_tags_fail_closed(
        self,
    ) -> None:
        captured: dict[str, object] = {}

        class Completions:
            def create(self, **kwargs):
                captured.update(kwargs)
                return {
                    "id": "p4e-test",
                    "model": DEFAULT_MODEL_ID,
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": "OK",
                                "reasoning_content": "private-chain",
                            },
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 2,
                        "completion_tokens": 1,
                    },
                }

        client = SimpleNamespace(
            chat=SimpleNamespace(completions=Completions())
        )
        selection = resolve_model_runtime(None)
        config = selection.effective_config
        provider = selection.registry.get_provider(config.provider_name)
        provider._client_factory = lambda **_: client
        parameters = config.parameters_for_call(
            ModelCallRole.REFACTOR_PLANNING
        )
        with patch.dict(
            os.environ,
            {"DEEPSEEK_API_KEY": "unit-test-key"},
            clear=False,
        ):
            response = provider.generate(
                config.to_model_spec(),
                ModelRequest(
                    messages=(ChatMessage(role="user", content="hello"),),
                    parameters=parameters,
                    metadata={
                        "model_call_policy": (
                            config.call_policy_evidence(
                                ModelCallRole.REFACTOR_PLANNING
                            )
                        )
                    },
                ),
            )
        self.assertNotIn("_agrefactor_call_evidence", captured)
        self.assertEqual(captured["reasoning_effort"], "max")
        self.assertEqual(
            captured["extra_body"],
            {"thinking": {"type": "enabled"}},
        )
        metadata = json.dumps(dict(response.metadata), sort_keys=True)
        self.assertNotIn("private-chain", metadata)
        self.assertTrue(response.metadata["has_reasoning_content"])
        self.assertEqual(
            response.metadata["model_call_policy"]["call_role"],
            "refactor_planning",
        )

        from agrefactor.models.openai_compatible import (
            _reject_private_reasoning_text,
        )

        for value in (
            "<think>private</think>final",
            "<think>private without close",
            "final</reasoning>",
        ):
            with self.subTest(value=value):
                with self.assertRaises(OpenAICompatibleResponseError):
                    _reject_private_reasoning_text(value)


if __name__ == "__main__":
    unittest.main()
