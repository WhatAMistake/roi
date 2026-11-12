"""Offline coverage of localization, fallbacks and localized handler paths."""

import ast
import logging
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from string import Formatter
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram import Dispatcher

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from i18n import LOCALES, t
import telegram_bot as tb


def fields(template):
    return {field for _, field, _, _ in Formatter().parse(template) if field is not None}


def test_locale_keys_and_placeholders_match():
    assert LOCALES["ru"].keys() == LOCALES["en"].keys()
    for key in LOCALES["ru"]:
        assert fields(LOCALES["ru"][key]) == fields(LOCALES["en"][key]), key
        for lang in ("ru", "en"):
            values = {field: f"VALUE_{field}" for field in fields(LOCALES[lang][key])}
            result = t(lang, key, **values)
            for value in values.values():
                assert value in result, (lang, key)


def test_static_localization_references_exist():
    root = Path(__file__).resolve().parents[1] / "src"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "t" and len(node.args) >= 2):
                continue
            key_expr = node.args[1]
            if isinstance(key_expr, ast.Constant) and isinstance(key_expr.value, str):
                keys = [key_expr.value]
            elif isinstance(key_expr, ast.IfExp):
                keys = [branch.value for branch in (key_expr.body, key_expr.orelse)
                        if isinstance(branch, ast.Constant) and isinstance(branch.value, str)]
                assert len(keys) == 2, (path, node.lineno)
            else:
                pytest.fail(f"Audit dynamic translation key in {path.name}:{node.lineno}")
            for key in keys:
                assert key in LOCALES["ru"] and key in LOCALES["en"], (path, node.lineno, key)


def test_fallbacks_and_diagnostics_never_log_values(monkeypatch, caplog):
    monkeypatch.setitem(LOCALES["en"], "test_only", "Hello {value}")
    secret = "SECRET_PRIVATE_USER_CONTENT"
    with caplog.at_level(logging.WARNING, logger="i18n"):
        assert t("unknown-" + secret, "test_only", value="ok") == "Hello ok"
        assert t("ru", "test_only", value="ok") == "Hello ok"
        assert t("ru", secret) == secret
        assert t("en", "test_only") == "Hello {value}"

        class Broken:
            def __format__(self, spec):
                raise ValueError(secret)

        assert t("en", "test_only", value=Broken()) == "Hello {value}"
    assert "unknown locale" in caplog.text
    assert "missing translation" in caplog.text
    assert "formatting failed" in caplog.text
    assert secret not in caplog.text


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_silence_html_and_substitution(lang):
    rendered = t(lang, "silence_remaining", remaining=37)
    assert rendered.startswith("<i>") and rendered.endswith("</i>")
    assert "37" in rendered
    parser = HTMLParser()
    parser.feed(rendered)
    parser.close()


def handler(owner, name):
    owner.dp = Dispatcher()
    owner._register_handlers()
    return next(h.callback for h in owner.dp.message.handlers if h.callback.__name__ == name)


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["ru", "en"])
async def test_sticker_handler_uses_saved_language(lang):
    owner = tb.TelegramTherapistBot.__new__(tb.TelegramTherapistBot)
    owner.user_langs = {7: lang}
    owner.user_recent_messages = {}
    owner._save_user_prefs = Mock(return_value=True)
    therapist = SimpleNamespace(generate_response=Mock(return_value="reply"), history=[])
    owner._get_therapist = Mock(return_value=therapist)
    msg = SimpleNamespace(from_user=SimpleNamespace(id=7),
                          sticker=SimpleNamespace(emoji="🙂"), answer=AsyncMock())
    await handler(owner, "handle_sticker")(msg)
    assert [m.content for m in therapist.history] == ["[Sticker: 🙂]", "reply"]
    prompt = t(lang, "sticker_prompt", emoji="🙂")
    therapist.generate_response.assert_called_once_with(
        prompt, temporary_system_instruction=prompt, use_analysis_model=True)
    msg.answer.assert_awaited_once_with("reply")


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["ru", "en"])
async def test_silence_handler_and_remaining_time(lang, monkeypatch):
    owner = tb.TelegramTherapistBot.__new__(tb.TelegramTherapistBot)
    owner.user_langs = {7: lang}
    owner.silence_until = {}
    msg = SimpleNamespace(from_user=SimpleNamespace(id=7), answer=AsyncMock())
    monkeypatch.setattr(time, "time", lambda: 1000)
    monkeypatch.setattr(tb.asyncio, "sleep", AsyncMock())
    await handler(owner, "cmd_silence")(msg)
    assert msg.answer.await_args_list[0].args == (t(lang, "silence_start"),)
    assert msg.answer.await_args_list[0].kwargs["parse_mode"] == "HTML"
    assert msg.answer.await_args_list[1].args == (t(lang, "silence_end"),)
    assert not owner.silence_until
    tb.asyncio.sleep.assert_awaited_once_with(60)

    owner.silence_until[7] = 1037
    owner.start_time = 0
    owner.message_queue = {}
    owner.user_first_seen = {}
    owner.user_last_active = {}
    owner._save_user_prefs = Mock()
    msg.date = SimpleNamespace(timestamp=lambda: 1000)
    msg.text = "hello"
    msg.answer.reset_mock()
    await owner._handle_message(msg)
    msg.answer.assert_awaited_once_with(t(lang, "silence_remaining", remaining=37),
                                       parse_mode="HTML")


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["ru", "en"])
async def test_meaning_handler_uses_localized_prompt_and_system(lang):
    owner = tb.TelegramTherapistBot.__new__(tb.TelegramTherapistBot)
    owner.user_langs = {7: lang}
    therapist = SimpleNamespace(generate_response=Mock(return_value="observation"), history=[])
    owner.user_recent_messages = {}
    owner._save_user_prefs = Mock(return_value=True)
    owner._get_therapist = Mock(return_value=therapist)
    msg = SimpleNamespace(from_user=SimpleNamespace(id=7), answer=AsyncMock())
    await owner._handle_meaning(msg)
    therapist.generate_response.assert_called_once_with(
        t(lang, "meaning_prompt"), temporary_system_instruction=t(lang, "meaning_system"),
        use_analysis_model=False)
    msg.answer.assert_awaited_once_with("🌱 observation")
    assert therapist.history[-1].content == "🌱 observation"
