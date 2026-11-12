"""Revocation is isolated, retryable, and cannot race active user work."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import telegram_bot as tb
from i18n import CONSENT_VERSION, t
from privacy import ConsentMiddleware
from app.features.filmframe import gate, logger as film_logger
from app.features.music.feature import Music
from code_reviewer import UpdateCache


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("USER_PREFS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("ROI_MUSIC_DB", str(tmp_path / "music.sqlite"))
    monkeypatch.setattr(film_logger, "FILM_FRAME_LOG_FILE", tmp_path / "film_frame_runs.jsonl")
    bot = tb.TelegramTherapistBot.__new__(tb.TelegramTherapistBot)
    for name in (
        "user_consents", "user_langs", "user_states", "sessions", "temp_associations",
        "user_locks", "user_reset_epochs", "user_ask_prob", "user_meaning_enabled",
        "user_meaning_history", "user_meaning_last_time", "user_meaning_count",
        "user_first_seen", "user_last_active", "user_summaries", "user_message_counts",
        "user_usernames", "user_recent_messages", "blocked_users", "user_consecutive_messages",
        "filmframe_state", "message_queue", "silence_until", "last_update_notification",
    ):
        setattr(bot, name, {})
    bot.processed_flood_users = set()
    bot.prefs_path = tmp_path / "user_prefs.json"
    bot._prefs_loaded = True
    bot.admin_id = 42
    bot.bot = SimpleNamespace(send_message=AsyncMock())
    bot.music = Music(bot)
    bot.update_cache = UpdateCache(tmp_path)
    bot.update_check_lock = asyncio.Lock()
    bot.update_broadcast_lock = asyncio.Lock()
    yield bot
    gate._user_daily_counts.clear()
    gate._user_daily_reset.clear()


def message(text="/privacy revoke", uid=7, lang="ru"):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=uid, first_name="Alex", language_code=lang, is_bot=False, username=None),
        chat=SimpleNamespace(id=uid, type="private"), text=text,
        answer=AsyncMock(), answer_document=AsyncMock(), edit_reply_markup=AsyncMock(),
    )


def consent(owner, uid=7):
    owner.user_consents[uid] = {"version": CONSENT_VERSION, "accepted_at": "now"}
    owner.user_langs[uid] = "ru"


async def prompt(owner, uid=7):
    msg = message(uid=uid)
    await ConsentMiddleware(owner)(AsyncMock(), msg, {})
    buttons = msg.answer.await_args.kwargs["reply_markup"].inline_keyboard
    return buttons


def event(data, uid=7):
    msg = message(uid=uid)
    return SimpleNamespace(from_user=msg.from_user, message=msg, data=data, answer=AsyncMock())


@pytest.mark.asyncio
async def test_document_prompt_cancel_stale_and_foreign(owner):
    consent(owner)
    for lang in ("ru", "en"):
        doc = message("/privacy", uid=9, lang=lang)
        await ConsentMiddleware(owner)(AsyncMock(), doc, {})
        assert "/privacy revoke" in doc.answer_document.await_args.args[0].data.decode()
    buttons = await prompt(owner)
    assert [row[0].text for row in buttons] == ["Подтверждаю", "Вернуться"]
    foreign = event(buttons[0][0].callback_data, uid=8)
    await ConsentMiddleware(owner)(AsyncMock(), foreign, {})
    assert owner._has_consent(7)
    cancel = event(buttons[1][0].callback_data)
    await ConsentMiddleware(owner)(AsyncMock(), cancel, {})
    assert owner._has_consent(7)
    assert cancel.message.answer.await_args.args[0] == t("ru", "privacy_revoke_cancelled")
    repeat = event(buttons[0][0].callback_data)
    await ConsentMiddleware(owner)(AsyncMock(), repeat, {})
    assert repeat.answer.await_args.kwargs["show_alert"] is True
    assert owner._has_consent(7)


@pytest.mark.asyncio
async def test_confirm_deletes_all_local_stores_and_preserves_other_user(owner, tmp_path):
    for uid in (7, 8):
        consent(owner, uid)
        owner.user_recent_messages[uid] = [{"role": "user", "content": f"private {uid}"}]
        owner.user_summaries[uid] = f"summary {uid}"
        owner.sessions[uid] = object()
        owner.temp_associations[uid] = {"death": ["x"]}
        owner.filmframe_state[uid] = {"scene": "x"}
        owner.message_queue[uid] = [1]
        owner.user_meaning_history[uid] = ["x"]
        owner.processed_flood_users.add(uid)
        gate._user_daily_counts[uid] = 1
        gate._user_daily_reset[uid] = "today"
        store = owner.music.store()
        store.set_initiative(uid, True)
        store.button(uid)
        with store.connect() as db:
            db.execute("INSERT INTO music_delivery VALUES (?, ?, 'sent')", (store.user(uid), str(uid)))
        film_logger.log_run(uid, "description", "preview", "prompt", "model", None, 1, "success")
    owner.update_cache._write({"_update_state": {"version": 1, "pending": {
        "recipients": {"7": {"status": "sent"}, "8": {"status": "sent"}}},
        "history": [{"recipients": {"7": {}, "8": {}}}]}})
    export = tmp_path / "users_stats_1.txt"
    export.write_text("TELEGRAM BOT USERS EXPORT\n7\tprivate\n8\tother\n", encoding="utf-8")
    temp_audio = tmp_path / "temp_audio"
    temp_audio.mkdir()
    for name in ("incoming-7-abcdefgh", "response-7-abcdefgh", "incoming-8-abcdefgh"):
        (temp_audio / name).mkdir()
        (temp_audio / name / "audio").write_bytes(b"audio")
    (temp_audio / "permanent.mp3").write_bytes(b"music")
    owner._save_user_prefs(force=True)
    buttons = await prompt(owner)
    confirm = event(buttons[0][0].callback_data)
    await ConsentMiddleware(owner)(AsyncMock(), confirm, {})
    assert confirm.message.answer.await_args.args[0] == t("ru", "privacy_revoke_done")
    assert not owner._has_consent(7) and owner._has_consent(8)
    for name in ("user_recent_messages", "user_summaries", "sessions", "filmframe_state",
                 "message_queue", "temp_associations", "user_meaning_history", "user_langs"):
        assert 7 not in getattr(owner, name)
        assert 8 in getattr(owner, name)
    assert 7 not in gate._user_daily_counts and 8 in gate._user_daily_counts
    assert 7 not in owner.processed_flood_users and 8 in owner.processed_flood_users
    assert [json.loads(line)["user_id"] for line in film_logger.FILM_FRAME_LOG_FILE.read_text(encoding="utf-8").splitlines()] == [8]
    assert "7\t" not in export.read_text(encoding="utf-8") and "8\t" in export.read_text(encoding="utf-8")
    assert not (temp_audio / "incoming-7-abcdefgh").exists()
    assert not (temp_audio / "response-7-abcdefgh").exists()
    assert (temp_audio / "incoming-8-abcdefgh" / "audio").exists()
    assert (temp_audio / "permanent.mp3").read_bytes() == b"music"
    state = owner.update_cache._read()["_update_state"]
    assert set(state["pending"]["recipients"]) == {"8"}
    assert set(state["history"][0]["recipients"]) == {"8"}
    with store.connect() as db:
        for table in ("music_user", "music_button", "music_delivery"):
            assert not db.execute(f"SELECT * FROM {table} WHERE user_key=?", (store.user(7),)).fetchall()
            assert db.execute(f"SELECT * FROM {table} WHERE user_key=?", (store.user(8),)).fetchall()
    owner._load_user_prefs()
    assert not owner._has_consent(7) and owner._has_consent(8)
    assert 7 not in owner.user_recent_messages
    stale = event(buttons[0][0].callback_data)
    await ConsentMiddleware(owner)(AsyncMock(), stale, {})
    assert stale.answer.await_args.kwargs["show_alert"] is True


@pytest.mark.asyncio
async def test_failure_blocks_until_retry_and_survives_restart(owner, monkeypatch):
    consent(owner)
    owner.user_recent_messages[7] = [{"role": "user", "content": "secret"}]
    owner._save_user_prefs(force=True)
    original = film_logger.delete_user
    monkeypatch.setattr(film_logger, "delete_user", lambda uid: (_ for _ in ()).throw(OSError()))
    buttons = await prompt(owner)
    confirm = event(buttons[0][0].callback_data)
    await ConsentMiddleware(owner)(AsyncMock(), confirm, {})
    assert confirm.message.answer.await_args.args[0] == t("ru", "privacy_revoke_failed")
    assert not owner._has_consent(7)
    owner.user_consents = {}
    owner._privacy_pending = {}
    owner._load_user_prefs()
    assert 7 in owner._privacy_pending and not owner._has_consent(7)
    blocked = message("/start")
    await ConsentMiddleware(owner)(AsyncMock(), blocked, {})
    assert blocked.answer.await_args.args[0] == t("ru", "privacy_revoke_busy")
    monkeypatch.setattr(film_logger, "delete_user", original)
    buttons = await prompt(owner)
    retry = event(buttons[0][0].callback_data)
    await ConsentMiddleware(owner)(AsyncMock(), retry, {})
    assert retry.message.answer.await_args.args[0] == t("ru", "privacy_revoke_done")
    assert 7 not in owner.user_recent_messages
    accepted = event(f"consent:yes:{CONSENT_VERSION}")
    await ConsentMiddleware(owner)(AsyncMock(), accepted, {})
    assert owner._has_consent(7) and 7 not in owner.user_recent_messages


@pytest.mark.asyncio
async def test_revocation_waits_for_active_work_and_rejects_new_work(owner):
    consent(owner)
    owner._save_user_prefs(force=True)
    started, release = asyncio.Event(), asyncio.Event()

    async def handler(msg, data):
        started.set()
        await release.wait()
        owner.user_recent_messages[7] = [{"role": "user", "content": "late"}]
        owner._save_user_prefs(force=True)

    running = asyncio.create_task(ConsentMiddleware(owner)(handler, message("personal"), {}))
    await started.wait()
    buttons = await prompt(owner)
    confirm = event(buttons[0][0].callback_data)
    deleting = asyncio.create_task(ConsentMiddleware(owner)(AsyncMock(), confirm, {}))
    await asyncio.sleep(0.01)
    assert not deleting.done()
    blocked = message("another")
    next_handler = AsyncMock()
    await ConsentMiddleware(owner)(next_handler, blocked, {})
    next_handler.assert_not_awaited()
    release.set()
    await asyncio.gather(running, deleting)
    assert 7 not in owner.user_recent_messages
    owner._load_user_prefs()
    assert 7 not in owner.user_recent_messages and not owner._has_consent(7)


@pytest.mark.asyncio
async def test_failure_to_save_pending_never_claims_success(owner, monkeypatch):
    consent(owner)
    owner.user_recent_messages[7] = [{"role": "user", "content": "secret"}]
    monkeypatch.setattr(owner, "_save_user_prefs", lambda **kw: False)
    confirm = event((await prompt(owner))[0][0].callback_data)
    await ConsentMiddleware(owner)(AsyncMock(), confirm, {})
    assert confirm.message.answer.await_args.args[0] == t("ru", "privacy_revoke_failed")
    assert not owner._has_consent(7)
    assert owner.user_recent_messages[7][0]["content"] == "secret"


@pytest.mark.asyncio
async def test_expired_revoke_button_is_rejected(owner):
    consent(owner)
    buttons = await prompt(owner)
    token = buttons[0][0].callback_data.split(":")[2]
    owner._privacy_tokens[token] = (7, -1e50)
    expired = event(buttons[0][0].callback_data)
    await ConsentMiddleware(owner)(AsyncMock(), expired, {})
    assert expired.answer.await_args.kwargs["show_alert"] is True
    assert owner._has_consent(7)


@pytest.mark.asyncio
async def test_confirmation_is_consumed_before_concurrent_callback(owner):
    consent(owner)
    owner._save_user_prefs(force=True)
    started, release = asyncio.Event(), asyncio.Event()
    async def handler(msg, data):
        started.set()
        await release.wait()
    active = asyncio.create_task(ConsentMiddleware(owner)(handler, message("hello"), {}))
    await started.wait()
    data = (await prompt(owner))[0][0].callback_data
    first, second = event(data), event(data)
    one = asyncio.create_task(ConsentMiddleware(owner)(AsyncMock(), first, {}))
    await asyncio.sleep(0)
    await ConsentMiddleware(owner)(AsyncMock(), second, {})
    assert second.answer.await_args.kwargs["show_alert"] is True
    release.set()
    await asyncio.gather(active, one)
    assert first.message.answer.await_args.args[0] == t("ru", "privacy_revoke_done")


@pytest.mark.asyncio
async def test_fresh_consent_after_deletion_starts_without_old_history(owner, monkeypatch):
    consent(owner)
    owner.user_recent_messages[7] = [{"role": "user", "content": "old story"}]
    owner.sessions[7] = object()
    owner._save_user_prefs(force=True)
    confirm = event((await prompt(owner))[0][0].callback_data)
    await ConsentMiddleware(owner)(AsyncMock(), confirm, {})
    start = message("/start")
    handler = AsyncMock()
    await ConsentMiddleware(owner)(handler, start, {})
    handler.assert_not_awaited()
    accepted = event(f"consent:yes:{CONSENT_VERSION}")
    await ConsentMiddleware(owner)(handler, accepted, {})
    assert owner._has_consent(7)
    owner.ask_question_prob = 0.5
    owner.llm_model = owner.llm_analysis_model = "test"
    owner.llm_api_key = owner.llm_api_base = None
    owner.use_rag = False
    monkeypatch.setattr(tb, "ExistentialTherapistBot", lambda **kw: SimpleNamespace(history=[]))
    assert owner._get_therapist(7).history == []


@pytest.mark.asyncio
async def test_failure_to_save_final_deletion_leaves_durable_pending(owner, monkeypatch):
    consent(owner)
    owner.user_recent_messages[7] = [{"role": "user", "content": "secret"}]
    owner._save_user_prefs(force=True)
    real_save = owner._save_user_prefs
    count = 0
    def save(*args, **kwargs):
        nonlocal count
        count += 1
        return real_save(*args, **kwargs) if count == 1 else False
    monkeypatch.setattr(owner, "_save_user_prefs", save)
    confirm = event((await prompt(owner))[0][0].callback_data)
    await ConsentMiddleware(owner)(AsyncMock(), confirm, {})
    assert confirm.message.answer.await_args.args[0] == t("ru", "privacy_revoke_failed")
    owner._privacy_pending = {}
    owner._load_user_prefs()
    assert 7 in owner._privacy_pending and not owner._has_consent(7)


@pytest.mark.asyncio
async def test_revocation_waits_for_background_daily_work(owner):
    consent(owner)
    owner._save_user_prefs(force=True)
    started, release = asyncio.Event(), asyncio.Event()
    async def daily(uid, chat_id):
        started.set()
        await release.wait()
        owner.user_meaning_history[uid] = ["late"]
        owner._save_user_prefs(force=True)
    owner._send_daily_meaning_if_due = daily
    running = asyncio.create_task(owner._check_and_send_daily_meaning(7, 7))
    await started.wait()
    confirm = event((await prompt(owner))[0][0].callback_data)
    deleting = asyncio.create_task(ConsentMiddleware(owner)(AsyncMock(), confirm, {}))
    await asyncio.sleep(0.01)
    assert not deleting.done()
    release.set()
    await asyncio.gather(running, deleting)
    assert 7 not in owner.user_meaning_history


@pytest.mark.asyncio
async def test_unrecognizable_film_log_cannot_yield_false_success(owner):
    consent(owner)
    owner._save_user_prefs(force=True)
    film_logger.FILM_FRAME_LOG_FILE.write_text("not-json\n", encoding="utf-8")
    confirm = event((await prompt(owner))[0][0].callback_data)
    await ConsentMiddleware(owner)(AsyncMock(), confirm, {})
    assert confirm.message.answer.await_args.args[0] == t("ru", "privacy_revoke_failed")
    assert 7 in owner._privacy_pending
    assert film_logger.FILM_FRAME_LOG_FILE.read_text(encoding="utf-8") == "not-json\n"


@pytest.mark.asyncio
async def test_actual_dispatcher_routes_revoke(owner):
    from aiogram import Bot, Dispatcher
    from aiogram.types import CallbackQuery, Message, Update
    from unittest.mock import patch
    from datetime import datetime

    consent(owner)
    owner._save_user_prefs(force=True)
    dp = Dispatcher()
    middleware = ConsentMiddleware(owner)
    dp.message.outer_middleware(middleware)
    dp.callback_query.outer_middleware(middleware)
    routed = AsyncMock()
    async def handler(event, **kwargs):
        await routed(event)
    dp.message.register(handler)
    dp.callback_query.register(handler)
    transport = Bot("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi")
    raw_user = {"id": 7, "first_name": "Alex", "is_bot": False, "language_code": "ru"}
    raw_message = {"message_id": 1, "date": int(datetime.now().timestamp()),
                   "chat": {"id": 7, "type": "private"}, "from": raw_user,
                   "text": "/privacy revoke"}
    try:
        with patch.object(Message, "answer", new_callable=AsyncMock) as answer, \
             patch.object(Message, "edit_reply_markup", new_callable=AsyncMock), \
             patch.object(CallbackQuery, "answer", new_callable=AsyncMock):
            await dp.feed_update(transport, Update.model_validate({"update_id": 1, "message": raw_message}))
            keyboard = answer.await_args.kwargs["reply_markup"].inline_keyboard
            confirm_data = keyboard[0][0].callback_data
            await dp.feed_update(transport, Update.model_validate({"update_id": 2, "callback_query": {
                "id": "cb", "from": raw_user, "chat_instance": "instance",
                "data": confirm_data, "message": raw_message}}))
            assert answer.await_args.args[0] == t("ru", "privacy_revoke_done")
            routed.assert_not_awaited()
            assert not owner._has_consent(7)
    finally:
        await transport.session.close()


@pytest.mark.asyncio
async def test_recover_cannot_restore_username_after_revocation(owner):
    from aiogram import Bot, Dispatcher
    from aiogram.types import Message, Update
    from unittest.mock import patch
    from datetime import datetime

    consent(owner, 7)
    consent(owner, 42)
    owner._save_user_prefs(force=True)
    started, release = asyncio.Event(), asyncio.Event()
    async def get_chat(uid):
        if uid == 7:
            started.set()
            await release.wait()
        return SimpleNamespace(username=f"user{uid}")
    owner.bot.get_chat = get_chat
    owner.dp = Dispatcher()
    middleware = ConsentMiddleware(owner)
    owner.dp.message.outer_middleware(middleware)
    owner.dp.callback_query.outer_middleware(middleware)
    owner._register_handlers()
    transport = Bot("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi")
    raw = {"update_id": 1, "message": {
        "message_id": 1, "date": int(datetime.now().timestamp()),
        "chat": {"id": 42, "type": "private"},
        "from": {"id": 42, "first_name": "Admin", "is_bot": False, "language_code": "ru"},
        "text": "/recover"}}
    try:
        with patch.object(Message, "answer", new_callable=AsyncMock):
            recovering = asyncio.create_task(owner.dp.feed_update(transport, Update.model_validate(raw)))
            await started.wait()
            confirm = event((await prompt(owner))[0][0].callback_data)
            deleting = asyncio.create_task(middleware(AsyncMock(), confirm, {}))
            await asyncio.sleep(0.01)
            assert not deleting.done()
            release.set()
            await asyncio.gather(recovering, deleting)
            assert 7 not in owner.user_usernames
            assert owner.user_usernames[42] == "user42"
            owner._load_user_prefs()
            assert 7 not in owner.user_usernames
    finally:
        release.set()
        await transport.session.close()


@pytest.mark.asyncio
async def test_stats_snapshot_finishes_before_revocation_succeeds(owner):
    consent(owner, 7)
    consent(owner, 42)
    owner.user_usernames[7] = "private"
    owner._save_user_prefs(force=True)
    started, release = asyncio.Event(), asyncio.Event()
    stats = message("/stats", uid=42)
    async def send_stats(*args, **kwargs):
        started.set()
        await release.wait()
    stats.answer.side_effect = send_stats
    running = asyncio.create_task(owner._handle_stats(stats))
    await started.wait()
    confirm = event((await prompt(owner))[0][0].callback_data)
    deleting = asyncio.create_task(ConsentMiddleware(owner)(AsyncMock(), confirm, {}))
    await asyncio.sleep(0.01)
    assert not deleting.done()
    release.set()
    await asyncio.gather(running, deleting)
    assert confirm.message.answer.await_args.args[0] == t("ru", "privacy_revoke_done")
    assert 7 not in owner.user_langs and 7 not in owner.user_usernames


@pytest.mark.asyncio
async def test_stats_does_not_include_user_awaiting_deletion(owner):
    consent(owner, 7)
    consent(owner, 42)
    owner.user_usernames[7] = "private"
    owner._privacy_pending = {7: True}
    stats = message("/stats", uid=42)
    await owner._handle_stats(stats)
    text = stats.answer.await_args.args[0]
    assert "• 7 " not in text and "private" not in text
    assert "• 42 " in text
