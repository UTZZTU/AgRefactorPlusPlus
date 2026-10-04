import tempfile
import unittest
from pathlib import Path

from agrefactor.evaluation.owner_evidence import (
    resolve_source_owner,
    tool_launch_owner,
)


class OwnerEvidenceTests(unittest.TestCase):
    EXECUTION = {"status": "completed", "returncode": 1, "timeout": False}
    def test_exact_compile_unit_path_resolves(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "nested" / "candidate.cpp"
            source.parent.mkdir()
            source.write_text("x", encoding="utf-8")
            evidence = resolve_source_owner(
                [{"file": "nested/candidate.cpp"}],
                source_roles={"nested/candidate.cpp": "candidate"},
                work_dir=root,
                execution=self.EXECUTION,
            )
        self.assertEqual(evidence.owner, "candidate")
        self.assertTrue(evidence.evidence_complete)

    def test_basename_or_unknown_path_does_not_resolve(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = resolve_source_owner(
                [{"file": "candidate.cpp"}],
                source_roles={"nested/candidate.cpp": "candidate"},
                work_dir=directory,
                execution=self.EXECUTION,
            )
        self.assertEqual(evidence.owner, "unknown")
        self.assertFalse(evidence.evidence_complete)

    def test_timeout_is_not_toolchain_evidence(self):
        evidence = tool_launch_owner(timeout=True)
        self.assertEqual(evidence.owner, "unknown")
        self.assertFalse(evidence.evidence_complete)

    def test_explicit_launch_error_is_toolchain(self):
        evidence = tool_launch_owner(launch_error=True)
        self.assertEqual(evidence.owner, "toolchain")
        self.assertTrue(evidence.evidence_complete)

    def test_missing_or_timed_out_execution_cannot_prove_source_owner(self):
        for execution in (None, {"status": "timeout", "returncode": None, "timeout": True}):
            evidence = resolve_source_owner(
                [{"file": "candidate.cpp"}],
                source_roles={"candidate.cpp": "candidate"}, work_dir=".",
                execution=execution,
            )
            self.assertEqual(evidence.owner, "unknown")
            self.assertFalse(evidence.evidence_complete)

    def test_actual_compile_unit_limits_source_mapping(self):
        evidence = resolve_source_owner(
            [{"file": "candidate.cpp"}],
            source_roles={"testbench.cpp": "testbench", "candidate.cpp": "candidate"},
            work_dir=".", compile_units=["testbench.cpp"], execution=self.EXECUTION,
        )
        self.assertEqual(evidence.owner, "unknown")

    def test_source_span_keeps_original_and_generated_bridge_separate(self):
        spans = {"reference.cpp": [
            {"line_start": 2, "line_end": 4, "owner": "original"},
            {"line_start": 6, "line_end": 8, "owner": "testbench"},
        ]}
        for line, owner in ((3, "original"), (7, "testbench"), (5, "unknown")):
            evidence = resolve_source_owner(
                [{"file": "reference.cpp", "line": line}],
                source_roles={"reference.cpp": "unknown"}, work_dir=".",
                execution=self.EXECUTION, source_spans=spans,
            )
            self.assertEqual(evidence.owner, owner)

    def test_mixed_sources_and_unknown_roles_are_not_proven(self):
        for roles, files in (
            ({"a.cpp": "candidate", "b.cpp": "testbench"}, ["a.cpp", "b.cpp"]),
            ({"a.cpp": "invented_role"}, ["a.cpp"]),
        ):
            evidence = resolve_source_owner(
                [{"file": name} for name in files], source_roles=roles, work_dir=".",
                execution=self.EXECUTION,
            )
            self.assertEqual(evidence.owner, "unknown")


if __name__ == "__main__":
    unittest.main()
