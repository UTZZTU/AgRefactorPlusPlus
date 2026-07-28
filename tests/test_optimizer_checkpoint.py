from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from agrefactor.optimization import (
    CandidateRecord,
    CandidateStatus,
    OptimizationLevel,
    OptimizerCheckpointWriter,
    OptimizerState,
)


BASELINE_SOURCE = b"void top() { /* baseline */ }\n"
CANDIDATE_SOURCE = b"void top() { /* candidate */ }\n"
CREATED = "2026-07-28T00:00:00Z"


def baseline_record():
    return CandidateRecord(
        candidate_id="baseline",
        sequence=0,
        parent_candidate_id=None,
        hypothesis_id=None,
        level=None,
        source_sha256=sha256(BASELINE_SOURCE).hexdigest(),
        source_artifact="candidates/baseline/source.cpp",
        status=CandidateStatus.GENERATED,
        created_at_utc=CREATED,
    )


def accepted_baseline():
    return baseline_record().transition_to(
        "validating"
    ).transition_to(
        "accepted",
        correctness={"preflight": "passed", "public": "passed", "hidden": "passed"},
        synthesis={"csynth": "passed"},
        decision={"decision": "initialize_best_correct"},
    )


def rejected_candidate():
    return CandidateRecord(
        candidate_id="cand-0001",
        sequence=1,
        parent_candidate_id="baseline",
        hypothesis_id="hyp-0001",
        level=OptimizationLevel.STRUCTURAL,
        source_sha256=sha256(CANDIDATE_SOURCE).hexdigest(),
        source_artifact="candidates/cand-0001/source.cpp",
        status="generated",
        created_at_utc=CREATED,
    ).transition_to("validating").transition_to(
        "rejected",
        correctness={"preflight": "failed"},
        decision={"decision": "reject", "reason": "preflight_failed"},
    )


class OptimizerCheckpointWriterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "optimizer"
        self.writer = OptimizerCheckpointWriter(self.root)
        self.baseline = accepted_baseline()
        self.writer.write_candidate_source(self.baseline, BASELINE_SOURCE)
        self.state = OptimizerState.initial(
            run_id="run-1"
        ).with_qualified_baseline(self.baseline)

    def test_first_checkpoint_writes_required_files(self):
        snapshot = self.writer.write_checkpoint(
            self.state,
            {"baseline": self.baseline},
        )
        self.assertEqual(snapshot.state.checkpoint_sequence, 1)
        self.assertTrue((self.root / "state.json").is_file())
        self.assertTrue((self.root / "candidate_index.json").is_file())
        self.assertTrue((self.root / "best_correct.cpp").is_file())
        self.assertFalse((self.root / "best_ppa.cpp").exists())
        self.assertTrue(
            (self.root / "checkpoints/checkpoint-0001.json").is_file()
        )
        self.assertEqual(
            (self.root / "best_correct.cpp").read_bytes(),
            BASELINE_SOURCE,
        )

    def test_second_checkpoint_is_monotonic_and_deterministic(self):
        first = self.writer.write_checkpoint(
            self.state,
            {"baseline": self.baseline},
        )
        candidate = rejected_candidate()
        self.writer.write_candidate_source(candidate, CANDIDATE_SOURCE)
        next_state = replace(
            first.state,
            current_candidate_id="baseline",
            executed_candidate_count=1,
        )
        second = self.writer.write_checkpoint(
            next_state,
            {"baseline": self.baseline, "cand-0001": candidate},
        )
        self.assertEqual(second.state.checkpoint_sequence, 2)
        self.assertEqual(
            second.checkpoint_path.name,
            "checkpoint-0002.json",
        )
        self.assertEqual(
            (self.root / "best_correct.cpp").read_bytes(),
            BASELINE_SOURCE,
        )
        payload = json.loads(second.checkpoint_path.read_text())
        self.assertEqual(payload["checkpoint_sequence"], 2)
        self.assertEqual(payload["previous_checkpoint_sequence"], 1)

    def test_rejected_candidate_never_overwrites_best_correct(self):
        first = self.writer.write_checkpoint(
            self.state,
            {"baseline": self.baseline},
        )
        candidate = rejected_candidate()
        self.writer.write_candidate_source(candidate, CANDIDATE_SOURCE)
        self.writer.write_checkpoint(
            replace(first.state, executed_candidate_count=1),
            {"baseline": self.baseline, "cand-0001": candidate},
        )
        self.assertNotEqual(BASELINE_SOURCE, CANDIDATE_SOURCE)
        self.assertEqual(
            (self.root / "best_correct.cpp").read_bytes(),
            BASELINE_SOURCE,
        )

    def test_source_hash_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            self.writer.write_candidate_source(
                self.baseline,
                b"tampered source\n",
            )

    def test_checkpoint_rejects_missing_source_artifact(self):
        other_root = Path(self.temporary.name) / "missing" / "optimizer"
        writer = OptimizerCheckpointWriter(other_root)
        with self.assertRaises(FileNotFoundError):
            writer.write_checkpoint(
                self.state,
                {"baseline": self.baseline},
            )

    def test_checkpoint_rejects_stale_sequence(self):
        self.writer.write_checkpoint(
            self.state,
            {"baseline": self.baseline},
        )
        with self.assertRaises(ValueError):
            self.writer.write_checkpoint(
                self.state,
                {"baseline": self.baseline},
            )

    def test_write_failure_keeps_old_checkpoint_recoverable(self):
        first = self.writer.write_checkpoint(
            self.state,
            {"baseline": self.baseline},
        )
        candidate = rejected_candidate()
        self.writer.write_candidate_source(candidate, CANDIDATE_SOURCE)

        def fail_before_state(label, path):
            if label == "state":
                raise OSError("injected write failure")

        failing = OptimizerCheckpointWriter(
            self.root,
            before_write=fail_before_state,
        )
        with self.assertRaises(OSError):
            failing.write_checkpoint(
                replace(first.state, executed_candidate_count=1),
                {"baseline": self.baseline, "cand-0001": candidate},
            )
        self.assertFalse(
            (self.root / "checkpoints/checkpoint-0002.json").exists()
        )
        recovered = self.writer.recover_latest()
        self.assertEqual(recovered.state.checkpoint_sequence, 1)
        index = json.loads((self.root / "candidate_index.json").read_text())
        self.assertEqual(
            [item["candidate_id"] for item in index["candidates"]],
            ["baseline"],
        )
        self.assertEqual(
            (self.root / "best_correct.cpp").read_bytes(),
            BASELINE_SOURCE,
        )

    def test_recovery_skips_corrupt_newer_checkpoint(self):
        self.writer.write_checkpoint(
            self.state,
            {"baseline": self.baseline},
        )
        corrupt = self.root / "checkpoints/checkpoint-0002.json"
        corrupt.write_text("{not-json", encoding="utf-8")
        recovered = self.writer.load_latest()
        self.assertEqual(recovered.state.checkpoint_sequence, 1)

    def test_recovery_returns_typed_state_and_index(self):
        written = self.writer.write_checkpoint(
            self.state,
            {"baseline": self.baseline},
        )
        (self.root / "state.json").write_text("{}\n", encoding="utf-8")
        recovered = self.writer.recover_latest()
        self.assertEqual(recovered.state, written.state)
        self.assertEqual(recovered.candidates["baseline"], self.baseline)
        restored = json.loads((self.root / "state.json").read_text())
        self.assertEqual(restored, written.state.to_dict())

    def test_tampered_candidate_source_invalidates_checkpoint(self):
        self.writer.write_checkpoint(
            self.state,
            {"baseline": self.baseline},
        )
        source = self.root / self.baseline.source_artifact
        source.write_bytes(b"tampered\n")
        with self.assertRaises(RuntimeError):
            self.writer.load_latest()

    def test_checkpoint_state_and_index_hashes_detect_tampering(self):
        snapshot = self.writer.write_checkpoint(
            self.state,
            {"baseline": self.baseline},
        )
        payload = json.loads(snapshot.checkpoint_path.read_text())
        payload["state"]["current_round"] = 99
        snapshot.checkpoint_path.write_text(
            json.dumps(payload, sort_keys=True),
            encoding="utf-8",
        )
        with self.assertRaises(RuntimeError):
            self.writer.load_latest()

    def test_candidate_source_path_cannot_escape_root(self):
        with self.assertRaises(ValueError):
            replace(
                self.baseline,
                source_artifact="candidates/baseline/../../outside.cpp",
            )

    def test_symlink_candidate_source_is_rejected(self):
        self.writer.write_checkpoint(
            self.state,
            {"baseline": self.baseline},
        )
        source = self.root / self.baseline.source_artifact
        target = self.root / "outside.cpp"
        target.write_bytes(BASELINE_SOURCE)
        source.unlink()
        try:
            source.symlink_to(target)
        except OSError:
            self.skipTest("symbolic links are unavailable")
        with self.assertRaises(RuntimeError):
            self.writer.load_latest()

    def test_no_temporary_files_remain_after_success(self):
        self.writer.write_checkpoint(
            self.state,
            {"baseline": self.baseline},
        )
        self.assertEqual(list(self.root.rglob("*.tmp")), [])

    def test_checkpoint_json_is_deterministically_formatted(self):
        snapshot = self.writer.write_checkpoint(
            self.state,
            {"baseline": self.baseline},
        )
        text = snapshot.checkpoint_path.read_text(encoding="utf-8")
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(
            text,
            json.dumps(
                json.loads(text),
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )


if __name__ == "__main__":
    unittest.main()
