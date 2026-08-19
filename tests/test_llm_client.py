import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "src"))

from llm_client import apply_thinking_disabled, create_openai_client


def test_apply_thinking_disabled_for_deepseek():
    kwargs = apply_thinking_disabled({"model": "deepseek-v4-pro", "max_tokens": 2000})
    assert kwargs["extra_body"]["thinking"] == {"type": "disabled"}
    assert kwargs["max_tokens"] == 2000


def test_apply_thinking_disabled_overwrites_existing_thinking():
    kwargs = apply_thinking_disabled({
        "model": "deepseek-v4-flash",
        "extra_body": {"thinking": {"type": "enabled"}, "foo": 1},
    })
    assert kwargs["extra_body"]["thinking"] == {"type": "disabled"}
    assert kwargs["extra_body"]["foo"] == 1


def test_apply_thinking_disabled_skips_other_models():
    kwargs = {"model": "gpt-5.4-mini", "extra_body": {"keep": True}}
    assert apply_thinking_disabled(kwargs) == kwargs


def test_create_openai_client_injects_thinking_disabled():
    fake_client = MagicMock()
    original_create = fake_client.chat.completions.create

    with patch("openai.OpenAI", return_value=fake_client):
        client = create_openai_client(api_key="test", base_url="https://example.com")

    client.chat.completions.create(model="deepseek-v4-pro", messages=[])
    _, kwargs = original_create.call_args
    assert kwargs["extra_body"]["thinking"] == {"type": "disabled"}
