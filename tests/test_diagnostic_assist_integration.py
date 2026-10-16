from __future__ import annotations

import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from autogen.agentchat import ConversableAgent

from agrefactor.evidence.experience_retrieval import CurrentCaseQuery
from agrefactor.models import ModelCallRole
from agrefactor.prompts import experience_context
from flow.tools import tb_optimizer


class FakeEffectiveModelConfig:
    provider_name = "deepseek"
    model_id = "deepseek-v4-flash"
    base_url = "https://api.deepseek.com"

    def parameters_for_call(self, role):
        self.last_role = role
        return {"max_tokens": 64}


class CapturingClient:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append([dict(item) for item in kwargs["messages"]])
        return object()

    def extract_text_or_completion_object(self, response):
        del response
        return "model response", None


def selected_context(query, **kwargs):
    del query
    snippets = ("[positive experience] validated pointer-depth update",)
    selected_context.counted_messages = list(kwargs["render_messages"](snippets))
    return SimpleNamespace(snippets=snippets, audit={"injected_ids": ["experience-1"]})


class DiagnosticAssistIntegrationTests(unittest.TestCase):
    def setUp(self):
        selected_context.counted_messages = []
        self.config = FakeEffectiveModelConfig()
        self.query = CurrentCaseQuery(
            stage="csynth",
            visibility="public",
            owner="testbench",
            components=("testbench",),
            diagnostic_terms=("runtime_contract",),
        )

    def build_agent(self):
        return ConversableAgent(
            name="experience_test_agent",
            system_message="Use current evidence and the active contract.",
            llm_config=False,
        )

    def test_ag2_client_receives_the_same_full_messages_that_were_counted(self):
        agent = self.build_agent()
        client = CapturingClient()
        audit = []
        with patch.object(experience_context, "default_model_token_counter", return_value=object()), patch.object(
            experience_context,
            "select_experience_prompt_context",
            side_effect=selected_context,
        ):
            tb_optimizer._register_experience_message_hook(
                agent,
                enabled=True,
                store_root="unused-test-store",
                effective_model_config=self.config,
                call_role=ModelCallRole.PUBLIC_TEST_GENERATION,
                query_factory=lambda: self.query,
                audit_events=audit,
            )
            body = agent.process_all_messages_before_reply(
                [{"role": "user", "content": "Current compile evidence"}]
            )
            final, response = agent.generate_oai_reply(
                messages=body,
                config=client,
            )

        self.assertTrue(final)
        self.assertEqual(response, "model response")
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0], selected_context.counted_messages)
        self.assertIn("validated pointer-depth update", client.calls[0][-1]["content"])
        self.assertEqual(audit[0]["injected_ids"], ["experience-1"])
        self.assertEqual(self.config.last_role, ModelCallRole.PUBLIC_TEST_GENERATION)

    def test_hidden_generation_queries_public_projection_without_persisting_audit(self):
        agent = self.build_agent()
        client = CapturingClient()
        hidden_query = CurrentCaseQuery(
            stage="public_testbench_generation",
            visibility="hidden_generation",
            owner="unknown",
            components=("stub",),
            diagnostic_terms=("compile_failed",),
        )
        audit = []
        with patch.object(experience_context, "default_model_token_counter", return_value=object()), patch.object(
            experience_context,
            "select_experience_prompt_context",
            side_effect=selected_context,
        ) as select:
            tb_optimizer._register_experience_message_hook(
                agent,
                enabled=True,
                store_root="unused-test-store",
                effective_model_config=self.config,
                call_role=ModelCallRole.HIDDEN_TEST_GENERATION,
                query_factory=lambda: hidden_query,
                audit_events=audit,
                component_factory=lambda: "stub",
            )
            body = agent.process_all_messages_before_reply(
                [{"role": "user", "content": "held-out generation failure"}]
            )
            agent.generate_oai_reply(messages=body, config=client)

        self.assertEqual(select.call_args.args[0].visibility, "hidden_generation")
        self.assertEqual(audit, [])
        self.assertIn("validated pointer-depth update", client.calls[0][-1]["content"])
        self.assertEqual(self.config.last_role, ModelCallRole.HIDDEN_TEST_GENERATION)

    def test_disabled_assist_does_not_register_or_run_retrieval(self):
        agent = self.build_agent()
        client = CapturingClient()
        with patch.object(
            experience_context,
            "select_experience_prompt_context",
            side_effect=AssertionError("retrieval must stay disabled"),
        ):
            tb_optimizer._register_experience_message_hook(
                agent,
                enabled=False,
                store_root="unused-test-store",
                effective_model_config=self.config,
                call_role=ModelCallRole.PUBLIC_TEST_GENERATION,
                query_factory=lambda: self.query,
            )
            body = agent.process_all_messages_before_reply(
                [{"role": "user", "content": "ordinary request"}]
            )
            agent.generate_oai_reply(messages=body, config=client)

        self.assertEqual(len(client.calls), 1)
        self.assertNotIn("Historical references", str(client.calls[0]))

    def test_runtime_contract_repair_uses_the_same_hook_before_model_request(self):
        class HookAgent:
            system_message = "Repair the Public runtime contract."
            _oai_system_message = [{"role": "system", "content": system_message}]

            def __init__(self):
                self.hooks = {}
                self.sent_messages = None

            def register_hook(self, name, hook):
                self.hooks.setdefault(name, []).append(hook)

        agent = HookAgent()

        class Loader:
            def __init__(self, *args, **kwargs):
                del args, kwargs

            def load_agent(self, name):
                del name
                return agent

        def fake_agent_run_once(current_agent, message, first_turn):
            del first_turn
            body = [{"role": "user", "content": message}]
            for hook in current_agent.hooks["process_all_messages_before_reply"]:
                body = hook(body)
            current_agent.sent_messages = [
                *current_agent._oai_system_message,
                *body,
            ]
            return "valid response"

        audit = []
        before_requests = []
        with (
            patch.object(tb_optimizer, "public_runtime_contract_depth_ports", return_value=("data",)),
            patch.object(tb_optimizer, "HLSAgentLoader", Loader),
            patch.object(tb_optimizer, "_agent_run_once", side_effect=fake_agent_run_once),
            patch.object(
                tb_optimizer,
                "_runtime_contract_from_model_response",
                return_value={"cosim_interface_depths": {"data": 16}},
            ),
            patch.object(experience_context, "default_model_token_counter", return_value=object()),
            patch.object(
                experience_context,
                "select_experience_prompt_context",
                side_effect=selected_context,
            ),
        ):
            result = tb_optimizer.generate_public_runtime_contract(
                orig_code="int top() { return 0; }",
                testbench_code="int main() { return 0; }",
                candidate_top_function="top_hls",
                frozen_hls_decl="void top_hls(int *data);",
                diagnostic_assist=True,
                diagnostic_experience_root="unused-test-store",
                effective_model_config=self.config,
                experience_query=self.query,
                experience_audit_events=audit,
                before_request=lambda: before_requests.append(True),
            )

        self.assertEqual(result["cosim_interface_depths"], {"data": 16})
        self.assertEqual(before_requests, [True])
        self.assertEqual(agent.sent_messages, selected_context.counted_messages)
        self.assertEqual(audit[0]["injected_ids"], ["experience-1"])
        self.assertEqual(self.config.last_role, ModelCallRole.TESTBENCH_REPAIR)

    def test_public_generation_wrapper_passes_retrieval_and_model_configuration(self):
        cv = {
            "orig_code": "void top() {}",
            "kernel_name": "top",
            "public_tb_artifact_dir": "unused-artifacts",
        }
        result = {
            "best_tb": "int main() { return 0; }",
            "instruction": "Generate a public testbench",
            "new_kernel_name": "top_hls",
            "trajectories": [],
            "best_trajectory": 0,
            "best_round": 0,
            "best_cov": 100.0,
        }
        with patch.object(
            tb_optimizer,
            "optimize_tb_public",
            return_value=result,
        ) as optimize:
            tb_optimizer.gen_tb_with_coverage(
                cv,
                diagnostic_assist=True,
                diagnostic_experience_root="unused-test-store",
                effective_model_config=self.config,
            )

        kwargs = optimize.call_args.kwargs
        self.assertTrue(kwargs["diagnostic_assist"])
        self.assertEqual(kwargs["diagnostic_experience_root"], "unused-test-store")
        self.assertIs(kwargs["effective_model_config"], self.config)

    def test_hidden_generation_wrapper_passes_projection_and_model_configuration(self):
        trajectory = {
            "trajectory_idx": 0,
            "best_cov": 95.0,
            "best_tb": "int main() { return 0; }",
            "best_stub": "",
            "best_round": 1,
            "synth_ok": True,
            "qualified": True,
            "rounds": [],
        }
        with patch.object(
            tb_optimizer,
            "run_trajectory",
            return_value=trajectory,
        ) as run:
            result = tb_optimizer.make_golden_hidden_tb(
                orig_code="void top(int *data) {}",
                kernel_name="top",
                pinned_public_hls_decl="void top_hls(int *data);",
                M=1,
                K=1,
                diagnostic_assist=True,
                diagnostic_experience_root="unused-test-store",
                effective_model_config=self.config,
            )

        kwargs = run.call_args.kwargs
        self.assertTrue(result["qualified"])
        self.assertTrue(kwargs["hidden_generation"])
        self.assertTrue(kwargs["diagnostic_assist"])
        self.assertEqual(kwargs["diagnostic_experience_root"], "unused-test-store")
        self.assertIs(kwargs["effective_model_config"], self.config)

    def test_standalone_cli_requires_model_profile_and_passes_resolved_config(self):
        from flow import new as flow_new

        with (
            patch.object(flow_new, "hls_refactor_with_rag", return_value=(True, {})) as run,
            patch.object(flow_new, "print_agrefactorpp_usage_summary"),
            patch.object(
                sys,
                "argv",
                [
                    "flow.new",
                    "--kernel_path",
                    "kernel.cpp",
                    "--kernel_name",
                    "top",
                    "--diagnostic-assist",
                    "--model",
                    "deepseek-v4-flash",
                    "--model-family",
                    "deepseek",
                ],
            ),
        ):
            self.assertEqual(flow_new.main(), 0)

        kwargs = run.call_args.kwargs
        self.assertTrue(kwargs["diagnostic_assist"])
        self.assertEqual(kwargs["effective_model_config"].model_id, "deepseek-v4-flash")


if __name__ == "__main__":
    unittest.main()
