import json
import tempfile
import unittest
from pathlib import Path

from agrefactor.evaluation import (
    TestbenchPreflight,
    classify_compile_failure,
)
from agrefactor.evidence import (
    TestbenchFailureKind,
    TestbenchFailureOwner,
    TestbenchPreflightStatus,
)

ORIGINAL = r'''
struct btnode { int value; btnode *left; btnode *right; };
typedef struct btnode node;
node *root = nullptr;
int front = 0;
int rear = -1;
bool g_fallback = false;
extern "C" void process_top(int n, int *in, int *out, int *fallback) {
    for (int i = 0; i < n; ++i) out[i] = in[i];
    *fallback = 0;
}
'''

CANDIDATE = r'''
extern "C" void process_top_hls(int n, int *in, int *out, int *fallback) {
    for (int i = 0; i < n; ++i) out[i] = in[i];
    *fallback = 0;
}
'''

BROKEN_TB = r'''
extern "C" void process_top(int, int *, int *, int *);
extern "C" void process_top_hls(int, int *, int *, int *);
extern node *root;
extern int front;
extern int rear;
extern bool g_fallback;
int main() {
    root = nullptr;
    front = 0;
    rear = -1;
    g_fallback = false;
    int in[2] = {2, 1};
    int a[2] = {};
    int b[2] = {};
    int fa = 0;
    int fb = 0;
    process_top(2, in, a, &fa);
    process_top_hls(2, in, b, &fb);
    return 0;
}
'''

VALID_TB = r'''
#include <cstring>
extern "C" void process_top(int, int *, int *, int *);
extern "C" void process_top_hls(int, int *, int *, int *);
int main() {
    int in[2] = {2, 1};
    int a[2] = {};
    int b[2] = {};
    int fa = 0;
    int fb = 0;
    process_top(2, in, a, &fa);
    process_top_hls(2, in, b, &fb);
    return fa != fb || std::memcmp(a, b, sizeof(a)) != 0;
}
'''


class TestbenchPreflightTests(unittest.TestCase):
    def test_classifies_real_node_error(self) -> None:
        error = "testbench.cpp:16:8: error: ‘node’ does not name a type"
        self.assertEqual(
            classify_compile_failure(error),
            TestbenchFailureKind.UNDECLARED_TYPE,
        )

    def test_broken_testbench_returns_real_compiler_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = TestbenchPreflight().compile_and_link(
                work_dir=directory,
                testbench_code=BROKEN_TB,
                original_code=ORIGINAL,
                candidate_code=CANDIDATE,
            )
            invocation = json.loads(
                (
                    Path(directory) / "testbench_preflight_invocation.json"
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(result.status, TestbenchPreflightStatus.FAILED)
        self.assertEqual(
            result.failure_kind,
            TestbenchFailureKind.UNDECLARED_TYPE,
        )
        self.assertEqual(result.stage.value, "compile_link")
        self.assertEqual(
            result.failure_owner,
            TestbenchFailureOwner.TESTBENCH,
        )
        self.assertEqual(result.next_action, "repair_testbench")
        self.assertEqual(invocation["execution"]["status"], "completed")
        self.assertTrue(invocation["command"])
        self.assertEqual(result.diagnostics[0].file, "testbench.cpp")

    def test_private_dependency_guess_does_not_skip_compiler(
        self,
    ) -> None:
        original = "long hidden_accumulator = 0;\n" + ORIGINAL
        testbench = VALID_TB.replace(
            "int main() {",
            (
                "extern long hidden_accumulator;\n"
                "int main() {\n"
                "    hidden_accumulator = 0;"
            ),
        )

        with tempfile.TemporaryDirectory() as directory:
            result = TestbenchPreflight(
                compiler="compiler-that-does-not-exist"
            ).compile_and_link(
                work_dir=directory,
                testbench_code=testbench,
                original_code=original,
                candidate_code=CANDIDATE,
            )
            invocation = json.loads(
                (
                    Path(directory) / "testbench_preflight_invocation.json"
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(
            result.failure_kind,
            TestbenchFailureKind.COMPILER_NOT_FOUND,
        )
        self.assertEqual(
            result.failure_owner,
            TestbenchFailureOwner.TOOLCHAIN,
        )
        self.assertEqual(result.stage.value, "compile_link")
        self.assertEqual(result.next_action, "inspect_toolchain")
        self.assertEqual(invocation["execution"]["status"], "launch_error")

    def test_public_interface_only_testbench_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = TestbenchPreflight().compile_and_link(
                work_dir=directory,
                testbench_code=VALID_TB,
                original_code=ORIGINAL,
                candidate_code=CANDIDATE,
            )
            self.assertTrue(
                (Path(directory) / "testbench_preflight").is_file()
            )
        self.assertTrue(result.succeeded)
        self.assertEqual(
            result.failure_owner,
            TestbenchFailureOwner.NONE,
        )
        self.assertEqual(result.next_action, "continue_validation")


    def test_cpp_definition_with_c_declaration_is_testbench_owned(
        self,
    ) -> None:
        original = ORIGINAL.replace('extern "C" ', '')
        candidate = CANDIDATE.replace('extern "C" ', '')

        with tempfile.TemporaryDirectory() as directory:
            result = TestbenchPreflight().compile_and_link(
                work_dir=directory,
                testbench_code=VALID_TB,
                original_code=original,
                candidate_code=candidate,
            )

        self.assertEqual(
            result.failure_kind,
            TestbenchFailureKind.LINKAGE_MISMATCH,
        )
        self.assertEqual(
            result.failure_owner,
            TestbenchFailureOwner.TESTBENCH,
        )
        self.assertEqual(result.next_action, "repair_testbench")
        self.assertIn(
            "process_top",
            result.diagnostics[0].message,
        )

    def test_true_missing_definition_remains_unknown(self) -> None:
        missing_candidate = CANDIDATE.replace(
            "process_top_hls",
            "different_name",
        )

        with tempfile.TemporaryDirectory() as directory:
            result = TestbenchPreflight().compile_and_link(
                work_dir=directory,
                testbench_code=VALID_TB,
                original_code=ORIGINAL,
                candidate_code=missing_candidate,
            )

        self.assertEqual(
            result.failure_kind,
            TestbenchFailureKind.LINK_ERROR,
        )
        self.assertEqual(
            result.failure_owner,
            TestbenchFailureOwner.UNKNOWN,
        )

    def test_candidate_compile_error_is_not_owned_by_testbench(self) -> None:
        bad_candidate = CANDIDATE.replace(
            'for (int i = 0; i < n; ++i) out[i] = in[i];',
            'this is not valid C++;',
        )
        with tempfile.TemporaryDirectory() as directory:
            result = TestbenchPreflight().compile_and_link(
                work_dir=directory,
                testbench_code=VALID_TB,
                original_code=ORIGINAL,
                candidate_code=bad_candidate,
            )

        self.assertEqual(
            result.failure_owner,
            TestbenchFailureOwner.CANDIDATE,
        )
        self.assertEqual(result.next_action, "repair_candidate")

    def test_evidence_serializes_real_tool_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = TestbenchPreflight().compile_and_link(
                work_dir=directory,
                testbench_code=BROKEN_TB,
                original_code=ORIGINAL,
                candidate_code=CANDIDATE,
            )

        payload = result.to_dict()
        self.assertEqual(payload["stage"], "compile_link")
        self.assertEqual(payload["failure_kind"], "undeclared_type")
        self.assertEqual(payload["failure_owner"], "testbench")
        self.assertEqual(payload["next_action"], "repair_testbench")

    def test_missing_compiler_is_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = TestbenchPreflight(
                compiler="compiler-that-does-not-exist"
            ).compile_and_link(
                work_dir=directory,
                testbench_code=VALID_TB,
                original_code=ORIGINAL,
                candidate_code=CANDIDATE,
            )
        self.assertEqual(result.status, TestbenchPreflightStatus.ERROR)
        self.assertEqual(
            result.failure_kind,
            TestbenchFailureKind.COMPILER_NOT_FOUND,
        )
        self.assertEqual(
            result.failure_owner,
            TestbenchFailureOwner.TOOLCHAIN,
        )
        self.assertEqual(result.next_action, "inspect_toolchain")


if __name__ == "__main__":
    unittest.main()
