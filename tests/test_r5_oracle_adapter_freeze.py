from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "r5_freeze_oracle_adapters",
    ROOT / "scripts" / "r5_freeze_oracle_adapters.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _write_repo(root: Path) -> tuple[Path, dict]:
    repo = root / "repo"
    repo.mkdir()
    cases = []
    for index, (period, control) in enumerate(
        (("history", "positive"), ("future", "inapplicable_or_confusable")),
        start=1,
    ):
        case = repo / f"case{index}"
        case.mkdir()
        for name in ("kernel.cpp", "main.cpp", "tb.cpp"):
            (case / name).write_text(f"int source_{index};\n", encoding="utf-8")
        public = (
            "int top(int); int top_hls(int); "
            "int main(){if(top(1)!=top_hls(1)){return 1;}return 0;}\n"
        )
        hidden = (
            "int top(int); int top_hls(int); "
            f"int main(){{if(top({index + 1})!=top_hls({index + 1}))"
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
                "legacy_candidate": f"case{index}/main.cpp",
                "legacy_testbench": f"case{index}/tb.cpp",
                "top": "top",
                "public_test": f"case{index}/public.cpp",
                "hidden_test": f"case{index}/hidden.cpp",
                "prior_evidence": "pre-R5",
            }
        )
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "test"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fixture"], check=True)
    return repo, {
        "schema_version": 1,
        "plan_id": "test",
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


class R5OracleAdapterFreezeTests(unittest.TestCase):
    def test_freeze_binds_distinct_history_future_and_controls(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo, plan = _write_repo(Path(raw))
            manifest = MODULE.build_manifest(repo, plan)
        self.assertEqual(manifest["status"], "ready_for_zero_call_protocol_audit")
        self.assertEqual(manifest["history_case_ids"], ["case-1"])
        self.assertEqual(manifest["future_case_ids"], ["case-2"])
        self.assertTrue(manifest["source_holdout_verified"])
        self.assertFalse(manifest["real_campaign_allowed"])
        self.assertEqual(manifest["provider_calls"], 0)

    def test_implementation_include_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo, plan = _write_repo(Path(raw))
            (repo / "case1/public.cpp").write_text(
                '#include "main.cpp"\nint main(){return 1;}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "must not include"):
                MODULE.build_manifest(repo, plan)

    def test_source_crossing_periods_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo, plan = _write_repo(Path(raw))
            plan["cases"][1]["source"] = plan["cases"][0]["source"]
            with self.assertRaisesRegex(ValueError, "crosses"):
                MODULE.build_manifest(repo, plan)

    def test_checked_plan_has_syntax_valid_distinct_adapters(self) -> None:
        plan = json.loads(
            (ROOT / "configs/r5/oracle_adapters/plan.json").read_text(
                encoding="utf-8"
            )
        )
        manifest = MODULE.build_manifest(ROOT, plan)
        self.assertEqual(len(manifest["history_case_ids"]), 2)
        self.assertEqual(len(manifest["future_case_ids"]), 2)
        for record in manifest["cases"]:
            for split in ("public_test", "hidden_test"):
                subprocess.run(
                    [
                        "g++",
                        "-std=c++17",
                        "-fsyntax-only",
                        str(ROOT / record["paths"][split]),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )


if __name__ == "__main__":
    unittest.main()
