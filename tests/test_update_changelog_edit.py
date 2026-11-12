"""Tests for bilingual admin changelog edit flow.

Run: py -m pytest tests/test_update_changelog_edit.py -v
"""

import sys
import asyncio
import html
import re
import time
from html.parser import HTMLParser
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


@pytest.mark.parametrize(("reason", "failures", "expected"), [
    ("Не удалось завершить анализ", ["file: LLM request failed (HTTP 429)"], "HTTP 429"),
    ("Не удалось завершить анализ", ["file: TimeoutError: LLM request timed out"], "вовремя"),
    ("Не удалось завершить анализ", ["file: ValueError: Invalid JSON response"], "некорректный результат"),
    ("Не удалось создать changelog (ValueError: Incomplete model response)", [], "ответ модели оборван"),
])
def test_incomplete_notice_shows_safe_diagnostic(reason, failures, expected):
    import telegram_bot as tb
    pending = {"status": "review", "reason": reason + " secret-personal-data",
               "analysis": {"missing": [], "failures": failures}}
    notice = tb._update_review_notice("incomplete", pending)
    assert expected in notice
    assert "secret-personal-data" not in notice


def test_incomplete_notice_keeps_skip_and_shows_model_failure():
    import telegram_bot as tb
    pending = {"id": "a" * 32, "status": "review", "reason": "Не удалось завершить анализ",
               "analysis": {"missing": ["src/private.py"],
                            "failures": ["src/private.py: LLM request failed (HTTP 400)"]}}
    notice = tb._update_review_notice("incomplete", pending)
    assert "снимки" in notice and "HTTP 400" in notice
    assert f"/updates skip {'a' * 32}" in notice
    assert "src/private.py" not in notice


@pytest.mark.asyncio
async def test_preview_contains_edit_option():
    bot, _ = _make_bot()
    preview = bot._format_update_preview(bot.pending_update_changelogs)
    assert "править" in preview
    assert "RU и EN" in preview
    assert "Old EN" not in preview


@pytest.mark.asyncio
async def test_preview_is_compact_and_hides_technical_details(tmp_path):
    bot = _durable_bot(tmp_path)
    pending = bot.update_cache.pending()
    pending["analysis"]["findings"] = [
            {"category": "fix", "component": "Ответы", "description": "Исправлен ответ",
             "path": "src/telegram_bot.py", "evidence": "secret source code"},
            {"category": "internal", "component": "Кэш", "description": "Упрощён кэш",
             "path": "src/cache.py", "evidence": "private code"},
            {"category": "uncertain", "component": "Диалог", "description": "Эффект неизвестен",
             "path": "prompts/a.md", "evidence": "private prompt"},
            {"category": "change", "component": "Кэш", "description": "Изменено условие оператора if",
             "path": "src/cache.py", "evidence": "if condition"},
    ]
    bot.update_cache.update(pending)
    preview = bot._format_update_preview(pending["notes"])
    for text in ("Изменено файлов", "Краткая сводка", "Исправлен ответ",
                 "RU и EN", "Отправить?"):
        assert text in preview
    for text in ("secret source code", "private prompt", "src/", "Упрощён кэш",
                 "Эффект неизвестен", "условие оператора", "Компоненты:"):
        assert text not in preview


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
    assert "New English changelog" not in preview_calls[-1].args[1]
    bot.send_long_message.reset_mock()
    await bot._handle_update_confirm_input(msg, "📄 EN")
    assert "New English changelog" in bot.send_long_message.await_args.args[1]


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
    pending["analysis"] = {"fragment_count": 1, "fragments": {"test": []},
                           "findings": [], "missing": [], "failures": []}
    bot.update_cache.update(pending)
    bot.pending_update_changelogs = dict(pending["notes"])
    bot.update_broadcast_lock = asyncio.Lock()
    bot.update_check_lock = asyncio.Lock()
    bot._has_consent = lambda user_id: user_id in (42, 7)
    cls = tb.TelegramTherapistBot
    bot._process_update_broadcast = cls._process_update_broadcast.__get__(bot, cls)
    bot._resume_update_broadcast = cls._resume_update_broadcast.__get__(bot, cls)
    bot._check_and_notify_updates = cls._check_and_notify_updates.__get__(bot, cls)
    bot._skip_unanalysable_update = cls._skip_unanalysable_update.__get__(bot, cls)
    return bot


@pytest.mark.asyncio
async def test_incomplete_analysis_blocks_preview_and_preserves_baseline(tmp_path):
    bot = _durable_bot(tmp_path)
    pending = bot.update_cache.pending()
    pending["reason"] = "Не удалось завершить анализ. secret failure details"
    pending["analysis"]["failures"] = ["src/telegram_bot.py: ValueError"] * 12
    bot.update_cache.update(pending)
    before = bot.update_cache._read()

    assert await bot._check_and_notify_updates(delay=False) == "incomplete"
    notice = bot.send_long_message.await_args.args[1]
    assert "не завершён" in notice and "/updates" in notice
    assert "Отправить?" not in notice and "ValueError" not in notice and "src/" not in notice
    assert bot.user_states.get(42, "chat") == "chat"
    assert bot.update_cache._read() == before
    await bot._process_update_broadcast()
    assert bot.update_cache._read() == before

    # A stale confirmation or skip must not bypass the durable state.
    bot.pending_update_changelogs = dict(pending["notes"])
    for choice in ("да", "нет", "править"):
        bot.user_states[42] = "update_confirm"
        await bot._handle_update_confirm_input(_msg(), choice)
        bot.pending_update_changelogs = dict(pending["notes"])
    assert bot.update_cache._read() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["missing_analysis", "missing_fragments", "missing_snapshot"])
async def test_unproven_analysis_is_incomplete(tmp_path, damage):
    bot = _durable_bot(tmp_path)
    pending = bot.update_cache.pending()
    if damage == "missing_analysis":
        pending.pop("analysis")
    elif damage == "missing_fragments":
        pending["analysis"]["fragments"] = {}
    else:
        pending["analysis"]["missing"] = ["src/telegram_bot.py"]
    bot.update_cache.update(pending)
    before = bot.update_cache._read()
    assert await bot._check_and_notify_updates(delay=False) == "incomplete"
    assert "не завершён" in bot.send_long_message.await_args.args[1]
    await bot._process_update_broadcast()
    assert bot.update_cache._read() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missing", "no_changes"])
async def test_explicit_skip_of_unrecoverable_or_empty_draft(tmp_path, kind):
    bot = _durable_bot(tmp_path)
    pending = bot.update_cache.pending()
    pending["notes"] = {"ru": "", "en": ""}
    if kind == "missing":
        pending["reason"] = "Не удалось завершить анализ."
        pending["analysis"]["missing"] = ["src/telegram_bot.py"]
    else:
        pending["reason"] = "Модель не нашла пользовательских изменений."
    bot.update_cache.update(pending)
    before = bot.update_cache._read()
    assert await bot._check_and_notify_updates(delay=False) == (
        "incomplete" if kind == "missing" else "no_changes")
    assert f"/updates skip {pending['id']}" in bot.send_long_message.await_args.args[1]
    await bot._skip_unanalysable_update(_msg(7), ["skip", pending["id"]])
    await bot._skip_unanalysable_update(_msg(), ["skip", "outdated"])
    assert bot.update_cache._read() == before
    # Newer changes are not included in the frozen draft being skipped.
    (tmp_path / "src" / "telegram_bot.py").write_bytes(b"newer\n")
    await bot._skip_unanalysable_update(_msg(), ["skip", pending["id"]])
    assert bot.update_cache.pending() is None
    assert bot.update_cache.prepare()["snapshots"]["src/telegram_bot.py"] == "newer\n"


@pytest.mark.asyncio
async def test_skip_command_cannot_discard_review_or_failed_model_draft(tmp_path):
    bot = _durable_bot(tmp_path)
    pending = bot.update_cache.pending()
    before = bot.update_cache._read()
    await bot._skip_unanalysable_update(_msg(), ["skip", pending["id"]])
    assert bot.update_cache._read() == before
    pending["status"] = "sending"
    pending["analysis"]["missing"] = ["src/telegram_bot.py"]
    bot.update_cache.update(pending)
    before = bot.update_cache._read()
    await bot._skip_unanalysable_update(_msg(), ["skip", pending["id"]])
    assert bot.update_cache._read() == before
    pending["status"] = "review"
    pending["analysis"]["missing"] = []
    pending["notes"] = {"ru": "", "en": ""}
    pending["analysis"]["failures"] = ["LLM request failed"]
    bot.update_cache.update(pending)
    before = bot.update_cache._read()
    await bot._skip_unanalysable_update(_msg(), ["skip", pending["id"]])
    assert bot.update_cache._read() == before


@pytest.mark.asyncio
async def test_line_ending_only_draft_can_be_skipped(tmp_path):
    bot = _durable_bot(tmp_path)
    pending = bot.update_cache.pending()
    pending["notes"] = {"ru": "", "en": ""}
    pending["reason"] = "Модель не нашла пользовательских изменений."
    pending["analysis"].update({"fragment_count": 0, "fragments": {},
                                "unchanged": pending["paths"]})
    bot.update_cache.update(pending)
    assert await bot._check_and_notify_updates(delay=False) == "no_changes"
    await bot._skip_unanalysable_update(_msg(), ["skip", pending["id"]])
    assert bot.update_cache.pending() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("notes", [
    {"ru": "", "en": ""},
    {"ru": "   ", "en": "Valid English"},
    {"ru": "Хороший текст", "en": "• NONE"},
    {"ru": "• Нет пользовательских изменений.", "en": "Valid English"},
    {"ru": "Хороший текст", "en": "---"},
    {"ru": "Хороший текст", "en": "A" * 3501},
])
async def test_invalid_bilingual_notes_cannot_be_confirmed_or_sent(tmp_path, notes):
    bot = _durable_bot(tmp_path)
    pending = bot.update_cache.pending()
    pending["notes"] = notes
    bot.update_cache.update(pending)
    before = bot.update_cache._read()
    assert await bot._check_and_notify_updates(delay=False) == "invalid"
    notice = bot.send_long_message.await_args.args[1]
    assert "RU и EN" in notice and "Отправить?" not in notice
    await bot._process_update_broadcast()
    assert bot.update_cache._read() == before

    bot.pending_update_changelogs = dict(notes)
    bot.user_states[42] = "update_confirm"
    await bot._handle_update_confirm_input(_msg(), "да")
    assert bot.update_cache._read() == before


@pytest.mark.asyncio
async def test_stale_preview_cannot_confirm_changed_draft(tmp_path):
    bot = _durable_bot(tmp_path)
    pending = bot.update_cache.pending()
    pending["notes"]["ru"] = "Другой текст"
    bot.update_cache.update(pending)
    before = bot.update_cache._read()
    bot.user_states[42] = "update_confirm"
    await bot._handle_update_confirm_input(_msg(), "да")
    assert bot.update_cache._read() == before
    assert bot.user_states[42] == "chat"


@pytest.mark.asyncio
async def test_no_user_changes_are_not_an_analysis_failure_or_a_broadcast(tmp_path):
    bot = _durable_bot(tmp_path)
    pending = bot.update_cache.pending()
    pending["notes"] = {"ru": "", "en": ""}
    pending["reason"] = "Модель не нашла пользовательских изменений. Можно пропустить или править."
    pending["analysis"]["findings"] = [{"category": "internal", "description": "Внутренняя правка"}]
    bot.update_cache.update(pending)
    before = bot.update_cache._read()
    assert await bot._check_and_notify_updates(delay=False) == "no_changes"
    notice = bot.send_long_message.await_args.args[1]
    assert "изменений не найдено" in notice and "не завершён" not in notice
    assert "Отправить?" not in notice
    await bot._process_update_broadcast()
    assert bot.update_cache._read() == before


@pytest.mark.asyncio
async def test_retry_incomplete_analysis_uses_existing_generation(tmp_path):
    bot = _durable_bot(tmp_path)
    pending = bot.update_cache.pending()
    pending["notes"] = {"ru": "", "en": ""}
    pending["reason"] = "Не удалось завершить анализ."
    pending["analysis"] = {"fragments": {}, "fragment_count": 1, "failures": ["ошибка"], "missing": []}
    bot.update_cache.update(pending)
    client = MagicMock()
    def completion(content):
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content=content))])
    client.chat.completions.create.side_effect = [
        completion('{"findings":[{"category":"fix","component":"Bot",'
                   '"description":"Исправлен ответ","evidence":"new"}]}'),
        completion('{"ru":"• Ответ исправлен","en":"• Response fixed"}'),
    ]
    bot._get_therapist.return_value = SimpleNamespace(client=client, model="mock")
    assert await bot._check_and_notify_updates(delay=False, retry_generation=True) == "review"
    assert bot.update_cache.pending()["notes"]["en"] == "• Response fixed"
    assert client.chat.completions.create.call_count == 2


@pytest.mark.asyncio
async def test_long_preview_is_bounded_escaped_and_contains_full_notes(tmp_path):
    bot = _durable_bot(tmp_path)
    pending = bot.update_cache.pending()
    ru = "RU " + "&<😀" * 1000
    en = "EN " + ">&😀" * 1000
    pending["notes"] = {"ru": ru, "en": en}
    pending["analysis"]["findings"] = [
        {"category": "fix", "description": f"Исправлено отображение сообщения {i} " + "'&<>" * 130}
        for i in range(12)
    ]
    bot.update_cache.update(pending)
    assert await bot._check_and_notify_updates(delay=False) == "review"
    calls = bot.send_long_message.await_args_list
    assert len(calls) == 1
    messages = [call.args[1] for call in calls]
    assert all(len(message) <= 4000 and len(message.encode("utf-16-le")) // 2 <= 4000
               for message in messages)
    assert "Полная сводка" in messages[0] and "RU и EN" in messages[0]
    assert ru not in messages[0] and en not in messages[0]

    class StrictHTML(HTMLParser):
        def handle_starttag(self, tag, attrs):
            assert tag == "b" and not attrs
        def handle_endtag(self, tag):
            assert tag == "b"
        def handle_entityref(self, name):
            assert name in {"amp", "lt", "gt", "quot"}
    for section, expected in (("сводка", "\n".join(
            "• " + item["description"] for item in pending["analysis"]["findings"])),
            ("RU", ru), ("EN", en)):
        bot.send_long_message.reset_mock()
        await bot._handle_update_confirm_input(_msg(), section)
        chunks = []
        while True:
            message = bot.send_long_message.await_args.args[1]
            assert len(message) <= 4000 and len(message.encode("utf-16-le")) // 2 <= 4000
            StrictHTML(convert_charrefs=False).feed(message)
            chunks.append(message.split("\n", 1)[1].rsplit("\n\nДля перехода", 1)[0])
            page, count = re.search(r"страница (\d+)/(\d+)", message).groups()
            if page == count:
                break
            await bot._handle_update_confirm_input(_msg(), "вперёд")
        assert html.unescape("".join(chunks)) == expected
        assert bot.send_long_message.await_args.kwargs["reply_markup"] is not None


@pytest.mark.asyncio
async def test_detail_navigation_routing_and_stale_draft(tmp_path):
    bot = _durable_bot(tmp_path)
    import telegram_bot as tb
    _enable_message_routing(bot, tb)
    pending = bot.update_cache.pending()
    pending["notes"]["ru"] = "• " + "Длинный текст с деталями. " * 120
    bot.update_cache.update(pending)
    await bot._send_update_preview(42, pending["notes"])
    await _route_text(bot, "📄 RU")
    first = bot.send_long_message.await_args.args[1]
    assert "страница 1/2" in first
    await _route_text(bot, "Вперёд ➡️")
    assert "страница 2/2" in bot.send_long_message.await_args.args[1]
    await _route_text(bot, "⬅️ Назад")
    assert bot.send_long_message.await_args.args[1] == first
    assert bot.message_queue[42] == []
    pending["notes"]["ru"] = "Другой текст"
    bot.update_cache.update(pending)
    bot.send_long_message.reset_mock()
    msg = await _route_text(bot, "📄 RU")
    bot.send_long_message.assert_not_awaited()
    assert "некорректны" in msg.answer.await_args.args[0]
    assert bot.user_states[42] == "chat"


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


def _enable_message_routing(bot, tb):
    cls = tb.TelegramTherapistBot
    bot._handle_message = cls._handle_message.__get__(bot, cls)
    bot._process_text_message = cls._process_text_message.__get__(bot, cls)
    bot._handle_cancel = cls._handle_cancel.__get__(bot, cls)
    bot.message_queue = {}
    bot.start_time = 0
    bot.processed_flood_users = set()
    bot.user_first_seen = {}
    bot.user_last_active = {}
    bot.silence_until = {}
    bot.temp_associations = {}
    bot.filmframe_state = {}


async def _route_text(bot, text):
    msg = _msg()
    msg.date = SimpleNamespace(timestamp=time.time)
    msg.text = text
    await bot._handle_message(msg)
    return msg


@pytest.mark.asyncio
async def test_repeated_preview_choices_then_skip_restore_chat_and_keyboard(tmp_path):
    bot = _durable_bot(tmp_path)
    import telegram_bot as tb
    _enable_message_routing(bot, tb)
    bot.user_states[42] = "update_confirm"
    bot._check_and_notify_updates = AsyncMock()

    for _ in range(4):
        await _route_text(bot, "непонятно")
        assert bot.message_queue[42] == []

    msg = await _route_text(bot, "❌ Нет")
    assert bot.user_states[42] == "chat"
    assert bot.update_cache.pending() is None
    assert bot.message_queue[42] == []
    assert msg.answer.await_args.kwargs["reply_markup"] == tb.get_main_keyboard("ru")
    bot._handle_chat = AsyncMock()
    await _route_text(bot, "Продолжим")
    bot._handle_chat.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("edit_state", ["update_edit_ru", "update_edit_en"])
async def test_cancel_edit_keeps_draft_and_releases_queue(tmp_path, edit_state):
    bot = _durable_bot(tmp_path)
    import telegram_bot as tb
    _enable_message_routing(bot, tb)
    bot.user_states[42] = edit_state
    before = bot.update_cache.pending()

    for _ in range(4):
        await _route_text(bot, " ")
        assert bot.message_queue[42] == []
    msg = await _route_text(bot, "Отмена")
    assert bot.user_states[42] == "chat"
    assert bot.update_cache.pending() == before
    assert bot.message_queue[42] == []
    assert msg.answer.await_args.kwargs["reply_markup"] == tb.get_main_keyboard("ru")


@pytest.mark.asyncio
async def test_update_input_failure_releases_own_queue_slot(tmp_path):
    bot = _durable_bot(tmp_path)
    import telegram_bot as tb
    _enable_message_routing(bot, tb)
    bot.user_states[42] = "update_confirm"
    existing = time.time() - 1
    bot.message_queue[42] = [existing]
    bot._handle_update_confirm_input = AsyncMock(side_effect=RuntimeError("failure"))
    with pytest.raises(RuntimeError):
        await _route_text(bot, "да")
    assert bot.message_queue[42] == [existing]


@pytest.mark.asyncio
async def test_cancel_failure_releases_own_queue_slot(tmp_path):
    bot = _durable_bot(tmp_path)
    import telegram_bot as tb
    _enable_message_routing(bot, tb)
    bot.user_states[42] = "update_edit_ru"
    existing = time.time() - 1
    bot.message_queue[42] = [existing]
    bot._handle_cancel = AsyncMock(side_effect=RuntimeError("failure"))
    with pytest.raises(RuntimeError):
        await _route_text(bot, "Отмена")
    assert bot.message_queue[42] == [existing]


@pytest.mark.asyncio
async def test_voice_update_input_does_not_release_unrelated_queue_slot(tmp_path):
    bot = _durable_bot(tmp_path)
    import telegram_bot as tb
    _enable_message_routing(bot, tb)
    bot.user_states[42] = "update_confirm"
    bot.message_queue[42] = [time.time()]
    await bot._process_text_message(_msg(), "непонятно", is_voice=True)
    assert len(bot.message_queue[42]) == 1


@pytest.mark.asyncio
async def test_broadcast_restores_main_keyboard(tmp_path):
    bot = _durable_bot(tmp_path)
    import telegram_bot as tb
    bot.user_states[42] = "update_confirm"
    bot._check_and_notify_updates = AsyncMock()
    await bot._process_update_broadcast()
    report = bot.send_long_message.await_args
    assert report.args[0] == 42
    assert report.kwargs["reply_markup"] == tb.get_main_keyboard("ru")
    assert bot.user_states[42] == "chat"
