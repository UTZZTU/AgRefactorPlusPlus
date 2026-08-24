from __future__ import annotations

import shlex
import sys
import tempfile
import time
import unittest

from agrefactor.runtime.cosim_stage import _normalize_outcome
from flow.tools.general import run_cmd


V1 = {
    "schema_version": 1,
    "kind": "public_differential_self_check_v1",
    "candidate_mismatch_returncodes": [1],
}
V2 = {
    **V1,
    "schema_version": 2,
    "cosim_interface_depths": {"input": 64, "output": 64},
}


def _candidate_failure_payload(returncode: int = 1) -> dict[str, object]:
    return {
        "status": "failed",
        "failure_kind": "candidate_rtl_functional_failure",
        "failure_owner": "candidate",
        "owner_authority": "deterministic_proven",
        "testbench_returncode": returncode,
        "reason_code": "public_rtl_mismatch",
        "timed_out": False,
        "returncode": returncode,
        "tool_launched": True,
        "cosim_launched": True,
        "evidence_sha256": "a" * 64,
    }


class RunCmdCaptureTests(unittest.TestCase):
    def test_large_stdout_and_stderr_do_not_deadlock(self) -> None:
        size = 2 * 1024 * 1024
        program = (
            "import sys;"
            f"sys.stdout.write('O'*{size});sys.stdout.flush();"
            f"sys.stderr.write('E'*{size});sys.stderr.flush()"
        )
        command = f"{shlex.quote(sys.executable)} -c {shlex.quote(program)}"
        with tempfile.TemporaryDirectory() as raw:
            result = run_cmd(raw, command, 20)
        self.assertFalse(result["timeout"])
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(len(result["stdout"]), size)
        self.assertEqual(len(result["stderr"]), size)

    def test_timeout_is_bounded_and_preserves_captured_output(self) -> None:
        program = (
            "import sys,time;"
            "sys.stdout.write('started\\n');sys.stdout.flush();"
            "time.sleep(60)"
        )
        command = f"{shlex.quote(sys.executable)} -c {shlex.quote(program)}"
        started = time.monotonic()
        with tempfile.TemporaryDirectory() as raw:
            result = run_cmd(raw, command, 1)
        elapsed = time.monotonic() - started
        self.assertTrue(result["timeout"])
        self.assertIsNone(result["returncode"])
        self.assertIn("started", result["stdout"])
        self.assertLess(elapsed, 10)

    def test_nonzero_exit_preserves_returncode_and_stderr(self) -> None:
        program = "import sys;sys.stderr.write('failure\\n');sys.exit(7)"
        command = f"{shlex.quote(sys.executable)} -c {shlex.quote(program)}"
        with tempfile.TemporaryDirectory() as raw:
            result = run_cmd(raw, command, 10)
        self.assertFalse(result["timeout"])
        self.assertEqual(result["returncode"], 7)
        self.assertEqual(result["stderr"], "failure\n")


class RuntimeContractV2NormalizationTests(unittest.TestCase):
    def test_v1_candidate_failure_remains_authoritative(self) -> None:
        outcome = _normalize_outcome(
            _candidate_failure_payload(),
            runtime_contract=V1,
        )
        self.assertEqual(outcome["failure_kind"], "candidate_rtl_functional_failure")
        self.assertEqual(outcome["failure_owner"], "candidate")

    def test_v2_candidate_failure_is_authoritative(self) -> None:
        outcome = _normalize_outcome(
            _candidate_failure_payload(),
            runtime_contract=V2,
        )
        self.assertEqual(outcome["failure_kind"], "candidate_rtl_functional_failure")
        self.assertEqual(outcome["failure_owner"], "candidate")

    def test_v2_wrong_returncode_fails_closed(self) -> None:
        outcome = _normalize_outcome(
            _candidate_failure_payload(returncode=2),
            runtime_contract=V2,
        )
        self.assertEqual(outcome["failure_kind"], "ownership_unknown")
        self.assertEqual(outcome["failure_owner"], "unknown")

    def test_malformed_v2_depths_fail_closed(self) -> None:
        malformed_contracts = (
            {**V2, "cosim_interface_depths": {}},
            {**V2, "cosim_interface_depths": {"bad-port": 64}},
            {**V2, "cosim_interface_depths": {"input": 0}},
            {**V2, "extra": True},
        )
        for contract in malformed_contracts:
            with self.subTest(contract=contract):
                outcome = _normalize_outcome(
                    _candidate_failure_payload(),
                    runtime_contract=contract,
                )
                self.assertEqual(outcome["failure_kind"], "ownership_unknown")
                self.assertEqual(outcome["failure_owner"], "unknown")


if __name__ == "__main__":
    unittest.main()
