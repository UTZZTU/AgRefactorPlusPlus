from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest

from agrefactor.campaign import (
    CampaignInvariantError,
    CampaignManifest,
    CampaignRunner,
)


EXPLICIT_IO = r'''
extern "C" int top(int x, int *out) {
    out[0] = x;
    return 0;
}
'''

PRIVATE_GLOBAL = r'''
static int state = 0;
extern "C" int top(int x) {
    state += x;
    return state;
}
'''


def write_csynth_evidence(
    root: Path,
    source: Path,
    *,
    status: str = "passed",
    complete: bool = True,
    source_sha256: str | None = None,
) -> Path:
    path = root / (
        f"{source.stem}_{status}_{'complete' if complete else 'incomplete'}"
        ".original_csynth.json"
    )
    payload = {
        "schema_version": 1,
        "evidence_view": "agent_safe",
        "phase": "original_csynth",
        "source_sha256": (
            source_sha256 or sha256(source.read_bytes()).hexdigest()
        ),
        "top_function": "top",
        "status": status,
        "tool_launched": True,
        "csynth_launched": True,
        "returncode": 0 if status == "passed" else 1,
        "timed_out": False,
        "evidence_sha256": "e" * 64 if complete else None,
        "evidence_ref": path.name,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def case(
    case_id,
    argv,
    *,
    cwd,
    timeout_s=2.0,
    primary_sample=False,
    eligibility=None,
):
    payload = {
        "case_id": case_id,
        "argv": argv,
        "cwd": str(cwd),
        "timeout_s": timeout_s,
        "primary_sample": primary_sample,
    }
    if eligibility is not None:
        payload["eligibility"] = eligibility
    return payload


def manifest(root, cases, *, heartbeat=0.03, max_wall=None):
    payload = {
        "schema_version": 1,
        "campaign_id": "r5-c-tests",
        "heartbeat_interval_s": heartbeat,
        "default_case_timeout_s": 2.0,
        "cases": cases,
    }
    if max_wall is not None:
        payload["max_wall_time_s"] = max_wall
    return CampaignManifest.from_dict(payload, base_dir=root)


class R5CCampaignTests(unittest.TestCase):
    def test_duplicate_case_ids_fail_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(CampaignInvariantError):
                manifest(
                    root,
                    [
                        case("same", [sys.executable, "-c", "pass"], cwd=root),
                        case("same", [sys.executable, "-c", "pass"], cwd=root),
                    ],
                )

    def test_shell_string_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(CampaignInvariantError):
                CampaignManifest.from_dict(
                    {
                        "campaign_id": "bad",
                        "cases": [
                            {
                                "case_id": "bad",
                                "argv": "echo unsafe",
                                "cwd": str(root),
                            }
                        ],
                    },
                    base_dir=root,
                )

    def test_passed_campaign_writes_start_progress_and_finish(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            runner = CampaignRunner(
                manifest(
                    root,
                    [
                        case(
                            "pass",
                            [sys.executable, "-c", "print('ok')"],
                            cwd=root,
                        )
                    ],
                ),
                artifact_root=artifacts,
            )
            result = runner.run()
            events = [
                json.loads(line)
                for line in (
                    artifacts / "campaign_events.jsonl"
                ).read_text(encoding="utf-8").splitlines()
            ]
            progress = json.loads(
                (artifacts / "campaign_progress.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(result.status, "passed")
        self.assertEqual(events[0]["event"], "campaign_started")
        self.assertEqual(events[-1]["event"], "campaign_finished")
        self.assertEqual(progress["state"], "passed")

    def test_long_case_emits_heartbeat(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            result = CampaignRunner(
                manifest(
                    root,
                    [
                        case(
                            "sleep",
                            [
                                sys.executable,
                                "-c",
                                "import time; time.sleep(0.14)",
                            ],
                            cwd=root,
                        )
                    ],
                    heartbeat=0.02,
                ),
                artifact_root=artifacts,
            ).run()
            events = [
                json.loads(line)
                for line in (
                    artifacts / "campaign_events.jsonl"
                ).read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(result.status, "passed")
        self.assertGreaterEqual(
            sum(item["event"] == "heartbeat" for item in events),
            1,
        )
        self.assertGreaterEqual(
            result.case_results[0]["heartbeat_count"],
            1,
        )

    def test_nonzero_case_is_fail_soft_and_next_case_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "ran.txt"
            artifacts = root / "artifacts"
            result = CampaignRunner(
                manifest(
                    root,
                    [
                        case(
                            "fail",
                            [sys.executable, "-c", "raise SystemExit(7)"],
                            cwd=root,
                        ),
                        case(
                            "continue",
                            [
                                sys.executable,
                                "-c",
                                (
                                    "from pathlib import Path; "
                                    f"Path({str(marker)!r}).write_text('yes')"
                                ),
                            ],
                            cwd=root,
                        ),
                    ],
                ),
                artifact_root=artifacts,
            ).run()
            marker_exists = marker.is_file()

        self.assertEqual(result.status, "completed_with_failures")
        self.assertEqual(result.case_results[0]["status"], "failed")
        self.assertEqual(result.case_results[1]["status"], "passed")
        self.assertTrue(marker_exists)

    def test_timeout_is_fail_soft_and_next_case_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "after-timeout.txt"
            result = CampaignRunner(
                manifest(
                    root,
                    [
                        case(
                            "timeout",
                            [
                                sys.executable,
                                "-c",
                                "import time; time.sleep(1)",
                            ],
                            cwd=root,
                            timeout_s=0.05,
                        ),
                        case(
                            "after",
                            [
                                sys.executable,
                                "-c",
                                (
                                    "from pathlib import Path; "
                                    f"Path({str(marker)!r}).write_text('yes')"
                                ),
                            ],
                            cwd=root,
                        ),
                    ],
                    heartbeat=0.01,
                ),
                artifact_root=root / "artifacts",
            ).run()
            marker_exists = marker.is_file()

        self.assertEqual(result.case_results[0]["status"], "timeout")
        self.assertEqual(result.case_results[1]["status"], "passed")
        self.assertTrue(marker_exists)

    def test_private_global_auto_primary_is_not_launched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "monobit_like.cpp"
            marker = root / "must-not-run.txt"
            source.write_text(PRIVATE_GLOBAL, encoding="utf-8")
            result = CampaignRunner(
                manifest(
                    root,
                    [
                        case(
                            "monobit",
                            [
                                sys.executable,
                                "-c",
                                (
                                    "from pathlib import Path; "
                                    f"Path({str(marker)!r}).write_text('bad')"
                                ),
                            ],
                            cwd=root,
                            primary_sample=True,
                            eligibility={
                                "source_path": str(source),
                                "top_function": "top",
                                "public_test_mode": "auto",
                                "original_csynth_evidence_path": str(
                                    write_csynth_evidence(root, source)
                                ),
                            },
                        )
                    ],
                ),
                artifact_root=root / "artifacts",
            ).run()

        self.assertEqual(
            result.case_results[0]["status"],
            "ineligible",
        )
        self.assertFalse(
            result.case_results[0]["tool_launch_observed"]
        )
        self.assertFalse(marker.exists())

    def test_original_csynth_false_skips_explicit_io_primary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "dfs_like.cpp"
            source.write_text(EXPLICIT_IO, encoding="utf-8")
            result = CampaignRunner(
                manifest(
                    root,
                    [
                        case(
                            "no-csynth",
                            [sys.executable, "-c", "print('not run')"],
                            cwd=root,
                            primary_sample=True,
                            eligibility={
                                "source_path": str(source),
                                "top_function": "top",
                                "public_test_mode": "auto",
                                "original_csynth_evidence_path": str(
                                    write_csynth_evidence(
                                        root, source, status="failed"
                                    )
                                ),
                            },
                        )
                    ],
                ),
                artifact_root=root / "artifacts",
            ).run()

        self.assertEqual(
            result.case_results[0]["status"],
            "ineligible",
        )
        self.assertEqual(
            result.case_results[0]["reason_code"],
            "original_csynth_failed",
        )
        self.assertFalse(
            result.case_results[0]["tool_launch_observed"]
        )

    def test_provided_tests_allow_stateful_primary_when_csynth_passed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "stateful.cpp"
            source.write_text(PRIVATE_GLOBAL, encoding="utf-8")
            result = CampaignRunner(
                manifest(
                    root,
                    [
                        case(
                            "provided",
                            [sys.executable, "-c", "pass"],
                            cwd=root,
                            primary_sample=True,
                            eligibility={
                                "source_path": str(source),
                                "top_function": "top",
                                "public_test_mode": "provided",
                                "original_csynth_evidence_path": str(
                                    write_csynth_evidence(root, source)
                                ),
                            },
                        )
                    ],
                ),
                artifact_root=root / "artifacts",
            ).run()

        self.assertEqual(result.case_results[0]["status"], "passed")
        self.assertTrue(
            result.case_results[0]["tool_launch_observed"]
        )

    def test_boolean_csynth_assertion_is_rejected_as_unknown_field(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "dfs_like.cpp"
            source.write_text(EXPLICIT_IO, encoding="utf-8")
            with self.assertRaises(CampaignInvariantError):
                manifest(
                    root,
                    [
                        case(
                            "boolean-only",
                            [sys.executable, "-c", "pass"],
                            cwd=root,
                            primary_sample=True,
                            eligibility={
                                "source_path": str(source),
                                "top_function": "top",
                                "public_test_mode": "auto",
                                "original_csynth_passed": True,
                            },
                        )
                    ],
                )

    def test_csynth_identity_mismatch_is_review_required_without_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "dfs_like.cpp"
            marker = root / "must-not-run.txt"
            source.write_text(EXPLICIT_IO, encoding="utf-8")
            evidence = write_csynth_evidence(
                root, source, source_sha256="a" * 64
            )
            result = CampaignRunner(
                manifest(
                    root,
                    [
                        case(
                            "identity-mismatch",
                            [
                                sys.executable,
                                "-c",
                                (
                                    "from pathlib import Path; "
                                    f"Path({str(marker)!r}).write_text('bad')"
                                ),
                            ],
                            cwd=root,
                            primary_sample=True,
                            eligibility={
                                "source_path": str(source),
                                "top_function": "top",
                                "public_test_mode": "auto",
                                "original_csynth_evidence_path": str(evidence),
                            },
                        )
                    ],
                ),
                artifact_root=root / "artifacts",
            ).run()
        self.assertEqual(
            result.case_results[0]["status"],
            "review_required",
        )
        self.assertEqual(
            result.case_results[0]["reason_code"],
            "original_csynth_identity_mismatch",
        )
        self.assertFalse(result.case_results[0]["tool_launch_observed"])
        self.assertFalse(marker.exists())

    def test_incomplete_claimed_pass_is_review_required_without_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "dfs_like.cpp"
            source.write_text(EXPLICIT_IO, encoding="utf-8")
            evidence = write_csynth_evidence(
                root, source, complete=False
            )
            result = CampaignRunner(
                manifest(
                    root,
                    [
                        case(
                            "incomplete-evidence",
                            [sys.executable, "-c", "pass"],
                            cwd=root,
                            primary_sample=True,
                            eligibility={
                                "source_path": str(source),
                                "top_function": "top",
                                "public_test_mode": "auto",
                                "original_csynth_evidence_path": str(evidence),
                            },
                        )
                    ],
                ),
                artifact_root=root / "artifacts",
            ).run()
        self.assertEqual(
            result.case_results[0]["status"],
            "review_required",
        )
        self.assertEqual(
            result.case_results[0]["reason_code"],
            "original_csynth_evidence_incomplete",
        )
        self.assertFalse(result.case_results[0]["tool_launch_observed"])

    def test_event_sequences_are_contiguous(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            CampaignRunner(
                manifest(
                    root,
                    [
                        case(
                            "one",
                            [sys.executable, "-c", "pass"],
                            cwd=root,
                        ),
                        case(
                            "two",
                            [sys.executable, "-c", "pass"],
                            cwd=root,
                        ),
                    ],
                ),
                artifact_root=artifacts,
            ).run()
            events = [
                json.loads(line)
                for line in (
                    artifacts / "campaign_events.jsonl"
                ).read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(
            [item["sequence"] for item in events],
            list(range(1, len(events) + 1)),
        )

    def test_artifact_root_must_be_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            (artifacts / "old.txt").write_text(
                "old",
                encoding="utf-8",
            )
            with self.assertRaises(CampaignInvariantError):
                CampaignRunner(
                    manifest(
                        root,
                        [
                            case(
                                "one",
                                [sys.executable, "-c", "pass"],
                                cwd=root,
                            )
                        ],
                    ),
                    artifact_root=artifacts,
                )


if __name__ == "__main__":
    unittest.main()
