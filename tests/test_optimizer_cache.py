import json
from pathlib import Path
import tempfile
import unittest

from agrefactor.optimization import (
    QualificationEvidenceCache,
    SuiteIdentity,
    ValidationCacheIdentity,
    build_toolchain_fingerprint,
    suite_identity_from_file,
)


SOURCE_SHA = "1" * 64
TOOLCHAIN_SHA = "2" * 64


def identity(**overrides):
    values = {
        "source_sha256": SOURCE_SHA,
        "effective_target": {
            "schema_version": 2,
            "profile": {
                "name": "vitis-2023.2-default",
                "toolchain": "vitis_hls",
                "toolchain_version": "2023.2",
                "device": "xcu200-fsgd2104-2-e",
                "clock_period_ns": 5.0,
                "compile_flags": ["-std=c++14"],
                "parser_profile": "vitis-hls-2023.2",
                "resource_limits": {},
            },
        },
        "toolchain_fingerprint_sha256": TOOLCHAIN_SHA,
        "suites": (
            SuiteIdentity(
                suite_id="public-main",
                split="public",
                content_sha256="3" * 64,
                suite_version="1",
            ),
            SuiteIdentity(
                suite_id="hidden-final",
                split="hidden",
                content_sha256="4" * 64,
                suite_version="1",
            ),
        ),
        "compile_flags": ("-std=c++14",),
        "clock_period_ns": 5.0,
        "device": "xcu200-fsgd2104-2-e",
        "parser_profile": "vitis-hls-2023.2",
    }
    values.update(overrides)
    return ValidationCacheIdentity.build(**values)


class ValidationCacheIdentityTests(unittest.TestCase):
    def test_build_is_deterministic(self):
        self.assertEqual(identity(), identity())

    def test_round_trip(self):
        original = identity()
        restored = ValidationCacheIdentity.from_dict(original.to_dict())
        self.assertEqual(restored, original)

    def test_suite_order_is_canonical(self):
        first = identity()
        second = identity(suites=tuple(reversed(first.public_suites + first.hidden_suites)))
        self.assertEqual(first.cache_key_sha256, second.cache_key_sha256)

    def test_source_change_misses(self):
        self.assertNotEqual(
            identity().cache_key_sha256,
            identity(source_sha256="5" * 64).cache_key_sha256,
        )

    def test_target_change_misses(self):
        changed = identity(
            effective_target={
                "profile": {
                    "name": "vitis-2023.2-default",
                    "toolchain": "vitis_hls",
                    "toolchain_version": "2023.2",
                    "device": "different-device",
                    "clock_period_ns": 5.0,
                    "compile_flags": ["-std=c++14"],
                    "parser_profile": "vitis-hls-2023.2",
                    "resource_limits": {},
                }
            }
        )
        self.assertNotEqual(identity().cache_key_sha256, changed.cache_key_sha256)

    def test_toolchain_change_misses(self):
        self.assertNotEqual(
            identity().cache_key_sha256,
            identity(toolchain_fingerprint_sha256="6" * 64).cache_key_sha256,
        )

    def test_public_suite_change_misses(self):
        changed = identity(
            suites=(
                SuiteIdentity(
                    suite_id="public-main",
                    split="public",
                    content_sha256="7" * 64,
                ),
                SuiteIdentity(
                    suite_id="hidden-final",
                    split="hidden",
                    content_sha256="4" * 64,
                ),
            )
        )
        self.assertNotEqual(identity().cache_key_sha256, changed.cache_key_sha256)

    def test_hidden_suite_change_misses(self):
        changed = identity(
            suites=(
                SuiteIdentity(
                    suite_id="public-main",
                    split="public",
                    content_sha256="3" * 64,
                ),
                SuiteIdentity(
                    suite_id="hidden-final",
                    split="hidden",
                    content_sha256="8" * 64,
                ),
            )
        )
        self.assertNotEqual(identity().cache_key_sha256, changed.cache_key_sha256)

    def test_compile_flag_change_misses(self):
        self.assertNotEqual(
            identity().cache_key_sha256,
            identity(compile_flags=("-std=c++17",)).cache_key_sha256,
        )

    def test_clock_change_misses(self):
        self.assertNotEqual(
            identity().cache_key_sha256,
            identity(clock_period_ns=4.0).cache_key_sha256,
        )

    def test_device_change_misses(self):
        self.assertNotEqual(
            identity().cache_key_sha256,
            identity(device="xcku115-flva1517-2-e").cache_key_sha256,
        )

    def test_parser_change_misses(self):
        self.assertNotEqual(
            identity().cache_key_sha256,
            identity(parser_profile="vitis-hls-generic").cache_key_sha256,
        )

    def test_pipeline_version_change_misses(self):
        self.assertNotEqual(
            identity().cache_key_sha256,
            identity(validation_pipeline_version="stage3-qualification-v2").cache_key_sha256,
        )

    def test_schema_version_change_misses(self):
        self.assertNotEqual(
            identity().cache_key_sha256,
            identity(validation_schema_version=2).cache_key_sha256,
        )

    def test_context_excludes_source(self):
        first = identity()
        second = identity(source_sha256="9" * 64)
        self.assertEqual(
            first.comparison_context_identity_sha256,
            second.comparison_context_identity_sha256,
        )

    def test_unknown_field_rejected(self):
        payload = identity().to_dict()
        payload["unexpected"] = True
        with self.assertRaises(ValueError):
            ValidationCacheIdentity.from_dict(payload)

    def test_tampered_declared_hash_rejected(self):
        payload = identity().to_dict()
        payload["cache_key_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            ValidationCacheIdentity.from_dict(payload)

    def test_secret_target_key_rejected(self):
        with self.assertRaises(ValueError):
            identity(effective_target={"api_key": "secret"})

    def test_toolchain_manifest_hash_is_deterministic(self):
        first = build_toolchain_fingerprint(
            {"actual_version": "2023.2", "executable_sha256": "a" * 64}
        )
        second = build_toolchain_fingerprint(
            {"executable_sha256": "a" * 64, "actual_version": "2023.2"}
        )
        self.assertEqual(first, second)

    def test_toolchain_secret_rejected(self):
        with self.assertRaises(ValueError):
            build_toolchain_fingerprint({"api_key": "not-allowed"})

    def test_suite_identity_from_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tb.cpp"
            path.write_text("int main(){return 0;}\n", encoding="utf-8")
            result = suite_identity_from_file(
                suite_id="public-main",
                split="public",
                path=path,
            )
            self.assertEqual(result.split, "public")
            self.assertEqual(len(result.content_sha256), 64)

    def test_suite_identity_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.cpp"
            target.write_text("x", encoding="utf-8")
            link = root / "link.cpp"
            link.symlink_to(target)
            with self.assertRaises(FileNotFoundError):
                suite_identity_from_file(
                    suite_id="public-main",
                    split="public",
                    path=link,
                )


class QualificationEvidenceCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.cache = QualificationEvidenceCache(self.temporary.name)
        self.identity = identity()
        self.evidence = {
            "schema_version": 1,
            "status": "accepted",
            "steps": [],
            "correctness_passed": True,
            "synthesis_passed": True,
            "objective_feasible": True,
            "ppa": {"report_sha256": "a" * 64},
            "decision": {"decision": "update_best"},
        }

    def test_store_then_load(self):
        path = self.cache.store(self.identity, self.evidence)
        self.assertTrue(path.is_file())
        self.assertEqual(self.cache.load(self.identity), self.evidence)

    def test_missing_entry_is_none(self):
        self.assertIsNone(self.cache.load(self.identity))

    def test_identical_store_is_idempotent(self):
        first = self.cache.store(self.identity, self.evidence)
        second = self.cache.store(self.identity, self.evidence)
        self.assertEqual(first, second)

    def test_different_existing_evidence_rejected(self):
        self.cache.store(self.identity, self.evidence)
        with self.assertRaises(FileExistsError):
            self.cache.store(
                self.identity,
                {**self.evidence, "status": "rejected"},
            )

    def test_tampered_evidence_rejected(self):
        path = self.cache.store(self.identity, self.evidence)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["evidence"]["status"] = "rejected"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.cache.load(self.identity)

    def test_tampered_identity_rejected(self):
        path = self.cache.store(self.identity, self.evidence)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["identity"]["source_sha256"] = "f" * 64
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.cache.load(self.identity)

    def test_raw_hidden_payload_key_rejected(self):
        with self.assertRaises(ValueError):
            self.cache.store(
                self.identity,
                {"hidden_testbench": "private content"},
            )

    def test_operator_full_payload_key_rejected(self):
        with self.assertRaises(ValueError):
            self.cache.store(
                self.identity,
                {"operator_full_payload": {"detail": "x"}},
            )

    def test_secret_key_rejected(self):
        with self.assertRaises(ValueError):
            self.cache.store(self.identity, {"api_key": "secret"})

    def test_cache_entry_symlink_rejected(self):
        path = self.cache.store(self.identity, self.evidence)
        content = path.read_bytes()
        path.unlink()
        target = Path(self.temporary.name) / "target.json"
        target.write_bytes(content)
        path.symlink_to(target)
        with self.assertRaises(ValueError):
            self.cache.load(self.identity)

    def test_atomic_store_leaves_no_temp(self):
        self.cache.store(self.identity, self.evidence)
        self.assertEqual(tuple(Path(self.temporary.name).rglob("*.tmp")), ())


if __name__ == "__main__":
    unittest.main()
