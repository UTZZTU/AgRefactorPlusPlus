from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agrefactor.cli import build_parser
from agrefactor.compat import (
    LegacyRefactorSettings,
    build_legacy_refactor_kwargs,
)
from agrefactor.config import (
    RunMode,
    TaskSpec,
    TestGenerationProfile,
    resolve_target_profile,
    resolve_test_generation_profile,
)
from agrefactor.models import resolve_model_runtime
from agrefactor.product import (
    SourceBootstrapRequest,
    SourceRunLayout,
    build_test_source_plan,
)
from agrefactor.product import source_bootstrap
from agrefactor.runtime.budget_profile import (
    DEFAULT_SOURCE_RUN_BUDGET_PROFILE,
)
from flow import new as flow_new
from flow.tools import tb_optimizer


class P0DualGenerationProfilesTests(unittest.TestCase):
    def _request(
        self,
        root: Path,
        *,
        profile: TestGenerationProfile = (
            TestGenerationProfile.LIGHTWEIGHT
        ),
        public_paths=(),
        hidden_mode: str | None = None,
        public_rounds: int = 3,
        trajectories: int = 3,
    ) -> SourceBootstrapRequest:
        source = root / "kernel.cpp"
        source.write_text(
            'extern "C" int top(int x) { return x; }\n',
            encoding="utf-8",
        )
        runtime = resolve_model_runtime("deepseek-v4-flash")
        return SourceBootstrapRequest(
            source_path=source,
            top_function="top",
            mode=RunMode.REFACTOR,
            effective_model_config=runtime.effective_config,
            target=resolve_target_profile(None),
            test_source_plan=build_test_source_plan(
                public_paths=public_paths,
                hidden_mode=hidden_mode,
            ),
            budget_contract=DEFAULT_SOURCE_RUN_BUDGET_PROFILE.resolve(),
            max_candidate_repairs=2,
            run_id="step-c-test",
            test_generation_profile=profile,
            public_coverage_rounds=public_rounds,
            test_generation_trajectories=trajectories,
        )

    def test_profile_default_resolves_to_lightweight(self):
        self.assertIs(
            resolve_test_generation_profile(None),
            TestGenerationProfile.LIGHTWEIGHT,
        )

    def test_source_cli_defaults_to_lightweight(self):
        args = build_parser().parse_args(
            [
                "refactor",
                "kernel.cpp",
                "--top",
                "top",
                "--model",
                "deepseek-v4-flash",
            ]
        )
        self.assertEqual(
            args.test_generation_profile,
            TestGenerationProfile.LIGHTWEIGHT.value,
        )
        self.assertEqual(args.public_coverage_rounds, 3)
        self.assertEqual(args.test_generation_trajectories, 3)

    def test_source_cli_accepts_explicit_coverage_profile(self):
        args = build_parser().parse_args(
            [
                "refactor",
                "kernel.cpp",
                "--top",
                "top",
                "--model",
                "deepseek-v4-flash",
                "--test-generation-profile",
                "coverage-enhanced",
                "--public-coverage-rounds",
                "5",
                "--test-generation-trajectories",
                "4",
            ]
        )
        self.assertEqual(
            args.test_generation_profile,
            "coverage-enhanced",
        )
        self.assertEqual(args.public_coverage_rounds, 5)
        self.assertEqual(args.test_generation_trajectories, 4)

    def test_source_cli_rejects_nonpositive_generation_counts(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "refactor",
                    "kernel.cpp",
                    "--top",
                    "top",
                    "--model",
                    "deepseek-v4-flash",
                    "--public-coverage-rounds",
                    "0",
                ]
            )

    def test_source_request_persists_profile_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            request = self._request(
                Path(directory),
                profile=TestGenerationProfile.COVERAGE_ENHANCED,
                public_rounds=5,
                trajectories=4,
            )
            payload = request.to_dict()
        self.assertEqual(
            payload["test_generation_profile"],
            "coverage-enhanced",
        )
        self.assertEqual(payload["public_coverage_rounds"], 5)
        self.assertEqual(
            payload["test_generation_trajectories"],
            4,
        )

    def test_source_request_rejects_invalid_generation_count(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                ValueError,
                "public_coverage_rounds",
            ):
                self._request(
                    Path(directory),
                    public_rounds=0,
                )

    def test_lightweight_auto_plan_uses_single_pass_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            request = self._request(Path(directory))
            layout = SourceRunLayout.create(
                request.run_id,
                artifact_base=Path(directory) / "artifacts",
                work_base=Path(directory) / "work",
            )
            settings = source_bootstrap._build_generation_settings(
                request,
                layout,
                debug=False,
            )
        self.assertFalse(settings.enable_tb_coverage_loop)
        self.assertEqual(settings.public_tb_rounds, 1)
        self.assertEqual(settings.public_tb_trajectories, 1)
        self.assertTrue(settings.enable_hidden_tb_eval)
        self.assertEqual(settings.hidden_tb_rounds, 1)
        self.assertEqual(settings.hidden_tb_trajectories, 1)

    def test_coverage_auto_plan_uses_configured_trajectories(self):
        with tempfile.TemporaryDirectory() as directory:
            request = self._request(
                Path(directory),
                profile=TestGenerationProfile.COVERAGE_ENHANCED,
                public_rounds=5,
                trajectories=4,
            )
            layout = SourceRunLayout.create(
                request.run_id,
                artifact_base=Path(directory) / "artifacts",
                work_base=Path(directory) / "work",
            )
            settings = source_bootstrap._build_generation_settings(
                request,
                layout,
                debug=False,
            )
        self.assertTrue(settings.enable_tb_coverage_loop)
        self.assertEqual(settings.public_tb_rounds, 5)
        self.assertEqual(settings.public_tb_trajectories, 4)
        self.assertEqual(settings.hidden_tb_rounds, 6)
        self.assertEqual(settings.hidden_tb_trajectories, 4)

    def test_provided_public_does_not_run_public_coverage_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public = root / "public.cpp"
            public.write_text("int main(){return 0;}\n", encoding="utf-8")
            request = self._request(
                root,
                profile=TestGenerationProfile.COVERAGE_ENHANCED,
                public_paths=(public,),
                hidden_mode="auto",
            )
            layout = SourceRunLayout.create(
                request.run_id,
                artifact_base=root / "artifacts",
                work_base=root / "work",
            )
            settings = source_bootstrap._build_generation_settings(
                request,
                layout,
                debug=False,
            )
        self.assertFalse(settings.enable_tb_coverage_loop)
        self.assertTrue(settings.enable_hidden_tb_eval)
        self.assertEqual(settings.hidden_tb_trajectories, 3)

    def test_legacy_kwargs_forward_profile_and_public_trajectories(self):
        settings = LegacyRefactorSettings(
            generation_only=True,
            test_generation_profile="coverage-enhanced",
            enable_tb_coverage_loop=True,
            public_tb_rounds=5,
            public_tb_trajectories=4,
        )
        kwargs = build_legacy_refactor_kwargs(
            TaskSpec(
                task_id="step-c",
                kernel_path="kernel.cpp",
                kernel_name="top",
            ),
            settings,
        )
        self.assertEqual(
            kwargs["test_generation_profile"],
            "coverage-enhanced",
        )
        self.assertEqual(kwargs["public_tb_trajectories"], 4)

    def test_explicit_lightweight_rejects_coverage_loop_mismatch(self):
        with patch.object(
            flow_new,
            "resolve_runtime_llm_config",
            return_value={},
        ):
            with self.assertRaisesRegex(
                ValueError,
                "lightweight",
            ):
                flow_new.hls_refactor_with_rag(
                    kernel_path="/not/read.cpp",
                    kernel_name="top",
                    test_generation_profile="lightweight",
                    enable_tb_coverage_loop=True,
                )

    def test_public_optimizer_selects_best_qualified_trajectory(self):
        def trajectory(**kwargs):
            index = kwargs["trajectory_idx"]
            return {
                "trajectory_idx": index,
                "best_round": 1,
                "best_cov": [61.0, 94.0, 80.0][index],
                "best_tb": f"tb-{index}",
                "best_stub": f"stub-{index}",
                "best_empty_stub": f"empty-{index}",
                "best_uncovered_lines": [],
                "final_text": f"instruction-{index}",
                "rounds": [],
                "synth_ok": True,
                "synth_error": "",
                "qualified": True,
                "trajectory_status": "qualified",
            }

        with patch.object(
            tb_optimizer,
            "run_trajectory",
            side_effect=trajectory,
        ) as run:
            result = tb_optimizer.optimize_tb_public(
                orig_code="void top(){}",
                kernel_name="top",
                K=2,
                M=3,
            )
        self.assertEqual(run.call_count, 3)
        self.assertEqual(result["best_trajectory"], 1)
        self.assertEqual(result["best_tb"], "tb-1")
        self.assertEqual(len(result["trajectories"]), 3)

    def test_public_optimizer_rejects_unqualified_trajectories(self):
        failed = {
            "trajectory_idx": 0,
            "best_round": 1,
            "best_cov": 0.0,
            "best_tb": "",
            "best_stub": "",
            "best_empty_stub": "",
            "best_uncovered_lines": [],
            "final_text": "",
            "rounds": [],
            "synth_ok": False,
            "synth_error": "compile failed",
            "qualified": False,
            "trajectory_status": "coverage_failed",
        }
        with patch.object(
            tb_optimizer,
            "run_trajectory",
            return_value=failed,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "no qualified trajectory",
            ):
                tb_optimizer.optimize_tb_public(
                    orig_code="void top(){}",
                    kernel_name="top",
                    K=1,
                    M=2,
                )

    def test_coverage_entry_records_trajectory_artifacts(self):
        result = {
            "best_tb": "int main(){return 0;}",
            "best_stub": "void top_hls(){}",
            "best_cov": 91.0,
            "best_round": 2,
            "best_trajectory": 1,
            "instruction": "keep ABI",
            "new_kernel_name": "top_hls",
            "trajectories": [{"trajectory_idx": 0}, {"trajectory_idx": 1}],
            "qualified": True,
        }
        cv = {
            "orig_code": "void top(){}",
            "kernel_name": "top",
            "test_generation_profile": "coverage-enhanced",
            "public_tb_artifact_dir": "/tmp/public-coverage",
        }
        with patch.object(
            tb_optimizer,
            "optimize_tb_public",
            return_value=result,
        ) as optimize:
            generated = tb_optimizer.gen_tb_with_coverage(
                cv,
                K=4,
                M=2,
            )
        self.assertEqual(generated[0], result["best_tb"])
        self.assertEqual(
            cv["public_testbench_coverage"]["trajectory_count"],
            2,
        )
        self.assertEqual(
            optimize.call_args.kwargs["artifact_root"],
            "/tmp/public-coverage",
        )


if __name__ == "__main__":
    unittest.main()
