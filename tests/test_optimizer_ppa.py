import json
from pathlib import Path
import shutil
import tempfile
import unittest

from agrefactor.optimization import (
    LatencyPpaComparator,
    PpaComparisonDecision,
    PpaEvidence,
    PpaParseError,
    PpaReportFormat,
    PpaResourceUsage,
    VitisHlsPpaReportAdapter,
)


FIXTURES = Path(__file__).parent / "fixtures" / "optimizer"
CONTEXT = "a" * 64


def make_work(fixture_name, *, suffix=".xml"):
    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name)
    report = root / "csynth" / "solution" / "syn" / "report"
    report.mkdir(parents=True)
    shutil.copyfile(
        FIXTURES / fixture_name,
        report / f"top_csynth{suffix}",
    )
    return temporary, root


def evidence(**overrides):
    values = {
        "evidence_id": "evidence-1",
        "parser_profile": "vitis-hls-2023.2",
        "report_format": PpaReportFormat.XML,
        "report_relative_path": "csynth/solution/syn/report/top_csynth.xml",
        "report_sha256": "b" * 64,
        "comparison_context_identity_sha256": CONTEXT,
        "latency_cycles_min": 90,
        "latency_cycles_max": 100,
        "initiation_interval_min": 1,
        "initiation_interval_max": 2,
        "target_clock_period_ns": 5.0,
        "achieved_clock_period_ns": 4.0,
        "resources_used": PpaResourceUsage(lut=100, ff=200),
        "resources_available": PpaResourceUsage(lut=1000, ff=2000),
        "max_resource_utilization_ratio": 0.1,
        "objective_feasible": True,
    }
    values.update(overrides)
    return PpaEvidence(**values)


class PpaReportAdapterTests(unittest.TestCase):
    def test_parses_xml_fixture(self):
        temporary, root = make_work("vitis_hls_2023_2_csynth.xml")
        self.addCleanup(temporary.cleanup)
        result = VitisHlsPpaReportAdapter().parse(
            root,
            top_function="top",
            parser_profile="vitis-hls-2023.2",
            comparison_context_identity_sha256=CONTEXT,
        )
        self.assertEqual(result.latency_cycles_min, 96)
        self.assertEqual(result.latency_cycles_max, 112)
        self.assertEqual(result.initiation_interval_max, 2)
        self.assertEqual(result.achieved_clock_period_ns, 4.25)
        self.assertEqual(result.resources_used.lut, 900)
        self.assertAlmostEqual(result.max_resource_utilization_ratio, 0.04)
        self.assertTrue(result.objective_feasible)

    def test_parses_text_fallback(self):
        temporary, root = make_work(
            "vitis_hls_text_fallback_csynth.rpt",
            suffix=".rpt",
        )
        self.addCleanup(temporary.cleanup)
        result = VitisHlsPpaReportAdapter().parse(
            root,
            top_function="top",
            parser_profile="vitis-hls-generic",
            comparison_context_identity_sha256=CONTEXT,
        )
        self.assertEqual(result.report_format, PpaReportFormat.TEXT)
        self.assertEqual(result.latency_cycles_max, 112)
        self.assertIn("text_report_fallback_used", result.parser_warnings)

    def test_missing_primary_latency_rejected(self):
        temporary, root = make_work(
            "vitis_hls_missing_latency_csynth.xml"
        )
        self.addCleanup(temporary.cleanup)
        with self.assertRaises(PpaParseError):
            VitisHlsPpaReportAdapter().parse(
                root,
                top_function="top",
                parser_profile="vitis-hls-2023.2",
                comparison_context_identity_sha256=CONTEXT,
            )

    def test_missing_report_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                VitisHlsPpaReportAdapter().parse(
                    directory,
                    top_function="top",
                    parser_profile="vitis-hls-2023.2",
                    comparison_context_identity_sha256=CONTEXT,
                )

    def test_report_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "real.xml"
            source.write_text("<profile/>", encoding="utf-8")
            report = root / "csynth/solution/syn/report"
            report.mkdir(parents=True)
            (report / "top_csynth.xml").symlink_to(source)
            with self.assertRaises(PpaParseError):
                VitisHlsPpaReportAdapter().parse(
                    root,
                    top_function="top",
                    parser_profile="vitis-hls-2023.2",
                    comparison_context_identity_sha256=CONTEXT,
                )

    def test_explicit_resource_limit_violation(self):
        temporary, root = make_work("vitis_hls_2023_2_csynth.xml")
        self.addCleanup(temporary.cleanup)
        result = VitisHlsPpaReportAdapter().parse(
            root,
            top_function="top",
            parser_profile="vitis-hls-2023.2",
            comparison_context_identity_sha256=CONTEXT,
            resource_limits={"max_lut": 800},
        )
        self.assertFalse(result.objective_feasible)
        self.assertEqual(
            result.constraint_violations,
            ("lut_used_900_exceeds_limit_800",),
        )

    def test_null_limits_do_not_create_pseudo_limits(self):
        temporary, root = make_work("vitis_hls_2023_2_csynth.xml")
        self.addCleanup(temporary.cleanup)
        result = VitisHlsPpaReportAdapter().parse(
            root,
            top_function="top",
            parser_profile="vitis-hls-2023.2",
            comparison_context_identity_sha256=CONTEXT,
            resource_limits={"max_lut": None},
        )
        self.assertTrue(result.objective_feasible)

    def test_missing_required_resource_is_unknown(self):
        temporary, root = make_work(
            "vitis_hls_missing_latency_csynth.xml"
        )
        self.addCleanup(temporary.cleanup)
        report = (
            root / "csynth/solution/syn/report/top_csynth.xml"
        )
        report.write_text(
            """<profile><PerformanceEstimates><SummaryOfOverallLatency>"
            "<Worst-caseLatency>10</Worst-caseLatency>"
            "</SummaryOfOverallLatency></PerformanceEstimates>"
            "<AreaEstimates><Resources><LUT>5</LUT></Resources>"
            "</AreaEstimates></profile>""",
            encoding="utf-8",
        )
        result = VitisHlsPpaReportAdapter().parse(
            root,
            top_function="top",
            parser_profile="vitis-hls-2023.2",
            comparison_context_identity_sha256=CONTEXT,
            resource_limits={"max_dsp": 10},
        )
        self.assertIsNone(result.objective_feasible)
        self.assertIn("resource_usage_missing:dsp", result.parser_warnings)

    def test_round_trip(self):
        original = evidence()
        restored = PpaEvidence.from_dict(original.to_dict())
        self.assertEqual(restored, original)

    def test_unknown_field_rejected(self):
        payload = evidence().to_dict()
        payload["unexpected"] = True
        with self.assertRaises(ValueError):
            PpaEvidence.from_dict(payload)

    def test_nonfinite_ratio_rejected(self):
        with self.assertRaises(ValueError):
            evidence(max_resource_utilization_ratio=float("nan"))

    def test_inconsistent_feasibility_rejected(self):
        with self.assertRaises(ValueError):
            evidence(
                objective_feasible=False,
                constraint_violations=(),
            )

    def test_deterministic_json_payload(self):
        first = json.dumps(evidence().to_dict(), sort_keys=True)
        second = json.dumps(evidence().to_dict(), sort_keys=True)
        self.assertEqual(first, second)


class LatencyComparatorTests(unittest.TestCase):
    def setUp(self):
        self.comparator = LatencyPpaComparator()

    def test_lower_latency_wins(self):
        result = self.comparator.compare(
            evidence(latency_cycles_max=90),
            evidence(latency_cycles_max=100),
            candidate_sequence=1,
            incumbent_sequence=0,
        )
        self.assertEqual(result.decision, PpaComparisonDecision.BETTER)
        self.assertEqual(result.decisive_metric, "latency_cycles_max")

    def test_higher_latency_loses(self):
        result = self.comparator.compare(
            evidence(latency_cycles_max=101),
            evidence(latency_cycles_max=100),
            candidate_sequence=1,
            incumbent_sequence=0,
        )
        self.assertFalse(result.better)

    def test_lower_ii_breaks_latency_tie(self):
        result = self.comparator.compare(
            evidence(initiation_interval_max=1),
            evidence(initiation_interval_max=2),
            candidate_sequence=1,
            incumbent_sequence=0,
        )
        self.assertEqual(result.decisive_metric, "initiation_interval_max")
        self.assertTrue(result.better)

    def test_missing_ii_skips_tiebreaker(self):
        result = self.comparator.compare(
            evidence(
                initiation_interval_max=None,
                max_resource_utilization_ratio=0.05,
            ),
            evidence(max_resource_utilization_ratio=0.10),
            candidate_sequence=1,
            incumbent_sequence=0,
        )
        self.assertEqual(
            result.decisive_metric,
            "max_resource_utilization_ratio",
        )

    def test_resource_ratio_breaks_tie(self):
        result = self.comparator.compare(
            evidence(max_resource_utilization_ratio=0.05),
            evidence(max_resource_utilization_ratio=0.10),
            candidate_sequence=1,
            incumbent_sequence=0,
        )
        self.assertTrue(result.better)

    def test_clock_breaks_tie(self):
        result = self.comparator.compare(
            evidence(
                max_resource_utilization_ratio=None,
                achieved_clock_period_ns=3.9,
            ),
            evidence(
                max_resource_utilization_ratio=None,
                achieved_clock_period_ns=4.0,
            ),
            candidate_sequence=1,
            incumbent_sequence=0,
        )
        self.assertEqual(result.decisive_metric, "achieved_clock_period_ns")

    def test_lower_sequence_is_final_tiebreaker(self):
        result = self.comparator.compare(
            evidence(),
            evidence(),
            candidate_sequence=1,
            incumbent_sequence=2,
        )
        self.assertTrue(result.better)
        self.assertEqual(result.decisive_metric, "candidate_sequence")

    def test_exact_tie_keeps_incumbent(self):
        result = self.comparator.compare(
            evidence(),
            evidence(),
            candidate_sequence=1,
            incumbent_sequence=1,
        )
        self.assertFalse(result.better)

    def test_context_mismatch_is_incomparable(self):
        result = self.comparator.compare(
            evidence(comparison_context_identity_sha256="c" * 64),
            evidence(),
            candidate_sequence=1,
            incumbent_sequence=0,
        )
        self.assertEqual(
            result.decision,
            PpaComparisonDecision.INCOMPARABLE,
        )

    def test_infeasible_candidate_is_incomparable(self):
        result = self.comparator.compare(
            evidence(
                objective_feasible=False,
                constraint_violations=("lut_exceeded",),
            ),
            evidence(),
            candidate_sequence=1,
            incumbent_sequence=0,
        )
        self.assertIsNone(result.better)

    def test_unknown_feasibility_is_incomparable(self):
        result = self.comparator.compare(
            evidence(objective_feasible=None),
            evidence(),
            candidate_sequence=1,
            incumbent_sequence=0,
        )
        self.assertIsNone(result.better)


if __name__ == "__main__":
    unittest.main()
