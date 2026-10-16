from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from scripts.replay_diagnostic_experiences import replay_manifest


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "diagnostic_experience_replay"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, value) -> bytes:
    data = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    path.write_bytes(data)
    return data


class ExperienceReplayTests(unittest.TestCase):
    def test_frozen_fixture_replays_expected_retrieval_contracts(self):
        report = replay_manifest(FIXTURE_ROOT / "manifest.json")

        self.assertTrue(report["passed"])
        self.assertEqual(report["scenario_count"], 4)
        self.assertEqual(report["source_family_split_leaks"], [])
        scenarios = {item["scenario_id"]: item for item in report["scenarios"]}
        candidate = scenarios["development-known-candidate-with-prior-failed-action"]
        self.assertEqual(
            [item["experience_id"] for item in candidate["positive"]],
            ["experience-dev-cross-stage", "experience-dev-positive"],
        )
        self.assertEqual(
            [item["experience_id"] for item in candidate["negative"]],
            ["experience-dev-negative"],
        )
        self.assertTrue(candidate["negative"][0]["previously_attempted"])
        self.assertEqual(scenarios["development-no-applicable-experience"]["positive"], [])
        self.assertEqual(scenarios["development-no-applicable-experience"]["negative"], [])

    def test_replay_reads_only_fixture_and_does_not_write_experience_store(self):
        before = {
            path.relative_to(FIXTURE_ROOT): _sha256(path.read_bytes())
            for path in FIXTURE_ROOT.rglob("*")
            if path.is_file()
        }

        report = replay_manifest(FIXTURE_ROOT / "manifest.json")

        after = {
            path.relative_to(FIXTURE_ROOT): _sha256(path.read_bytes())
            for path in FIXTURE_ROOT.rglob("*")
            if path.is_file()
        }
        self.assertTrue(report["passed"])
        self.assertEqual(after, before)
        self.assertTrue(all(item["synthetic"] for item in report["scenarios"]))
        self.assertEqual(len(report["source_artifacts"]), 3)

    def test_rejects_fixture_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "fixture"
            shutil.copytree(FIXTURE_ROOT, copied)
            path = copied / "experiences.json"
            path.write_bytes(path.read_bytes() + b" ")

            with self.assertRaisesRegex(ValueError, "fixture hash mismatch"):
                replay_manifest(copied / "manifest.json")

    def test_rejects_path_traversal_before_reading_external_fixture(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            fixture = parent / "fixture"
            fixture.mkdir()
            outside = parent / "outside.json"
            outside_bytes = b"{}"
            outside.write_bytes(outside_bytes)
            manifest = {
                "schema_version": 1,
                "dataset_id": "path-traversal-test",
                "dataset_status": "synthetic_retrieval_contract_fixture",
                "experiences": {"path": "../outside.json", "sha256": _sha256(outside_bytes)},
                "queries": {"path": "queries.json", "sha256": _sha256(b"{}")},
                "sources": [],
            }
            _write_json(fixture / "manifest.json", manifest)

            with self.assertRaises(ValueError):
                replay_manifest(fixture / "manifest.json")

    def test_rejects_source_family_crossing_development_and_heldout(self):
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "fixture"
            shutil.copytree(FIXTURE_ROOT, copied)
            manifest_path = copied / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            queries_path = copied / manifest["queries"]["path"]
            queries = json.loads(queries_path.read_text(encoding="utf-8"))
            queries["scenarios"][0]["source_family"] = "heldout-experience-family"
            queries_bytes = _write_json(queries_path, queries)
            manifest["queries"]["sha256"] = _sha256(queries_bytes)
            _write_json(manifest_path, manifest)

            with self.assertRaisesRegex(ValueError, "source families cross"):
                replay_manifest(manifest_path)


if __name__ == "__main__":
    unittest.main()
