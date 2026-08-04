# P4-0D Public RTL COSIM Acceptance

This document is populated as accepted only after the target-host full tracked
shadow, exact complete regression, real Vitis chain and final scope gate pass.

Required parent: `d7a820d200bb8af8c08b10d95b5daff3389149e9`.

Required evidence:

- exact order `Preflight -> Public native Vitis CSIM -> CSYNTH -> Public RTL
  COSIM -> Hidden`;
- explicit P4-0D typed state and qualification stage;
- separate version-probe accounting `tool_calls=1, cosim_calls=0`;
- prospective physical launch accounting `tool_calls=1, cosim_calls=1`;
- no command launch when that prospective budget is blocked;
- exact Public suite declaration matching and safe `suite_NNN` work layout;
- pass is fail-closed without physical launch and immutable evidence;
- typed Candidate/Testbench ownership, otherwise unknown-safe;
- no Public COSIM candidate repair, optimizer recovery or model feedback;
- Public/Hidden data isolation;
- cache identity separation by COSIM policy and timeout;
- execution identity contains COSIM budget, invocation and suite evidence;
- Refactor, advanced repair-aware, direct Optimize and Full wiring;
- P4-0C baseline `2089` plus `7` new tests equals full `2096` exactly;
- focused test count is exactly `7`;
- real Vitis 2023.2 Preflight->CSIM->CSYNTH->COSIM->Hidden smoke using the
  formal Preflight and Hidden handlers;
- `network_llm_used=false`;
- exact final Git scope and no P4-0E+ paths.

This acceptance does not permit Stage 4.

## Vitis 2023.2 testbench outcome transport

The Public RTL COSIM typed outcome path is passed to the self-checking C/C++
testbench with the documented `csim_design -argv` and `cosim_design -argv`
interfaces. It is not encoded as a C preprocessor string macro. The native
Public CSIM invocation remains compatible when no argument is supplied. A
COSIM pass is accepted only when the command succeeds and a fresh exact-schema
typed outcome records `status=passed`, `failure_owner=none`, and
`reason_code=cosim_passed`.
