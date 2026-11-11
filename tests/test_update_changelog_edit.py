"""Tests for bilingual admin changelog edit flow.

Run: py -m pytest tests/test_update_changelog_edit.py -v
"""

import sys
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "src"))


def _make_bot():
    """Build a lightweight stand-in with real update-edit methods bound on it."""
    import telegram_bot as tb

    bot = MagicMock()
    bot.admin_id = 42
    bot.user_langs = {42: "ru", 7: "en"}
    bot.user_states = {}
    bot.pending_update_changelogs = {
        "ru": "Старый RU",
        "en": "Old EN",
    }
    bot.send_long_message = AsyncMock()

    # Bind real helpers onto the mock instance
    cls = tb.TelegramTherapistBot
    bot._clear_pending_update = cls._clear_pending_update.__get__(bot, cls)
    bot._format_update_preview = cls._format_update_preview.__get__(bot, cls)
    bot._send_update_preview = cls._send_update_preview.__get__(bot, cls)
    bot._start_update_edit = cls._start_update_edit.__get__(bot, cls)
    bot._handle_update_confirm_input = cls._handle_update_confirm_input.__get__(bot, cls)
    bot._handle_update_edit_input = cls._handle_update_edit_input.__get__(bot, cls)
    bot._process_update_broadcast = AsyncMock()
    return bot, tb


def _msg(user_id: int = 42):
    message = MagicMock()
    message.from_user = SimpleNamespace(id=user_id)
    message.answer = AsyncMock()
    return message


@pytest.mark.asyncio
async def test_preview_contains_edit_option():
    bot, _ = _make_bot()
    preview = bot._format_update_preview(bot.pending_update_changelogs)
    assert "править" in preview
    assert "Старый RU" in preview
    assert "Old EN" in preview


@pytest.mark.asyncio
async def test_preview_shows_technical_summary_not_source(tmp_path):
    bot = _durable_bot(tmp_path)
    pending = bot.update_cache.pending()
    pending["analysis"] = {
        "added": 4, "removed": 2, "missing": ["src/unknown.py"], "failures": [],
        "findings": [
            {"category": "fix", "component": "Ответы", "description": "Исправлен ответ",
             "path": "src/telegram_bot.py", "evidence": "secret source code"},
            {"category": "internal", "component": "Кэш", "description": "Упрощён кэш",
             "path": "src/cache.py", "evidence": "private code"},
            {"category": "uncertain", "component": "Диалог", "description": "Эффект неизвестен",
             "path": "prompts/a.md", "evidence": "private prompt"},
        ],
    }
    bot.update_cache.update(pending)
    preview = bot._format_update_preview(pending["notes"])
    for text in ("+4", "−2", "Ответы", "Исправлен ответ", "Упрощён кэш",
                 "Эффект неизвестен", "src/unknown.py", "Готовые пользовательские тексты"):
        assert text in preview
    assert "secret source code" not in preview
    assert "private prompt" not in preview


@pytest.mark.asyncio
async def test_edit_flow_saves_ru_then_en_and_returns_to_confirm():
    bot, _ = _make_bot()
    bot.user_states[42] = "update_confirm"
    msg = _msg(42)

    await bot._handle_update_confirm_input(msg, "править")
    assert bot.user_states[42] == "update_edit_ru"

    await bot._handle_update_edit_input(msg, "update_edit_ru", "Новый русский changelog")
    assert bot.pending_update_changelogs["ru"] == "Новый русский changelog"
    assert bot.user_states[42] == "update_edit_en"

    await bot._handle_update_edit_input(msg, "update_edit_en", "New English changelog")
    assert bot.pending_update_changelogs["en"] == "New English changelog"
    assert bot.user_states[42] == "update_confirm"

    preview_calls = [
        c for c in bot.send_long_message.await_args_list
        if c.args and "Предпросмотр обновления" in str(c.args[1])
    ]
    assert preview_calls, "expected updated preview after EN save"
    assert "Новый русский changelog" in preview_calls[-1].args[1]
    assert "New English changelog" in preview_calls[-1].args[1]


@pytest.mark.asyncio
async def test_confirm_yes_starts_broadcast():
    bot, _ = _make_bot()
    bot.user_states[42] = "update_confirm"
    msg = _msg(42)

    await bot._handle_update_confirm_input(msg, "да")
    bot._process_update_broadcast.assert_awaited_once()


@pytest.mark.asyncio
async def test_confirm_no_clears_pending():
    bot, _ = _make_bot()
    bot.user_states[42] = "update_confirm"
    msg = _msg(42)

    await bot._handle_update_confirm_input(msg, "нет")
    assert not hasattr(bot, "pending_update_changelogs")
    assert bot.user_states[42] == "chat"


@pytest.mark.asyncio
async def test_empty_edit_rejected():
    bot, _ = _make_bot()
    bot.user_states[42] = "update_edit_ru"
    msg = _msg(42)

    await bot._handle_update_edit_input(msg, "update_edit_ru", "   ")
    assert bot.pending_update_changelogs["ru"] == "Старый RU"
    assert bot.user_states[42] == "update_edit_ru"
    msg.answer.assert_awaited()


def _durable_bot(tmp_path):
    bot, tb = _make_bot()
    source = tmp_path / "src" / "telegram_bot.py"
    source.parent.mkdir()
    source.write_text("old\n", encoding="utf-8")
    bot.update_cache = tb.UpdateCache(tmp_path)
    bot.update_cache.prepare()
    source.write_text("new\n", encoding="utf-8")
    pending = bot.update_cache.prepare()
    pending["status"] = "review"
    pending["notes"] = {"ru": "Первое", "en": "First"}
    bot.update_cache.update(pending)
    bot.pending_update_changelogs = dict(pending["notes"])
    bot.update_broadcast_lock = asyncio.Lock()
    bot.update_check_lock = asyncio.Lock()
    bot._has_consent = lambda user_id: user_id in (42, 7)
    cls = tb.TelegramTherapistBot
    bot._process_update_broadcast = cls._process_update_broadcast.__get__(bot, cls)
    bot._resume_update_broadcast = cls._resume_update_broadcast.__get__(bot, cls)
    bot._check_and_notify_updates = cls._check_and_notify_updates.__get__(bot, cls)
    return bot


@pytest.mark.asyncio
async def test_edits_and_preview_failure_survive_restart(tmp_path):
    bot = _durable_bot(tmp_path)
    bot.user_states[42] = "update_edit_ru"
    await bot._handle_update_edit_input(_msg(), "update_edit_ru", "Изменено")
    assert bot.update_cache.pending()["notes"]["ru"] == "Изменено"
    bot.send_long_message.side_effect = RuntimeError("Telegram unavailable")
    assert await bot._check_and_notify_updates(delay=False) == "error"
    assert bot.update_cache.pending()["notes"]["ru"] == "Изменено"
    bot.send_long_message.side_effect = None
    assert await bot._check_and_notify_updates(delay=False) == "review"
    assert bot.pending_update_changelogs["ru"] == "Изменено"


@pytest.mark.asyncio
async def test_uncertain_broadcast_is_not_retried(tmp_path):
    bot = _durable_bot(tmp_path)
    attempts = []

    async def send(user_id, *args, **kwargs):
        attempts.append(user_id)
        if user_id == 7 and attempts.count(7) == 1:
            raise RuntimeError("temporary failure")

    bot.send_long_message.side_effect = send
    await bot._process_update_broadcast()
    assert bot.update_cache.pending() is None
    history = bot.update_cache._read()["_update_state"]["history"]
    assert history[-1]["recipients"]["42"]["status"] == "sent"
    assert history[-1]["recipients"]["7"]["status"] == "uncertain"
    assert await bot._check_and_notify_updates(delay=False) == "idle"
    sends_to_7 = [call for call in bot.send_long_message.await_args_list if call.args[0] == 7]
    assert len(sends_to_7) == 1


@pytest.mark.asyncio
async def test_skip_resolves_only_frozen_snapshot(tmp_path):
    bot = _durable_bot(tmp_path)
    (tmp_path / "src" / "telegram_bot.py").write_text("newer\n", encoding="utf-8")
    bot._check_and_notify_updates = AsyncMock()
    await bot._handle_update_confirm_input(_msg(), "нет")
    assert bot.update_cache.pending() is None
    assert bot.update_cache.prepare()["paths"] == ["src/telegram_bot.py"]


@pytest.mark.asyncio
async def test_revoked_consent_is_skipped_on_retry(tmp_path):
    bot = _durable_bot(tmp_path)
    async def send(user_id, *args, **kwargs):
        if user_id == 7:
            raise RuntimeError("temporary failure")
    bot.send_long_message.side_effect = send
    pending = bot.update_cache.pending()
    pending["status"] = "sending"
    pending["recipients"] = {"42": {"status": "sent", "lang": "ru"},
                             "7": {"status": "pending", "lang": "en"}}
    bot.update_cache.update(pending)
    bot._has_consent = lambda user_id: user_id == 42
    bot._check_and_notify_updates = AsyncMock()
    bot.send_long_message.reset_mock()
    await bot._process_update_broadcast()
    assert bot.update_cache.pending() is None
    assert all(call.args[0] != 7 for call in bot.send_long_message.await_args_list)


@pytest.mark.asyncio
async def test_in_flight_after_restart_is_not_sent_twice(tmp_path):
    bot = _durable_bot(tmp_path)
    pending = bot.update_cache.pending()
    pending["status"] = "sending"
    pending["recipients"] = {"7": {"status": "in_flight", "lang": "en"}}
    bot.update_cache.update(pending)
    bot._check_and_notify_updates = AsyncMock()
    await bot._process_update_broadcast()
    assert bot.update_cache.pending() is None
    assert bot.update_cache._read()["_update_state"]["history"][-1]["recipients"]["7"]["status"] == "uncertain"
    assert all(call.args[0] != 7 for call in bot.send_long_message.await_args_list)
