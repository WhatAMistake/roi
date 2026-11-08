"""Music tests use fake Telegram calls; no audio files or network sends are created."""

import asyncio
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendAudio
from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import music
import telegram_bot as tb
from privacy import ConsentMiddleware


@pytest.fixture
def setup_music(tmp_path, monkeypatch):
    monkeypatch.setenv("USER_PREFS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("ROI_MUSIC_DB", str(tmp_path / "music.sqlite"))
    owner = tb.TelegramTherapistBot.__new__(tb.TelegramTherapistBot)
    owner.user_langs = {7: "en"}
    owner.user_recent_messages = {}
    owner.sessions = {}
    owner.user_locks = {}
    owner._save_user_prefs = Mock(return_value=True)
    owner.bot = SimpleNamespace(send_audio=AsyncMock(
        return_value=SimpleNamespace(audio=SimpleNamespace(file_id="telegram-file"))))
    therapist = SimpleNamespace(history=[], client=None)
    owner.sessions[7] = therapist
    feature = music.Music(owner)
    # A harmless existing source file is used only for stat(); send_audio is mocked.
    tracks = [
        {"id": f"death-{i}", "category": "death", "file": f"death/{i}.mp3",
         "title": f"Title {i}", "artist": "Artist", "path": Path(__file__)}
        for i in range(7)
    ]
    monkeypatch.setattr(music, "catalogue", lambda *args: (tracks, tracks))
    msg = SimpleNamespace(from_user=SimpleNamespace(id=7), answer=AsyncMock(),
                          edit_reply_markup=AsyncMock())
    return owner, feature, therapist, tracks, msg


def test_manifest_template_and_missing_library(tmp_path):
    path = Path(__file__).resolve().parents[1] / "docs" / "music_manifest.json"
    configured, playable = music.catalogue(path, tmp_path)
    assert len(configured) == 28 and not playable
    assert len({x["id"] for x in configured}) == 28
    assert all(sum(x["category"] == c for x in configured) == 7 for c in music.CATEGORIES)


@pytest.mark.parametrize("filename", ["../escape.mp3", "/outside.mp3", "C:/escape.mp3",
                                      r"..\escape.mp3", "song.wav", "missing.mp3"])
def test_unsafe_or_missing_path_is_not_playable(tmp_path, filename):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps([{"id": "one", "category": "death", "file": filename,
                                    "title": "Title", "artist": "Artist"}]))
    configured, playable = music.catalogue(manifest, tmp_path)
    assert len(configured) == 1 and not playable


def test_concurrent_reservations_and_persistence(tmp_path):
    path = tmp_path / "history.sqlite"
    store = music.Store(path, "key")
    tracks = [{"id": str(i)} for i in range(7)]

    def reserve(_):
        return music.Store(path, "key").reserve(7, tracks)

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(reserve, range(20)))
    ids = [item["id"] for item in results if item]
    assert len(ids) == len(set(ids)) == 7
    for track_id in ids:
        store.finish(7, track_id, True)
    store.reset_session(7)
    restarted = music.Store(path, "key")
    assert restarted.sent(7) == set(ids)
    assert restarted.reserve(7, tracks) is None
    assert restarted.reserve(8, tracks)
    with pytest.raises(ValueError):
        music.Store(path, "changed-key")


@pytest.mark.asyncio
async def test_unique_sends_exhaustion_file_id_and_restart(setup_music):
    owner, feature, therapist, tracks, msg = setup_music
    for _ in range(7):
        await feature.send(msg, 7, "death")
    assert owner.bot.send_audio.await_count == 7
    assert len(feature.store().sent(7)) == 7
    assert all(call.kwargs["title"].startswith("Title") and call.kwargs["performer"] == "Artist"
               for call in owner.bot.send_audio.await_args_list)
    assert all(call.kwargs["parse_mode"] is None for call in owner.bot.send_audio.await_args_list)
    await feature.send(msg, 7, "death")
    assert "all my music" in msg.answer.await_args.args[0]
    assert owner.bot.send_audio.await_count == 7
    restarted = music.Music(owner)
    assert len(restarted.store().sent(7)) == 7
    # Another user can use a cached file_id for a track already uploaded.
    selected = tracks[0]
    fingerprint, file_id = feature.store().file_id(selected)
    assert fingerprint and file_id == "telegram-file"
    owner.sessions[8] = SimpleNamespace(history=[])
    monkey_track = selected.copy()
    # Directly inspect the cached id without making any network calls.
    assert feature.store().file_id(monkey_track)[1] == "telegram-file"


@pytest.mark.asyncio
async def test_rejected_send_can_be_retried_and_uncertain_send_is_reserved(setup_music):
    owner, feature, therapist, tracks, msg = setup_music
    error = TelegramBadRequest(method=SendAudio(chat_id=7, audio="x"), message="rejected")
    owner.bot.send_audio.side_effect = error
    await feature.send(msg, 7, "death")
    assert not feature.store().unavailable(7)
    owner.bot.send_audio.side_effect = TimeoutError()
    await feature.send(msg, 7, "death")
    assert len(feature.store().unavailable(7)) == 1
    assert not feature.store().sent(7)
    assert len(music.Music(owner).store().unavailable(7)) == 1


@pytest.mark.asyncio
async def test_concurrent_sends_never_repeat_a_track(setup_music):
    owner, feature, therapist, tracks, msg = setup_music
    await asyncio.gather(*(feature.send(msg, 7, "death") for _ in range(12)))
    titles = [call.kwargs["title"] for call in owner.bot.send_audio.await_args_list]
    assert len(titles) == len(set(titles)) == 7
    assert len(feature.store().sent(7)) == 7


@pytest.mark.asyncio
async def test_rejected_cached_file_is_forgotten(setup_music, monkeypatch):
    owner, feature, therapist, tracks, msg = setup_music
    track = tracks[0]
    monkeypatch.setattr(music, "catalogue", lambda *args: ([track], [track]))
    fingerprint, _ = feature.store().file_id(track)
    feature.store().save_file_id(track["id"], fingerprint, "invalid-file")
    owner.bot.send_audio.side_effect = TelegramBadRequest(
        method=SendAudio(chat_id=7, audio="invalid-file"), message="rejected")
    await feature.send(msg, 7, "death")
    assert not feature.store().unavailable(7)
    assert feature.store().file_id(track)[1] is None
    owner.bot.send_audio.side_effect = None
    await feature.send(msg, 7, "death")
    assert feature.store().sent(7) == {track["id"]}


@pytest.mark.asyncio
async def test_category_exhaustion_offers_available_other_category(setup_music, monkeypatch):
    owner, feature, therapist, tracks, msg = setup_music
    other = {**tracks[0], "id": "freedom-1", "category": "freedom"}
    monkeypatch.setattr(music, "catalogue", lambda *args: (tracks + [other], tracks + [other]))
    for track in tracks:
        feature.store().reserve(7, [track])
        feature.store().finish(7, track["id"], True)
    await feature.send(msg, 7, "death")
    buttons = msg.answer.await_args.kwargs["reply_markup"].inline_keyboard
    assert [row[0].text for row in buttons] == ["Freedom"]
    assert "shared everything" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_offer_consent_buttons_are_single_use_and_recorded(setup_music):
    owner, feature, therapist, tracks, msg = setup_music
    token = feature.store().button(7, "offer", "death", 7 * 86400)
    callback = SimpleNamespace(from_user=msg.from_user, message=msg,
                               data=f"music:{token}:yes", answer=AsyncMock())
    await asyncio.gather(feature.callback(callback), feature.callback(callback))
    assert owner.bot.send_audio.await_count == 1
    assert feature.store().sent(7)
    assert therapist.history[0].content == "Let’s listen."
    assert "Artist" in therapist.history[-1].content
    assert callback.answer.await_args.args[0] == "This button has expired."
    assert feature.store().button(7, "offer", "death", 7 * 86400) is None


@pytest.mark.asyncio
async def test_refusal_no_audio_and_cooldown_survives_session_reset(setup_music):
    owner, feature, therapist, tracks, msg = setup_music
    token = feature.store().button(7, "offer", "death", 7 * 86400)
    callback = SimpleNamespace(from_user=msg.from_user, message=msg,
                               data=f"music:{token}:no", answer=AsyncMock())
    await feature.callback(callback)
    owner.bot.send_audio.assert_not_awaited()
    assert [m.content for m in therapist.history] == ["Not now.", "All right."]
    feature.store().reset_session(7)
    assert feature.store().button(7, "offer", "death", 7 * 86400) is None
    assert feature.store().button(7, "offer", "death", 0)


def test_buttons_are_bound_to_user_session_and_expire(setup_music):
    owner, feature, therapist, tracks, msg = setup_music
    store = feature.store()
    token = store.button(7, "choose")
    assert store.consume(8, token, "death") is None
    assert store.consume(7, token, "no") is None
    store.reset_session(7)
    assert store.consume(7, token, "death") is None
    token = store.button(7, "choose")
    with store.connect() as db:
        db.execute("UPDATE music_button SET created=0 WHERE token=?", (token,))
    assert store.consume(7, token, "death") is None


@pytest.mark.asyncio
async def test_command_without_context_asks_for_selection(setup_music):
    owner, feature, therapist, tracks, msg = setup_music
    await feature.command(msg)
    owner.bot.send_audio.assert_not_awaited()
    assert msg.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0].text == "Death"
    assert therapist.history[0].content == "/music"


@pytest.mark.asyncio
async def test_selection_sends_and_reset_preserves_delivery(setup_music):
    owner, feature, therapist, tracks, msg = setup_music
    owner.music = feature
    owner.user_message_counts = {}
    owner.user_summaries = {}
    owner.user_states = {}
    therapist.reset = lambda: therapist.history.clear()
    await feature.command(msg)
    button = msg.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0]
    callback = SimpleNamespace(from_user=msg.from_user, message=msg,
                               data=button.callback_data, answer=AsyncMock())
    await feature.callback(callback)
    assert owner.bot.send_audio.await_count == 1
    sent = feature.store().sent(7)
    await owner._handle_reset(msg)
    assert not therapist.history
    assert music.Music(owner).store().sent(7) == sent
    await feature.callback(callback)
    assert owner.bot.send_audio.await_count == 1


@pytest.mark.asyncio
async def test_offer_uses_safety_and_never_sends_without_consent(setup_music):
    owner, feature, therapist, tracks, msg = setup_music
    therapist.history = [music.Message("user", f"ordinary turn {i}") for i in range(6)]
    therapist.client = object()
    therapist.model = "model"
    therapist._is_crisis_or_weight = lambda text: "crisis" in text
    therapist._complete_text = Mock(return_value='{"category":"death","confident":true,"crisis":false,"offer":true}')
    await feature.maybe_offer(msg, therapist, 7)
    owner.bot.send_audio.assert_not_awaited()
    assert msg.answer.await_args.kwargs["reply_markup"].inline_keyboard
    therapist.history.extend(music.Message("user", "another") for _ in range(6))
    await feature.maybe_offer(msg, therapist, 7)
    assert therapist._complete_text.call_count == 1
    therapist.history[-1].content = "crisis"
    assert feature.classify(therapist, initiative=True) is None
    therapist.history[-1].content = "ordinary"
    therapist._complete_text.return_value = '{"category":"death","confident":true,"crisis":true,"offer":true}'
    assert feature.classify(therapist, initiative=True) is None
    feature.enabled = False
    await feature.maybe_offer(msg, therapist, 7)
    owner.bot.send_audio.assert_not_awaited()


@pytest.mark.asyncio
async def test_nonconsent_callback_routing_remains_gated(setup_music):
    owner, feature, therapist, tracks, msg = setup_music
    owner.user_consents = {}
    owner._show_consent = AsyncMock()
    msg.chat = SimpleNamespace(type="private", id=7)
    msg.from_user.is_bot = False
    msg.from_user.language_code = "en"
    event = SimpleNamespace(from_user=msg.from_user, message=msg, data="music:token:yes", answer=AsyncMock())
    handler = AsyncMock()
    await ConsentMiddleware(owner)(handler, event, {})
    handler.assert_not_awaited()
    owner._show_consent.assert_awaited_once()
