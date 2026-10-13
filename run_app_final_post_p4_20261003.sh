#!/usr/bin/env bash
set -u
source /data/agrefactorpp_env.sh || exit 90
cd /data/AgRefactor || exit 91

base=/data/agrefactor_runs/app_general_mechanism_post_p4_20261003
mkdir -p "$base"

run_case() {
  local name="$1"
  local source_path="$2"
  local top="$3"
  local source_root="$4"
  mkdir -p "$base/$name"
  python -m agrefactor.cli refactor "$source_path" \
    --top "$top" \
    --source-root "$source_root" \
    --public-tests auto \
    --hidden-tests auto \
    --test-generation-profile lightweight \
    --max-testbench-repairs 3 \
    --max-candidate-repairs 3 \
    --csim-timeout-s 900 \
    --csynth-timeout-s 2700 \
    --cosim-timeout-s 4500 \
    --cosim-policy required \
    --json \
    --output-dir "$base/$name" \
    > "$base/$name.log" 2>&1
  printf 'case=%s exit_code=%s result_dir=%s\n' "$name" "$?" "$base/$name"
}

run_case encode_one_block \
  /data/AgRefactor/src/app/libjpeg/encode_one_block.cpp \
  encode_one_block \
  /data/AgRefactor/src/app/libjpeg
run_case select_colors \
  /data/AgRefactor/src/app/libjpeg/select_colors.cpp \
  select_colors \
  /data/AgRefactor/src/app/libjpeg
run_case mm_chain_dp_orig \
  /data/AgRefactor/src/app/minimap2/mm_chain_dp_orig.cpp \
  mm_chain_dp_orig \
  /data/AgRefactor/src/app/minimap2
run_case chacha20_stream \
  /data/AgRefactor/src/app/libsodium/chacha20_stream.cpp \
  chacha20_encrypt_bytes \
  /data/AgRefactor/src/app/libsodium
run_case recalib_table_orig \
  /data/AgRefactor/src/app/bqsr/recalib_table_orig.cpp \
  Java_com_falconcomputing_genomics_bqsr_FalconRecalibrationEngine_recalibrateNative \
  /data/AgRefactor/src/app/bqsr
