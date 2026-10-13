#!/usr/bin/env bash
set -u
source /data/agrefactorpp_env.sh || exit 90
cd /data/AgRefactor || exit 91

base=/data/agrefactor_runs/rerun_after_parser_fix_20261001
mkdir -p "$base"
batch="${1:-0}"
case_index=0
status_file="$base/batch_status_$batch.tsv"
printf 'case\texit_code\tresult_dir\n' > "$status_file"

run_case() {
    local name="$1"
    local source="$2"
    local top="$3"
    shift 3
    local assigned=$((case_index % 2))
    case_index=$((case_index + 1))
    if [ "$assigned" -ne "$batch" ]; then return 0; fi
    local result_dir="$base/$name"

    printf '\n===== START %s =====\n' "$name"
    source /data/agrefactorpp_env.sh || return 90
    python -m agrefactor.cli refactor "$source" \
        --top "$top" \
        "$@" \
        --public-tests auto \
        --hidden-tests auto \
        --test-generation-profile lightweight \
        --max-testbench-repairs 3 \
        --max-candidate-repairs 3 \
        --cosim-policy required \
        --json \
        --output-dir "$result_dir" > "$base/$name.log" 2>&1
    local rc=$?
    printf '%s\t%s\t%s\n' "$name" "$rc" "$result_dir" >> "$status_file"
    printf '===== END %s rc=%s =====\n' "$name" "$rc"
    return 0
}

run_case c2hlsc_sha256 /data/AgRefactor/src/c2hlsc/sha256/kernel.cpp sha256_update \
    --source-root /data/AgRefactor/src/c2hlsc/sha256
run_case hlsrewritter_boolean_e2_fft /data/AgRefactor/src/hlsrewritter/Boolean_E2_FFT/kernel.cpp fft_processing \
    --source-root /data/AgRefactor/src/hlsrewritter/Boolean_E2_FFT
run_case hlsrewritter_da_e1_linear_program /data/AgRefactor/src/hlsrewritter/DA_E1_linear_program/kernel.cpp solve \
    --source-root /data/AgRefactor/src/hlsrewritter/DA_E1_linear_program
run_case hlsrewritter_da_e2_binary_tree /data/AgRefactor/src/hlsrewritter/DA_E2_binary_tree/kernel.h DFS \
    --source-root /data/AgRefactor/src/hlsrewritter/DA_E2_binary_tree
run_case hlsrewritter_exception_e1_matrix_qr /data/AgRefactor/src/hlsrewritter/Exception_E1_Matrix_QR_Decomposition/kernel.cpp qrd_compute \
    --source-root /data/AgRefactor/src/hlsrewritter/Exception_E1_Matrix_QR_Decomposition
run_case hlsrewritter_incomplete_e3_aes /data/AgRefactor/src/hlsrewritter/Incomplete_E3_AES/kernel.cpp aes_encrypt \
    --source-root /data/AgRefactor/src/hlsrewritter/Incomplete_E3_AES
run_case hlsrewritter_pointer_e1_dnn /data/AgRefactor/src/hlsrewritter/Pointer_E1_DNN/kernel.cpp forward \
    --source-root /data/AgRefactor/src/hlsrewritter/Pointer_E1_DNN
run_case hlsrewritter_pointer_e3_double_pointer /data/AgRefactor/src/hlsrewritter/Pointer_E3_double_pointer/kernel.cpp trap \
    --source-root /data/AgRefactor/src/hlsrewritter/Pointer_E3_double_pointer

printf '\nALL CASES DISPATCHED batch=%s\n' "$batch"
