"""Verify the registered DeepSeek tokenizer against synthetic API usage."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agrefactor.models import (
    ChatMessage,
    ModelRequest,
    ModelSpec,
    OpenAICompatibleProvider,
)
from agrefactor.models.environment import load_invocation_dotenv
from agrefactor.models.model_token_counting import (
    ModelTokenCounter,
    TokenCountingUnavailable,
)


_SAMPLES = (
    (ChatMessage("system", "Reply with exactly OK."), ChatMessage("user", "Count a simple English prompt.")),
    (ChatMessage("system", "请只回复 OK。"), ChatMessage("user", "这是一条中文 token 计数探针。")),
    (ChatMessage("system", "Reply with exactly OK."), ChatMessage("user", "HLS 214-298: struct input_t { int* data; };")),
    (ChatMessage("system", "Reply with exactly OK."), ChatMessage("user", "error: unsupported pointer member in top-level argument")),
    (ChatMessage("system", "Reply with exactly OK."), ChatMessage("user", "std::array<int, 16> values{}; return values[3] + 1;")),
    (ChatMessage("system", "Reply with exactly OK."), ChatMessage("user", "签名检查：void top(const sample_t* input, int output[8]);")),
    (ChatMessage("system", "Reply with exactly OK."), ChatMessage("user", "line 31: cannot convert 'ap_uint<8>' to 'int'")),
    (ChatMessage("system", "Reply with exactly OK."), ChatMessage("user", "enum class state_t { idle, busy, done };")),
    (ChatMessage("system", "Reply with exactly OK."), ChatMessage("user", "α + β <= 0; // UTF-8 symbols")),
    (
        ChatMessage("system", "Answer the last user message with exactly OK."),
        ChatMessage("user", "First synthetic turn."),
        ChatMessage("assistant", "The first turn is complete."),
        ChatMessage("user", "Second synthetic turn."),
    ),
    (
        ChatMessage("system", "Reply with exactly OK."),
        ChatMessage("user", "A declaration has a pointer-to-const parameter."),
        ChatMessage("assistant", "Check the declaration."),
        ChatMessage("user", "Use the same response format."),
    ),
    (
        ChatMessage("system", "Reply with exactly OK."),
        ChatMessage("user", "#include <ap_int.h>\nvoid top(ap_uint<16> x, int* y) { *y = x; }"),
    ),
)


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument(
        "--manifest",
        default="agrefactor/models/tokenization_sources/deepseek_v4_flash.json",
    )
    parser.add_argument("--online", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output_path = Path(args.output)
    if args.model != "deepseek-v4-flash":
        parser.error("only the registered deepseek-v4-flash model is supported")
    try:
        counter = ModelTokenCounter.from_manifest(
            args.manifest,
            allow_pending_verification=True,
        )
    except TokenCountingUnavailable as exc:
        print(f"tokenizer verification unavailable: {exc}", file=sys.stderr)
        return 2

    if not args.online:
        counts = [counter.count_messages(messages) for messages in _SAMPLES]
        payload = {
            "verification": "offline_tokenizer_verified",
            "model_id": args.model,
            "sample_count": len(counts),
            "prompt_token_counts": counts,
            "message_template": "DeepSeek official tokenizer chat_template",
        }
        _write(output_path, payload)
        print(json.dumps(payload, sort_keys=True))
        return 0

    if len(_SAMPLES) > 12:
        raise AssertionError("online request limit exceeded")
    load_invocation_dotenv(Path.cwd())
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("missing required environment variable: DEEPSEEK_API_KEY", file=sys.stderr)
        return 2
    provider = OpenAICompatibleProvider(
        name="deepseek",
        default_base_url="https://api.deepseek.com",
        default_api_key_env="DEEPSEEK_API_KEY",
        timeout_s=45,
    )
    model = ModelSpec(
        name=args.model,
        provider="deepseek",
        model=args.model,
        base_url="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
    )
    samples: list[dict[str, int | str]] = []
    for index, messages in enumerate(_SAMPLES, start=1):
        local_count = counter.count_messages(messages)
        response = provider.generate(
            model,
            ModelRequest(
                messages=messages,
                parameters={
                    "max_tokens": 8,
                    "extra_body": {"thinking": {"type": "disabled"}},
                },
                metadata={"purpose": "synthetic_token_count_validation"},
            ),
        )
        server_count = response.usage.prompt_tokens
        if server_count <= 0:
            print("provider returned no usable prompt token usage", file=sys.stderr)
            return 2
        samples.append(
            {
                "sample_id": f"synthetic_{index:02d}",
                "local_prompt_tokens": local_count,
                "server_prompt_tokens": server_count,
                "server_minus_local": server_count - local_count,
                "max_output_tokens": 8,
            }
        )
    deltas = [int(item["server_minus_local"]) for item in samples]
    payload = {
        "verification": "empirically_bounded",
        "model_id": args.model,
        "endpoint": "https://api.deepseek.com",
        "request_mode": "chat_completions",
        "probe_reasoning_mode": "thinking_disabled_for_eight_token_output_cap",
        "request_count": len(samples),
        "max_tokens_per_request": 8,
        "sample_kind": "synthetic_only",
        "samples": samples,
        "measured_min_delta": min(deltas),
        "measured_max_delta": max(deltas),
        "measured_safe_margin_tokens": max(0, max(deltas)),
        "exact_count_claim": False,
    }
    _write(output_path, payload)
    print(json.dumps({key: value for key, value in payload.items() if key != "samples"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
