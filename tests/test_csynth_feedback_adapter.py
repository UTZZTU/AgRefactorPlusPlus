import copy
import unittest

from agrefactor.evaluation import CsynthFeedbackAdapter
from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackOwner,
    FeedbackSeverity,
    FeedbackStage,
)


def base_invocation() -> dict:
    return {
        "schema_version": 1,
        "phase": "csynth",
        "work_dir": "/tmp/run",
        "top_kernel": "top_hls",
        "requested_toolchain_version": "2023.2",
        "target_profile": {
            "name": "default",
            "device": "xcu200-fsgd2104-2-e",
        },
        "toolchain_version_verification": {
            "status": "matched",
            "requested": "2023.2",
            "actual": "2023.2",
            "returncode": 0,
            "stdout": "vitis-run v2023.2",
            "stderr": "",
        },
        "budget": {
            "status": "consumed",
            "checkpoint": "before_csynth_launch",
        },
        "execution": {
            "status": "completed",
            "returncode": 0,
            "timeout": False,
        },
    }


class CsynthFeedbackAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = CsynthFeedbackAdapter()

    def test_success_creates_empty_report(self) -> None:
        report = self.adapter.to_operator_report(
            invocation=base_invocation(),
            report_id="success",
            legacy_status="succeeded",
            evidence_ref="run/csynth_invocation.json",
        )

        self.assertEqual(report.items, ())
        self.assertFalse(report.blocking)
        self.assertIsNone(report.highest_severity)
        self.assertEqual(
            report.metadata["evidence_view"],
            "operator_full",
        )

    def test_csynth_failure_is_not_overclassified(self) -> None:
        invocation = base_invocation()
        invocation["execution"]["returncode"] = 1

        item = self.adapter.to_operator_report(
            invocation=invocation,
            report_id="failed",
            legacy_status="csynth_failed",
            error_msg="Vitis synthesis failed",
        ).items[0]

        self.assertEqual(item.stage, FeedbackStage.CSYNTH)
        self.assertEqual(item.category, FeedbackCategory.UNKNOWN)
        self.assertEqual(item.severity, FeedbackSeverity.ERROR)
        self.assertEqual(item.owner, FeedbackOwner.UNKNOWN)
        self.assertEqual(item.detail, "Vitis synthesis failed")

    def test_timeout_maps_to_fatal_timeout(self) -> None:
        invocation = base_invocation()
        invocation["execution"] = {
            "status": "completed",
            "returncode": None,
            "timeout": True,
        }

        item = self.adapter.to_operator_report(
            invocation=invocation,
            report_id="timeout",
            legacy_status="timeout",
        ).items[0]

        self.assertEqual(item.stage, FeedbackStage.CSYNTH)
        self.assertEqual(item.category, FeedbackCategory.TIMEOUT)
        self.assertEqual(item.severity, FeedbackSeverity.FATAL)
        self.assertEqual(item.owner, FeedbackOwner.UNKNOWN)

    def test_budget_block_maps_to_evaluator_owned_budget(self) -> None:
        invocation = base_invocation()
        invocation["budget"] = {
            "status": "blocked",
            "checkpoint": "before_version_probe",
            "resource": "csynth_calls",
            "limit": 0,
            "attempted": 1,
        }
        invocation["execution"] = {
            "status": "blocked_by_budget",
            "returncode": None,
            "timeout": False,
        }

        report = self.adapter.to_operator_report(
            invocation=invocation,
            report_id="budget",
        )
        item = report.items[0]

        self.assertEqual(
            item.stage,
            FeedbackStage.CONFIGURATION,
        )
        self.assertEqual(
            item.category,
            FeedbackCategory.BUDGET_EXHAUSTED,
        )
        self.assertEqual(item.owner, FeedbackOwner.EVALUATOR)
        self.assertEqual(item.severity, FeedbackSeverity.ERROR)
        self.assertEqual(
            item.metadata["budget_resource"],
            "csynth_calls",
        )

    def test_version_mismatch_maps_to_configuration(self) -> None:
        invocation = base_invocation()
        invocation["toolchain_version_verification"] = {
            "status": "mismatch",
            "requested": "2023.2",
            "actual": "2024.1",
            "returncode": 0,
            "stdout": "vitis-run v2024.1",
            "stderr": "",
        }
        invocation["execution"] = {
            "status": "blocked_before_csynth",
            "returncode": None,
            "timeout": False,
        }

        item = self.adapter.to_operator_report(
            invocation=invocation,
            report_id="version",
        ).items[0]

        self.assertEqual(item.stage, FeedbackStage.TOOLCHAIN)
        self.assertEqual(
            item.category,
            FeedbackCategory.INVALID_CONFIGURATION,
        )
        self.assertEqual(
            item.owner,
            FeedbackOwner.CONFIGURATION,
        )
        self.assertEqual(item.severity, FeedbackSeverity.FATAL)
        self.assertEqual(
            item.metadata["toolchain_actual_version"],
            "2024.1",
        )

    def test_probe_timeout_maps_to_toolchain_timeout(self) -> None:
        invocation = base_invocation()
        invocation["toolchain_version_verification"] = {
            "status": "probe_timeout",
            "requested": "2023.2",
            "actual": None,
            "stderr": "probe timed out",
        }
        invocation["execution"] = {
            "status": "blocked_before_csynth",
            "returncode": None,
            "timeout": False,
        }

        item = self.adapter.to_operator_report(
            invocation=invocation,
            report_id="probe-timeout",
        ).items[0]

        self.assertEqual(item.stage, FeedbackStage.TOOLCHAIN)
        self.assertEqual(item.category, FeedbackCategory.TIMEOUT)
        self.assertEqual(item.owner, FeedbackOwner.TOOLCHAIN)
        self.assertEqual(item.severity, FeedbackSeverity.FATAL)
        self.assertEqual(item.detail, "probe timed out")

    def test_executable_not_found_maps_to_toolchain_failure(
        self,
    ) -> None:
        invocation = base_invocation()
        invocation["toolchain_version_verification"] = {
            "status": "executable_not_found",
            "requested": "2023.2",
            "actual": None,
            "stderr": "",
        }
        invocation["execution"] = {
            "status": "blocked_before_csynth",
            "returncode": None,
            "timeout": False,
        }

        item = self.adapter.to_operator_report(
            invocation=invocation,
            report_id="missing",
        ).items[0]

        self.assertEqual(item.stage, FeedbackStage.TOOLCHAIN)
        self.assertEqual(
            item.category,
            FeedbackCategory.TOOLCHAIN_FAILURE,
        )
        self.assertEqual(item.owner, FeedbackOwner.TOOLCHAIN)

    def test_launch_error_maps_to_toolchain_failure(self) -> None:
        invocation = base_invocation()
        invocation["execution"] = {
            "status": "launch_error",
            "returncode": None,
            "timeout": False,
            "error_type": "OSError",
            "error": "cannot execute vitis-run",
        }

        item = self.adapter.to_operator_report(
            invocation=invocation,
            report_id="launch",
        ).items[0]

        self.assertEqual(item.stage, FeedbackStage.TOOLCHAIN)
        self.assertEqual(
            item.category,
            FeedbackCategory.TOOLCHAIN_FAILURE,
        )
        self.assertEqual(item.owner, FeedbackOwner.TOOLCHAIN)
        self.assertEqual(
            item.detail,
            "cannot execute vitis-run",
        )

    def test_nonzero_return_code_without_legacy_status(self) -> None:
        invocation = base_invocation()
        invocation["execution"]["returncode"] = 2

        item = self.adapter.to_operator_report(
            invocation=invocation,
            report_id="returncode",
        ).items[0]

        self.assertEqual(item.stage, FeedbackStage.CSYNTH)
        self.assertEqual(item.category, FeedbackCategory.UNKNOWN)
        self.assertEqual(item.severity, FeedbackSeverity.ERROR)

    def test_complete_source_evidence_is_preserved(self) -> None:
        invocation = base_invocation()

        report = self.adapter.to_operator_report(
            invocation=invocation,
            report_id="preserved",
            legacy_status="csynth_failed",
            error_msg="raw Vitis tail",
            evidence_ref="/tmp/run/csynth_invocation.json",
        )

        self.assertEqual(
            report.source_evidence["invocation"],
            invocation,
        )
        self.assertEqual(
            report.source_evidence["error_msg"],
            "raw Vitis tail",
        )
        self.assertEqual(
            report.metadata["evidence_ref"],
            "/tmp/run/csynth_invocation.json",
        )
        self.assertEqual(
            report.items[0].evidence_ref,
            "/tmp/run/csynth_invocation.json",
        )

    def test_report_round_trip(self) -> None:
        invocation = base_invocation()
        invocation["execution"]["returncode"] = 1

        original = self.adapter.to_operator_report(
            invocation=invocation,
            report_id="round-trip",
            legacy_status="csynth_failed",
        )
        restored = type(original).from_dict(
            original.to_dict()
        )

        self.assertEqual(restored, original)

    def test_adapter_does_not_mutate_invocation(self) -> None:
        invocation = base_invocation()
        before = copy.deepcopy(invocation)

        self.adapter.to_operator_report(
            invocation=invocation,
            report_id="immutable",
            legacy_status="csynth_failed",
        )

        self.assertEqual(invocation, before)

    def test_rejects_non_mapping_invocation(self) -> None:
        with self.assertRaises(TypeError):
            self.adapter.to_operator_report(
                invocation="csynth_invocation.json",
                report_id="invalid",
            )

    def test_rejects_non_string_error_message(self) -> None:
        with self.assertRaises(TypeError):
            self.adapter.to_operator_report(
                invocation=base_invocation(),
                report_id="invalid-error",
                error_msg={"message": "bad"},
            )

    def test_adapter_is_kernel_agnostic(self) -> None:
        families = (
            "array-map",
            "reduction",
            "stencil",
            "multi-output",
            "stream",
            "stateful",
        )

        reports = []
        for family in families:
            invocation = base_invocation()
            invocation["top_kernel"] = f"{family}_top"
            reports.append(
                self.adapter.to_operator_report(
                    invocation=invocation,
                    report_id=f"{family}-report",
                    legacy_status="succeeded",
                )
            )

        self.assertEqual(len(reports), len(families))


if __name__ == "__main__":
    unittest.main()
