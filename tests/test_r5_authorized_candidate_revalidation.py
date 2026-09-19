from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from agrefactor.recovery.r5_authorized_revalidation import (
    R5AuthorizedRevalidationBundle,
    R5AuthorizedRevalidationError,
    _sealed_payloads,
)


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "r5_revalidate_authorized_candidate",
    ROOT / "scripts" / "r5_revalidate_authorized_candidate.py",
)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class R5AuthorizedCandidateRevalidationTests(unittest.TestCase):
    def _archive(self, root: Path, *, bad_hash: bool = False) -> tuple[Path, str, str]:
        payload = b"sealed-candidate\n"
        content = {
            "schema_version": 1,
            "files": {
                "candidate.cpp": (
                    "0" * 64 if bad_hash else hashlib.sha256(payload).hexdigest()
                )
            },
        }
        content_bytes = (
            json.dumps(content, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        archive = root / "evidence.zip"
        with zipfile.ZipFile(archive, "w") as sealed:
            for name, data in (
                ("candidate.cpp", payload),
                ("evidence_content_manifest.json", content_bytes),
            ):
                info = zipfile.ZipInfo(name, (2026, 9, 19, 0, 0, 0))
                info.external_attr = 0o100644 << 16
                sealed.writestr(info, data)
        return (
            archive,
            hashlib.sha256(archive.read_bytes()).hexdigest(),
            hashlib.sha256(content_bytes).hexdigest(),
        )

    def test_sealed_payloads_verify_every_member(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            archive, archive_sha, content_sha = self._archive(Path(raw))
            payloads = _sealed_payloads(
                archive,
                archive_sha256=archive_sha,
                content_manifest_file_sha256=content_sha,
            )
        self.assertEqual(payloads["candidate.cpp"], b"sealed-candidate\n")

    def test_sealed_payloads_reject_tampered_member_hash(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            archive, archive_sha, content_sha = self._archive(
                Path(raw), bad_hash=True
            )
            with self.assertRaisesRegex(
                R5AuthorizedRevalidationError, "member hash mismatch"
            ):
                _sealed_payloads(
                    archive,
                    archive_sha256=archive_sha,
                    content_manifest_file_sha256=content_sha,
                )

    def test_task_preserves_frozen_cosim_depths(self) -> None:
        bundle = R5AuthorizedRevalidationBundle(
            plan_sha256="1" * 64,
            case_id="case",
            reference_top="reference_top",
            candidate_top="candidate_top",
            source_sha256="2" * 64,
            candidate_before_sha256="3" * 64,
            candidate_after_sha256="4" * 64,
            paths={
                "reference": "reference.cpp",
                "public_test": "public.cpp",
                "hidden_test": "hidden.cpp",
            },
            file_sha256s={
                "reference": "5" * 64,
                "public_test": "6" * 64,
                "hidden_test": "7" * 64,
            },
            reference_code="void reference_top(int*);\n",
            candidate_code="void candidate_top(int*);\n",
            public_test_code="int main(){return 0;}\n",
            hidden_test_code="int main(){return 0;}\n",
            public_runtime_contract={
                "schema_version": 2,
                "kind": "public_differential_self_check_v1",
                "candidate_mismatch_returncodes": [1],
                "cosim_interface_depths": {"arr": 9},
            },
            authorization={"authorization_id": "authorization"},
            source_episode={"episode_id": "episode"},
            archive_sha256="8" * 64,
            content_manifest_file_sha256="9" * 64,
            source_episode_sha256="a" * 64,
        )
        task = RUNNER._task(ROOT, bundle, "run")
        public = next(
            suite for suite in task.test_suites if suite.split.value == "public"
        )
        self.assertEqual(public.runtime_contract["cosim_interface_depths"], {"arr": 9})
        self.assertEqual(task.mode.value, "refactor")


if __name__ == "__main__":
    unittest.main()
