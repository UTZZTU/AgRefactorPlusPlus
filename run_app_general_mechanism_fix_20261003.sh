#!/usr/bin/env bash
set -u
source /data/agrefactorpp_env.sh || exit 90
cd /data/AgRefactor || exit 91

base=/data/agrefactor_runs/app_general_mechanism_fix_20261003
mkdir -p "$base"
case_name="${1:?case name required}"
source_path="${2:?source path required}"
top_name="${3:?top function required}"
source_root="${4:?source root required}"
cosim_timeout="${5:-4500}"

python -m agrefactor.cli refactor "$source_path" \
  --top "$top_name" \
  --source-root "$source_root" \
  --public-tests auto \
  --hidden-tests auto \
  --test-generation-profile lightweight \
  --max-testbench-repairs 3 \
  --max-candidate-repairs 3 \
  --csim-timeout-s 900 \
  --csynth-timeout-s 2700 \
  --cosim-timeout-s "$cosim_timeout" \
  --cosim-policy required \
  --json \
  --output-dir "$base/$case_name" \
  > "$base/$case_name.log" 2>&1
printf 'case=%s exit_code=%s result_dir=%s\n' "$case_name" "$?" "$base/$case_name"
