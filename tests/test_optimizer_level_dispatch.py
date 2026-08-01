import hashlib
import unittest

from agrefactor.optimization import (
    BudgetIncrement,
    CandidateExecutionRequest,
    CandidateRecord,
    CandidateStatus,
    FakeCandidateExecutor,
    FakeHypothesisProvider,
    HypothesisRequest,
    LevelDispatchCandidateExecutor,
    LevelDispatchHypothesisProvider,
    OptimizationLevel,
)


SOURCE = b"void top(int *a) { a[0] += 1; }\n"


def parent():
    return CandidateRecord(
        candidate_id="baseline",
        sequence=0,
        parent_candidate_id=None,
        hypothesis_id=None,
        level=None,
        source_sha256=hashlib.sha256(SOURCE).hexdigest(),
        source_artifact="candidates/baseline/source.cpp",
        status=CandidateStatus.ACCEPTED,
    )


def hrequest(level):
    evidence = ("ppa-baseline",) if level in {OptimizationLevel.BOTTLENECK, OptimizationLevel.PRAGMA} else ()
    return HypothesisRequest(
        run_id="dispatch",
        level=level,
        round_number=1,
        parent_candidate=parent(),
        max_hypotheses=1,
        supporting_evidence_ids=evidence,
        parent_source=SOURCE,
    )


class LevelDispatchProviderTests(unittest.TestCase):
    def test_routes_by_explicit_level(self):
        structural = FakeHypothesisProvider(name="structural")
        bottleneck = FakeHypothesisProvider(name="bottleneck")
        pragma = FakeHypothesisProvider(name="pragma")
        dispatch = LevelDispatchHypothesisProvider(
            {
                OptimizationLevel.STRUCTURAL: structural,
                OptimizationLevel.BOTTLENECK: bottleneck,
                OptimizationLevel.PRAGMA: pragma,
            }
        )
        result = dispatch.propose(hrequest(OptimizationLevel.PRAGMA))
        self.assertEqual(len(result), 1)
        self.assertEqual(structural.call_count, 0)
        self.assertEqual(bottleneck.call_count, 0)
        self.assertEqual(pragma.call_count, 1)
        self.assertEqual(dispatch.levels, (OptimizationLevel.BOTTLENECK, OptimizationLevel.PRAGMA, OptimizationLevel.STRUCTURAL))

    def test_missing_level_is_rejected(self):
        dispatch = LevelDispatchHypothesisProvider(
            {OptimizationLevel.STRUCTURAL: FakeHypothesisProvider()}
        )
        with self.assertRaises(ValueError):
            dispatch.propose(hrequest(OptimizationLevel.BOTTLENECK))

    def test_budget_increments_must_match(self):
        with self.assertRaises(ValueError):
            LevelDispatchHypothesisProvider(
                {
                    OptimizationLevel.STRUCTURAL: FakeHypothesisProvider(
                        budget_increment=BudgetIncrement(llm_calls=1)
                    ),
                    OptimizationLevel.BOTTLENECK: FakeHypothesisProvider(
                        budget_increment=BudgetIncrement(llm_calls=2)
                    ),
                }
            )

    def test_network_flag_is_aggregate(self):
        network = FakeHypothesisProvider()
        network._uses_network = True
        # Use a small protocol-compatible wrapper instead of source inspection.
        class NetworkProvider:
            name = "network"
            budget_increment = BudgetIncrement()
            uses_network = True
            def propose(self, request):
                return network.propose(request)
        dispatch = LevelDispatchHypothesisProvider(
            {
                OptimizationLevel.STRUCTURAL: FakeHypothesisProvider(),
                OptimizationLevel.BOTTLENECK: NetworkProvider(),
            }
        )
        self.assertTrue(dispatch.uses_network)


class LevelDispatchExecutorTests(unittest.TestCase):
    def request(self, level):
        hypothesis = FakeHypothesisProvider().propose(hrequest(level))[0]
        return CandidateExecutionRequest(
            run_id="dispatch",
            sequence=1,
            candidate_id="cand-1",
            level=level,
            round_number=1,
            parent_candidate=parent(),
            parent_source=SOURCE,
            hypothesis=hypothesis,
        )

    def test_routes_by_explicit_level(self):
        structural = FakeCandidateExecutor(name="structural")
        bottleneck = FakeCandidateExecutor(name="bottleneck")
        pragma = FakeCandidateExecutor(name="pragma")
        dispatch = LevelDispatchCandidateExecutor(
            {
                OptimizationLevel.STRUCTURAL: structural,
                OptimizationLevel.BOTTLENECK: bottleneck,
                OptimizationLevel.PRAGMA: pragma,
            }
        )
        dispatch.execute(self.request(OptimizationLevel.PRAGMA))
        self.assertEqual(structural.call_count, 0)
        self.assertEqual(bottleneck.call_count, 0)
        self.assertEqual(pragma.call_count, 1)
        self.assertEqual(dispatch.levels, (OptimizationLevel.BOTTLENECK, OptimizationLevel.PRAGMA, OptimizationLevel.STRUCTURAL))

    def test_missing_level_is_rejected(self):
        dispatch = LevelDispatchCandidateExecutor(
            {OptimizationLevel.STRUCTURAL: FakeCandidateExecutor()}
        )
        with self.assertRaises(ValueError):
            dispatch.execute(self.request(OptimizationLevel.BOTTLENECK))

    def test_budget_increments_must_match(self):
        with self.assertRaises(ValueError):
            LevelDispatchCandidateExecutor(
                {
                    OptimizationLevel.STRUCTURAL: FakeCandidateExecutor(
                        budget_increment=BudgetIncrement(llm_calls=1)
                    ),
                    OptimizationLevel.BOTTLENECK: FakeCandidateExecutor(
                        budget_increment=BudgetIncrement(llm_calls=2)
                    ),
                }
            )

    def test_flags_are_aggregate(self):
        class ExternalExecutor:
            name = "external"
            budget_increment = BudgetIncrement()
            uses_network = True
            uses_vitis = True
            def execute(self, request):
                return FakeCandidateExecutor().execute(request)
        dispatch = LevelDispatchCandidateExecutor(
            {
                OptimizationLevel.STRUCTURAL: FakeCandidateExecutor(),
                OptimizationLevel.BOTTLENECK: ExternalExecutor(),
            }
        )
        self.assertTrue(dispatch.uses_network)
        self.assertTrue(dispatch.uses_vitis)


if __name__ == "__main__":
    unittest.main()
