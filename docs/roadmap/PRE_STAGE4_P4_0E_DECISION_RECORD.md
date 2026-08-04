# Pre-Stage-4 P4-0E Model Runtime Decision Record

Parent: `8d5ccd9063b4855978fdf284463034af9b81d545`.

Scope: default DeepSeek V4 Flash selection, CWD-local `.env`, typed credential
gate, per-call reasoning/Thinking policy, safe evidence, private-reasoning
suppression, legacy YAML default cleanup, and the first post-hardening real
network smoke over a committed C/C++ fixture.

Frozen normal CLI defaults are `model_id=deepseek-v4-flash`, `family=deepseek`,
`base_url=https://api.deepseek.com`, `api_key_env=DEEPSEEK_API_KEY`, and
`--reasoning-effort auto`. User model/family/endpoint/key-environment overrides
remain explicit. There is no raw API-key option.

The invocation CWD `.env` is loaded with `override=False`: an already exported
process variable wins. Missing selected credentials fail before provider launch
with typed, value-free evidence. No `.env` contents or credential values are
persisted. The selected value may enter only the in-memory provider transport.


Every call has one typed role. Auto maps the frozen medium roles to project
medium and all generation/repair/optimization roles to project high. For the
concrete `deepseek-v4-flash` profile, project medium maps to provider high,
project high maps to provider max, and Thinking is sent as
`extra_body={"thinking":{"type":"enabled"}}`. Other families use their typed
map/omit/reject policy; support is never guessed.

Provider-neutral transport parameters remain the public parameter contract. Safe per-call policy evidence travels separately in `ModelRequest.metadata`; only the legacy AG2 in-memory bridge uses an internal field, which is stripped before client configuration while preserving imported Python objects. Existing pre-resolved immutable configurations remain authoritative and are not remapped a second time.

Provider reasoning payloads are never persisted. Final content containing
private-reasoning tags fails closed. P4-0F and later behavior remains excluded;
Stage 4 remains forbidden.
