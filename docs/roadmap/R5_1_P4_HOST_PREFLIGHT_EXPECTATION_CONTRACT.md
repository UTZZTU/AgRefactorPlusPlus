# R5.1 P4 Host-Preflight Expectation Contract

Date: 2026-09-20  
Route: V2.3 R5.1 P4  
Status: implemented for protocol schema v2  

## Problem

The original P4 host preflight treated every nonzero public-oracle result as an
invalid adapter. That conflated two distinct conditions:

1. the adapter, ABI, or oracle is invalid; and
2. a frozen, independently reviewed oracle correctly rejects the raw source.

The second condition is a valid source-baseline opportunity. Excluding it
would bias P4 toward sources that already pass and remove precisely the cases
needed to measure refactor lift.

## Contract

Each case in source-baseline protocol schema v2 declares exactly one
`host_preflight_expected` value:

- `oracle_pass`: host compilation must succeed, execution must return zero,
  and the frozen pass marker must be present.
- `candidate_mismatch`: host compilation must succeed, execution must return a
  code in the frozen `candidate_mismatch_returncodes`, and the pass marker must
  be absent.

Any other observation fails the preflight. A case cannot infer or change its
expectation after execution. Compile failures, missing markers, unexpected
return codes, and pass markers emitted with nonzero status remain failures.

The real source-baseline run still uses the existing ValidationOrchestrator,
TargetProfile, CSIM, CSYNTH, and COSIM handlers. This contract does not turn a
host mismatch into an accepted repair, bypass formal validation, alter R2-R5,
or consume Provider/Vitis calls during preflight.

## Oracle Boundary

The newly admitted internal adapters call only the top registered by the
existing Vitis contract:

- AES `Cipher`: fixed AES-128 expanded key and a standard known-answer output.
- DES `des_crypt`: fixed standard DES round keys and the standard ciphertext
  for key `0123456789ABCDEF` and plaintext `0123456789ABCDE7`.
- PRESENT `present80_encryptBlock`: published zero-key/zero-plaintext
  PRESENT-80 known-answer vector.
- SHA-256 `sha256_update`: bounded state-transition checks for the registered
  update top, without calling non-top digest helpers.
- Overlapping `Overlapping`: independently calculated input pattern and
  floating-point result with a bounded tolerance.

No adapter copies or replaces the candidate algorithm, adds an optimization
pragma, uses Hidden input, or encodes a Vitis log identifier. Fixed input
vectors and expected outputs are test data, not repair policy.

The DES raw source is preregistered as `candidate_mismatch`: its top indexes a
schedule pointer outside the supplied 16-round schedule, so repeated runs of
the same binary produce different first mismatch bytes. The adapter never
accepts any of those observed bytes and compares only with the standard known
answer. This is candidate-owned raw failure evidence, not an oracle pass.

## Version and Safety

The protocol is `v2.3-r5.1-p4-source-baseline-v3`, schema version 2. The
history/future partition remains the P0-P2 partition; DES remains in `future`.
The maximum reserve is 39 Vitis launches for 13 cases, with zero Provider
calls. `R5_ACCEPTED` remains false and `R6_STARTED` remains false.
