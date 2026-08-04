# Pre-Stage-4 P4-0D Decision Record

Parent: `d7a820d200bb8af8c08b10d95b5daff3389149e9`.

Scope: Public RTL COSIM only.

## Inherited authority

- P4-0C is the accepted intermediate order:
  `Preflight -> Public native Vitis CSIM -> CSYNTH -> Hidden`.
- P4-0D inserts an explicit typed Public RTL COSIM stage between CSYNTH and
  Hidden.
- The inherited contract freezes the independent stage and its position, not a
  concrete enum spelling.

## P4-0D decisions

- This section chooses `ValidationState.PUBLIC_COSIM` and
  `QualificationStage.PUBLIC_COSIM` and freezes those names here.
- Final order:
  `Preflight -> Public native Vitis CSIM -> CSYNTH -> Public RTL COSIM -> Hidden`.
- Add hard `max_cosim_calls`, consumed `cosim_calls`, default
  `cosim_timeout_s=900` and safety ceiling `7200`.
- A separate Vitis Tcl version probe consumes `tool_calls=1` and no COSIM call.
- Immediately before the physical COSIM-chain launch, prospectively check and
  consume `tool_calls=1, cosim_calls=1`. A blocked launch does not execute the
  physical command.
- `cosim_policy=required` is normal product behavior. `off` is development-only
  and emits typed skipped suite evidence.
- Raw stdout/stderr and Vitis reports are auxiliary evidence. Static strings or
  regex alone cannot produce an authoritative pass or ownership verdict.
- Candidate/Testbench ownership requires a valid structured outcome combined
  with the Public COSIM phase and declared source roles. Otherwise ownership is
  `unknown` and repair is false.
- Public COSIM repair suppression is enforced in the validation transition
  policy. Report metadata is descriptive, not the enforcement mechanism.
- A typed Candidate/Testbench COSIM failure terminates the candidate. It cannot
  enter candidate repair, optimizer recovery, or any model prompt.
- Public suite IDs must exactly match task-declared Public suites. Hidden suites
  are never accepted by the Public COSIM handler.
- A claimed pass requires physical tool launch, physical COSIM launch, no
  timeout, return code zero and immutable typed evidence.
- Qualification/cache pipeline identity advances to
  `prestage4-public-rtl-cosim-v1`; Stage3 fingerprints COSIM policy and timeout.
- Execution identity records COSIM budget usage, invocation phase/version
  evidence and per-Public-suite status/evidence hashes.
- Stage3 recovery prospective budget retains the accepted prior formula and
  adds two tool calls plus one COSIM call per Public suite.
- The acceptance smoke uses the formal Preflight handler and the formal Hidden
  host-differential CSIM handler; fixture-only file reads or ad-hoc Hidden
  compilation are not accepted as stage completion evidence.
- P4-0C full baseline is exactly `2089`. P4-0D adds exactly `7` unittest methods,
  so acceptance requires focused `7` and full `2096` exactly.

P4-0E through P4-0I are excluded. Stage 4 remains forbidden.

## Vitis 2023.2 testbench outcome transport

The Public RTL COSIM typed outcome path is passed to the self-checking C/C++
testbench with the documented `csim_design -argv` and `cosim_design -argv`
interfaces. It is not encoded as a C preprocessor string macro. The native
Public CSIM invocation remains compatible when no argument is supplied. A
COSIM pass is accepted only when the command succeeds and a fresh exact-schema
typed outcome records `status=passed`, `failure_owner=none`, and
`reason_code=cosim_passed`.
