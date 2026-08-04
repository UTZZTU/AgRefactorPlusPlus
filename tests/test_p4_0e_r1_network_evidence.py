from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agrefactor.models import resolve_model_runtime
from agrefactor.runtime import BudgetLimits, BudgetManager


_ROOT = Path(__file__).resolve().parents[1]
_TOOL_PATH = _ROOT / "tools" / "p4_0e_real_network_smoke.py"
_SPEC = importlib.util.spec_from_file_location(
    "p4_0e_real_network_smoke_r1",
    _TOOL_PATH,
)
assert _SPEC is not None and _SPEC.loader is not None
_TOOL = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_TOOL)
_PACKAGE_SHA = "a" * 64


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args],
        text=True,
    ).strip()


def _repository(root: Path) -> tuple[Path, str]:
    repo = root / "repo"
    sample = repo / "tests/fixtures/p4_0d_rtl_cosim/reference.cpp"
    sample.parent.mkdir(parents=True)
    sample.write_text(
        'extern "C" void reference_top(int *value) { value[0] += 1; }\n',
        encoding="utf-8",
    )
    subprocess.check_call(["git", "init", "-q", str(repo)])
    _git(repo, "config", "user.name", "P4E R1 Test")
    _git(repo, "config", "user.email", "p4e-r1@example.invalid")
    _git(repo, "checkout", "-q", "-b", "stage2-general-feedback")
    _git(repo, "add", "--", ".")
    _git(repo, "commit", "-q", "-m", "fixture")
    return repo, _git(repo, "rev-parse", "HEAD")


def _selection(*, failing: bool = False, calls: list[dict] | None = None):
    selection = resolve_model_runtime(None)
    provider = selection.registry.get_provider(
        selection.effective_config.provider_name
    )

    class Completions:
        def create(self, **kwargs):
            if calls is not None:
                calls.append(dict(kwargs))
            if failing:
                raise RuntimeError("provider-secret-error-text")
            return {
                "id": "p4e-r1-unit",
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": "AGREFACTOR_P4_0E_R1_NETWORK_OK",
                            "reasoning_content": "private-not-persisted",
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 2,
                },
            }

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=Completions())
    )
    provider._client_factory = lambda **_: client
    return selection


class P4ER1NetworkEvidenceTests(unittest.TestCase):
    def test_authoritative_smoke_records_exact_budget_and_identity(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo, head = _repository(root)
            output = root / "evidence"
            calls: list[dict] = []
            with patch.dict(
                os.environ,
                {"DEEPSEEK_API_KEY": "unit-secret"},
                clear=False,
            ):
                payload = _TOOL.run_network_smoke(
                    output=output,
                    invocation_cwd=repo,
                    repository_root=repo,
                    expected_head=head,
                    run_id="p4e-r1-unit-pass",
                    package_manifest_sha256=_PACKAGE_SHA,
                    selection=_selection(calls=calls),
                )
            self.assertEqual(len(calls), 1)
            self.assertEqual(payload["repository"]["head"], head)
            self.assertTrue(payload["repository"]["clean"])
            self.assertEqual(
                payload["budget"]["usage_before"]["llm_calls"],
                0,
            )
            self.assertEqual(
                payload["budget"]["usage_after_call"]["llm_calls"],
                1,
            )
            self.assertEqual(
                payload["budget"]["usage_after_observed"]["llm_calls"],
                1,
            )
            self.assertTrue(payload["budget"]["prospective_check_passed"])
            self.assertTrue(payload["budget"]["exact_once_llm_accounting"])
            self.assertFalse(
                payload["hidden_boundary"]["hidden_exposed_to_model"]
            )
            serialized = json.dumps(payload, sort_keys=True)
            self.assertNotIn("unit-secret", serialized)
            self.assertNotIn("private-not-persisted", serialized)
            artifact = output / "p4_0e_r1_network_evidence.json"
            self.assertTrue(artifact.is_file())
            self.assertTrue(
                artifact.with_suffix(".json.sha256").is_file()
            )

    def test_dirty_or_wrong_repository_blocks_before_provider(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo, head = _repository(root)
            (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
            calls: list[dict] = []
            with patch.dict(
                os.environ,
                {"DEEPSEEK_API_KEY": "unit-secret"},
                clear=False,
            ):
                with self.assertRaises(_TOOL.NetworkEvidenceClosureError):
                    _TOOL.run_network_smoke(
                        output=root / "evidence",
                        invocation_cwd=repo,
                        repository_root=repo,
                        expected_head=head,
                        run_id="p4e-r1-unit-dirty",
                        package_manifest_sha256=_PACKAGE_SHA,
                        selection=_selection(calls=calls),
                    )
            self.assertEqual(calls, [])

    def test_zero_llm_budget_blocks_before_provider(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo, head = _repository(root)
            calls: list[dict] = []
            manager = BudgetManager(BudgetLimits(max_llm_calls=0))
            with patch.dict(
                os.environ,
                {"DEEPSEEK_API_KEY": "unit-secret"},
                clear=False,
            ):
                with self.assertRaises(_TOOL.NetworkEvidenceClosureError):
                    _TOOL.run_network_smoke(
                        output=root / "evidence",
                        invocation_cwd=repo,
                        repository_root=repo,
                        expected_head=head,
                        run_id="p4e-r1-unit-budget",
                        package_manifest_sha256=_PACKAGE_SHA,
                        selection=_selection(calls=calls),
                        budget=manager,
                    )
            self.assertEqual(calls, [])
            self.assertEqual(manager.snapshot().llm_calls, 0)
            payload = json.loads(
                (root / "evidence/p4_0e_r1_network_evidence.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                payload["reason_code"],
                "llm_budget_blocked_prelaunch",
            )

    def test_provider_failure_consumes_once_without_raw_error(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo, head = _repository(root)
            calls: list[dict] = []
            manager = BudgetManager(BudgetLimits(max_llm_calls=1))
            with patch.dict(
                os.environ,
                {"DEEPSEEK_API_KEY": "unit-secret"},
                clear=False,
            ):
                with self.assertRaises(_TOOL.NetworkEvidenceClosureError):
                    _TOOL.run_network_smoke(
                        output=root / "evidence",
                        invocation_cwd=repo,
                        repository_root=repo,
                        expected_head=head,
                        run_id="p4e-r1-unit-provider-failure",
                        package_manifest_sha256=_PACKAGE_SHA,
                        selection=_selection(failing=True, calls=calls),
                        budget=manager,
                    )
            self.assertEqual(len(calls), 1)
            self.assertEqual(manager.snapshot().llm_calls, 1)
            serialized = (
                root / "evidence/p4_0e_r1_network_evidence.json"
            ).read_text(encoding="utf-8")
            self.assertNotIn("provider-secret-error-text", serialized)
            self.assertNotIn("unit-secret", serialized)
            payload = json.loads(serialized)
            self.assertEqual(payload["reason_code"], "provider_call_failed")
            self.assertEqual(payload["error_type"], "RuntimeError")


if __name__ == "__main__":
    unittest.main()
