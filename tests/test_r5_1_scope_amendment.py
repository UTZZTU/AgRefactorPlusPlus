from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "r5_1_audit_scope_amendment",
    ROOT / "scripts" / "r5_1_audit_scope_amendment.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class R51ScopeAmendmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.protocol = MODULE.load_object(
            ROOT / "configs/r5_1/r5_1_pre_r6_scope_protocol_v2.json"
        )
        cls.r5_gate = MODULE.load_object(ROOT / "configs/r5_1/r5_acceptance_gate_v2.json")
        cls.r6_gate = MODULE.load_object(ROOT / "configs/r5_1/r6_entry_gate_v2.json")

    def test_frozen_contracts_are_consistent(self) -> None:
        self.assertEqual(
            MODULE.validate_contracts(self.protocol, self.r5_gate, self.r6_gate),
            [],
        )

    def test_o1_cannot_enter_prompt_gate_or_authorization(self) -> None:
        for consumer in (
            "product_prompt",
            "applicability_gate",
            "r4_mutation_authorization",
            "r5_mutation_authorization",
        ):
            with self.subTest(consumer=consumer):
                changed = copy.deepcopy(self.protocol)
                changed["p8_mandatory_minimum"]["forbidden_consumers"].remove(consumer)
                codes = {
                    item["code"]
                    for item in MODULE.validate_contracts(changed, self.r5_gate, self.r6_gate)
                }
                self.assertIn("observation_consumer_boundary_invalid", codes)

    def test_m_o_cannot_consume_current_budget_or_support_primary_claim(self) -> None:
        for key in ("m_o_current_budget_allowed", "m_o_primary_efficacy_claim_allowed"):
            with self.subTest(key=key):
                changed = copy.deepcopy(self.protocol)
                changed["p9_primary_matrix"][key] = True
                codes = {
                    item["code"]
                    for item in MODULE.validate_contracts(changed, self.r5_gate, self.r6_gate)
                }
                self.assertIn("m_o_authority_invalid", codes)

    def test_o2_o3_are_not_r5_or_r6_blockers(self) -> None:
        self.assertFalse(
            self.protocol["deferred_extensions"]["implementation_required_for_r5_acceptance"]
        )
        self.assertFalse(
            self.protocol["deferred_extensions"]["implementation_required_for_r6_entry"]
        )
        self.assertIn("O2_Ablation_supported", self.r5_gate["deferred_non_blockers"])
        self.assertIn("O3_Replicated", self.r6_gate["not_required_for_entry"])

    def test_only_three_product_entrypoints_and_default_off(self) -> None:
        self.assertEqual(
            self.protocol["product_boundaries"]["formal_entrypoints"],
            ["refactor", "optimize", "full"],
        )
        self.assertFalse(self.protocol["product_boundaries"]["r2_r5_default_enabled"])


if __name__ == "__main__":
    unittest.main()
