from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from flow.tools import testbench


class ExternalPublicQualificationTests(unittest.TestCase):
    CV = {
        "kernel_name": "source_top",
        "new_kernel_name": "source_top_hls",
        "curr_code": "void source_top() {}",
        "target_profile": {"compile_flags": ()},
        "source_package": {},
    }
    TB = (
        'extern "C" void source_top();\n'
        'extern "C" void source_top_hls();\n'
        "int main() { source_top(); source_top_hls(); return 0; }\n"
    )
    DECL = 'extern "C" void source_top_hls();'
    STUB = 'extern "C" void source_top_hls() {}'

    def _patch_common(self, original_result=None, synth_result=None):
        original_result = original_result or {"status": "ok"}
        synth_result = synth_result or (True, "")
        loader = Mock()
        loader.load_agent.return_value = object()
        return (
            patch.object(testbench, "HLSAgentLoader", return_value=loader),
            patch.object(
                testbench.tools.tb_optimizer,
                "validate_testbench_top_contract",
            ),
            patch.object(
                testbench.tools.tb_optimizer,
                "extract_hls_decl_from_testbench",
                return_value=self.DECL,
            ),
            patch.object(
                testbench.tools.tb_coverage,
                "check_original_execution",
                return_value=original_result,
            ),
            patch.object(
                testbench,
                "isolate_reference_program_entry",
                return_value=self.CV["curr_code"],
            ),
            patch.object(
                testbench.tools.tb_optimizer,
                "_request_cpp_artifact",
                return_value=self.STUB,
            ),
            patch.object(
                testbench.tools.tb_optimizer,
                "validate_stub_contract",
            ),
            patch.object(
                testbench.tools.tb_optimizer,
                "_synth_check",
                return_value=synth_result,
            ),
        )

    def test_external_public_runs_original_then_synth_before_return(self):
        events = []
        patches = self._patch_common()
        with patches[0], patches[1], patches[2], \
             patch.object(
                 testbench.tools.tb_coverage,
                 "check_original_execution",
                 side_effect=lambda *args, **kwargs: (
                     events.append("original") or {"status": "ok"}
                 ),
             ), patches[4], patches[5], patches[6], \
             patch.object(
                 testbench.tools.tb_optimizer,
                 "_synth_check",
                 side_effect=lambda *args, **kwargs: (
                     events.append("synth") or (True, "")
                 ),
             ):
            result = testbench.qualify_external_public_testbench(
                dict(self.CV),
                self.TB,
                llm_config={},
            )

        self.assertEqual(result, self.TB)
        self.assertEqual(events, ["original", "synth"])

    def test_external_public_original_failure_blocks_synth_probe(self):
        patches = self._patch_common(
            original_result={
                "status": "run_failed",
                "failure_owner": "original",
                "run_stderr": "original failure",
            }
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patch.object(
                 testbench.tools.tb_optimizer,
                 "_request_cpp_artifact",
             ) as request, patches[6], \
             patch.object(
                 testbench.tools.tb_optimizer,
                 "_synth_check",
             ) as synth:
            with self.assertRaises(
                testbench.tools.tb_optimizer.TestbenchGenerationExhausted
            ) as caught:
                testbench.qualify_external_public_testbench(
                    dict(self.CV),
                    self.TB,
                    llm_config={},
                )

        self.assertEqual(
            caught.exception.to_dict()["stage"],
            "external_public_original_qualification",
        )
        request.assert_not_called()
        synth.assert_not_called()

    def test_external_public_synth_failure_blocks_abi_binding(self):
        patches = self._patch_common(
            synth_result=(False, "unsupported ABI"),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patches[5], patches[6], patches[7]:
            with self.assertRaises(
                testbench.tools.tb_optimizer.TestbenchGenerationExhausted
            ) as caught:
                testbench.qualify_external_public_testbench(
                    dict(self.CV),
                    self.TB,
                    llm_config={},
                )

        self.assertEqual(
            caught.exception.to_dict()["stage"],
            "external_public_abi_synthesis_qualification",
        )


if __name__ == "__main__":
    unittest.main()
