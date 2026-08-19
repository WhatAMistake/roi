import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "src"))

from therapist_bot import ExistentialTherapistBot


def _bot(language: str = "ru") -> ExistentialTherapistBot:
    bot = ExistentialTherapistBot(api_key="TEST", use_rag=False, language=language)
    bot.client = MagicMock()
    return bot


def _text_response(content: str, finish_reason: str = "stop"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ]
    )


def _stream_chunks(*parts: tuple[str | None, str | None]):
    for content, finish_reason in parts:
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content=content),
                    finish_reason=finish_reason,
                )
            ]
        )


def test_looks_complete_accepts_sentence_and_quoted_endings():
    bot = _bot()
    assert bot._looks_complete("Я рядом.")
    assert bot._looks_complete("Stay with this?")
    assert bot._looks_complete("Это конец…")
    assert bot._looks_complete('He said "enough."')
    assert bot._looks_complete("Хватит».")
    assert not bot._looks_complete("Я чувствую что")
    assert not bot._looks_complete("обрыва")
    assert not bot._looks_complete("")


def test_needs_continuation_on_length_or_incomplete_text():
    bot = _bot()
    assert bot._needs_continuation("Я рядом.", "length")
    assert bot._needs_continuation("Я рядом.", "max_tokens")
    assert bot._needs_continuation("Я чувствую что", "stop")
    assert not bot._needs_continuation("Я рядом.", "stop")


def test_complete_text_continues_after_length_and_joins():
    bot = _bot()
    bot.client.chat.completions.create.side_effect = [
        _text_response("Я чувствую что жизнь обрыв", "length"),
        _text_response("ается на полуслове.", "stop"),
    ]

    text = bot._complete_text(
        model="deepseek-v4-pro",
        messages=[{"role": "user", "content": "мне тяжело"}],
        temperature=0.7,
        max_tokens=bot.CHAT_MAX_TOKENS,
    )

    assert text == "Я чувствую что жизнь обрывается на полуслове."
    assert bot.client.chat.completions.create.call_count == 2
    first_kwargs = bot.client.chat.completions.create.call_args_list[0].kwargs
    second_kwargs = bot.client.chat.completions.create.call_args_list[1].kwargs
    assert first_kwargs["max_tokens"] == 4096
    assert second_kwargs["messages"][-1]["content"] == (
        "Продолжи предыдущий ответ ровно с того места, где он оборвался. "
        "Не повторяй уже написанный текст. Допиши текущее предложение и заверши мысль."
    )
    assert second_kwargs["messages"][-2]["content"] == "Я чувствую что жизнь обрыв"


def test_complete_text_does_not_continue_finished_reply():
    bot = _bot()
    bot.client.chat.completions.create.return_value = _text_response("Я рядом.", "stop")

    text = bot._complete_text(
        model="deepseek-v4-pro",
        messages=[{"role": "user", "content": "ок"}],
        temperature=0.7,
        max_tokens=bot.CHAT_MAX_TOKENS,
    )

    assert text == "Я рядом."
    assert bot.client.chat.completions.create.call_count == 1


def test_complete_text_caps_continuations():
    bot = _bot()
    bot.client.chat.completions.create.side_effect = [
        _text_response("one ", "length"),
        _text_response("two ", "length"),
        _text_response("three ", "length"),
        _text_response("four ", "length"),
    ]

    text = bot._complete_text(
        model="deepseek-v4-pro",
        messages=[{"role": "user", "content": "go"}],
        temperature=0.7,
        max_tokens=bot.CHAT_MAX_TOKENS,
    )

    assert text == "one two three "
    assert bot.client.chat.completions.create.call_count == 3


def test_complete_stream_continues_and_yields_both_parts():
    bot = _bot()
    bot.client.chat.completions.create.side_effect = [
        _stream_chunks(("Я чувствую что жизнь обрыв", None), (None, "length")),
        _stream_chunks(("ается на полуслове.", "stop")),
    ]

    chunks = list(
        bot._complete_stream(
            model="deepseek-v4-pro",
            messages=[{"role": "user", "content": "мне тяжело"}],
            temperature=0.7,
            max_tokens=bot.CHAT_MAX_TOKENS,
        )
    )

    assert "".join(chunks) == "Я чувствую что жизнь обрывается на полуслове."
    assert bot.client.chat.completions.create.call_count == 2
    assert bot.client.chat.completions.create.call_args_list[0].kwargs["stream"] is True
    assert bot.client.chat.completions.create.call_args_list[0].kwargs["max_tokens"] == 4096


def test_chat_and_generate_use_chat_budget():
    bot = _bot()
    bot.client.chat.completions.create.return_value = _text_response("Я рядом.", "stop")

    bot.chat("ок")
    bot.generate_response("ок")

    assert bot.CHAT_MAX_TOKENS == 4096
    assert bot.ANALYSIS_MAX_TOKENS == 8192
    for call in bot.client.chat.completions.create.call_args_list:
        assert call.kwargs["max_tokens"] == 4096
