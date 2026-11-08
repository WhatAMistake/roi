import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from therapist_bot import ExistentialTherapistBot, voice_response_fits


def bot():
    obj = ExistentialTherapistBot(api_key="TEST", use_rag=False)
    obj.client = MagicMock()
    return obj


def response(text, reason="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason=reason)])


def stream(*parts):
    return iter(SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content), finish_reason=reason)]) for content, reason in parts)


def complete(obj):
    return obj._complete_text(model="model", messages=[{"role": "user", "content": "hello"}])


def test_api_status_is_completion_signal():
    obj = bot()
    assert obj._needs_continuation("A finished sentence.", "length")
    assert obj._needs_continuation("A finished sentence.", "max_tokens")
    assert not obj._needs_continuation("No punctuation", "stop")
    obj.client.chat.completions.create.return_value = response("No punctuation")
    assert complete(obj) == "No punctuation"
    assert obj.client.chat.completions.create.call_count == 1


def test_text_continues_and_removes_echoed_prefix():
    obj = bot()
    obj.client.chat.completions.create.side_effect = [response("The first sentence. ", "length"), response("The first sentence. And another.")]
    assert complete(obj) == "The first sentence. And another."
    call = obj.client.chat.completions.create.call_args_list[1].kwargs
    assert call["messages"][-2]["content"] == "The first sentence. "
    assert "Не повторяй" in call["messages"][-1]["content"]


def test_stream_continuation_removes_echo_split_across_chunks():
    obj = bot()
    obj.client.chat.completions.create.side_effect = [stream(("The first sentence. ", "length")), stream(("The first ", None), ("sentence. And another.", "stop"))]
    assert "".join(obj._complete_stream(model="m", messages=[])) == "The first sentence. And another."
    assert all(c.kwargs["stream"] is True for c in obj.client.chat.completions.create.call_args_list)


def test_continuations_are_bounded_and_partial_history_not_saved():
    obj = bot()
    obj.client.chat.completions.create.side_effect = [response("one ", "length"), response("two ", "length"), response("three ", "length")]
    assert obj.chat("hello").startswith("Ошибка:")
    assert obj.history == []
    assert obj.client.chat.completions.create.call_count == 3


@pytest.mark.parametrize("item", [response("", "stop"), response("partial", None), response("partial", "content_filter")])
def test_invalid_finish_or_empty_does_not_enter_history(item):
    obj = bot()
    obj.client.chat.completions.create.return_value = item
    assert obj.chat("hello").startswith("Ошибка:")
    assert obj.history == []
    assert obj.client.chat.completions.create.call_count == 1


def test_interrupted_stream_reports_failure_and_does_not_save_history():
    obj = bot()
    obj.client.chat.completions.create.return_value = stream(("Partial text", None))
    chunks = list(obj.chat_stream("hello"))
    assert chunks[0] == "Partial text"
    assert chunks[-1].startswith("Ошибка:")
    assert not obj.history


def test_empty_stream_and_transport_error_do_not_save_history():
    for outcome in [stream((None, "stop")), ConnectionError("connection lost")]:
        obj = bot()
        obj.client.chat.completions.create.side_effect = outcome if isinstance(outcome, Exception) else None
        if not isinstance(outcome, Exception):
            obj.client.chat.completions.create.return_value = outcome
        assert "Ошибка:" in "".join(obj.chat_stream("hello"))
        assert obj.history == []
        assert obj.client.chat.completions.create.call_count == 1


def test_compatibility_error_is_reported_without_retry():
    obj = bot()
    obj.client.chat.completions.create.side_effect = ValueError("unsupported reasoning_effort")
    assert "unsupported reasoning_effort" in obj.chat("hello")
    assert obj.client.chat.completions.create.call_count == 1
    assert obj.history == []


def test_short_word_overlap_and_unbroken_word_continuation():
    obj = bot()
    assert obj._join_continuation("It was sad", "sad but true") == " but true"
    assert obj._join_continuation("обрыв", "ается") == "ается"
    assert obj._join_continuation("A complete thought. ", "A complete thought. Next") == "Next"


@pytest.mark.parametrize("text,expected", [
    ("x" * 1700, True), ("x" * 1701, False),
    ("**" + "x" * 1696 + "**", True),
    ("**" + "x" * 1697 + "**", False),
    ("😀" * 850, True), ("😀" * 851, False),
    ("&amp;" * 340, True), ("&amp;" * 341, False),
])
def test_voice_limit_counts_literal_markup_and_utf16(text, expected):
    assert voice_response_fits(text) is expected


def test_plain_text_chat_has_no_voice_instruction_or_limit():
    obj = bot()
    answer = "x" * 1800
    obj._complete_text = MagicMock(return_value=answer)
    assert obj.chat("hello") == answer
    messages = obj._complete_text.call_args.kwargs["messages"]
    assert all("1700" not in message["content"] for message in messages)
    assert obj._complete_text.call_count == 1


def test_voice_at_limit_is_accepted_without_regeneration():
    obj = bot()
    answer = "x" * 1699 + "."
    obj._complete_text = MagicMock(return_value=answer)
    assert obj.chat("hello", is_voice=True) == answer
    assert obj._complete_text.call_count == 1
    assert [message.content for message in obj.history] == ["hello", answer]
