from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "r5_1_audit_external_adapter_smoke",
    ROOT / "scripts" / "r5_1_audit_external_adapter_smoke.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def clean_result() -> dict:
    cases = []
    for source in ("c2hlsc", "hls-eval", "hlspilot", "hlsfactory"):
        cases.append(
            {
                "case_id": f"case-{source}",
                "source_id": source,
                "decision": "admit" if source == "c2hlsc" else "external-only",
                "status": "passed",
                "compile_returncode": 0,
                "run_returncode": 0,
                "pass_marker_observed": True,
            }
        )
    result = {
        "schema_version": 1,
        "status": "passed",
        "cases": cases,
        "provider_calls": 0,
        "vitis_launches": 0,
        "git_history_mutations": 0,
        "formal_admission_unchanged": True,
        "r5_accepted": False,
        "r6_started": False,
    }
    result["result_sha256"] = MODULE.sha_value(result)
    return result


class R51ExternalAdapterAuditTests(unittest.TestCase):
    def test_clean_four_source_result_passes(self) -> None:
        audit = MODULE.audit_result(clean_result())
        self.assertEqual(audit["status"], "passed")
        self.assertEqual(audit["critical_findings"], 0)
        self.assertEqual(audit["source_count"], 4)

    def test_nonzero_real_call_count_is_critical(self) -> None:
        result = clean_result()
        result["provider_calls"] = 1
        result["result_sha256"] = MODULE.sha_value(
            {key: value for key, value in result.items() if key != "result_sha256"}
        )
        audit = MODULE.audit_result(result)
        self.assertEqual(audit["status"], "failed")
        self.assertIn("nonzero_provider_calls", {item["code"] for item in audit["findings"]})

    def test_missing_oracle_marker_is_critical(self) -> None:
        result = clean_result()
        result["cases"][0]["pass_marker_observed"] = False
        result["result_sha256"] = MODULE.sha_value(
            {key: value for key, value in result.items() if key != "result_sha256"}
        )
        audit = MODULE.audit_result(result)
        self.assertEqual(audit["status"], "failed")


if __name__ == "__main__":
    unittest.main()
