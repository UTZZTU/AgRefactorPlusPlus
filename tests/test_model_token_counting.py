import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from agrefactor.models.model_token_counting import (
    ModelTokenCounter,
    TokenCountingUnavailable,
)


class FakeTokenizer:
    chat_template = "registered"

    def apply_chat_template(self, messages, *, tokenize, add_generation_prompt):
        assert tokenize and add_generation_prompt
        text = "".join(f"<{item['role']}>{item['content']}" for item in messages)
        return list(range(len(text.split())))


class ModelTokenCountingTests(unittest.TestCase):
    def create_manifest(self, root, *, status="verified"):
        tokenizer_dir = root / "tokenizer"
        tokenizer_dir.mkdir()
        payloads = {"tokenizer.json": b"vocab", "tokenizer_config.json": b"template"}
        for name, data in payloads.items():
            (tokenizer_dir / name).write_bytes(data)
        manifest = {
            "schema_version": 1,
            "provider": "deepseek",
            "model_id": "deepseek-v4-flash",
            "base_url": "https://api.deepseek.com",
            "request_mode": "chat_completions",
            "tokenizer_dir": str(tokenizer_dir),
            "tokenizer_sha256": {
                name: hashlib.sha256(data).hexdigest()
                for name, data in payloads.items()
            },
            "context_window": 1000,
            "safe_margin_tokens": 4 if status == "verified" else None,
            "verification_status": status,
        }
        path = root / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path

    def test_unverified_manifest_fails_closed_for_runtime(self):
        with TemporaryDirectory() as directory:
            manifest = self.create_manifest(Path(directory), status="pending_usage_check")
            with self.assertRaises(TokenCountingUnavailable):
                ModelTokenCounter.from_manifest(manifest)

    def test_registered_files_and_effective_model_identity_are_required(self):
        with TemporaryDirectory() as directory:
            manifest = self.create_manifest(Path(directory))
            with patch("transformers.AutoTokenizer.from_pretrained", return_value=FakeTokenizer()):
                counter = ModelTokenCounter.from_manifest(manifest)
            self.assertTrue(counter.matches(
                provider="deepseek",
                model_id="deepseek-v4-flash",
                base_url="https://api.deepseek.com/",
                request_mode="chat_completions",
            ))
            self.assertFalse(counter.matches(
                provider="deepseek",
                model_id="other-model",
                base_url="https://api.deepseek.com",
                request_mode="chat_completions",
            ))
            self.assertGreater(counter.count_messages([
                {"role": "system", "content": "Use current evidence."},
                {"role": "user", "content": "int value = 2;"},
            ]), 0)

    def test_modified_tokenizer_and_unregistered_tool_message_are_rejected(self):
        with TemporaryDirectory() as directory:
            manifest = self.create_manifest(Path(directory))
            tokenizer_path = Path(directory) / "tokenizer" / "tokenizer.json"
            tokenizer_path.write_bytes(b"changed")
            with self.assertRaises(TokenCountingUnavailable):
                ModelTokenCounter.from_manifest(manifest)

        with TemporaryDirectory() as directory:
            manifest = self.create_manifest(Path(directory))
            with patch("transformers.AutoTokenizer.from_pretrained", return_value=FakeTokenizer()):
                counter = ModelTokenCounter.from_manifest(manifest)
            with self.assertRaises(TokenCountingUnavailable):
                counter.count_messages([{"role": "assistant", "content": None, "tool_calls": []}])


if __name__ == "__main__":
    unittest.main()
