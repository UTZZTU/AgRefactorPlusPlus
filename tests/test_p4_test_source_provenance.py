from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from agrefactor.config import (
    EvaluationSplit,
    TaskSpec,
    TestSourceKind,
    TestSourceProvenance,
    TestSourceSpec,
    TestSuiteSpec,
    resolve_test_source,
)
from agrefactor.evaluation import CsimSuiteEvaluator
from agrefactor.evidence import (
    TestEvaluationEvidence,
    TestEvaluationStatus,
)
from agrefactor.runtime import TraceRecorder, TraceEvidenceView


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_source(
    *,
    source_id: str = "public-main-source",
    revision: str | None = "r1",
    expected: str | None = None,
    kind: TestSourceKind = TestSourceKind.FILESYSTEM,
) -> TestSourceSpec:
    return TestSourceSpec(
        source_id=source_id,
        source_revision=revision,
        source_kind=kind,
        expected_content_sha256=expected,
    )


def make_suite(
    path: Path,
    *,
    split: EvaluationSplit = EvaluationSplit.PUBLIC,
    source: TestSourceSpec | None = None,
    suite_id: str = "suite-main",
) -> TestSuiteSpec:
    return TestSuiteSpec(
        suite_id=suite_id,
        suite_version="v1",
        split=split,
        case_count=2,
        testbench_path=str(path),
        source=source,
    )


class TestSourceSpecTests(unittest.TestCase):
    def test_source_id_is_trimmed(self):
        item = TestSourceSpec(source_id="  source-a  ")
        self.assertEqual(item.source_id, "source-a")

    def test_empty_source_id_is_rejected(self):
        with self.assertRaises(ValueError):
            TestSourceSpec(source_id=" ")

    def test_source_revision_is_trimmed(self):
        item = TestSourceSpec(
            source_id="a",
            source_revision=" r7 ",
        )
        self.assertEqual(item.source_revision, "r7")

    def test_string_kind_is_normalized(self):
        item = TestSourceSpec(
            source_id="a",
            source_kind="external",
        )
        self.assertIs(item.source_kind, TestSourceKind.EXTERNAL)

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            TestSourceSpec(
                source_id="a",
                source_kind="unknown",
            )

    def test_digest_is_normalized_to_lowercase(self):
        item = TestSourceSpec(
            source_id="a",
            expected_content_sha256="A" * 64,
        )
        self.assertEqual(
            item.expected_content_sha256,
            "a" * 64,
        )

    def test_invalid_digest_is_rejected(self):
        with self.assertRaises(ValueError):
            TestSourceSpec(
                source_id="a",
                expected_content_sha256="abc",
            )

    def test_round_trip(self):
        original = make_source(expected="b" * 64)
        restored = TestSourceSpec.from_dict(
            original.to_dict()
        )
        self.assertEqual(restored, original)

    def test_unknown_field_is_rejected(self):
        with self.assertRaises(ValueError):
            TestSourceSpec.from_dict(
                {
                    "source_id": "a",
                    "unexpected": True,
                }
            )

    def test_to_dict_is_json_serializable(self):
        json.dumps(make_source().to_dict())


class TestSourceResolutionTests(unittest.TestCase):
    def test_resolve_records_exact_digest_and_size(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "tb.cpp"
            path.write_text("int main(){}\n", encoding="utf-8")
            item = resolve_test_source(
                make_source(),
                path,
            )
            self.assertEqual(
                item.content_sha256,
                digest("int main(){}\n"),
            )
            self.assertEqual(
                item.size_bytes,
                len("int main(){}\n".encode("utf-8")),
            )

    def test_expected_digest_match_passes(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "tb.cpp"
            path.write_text("A", encoding="utf-8")
            item = resolve_test_source(
                make_source(expected=digest("A")),
                path,
            )
            self.assertEqual(item.content_sha256, digest("A"))

    def test_expected_digest_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "tb.cpp"
            path.write_text("A", encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError,
                "digest mismatch",
            ):
                resolve_test_source(
                    make_source(expected=digest("B")),
                    path,
                )

    def test_missing_path_fails(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(FileNotFoundError):
                resolve_test_source(
                    make_source(),
                    Path(root) / "missing.cpp",
                )

    def test_directory_path_fails(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(
                ValueError,
                "regular file",
            ):
                resolve_test_source(
                    make_source(),
                    root,
                )

    def test_non_filesystem_source_fails_local_resolution(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "tb.cpp"
            path.write_text("A", encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError,
                "filesystem",
            ):
                resolve_test_source(
                    make_source(kind=TestSourceKind.EXTERNAL),
                    path,
                )

    def test_execution_content_match_passes(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "tb.cpp"
            path.write_text("A", encoding="utf-8")
            item = resolve_test_source(
                make_source(),
                path,
                execution_content="A",
            )
            self.assertEqual(item.content_sha256, digest("A"))

    def test_execution_content_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "tb.cpp"
            path.write_text("A", encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError,
                "does not match",
            ):
                resolve_test_source(
                    make_source(),
                    path,
                    execution_content="B",
                )

    def test_same_content_different_paths_has_same_digest(self):
        with tempfile.TemporaryDirectory() as root:
            first = Path(root) / "a.cpp"
            second = Path(root) / "b.cpp"
            first.write_text("A", encoding="utf-8")
            second.write_text("A", encoding="utf-8")
            a = resolve_test_source(make_source(), first)
            b = resolve_test_source(make_source(), second)
            self.assertEqual(a.content_sha256, b.content_sha256)
            self.assertNotEqual(a.resolved_path, b.resolved_path)

    def test_changed_content_changes_digest(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "tb.cpp"
            path.write_text("A", encoding="utf-8")
            a = resolve_test_source(make_source(), path)
            path.write_text("B", encoding="utf-8")
            b = resolve_test_source(make_source(), path)
            self.assertNotEqual(a.content_sha256, b.content_sha256)


class TestSuiteSourceIntegrationTests(unittest.TestCase):
    def test_suite_source_round_trip(self):
        suite = TestSuiteSpec(
            suite_id="public",
            testbench_path="/tmp/public.cpp",
            source=make_source(),
        )
        restored = TestSuiteSpec.from_dict(
            suite.to_dict()
        )
        self.assertEqual(restored, suite)

    def test_legacy_suite_omits_source_field(self):
        suite = TestSuiteSpec(
            suite_id="legacy",
            testbench_path="/tmp/legacy.cpp",
        )
        self.assertNotIn("source", suite.to_dict())

    def test_source_mapping_is_parsed(self):
        suite = TestSuiteSpec(
            suite_id="mapped",
            testbench_path="/tmp/mapped.cpp",
            source={"source_id": "mapped-source"},
        )
        self.assertIsInstance(suite.source, TestSourceSpec)

    def test_source_requires_testbench_path(self):
        with self.assertRaisesRegex(
            ValueError,
            "requires testbench_path",
        ):
            TestSuiteSpec(
                suite_id="invalid",
                source=make_source(),
            )

    def test_task_multiple_suites_keep_independent_sources(self):
        first = TestSuiteSpec(
            suite_id="public-a",
            testbench_path="/tmp/a.cpp",
            source=make_source(source_id="source-a"),
        )
        second = TestSuiteSpec(
            suite_id="hidden-b",
            split=EvaluationSplit.HIDDEN,
            testbench_path="/tmp/b.cpp",
            source=make_source(source_id="source-b"),
        )
        task = TaskSpec(
            task_id="task",
            kernel_path="kernel.cpp",
            kernel_name="top",
            test_suites=(first, second),
        )
        self.assertEqual(
            [suite.source.source_id for suite in task.test_suites],
            ["source-a", "source-b"],
        )


class TestEvidenceProvenanceTests(unittest.TestCase):
    def make_evidence(
        self,
        split: EvaluationSplit,
    ) -> TestEvaluationEvidence:
        provenance = TestSourceProvenance(
            source_id="hidden-source",
            source_revision="r2",
            source_kind=TestSourceKind.FILESYSTEM,
            content_sha256="c" * 64,
            size_bytes=123,
            resolved_path="/private/hidden.cpp",
        )
        suite = TestSuiteSpec(
            suite_id="suite",
            suite_version="v2",
            split=split,
            testbench_path="/private/hidden.cpp",
            source=make_source(source_id="hidden-source"),
        )
        return TestEvaluationEvidence(
            suite=suite,
            status=TestEvaluationStatus.FAILED,
            failed_cases=1,
            summary="failure details",
            details={"secret": "diagnostic"},
            artifacts=("/private/log.txt",),
            source_provenance=provenance,
        )

    def test_operator_view_includes_full_provenance(self):
        payload = self.make_evidence(
            EvaluationSplit.HIDDEN
        ).to_dict()
        self.assertEqual(
            payload["source_provenance"]["content_sha256"],
            "c" * 64,
        )
        self.assertEqual(
            payload["source_provenance"]["resolved_path"],
            "/private/hidden.cpp",
        )

    def test_public_agent_view_includes_full_provenance(self):
        payload = self.make_evidence(
            EvaluationSplit.PUBLIC
        ).to_agent_dict()
        self.assertEqual(
            payload["source_provenance"]["content_sha256"],
            "c" * 64,
        )

    def test_hidden_agent_view_redacts_digest_and_path(self):
        payload = self.make_evidence(
            EvaluationSplit.HIDDEN
        ).to_agent_dict()
        source = payload["source_provenance"]
        self.assertTrue(source["redacted"])
        self.assertNotIn("content_sha256", source)
        self.assertNotIn("resolved_path", source)
        self.assertNotIn("size_bytes", source)

    def test_hidden_agent_view_keeps_safe_identity(self):
        payload = self.make_evidence(
            EvaluationSplit.HIDDEN
        ).to_agent_dict()
        source = payload["source_provenance"]
        self.assertEqual(source["source_id"], "hidden-source")
        self.assertEqual(source["source_revision"], "r2")

    def test_evidence_round_trip_preserves_provenance(self):
        original = self.make_evidence(
            EvaluationSplit.PUBLIC
        )
        restored = TestEvaluationEvidence.from_dict(
            original.to_dict()
        )
        self.assertEqual(restored, original)


class TestCsimProvenanceIntegrationTests(unittest.TestCase):
    def test_evaluator_attaches_verified_source(self):
        calls = []

        def executor(work_dir, context, timelimit, *, budget=None):
            calls.append((work_dir, context, timelimit, budget))
            return "succeeded", ""

        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "tb.cpp"
            path.write_text("TB", encoding="utf-8")
            suite = make_suite(
                path,
                source=make_source(expected=digest("TB")),
            )
            result = CsimSuiteEvaluator(
                executor=executor
            ).evaluate(
                work_dir=root,
                context_variables={"testbench": "TB"},
                suite=suite,
            )
            self.assertEqual(len(calls), 1)
            self.assertEqual(
                result.evidence.source_provenance.content_sha256,
                digest("TB"),
            )

    def test_digest_mismatch_blocks_before_executor(self):
        called = False

        def executor(*args, **kwargs):
            nonlocal called
            called = True
            return "succeeded", ""

        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "tb.cpp"
            path.write_text("A", encoding="utf-8")
            suite = make_suite(
                path,
                source=make_source(expected=digest("B")),
            )
            with self.assertRaisesRegex(
                ValueError,
                "digest mismatch",
            ):
                CsimSuiteEvaluator(
                    executor=executor
                ).evaluate(
                    work_dir=root,
                    context_variables={"testbench": "A"},
                    suite=suite,
                )
            self.assertFalse(called)

    def test_context_mismatch_blocks_before_executor(self):
        called = False

        def executor(*args, **kwargs):
            nonlocal called
            called = True
            return "succeeded", ""

        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "tb.cpp"
            path.write_text("A", encoding="utf-8")
            suite = make_suite(
                path,
                source=make_source(),
            )
            with self.assertRaisesRegex(
                ValueError,
                "does not match",
            ):
                CsimSuiteEvaluator(
                    executor=executor
                ).evaluate(
                    work_dir=root,
                    context_variables={"testbench": "B"},
                    suite=suite,
                )
            self.assertFalse(called)

    def test_hidden_default_trace_redacts_source_details(self):
        def executor(*args, **kwargs):
            return "csim_failed", "private diagnostic"

        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "hidden.cpp"
            path.write_text("H", encoding="utf-8")
            suite = make_suite(
                path,
                split=EvaluationSplit.HIDDEN,
                source=make_source(source_id="hidden-source"),
            )
            trace = TraceRecorder("run")
            CsimSuiteEvaluator(
                executor=executor
            ).evaluate(
                work_dir=root,
                context_variables={"testbench": "H"},
                suite=suite,
                trace=trace,
            )
            payload = trace.events[0].metadata[
                "test_evaluation"
            ]
            encoded = json.dumps(payload, sort_keys=True)
            self.assertNotIn(digest("H"), encoded)
            self.assertNotIn(str(path.resolve()), encoded)
            self.assertIn("hidden-source", encoded)

    def test_operator_trace_keeps_full_provenance(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "hidden.cpp"
            path.write_text("H", encoding="utf-8")
            provenance = resolve_test_source(
                make_source(source_id="hidden-source"),
                path,
            )
            evidence = TestEvaluationEvidence(
                suite=make_suite(
                    path,
                    split=EvaluationSplit.HIDDEN,
                    source=make_source(source_id="hidden-source"),
                ),
                status=TestEvaluationStatus.PASSED,
                source_provenance=provenance,
            )
            trace = TraceRecorder("run")
            event = trace.record_test_evaluation(
                evidence,
                view=TraceEvidenceView.OPERATOR_FULL,
            )
            encoded = json.dumps(event.metadata, sort_keys=True)
            self.assertIn(digest("H"), encoded)
            self.assertIn(str(path.resolve()), encoded)

    def test_legacy_suite_without_source_remains_supported(self):
        calls = []

        def executor(*args, **kwargs):
            calls.append(True)
            return "succeeded", ""

        with tempfile.TemporaryDirectory() as root:
            suite = TestSuiteSpec(
                suite_id="legacy",
                testbench_path="/not/resolved/by/legacy.cpp",
            )
            result = CsimSuiteEvaluator(
                executor=executor
            ).evaluate(
                work_dir=root,
                context_variables={"testbench": "legacy"},
                suite=suite,
            )
            self.assertEqual(calls, [True])
            self.assertIsNone(
                result.evidence.source_provenance
            )

    def test_two_suites_keep_independent_provenance(self):
        def executor(*args, **kwargs):
            return "succeeded", ""

        with tempfile.TemporaryDirectory() as root:
            first_path = Path(root) / "a.cpp"
            second_path = Path(root) / "b.cpp"
            first_path.write_text("A", encoding="utf-8")
            second_path.write_text("B", encoding="utf-8")
            evaluator = CsimSuiteEvaluator(executor=executor)
            first = evaluator.evaluate(
                work_dir=root,
                context_variables={"testbench": "A"},
                suite=make_suite(
                    first_path,
                    source=make_source(source_id="source-a"),
                    suite_id="public-a",
                ),
            )
            second = evaluator.evaluate(
                work_dir=root,
                context_variables={"testbench": "B"},
                suite=make_suite(
                    second_path,
                    split=EvaluationSplit.HIDDEN,
                    source=make_source(source_id="source-b"),
                    suite_id="hidden-b",
                ),
            )
            self.assertNotEqual(
                first.evidence.source_provenance.content_sha256,
                second.evidence.source_provenance.content_sha256,
            )


if __name__ == "__main__":
    unittest.main()
