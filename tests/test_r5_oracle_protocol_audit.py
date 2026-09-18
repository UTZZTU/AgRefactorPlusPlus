from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


FREEZE = _module("r5_freeze_oracle_adapters", "scripts/r5_freeze_oracle_adapters.py")
AUDIT = _module("r5_oracle_protocol_audit", "scripts/r5_oracle_protocol_audit.py")


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _state() -> dict:
    return {
        "R5_ACCEPTED": False,
        "R6_STARTED": False,
        "R5_PROVIDER_CALL_HARD_CAP": 500,
        "R5_VITIS_LAUNCH_HARD_CAP": 500,
        "R5_CONSUMED_PROVIDER_CALLS": 0,
        "R5_CONSUMED_VITIS_LAUNCHES": 0,
        "R5_REAL_CAMPAIGN_ALLOWED": False,
    }


def _certificate(*, failure_classes=("family",)) -> dict:
    value = {
        "schema_version": 2,
        "split_id": "test-split",
        "split_sha256": "a" * 64,
        "report_sha256": "b" * 64,
        "policy_sha256": "c" * 64,
        "provider_identity_sha256": "d" * 64,
        "prompt_contract_version": "r2-shadow-output-v4",
        "strict_parser": "r2-v1",
        "input_contract_version": "r2-agent-safe-diagnostic-evidence-v2",
        "eligible_confidence_labels": ["high"],
        "accepted": True,
        "reasons": [],
        "eligible_failure_classes": list(failure_classes),
    }
    value["certificate_id"] = "r2-calibration-" + AUDIT._sha(value)[:32]
    return value


def _fixture(root: Path, *, future_control: bool = True):
    repo = root / "repo"
    repo.mkdir()
    cases = []
    definitions = (
        ("history", "positive"),
        (
            "history",
            "positive" if future_control else "inapplicable_or_confusable",
        ),
        ("future", "positive"),
        (
            "future",
            "inapplicable_or_confusable" if future_control else "positive",
        ),
    )
    for index, (period, control) in enumerate(definitions, start=1):
        case = repo / f"case{index}"
        case.mkdir()
        for name in ("kernel.cpp", "candidate.cpp", "legacy_tb.cpp"):
            (case / name).write_text(
                f"int fixture_{index}_{name.replace('.', '_')};\n",
                encoding="utf-8",
            )
        public = (
            "int top(int); int top_hls(int); "
            f"int main(){{if(top({index})!=top_hls({index}))"
            "{return 1;}return 0;}\n"
        )
        hidden = (
            "int top(int); int top_hls(int); "
            f"int main(){{if(top({index + 10})!=top_hls({index + 10}))"
            "{return 1;}return 0;}\n"
        )
        (case / "public.cpp").write_text(public, encoding="utf-8")
        (case / "hidden.cpp").write_text(hidden, encoding="utf-8")
        cases.append(
            {
                "case_id": f"case-{index}",
                "period": period,
                "control_role": control,
                "source": f"case{index}/kernel.cpp",
                "legacy_candidate": f"case{index}/candidate.cpp",
                "legacy_testbench": f"case{index}/legacy_tb.cpp",
                "top": "top",
                "public_test": f"case{index}/public.cpp",
                "hidden_test": f"case{index}/hidden.cpp",
                "prior_evidence": "pre-R5 fixture",
                "expected_r2_failure_class": "family",
                "expected_r2_entry_boundary": (
                    "unknown_or_mixed_review"
                    if control == "positive"
                    else "inapplicable_or_confusable"
                ),
                "deterministic_repair_expected": False,
            }
        )
    plan = {
        "schema_version": 1,
        "plan_id": "test-plan",
        "frozen_at": "2026-09-19T00:00:00Z",
        "status": "predeclared_before_outcome_observation",
        "primary_failure_family": "family",
        "candidate_top_suffix": "_hls",
        "cases": cases,
        "invariants": {
            "expected_outputs_invented": False,
            "reference_top_is_oracle": True,
            "public_hidden_inputs_distinct": True,
            "original_benchmark_files_mutated": False,
            "history_future_roles_predeclared": True,
            "outcomes_observed_at_freeze": False,
            "hidden_model_visible": False,
            "trusted_revision_creation_allowed": False,
            "real_campaign_allowed": False,
        },
    }
    plan_path = repo / "plan.json"
    plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "test"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "freeze"], check=True)
    manifest = FREEZE.build_manifest(repo, plan)
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    return repo, plan, manifest, manifest_bytes


class R5OracleProtocolAuditTests(unittest.TestCase):
    def test_audit_freezes_future_only_bounds_and_keeps_campaign_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo, plan, manifest, manifest_bytes = _fixture(Path(raw))
            (repo / "later.txt").write_text("later\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "later.txt"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "later"], check=True)
            result = AUDIT.build_audit(
                repo,
                manifest,
                plan,
                _state(),
                _certificate(),
                manifest_file_sha256=_sha_bytes(manifest_bytes),
                plan_relative_path="plan.json",
            )
        self.assertEqual(
            result["protocol_audit"]["status"],
            "ready_for_history_acquisition",
        )
        self.assertFalse(result["protocol_audit"]["real_campaign_allowed"])
        self.assertEqual(result["budget_plan"]["pilot_provider_upper_bound"], 60)
        self.assertEqual(result["budget_plan"]["pilot_vitis_upper_bound"], 72)
        self.assertEqual(result["budget_plan"]["formal_provider_upper_bound"], 120)
        self.assertEqual(result["budget_plan"]["formal_vitis_upper_bound"], 144)

    def test_manifest_signature_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo, plan, manifest, manifest_bytes = _fixture(Path(raw))
            manifest["failure_family"] = "tampered"
            with self.assertRaisesRegex(ValueError, "manifest_sha256 mismatch"):
                AUDIT.build_audit(
                    repo,
                    manifest,
                    plan,
                    _state(),
                    _certificate(),
                    manifest_file_sha256=_sha_bytes(manifest_bytes),
                    plan_relative_path="plan.json",
                )

    def test_control_in_history_instead_of_future_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo, plan, manifest, manifest_bytes = _fixture(
                Path(raw), future_control=False
            )
            with self.assertRaisesRegex(ValueError, "history acquisition requires"):
                AUDIT.build_audit(
                    repo,
                    manifest,
                    plan,
                    _state(),
                    _certificate(),
                    manifest_file_sha256=_sha_bytes(manifest_bytes),
                    plan_relative_path="plan.json",
                )

    def test_nonzero_reconciled_r5_usage_is_carried_into_budget_plan(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo, plan, manifest, manifest_bytes = _fixture(Path(raw))
            state = _state()
            state["R5_CONSUMED_PROVIDER_CALLS"] = 12
            result = AUDIT.build_audit(
                repo,
                manifest,
                plan,
                state,
                _certificate(),
                manifest_file_sha256=_sha_bytes(manifest_bytes),
                plan_relative_path="plan.json",
            )
        self.assertEqual(result["budget_plan"]["ledger"]["provider_used"], 12)

    def test_failure_class_outside_certificate_scope_blocks_history(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo, plan, manifest, manifest_bytes = _fixture(Path(raw))
            result = AUDIT.build_audit(
                repo,
                manifest,
                plan,
                _state(),
                _certificate(failure_classes=("unsupported_construct",)),
                manifest_file_sha256=_sha_bytes(manifest_bytes),
                plan_relative_path="plan.json",
            )
        self.assertEqual(
            result["protocol_audit"]["status"],
            "blocked_history_acquisition",
        )
        self.assertEqual(
            {item["code"] for item in result["protocol_audit"]["admission_issues"]},
            {"failure_class_outside_calibration_scope"},
        )


if __name__ == "__main__":
    unittest.main()
