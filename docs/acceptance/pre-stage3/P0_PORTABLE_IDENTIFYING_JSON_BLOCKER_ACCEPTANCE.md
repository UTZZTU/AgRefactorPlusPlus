# P0 Portable Identifying JSON-Object Blocker Correction

## Observed real-P0 failure

A real DFS source-only run reached the selected model endpoint and then
failed inside the Legacy AG2 identifying branch:

```text
This response_format type is unavailable now
```

The identifying agents declared imported Pydantic response models even
though their consumer already parses and validates JSON text with
`json.loads()`.

## Corrected contract

Identification is inherently a JSON-object task, independent of model
vendor. Its three shared AG2 configurations now declare the portable
OpenAI-compatible contract:

```json
{"type": "json_object"}
```

The six identifier prompts explicitly require:

```json
{"identified_items": ["item_identified: description", "..."]}
```

Deduplicator and filter prompts already declare their JSON object
shapes. No provider name, endpoint test, or vendor branch is added to
`HLSAgentLoader`; the frozen vendor-neutral loader boundary remains
intact.

## Scope

This is an observed P0 blocker correction only. It does not close P0,
close Pre-Stage-3, or start Stage 3. Acceptance requires deterministic
regression, one bounded real AG2 JSON-object call through the same
identifying configuration, credential-value scanning, and a clean
synchronized repository.

```text
P0_STATUS=active_retry_required
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```
