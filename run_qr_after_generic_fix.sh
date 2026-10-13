#!/usr/bin/env bash
set -u
source /data/agrefactorpp_env.sh || exit 90
cd /data/AgRefactor || exit 91
base=/data/agrefactor_runs/targeted_common_fix_20261002
mkdir -p "$base"
python -m agrefactor.cli refactor /data/AgRefactor/src/hlsrewritter/Exception_E1_Matrix_QR_Decomposition/kernel.cpp --top qrd_compute --source-root /data/AgRefactor/src/hlsrewritter/Exception_E1_Matrix_QR_Decomposition --public-tests auto --hidden-tests auto --test-generation-profile lightweight --max-testbench-repairs 3 --max-candidate-repairs 3 --cosim-policy required --json --output-dir "$base/qr"
printf 'QR_RC=%s\n' "$?"
