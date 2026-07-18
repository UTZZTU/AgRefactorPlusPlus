"""Manually authored Stage 2 smoke inputs and independent labels."""

from __future__ import annotations

from textwrap import dedent

from .stage2_matrix import (
    Stage2SmokeBudgetExpectation,
    Stage2SmokeCase,
    Stage2SmokeExpectedRoute,
    Stage2SmokeExpectedTerminalState,
    Stage2SmokeGroundTruth,
    Stage2SmokeGroundTruthOwner,
    Stage2SmokeGroundTruthStage,
    Stage2SmokeHiddenVisibility,
    Stage2SmokeKernelType,
    Stage2SmokeScenarioKind,
)


def _source(value: str) -> str:
    return dedent(value).strip() + "\n"


def _baseline_truth(
    case_id: str,
    kernel_type: Stage2SmokeKernelType,
) -> Stage2SmokeGroundTruth:
    return Stage2SmokeGroundTruth(
        case_id=case_id,
        kernel_type=kernel_type,
        scenario_kind=Stage2SmokeScenarioKind.BASELINE,
        injected_fault="none_baseline",
        ground_truth_owner=Stage2SmokeGroundTruthOwner.NONE,
        ground_truth_stage=Stage2SmokeGroundTruthStage.NONE,
        expected_route=Stage2SmokeExpectedRoute.ADVANCE,
        expected_terminal_state=(
            Stage2SmokeExpectedTerminalState.ACCEPTED
        ),
        hidden_visibility_expectation=(
            Stage2SmokeHiddenVisibility.OPERATOR_ONLY_NEVER_AGENT
        ),
    )


def _baseline_budget() -> Stage2SmokeBudgetExpectation:
    return Stage2SmokeBudgetExpectation(
        tool_calls=6,
        compile_calls=3,
        csynth_calls=1,
        csim_calls=2,
    )


ARRAY_MAP_SECRET = "HIDDEN_ARRAY_MAP_61F3"
REDUCTION_SECRET = "HIDDEN_REDUCTION_17C4"
STENCIL_SECRET = "HIDDEN_STENCIL_9A20"
MULTI_OUTPUT_SECRET = "HIDDEN_MULTI_OUTPUT_B781"
STRUCT_SECRET = "HIDDEN_STRUCT_44DE"
STREAM_SECRET = "HIDDEN_STREAM_D02A"
STATEFUL_SECRET = "HIDDEN_STATEFUL_8C13"


STAGE2_SMOKE_CASES = (
    Stage2SmokeCase(
        case_id="array-map",
        kernel_type=Stage2SmokeKernelType.ARRAY_MAP,
        task_id="stage2-smoke-array-map",
        kernel_name="candidate_top",
        original_code=_source(
            """
            extern "C" void original_top(
                const int input[8],
                int output[8]
            ) {
                for (int i = 0; i < 8; ++i) {
                    output[i] = input[i] * 3 + 1;
                }
            }
            """
        ),
        candidate_code=_source(
            """
            extern "C" void candidate_top(
                const int input[8],
                int output[8]
            ) {
                for (int i = 0; i < 8; ++i) {
                    output[i] = input[i] * 3 + 1;
                }
            }
            """
        ),
        preflight_testbench_code=_source(
            """
            extern "C" void original_top(
                const int input[8],
                int output[8]
            );
            extern "C" void candidate_top(
                const int input[8],
                int output[8]
            );

            int main() {
                const int input[8] = {0, 1, -2, 7, 9, -5, 11, 3};
                int original[8] = {};
                int candidate[8] = {};
                original_top(input, original);
                candidate_top(input, candidate);
                for (int i = 0; i < 8; ++i) {
                    if (original[i] != candidate[i]) {
                        return 1;
                    }
                }
                return 0;
            }
            """
        ),
        public_testbench_code=_source(
            """
            extern "C" void original_top(
                const int input[8],
                int output[8]
            );
            extern "C" void candidate_top(
                const int input[8],
                int output[8]
            );

            int main() {
                const int input[8] = {2, 4, 6, 8, -1, -3, 13, 21};
                int original[8] = {};
                int candidate[8] = {};
                original_top(input, original);
                candidate_top(input, candidate);
                for (int i = 0; i < 8; ++i) {
                    if (original[i] != candidate[i]) {
                        return 1;
                    }
                }
                return 0;
            }
            """
        ),
        hidden_testbench_code=_source(
            f"""
            #include <cstdio>
            extern "C" void original_top(
                const int input[8],
                int output[8]
            );
            extern "C" void candidate_top(
                const int input[8],
                int output[8]
            );

            int main() {{
                const int input[8] = {{31, -17, 0, 5, 99, -40, 8, 12}};
                int original[8] = {{}};
                int candidate[8] = {{}};
                original_top(input, original);
                candidate_top(input, candidate);
                for (int i = 0; i < 8; ++i) {{
                    if (original[i] != candidate[i]) {{
                        std::fprintf(stderr, "{ARRAY_MAP_SECRET}\\n");
                        return 1;
                    }}
                }}
                return 0;
            }}
            """
        ),
        hidden_secret_marker=ARRAY_MAP_SECRET,
        ground_truth=_baseline_truth(
            "array-map",
            Stage2SmokeKernelType.ARRAY_MAP,
        ),
        expected_budget=_baseline_budget(),
        tags=("array", "map", "fixed-bound"),
    ),
    Stage2SmokeCase(
        case_id="reduction",
        kernel_type=Stage2SmokeKernelType.REDUCTION,
        task_id="stage2-smoke-reduction",
        kernel_name="candidate_top",
        original_code=_source(
            """
            extern "C" int original_top(const int input[16]) {
                int sum = 0;
                for (int i = 0; i < 16; ++i) {
                    sum += input[i];
                }
                return sum;
            }
            """
        ),
        candidate_code=_source(
            """
            extern "C" int candidate_top(const int input[16]) {
                int sum = 0;
                for (int i = 0; i < 16; ++i) {
                    sum += input[i];
                }
                return sum;
            }
            """
        ),
        preflight_testbench_code=_source(
            """
            extern "C" int original_top(const int input[16]);
            extern "C" int candidate_top(const int input[16]);

            int main() {
                const int input[16] = {
                    1, 2, 3, 4, -5, 6, 7, 8,
                    9, 10, -11, 12, 13, 14, 15, 16
                };
                return original_top(input) == candidate_top(input)
                    ? 0
                    : 1;
            }
            """
        ),
        public_testbench_code=_source(
            """
            extern "C" int original_top(const int input[16]);
            extern "C" int candidate_top(const int input[16]);

            int main() {
                const int input[16] = {
                    0, 0, 0, 0, 8, 7, 6, 5,
                    4, 3, 2, 1, -4, -3, -2, -1
                };
                return original_top(input) == candidate_top(input)
                    ? 0
                    : 1;
            }
            """
        ),
        hidden_testbench_code=_source(
            f"""
            #include <cstdio>
            extern "C" int original_top(const int input[16]);
            extern "C" int candidate_top(const int input[16]);

            int main() {{
                const int input[16] = {{
                    100, -100, 31, -7, 19, 23, -41, 5,
                    17, -13, 29, -3, 11, -2, 37, -43
                }};
                if (original_top(input) != candidate_top(input)) {{
                    std::fprintf(stderr, "{REDUCTION_SECRET}\\n");
                    return 1;
                }}
                return 0;
            }}
            """
        ),
        hidden_secret_marker=REDUCTION_SECRET,
        ground_truth=_baseline_truth(
            "reduction",
            Stage2SmokeKernelType.REDUCTION,
        ),
        expected_budget=_baseline_budget(),
        tags=("reduction", "scalar-return", "fixed-bound"),
    ),
    Stage2SmokeCase(
        case_id="nested-stencil",
        kernel_type=Stage2SmokeKernelType.NESTED_STENCIL,
        task_id="stage2-smoke-nested-stencil",
        kernel_name="candidate_top",
        original_code=_source(
            """
            extern "C" void original_top(
                const int input[16],
                int output[9]
            ) {
                for (int row = 0; row < 3; ++row) {
                    for (int col = 0; col < 3; ++col) {
                        const int base = row * 4 + col;
                        output[row * 3 + col] =
                            input[base]
                            + input[base + 1]
                            + input[base + 4]
                            + input[base + 5];
                    }
                }
            }
            """
        ),
        candidate_code=_source(
            """
            extern "C" void candidate_top(
                const int input[16],
                int output[9]
            ) {
                for (int row = 0; row < 3; ++row) {
                    for (int col = 0; col < 3; ++col) {
                        const int base = row * 4 + col;
                        output[row * 3 + col] =
                            input[base]
                            + input[base + 1]
                            + input[base + 4]
                            + input[base + 5];
                    }
                }
            }
            """
        ),
        preflight_testbench_code=_source(
            """
            extern "C" void original_top(
                const int input[16],
                int output[9]
            );
            extern "C" void candidate_top(
                const int input[16],
                int output[9]
            );

            int main() {
                const int input[16] = {
                    0, 1, 2, 3,
                    4, 5, 6, 7,
                    8, 9, 10, 11,
                    12, 13, 14, 15
                };
                int original[9] = {};
                int candidate[9] = {};
                original_top(input, original);
                candidate_top(input, candidate);
                for (int i = 0; i < 9; ++i) {
                    if (original[i] != candidate[i]) {
                        return 1;
                    }
                }
                return 0;
            }
            """
        ),
        public_testbench_code=_source(
            """
            extern "C" void original_top(
                const int input[16],
                int output[9]
            );
            extern "C" void candidate_top(
                const int input[16],
                int output[9]
            );

            int main() {
                const int input[16] = {
                    4, -1, 8, 3,
                    7, 2, -5, 9,
                    11, 0, 6, -4,
                    1, 13, 5, 12
                };
                int original[9] = {};
                int candidate[9] = {};
                original_top(input, original);
                candidate_top(input, candidate);
                for (int i = 0; i < 9; ++i) {
                    if (original[i] != candidate[i]) {
                        return 1;
                    }
                }
                return 0;
            }
            """
        ),
        hidden_testbench_code=_source(
            f"""
            #include <cstdio>
            extern "C" void original_top(
                const int input[16],
                int output[9]
            );
            extern "C" void candidate_top(
                const int input[16],
                int output[9]
            );

            int main() {{
                const int input[16] = {{
                    31, -7, 19, 2,
                    -11, 5, 17, 23,
                    29, -3, 13, 41,
                    -37, 43, 47, -53
                }};
                int original[9] = {{}};
                int candidate[9] = {{}};
                original_top(input, original);
                candidate_top(input, candidate);
                for (int i = 0; i < 9; ++i) {{
                    if (original[i] != candidate[i]) {{
                        std::fprintf(stderr, "{STENCIL_SECRET}\\n");
                        return 1;
                    }}
                }}
                return 0;
            }}
            """
        ),
        hidden_secret_marker=STENCIL_SECRET,
        ground_truth=_baseline_truth(
            "nested-stencil",
            Stage2SmokeKernelType.NESTED_STENCIL,
        ),
        expected_budget=_baseline_budget(),
        tags=("nested-loop", "stencil", "array"),
    ),
    Stage2SmokeCase(
        case_id="multi-output",
        kernel_type=Stage2SmokeKernelType.MULTI_OUTPUT,
        task_id="stage2-smoke-multi-output",
        kernel_name="candidate_top",
        original_code=_source(
            """
            extern "C" void original_top(
                const int input[8],
                int doubled[8],
                int parity[8]
            ) {
                for (int i = 0; i < 8; ++i) {
                    doubled[i] = input[i] * 2;
                    parity[i] = input[i] & 1;
                }
            }
            """
        ),
        candidate_code=_source(
            """
            extern "C" void candidate_top(
                const int input[8],
                int doubled[8],
                int parity[8]
            ) {
                for (int i = 0; i < 8; ++i) {
                    doubled[i] = input[i] * 2;
                    parity[i] = input[i] & 1;
                }
            }
            """
        ),
        preflight_testbench_code=_source(
            """
            extern "C" void original_top(
                const int input[8],
                int doubled[8],
                int parity[8]
            );
            extern "C" void candidate_top(
                const int input[8],
                int doubled[8],
                int parity[8]
            );

            int main() {
                const int input[8] = {0, 1, 2, 3, -4, -5, 8, 9};
                int original_a[8] = {};
                int original_b[8] = {};
                int candidate_a[8] = {};
                int candidate_b[8] = {};
                original_top(input, original_a, original_b);
                candidate_top(input, candidate_a, candidate_b);
                for (int i = 0; i < 8; ++i) {
                    if (
                        original_a[i] != candidate_a[i]
                        || original_b[i] != candidate_b[i]
                    ) {
                        return 1;
                    }
                }
                return 0;
            }
            """
        ),
        public_testbench_code=_source(
            """
            extern "C" void original_top(
                const int input[8],
                int doubled[8],
                int parity[8]
            );
            extern "C" void candidate_top(
                const int input[8],
                int doubled[8],
                int parity[8]
            );

            int main() {
                const int input[8] = {11, 12, 13, 14, 15, 16, -7, -8};
                int original_a[8] = {};
                int original_b[8] = {};
                int candidate_a[8] = {};
                int candidate_b[8] = {};
                original_top(input, original_a, original_b);
                candidate_top(input, candidate_a, candidate_b);
                for (int i = 0; i < 8; ++i) {
                    if (
                        original_a[i] != candidate_a[i]
                        || original_b[i] != candidate_b[i]
                    ) {
                        return 1;
                    }
                }
                return 0;
            }
            """
        ),
        hidden_testbench_code=_source(
            f"""
            #include <cstdio>
            extern "C" void original_top(
                const int input[8],
                int doubled[8],
                int parity[8]
            );
            extern "C" void candidate_top(
                const int input[8],
                int doubled[8],
                int parity[8]
            );

            int main() {{
                const int input[8] = {{31, -17, 19, -23, 29, -31, 37, -41}};
                int original_a[8] = {{}};
                int original_b[8] = {{}};
                int candidate_a[8] = {{}};
                int candidate_b[8] = {{}};
                original_top(input, original_a, original_b);
                candidate_top(input, candidate_a, candidate_b);
                for (int i = 0; i < 8; ++i) {{
                    if (
                        original_a[i] != candidate_a[i]
                        || original_b[i] != candidate_b[i]
                    ) {{
                        std::fprintf(stderr, "{MULTI_OUTPUT_SECRET}\\n");
                        return 1;
                    }}
                }}
                return 0;
            }}
            """
        ),
        hidden_secret_marker=MULTI_OUTPUT_SECRET,
        ground_truth=_baseline_truth(
            "multi-output",
            Stage2SmokeKernelType.MULTI_OUTPUT,
        ),
        expected_budget=_baseline_budget(),
        tags=("multi-output", "array", "fixed-bound"),
    ),
    Stage2SmokeCase(
        case_id="struct-record",
        kernel_type=Stage2SmokeKernelType.STRUCT_RECORD,
        task_id="stage2-smoke-struct-record",
        kernel_name="candidate_top",
        original_code=_source(
            """
            struct PairRecord {
                int x;
                int y;
            };

            extern "C" void original_top(
                const PairRecord input[4],
                PairRecord output[4]
            ) {
                for (int i = 0; i < 4; ++i) {
                    output[i].x = input[i].x + input[i].y;
                    output[i].y = input[i].x - input[i].y;
                }
            }
            """
        ),
        candidate_code=_source(
            """
            struct PairRecord {
                int x;
                int y;
            };

            extern "C" void candidate_top(
                const PairRecord input[4],
                PairRecord output[4]
            ) {
                for (int i = 0; i < 4; ++i) {
                    output[i].x = input[i].x + input[i].y;
                    output[i].y = input[i].x - input[i].y;
                }
            }
            """
        ),
        preflight_testbench_code=_source(
            """
            struct PairRecord {
                int x;
                int y;
            };

            extern "C" void original_top(
                const PairRecord input[4],
                PairRecord output[4]
            );
            extern "C" void candidate_top(
                const PairRecord input[4],
                PairRecord output[4]
            );

            int main() {
                const PairRecord input[4] = {
                    {1, 2}, {-3, 7}, {0, 0}, {11, -5}
                };
                PairRecord original[4] = {};
                PairRecord candidate[4] = {};
                original_top(input, original);
                candidate_top(input, candidate);
                for (int i = 0; i < 4; ++i) {
                    if (
                        original[i].x != candidate[i].x
                        || original[i].y != candidate[i].y
                    ) {
                        return 1;
                    }
                }
                return 0;
            }
            """
        ),
        public_testbench_code=_source(
            """
            struct PairRecord {
                int x;
                int y;
            };

            extern "C" void original_top(
                const PairRecord input[4],
                PairRecord output[4]
            );
            extern "C" void candidate_top(
                const PairRecord input[4],
                PairRecord output[4]
            );

            int main() {
                const PairRecord input[4] = {
                    {13, 8}, {-9, -4}, {21, 1}, {-2, 17}
                };
                PairRecord original[4] = {};
                PairRecord candidate[4] = {};
                original_top(input, original);
                candidate_top(input, candidate);
                for (int i = 0; i < 4; ++i) {
                    if (
                        original[i].x != candidate[i].x
                        || original[i].y != candidate[i].y
                    ) {
                        return 1;
                    }
                }
                return 0;
            }
            """
        ),
        hidden_testbench_code=_source(
            f"""
            #include <cstdio>
            struct PairRecord {{
                int x;
                int y;
            }};

            extern "C" void original_top(
                const PairRecord input[4],
                PairRecord output[4]
            );
            extern "C" void candidate_top(
                const PairRecord input[4],
                PairRecord output[4]
            );

            int main() {{
                const PairRecord input[4] = {{
                    {{31, -7}}, {{19, 23}}, {{-41, 5}}, {{17, -13}}
                }};
                PairRecord original[4] = {{}};
                PairRecord candidate[4] = {{}};
                original_top(input, original);
                candidate_top(input, candidate);
                for (int i = 0; i < 4; ++i) {{
                    if (
                        original[i].x != candidate[i].x
                        || original[i].y != candidate[i].y
                    ) {{
                        std::fprintf(stderr, "{STRUCT_SECRET}\\n");
                        return 1;
                    }}
                }}
                return 0;
            }}
            """
        ),
        hidden_secret_marker=STRUCT_SECRET,
        ground_truth=_baseline_truth(
            "struct-record",
            Stage2SmokeKernelType.STRUCT_RECORD,
        ),
        expected_budget=_baseline_budget(),
        tags=("struct", "record", "multi-field"),
    ),
    Stage2SmokeCase(
        case_id="hls-stream",
        kernel_type=Stage2SmokeKernelType.HLS_STREAM,
        task_id="stage2-smoke-hls-stream",
        kernel_name="candidate_top",
        original_code=_source(
            """
            #include <hls_stream.h>

            extern "C" void original_top(
                hls::stream<int>& input,
                hls::stream<int>& output
            ) {
                for (int i = 0; i < 8; ++i) {
                    output.write(input.read() * 2 + 1);
                }
            }
            """
        ),
        candidate_code=_source(
            """
            #include <hls_stream.h>

            extern "C" void candidate_top(
                hls::stream<int>& input,
                hls::stream<int>& output
            ) {
                for (int i = 0; i < 8; ++i) {
                    output.write(input.read() * 2 + 1);
                }
            }
            """
        ),
        preflight_testbench_code=_source(
            """
            #include <hls_stream.h>

            extern "C" void original_top(
                hls::stream<int>& input,
                hls::stream<int>& output
            );
            extern "C" void candidate_top(
                hls::stream<int>& input,
                hls::stream<int>& output
            );

            int main() {
                const int values[8] = {0, 1, -2, 7, 9, -5, 11, 3};
                hls::stream<int> original_input;
                hls::stream<int> candidate_input;
                hls::stream<int> original_output;
                hls::stream<int> candidate_output;
                for (int i = 0; i < 8; ++i) {
                    original_input.write(values[i]);
                    candidate_input.write(values[i]);
                }
                original_top(original_input, original_output);
                candidate_top(candidate_input, candidate_output);
                for (int i = 0; i < 8; ++i) {
                    if (
                        original_output.read()
                        != candidate_output.read()
                    ) {
                        return 1;
                    }
                }
                return 0;
            }
            """
        ),
        public_testbench_code=_source(
            """
            #include <hls_stream.h>

            extern "C" void original_top(
                hls::stream<int>& input,
                hls::stream<int>& output
            );
            extern "C" void candidate_top(
                hls::stream<int>& input,
                hls::stream<int>& output
            );

            int main() {
                const int values[8] = {2, 4, 6, 8, -1, -3, 13, 21};
                hls::stream<int> original_input;
                hls::stream<int> candidate_input;
                hls::stream<int> original_output;
                hls::stream<int> candidate_output;
                for (int i = 0; i < 8; ++i) {
                    original_input.write(values[i]);
                    candidate_input.write(values[i]);
                }
                original_top(original_input, original_output);
                candidate_top(candidate_input, candidate_output);
                for (int i = 0; i < 8; ++i) {
                    if (
                        original_output.read()
                        != candidate_output.read()
                    ) {
                        return 1;
                    }
                }
                return 0;
            }
            """
        ),
        hidden_testbench_code=_source(
            f"""
            #include <cstdio>
            #include <hls_stream.h>

            extern "C" void original_top(
                hls::stream<int>& input,
                hls::stream<int>& output
            );
            extern "C" void candidate_top(
                hls::stream<int>& input,
                hls::stream<int>& output
            );

            int main() {{
                const int values[8] = {{31, -17, 19, -23, 29, -31, 37, -41}};
                hls::stream<int> original_input;
                hls::stream<int> candidate_input;
                hls::stream<int> original_output;
                hls::stream<int> candidate_output;
                for (int i = 0; i < 8; ++i) {{
                    original_input.write(values[i]);
                    candidate_input.write(values[i]);
                }}
                original_top(original_input, original_output);
                candidate_top(candidate_input, candidate_output);
                for (int i = 0; i < 8; ++i) {{
                    if (
                        original_output.read()
                        != candidate_output.read()
                    ) {{
                        std::fprintf(stderr, "{STREAM_SECRET}\\n");
                        return 1;
                    }}
                }}
                return 0;
            }}
            """
        ),
        hidden_secret_marker=STREAM_SECRET,
        ground_truth=_baseline_truth(
            "hls-stream",
            Stage2SmokeKernelType.HLS_STREAM,
        ),
        expected_budget=_baseline_budget(),
        tags=("stream", "hls-stream", "fixed-count"),
    ),
    Stage2SmokeCase(
        case_id="stateful",
        kernel_type=Stage2SmokeKernelType.STATEFUL,
        task_id="stage2-smoke-stateful",
        kernel_name="candidate_top",
        original_code=_source(
            """
            extern "C" int original_top(int value, bool reset) {
                static int accumulator = 0;
                if (reset) {
                    accumulator = 0;
                }
                accumulator += value;
                return accumulator;
            }
            """
        ),
        candidate_code=_source(
            """
            extern "C" int candidate_top(int value, bool reset) {
                static int accumulator = 0;
                if (reset) {
                    accumulator = 0;
                }
                accumulator += value;
                return accumulator;
            }
            """
        ),
        preflight_testbench_code=_source(
            """
            extern "C" int original_top(int value, bool reset);
            extern "C" int candidate_top(int value, bool reset);

            int main() {
                const int values[5] = {3, -1, 7, 0, 5};
                for (int i = 0; i < 5; ++i) {
                    const bool reset = i == 0;
                    if (
                        original_top(values[i], reset)
                        != candidate_top(values[i], reset)
                    ) {
                        return 1;
                    }
                }
                return 0;
            }
            """
        ),
        public_testbench_code=_source(
            """
            extern "C" int original_top(int value, bool reset);
            extern "C" int candidate_top(int value, bool reset);

            int main() {
                const int values[6] = {11, 2, -4, 9, -3, 1};
                for (int i = 0; i < 6; ++i) {
                    const bool reset = i == 0;
                    if (
                        original_top(values[i], reset)
                        != candidate_top(values[i], reset)
                    ) {
                        return 1;
                    }
                }
                return 0;
            }
            """
        ),
        hidden_testbench_code=_source(
            f"""
            #include <cstdio>
            extern "C" int original_top(int value, bool reset);
            extern "C" int candidate_top(int value, bool reset);

            int main() {{
                const int values[7] = {{31, -7, 19, 23, -41, 5, 17}};
                for (int i = 0; i < 7; ++i) {{
                    const bool reset = i == 0;
                    if (
                        original_top(values[i], reset)
                        != candidate_top(values[i], reset)
                    ) {{
                        std::fprintf(stderr, "{STATEFUL_SECRET}\\n");
                        return 1;
                    }}
                }}
                return 0;
            }}
            """
        ),
        hidden_secret_marker=STATEFUL_SECRET,
        ground_truth=_baseline_truth(
            "stateful",
            Stage2SmokeKernelType.STATEFUL,
        ),
        expected_budget=_baseline_budget(),
        tags=("stateful", "static-local", "sequence"),
    ),
)
