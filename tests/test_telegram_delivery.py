import asyncio
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from cryptography.fernet import Fernet

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import telegram_bot as tb


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setenv("USER_PREFS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    bot = tb.TelegramTherapistBot.__new__(tb.TelegramTherapistBot)
    for name in (
        "sessions", "user_langs", "user_states", "user_consents", "user_ask_prob",
        "user_meaning_enabled", "user_meaning_history", "user_meaning_last_time",
        "user_meaning_count", "user_first_seen", "user_last_active", "user_summaries",
        "user_message_counts", "user_usernames", "user_recent_messages", "blocked_users",
        "user_consecutive_messages", "message_queue",
    ):
        setattr(bot, name, {})
    bot.bot = SimpleNamespace(id=123, send_chat_action=AsyncMock())
    bot.prefs_path = tmp_path / "data" / "prefs.json"
    bot._prefs_loaded = True
    return bot


def message(sent=None):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=7, username="alice"),
        chat=SimpleNamespace(id=7),
        answer=AsyncMock(return_value=sent or SimpleNamespace(edit_text=AsyncMock())),
    )


class Therapist:
    def __init__(self, chunks):
        self.history = []
        self.chunks = chunks
        self.system_prompt = "system"

    def chat_stream(self, text):
        for chunk in self.chunks:
            yield chunk
        self.history.extend([tb.Message(role="user", content=text),
                             tb.Message(role="assistant", content="".join(self.chunks))])

    def reset(self):
        self.history = []


@pytest.mark.asyncio
async def test_chat_commits_delivered_text_and_survives_restart(owner):
    therapist = Therapist(["one two three four"])
    owner.sessions[7] = therapist
    msg = message()
    await owner._handle_chat(msg, "hello")
    assert [x.content for x in therapist.history] == ["hello", "one two three four"]
    owner.user_recent_messages = {}
    owner._load_user_prefs()
    assert [x["content"] for x in owner.user_recent_messages[7]] == ["hello", "one two three four"]
    await owner._handle_reset(msg)
    owner._load_user_prefs()
    assert owner.user_recent_messages[7] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("chunks", [[], ["partial", "Ошибка: API failure"]])
async def test_failed_generation_does_not_commit(owner, chunks):
    therapist = Therapist(chunks)
    owner.sessions[7] = therapist
    msg = message()
    await owner._handle_chat(msg, "hello")
    assert therapist.history == []
    assert 7 not in owner.user_recent_messages
    assert msg.answer.return_value.edit_text.await_args.args[0] == tb.t(tb.DEFAULT_LANG, "error_llm")


@pytest.mark.asyncio
async def test_edit_failure_falls_back_to_new_message(owner):
    sent = SimpleNamespace(edit_text=AsyncMock(side_effect=RuntimeError("edit failed")))
    msg = message(sent)
    owner.sessions[7] = Therapist(["a complete response"])
    await owner._handle_chat(msg, "hello")
    assert msg.answer.await_args.args[0] == "a complete response"
    assert owner.user_recent_messages[7][-1]["content"] == "a complete response"


@pytest.mark.asyncio
async def test_partial_long_delivery_is_the_history(owner):
    sent = SimpleNamespace(edit_text=AsyncMock())
    msg = message(sent)
    msg.answer.side_effect = [sent, RuntimeError("placeholder failed"),
                              RuntimeError("send failed"), True]
    owner.sessions[7] = Therapist(["x" * 4100])
    await owner._handle_chat(msg, "hello")
    assert sent.edit_text.await_args.args[0] == "x" * 4096
    assert owner.user_recent_messages[7][-1]["content"] == "x" * 4096
    assert owner.sessions[7].history[-1].content == "x" * 4096


@pytest.mark.asyncio
async def test_remarque_uses_context_and_analysis_model_without_mutation(owner):
    therapist = Therapist([])
    therapist.history = [tb.Message(role="user", content="A quiet day")]
    therapist.generate_response = Mock(return_value="A quiet moment.")
    owner.sessions[7] = therapist
    await owner._handle_remarque(message())
    args, kwargs = therapist.generate_response.call_args
    assert "A quiet day" in args[0]
    assert kwargs["temporary_system_instruction"] == "system"
    assert kwargs["use_analysis_model"] is True
    assert [m.content for m in therapist.history] == ["A quiet day"]


@pytest.mark.asyncio
async def test_reset_waits_for_active_turn(owner):
    started, release = asyncio.Event(), asyncio.Event()
    therapist = Therapist([])
    owner.sessions[7] = therapist

    async def generation(*args):
        started.set()
        await release.wait()
        return "answer", ["answer"]

    owner._smooth_stream = generation
    chat = asyncio.create_task(owner._handle_chat(message(), "hello"))
    await started.wait()
    reset = asyncio.create_task(owner._handle_reset(message()))
    await asyncio.sleep(0)
    assert not reset.done()
    release.set()
    await asyncio.gather(chat, reset)
    owner._load_user_prefs()
    assert therapist.history == []
    assert owner.user_recent_messages[7] == []


def test_encryption_failure_preserves_existing_file_and_key_is_not_logged(owner, monkeypatch, capsys):
    owner.user_langs[7] = "en"
    assert owner._save_user_prefs(force=True)
    original = owner.prefs_path.read_text()
    key = os.environ["USER_PREFS_ENCRYPTION_KEY"]
    monkeypatch.delenv("USER_PREFS_ENCRYPTION_KEY")
    assert owner._save_user_prefs(force=True) is False
    assert owner.prefs_path.read_text() == original
    output = capsys.readouterr().out
    assert key not in output and key[:8] not in output and key[-8:] not in output


def test_legacy_prefs_migrate_without_losing_unknown_fields(owner):
    owner.prefs_path.parent.mkdir(parents=True)
    owner.prefs_path.write_text(json.dumps({"user_langs": {"7": "en"}, "future": {"value": 3}}))
    owner._load_user_prefs()
    assert owner._save_user_prefs(force=True)
    contents = tb.decrypt_data(owner.prefs_path.read_text(), tb.get_encryption_key())
    assert json.loads(contents)["future"] == {"value": 3}


def test_split_preserves_text_and_limits_unbroken_words(owner):
    text = "a" * 4100 + "\n" + "b" * 4001
    chunks = owner._split_text(text)
    assert "".join(chunks) == text
    assert all(len(chunk) <= 4096 for chunk in chunks)


@pytest.mark.parametrize(
    "env_chat,env_analysis,explicit_chat,explicit_analysis,expected",
    [
        (None, None, None, None, ("deepseek-v4.1-flash", "deepseek-v4.1-flash")),
        ("env-chat", None, None, None, ("env-chat", "env-chat")),
        ("env-chat", "env-analysis", "cli-chat", None, ("cli-chat", "env-analysis")),
        ("env-chat", "env-analysis", "cli-chat", "cli-analysis", ("cli-chat", "cli-analysis")),
    ],
)
def test_model_selection_and_command_registration(monkeypatch, env_chat, env_analysis,
                                                  explicit_chat, explicit_analysis, expected):
    for name, value in (("OPENAI_MODEL", env_chat), ("OPENAI_ANALYSIS_MODEL", env_analysis)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    monkeypatch.setattr(tb.TelegramTherapistBot, "_load_user_prefs",
                        lambda bot: setattr(bot, "_prefs_loaded", True))
    bot = tb.TelegramTherapistBot("123456:TEST", llm_model=explicit_chat,
                                  llm_analysis_model=explicit_analysis, use_rag=False)
    assert (bot.llm_model, bot.llm_analysis_model) == expected
    commands = {
        command
        for handler in bot.dp.message.handlers
        for filt in handler.filters
        for command in getattr(filt.callback, "commands", ())
    }
    assert "lysis" in commands and "assoc" in commands and "remarque" in commands
    assert "analyze" not in commands and "look" not in commands
    for lang in ("en", "ru"):
        assert "/lysis" in tb.t(lang, "help")
        assert "/analyze" not in tb.t(lang, "help")


def test_session_restores_recent_dialogue(owner, monkeypatch):
    owner.user_recent_messages[7] = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "welcome"},
    ]
    owner.llm_model = "chat"
    owner.llm_analysis_model = "analysis"
    owner.llm_api_key = owner.llm_api_base = None
    owner.use_rag = False
    owner.ask_question_prob = 0.37
    monkeypatch.setattr(tb, "ExistentialTherapistBot", lambda **kwargs: Therapist([]))
    session = owner._get_therapist(7)
    assert [(m.role, m.content) for m in session.history] == [
        ("user", "hello"), ("assistant", "welcome")
    ]


@pytest.mark.asyncio
async def test_generic_stream_reports_partial_generation_error(owner):
    def generator():
        yield "partial"
        yield "Ошибка анализа изображения: failed"
    with pytest.raises(RuntimeError):
        await owner._smooth_stream_generic(owner._stream_generic(generator))


@pytest.mark.asyncio
async def test_cancelled_chat_waits_for_worker_before_reset(owner):
    started, release = threading.Event(), threading.Event()
    therapist = Therapist([])

    def slow_stream(text):
        started.set()
        release.wait(timeout=5)
        therapist.history.append(tb.Message(role="assistant", content="late"))
        yield "late"

    therapist.chat_stream = slow_stream
    owner.sessions[7] = therapist
    chat = asyncio.create_task(owner._handle_chat(message(), "hello"))
    assert await asyncio.to_thread(started.wait, 2)
    chat.cancel()
    reset = asyncio.create_task(owner._handle_reset(message()))
    await asyncio.sleep(0.05)
    assert not reset.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await chat
    await reset
    assert therapist.history == []
    assert owner.user_recent_messages[7] == []


@pytest.mark.asyncio
async def test_story_generation_error_keeps_story_mode(owner):
    therapist = Therapist([])
    therapist.analyze_story_stream = lambda text: iter(["partial", "Ошибка: failed"])
    owner.sessions[7] = therapist
    owner.user_states[7] = "analyze_story"
    msg = message()
    await owner._handle_story_input(msg, "A sufficiently long story for analysis.")
    assert owner.user_states[7] == "analyze_story"
    assert msg.answer.return_value.edit_text.await_args.args[0] == tb.t(tb.DEFAULT_LANG, "error_generation_incomplete")


class FakeTelegram:
    """Track only API-confirmed, visible text."""
    def __init__(self):
        self.visible = []
        self.answer = AsyncMock(side_effect=self.send)
        self.from_user = SimpleNamespace(id=7, username="alice")
        self.chat = SimpleNamespace(id=7)

    async def send(self, text, **kwargs):
        item = FakeMessage(self, text)
        self.visible.append(item)
        return item

    def contents(self):
        return "".join(item.text for item in self.visible if not item.deleted)


class FakeMessage:
    def __init__(self, telegram, text="..."):
        self.telegram = telegram
        self.text = text
        self.deleted = False
        self.edit_text = AsyncMock(side_effect=self.edit)
        self.delete = AsyncMock(side_effect=self.remove)

    async def edit(self, text, **kwargs):
        self.text = text
        return self

    async def remove(self):
        self.deleted = True
        return True


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [2000, 3500, 4096, 5000, 9000])
@pytest.mark.parametrize("heading", ["", "Интерпретация:\n\n"])
async def test_delivery_lengths_are_lossless(owner, monkeypatch, size, heading):
    monkeypatch.setattr(tb.asyncio, "sleep", AsyncMock())
    telegram = FakeTelegram()
    first = await telegram.send("...")
    body = ("word abc_def <b> &amp; 😀\n\n" * (size // 25 + 1))[:size]
    text = heading + body
    assert await owner._deliver_response(telegram, first, text, [text]) == text
    assert telegram.contents() == text
    assert all(len(item.text.encode("utf-16-le")) // 2 <= 4096
               for item in telegram.visible if not item.deleted)
    assert all(call.kwargs.get("parse_mode") is None for call in first.edit_text.await_args_list)
    assert len(telegram.visible) == len(owner._split_text(text))


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [2000, 3500, 4096, 5000, 9000])
async def test_ascii_lengths_include_heading_in_limit(owner, monkeypatch, size):
    monkeypatch.setattr(tb.asyncio, "sleep", AsyncMock())
    telegram = FakeTelegram()
    first = await telegram.send("...")
    heading = "Интерпретация:\n\n"
    text = heading + "x" * (size - len(heading))
    await owner._deliver_response(telegram, first, text, [text])
    assert telegram.contents() == text
    assert len(telegram.visible) == (size + 4095) // 4096


def test_split_markup_boundaries_and_oversized_literal(owner):
    samples = [
        "x" * 4090 + "<b>bold &amp; text</b> abc_def",
        "x" * 4080 + '<a title="some\n\nwords">tail',
        "<tag " + "x" * 5000 + ">",
        "😀" * 2500 + " spaces\n\nand more",
        "x" * 4094 + "&amp; y",
    ]
    for text in samples:
        parts = owner._split_text(text)
        assert "".join(parts) == text
        assert all(len(part.encode("utf-16-le")) // 2 <= 4096 for part in parts)
    assert "<b>" in owner._split_text(samples[0])[0]
    assert owner._split_text(samples[1])[0] == "x" * 4080
    assert not owner._split_text(samples[-1])[0].endswith("&a")
    for prefix in owner._typing_prefixes('one two <a title="a b c d"> end more words'):
        assert not ("<a " in prefix and ">" not in prefix)


@pytest.mark.asyncio
async def test_typing_is_three_words_per_tick_after_generation(owner, monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(tb.asyncio, "sleep", sleep)
    generated = []

    async def source():
        yield "one two "
        generated.append("complete")
        yield "three four five six seven"

    text, shown = await owner._smooth_stream_generic(source())
    assert generated == ["complete"]
    assert shown == ["one two three", "one two three four five six", text]
    telegram = FakeTelegram()
    first = await telegram.send("...")
    await owner._deliver_response(telegram, first, text, shown)
    assert [c.args[0] for c in first.edit_text.await_args_list] == shown
    assert all(c.args == (0.12,) for c in sleep.await_args_list)


@pytest.mark.asyncio
async def test_edit_failure_deletes_preview_and_resends(owner, monkeypatch):
    monkeypatch.setattr(tb.asyncio, "sleep", AsyncMock())
    telegram = FakeTelegram()
    first = await telegram.send("...")
    first.edit_text.side_effect = [first, RuntimeError("secret edit")]
    first.text = "one two three"
    text = "one two three four five six seven"
    assert await owner._deliver_response(telegram, first, text, [text]) == text
    assert first.deleted
    assert telegram.contents() == text


@pytest.mark.asyncio
async def test_edit_failure_with_undeletable_preview_sends_only_suffix(owner, monkeypatch):
    monkeypatch.setattr(tb.asyncio, "sleep", AsyncMock())
    telegram = FakeTelegram()
    first = await telegram.send("...")
    async def edit(text, **kwargs):
        if first.text != "...":
            raise RuntimeError("secret edit")
        return await first.edit(text, **kwargs)
    first.edit_text.side_effect = edit
    first.delete.side_effect = RuntimeError("secret delete")
    text = "one two three four five six seven"
    assert await owner._deliver_response(telegram, first, text, [text]) == text
    assert telegram.contents() == text
    assert telegram.answer.await_args.args[0] == text[len("one two three"):]


@pytest.mark.asyncio
async def test_failed_fallback_keeps_confirmed_preview(owner, monkeypatch):
    monkeypatch.setattr(tb.asyncio, "sleep", AsyncMock())
    telegram = FakeTelegram()
    first = await telegram.send("...")
    async def edit(text, **kwargs):
        if first.text != "...":
            raise RuntimeError("edit failed")
        return await first.edit(text, **kwargs)
    first.edit_text.side_effect = edit
    first.delete.side_effect = RuntimeError("delete failed")
    telegram.answer.side_effect = RuntimeError("send failed")
    with pytest.raises(tb.DeliveryError) as error:
        await owner._deliver_response(telegram, first, "one two three four five six", [True])
    assert error.value.delivered == telegram.contents() == "one two three"


@pytest.mark.asyncio
async def test_failed_send_exposes_only_confirmed_prefix(owner, monkeypatch):
    monkeypatch.setattr(tb.asyncio, "sleep", AsyncMock())
    telegram = FakeTelegram()
    first = await telegram.send("...")
    telegram.answer.side_effect = RuntimeError("secret send")
    text = "x" * 5000
    with pytest.raises(tb.DeliveryError) as error:
        await owner._deliver_response(telegram, first, text, None)
    assert error.value.delivered == "x" * 4096 == telegram.contents()
    assert text[len(error.value.delivered):] == "x" * 904


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [None, False])
async def test_unconfirmed_api_result_is_failure(owner, result):
    telegram = FakeTelegram()
    telegram.answer.side_effect = None
    telegram.answer.return_value = result
    with pytest.raises(tb.DeliveryError) as error:
        await owner._deliver_response(telegram, None, "unconfirmed")
    assert error.value.delivered == ""


@pytest.mark.asyncio
async def test_retries_and_logs_are_bounded_and_private(owner, monkeypatch, caplog):
    sleep = AsyncMock()
    monkeypatch.setattr(tb.asyncio, "sleep", sleep)
    method = object()
    call = AsyncMock(side_effect=[
        tb.TelegramNetworkError(method, "secret private text"),
        tb.TelegramRetryAfter(method, "secret private text", retry_after=2),
        True,
    ])
    assert await owner._telegram_request("send_message", call, "private response", part_index=2)
    assert [c.args[0] for c in sleep.await_args_list] == [0.5, 2.0]
    assert "secret" not in caplog.text and "private response" not in caplog.text
    assert "TelegramNetworkError" in caplog.text and "part=2" in caplog.text
    call = AsyncMock(side_effect=tb.TelegramNetworkError(method, "secret"))
    with pytest.raises(tb.TelegramNetworkError):
        await owner._telegram_request("send_message", call, "private")
    assert call.await_count == 3
    call = AsyncMock(side_effect=tb.TelegramRetryAfter(method, "secret", retry_after=31))
    with pytest.raises(tb.TelegramRetryAfter):
        await owner._telegram_request("send_message", call, "private")
    assert call.await_count == 1


@pytest.mark.asyncio
async def test_transient_edit_and_send_retries_do_not_repeat_confirmed_parts(owner, monkeypatch):
    monkeypatch.setattr(tb.asyncio, "sleep", AsyncMock())
    telegram = FakeTelegram()
    first = await telegram.send("...")
    network_error = tb.TelegramNetworkError(object(), "secret")
    edit_attempts = 0
    async def edit(text, **kwargs):
        nonlocal edit_attempts
        edit_attempts += 1
        if edit_attempts == 1:
            raise network_error
        return await first.edit(text, **kwargs)
    first.edit_text.side_effect = edit
    send_attempts = 0
    async def send(text, **kwargs):
        nonlocal send_attempts
        send_attempts += 1
        if send_attempts == 1:
            raise network_error
        return await telegram.send(text, **kwargs)
    telegram.answer.side_effect = send
    text = "x" * 5000
    assert await owner._deliver_response(telegram, first, text) == text
    assert telegram.contents() == text
    assert edit_attempts == 2 and send_attempts == 2
    assert len(telegram.visible) == 2


@pytest.mark.asyncio
async def test_unchanged_edit_is_confirmed(owner):
    call = AsyncMock(side_effect=tb.TelegramBadRequest(object(), "message is not modified"))
    assert await owner._telegram_request("edit_text", call, "already visible") is True
    assert call.await_count == 1


@pytest.mark.asyncio
async def test_failure_notice_failure_is_not_silenced(owner):
    telegram = FakeTelegram()
    telegram.answer.side_effect = RuntimeError("secret")
    with pytest.raises(tb.DeliveryError, match="Telegram response delivery failed"):
        await owner._show_delivery_error(telegram, None, "delivery failed")


@pytest.mark.asyncio
async def test_failed_placeholder_still_allows_direct_delivery(owner, monkeypatch):
    monkeypatch.setattr(tb.asyncio, "sleep", AsyncMock())
    telegram = FakeTelegram()
    telegram.answer.side_effect = [RuntimeError("secret"), FakeMessage(telegram, "answer")]
    placeholder = await owner._new_delivery_placeholder(telegram)
    assert placeholder is None
    assert await owner._deliver_response(telegram, placeholder, "answer", ["answer"]) == "answer"
    assert [c.args[0] for c in telegram.answer.await_args_list] == ["...", "answer"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["assoc", "story"])
async def test_analysis_success_delivers_verbatim_and_clears_state(owner, monkeypatch, kind):
    monkeypatch.setattr(tb.asyncio, "sleep", AsyncMock())
    therapist = Therapist([])
    response = "abc_def <b>literal</b> &amp; **stars**\n- list " + "z" * 3500
    therapist.analyze_associations_stream = lambda associations: iter([response])
    therapist.analyze_story_stream = lambda story: iter([response])
    owner.sessions[7] = therapist
    owner.temp_associations = {7: {"freedom": ["<free>"], "nonsense": ["void"], "solitude": ["alone"]}}
    telegram = FakeTelegram()
    if kind == "assoc":
        owner.user_states[7] = "assoc_death"
        await owner._handle_assoc_input(telegram, "assoc_death", "death")
        assert 7 not in owner.temp_associations
        assert "&lt;free&gt;" in telegram.visible[0].text
        title = "Interpretation:\n\n"
    else:
        owner.user_states[7] = "analyze_story"
        await owner._handle_story_input(telegram, "A sufficiently long story for analysis.")
        title = "Existential response:\n\n"
    assert owner.user_states[7] == "chat"
    assert telegram.visible[-1].text == title + response
    assert len(telegram.visible) == (2 if kind == "assoc" else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["assoc", "story"])
@pytest.mark.parametrize("failure", ["generation", "delivery"])
async def test_analysis_failure_notice_and_state(owner, monkeypatch, kind, failure):
    monkeypatch.setattr(tb.asyncio, "sleep", AsyncMock())
    therapist = Therapist([])
    chunks = ["partial", "Ошибка: failed"] if failure == "generation" else ["x" * 5000]
    therapist.analyze_associations_stream = lambda associations: iter(chunks)
    therapist.analyze_story_stream = lambda story: iter(chunks)
    owner.sessions[7] = therapist
    owner.temp_associations = {7: {"freedom": ["free"], "nonsense": ["void"], "solitude": ["alone"]}}
    telegram = FakeTelegram()
    if failure == "delivery":
        placeholder_sent = False
        async def send(text, **kwargs):
            nonlocal placeholder_sent
            if len(text) > 100 or text == "...":
                if text == "..." and not placeholder_sent:
                    placeholder_sent = True
                    return await telegram.send(text, **kwargs)
                raise RuntimeError("secret")
            return await telegram.send(text, **kwargs)
        if kind == "assoc":
            async def send_assoc(text, **kwargs):
                if kwargs.get("parse_mode") == "HTML":
                    return await telegram.send(text, **kwargs)
                return await send(text, **kwargs)
            telegram.answer.side_effect = send_assoc
        else:
            telegram.answer.side_effect = send
    if kind == "assoc":
        owner.user_states[7] = "assoc_death"
        await owner._handle_assoc_input(telegram, "assoc_death", "death")
        assert owner.user_states[7] == "assoc_death"
        assert 7 in owner.temp_associations
    else:
        owner.user_states[7] = "analyze_story"
        await owner._handle_story_input(telegram, "A sufficiently long story for analysis.")
        assert owner.user_states[7] == "analyze_story"
    key = "error_generation_incomplete" if failure == "generation" else "error_delivery_partial"
    assert any(item.text == tb.t(tb.DEFAULT_LANG, key) for item in telegram.visible)
    if failure == "delivery":
        title = "Interpretation:\n\n" if kind == "assoc" else "Existential response:\n\n"
        assert any(item.text == (title + "x" * 5000)[:4096] for item in telegram.visible)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["assoc", "story"])
async def test_analysis_does_not_replay_long_answer_as_rapid_edits(owner, monkeypatch, kind):
    sleep = AsyncMock()
    monkeypatch.setattr(tb.asyncio, "sleep", sleep)
    therapist = Therapist([])
    response = "a detailed response with several words. " * 200
    therapist.analyze_story_stream = lambda story: iter([response])
    therapist.analyze_associations_stream = lambda associations: iter([response])
    owner.sessions[7] = therapist
    owner.temp_associations = {7: {"freedom": ["free"], "nonsense": ["void"], "solitude": ["alone"]}}
    telegram = FakeTelegram()
    if kind == "story":
        owner.user_states[7] = "analyze_story"
        await owner._handle_story_input(telegram, "A sufficiently long story for analysis.")
        title = "Existential response:\n\n"
        parts = telegram.visible
    else:
        owner.user_states[7] = "assoc_death"
        await owner._handle_assoc_input(telegram, "assoc_death", "death")
        title = "Interpretation:\n\n"
        parts = telegram.visible[1:]
    assert "".join(part.text for part in parts) == title + response
    edits = sum(part.edit_text.await_count for part in parts)
    if kind == "assoc":
        assert edits == 1
    else:
        assert 1 < edits <= 6
    if kind == "story":
        assert [call.args[0] for call in sleep.await_args_list] == [0.5] * (edits - 1)
    assert owner.user_states[7] == "chat"


@pytest.mark.asyncio
@pytest.mark.parametrize("rate_limited", [False, True])
async def test_story_animation_failure_delivers_complete_text(owner, monkeypatch, rate_limited):
    sleep = AsyncMock()
    monkeypatch.setattr(tb.asyncio, "sleep", sleep)
    therapist = Therapist([])
    response = "one two three four five six seven eight nine ten eleven twelve"
    therapist.analyze_story_stream = lambda story: iter([response])
    owner.sessions[7] = therapist
    owner.user_states[7] = "analyze_story"
    telegram = FakeTelegram()
    failure = (
        tb.TelegramRetryAfter(object(), "private", retry_after=3)
        if rate_limited else RuntimeError("private")
    )
    async def send(text, **kwargs):
        item = await telegram.send(text, **kwargs)
        async def edit(preview, **edit_kwargs):
            if item.edit_text.await_count == 1:
                raise failure
            return await item.edit(preview, **edit_kwargs)
        item.edit_text.side_effect = edit
        return item
    telegram.answer.side_effect = send

    await owner._handle_story_input(telegram, "A sufficiently long story for analysis.")

    assert telegram.contents() == "Existential response:\n\n" + response
    assert telegram.visible[0].edit_text.await_count == 2
    assert owner.user_states[7] == "chat"
    if rate_limited:
        sleep.assert_awaited_once_with(3.0)
    else:
        sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_story_dots_use_default_speed_and_rate_limit_skips_text_animation(owner, monkeypatch):
    from contextlib import asynccontextmanager

    therapist = Therapist([])
    response = "one two three four five six seven eight nine ten"
    therapist.analyze_story_stream = lambda story: iter([response])
    owner.sessions[7] = therapist
    owner.user_states[7] = "analyze_story"
    sleep = AsyncMock()
    monkeypatch.setattr(tb.asyncio, "sleep", sleep)
    @asynccontextmanager
    async def dots(sent, interval=0.37, *, stop_on_rate_limit=False):
        assert interval == 0.37
        assert stop_on_rate_limit
        yield tb._LoadingAnimationState(
            rate_limited=True, retry_deadline=tb.time.monotonic() + 3.0,
        )

    owner._animate_loading_dots = dots
    telegram = FakeTelegram()
    await owner._handle_story_input(telegram, "A sufficiently long story for analysis.")

    assert sleep.await_count == 1
    assert 2.9 <= sleep.await_args.args[0] <= 3.0
    assert telegram.contents() == "Existential response:\n\n" + response
    assert telegram.visible[0].edit_text.await_count == 1


@pytest.mark.asyncio
async def test_story_runs_real_fast_dots_then_delivers_answer(owner):
    therapist = Therapist([])

    def analyze(story):
        tb.time.sleep(1.4)
        yield "Reflection."

    therapist.analyze_story_stream = analyze
    owner.sessions[7] = therapist
    owner.user_states[7] = "analyze_story"
    telegram = FakeTelegram()

    await owner._handle_story_input(telegram, "A sufficiently long story for analysis.")

    edits = [call.args[0] for call in telegram.visible[0].edit_text.await_args_list]
    assert edits[:2] == ["..", "."]
    assert edits[-1] == "Existential response:\n\nReflection."
    assert owner.user_states[7] == "chat"


@pytest.mark.asyncio
async def test_story_failed_later_preview_jumps_to_full_text(owner, monkeypatch):
    monkeypatch.setattr(tb.asyncio, "sleep", AsyncMock())
    therapist = Therapist([])
    response = "one two three four five six seven eight nine ten eleven twelve"
    therapist.analyze_story_stream = lambda story: iter([response])
    owner.sessions[7] = therapist
    owner.user_states[7] = "analyze_story"
    telegram = FakeTelegram()

    async def send(text, **kwargs):
        item = await telegram.send(text, **kwargs)

        async def edit(preview, **edit_kwargs):
            if item.edit_text.await_count == 2:
                raise RuntimeError("private")
            return await item.edit(preview, **edit_kwargs)

        item.edit_text.side_effect = edit
        return item

    telegram.answer.side_effect = send
    await owner._handle_story_input(telegram, "A sufficiently long story for analysis.")

    assert telegram.contents() == "Existential response:\n\n" + response
    assert telegram.visible[0].edit_text.await_count == 3
    assert owner.user_states[7] == "chat"


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["short", "x" * 3001])
async def test_invalid_story_releases_queue_slot(owner, text):
    owner.message_queue[7] = [1]
    owner.user_states[7] = "analyze_story"
    await owner._handle_story_input(message(), text)
    assert owner.message_queue[7] == []
    assert owner.user_states[7] == "analyze_story"


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["ru", "en"])
async def test_story_end_to_end_with_real_analysis_stream(owner, language):
    from therapist_bot import ExistentialTherapistBot
    therapist = ExistentialTherapistBot(api_key="TEST", use_rag=False, language=language)
    therapist.client = Mock()
    response = "A complete reflection with enough detail."
    therapist.client.chat.completions.create.return_value = iter([
        SimpleNamespace(choices=[SimpleNamespace(
            delta=SimpleNamespace(content=response), finish_reason="stop")])
    ])
    owner.sessions[7] = therapist
    owner.user_langs[7] = language
    owner.user_states[7] = "analyze_story"
    telegram = FakeTelegram()
    await owner._handle_story_input(telegram, "A sufficiently long story for analysis.")
    title = "Экзистенциальный отклик:\n\n" if language == "ru" else "Existential response:\n\n"
    assert telegram.contents() == title + response
    assert owner.user_states[7] == "chat"
    assert therapist.client.chat.completions.create.call_args.kwargs["stream"] is True


@pytest.fixture
def fast_voice_actions(monkeypatch):
    record_voice = tb.ChatActionSender.record_voice
    def fast(**kwargs):
        assert kwargs["interval"] == 4.5
        kwargs["interval"] = 0.03
        return record_voice(**kwargs)
    monkeypatch.setattr(tb.ChatActionSender, "record_voice", fast)


def voice_message(user_id=7):
    msg = message()
    msg.from_user.id = user_id
    msg.chat.id = user_id
    msg.answer_voice = AsyncMock()
    return msg


def voice_therapist(started, release, outcome="success"):
    therapist = Therapist([])
    therapist.chat = lambda text: "spoken answer"
    def generate(text, output):
        started.set()
        release.wait(timeout=5)
        if outcome == "exception":
            raise RuntimeError("private")
        if outcome == "error":
            return "Ошибка генерации"
        Path(output).write_bytes(b"voice")
        return output
    therapist.generate_speech = generate
    return therapist


@pytest.mark.asyncio
async def test_voice_action_repeats_while_thread_runs_and_stops_before_delivery(owner, monkeypatch, tmp_path, fast_voice_actions):
    monkeypatch.chdir(tmp_path)
    started, release = threading.Event(), threading.Event()
    owner.sessions[7] = voice_therapist(started, release)
    msg = voice_message()
    task = asyncio.create_task(owner._handle_chat(msg, "hello", is_voice=True))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        await asyncio.sleep(0.12)
        actions = [c for c in owner.bot.send_chat_action.await_args_list if c.kwargs["action"] == "record_voice"]
        assert len(actions) >= 2
        assert all(c.kwargs["chat_id"] == 7 for c in actions)
        assert not task.done()
    finally:
        release.set()
    await task
    msg.answer_voice.assert_awaited_once()
    count = owner.bot.send_chat_action.await_count
    await asyncio.sleep(0.08)
    assert owner.bot.send_chat_action.await_count == count
    assert not (tmp_path / "temp_audio" / "response_7.mp3").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["error", "exception"])
async def test_voice_action_stops_on_generation_error(owner, monkeypatch, tmp_path, fast_voice_actions, outcome):
    monkeypatch.chdir(tmp_path)
    started, release = threading.Event(), threading.Event()
    owner.sessions[7] = voice_therapist(started, release, outcome)
    msg = voice_message()
    task = asyncio.create_task(owner._handle_chat(msg, "hello", is_voice=True))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        await asyncio.sleep(0.06)
    finally:
        release.set()
    await task
    msg.answer_voice.assert_not_awaited()
    count = owner.bot.send_chat_action.await_count
    await asyncio.sleep(0.08)
    assert owner.bot.send_chat_action.await_count == count


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "exception"])
async def test_cancelled_voice_stops_action_and_waits_for_worker_cleanup(owner, monkeypatch, tmp_path, fast_voice_actions, outcome):
    monkeypatch.chdir(tmp_path)
    started, release = threading.Event(), threading.Event()
    owner.sessions[7] = voice_therapist(started, release, outcome)
    msg = voice_message()
    task = asyncio.create_task(owner._handle_chat(msg, "hello", is_voice=True))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        await asyncio.sleep(0.06)
        task.cancel()
        await asyncio.sleep(0.06)
        count = owner.bot.send_chat_action.await_count
        await asyncio.sleep(0.08)
        assert owner.bot.send_chat_action.await_count == count
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    msg.answer_voice.assert_not_awaited()
    assert not (tmp_path / "temp_audio" / "response_7.mp3").exists()


@pytest.mark.asyncio
async def test_voice_action_continues_through_fable_fallback(owner, monkeypatch, tmp_path, fast_voice_actions):
    import tts_service as tts
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TTS_PROVIDER", "clone")
    monkeypatch.delenv("TTS_MODEL", raising=False)
    monkeypatch.delenv("TTS_FALLBACK_MODEL", raising=False)
    monkeypatch.delenv("TTS_FALLBACK_VOICE", raising=False)
    started, release = threading.Event(), threading.Event()
    def failed_clone(*args, **kwargs):
        started.set()
        release.wait(timeout=5)
        raise tts.CloneError("unavailable")
    monkeypatch.setattr(tts, "clone_speech", failed_clone)
    client = Mock()
    client.audio.speech.create.return_value.stream_to_file.side_effect = lambda path: Path(path).write_bytes(b"fable")
    therapist = Therapist([])
    therapist.chat = lambda text: "spoken answer"
    therapist.generate_speech = lambda text, path: tts.generate_speech(client, text, path)
    owner.sessions[7] = therapist
    msg = voice_message()
    task = asyncio.create_task(owner._handle_chat(msg, "hello", is_voice=True))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        await asyncio.sleep(0.08)
        assert sum(c.kwargs["action"] == "record_voice"
                   for c in owner.bot.send_chat_action.await_args_list) >= 2
    finally:
        release.set()
    await task
    client.audio.speech.create.assert_called_once_with(model="tts-1", voice="fable", input="spoken answer")
    msg.answer_voice.assert_awaited_once()
    count = owner.bot.send_chat_action.await_count
    await asyncio.sleep(0.08)
    assert owner.bot.send_chat_action.await_count == count


@pytest.mark.asyncio
async def test_voice_actions_are_independent_for_concurrent_users(owner, monkeypatch, tmp_path, fast_voice_actions):
    monkeypatch.chdir(tmp_path)
    started1, release1 = threading.Event(), threading.Event()
    started2, release2 = threading.Event(), threading.Event()
    owner.sessions[7] = voice_therapist(started1, release1)
    owner.sessions[8] = voice_therapist(started2, release2)
    tasks = [asyncio.create_task(owner._handle_chat(voice_message(user), "hello", is_voice=True))
             for user in (7, 8)]
    try:
        assert await asyncio.to_thread(started1.wait, 2)
        assert await asyncio.to_thread(started2.wait, 2)
        await asyncio.sleep(0.08)
        release1.set()
        await tasks[0]
        count1 = sum(c.kwargs["action"] == "record_voice" and c.kwargs["chat_id"] == 7
                     for c in owner.bot.send_chat_action.await_args_list)
        count2 = sum(c.kwargs["action"] == "record_voice" and c.kwargs["chat_id"] == 8
                     for c in owner.bot.send_chat_action.await_args_list)
        await asyncio.sleep(0.08)
        assert sum(c.kwargs["action"] == "record_voice" and c.kwargs["chat_id"] == 7
                   for c in owner.bot.send_chat_action.await_args_list) == count1
        assert sum(c.kwargs["action"] == "record_voice" and c.kwargs["chat_id"] == 8
                   for c in owner.bot.send_chat_action.await_args_list) > count2
    finally:
        release1.set()
        release2.set()
        await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_voice_action_failure_does_not_prevent_delivery(owner, monkeypatch, tmp_path, fast_voice_actions):
    monkeypatch.chdir(tmp_path)
    started, release = threading.Event(), threading.Event()
    owner.sessions[7] = voice_therapist(started, release)
    async def action(**kwargs):
        if kwargs["action"] == "record_voice":
            raise RuntimeError("private")
    owner.bot.send_chat_action.side_effect = action
    msg = voice_message()
    task = asyncio.create_task(owner._handle_chat(msg, "hello", is_voice=True))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        await asyncio.sleep(0.06)
    finally:
        release.set()
    await task
    msg.answer_voice.assert_awaited_once()
