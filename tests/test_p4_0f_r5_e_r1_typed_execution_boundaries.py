from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from autogen.agentchat.group import ContextVariables

from agrefactor.config import (
    EvaluationSplit,
    TaskSpec,
    TestSuiteSpec,
    default_target_profile,
)
from agrefactor.evaluation import (
    FeedbackRouteAction,
    ValidationFeedbackCoordinator,
    ValidationState,
)
from agrefactor.evidence import FeedbackOwner
from agrefactor.runtime import (
    BudgetLimits,
    BudgetManager,
    CsimStageInputs,
    CsimValidationStageHandler,
    RunContext,
    TraceRecorder,
)
from agrefactor.runtime.cosim_stage import (
    CosimStageInputs,
    CosimValidationStageHandler,
)
from flow.tools.typed_testbench_outcome import (
    build_typed_testbench_adapter,
    make_typed_outcome_identity,
    read_typed_testbench_outcome,
)
from flow.tools.vitis_csim import run_vitis_csim
from flow.tools.vitis_cosim import run_vitis_cosim


# Exact bytes from the R5-E v2 campaign package.  Hashes and authority run/archive
# identity are frozen in fixtures/r5_e_v2/PROVENANCE.json in the execution ZIP.
REFERENCE = '''extern "C" int reference_top(int x) {
    return x + 1;
}
'''
CANDIDATE_CSIM_FAULT = '''extern "C" int candidate_top(int x) {
    return x + 2;
}
'''
CANDIDATE_COSIM_FAULT = '''extern "C" int candidate_top(int x) {
#ifndef __SYNTHESIS__
    return x + 1;
#else
    return x + 2;
#endif
}
'''
TESTBENCH = '''#include <cstdlib>
extern "C" int reference_top(int);
extern "C" int candidate_top(int);

int main() {
    for (int x = -4; x <= 4; ++x) {
        if (reference_top(x) != candidate_top(x)) {
            return 1;
        }
    }
    return 0;
}
'''
RUNTIME_CONTRACT = {
    "schema_version": 1,
    "kind": "public_differential_self_check_v1",
    "candidate_mismatch_returncodes": [1],
}
HISTORICAL_RUN_ID = "p4_0f_r5_e_20260806T172137Z_60901"
HISTORICAL_ARCHIVE_SHA256 = (
    "5fb38e6cc01cfcab2a4237e424d692ff8c146caa00e1687981b3e8ca8138159d"
)
HISTORICAL_VITIS_FAILURE = (
    "ERROR: [SIM 211-100] 'csim_design' failed: nonzero return value.\n"
    "ERROR: [Common 17-39] 'csim_design' failed due to earlier errors.\n"
)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _resolution(_profile=None):
    return {
        "command": "fake-vitis-run --input_file vitis.tcl",
        "command_source": "fixture",
        "executable": "fake-vitis-run",
        "resolved_executable": "/fixture/vitis-run",
        "settings_path": None,
        "resolved_settings_path": None,
        "probe_source": "fixture",
        "profile_name": "vitis-2023.2-default",
        "effective_value_provenance": {},
    }


def _verification(_resolution, requested):
    return {
        "status": "matched",
        "requested": requested,
        "actual": requested,
        "returncode": 0,
        "stdout": "",
        "stderr": "",
    }


def _raw_payload(identity, *, returncode: int):
    return {
        "schema_version": 2,
        **identity,
        "status": "passed" if returncode == 0 else "failed",
        "testbench_returncode": returncode,
    }


def _preflight_authority(*, suite_id="public-main", candidate=CANDIDATE_CSIM_FAULT, testbench=TESTBENCH):
    return {
        "status": "passed",
        "authority": "staged_preflight_typed",
        "evidence_sha256": "a" * 64,
        "suite_id_sha256": _digest(suite_id),
        "candidate_sha256": _digest(candidate),
        "testbench_sha256": _digest(testbench),
        "reference_sha256": _digest(REFERENCE),
        "required_substages": [
            "candidate_compile",
            "candidate_interface_check",
            "link",
            "reference_compile",
            "reference_interface_check",
            "testbench_compile",
        ],
    }


def _variables(*, contract=RUNTIME_CONTRACT, preflight=None, candidate=CANDIDATE_CSIM_FAULT):
    return ContextVariables(
        data={
            "orig_code": REFERENCE,
            "curr_code": candidate,
            "testbench": TESTBENCH,
            "candidate_top_function": "candidate_top",
            "target_profile": default_target_profile(),
            "csim_suite_id": "public-main",
            "csim_runtime_contract": contract,
            "csim_preflight_authority": (
                _preflight_authority(candidate=candidate) if preflight is None else preflight
            ),
        }
    )


def _write_preflight_fixture(root: Path, *, candidate=CANDIDATE_CSIM_FAULT, good=True):
    preflight = root / "preflight"
    preflight.mkdir(parents=True, exist_ok=True)
    (preflight / "testbench.cpp").write_text(TESTBENCH, encoding="utf-8")
    (preflight / "orig_code.cpp").write_text(REFERENCE, encoding="utf-8")
    (preflight / "refactor_code.cpp").write_text(candidate, encoding="utf-8")
    required = [
        "testbench_compile",
        "reference_compile",
        "candidate_compile",
        "reference_interface_check",
        "candidate_interface_check",
        "link",
    ]
    payload = {
        "schema_version": 1,
        "reason_code": "passed" if good else "candidate_compile_failed",
        "failed_component": None if good else "candidate",
        "execution": {
            "status": "completed",
            "returncode": 0 if good else 1,
            "timeout": False,
        },
        "substeps": [
            {
                "substage": name,
                "status": "passed" if good else ("failed" if name == "candidate_compile" else "passed"),
                "returncode": 0 if good or name != "candidate_compile" else 1,
            }
            for name in required
        ],
    }
    (preflight / "testbench_preflight_invocation.json").write_text(
        json.dumps(payload) + "\n", encoding="utf-8"
    )


class TypedBoundaryContractTests(unittest.TestCase):
    def test_runtime_contract_is_explicit_public_and_strict(self):
        suite = TestSuiteSpec(
            suite_id="public-main",
            split=EvaluationSplit.PUBLIC,
            runtime_contract=RUNTIME_CONTRACT,
        )
        self.assertEqual(
            tuple(suite.runtime_contract["candidate_mismatch_returncodes"]),
            (1,),
        )
        self.assertNotIn("runtime_contract", TestSuiteSpec(suite_id="plain").to_dict())
        with self.assertRaises(ValueError):
            TestSuiteSpec(
                suite_id="hidden",
                split=EvaluationSplit.HIDDEN,
                runtime_contract=RUNTIME_CONTRACT,
            )
        with self.assertRaises(ValueError):
            TestSuiteSpec(
                suite_id="bad",
                runtime_contract={**RUNTIME_CONTRACT, "candidate_mismatch_returncodes": [0]},
            )

    def test_identity_binds_execution_phase_suite_candidate_and_testbench(self):
        first = make_typed_outcome_identity(
            phase="csim_prerequisite",
            suite_id="public-main",
            candidate_code=CANDIDATE_CSIM_FAULT,
            testbench_code=TESTBENCH,
        )
        second = make_typed_outcome_identity(
            phase="cosim",
            suite_id="public-main",
            candidate_code=CANDIDATE_CSIM_FAULT,
            testbench_code=TESTBENCH,
            execution_id=first["execution_id"],
        )
        self.assertEqual(first["execution_id"], second["execution_id"])
        self.assertNotEqual(first["phase"], second["phase"])
        self.assertEqual(first["candidate_sha256"], second["candidate_sha256"])
        other = make_typed_outcome_identity(
            phase="cosim",
            suite_id="other",
            candidate_code=CANDIDATE_CSIM_FAULT + "\n",
            testbench_code=TESTBENCH + "\n",
        )
        self.assertNotEqual(first["suite_id_sha256"], other["suite_id_sha256"])
        self.assertNotEqual(first["candidate_sha256"], other["candidate_sha256"])
        self.assertNotEqual(first["testbench_sha256"], other["testbench_sha256"])

    def test_reader_rejects_stale_generation_candidate_and_phase(self):
        expected = make_typed_outcome_identity(
            phase="cosim",
            suite_id="public-main",
            candidate_code=CANDIDATE_COSIM_FAULT,
            testbench_code=TESTBENCH,
        )
        mutations = (
            ("execution_id", "0" * 32),
            ("candidate_sha256", "f" * 64),
            ("phase", "csim_prerequisite"),
        )
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "outcome.json"
            for key, replacement in mutations:
                with self.subTest(key=key):
                    stale = dict(expected)
                    stale[key] = replacement
                    path.write_text(
                        json.dumps(_raw_payload(stale, returncode=1)),
                        encoding="utf-8",
                    )
                    self.assertIsNone(
                        read_typed_testbench_outcome(path, expected_identity=expected)
                    )

    def test_shared_wrapper_atomically_overwrites_raw_fact_without_owner(self):
        compiler = shutil.which("g++") or shutil.which("c++")
        if compiler is None:
            self.skipTest("C++ compiler unavailable")
        source = (
            "#include <cstdlib>\n"
            "int main() { return std::getenv(\"AGREFACTOR_R5_E_R1_FAIL\") ? 7 : 0; }\n"
        )
        identity = make_typed_outcome_identity(
            phase="cosim",
            suite_id="public-main",
            candidate_code=CANDIDATE_COSIM_FAULT,
            testbench_code=source,
        )
        instrumented, wrapper, adapter = build_typed_testbench_adapter(
            source,
            wrapped_main_name="agrefactor_test_main",
            base_identity=identity,
            allowed_phases=("cosim",),
        )
        self.assertTrue(adapter["records_only_raw_returncode"])
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "tb.cpp").write_text(instrumented, encoding="utf-8")
            (root / "wrapper.cpp").write_text(wrapper, encoding="utf-8")
            executable = root / "runner"
            subprocess.run(
                [compiler, "-std=c++17", "tb.cpp", "wrapper.cpp", "-o", str(executable)],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            outcome = root / "outcome.json"
            argv = [str(executable), str(outcome), identity["execution_id"], "cosim"]
            self.assertEqual(subprocess.run(argv, cwd=root, check=False).returncode, 0)
            env = dict(os.environ)
            env["AGREFACTOR_R5_E_R1_FAIL"] = "1"
            self.assertEqual(
                subprocess.run(argv, cwd=root, env=env, check=False).returncode,
                7,
            )
            payload = json.loads(outcome.read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["testbench_returncode"], 7)
        self.assertNotIn("failure_owner", payload)
        self.assertNotIn("failure_kind", payload)
        self.assertFalse(any(outcome.parent.glob("*.tmp")))


class NativeCsimDeterministicAuthorityTests(unittest.TestCase):
    def _run(self, *, code=1, contract=RUNTIME_CONTRACT, preflight=None, write_outcome=True):
        def run_cmd(work_dir, _command, _timelimit):
            root = Path(work_dir)
            if write_outcome:
                invocation = json.loads(
                    (root / "csim_invocation.json").read_text(encoding="utf-8")
                )
                identity = invocation["typed_outcome_identity"]
                (root / "agrefactor_csim_outcome.json").write_text(
                    json.dumps(_raw_payload(identity, returncode=code)) + "\n",
                    encoding="utf-8",
                )
            return {
                "returncode": code,
                "timeout": False,
                "stdout": "R5-E v2 public differential mismatch\n",
                "stderr": HISTORICAL_VITIS_FAILURE,
            }

        with tempfile.TemporaryDirectory() as raw:
            with (
                patch("flow.tools.vitis_csim.resolve_csynth_command", _resolution),
                patch("flow.tools.vitis_csim.probe_csynth_version", _verification),
                patch("flow.tools.general.run_cmd", side_effect=run_cmd),
            ):
                return run_vitis_csim(
                    raw,
                    _variables(contract=contract, preflight=(
                        _preflight_authority() if preflight is None else preflight
                    )),
                )

    def test_historical_v2_fixture_and_hash_provenance_become_candidate_only_with_all_authorities(self):
        self.assertEqual(HISTORICAL_RUN_ID, "p4_0f_r5_e_20260806T172137Z_60901")
        self.assertEqual(len(HISTORICAL_ARCHIVE_SHA256), 64)
        self.assertEqual(_digest(TESTBENCH), "2ad5d577e9e20ca1c3d74b569d69edc681605873d987dccbd2d97a8e9f35b774")
        self.assertEqual(_digest(REFERENCE), "7209bc46aec7c441a944020655920c3d57b5f5413340f2f37feb705843c26c56")
        self.assertEqual(_digest(CANDIDATE_CSIM_FAULT), "70c2d5e51f7a883354862e2c8b9aaf36173701cab12976fbf58e227c8c99f842")
        status, diagnostic = self._run()
        self.assertEqual(status, "csim_failed")
        self.assertIn("ERROR: [SIM 211-100]", diagnostic)

    def test_nonzero_without_explicit_suite_runtime_contract_is_unknown(self):
        status, _ = self._run(contract=None)
        self.assertEqual(status, "csim_execution_failed")

    def test_nonzero_without_matching_preflight_typed_identity_is_unknown(self):
        unavailable = {"status": "unavailable", "authority": "staged_preflight_typed"}
        status, _ = self._run(preflight=unavailable)
        self.assertEqual(status, "csim_execution_failed")
        wrong = _preflight_authority()
        wrong["candidate_sha256"] = "0" * 64
        status, _ = self._run(preflight=wrong)
        self.assertEqual(status, "csim_execution_failed")

    def test_testbench_setup_or_reference_returncodes_are_not_candidate(self):
        for code in (2, 3, 7):
            with self.subTest(code=code):
                status, _ = self._run(code=code)
                self.assertEqual(status, "csim_execution_failed")

    def test_deterministic_native_authority_flows_through_existing_router(self):
        task = TaskSpec(
            task_id="r5-e-r1-native-route",
            kernel_path="candidate.cpp",
            kernel_name="candidate_top",
            target=default_target_profile(),
            test_suites=(
                TestSuiteSpec(
                    suite_id="public-main",
                    split=EvaluationSplit.PUBLIC,
                    runtime_contract=RUNTIME_CONTRACT,
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as raw:
            attempt = Path(raw) / "attempt"
            _write_preflight_fixture(attempt, good=True)
            handler = CsimValidationStageHandler(
                CsimStageInputs(
                    work_dir=attempt / "csim",
                    original_code=REFERENCE,
                    candidate_code=CANDIDATE_CSIM_FAULT,
                    suite_testbench_codes={"public-main": TESTBENCH},
                    execution_backend="native_vitis",
                    candidate_top_function="candidate_top",
                    target_profile=task.target,
                ),
                split=EvaluationSplit.PUBLIC,
            )
            context = RunContext(
                run_id="r5-e-r1-native-route",
                task=task,
                budget=BudgetManager(BudgetLimits()),
                trace=TraceRecorder("r5-e-r1-native-route", task_id=task.task_id),
            )

            def run_cmd(work_dir, _command, _timelimit):
                root = Path(work_dir)
                identity = json.loads(
                    (root / "csim_invocation.json").read_text(encoding="utf-8")
                )["typed_outcome_identity"]
                (root / "agrefactor_csim_outcome.json").write_text(
                    json.dumps(_raw_payload(identity, returncode=1)),
                    encoding="utf-8",
                )
                return {
                    "returncode": 1,
                    "timeout": False,
                    "stdout": "mismatch\n",
                    "stderr": HISTORICAL_VITIS_FAILURE,
                }

            with (
                patch("flow.tools.vitis_csim.resolve_csynth_command", _resolution),
                patch("flow.tools.vitis_csim.probe_csynth_version", _verification),
                patch("flow.tools.general.run_cmd", side_effect=run_cmd),
            ):
                report = handler(context)
        self.assertEqual(report.items[0].owner, FeedbackOwner.CANDIDATE)
        self.assertEqual(
            report.items[0].metadata.get("owner_authority"),
            "deterministic_proven",
        )
        coordinated = ValidationFeedbackCoordinator(task).coordinate(
            report,
            ValidationState.PUBLIC_EVALUATION,
            coordination_id="r5-e-r1-native-route.coordinate",
        )
        self.assertEqual(coordinated.route_action, FeedbackRouteAction.REPAIR_CANDIDATE)
        self.assertEqual(coordinated.transition.next_state, ValidationState.REPAIR_PENDING)


class CosimStructuredGenerationTests(unittest.TestCase):
    def _run(self, mode: str):
        calls: list[int] = []
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)

            def run_cmd(_work_dir, _command, _timelimit):
                calls.append(len(calls) + 1)
                if len(calls) == 1:
                    (root / "toolchain_version.txt").write_text("2023.2\n", encoding="utf-8")
                    return {"returncode": 0, "timeout": False}
                invocation = json.loads(
                    (root / "cosim_invocation.json").read_text(encoding="utf-8")
                )
                identities = invocation["typed_outcome_identities"]
                (root / "cosim_command_status.json").write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "status": "failed",
                            "phase": "cosim",
                            "reason_code": "cosim_command_failed",
                        }
                    ) + "\n",
                    encoding="utf-8",
                )
                typed_path = root / "agrefactor_cosim_outcome.json"
                if mode == "terminal_failure_after_prepass":
                    typed_path.write_text(
                        json.dumps(_raw_payload(identities["csim_prerequisite"], returncode=0)),
                        encoding="utf-8",
                    )
                    typed_path.unlink()  # mirrors the explicit Tcl invalidation boundary
                    typed_path.write_text(
                        json.dumps(_raw_payload(identities["cosim"], returncode=1)),
                        encoding="utf-8",
                    )
                elif mode == "terminal_pass_conflict":
                    typed_path.write_text(
                        json.dumps(_raw_payload(identities["cosim"], returncode=0)),
                        encoding="utf-8",
                    )
                elif mode == "missing":
                    pass
                elif mode == "stale_phase":
                    typed_path.write_text(
                        json.dumps(_raw_payload(identities["csim_prerequisite"], returncode=1)),
                        encoding="utf-8",
                    )
                else:
                    raise AssertionError(mode)
                return {"returncode": 23, "timeout": False}

            with (
                patch("flow.tools.vitis_cosim.resolve_csynth_command", return_value=_resolution()),
                patch("flow.tools.vitis_cosim.tools.general.run_cmd", side_effect=run_cmd),
            ):
                outcome = run_vitis_cosim(
                    work_dir=root,
                    original_code=REFERENCE,
                    candidate_code=CANDIDATE_COSIM_FAULT,
                    testbench_code=TESTBENCH,
                    candidate_top_function="candidate_top",
                    target_profile=default_target_profile(),
                    timelimit=30,
                    budget=BudgetManager(BudgetLimits(max_tool_calls=2, max_cosim_calls=1)),
                    suite_id="public-main",
                    runtime_contract=RUNTIME_CONTRACT,
                )
                invocation = json.loads(
                    (root / "cosim_invocation.json").read_text(encoding="utf-8")
                )
            return outcome, invocation

    def test_historical_v2_stale_prepass_is_invalidated_and_cosim_phase_failure_is_deterministic(self):
        self.assertEqual(_digest(CANDIDATE_COSIM_FAULT), "6765370432e479543e9006783b52530929ba8b3de9d56df1add3d875f1a27039")
        outcome, invocation = self._run("terminal_failure_after_prepass")
        identities = invocation["typed_outcome_identities"]
        self.assertEqual(
            identities["csim_prerequisite"]["execution_id"],
            identities["cosim"]["execution_id"],
        )
        self.assertEqual(identities["cosim"]["phase"], "cosim")
        self.assertEqual(outcome["failure_owner"], "candidate")
        self.assertEqual(outcome["owner_authority"], "deterministic_proven")

    def test_outer_cosim_failure_with_pass_or_missing_typed_terminal_is_unknown(self):
        for mode in ("terminal_pass_conflict", "missing"):
            with self.subTest(mode=mode):
                outcome, _ = self._run(mode)
                self.assertEqual(outcome["failure_owner"], "unknown")
                self.assertEqual(outcome["failure_kind"], "ownership_unknown")

    def test_stale_phase_is_rejected_and_stage_rejects_unproven_candidate_owner(self):
        outcome, _ = self._run("stale_phase")
        self.assertEqual(outcome["failure_owner"], "unknown")
        task = TaskSpec(
            task_id="r5-e-r1-cosim-stage",
            kernel_path="candidate.cpp",
            kernel_name="candidate_top",
            target=default_target_profile(),
            test_suites=(
                TestSuiteSpec(
                    suite_id="public-main",
                    split=EvaluationSplit.PUBLIC,
                    runtime_contract=RUNTIME_CONTRACT,
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as raw:
            handler = CosimValidationStageHandler(
                CosimStageInputs(
                    work_dir=raw,
                    original_code=REFERENCE,
                    candidate_code=CANDIDATE_COSIM_FAULT,
                    suite_testbench_codes={"public-main": TESTBENCH},
                    candidate_top_function="candidate_top",
                    target_profile=task.target,
                    timelimit=30,
                ),
                executor=lambda **_: {
                    "status": "failed",
                    "failure_kind": "candidate_rtl_functional_failure",
                    "failure_owner": "candidate",
                    "reason_code": "unproven_candidate_claim",
                    "timed_out": False,
                    "returncode": 23,
                    "tool_launched": True,
                    "cosim_launched": True,
                    "evidence_sha256": "b" * 64,
                },
            )
            report = handler(
                RunContext(
                    run_id="r5-e-r1-cosim-stage",
                    task=task,
                    budget=BudgetManager(),
                    trace=TraceRecorder("r5-e-r1-cosim-stage", task_id=task.task_id),
                )
            )
        self.assertEqual(report.items[0].owner, FeedbackOwner.UNKNOWN)

        with tempfile.TemporaryDirectory() as raw:
            wrong_code = CosimValidationStageHandler(
                CosimStageInputs(
                    work_dir=raw,
                    original_code=REFERENCE,
                    candidate_code=CANDIDATE_COSIM_FAULT,
                    suite_testbench_codes={"public-main": TESTBENCH},
                    candidate_top_function="candidate_top",
                    target_profile=task.target,
                    timelimit=30,
                ),
                executor=lambda **_: {
                    "status": "failed",
                    "failure_kind": "candidate_rtl_functional_failure",
                    "failure_owner": "candidate",
                    "owner_authority": "deterministic_proven",
                    "testbench_returncode": 7,
                    "reason_code": "wrong_runtime_contract_code",
                    "timed_out": False,
                    "returncode": 23,
                    "tool_launched": True,
                    "cosim_launched": True,
                    "evidence_sha256": "c" * 64,
                },
            )(
                RunContext(
                    run_id="r5-e-r1-cosim-stage-wrong-code",
                    task=task,
                    budget=BudgetManager(),
                    trace=TraceRecorder(
                        "r5-e-r1-cosim-stage-wrong-code",
                        task_id=task.task_id,
                    ),
                )
            )
        self.assertEqual(wrong_code.items[0].owner, FeedbackOwner.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
