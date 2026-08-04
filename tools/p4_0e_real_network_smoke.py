#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from agrefactor.models import (
    ChatMessage,
    ModelCallRole,
    ModelRequest,
    credential_presence_evidence,
    load_invocation_dotenv,
    resolve_model_runtime,
)


def atomic(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--invocation-cwd", type=Path, required=True)
    parser.add_argument("--model", default=None)
    parser.add_argument("--family")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key-env")
    args = parser.parse_args()

    environment_evidence = load_invocation_dotenv(args.invocation_cwd)
    selection = resolve_model_runtime(
        args.model,
        family=args.family,
        base_url=args.base_url,
        api_key_env=args.api_key_env,
        reasoning_effort="auto",
    )
    config = selection.effective_config
    credential = credential_presence_evidence(config.api_key_env)
    output_path = args.output / "p4_0e_network_smoke.json"
    if not credential["credential_present"]:
        atomic(
            output_path,
            {
                "schema_version": 1,
                "status": "blocked",
                "reason_code": "selected_credential_missing",
                "environment": environment_evidence.to_dict(),
                "credential": credential,
                "secret_values_persisted": False,
            },
        )
        raise SystemExit(
            "P4_0E_REAL_NETWORK_SMOKE_BLOCKED "
            "reason=selected_credential_missing "
            f"api_key_env={credential['api_key_env']}"
        )

    repo = Path(__file__).resolve().parents[1]
    sample_path = (
        repo
        / "tests/fixtures/p4_0d_rtl_cosim/reference.cpp"
    )
    if sample_path.is_symlink() or not sample_path.is_file():
        raise SystemExit(
            "committed real smoke sample missing or unsafe: "
            + str(sample_path)
        )
    sample = sample_path.read_text(encoding="utf-8")
    sample_sha = hashlib.sha256(sample.encode("utf-8")).hexdigest()

    parameters, policy = config.parameterize_call(
        ModelCallRole.REFACTOR_PLANNING
    )
    provider = selection.registry.get_provider(config.provider_name)
    response = provider.generate(
        config.to_model_spec(),
        ModelRequest(
            messages=(
                ChatMessage(
                    role="system",
                    content=(
                        "Read the committed C/C++ sample. Return only the "
                        "requested final token. Never expose private reasoning."
                    ),
                ),
                ChatMessage(
                    role="user",
                    content=(
                        "Committed sample SHA256: "
                        + sample_sha
                        + "\nSample:\n"
                        + sample
                        + "\nReply with exactly "
                        "AGREFACTOR_P4_0E_NETWORK_OK."
                    ),
                ),
            ),
            parameters=parameters,
            metadata={"model_call_policy": policy.to_dict()},
        ),
    )
    final_text = response.text.strip()
    if (
        "AGREFACTOR_P4_0E_NETWORK_OK" not in final_text
        or len(final_text) > 512
    ):
        raise SystemExit(
            "real provider final response did not satisfy the bounded "
            "transport contract"
        )

    payload = {
        "schema_version": 1,
        "status": "passed",
        "model": config.to_manifest(),
        "model_defaults_source": selection.defaults_source,
        "environment": environment_evidence.to_dict(),
        "credential": credential,
        "call_policy": policy.to_dict(),
        "sample": {
            "path": str(sample_path.relative_to(repo)),
            "sha256": sample_sha,
            "size_bytes": sample_path.stat().st_size,
            "committed_fixture": True,
        },
        "response_sha256": hashlib.sha256(
            response.text.encode("utf-8")
        ).hexdigest(),
        "response_chars": len(response.text),
        "usage": response.usage.to_dict(),
        "provider_metadata": dict(response.metadata),
        "raw_response_persisted": False,
        "private_reasoning_persisted": False,
        "secret_values_persisted": False,
        "dotenv_contents_persisted": False,
    }
    serialized = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
    )
    secret = os.environ.get(config.api_key_env or "")
    if isinstance(secret, str) and secret and secret in serialized:
        raise SystemExit("selected credential value entered persisted evidence")
    lowered = serialized.casefold()
    if "<think" in lowered or "</reasoning>" in lowered:
        raise SystemExit("private reasoning tag entered persisted evidence")
    atomic(output_path, payload)
    print(
        "P4_0E_REAL_NETWORK_SMOKE_PASSED "
        f"model={config.model_id} sample=true thinking=true "
        f"provider_effort={policy.effective_provider_reasoning_effort} "
        "secret_free=true private_reasoning_persisted=false"
    )


if __name__ == "__main__":
    main()
