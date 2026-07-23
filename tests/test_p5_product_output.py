from __future__ import annotations

from argparse import Namespace
from contextlib import redirect_stdout
from decimal import Decimal
import io
import json
from pathlib import Path
import tempfile
import unittest

from agrefactor.config import RunMode
from agrefactor.product.run_output import (
    ProductOutputMode,
    build_product_summary,
    build_rejection_summary,
    capture_product_streams,
    finalize_product_artifacts,
    render_product_output,
    resolve_output_mode,
    write_rejection_support_artifacts,
)
from agrefactor.runtime import (
    BudgetUsage,
    PhaseResult,
    PhaseStatus,
    RunPhase,
    RunResult,
    RunStatus,
)


class P5ProductOutputTests(unittest.TestCase):
    def _identity(self, root: Path, *, exceeded: bool = False) -> dict:
        candidate = root / "refactor" / "final_candidate.cpp"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text("void process_top_hls() {}\n", encoding="utf-8")
        source_request = root / "bootstrap" / "source_request.json"
        source_request.parent.mkdir(parents=True, exist_ok=True)
        source_request.write_text(
            json.dumps({"max_candidate_repairs": 2}),
            encoding="utf-8",
        )
        payload = {
            "schema_version": 1,
            "run_id": "run-1",
            "execution_id": "exec-1",
            "request_identity_sha256": "1" * 64,
            "cache_identity_sha256": "2" * 64,
            "bundle_sha256": "3" * 64,
            "source": {
                "top_function": "process_top",
                "path": "/safe/kernel.cpp",
                "sha256": "4" * 64,
            },
            "normalized_task": {"value": {"mode": "refactor"}},
            "model": {
                "value": {
                    "model_id": "deepseek-v4-flash",
                    "api_key_env": "DEEPSEEK_API_KEY",
                },
                "pricing": {
                    "snapshot_sha256": "5" * 64,
                    "source_status": "official_verified",
                    "cost_estimation_quality": "approximate",
                    "currency": "CNY",
                    "actual_estimation": {
                        "quality": "approximate",
                        "amounts_by_currency": {"CNY": "0.42"},
                        "is_invoice": False,
                    },
                    "is_invoice": False,
                },
            },
            "prompt_identity": {
                "actual_call_count": 1,
                "calls": [
                    {
                        "template_id": "candidate_repair",
                        "message_sequence_sha256": "6" * 64,
                    }
                ],
            },
            "suites": [
                {
                    "suite_id": "public-001",
                    "split": "public",
                    "evaluation_status": "passed",
                    "qualification_status": "qualified",
                    "operator_artifact_path": "/hidden/public.cpp",
                },
                {
                    "suite_id": "hidden-001",
                    "split": "hidden",
                    "evaluation_status": "passed",
                    "qualification_status": "qualified",
                    "operator_artifact_path": "/secret/hidden.cpp",
                },
            ],
            "budget": {
                "contract": {
                    "system_defaults": {
                        "max_llm_calls": 8,
                        "max_tool_calls": 20,
                        "max_compile_calls": 10,
                        "max_csim_calls": 6,
                        "max_csynth_calls": 3,
                        "max_wall_time_s": 1800,
                    },
                    "system_safety_ceilings": {
                        "max_llm_calls": 20,
                        "max_tool_calls": 50,
                        "max_compile_calls": 30,
                        "max_csim_calls": 20,
                        "max_csynth_calls": 10,
                        "max_wall_time_s": 7200,
                    },
                    "user_requested": {
                        "max_llm_calls": None,
                        "max_tool_calls": None,
                        "max_compile_calls": None,
                        "max_csim_calls": None,
                        "max_csynth_calls": None,
                        "max_wall_time_s": None,
                    },
                    "effective_hard_limits": {
                        "max_llm_calls": 8,
                        "max_tool_calls": 20,
                        "max_compile_calls": 10,
                        "max_csim_calls": 6,
                        "max_csynth_calls": 3,
                        "max_wall_time_s": 1800,
                    },
                    "soft_usage_budgets": {
                        "token_budget": 50000,
                        "cost_budget": "1.00",
                        "currency": "CNY",
                        "enforcement": "observed_only",
                        "blocking": False,
                    },
                },
                "usage": {
                    "llm_calls": 6,
                    "tool_calls": 11,
                    "compile_calls": 5,
                    "csim_calls": 4,
                    "csynth_calls": 2,
                    "tokens": 32418,
                    "cost_usd": 0.0,
                    "elapsed_s": 1112.0,
                    "costs_by_currency": {"CNY": "0.42"},
                },
                "remaining_hard_budget": {
                    "max_llm_calls": 2,
                    "max_tool_calls": 9,
                    "max_compile_calls": 5,
                    "max_csim_calls": 2,
                    "max_csynth_calls": 1,
                    "max_wall_time_s": 688,
                },
                "hard_budget_exhaustion": None,
                "soft_budget_exceeded": {
                    "tokens": exceeded,
                    "cost": False,
                },
            },
        }
        (root / "execution_identity.json").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )
        return payload

    def _result(self) -> RunResult:
        return RunResult(
            run_id="run-1",
            task_id="task-1",
            mode=RunMode.REFACTOR,
            status=RunStatus.SUCCEEDED,
            phases=(
                PhaseResult(
                    phase=RunPhase.REFACTOR,
                    status=PhaseStatus.SUCCEEDED,
                    summary="candidate accepted",
                    metadata={
                        "accepted": True,
                        "repair_attempt_count": 1,
                        "last_validation_state": "accepted",
                    },
                ),
            ),
            budget_usage=BudgetUsage(
                llm_calls=6,
                tool_calls=11,
                compile_calls=5,
                csim_calls=4,
                csynth_calls=2,
                tokens=32418,
                cost_usd=0.0,
                elapsed_s=1112.0,
                costs_by_currency={"CNY": Decimal("0.42")},
            ),
            metadata={"execution_identity": {"execution_id": "exec-1"}},
        )

    def test_default_mode(self):
        self.assertIs(
            resolve_output_mode(Namespace(json_output=False, verbose=False, debug=False)),
            ProductOutputMode.DEFAULT,
        )

    def test_json_mode(self):
        self.assertIs(
            resolve_output_mode(Namespace(json_output=True, verbose=False, debug=False)),
            ProductOutputMode.JSON,
        )

    def test_output_modes_are_mutually_exclusive(self):
        with self.assertRaises(ValueError):
            resolve_output_mode(Namespace(json_output=True, verbose=True, debug=False))

    def test_accepted_summary_uses_identity_as_usage_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._identity(root)
            summary = build_product_summary(self._result(), artifact_root=root)
            self.assertEqual(summary["status"], "accepted")
            self.assertEqual(summary["usage"]["tokens"], 32418)
            self.assertEqual(summary["cost_estimation_quality"], "approximate")

    def test_summary_hides_hidden_and_operator_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._identity(root)
            summary = build_product_summary(self._result(), artifact_root=root)
            encoded = json.dumps(summary)
            self.assertNotIn("/secret/hidden.cpp", encoded)
            self.assertNotIn("operator_artifact_path", encoded)

    def test_default_output_is_concise_not_full_json(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._identity(root)
            summary = build_product_summary(self._result(), artifact_root=root)
            out = io.StringIO()
            render_product_output(summary, mode="default", stdout=out)
            text = out.getvalue()
            self.assertIn("Status: accepted", text)
            self.assertNotIn('"phases"', text)
            self.assertNotIn("message_sequence_sha256", text)

    def test_default_output_labels_soft_and_hard_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._identity(root)
            summary = build_product_summary(self._result(), artifact_root=root)
            out = io.StringIO()
            render_product_output(summary, mode="default", stdout=out)
            text = out.getvalue()
            self.assertIn("soft, observed only", text)
            self.assertIn("LLM calls: 6 / 8 (hard)", text)
            self.assertIn("Estimated cost: 0.42 CNY / 1.00 CNY", text)

    def test_json_output_is_one_stable_object_with_authority_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._identity(root)
            summary = build_product_summary(self._result(), artifact_root=root)
            out = io.StringIO()
            render_product_output(summary, mode="json", stdout=out)
            payload = json.loads(out.getvalue())
            for key in (
                "system_defaults",
                "system_safety_ceilings",
                "user_requested",
                "effective_hard_limits",
                "soft_budgets",
                "usage",
                "remaining",
                "hard_budget_exhausted",
                "soft_budget_exceeded",
                "pricing",
                "cost_estimation_quality",
            ):
                self.assertIn(key, payload)

    def test_verbose_output_contains_phase_summary_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._identity(root)
            summary = build_product_summary(self._result(), artifact_root=root)
            out = io.StringIO()
            render_product_output(summary, mode="verbose", stdout=out)
            text = out.getvalue()
            self.assertIn("Phases:", text)
            self.assertIn("refactor: succeeded", text)
            self.assertNotIn("/secret/hidden.cpp", text)

    def test_debug_renderer_does_not_add_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._identity(root)
            summary = build_product_summary(self._result(), artifact_root=root)
            out = io.StringIO()
            render_product_output(summary, mode="debug", stdout=out)
            self.assertNotIn("DEEPSEEK_API_KEY=", out.getvalue())

    def test_default_capture_suppresses_terminal(self):
        with tempfile.TemporaryDirectory() as temp:
            terminal = io.StringIO()
            with capture_product_streams(temp, stdout=terminal, stderr=terminal) as capture:
                print("legacy noise")
            self.assertEqual(terminal.getvalue(), "")
            self.assertIn("legacy noise", capture.stdout_path.read_text())

    def test_debug_capture_tees_and_persists(self):
        with tempfile.TemporaryDirectory() as temp:
            terminal = io.StringIO()
            with capture_product_streams(
                temp,
                stdout=terminal,
                stderr=terminal,
                tee_debug=True,
            ) as capture:
                print("debug noise")
            self.assertIn("debug noise", terminal.getvalue())
            self.assertIn("debug noise", capture.stdout_path.read_text())

    def test_finalize_writes_all_required_product_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "artifacts"
            work = base / "work"
            root.mkdir()
            work.mkdir()
            self._identity(root)
            (root / "run_artifact_manifest.json").write_text(
                json.dumps({"schema_version": 1, "files": []}),
                encoding="utf-8",
            )
            with capture_product_streams(work) as captured:
                print("captured")
            finalize_product_artifacts(
                self._result(),
                artifact_root=root,
                work_root=work,
                captured=captured,
            )
            for name in (
                "full_result.json",
                "model_calls.json",
                "tool_calls.json",
                "stdout.log",
                "stderr.log",
                "run_artifact_manifest.json",
            ):
                self.assertTrue((root / name).is_file(), name)

    def test_manifest_hashes_include_product_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "artifacts"
            work = base / "work"
            root.mkdir()
            work.mkdir()
            self._identity(root)
            (root / "run_artifact_manifest.json").write_text(
                json.dumps({"schema_version": 1, "files": []}),
                encoding="utf-8",
            )
            with capture_product_streams(work) as captured:
                pass
            finalize_product_artifacts(
                self._result(),
                artifact_root=root,
                work_root=work,
                captured=captured,
            )
            manifest = json.loads((root / "run_artifact_manifest.json").read_text())
            names = {item["relative_path"] for item in manifest["files"]}
            self.assertTrue(
                {"full_result.json", "model_calls.json", "tool_calls.json", "stdout.log", "stderr.log"}
                <= names
            )

    def test_model_calls_is_hash_only(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "artifacts"
            work = base / "work"
            root.mkdir()
            work.mkdir()
            self._identity(root)
            (root / "run_artifact_manifest.json").write_text(
                json.dumps({"schema_version": 1, "files": []}), encoding="utf-8"
            )
            with capture_product_streams(work) as captured:
                pass
            finalize_product_artifacts(
                self._result(), artifact_root=root, work_root=work, captured=captured
            )
            payload = json.loads((root / "model_calls.json").read_text())
            self.assertFalse(payload["plaintext_prompts_persisted"])
            self.assertNotIn("full secret prompt", json.dumps(payload))

    def test_tool_calls_redacts_secret_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "artifacts"
            work = base / "work"
            root.mkdir()
            work.mkdir()
            self._identity(root)
            invocation = work / "x_invocation.json"
            invocation.write_text(
                json.dumps({"api_key": "secret-value", "status": "completed"}),
                encoding="utf-8",
            )
            (root / "run_artifact_manifest.json").write_text(
                json.dumps({"schema_version": 1, "files": []}), encoding="utf-8"
            )
            with capture_product_streams(work) as captured:
                pass
            finalize_product_artifacts(
                self._result(), artifact_root=root, work_root=work, captured=captured
            )
            text = (root / "tool_calls.json").read_text()
            self.assertNotIn("secret-value", text)
            self.assertIn("<redacted>", text)

    def test_rejection_support_artifacts_are_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._identity(root)
            rejection = {
                "kind": "safety_ceiling_exceeded",
                "resource": "max_llm_calls",
                "user_requested": 21,
                "system_safety_ceiling": 20,
            }
            (root / "request_rejection.json").write_text(
                json.dumps(rejection), encoding="utf-8"
            )
            write_rejection_support_artifacts(root, rejection=rejection)
            for name in (
                "full_result.json",
                "model_calls.json",
                "tool_calls.json",
                "stdout.log",
                "stderr.log",
            ):
                self.assertTrue((root / name).is_file())

    def test_rejection_summary_is_machine_renderable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            identity = self._identity(root)
            identity["budget"]["contract"] = {
                "system_defaults": {},
                "system_safety_ceilings": {"max_llm_calls": 20},
                "user_requested": {"max_llm_calls": 21},
                "effective_hard_limits": None,
                "soft_usage_budgets": {},
            }
            (root / "execution_identity.json").write_text(
                json.dumps(identity), encoding="utf-8"
            )
            rejection = {
                "kind": "safety_ceiling_exceeded",
                "resource": "max_llm_calls",
                "user_requested": 21,
                "system_safety_ceiling": 20,
            }
            (root / "request_rejection.json").write_text(
                json.dumps(rejection), encoding="utf-8"
            )
            summary = build_rejection_summary(root)
            self.assertEqual(summary["status"], "rejected")
            self.assertEqual(summary["failed_stage"], "request")

    def test_soft_budget_exceeded_does_not_change_acceptance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._identity(root, exceeded=True)
            summary = build_product_summary(self._result(), artifact_root=root)
            self.assertEqual(summary["status"], "accepted")
            out = io.StringIO()
            render_product_output(summary, mode="default", stdout=out)
            self.assertIn("soft budget exceeded", out.getvalue())


if __name__ == "__main__":
    unittest.main()
