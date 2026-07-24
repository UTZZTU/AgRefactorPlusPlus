from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch

from flow import new as flow_new
from flow.tools import tb_optimizer
from agrefactor.product import source_bootstrap


class P0HiddenBoundaryTests(unittest.TestCase):
    PUBLIC_DECL = (
        'extern "C" void process_top_hls('
        'int n, int *input, int *output);'
    )

    def test_public_optimizer_has_no_hidden_input_parameter(self):
        parameters = inspect.signature(
            tb_optimizer.optimize_tb_public
        ).parameters
        self.assertNotIn("hidden_sig_spec", parameters)
        self.assertNotIn("pinned_hls_decl", parameters)

    def test_public_entrypoint_has_no_hidden_input_parameter(self):
        parameters = inspect.signature(
            tb_optimizer.gen_tb_with_coverage
        ).parameters
        self.assertNotIn("hidden_sig_spec", parameters)
        self.assertNotIn("pinned_hls_decl", parameters)

    def test_public_prompt_has_no_hidden_channel(self):
        prompt = tb_optimizer._initial_user_message(
            "void process_top(){}",
            "process_top",
        )
        self.assertNotIn("canonical hidden testbench", prompt)
        self.assertNotIn("hidden_sig_spec", prompt)

    def test_held_out_prompt_uses_public_derived_abi(self):
        prompt = tb_optimizer._initial_user_message(
            "void process_top(){}",
            "process_top",
            pinned_public_hls_decl=self.PUBLIC_DECL,
        )
        self.assertIn("FROZEN PUBLIC-DERIVED", prompt)
        self.assertIn(self.PUBLIC_DECL, prompt)
        self.assertNotIn("canonical hidden testbench", prompt)

    def test_held_out_generation_requires_public_abi(self):
        with self.assertRaisesRegex(
            ValueError,
            "frozen Public-derived ABI",
        ):
            tb_optimizer.make_golden_hidden_tb(
                orig_code="void process_top(){}",
                kernel_name="process_top",
                pinned_public_hls_decl="",
                M=1,
                K=1,
            )

    def test_held_out_trajectory_receives_public_abi_only(self):
        trajectory = {
            "trajectory_idx": 0,
            "best_cov": 91.0,
            "best_tb": "int main(){return 0;}",
            "best_stub": "void process_top_hls(){}",
            "best_empty_stub": "void process_top_hls(){}",
            "best_round": 1,
            "final_text": "",
            "rounds": [],
            "synth_ok": True,
            "qualified": True,
            "trajectory_status": "qualified",
        }
        with patch.object(
            tb_optimizer,
            "run_trajectory",
            return_value=trajectory,
        ) as run:
            result = tb_optimizer.make_golden_hidden_tb(
                orig_code="void process_top(){}",
                kernel_name="process_top",
                pinned_public_hls_decl=self.PUBLIC_DECL,
                M=1,
                K=1,
            )
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["pinned_hls_decl"], self.PUBLIC_DECL)
        self.assertFalse(kwargs["emit_final_text"])
        self.assertNotIn("sig_spec_constraint", kwargs)
        self.assertNotIn("hidden_sig_spec", result)
        self.assertEqual(result["public_hls_decl"], self.PUBLIC_DECL)

    def test_boundary_order_is_public_candidate_hidden(self):
        boundary = flow_new._build_model_data_boundary(
            event_order=[
                "public_generation",
                "candidate_generation",
                "hidden_generation",
            ],
            public_hls_decl=self.PUBLIC_DECL,
            hidden_generation_enabled=True,
        )
        self.assertTrue(boundary["complete"])
        self.assertTrue(boundary["hidden_generation_after_candidate"])
        self.assertFalse(
            source_bootstrap._hidden_exposure_from_boundary(boundary)
        )

    def test_boundary_fails_closed_on_reverse_input(self):
        boundary = flow_new._build_model_data_boundary(
            event_order=[
                "public_generation",
                "candidate_generation",
                "hidden_generation",
            ],
            public_hls_decl=self.PUBLIC_DECL,
            hidden_generation_enabled=True,
        )
        boundary["candidate_generation_hidden_inputs"] = [
            "hidden_testbench"
        ]
        self.assertTrue(
            source_bootstrap._hidden_exposure_from_boundary(boundary)
        )

    def test_boundary_fails_closed_on_wrong_order(self):
        boundary = flow_new._build_model_data_boundary(
            event_order=[
                "hidden_generation",
                "public_generation",
                "candidate_generation",
            ],
            public_hls_decl=self.PUBLIC_DECL,
            hidden_generation_enabled=True,
        )
        self.assertFalse(boundary["complete"])
        self.assertTrue(
            source_bootstrap._hidden_exposure_from_boundary(boundary)
        )

    def test_cached_held_out_as_public_is_rejected_prelaunch(self):
        with self.assertRaisesRegex(
            ValueError,
            "one-way evaluation boundary",
        ):
            flow_new.hls_refactor_with_rag(
                kernel_path="/does/not/matter.cpp",
                kernel_name="process_top",
                use_cached_tb_as_public=True,
            )


if __name__ == "__main__":
    unittest.main()
