"""Onboarding decisions must actually gate every Telegram feature."""

from datetime import datetime
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
    ("/start", {}), ("something personal", {}), ("/assoc", {}), ("/analyze", {}),
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
    assert "О чём" in event.message.answer.await_args.args[0]
    assert "accepted_at" in owner.user_consents[7]
    owner.user_consents = {}
    owner._load_user_prefs()
    assert owner._has_consent(7)
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
        assert document.filename.endswith(f"{lang}.txt")
    assert not owner.user_consents
    assert not owner.prefs_path.exists()


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
