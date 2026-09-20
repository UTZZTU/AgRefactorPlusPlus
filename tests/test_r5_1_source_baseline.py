from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "r5_1_source_baseline",
    ROOT / "scripts" / "r5_1_source_baseline.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class R51SourceBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = MODULE.load_object(
            ROOT / "configs" / "r5_1" / "source_baseline_protocol.json"
        )

    def test_frozen_protocol_has_audited_coverage_and_budget(self) -> None:
        MODULE.validate_plan(self.plan)
        cases = self.plan["cases"]
        self.assertEqual(len(cases), 5)
        self.assertEqual(len({case["source_id"] for case in cases}), 4)
        self.assertEqual(len({case["algorithm_family"] for case in cases}), 4)
        self.assertEqual(
            self.plan["budget"]["campaign_reserve"],
            {"provider_calls": 0, "vitis_launches": 15},
        )
        self.assertEqual(self.plan["invariants"]["provider_calls"], 0)
        self.assertFalse(self.plan["invariants"]["hidden_inputs_used"])
        self.assertEqual(
            self.plan["invariants"]["validation_budget_per_case"],
            {
                "tool_calls": 11,
                "compile_calls": 5,
                "csim_calls": 1,
                "csynth_calls": 1,
                "cosim_calls": 1,
            },
        )

    def test_validation_budget_cannot_underfund_staged_preflight(self) -> None:
        for field in ("tool_calls", "compile_calls"):
            with self.subTest(field=field):
                plan = copy.deepcopy(self.plan)
                plan["invariants"]["validation_budget_per_case"][field] -= 1
                with self.assertRaisesRegex(ValueError, "validation budget"):
                    MODULE.validate_plan(plan)

    def test_semantic_family_cannot_cross_history_future(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["cases"][3]["partition"] = "history"
        with self.assertRaisesRegex(ValueError, "semantic family crosses"):
            MODULE.validate_plan(plan)

    def test_source_path_escape_is_rejected(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["cases"][0]["design"]["parts"][0]["path"] = "../secret.cpp"
        with self.assertRaisesRegex(ValueError, "path escapes"):
            MODULE.validate_plan(plan)

    def test_repository_material_hash_mismatch_is_rejected(self) -> None:
        case = copy.deepcopy(self.plan["cases"][-1])
        case["design"]["parts"][0]["sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as external:
            with self.assertRaisesRegex(ValueError, "hash_mismatch"):
                MODULE.materialize_case(ROOT, Path(external), case)

    def test_terminal_classification_is_owner_and_execution_based(self) -> None:
        rows = (
            (True, "accepted", set(), True, "raw_pass_all"),
            (False, "preflight", {"candidate"}, True, "oracle_or_adapter_invalid"),
            (False, "csynth", {"toolchain"}, True, "infrastructure_failure"),
            (False, "public_evaluation", {"testbench"}, True, "oracle_or_adapter_invalid"),
            (False, "csynth", {"candidate"}, True, "raw_fail_actionable"),
            (False, "csynth", {"candidate"}, False, "raw_fail_ambiguous"),
            (False, "csynth", set(), True, "raw_fail_ambiguous"),
        )
        for accepted, state, owners, physical, expected in rows:
            with self.subTest(expected=expected, state=state, owners=owners):
                self.assertEqual(
                    MODULE.classify_terminal(
                        accepted=accepted,
                        terminal_state=state,
                        owners=owners,
                        physical_execution=physical,
                    ),
                    expected,
                )

    def test_stage_status_uses_blocking_authority(self) -> None:
        outcome = SimpleNamespace(
            result=SimpleNamespace(
                steps=(
                    SimpleNamespace(
                        state=SimpleNamespace(value="public_evaluation"),
                        source_blocking=False,
                    ),
                    SimpleNamespace(
                        state=SimpleNamespace(value="csynth"),
                        source_blocking=True,
                    ),
                )
            )
        )
        self.assertEqual(
            MODULE._stage_statuses(outcome),
            {
                "S0_host_oracle": "passed",
                "S1_public_csim": "passed",
                "S2_csynth": "failed",
                "S3_public_cosim": "not_run",
            },
        )


if __name__ == "__main__":
    unittest.main()
