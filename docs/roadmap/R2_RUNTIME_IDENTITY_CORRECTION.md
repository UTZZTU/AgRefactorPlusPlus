# V2.3 R2 runtime identity correction

Status: product correction complete; identity-complete real calibration and
revised R4 canary evidence are pending.

## Observed defect

The real calibration runner resolved `temperature=0` and `max_tokens=4096`,
but the R2 advisor sent only `response_format=json_object` to the provider.
Its certificate identity bound provider and model names, but not the model
family, endpoint, seed, or effective request parameters. The earlier real
certificate therefore did not prove that calibration and held-out execution
used the same runtime.

## Correction

- The R2 advisor now freezes, validates, sends, and exposes the complete
  non-secret request parameter object.
- `response_format=json_object` is mandatory; conflicting formats, non-finite
  values, and credential-like parameter keys fail before a provider call.
- Provider identity now binds provider, logical and physical model, family,
  base URL, and complete effective request parameters.
- The frozen calibration manifest fixes `deepseek-flash`, the `deepseek`
  family, the DeepSeek endpoint, `temperature=0`, `max_tokens=4096`, and
  `seed=23`. Runtime CLI drift is rejected.
- Certificate verification already hashes the complete provider identity, so
  endpoint or parameter drift now fails closed without a second mechanism.

## Qualification reuse

The calibration runner may reuse prior real-Vitis qualification records only
through an explicit read-only root. It regenerates every case identity and
Candidate source hash, validates truth, zero-provider accounting, Vitis
accounting, privacy flags, and the uniquely eligible event, then records both
source-file and canonical-record hashes. Provider results are never reused.
The independent auditor validates the reuse ledger and requires zero Vitis
launches in the current calibration execution.

## Preserved boundaries

No product CLI or parallel orchestration path was added. `refactor`,
`optimize`, and `full` remain the only normal entrypoints. R2 remains
shadow-only and R4 remains default-off, Candidate-only, canary-gated, and
subject to full revalidation and independent audit. The prior certificate is
invalid for R4 because it did not bind the complete runtime identity.
