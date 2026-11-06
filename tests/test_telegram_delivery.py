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
    bot.bot = SimpleNamespace(send_chat_action=AsyncMock())
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
    msg.answer.side_effect = [sent, RuntimeError("send failed")]
    owner.sessions[7] = Therapist(["x" * 4100])
    await owner._handle_chat(msg, "hello")
    assert sent.edit_text.await_args.args[0] == "x" * 4000
    assert owner.user_recent_messages[7][-1]["content"] == "x" * 4000
    assert owner.sessions[7].history[-1].content == "x" * 4000


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
    assert all(len(chunk) <= 4000 for chunk in chunks)


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
    assert msg.answer.return_value.edit_text.await_args.args[0] == tb.t(tb.DEFAULT_LANG, "error_llm")
