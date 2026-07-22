from __future__ import annotations

import inspect
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import agrefactor.cli as cli_module
import agrefactor.compat.legacy_refactor as legacy_module
import flow.new as flow_new_module
from agrefactor.compat import (
    LegacyRefactorAdapter,
    LegacyRefactorSettings,
    build_legacy_refactor_kwargs,
)
from agrefactor.config import RunMode, TargetProfile, TaskSpec
from agrefactor.models import (
    DEEPSEEK_MODEL_FAMILY_PROFILE,
    EffectiveModelConfig,
    GENERIC_OPENAI_COMPATIBLE_MODEL_FAMILY_PROFILE,
    UnknownModelFamilyProfileError,
)
from agrefactor.runtime import (
    BudgetManager,
    RunContext,
    TraceRecorder,
)


def make_config(
    *,
    provider_name: str = "openai-compatible",
    parameters=None,
    base_url: str | None = "https://api.deepseek.com",
) -> EffectiveModelConfig:
    return EffectiveModelConfig(
        logical_model_name="deepseek-v4-flash",
        provider_name=provider_name,
        model_id="deepseek-v4-flash",
        requested_family_name="deepseek",
        family_profile=DEEPSEEK_MODEL_FAMILY_PROFILE,
        base_url=base_url,
        api_key_env="DEEPSEEK_API_KEY",
        effective_parameters=(
            {
                "temperature": 0,
                "reasoning_effort": "high",
                "nested": {"items": [1, 2]},
            }
            if parameters is None
            else parameters
        ),
    )


def make_task() -> TaskSpec:
    return TaskSpec(
        task_id="p1c3a-legacy-translation",
        kernel_path="src/kernel.cpp",
        kernel_name="top",
        mode=RunMode.REFACTOR,
        target=TargetProfile(
            name="vitis-2023.2-default",
            toolchain="vitis_hls",
            toolchain_version="2023.2",
        ),
    )


def make_context() -> RunContext:
    task = make_task()
    return RunContext(
        run_id="p1c3a-run",
        task=task,
        budget=BudgetManager(),
        trace=TraceRecorder(
            "p1c3a-run",
            task_id=task.task_id,
        ),
    )


def make_cli_args(
    *,
    model: str | None = "deepseek-v4-flash",
    model_family: str | None = "deepseek",
    reasoning_effort: str | None = "low",
    base_url: str | None = "https://api.deepseek.com",
):
    return SimpleNamespace(
        model=model,
        model_family=model_family,
        reasoning_effort=reasoning_effort,
        base_url=base_url,
        api_key_env="DEEPSEEK_API_KEY",
        max_retry_attempts=3,
        enable_testbench_repair=False,
        max_testbench_repair_attempts=2,
        testbench_repair_model=None,
        testbench_repair_api_key_env="OPENAI_API_KEY",
        output_dir=None,
        debug=False,
    )


class LegacyEffectiveConfigSettingsTests(unittest.TestCase):
    def test_accepts_effective_model_config(self):
        config = make_config()
        settings = LegacyRefactorSettings(
            effective_model_config=config,
        )
        self.assertIs(settings.effective_model_config, config)

    def test_rejects_wrong_effective_config_type(self):
        with self.assertRaises(TypeError):
            LegacyRefactorSettings(
                effective_model_config={},
            )

    def test_accepts_matching_compatibility_model(self):
        config = make_config()
        settings = LegacyRefactorSettings(
            effective_model_config=config,
            model="deepseek-v4-flash",
        )
        self.assertEqual(
            settings.model,
            "deepseek-v4-flash",
        )

    def test_rejects_conflicting_compatibility_model(self):
        with self.assertRaisesRegex(
            ValueError,
            "model conflicts",
        ):
            LegacyRefactorSettings(
                effective_model_config=make_config(),
                model="different-model",
            )

    def test_accepts_matching_compatibility_base_url(self):
        settings = LegacyRefactorSettings(
            effective_model_config=make_config(),
            base_url="https://api.deepseek.com",
        )
        self.assertEqual(
            settings.base_url,
            "https://api.deepseek.com",
        )

    def test_rejects_conflicting_compatibility_base_url(self):
        with self.assertRaisesRegex(
            ValueError,
            "base_url conflicts",
        ):
            LegacyRefactorSettings(
                effective_model_config=make_config(),
                base_url="https://other.invalid",
            )

    def test_rejects_parallel_reasoning_authority(self):
        with self.assertRaisesRegex(
            ValueError,
            "reasoning_effort",
        ):
            LegacyRefactorSettings(
                effective_model_config=make_config(),
                reasoning_effort="low",
            )

    def test_testbench_repair_can_use_effective_main_model(self):
        settings = LegacyRefactorSettings(
            effective_model_config=make_config(),
            enable_testbench_repair=True,
        )
        self.assertTrue(settings.enable_testbench_repair)

    def test_testbench_repair_without_any_model_still_fails(self):
        with self.assertRaisesRegex(
            ValueError,
            "requires",
        ):
            LegacyRefactorSettings(
                enable_testbench_repair=True,
            )

    def test_raw_legacy_fields_remain_compatible(self):
        settings = LegacyRefactorSettings(
            model="legacy-model",
            reasoning_effort="medium",
            base_url="https://legacy.invalid",
        )
        self.assertIsNone(settings.effective_model_config)
        self.assertEqual(settings.model, "legacy-model")
        self.assertEqual(
            settings.reasoning_effort,
            "medium",
        )


class LegacyEffectiveTranslationTests(unittest.TestCase):
    def test_kwargs_use_resolved_model_identity(self):
        kwargs = build_legacy_refactor_kwargs(
            make_task(),
            LegacyRefactorSettings(
                effective_model_config=make_config(),
            ),
        )
        self.assertEqual(
            kwargs["model"],
            "deepseek-v4-flash",
        )
        self.assertEqual(
            kwargs["base_url"],
            "https://api.deepseek.com",
        )

    def test_kwargs_use_resolved_reasoning_parameter(self):
        kwargs = build_legacy_refactor_kwargs(
            make_task(),
            LegacyRefactorSettings(
                effective_model_config=make_config(),
            ),
        )
        self.assertEqual(
            kwargs["reasoning_effort"],
            "high",
        )

    def test_llm_override_contains_transport_identity(self):
        kwargs = build_legacy_refactor_kwargs(
            make_task(),
            LegacyRefactorSettings(
                effective_model_config=make_config(),
            ),
        )
        override = kwargs["llm_config_override"]
        self.assertEqual(
            override["model"],
            "deepseek-v4-flash",
        )
        self.assertEqual(
            override["api_type"],
            "openai",
        )
        self.assertEqual(
            override["base_url"],
            "https://api.deepseek.com",
        )

    def test_llm_override_contains_effective_parameters(self):
        kwargs = build_legacy_refactor_kwargs(
            make_task(),
            LegacyRefactorSettings(
                effective_model_config=make_config(),
            ),
        )
        override = kwargs["llm_config_override"]
        self.assertEqual(override["temperature"], 0)
        self.assertEqual(
            override["reasoning_effort"],
            "high",
        )
        self.assertEqual(
            override["nested"]["items"],
            [1, 2],
        )

    def test_llm_override_is_detached(self):
        config = make_config()
        kwargs = build_legacy_refactor_kwargs(
            make_task(),
            LegacyRefactorSettings(
                effective_model_config=config,
            ),
        )
        kwargs["llm_config_override"]["nested"]["items"][0] = 99
        self.assertEqual(
            config.parameters["nested"]["items"],
            [1, 2],
        )

    def test_kwargs_include_exact_safe_manifest(self):
        config = make_config()
        kwargs = build_legacy_refactor_kwargs(
            make_task(),
            LegacyRefactorSettings(
                effective_model_config=config,
            ),
        )
        self.assertEqual(
            kwargs["effective_model_config_manifest"],
            config.to_manifest(),
        )

    def test_kwargs_include_family_instruction(self):
        config = make_config()
        kwargs = build_legacy_refactor_kwargs(
            make_task(),
            LegacyRefactorSettings(
                effective_model_config=config,
            ),
        )
        self.assertEqual(
            kwargs["family_instruction"],
            config.family_instruction,
        )

    def test_raw_path_has_no_resolved_override(self):
        kwargs = build_legacy_refactor_kwargs(
            make_task(),
            LegacyRefactorSettings(
                model="legacy-model",
                reasoning_effort="medium",
                base_url="https://legacy.invalid",
            ),
        )
        self.assertIsNone(kwargs["llm_config_override"])
        self.assertIsNone(
            kwargs["effective_model_config_manifest"]
        )
        self.assertIsNone(kwargs["family_instruction"])
        self.assertEqual(
            kwargs["model_configuration_source"],
            "legacy_compatibility",
        )

    def test_reserved_identity_parameter_is_rejected(self):
        config = make_config(
            parameters={"model": "override"},
        )
        with self.assertRaisesRegex(
            ValueError,
            "reserved",
        ):
            build_legacy_refactor_kwargs(
                make_task(),
                LegacyRefactorSettings(
                    effective_model_config=config,
                ),
            )

    def test_unknown_provider_translation_is_rejected(self):
        config = EffectiveModelConfig(
            logical_model_name="logical",
            provider_name="unknown-provider",
            model_id="provider-model",
            family_profile=(
                GENERIC_OPENAI_COMPATIBLE_MODEL_FAMILY_PROFILE
            ),
        )
        with self.assertRaisesRegex(
            ValueError,
            "provider",
        ):
            build_legacy_refactor_kwargs(
                make_task(),
                LegacyRefactorSettings(
                    effective_model_config=config,
                ),
            )


class LegacyAdapterManifestTests(unittest.TestCase):
    def test_backend_receives_resolved_payload(self):
        captured = {}

        def backend(**kwargs):
            captured.update(kwargs)
            return True, {}

        config = make_config()
        result = LegacyRefactorAdapter(
            LegacyRefactorSettings(
                effective_model_config=config,
            ),
            backend=backend,
        )(make_context())
        self.assertTrue(result.succeeded)
        self.assertEqual(
            captured["effective_model_config_manifest"],
            config.to_manifest(),
        )
        self.assertEqual(
            captured["model_configuration_source"],
            "effective_model_config",
        )

    def test_invocation_trace_records_safe_manifest(self):
        context = make_context()
        config = make_config()
        LegacyRefactorAdapter(
            LegacyRefactorSettings(
                effective_model_config=config,
            ),
            backend=lambda **kwargs: (True, {}),
        )(context)
        event = next(
            item
            for item in context.trace.events
            if item.event == "legacy_refactor.invoked"
        )
        self.assertEqual(
            event.metadata["effective_model_config"],
            config.to_manifest(),
        )

    def test_phase_metadata_records_safe_manifest(self):
        config = make_config()
        result = LegacyRefactorAdapter(
            LegacyRefactorSettings(
                effective_model_config=config,
            ),
            backend=lambda **kwargs: (True, {}),
        )(make_context())
        self.assertEqual(
            result.metadata["effective_model_config"],
            config.to_manifest(),
        )

    def test_phase_metadata_records_configuration_source(self):
        result = LegacyRefactorAdapter(
            LegacyRefactorSettings(
                effective_model_config=make_config(),
            ),
            backend=lambda **kwargs: (True, {}),
        )(make_context())
        self.assertEqual(
            result.metadata["model_configuration_source"],
            "effective_model_config",
        )


class LegacyCliEffectiveConfigTests(unittest.TestCase):
    def test_cli_builder_returns_typed_legacy_settings(self):
        settings = cli_module._build_cli_legacy_settings(
            make_cli_args()
        )
        self.assertIsInstance(
            settings.effective_model_config,
            EffectiveModelConfig,
        )
        self.assertEqual(
            settings.model,
            "deepseek-v4-flash",
        )
        self.assertIsNone(settings.reasoning_effort)

    def test_cli_builder_resolves_reasoning_policy(self):
        settings = cli_module._build_cli_legacy_settings(
            make_cli_args(reasoning_effort="low")
        )
        self.assertEqual(
            settings.effective_model_config.parameters[
                "reasoning_effort"
            ],
            "high",
        )

    def test_cli_builder_rejects_unknown_family(self):
        with self.assertRaises(
            UnknownModelFamilyProfileError
        ):
            cli_module._build_cli_legacy_settings(
                make_cli_args(
                    model_family="not-registered"
                )
            )

    def test_cli_manifest_never_reads_api_key_value(self):
        os.environ["DEEPSEEK_API_KEY"] = "must-not-leak"
        try:
            settings = (
                cli_module._build_cli_legacy_settings(
                    make_cli_args()
                )
            )
            encoded = json.dumps(
                settings.effective_model_config.to_manifest(),
                sort_keys=True,
            )
        finally:
            os.environ.pop("DEEPSEEK_API_KEY", None)
        self.assertNotIn("must-not-leak", encoded)

    def test_cli_model_without_family_keeps_raw_compatibility(self):
        settings = cli_module._build_cli_legacy_settings(
            make_cli_args(
                model="deepseek-v4-flash",
                model_family=None,
                reasoning_effort="low",
                base_url="https://api.deepseek.com",
            )
        )
        self.assertIsNone(settings.effective_model_config)
        self.assertEqual(
            settings.model,
            "deepseek-v4-flash",
        )
        self.assertEqual(
            settings.reasoning_effort,
            "low",
        )
        self.assertEqual(
            settings.base_url,
            "https://api.deepseek.com",
        )

    def test_cli_without_model_keeps_legacy_compatibility(self):
        settings = cli_module._build_cli_legacy_settings(
            make_cli_args(
                model=None,
                model_family=None,
                reasoning_effort=None,
                base_url=None,
            )
        )
        self.assertIsNone(settings.effective_model_config)
        self.assertIsNone(settings.model)

    def test_cli_builder_resolves_effective_config_once(self):
        source = inspect.getsource(
            cli_module._build_cli_legacy_settings
        )
        self.assertEqual(
            source.count("resolve_effective_config("),
            1,
        )


class FlowNewOverrideTests(unittest.TestCase):
    def test_override_bypasses_raw_model_resolution(self):
        override = {
            "model": "deepseek-v4-flash",
            "api_type": "openai",
            "reasoning_effort": "high",
        }
        with patch.object(
            flow_new_module,
            "make_llm_config",
            side_effect=AssertionError("raw resolver called"),
        ):
            resolved = (
                flow_new_module.resolve_runtime_llm_config(
                    model="different",
                    reasoning_effort="low",
                    base_url="https://other.invalid",
                    llm_config_override=override,
                )
            )
        self.assertEqual(resolved, override)

    def test_override_returns_deep_copy(self):
        override = {
            "model": "deepseek-v4-flash",
            "nested": {"items": [1, 2]},
        }
        resolved = flow_new_module.resolve_runtime_llm_config(
            model=None,
            llm_config_override=override,
        )
        resolved["nested"]["items"][0] = 99
        self.assertEqual(
            override["nested"]["items"],
            [1, 2],
        )

    def test_raw_path_preserves_existing_make_llm_config(self):
        resolved = flow_new_module.resolve_runtime_llm_config(
            model="legacy-model",
            reasoning_effort="medium",
            base_url="https://legacy.invalid",
        )
        self.assertEqual(
            resolved,
            flow_new_module.make_llm_config(
                "legacy-model",
                "medium",
                "https://legacy.invalid",
            ),
        )


if __name__ == "__main__":
    unittest.main()
