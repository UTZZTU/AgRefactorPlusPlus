from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from agrefactor.optimization import (
    CandidateRecord,
    CandidateStatus,
    OptimizationLevel,
    candidate_index_from_dict,
    candidate_index_to_dict,
)


def _load():
    path = Path(__file__).resolve().parents[1] / "tools" / "stage3_s37_real_product_smoke.py"
    spec = importlib.util.spec_from_file_location("stage3_s37_real_product_smoke", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load S3.7 real product smoke")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _calls(
    kinds: tuple[str, ...],
    *,
    invalid: dict[str, tuple[str, ...]] | None = None,
) -> list[dict[str, object]]:
    failures = dict(invalid or {})
    values: list[dict[str, object]] = []
    for sequence, kind in enumerate(kinds, 1):
        reasons = failures.get(kind)
        values.append(
            {
                "sequence": sequence,
                "call_kind": kind,
                "response_valid": reasons is None,
                "error_code": None if reasons is None else "CandidateResponseError",
                "error_reason_codes": [] if reasons is None else list(reasons),
            }
        )
    return values


def _decision(
    *,
    sequence: int,
    event: str,
    level: str,
    candidate_id: str | None = None,
    hypothesis_id: str | None = None,
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "sequence": sequence,
        "event": event,
        "level": level,
        "round_number": 1,
        "candidate_id": candidate_id,
        "hypothesis_id": hypothesis_id,
        "action": "fixture",
        "reason": "fixture",
        "metadata": dict(metadata or {}),
        "timestamp_utc": "2026-08-01T00:00:00Z",
    }


def _fixture_candidate(candidate_id: str) -> CandidateRecord:
    if candidate_id == "baseline":
        return CandidateRecord(
            candidate_id="baseline",
            sequence=0,
            parent_candidate_id=None,
            hypothesis_id=None,
            level=None,
            source_sha256="0" * 64,
            source_artifact="candidates/baseline/source.cpp",
            status=CandidateStatus.ACCEPTED,
        )
    sequence = int(candidate_id.removeprefix("cand-"))
    level = {
        1: OptimizationLevel.STRUCTURAL,
        2: OptimizationLevel.BOTTLENECK,
        3: OptimizationLevel.PRAGMA,
    }.get(sequence, OptimizationLevel.PRAGMA)
    return CandidateRecord(
        candidate_id=candidate_id,
        sequence=sequence,
        parent_candidate_id="baseline",
        hypothesis_id=f"hyp-{level.value}-r1-1",
        level=level,
        source_sha256=f"{sequence:064x}",
        source_artifact=f"candidates/{candidate_id}/source.cpp",
        status=CandidateStatus.ACCEPTED,
        correctness={"accepted": True},
        synthesis={"accepted": True},
        decision={"reason": "fixture"},
    )


def _write_optimizer_artifacts(
    root: Path,
    *,
    decisions: list[dict[str, object]],
    candidates: tuple[str, ...] = ("baseline",),
) -> None:
    optimizer = root / "optimize" / "optimizer"
    optimizer.mkdir(parents=True, exist_ok=True)
    (optimizer / "decisions.jsonl").write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in decisions),
        encoding="utf-8",
    )
    records = {item: _fixture_candidate(item) for item in candidates}
    (optimizer / "candidate_index.json").write_text(
        json.dumps(
            candidate_index_to_dict(records),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _pragma_artifacts(root: Path, *, with_hypothesis: bool, with_action: bool = True) -> None:
    if with_action:
        actions = root / "optimize" / "model" / "pragma_actions"
        actions.mkdir(parents=True, exist_ok=True)
        (actions / "pragma-parent-r1-1.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "action_id": "pragma-parent-r1-1",
                    "authoritative": False,
                    "action_source": "model_proposal",
                }
            ),
            encoding="utf-8",
        )
    if with_hypothesis:
        hypotheses = root / "optimize" / "optimizer" / "hypotheses"
        hypotheses.mkdir(parents=True, exist_ok=True)
        (hypotheses / "hyp-pragma-r1-1.json").write_text(
            json.dumps({"schema_version": 1, "hypothesis_id": "hyp-pragma-r1-1"}),
            encoding="utf-8",
        )


def _no_hypothesis(sequence: int, level: str) -> dict[str, object]:
    return _decision(
        sequence=sequence,
        event="round_no_executable_hypothesis",
        level=level,
    )


def _candidate_terminal(
    sequence: int, level: str, candidate_id: str
) -> dict[str, object]:
    return _decision(
        sequence=sequence,
        event="candidate_terminal",
        level=level,
        candidate_id=candidate_id,
        hypothesis_id=f"hyp-{level}-r1-1",
        metadata={"qualification_status": "accepted"},
    )


def _analysis_abstention(sequence: int, level: str) -> dict[str, object]:
    return _decision(
        sequence=sequence,
        event="hypothesis_generation_abstained",
        level=level,
        metadata={
            "error_code": "BottleneckModelContractError",
            "detail_codes": ["analysis_response_contract_invalid"],
            "automatic_retry": False,
            "hypothesis_created": False,
        },
    )


def _rewrite_abstention(
    sequence: int, level: str, reasons: tuple[str, ...]
) -> dict[str, object]:
    return _decision(
        sequence=sequence,
        event="candidate_generation_abstained",
        level=level,
        hypothesis_id=f"hyp-{level}-r1-1",
        metadata={
            "error_code": "CandidateResponseError",
            "detail_codes": list(reasons),
            "automatic_retry": False,
            "candidate_created": False,
            "qualification_started": False,
        },
    )


class ProductStage3SmokeToolTests(unittest.TestCase):
    def test_real_product_smoke_frozen_semantic_contract(self) -> None:
        module = _load()
        self.assertEqual(module.EXPECTED_BASELINE, "197327af79382327f2711119225d47e8ea060e00")
        self.assertEqual(module.MIN_SEMANTIC_REAL_LLM_CALLS, 3)
        self.assertEqual(module.MIN_EXPECTED_REAL_LLM_CALLS, 4)
        self.assertEqual(module.MAX_EXPECTED_REAL_LLM_CALLS, 6)
        self.assertEqual(module.MAX_SAFE_V1_LLM_CALLS, 14)
        self.assertEqual(module.OUTPUT_TOKEN_LIMIT, 32768)
        self.assertEqual(module.OUTPUT_TOKEN_SAFETY_CEILING, 65536)
        self.assertEqual(
            module.REQUIRED_ANALYSIS_CALL_KINDS,
            ("structural_hypothesis", "bottleneck_analysis", "pragma_analysis"),
        )
        self.assertEqual(
            module.CONDITIONAL_REWRITE_CALL_KINDS,
            ("structural_rewrite", "bottleneck_rewrite", "pragma_rewrite"),
        )

    def test_contract_accepts_three_valid_analyses_with_safe_abstention(self) -> None:
        module = _load()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_optimizer_artifacts(
                root,
                decisions=[
                    _no_hypothesis(1, "structural"),
                    _no_hypothesis(2, "bottleneck"),
                    _no_hypothesis(3, "pragma"),
                ],
            )
            _pragma_artifacts(root, with_hypothesis=False)
            result = module.verify_model_call_contract(
                calls=_calls(module.REQUIRED_ANALYSIS_CALL_KINDS),
                artifact_root=root,
            )
        self.assertEqual(result["qualified_rewrite_count"], 0)
        self.assertEqual(result["controlled_model_abstention_count"], 0)
        self.assertEqual(
            result["level_execution_branches"],
            {
                "structural": "analysis_safe_abstention",
                "bottleneck": "analysis_safe_abstention",
                "pragma": "analysis_safe_abstention",
            },
        )

    def test_contract_accepts_all_rewrites_linked_to_qualification(self) -> None:
        module = _load()
        kinds = (
            "structural_hypothesis",
            "structural_rewrite",
            "bottleneck_analysis",
            "bottleneck_rewrite",
            "pragma_analysis",
            "pragma_rewrite",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_optimizer_artifacts(
                root,
                decisions=[
                    _candidate_terminal(1, "structural", "cand-1"),
                    _candidate_terminal(2, "bottleneck", "cand-2"),
                    _candidate_terminal(3, "pragma", "cand-3"),
                ],
                candidates=("baseline", "cand-1", "cand-2", "cand-3"),
            )
            _pragma_artifacts(root, with_hypothesis=True)
            result = module.verify_model_call_contract(
                calls=_calls(kinds), artifact_root=root
            )
        self.assertEqual(result["model_response_valid_count"], 6)
        self.assertEqual(result["qualified_rewrite_count"], 3)
        self.assertEqual(result["controlled_model_abstention_count"], 0)
        self.assertEqual(result["pragma_execution_branch"], "rewrite_qualified")

    def test_contract_accepts_linked_bottleneck_rewrite_abstention(self) -> None:
        module = _load()
        reasons = ("commentary_outside_code",)
        kinds = (
            "structural_hypothesis",
            "structural_rewrite",
            "bottleneck_analysis",
            "bottleneck_rewrite",
            "pragma_analysis",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_optimizer_artifacts(
                root,
                decisions=[
                    _candidate_terminal(1, "structural", "cand-1"),
                    _rewrite_abstention(2, "bottleneck", reasons),
                    _no_hypothesis(3, "pragma"),
                ],
                candidates=("baseline", "cand-1"),
            )
            _pragma_artifacts(root, with_hypothesis=False)
            result = module.verify_model_call_contract(
                calls=_calls(kinds, invalid={"bottleneck_rewrite": reasons}),
                artifact_root=root,
            )
        self.assertEqual(result["qualified_rewrite_count"], 1)
        self.assertEqual(result["rewrite_abstention_count"], 1)
        self.assertEqual(
            result["level_execution_branches"]["bottleneck"],
            "rewrite_contract_abstention",
        )

    def test_contract_accepts_linked_analysis_contract_abstention(self) -> None:
        module = _load()
        kinds = (
            "structural_hypothesis",
            "bottleneck_analysis",
            "bottleneck_rewrite",
            "pragma_analysis",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_optimizer_artifacts(
                root,
                decisions=[
                    _analysis_abstention(1, "structural"),
                    _candidate_terminal(2, "bottleneck", "cand-1"),
                    _no_hypothesis(3, "pragma"),
                ],
                candidates=("baseline", "cand-1"),
            )
            _pragma_artifacts(root, with_hypothesis=False)
            calls = _calls(kinds)
            calls[0].update(
                {
                    "response_valid": False,
                    "error_code": "BottleneckModelContractError",
                    "error_reason_codes": ["analysis_response_contract_invalid"],
                }
            )
            result = module.verify_model_call_contract(calls=calls, artifact_root=root)
        self.assertEqual(result["analysis_abstention_count"], 1)
        self.assertEqual(
            result["level_execution_branches"]["structural"],
            "analysis_contract_abstention",
        )

    def test_contract_rejects_invalid_rewrite_without_typed_decision(self) -> None:
        module = _load()
        reasons = ("semantic_unchanged",)
        kinds = (
            "structural_hypothesis",
            "structural_rewrite",
            "bottleneck_analysis",
            "pragma_analysis",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_optimizer_artifacts(
                root,
                decisions=[
                    _no_hypothesis(1, "bottleneck"),
                    _no_hypothesis(2, "pragma"),
                ],
            )
            _pragma_artifacts(root, with_hypothesis=False)
            with self.assertRaisesRegex(RuntimeError, "candidate_generation_abstained"):
                module.verify_model_call_contract(
                    calls=_calls(kinds, invalid={"structural_rewrite": reasons}),
                    artifact_root=root,
                )

    def test_contract_rejects_valid_rewrite_without_qualification_link(self) -> None:
        module = _load()
        kinds = (
            "structural_hypothesis",
            "structural_rewrite",
            "bottleneck_analysis",
            "pragma_analysis",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_optimizer_artifacts(
                root,
                decisions=[
                    _no_hypothesis(1, "bottleneck"),
                    _no_hypothesis(2, "pragma"),
                ],
            )
            _pragma_artifacts(root, with_hypothesis=False)
            with self.assertRaisesRegex(RuntimeError, "candidate_terminal"):
                module.verify_model_call_contract(
                    calls=_calls(kinds), artifact_root=root
                )

    def test_contract_rejects_invalid_analysis_without_safe_reason_codes(self) -> None:
        module = _load()
        kinds = module.REQUIRED_ANALYSIS_CALL_KINDS
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_optimizer_artifacts(
                root,
                decisions=[
                    _analysis_abstention(1, "structural"),
                    _no_hypothesis(2, "bottleneck"),
                    _no_hypothesis(3, "pragma"),
                ],
            )
            _pragma_artifacts(root, with_hypothesis=False)
            calls = _calls(kinds)
            calls[0].update(
                {
                    "response_valid": False,
                    "error_code": "StructuralModelContractError",
                    "error_reason_codes": [],
                }
            )
            with self.assertRaisesRegex(RuntimeError, "safe reason codes"):
                module.verify_model_call_contract(calls=calls, artifact_root=root)

    def test_contract_rejects_missing_pragma_analysis(self) -> None:
        module = _load()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_optimizer_artifacts(
                root,
                decisions=[
                    _no_hypothesis(1, "structural"),
                    _no_hypothesis(2, "bottleneck"),
                ],
            )
            with self.assertRaisesRegex(RuntimeError, "pragma analysis"):
                module.verify_model_call_contract(
                    calls=_calls(
                        (
                            "structural_hypothesis",
                            "bottleneck_analysis",
                            "structural_rewrite",
                        )
                    ),
                    artifact_root=root,
                )

    def test_real_product_smoke_is_single_kernel_not_s38(self) -> None:
        module = _load()
        text = Path(module.__file__).read_text(encoding="utf-8")
        self.assertIn("single_kernel_product_adapter_entry_gate", text)
        self.assertIn('"multi_kernel_claimed": False', text)
        self.assertIn("STAGE2_SMOKE_CASES", text)

    def test_real_product_smoke_uses_normal_phase_with_acceptance_round_cap(self) -> None:
        module = _load()
        text = Path(module.__file__).read_text(encoding="utf-8")
        self.assertIn("Stage3ProductOptimizationPhase", text)
        self.assertIn("acceptance_one_physical_round_per_level=True", text)
        self.assertIn('"normal_product_policy_unchanged": "safe-v1-2-2-3"', text)

    def test_fixture_writes_frozen_candidate_index_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_optimizer_artifacts(
                root,
                decisions=[],
                candidates=("baseline", "cand-1", "cand-2"),
            )
            payload = json.loads(
                (root / "optimize" / "optimizer" / "candidate_index.json").read_text(
                    encoding="utf-8"
                )
            )
        self.assertEqual(set(payload), {"schema_version", "candidates"})
        parsed = candidate_index_from_dict(payload)
        self.assertEqual(set(parsed), {"baseline", "cand-1", "cand-2"})

    def test_contract_rejects_obsolete_flat_candidate_index_shape(self) -> None:
        module = _load()
        kinds = (
            "structural_hypothesis",
            "structural_rewrite",
            "bottleneck_analysis",
            "pragma_analysis",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_optimizer_artifacts(
                root,
                decisions=[
                    _candidate_terminal(1, "structural", "cand-1"),
                    _no_hypothesis(2, "bottleneck"),
                    _no_hypothesis(3, "pragma"),
                ],
                candidates=("baseline", "cand-1"),
            )
            index_path = root / "optimize" / "optimizer" / "candidate_index.json"
            index_path.write_text(
                json.dumps({"baseline": {}, "cand-1": {}}) + "\n",
                encoding="utf-8",
            )
            _pragma_artifacts(root, with_hypothesis=False)
            with self.assertRaises((TypeError, ValueError)):
                module.verify_model_call_contract(
                    calls=_calls(kinds),
                    artifact_root=root,
                )


if __name__ == "__main__":
    unittest.main()
