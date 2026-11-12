"""Onboarding decisions must actually gate every Telegram feature."""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from cryptography.fernet import Fernet
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import telegram_bot as tb
from i18n import t
from privacy import CONSENT_VERSION, ConsentMiddleware


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setenv("USER_PREFS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    bot = tb.TelegramTherapistBot.__new__(tb.TelegramTherapistBot)
    for name in (
        "user_consents", "user_langs", "user_states", "sessions", "temp_associations",
        "user_ask_prob", "user_meaning_enabled", "user_meaning_history",
        "user_meaning_last_time", "user_meaning_count", "user_first_seen", "user_last_active",
        "user_summaries", "user_message_counts", "user_usernames", "user_recent_messages",
        "blocked_users", "user_consecutive_messages",
    ):
        setattr(bot, name, {})
    bot.prefs_path = tmp_path / "user_prefs.json"
    bot._prefs_loaded = True
    bot.admin_id = 42
    bot.bot = SimpleNamespace(send_message=AsyncMock())
    return bot


def message(text="/start", *, uid=7, lang="ru", chat_type="private", **extra):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=uid, first_name="Alex", language_code=lang, is_bot=False),
        chat=SimpleNamespace(id=uid if chat_type == "private" else -100, type=chat_type),
        text=text, answer=AsyncMock(), answer_document=AsyncMock(), edit_reply_markup=AsyncMock(),
        **extra,
    )


def callback(choice="yes", version=CONSENT_VERSION):
    msg = message()
    return SimpleNamespace(from_user=msg.from_user, message=msg,
                           data=f"consent:{choice}:{version}", answer=AsyncMock())


@pytest.mark.asyncio
@pytest.mark.parametrize("text,extra", [
    ("/start", {}), ("something personal", {}), ("/assoc", {}), ("/lysis", {}),
    ("/shoot", {}), ("/remarque", {}), ("/meaning", {}), ("/feedback personal", {}),
    (None, {"voice": object()}), (None, {"photo": [object()]}),
    (None, {"sticker": object()}),
])
async def test_no_feature_runs_before_acceptance(owner, text, extra):
    handler = AsyncMock()
    msg = message(text, **extra)
    await ConsentMiddleware(owner)(handler, msg, {})
    handler.assert_not_awaited()
    msg.answer.assert_awaited_once()
    notice = msg.answer.await_args.args[0]
    assert "Alex" in notice and "/privacy" in notice
    assert "С чего начнём?" not in notice
    buttons = msg.answer.await_args.kwargs["reply_markup"].inline_keyboard
    assert [row[0].text for row in buttons] == ["Да, давай начнём", "Нет, пока не готов"]
    assert not owner.prefs_path.exists()
    assert not owner.sessions


@pytest.mark.asyncio
async def test_acceptance_opens_access_and_survives_restart(owner):
    handler = AsyncMock()
    # A recent ordinary preference save must not swallow the consent write.
    owner._save_user_prefs(force=True)
    event = callback()
    await ConsentMiddleware(owner)(handler, event, {})
    assert owner._has_consent(7)
    assert t("ru", "welcome", name="Alex") in event.message.answer.await_args.args[0]
    started = owner.user_meaning_last_time[7]
    assert owner.user_meaning_enabled[7] is True
    assert "accepted_at" in owner.user_consents[7]
    owner.user_consents = {}
    owner.user_meaning_last_time = {}
    owner.user_meaning_enabled = {}
    owner._load_user_prefs()
    assert owner._has_consent(7)
    assert owner.user_meaning_last_time[7] == started
    assert owner.user_meaning_enabled[7] is True
    msg = message("let's talk")
    await ConsentMiddleware(owner)(handler, msg, {})
    handler.assert_awaited_once_with(msg, {})


@pytest.mark.asyncio
async def test_refusal_keeps_chat_closed_without_saving_a_profile(owner):
    handler = AsyncMock()
    event = callback("no")
    await ConsentMiddleware(owner)(handler, event, {})
    assert not owner._has_consent(7)
    assert "остановимся" in event.message.answer.await_args.args[0]
    assert not owner.prefs_path.exists()
    await ConsentMiddleware(owner)(handler, message("a personal story"), {})
    handler.assert_not_awaited()
    # A new /start can offer the choice again, and acceptance then works.
    await ConsentMiddleware(owner)(handler, message("/start"), {})
    await ConsentMiddleware(owner)(handler, callback(), {})
    assert owner._has_consent(7)


@pytest.mark.asyncio
async def test_failed_save_never_grants_access(owner):
    owner._prefs_loaded = False
    event = callback()
    await ConsentMiddleware(owner)(AsyncMock(), event, {})
    assert not owner._has_consent(7)
    assert not owner.user_langs
    assert not owner.user_meaning_enabled
    assert not owner.user_meaning_last_time
    assert not owner.user_meaning_history
    assert "Не получилось" in event.message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_disk_error_never_grants_access(owner, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(Path, "replace", fail)
    await ConsentMiddleware(owner)(AsyncMock(), callback(), {})
    assert not owner._has_consent(7)
    assert not owner.prefs_path.exists()


@pytest.mark.asyncio
async def test_old_version_and_other_users_cannot_reuse_acceptance(owner):
    event = callback(version="old")
    await ConsentMiddleware(owner)(AsyncMock(), event, {})
    assert not owner._has_consent(7)
    assert "устарела" in event.message.answer.await_args.args[0]
    await ConsentMiddleware(owner)(AsyncMock(), callback(), {})
    assert owner._has_consent(7)
    assert not owner._has_consent(8)
    owner.user_consents[7]["version"] = "old"
    assert not owner._has_consent(7)


@pytest.mark.asyncio
async def test_old_buttons_do_not_reverse_acceptance(owner):
    middleware = ConsentMiddleware(owner)
    await middleware(AsyncMock(), callback(), {})
    before = dict(owner.user_consents[7])
    await middleware(AsyncMock(), callback("no"), {})
    assert owner.user_consents[7] == before


@pytest.mark.asyncio
async def test_document_available_before_acceptance_in_both_languages(owner):
    for lang in ("ru", "en"):
        msg = message("/privacy", lang=lang)
        handler = AsyncMock()
        await ConsentMiddleware(owner)(handler, msg, {})
        handler.assert_not_awaited()
        document = msg.answer_document.await_args.args[0]
        assert document.data.decode("utf-8") == t(lang, "consent_details")
        assert CONSENT_VERSION in document.data.decode("utf-8")
        assert document.filename.endswith(f"{lang}.txt")
    assert not owner.user_consents
    assert not owner.prefs_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["ru", "en"])
async def test_onboarding_does_not_depend_on_welcome_wording(owner, monkeypatch, lang):
    from i18n import LOCALES

    monkeypatch.setitem(LOCALES[lang], "welcome", "A changed invitation, {name}: TALK NOW?")
    owner.user_langs[7] = lang
    msg = message("/start", lang=lang)
    await ConsentMiddleware(owner)(AsyncMock(), msg, {})
    assert "TALK NOW?" not in msg.answer.await_args.args[0]
    event = callback()
    await ConsentMiddleware(owner)(AsyncMock(), event, {})
    assert "A changed invitation, Alex: TALK NOW?" in event.message.answer.await_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["ru", "en"])
async def test_start_after_consent_restarts_timer_without_sending(owner, lang):
    owner.user_langs[7] = lang
    await ConsentMiddleware(owner)(AsyncMock(), callback(), {})
    owner._check_and_send_daily_meaning = AsyncMock()
    owner.user_meaning_last_time[7] = datetime.now() - timedelta(hours=23)
    owner.user_meaning_history[7] = ["old"]
    owner.user_meaning_count[7] = 3
    msg = message("/start", lang=lang)
    msg.from_user.username = None

    async def start_handler(event, data):
        await owner._handle_start(event)

    before = datetime.now()
    await ConsentMiddleware(owner)(start_handler, msg, {})
    assert msg.answer.await_args.args[0] == t(lang, "welcome", name="Alex")
    msg.answer.assert_awaited_once()
    assert owner.user_meaning_enabled[7] is True
    assert before <= owner.user_meaning_last_time[7] <= datetime.now()
    assert owner.user_meaning_history[7] == ["old"]
    assert owner.user_meaning_count[7] == 3
    owner._check_and_send_daily_meaning.assert_not_awaited()
    saved_time = owner.user_meaning_last_time[7]
    owner.user_meaning_last_time = {}
    owner._load_user_prefs()
    assert owner.user_meaning_last_time[7] == saved_time


@pytest.mark.asyncio
async def test_start_preserves_daily_opt_out(owner):
    await ConsentMiddleware(owner)(AsyncMock(), callback(), {})
    owner.user_meaning_enabled[7] = False
    msg = message()
    msg.from_user.username = None
    await owner._handle_start(msg)
    assert owner.user_meaning_enabled[7] is False
    await owner._check_and_send_daily_meaning(7, 7)
    owner.bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_daily_meaning_waits_24_hours_after_start(owner):
    await ConsentMiddleware(owner)(AsyncMock(), callback(), {})
    msg = message()
    msg.from_user.username = None
    owner.user_meaning_last_time[7] = datetime.now() - timedelta(days=2)
    await owner._handle_start(msg)
    therapist = SimpleNamespace(generate_response=Mock(return_value="A thought"))
    owner._get_therapist = Mock(return_value=therapist)
    await owner._check_and_send_daily_meaning(7, 7)
    therapist.generate_response.assert_not_called()
    owner.bot.send_message.assert_not_awaited()

    owner.user_meaning_last_time[7] -= timedelta(hours=24)
    await owner._check_and_send_daily_meaning(7, 7)
    therapist.generate_response.assert_called_once()
    owner.bot.send_message.assert_awaited_once()
    assert owner.user_meaning_count[7] == 1


@pytest.mark.asyncio
async def test_start_restarts_timer_after_inflight_daily_generation(owner):
    await ConsentMiddleware(owner)(AsyncMock(), callback(), {})
    started = asyncio.Event()
    release = asyncio.Event()

    async def daily(user_id, chat_id):
        started.set()
        await release.wait()
        owner.user_meaning_last_time[user_id] = datetime.now() - timedelta(hours=1)

    owner._send_daily_meaning_if_due = daily
    task = asyncio.create_task(owner._check_and_send_daily_meaning(7, 7))
    await started.wait()
    msg = message()
    msg.from_user.username = None
    start_task = asyncio.create_task(owner._handle_start(msg))
    await asyncio.sleep(0)
    assert not start_task.done()
    before = datetime.now()
    release.set()
    await asyncio.gather(task, start_task)
    assert owner.user_meaning_last_time[7] >= before


@pytest.mark.asyncio
async def test_group_message_does_not_open_a_session(owner):
    handler = AsyncMock()
    msg = message(chat_type="group")
    await ConsentMiddleware(owner)(handler, msg, {})
    handler.assert_not_awaited()
    assert "личном чате" in msg.answer.await_args.args[0]
    assert not owner.prefs_path.exists()


@pytest.mark.asyncio
async def test_daily_messages_do_not_call_ai_before_consent(owner):
    owner.user_meaning_enabled[7] = True
    owner._get_therapist = AsyncMock()
    await owner._check_and_send_daily_meaning(7, 7)
    owner._get_therapist.assert_not_called()


@pytest.mark.asyncio
async def test_dispatcher_gates_commands_and_callbacks(owner):
    """Use actual aiogram routing, not just calls to the middleware helper."""
    from aiogram import Bot, Dispatcher
    from aiogram.types import Update

    dp = Dispatcher()
    middleware = ConsentMiddleware(owner)
    dp.message.outer_middleware(middleware)
    dp.callback_query.outer_middleware(middleware)
    handler = AsyncMock()

    async def routed_handler(event, **data):
        await handler(event)

    dp.message.register(routed_handler)
    dp.callback_query.register(routed_handler)
    transport = Bot("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi")
    raw_user = {"id": 7, "first_name": "Alex", "is_bot": False, "language_code": "ru"}
    raw_message = {"message_id": 1, "date": int(datetime.now().timestamp()),
                   "chat": {"id": 7, "type": "private"}, "from": raw_user, "text": "/assoc"}
    try:
        update = Update.model_validate({"update_id": 1, "message": raw_message})
        # Replace the network-bound replies while retaining real update routing.
        from aiogram.types import Message, CallbackQuery
        from unittest.mock import patch
        with patch.object(Message, "answer", new_callable=AsyncMock), \
             patch.object(Message, "edit_reply_markup", new_callable=AsyncMock), \
             patch.object(CallbackQuery, "answer", new_callable=AsyncMock):
            await dp.feed_update(transport, update)
            handler.assert_not_awaited()
            accepted = Update.model_validate({"update_id": 2, "callback_query": {
                "id": "c1", "from": raw_user, "chat_instance": "instance",
                "data": f"consent:yes:{CONSENT_VERSION}", "message": raw_message}})
            await dp.feed_update(transport, accepted)
            assert owner._has_consent(7)
            await dp.feed_update(transport, update)
            handler.assert_awaited_once()
    finally:
        await transport.session.close()
