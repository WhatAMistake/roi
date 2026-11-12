"""Music tests use fake Telegram calls; no audio files or network sends are created."""

import asyncio
import hashlib
import hmac
import json
import os
import re
import sqlite3
import subprocess
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
import telegram_bot as tb
from app.features.music import feature as music
from therapist_bot import Message
from privacy import CONSENT_VERSION, ConsentMiddleware


@pytest.fixture
def setup_music(tmp_path, monkeypatch):
    monkeypatch.setenv("USER_PREFS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("ROI_MUSIC_DB", str(tmp_path / "music.sqlite"))
    owner = tb.TelegramTherapistBot.__new__(tb.TelegramTherapistBot)
    owner.user_langs = {7: "en"}
    owner.user_consents = {7: {"version": CONSENT_VERSION, "accepted_at": "now"}}
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
    msg = SimpleNamespace(from_user=SimpleNamespace(id=7), text="/music", answer=AsyncMock(),
                          edit_reply_markup=AsyncMock())
    return owner, feature, therapist, tracks, msg


def test_manifest_template_and_missing_library(tmp_path):
    path = Path(__file__).resolve().parents[1] / "docs" / "music_manifest.json"
    configured, playable = music.catalogue(path, tmp_path)
    assert len(configured) == 28 and not playable
    assert len({x["id"] for x in configured}) == 28
    assert all(sum(x["category"] == c for x in configured) == 7 for c in music.CATEGORIES)
    for track in configured:
        for lang, wrong_script in (("ru", r"[A-Za-z]"), ("en", r"[А-Яа-яЁё]")):
            caption = track[f"caption_{lang}"]
            assert isinstance(caption, str) and caption.strip()
            assert not re.search(wrong_script, caption)
            assert len(music.COLLECTION_INTROS[lang][track["category"]] + " " + caption) <= 1024


def test_catalogue_preserves_optional_captions(tmp_path):
    (tmp_path / "song.mp3").write_bytes(b"ID3test")
    track = {"id": "one", "category": "isolation", "file": "song.mp3",
             "title": "Title", "artist": "Artist", "description": "Internal",
             "caption_ru": "Русская подпись.", "caption_en": "English caption."}
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps([track], ensure_ascii=False), encoding="utf-8")
    configured, playable = music.catalogue(manifest, tmp_path)
    assert configured == [track]
    assert playable == [{**track, "path": (tmp_path / "song.mp3").resolve()}]


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
    assert msg.answer.await_args.args[0] == "My music is now fully yours. Thank you for being here."
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
    assert [button.text for row in buttons for button in row] == [
        "Death", "Freedom", "Loneliness", "Meaninglessness"]
    assert "shared everything" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_category_buttons_are_single_use_and_recorded(setup_music):
    owner, feature, therapist, tracks, msg = setup_music
    token = feature.store().button(7)
    callback = SimpleNamespace(from_user=msg.from_user, message=msg,
                               data=f"music:{token}:death", answer=AsyncMock())
    await asyncio.gather(feature.callback(callback), feature.callback(callback))
    assert owner.bot.send_audio.await_count == 1
    assert feature.store().sent(7)
    assert len(therapist.history) == 1
    assert "Artist" in therapist.history[-1].content
    assert callback.answer.await_args.args[0] == "This button has expired."
def test_initiative_cooldown_survives_session_reset(setup_music):
    owner, feature, therapist, tracks, msg = setup_music
    assert feature.store().claim_initiative(7, 7 * 86400)
    assert not feature.store().claim_initiative(7, 0)
    feature.store().reset_session(7)
    assert not feature.store().claim_initiative(7, 7 * 86400)
    assert feature.store().claim_initiative(7, 0)


def test_buttons_are_bound_to_user_session_and_expire(setup_music):
    owner, feature, therapist, tracks, msg = setup_music
    store = feature.store()
    token = store.button(7)
    assert store.consume(8, token, "death") is None
    assert store.consume(7, token, "no") is None
    store.reset_session(7)
    assert store.consume(7, token, "death") is None
    token = store.button(7)
    with store.connect() as db:
        db.execute("UPDATE music_button SET created=0 WHERE token=?", (token,))
    assert store.consume(7, token, "death") is None


@pytest.mark.asyncio
async def test_command_without_context_asks_for_selection(setup_music):
    owner, feature, therapist, tracks, msg = setup_music
    await feature.command(msg)
    owner.bot.send_audio.assert_not_awaited()
    assert msg.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0].text == "Death"
    assert therapist.history == []


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
    therapist.history = [Message("user", f"ordinary turn {i}") for i in range(6)]
    therapist.client = object()
    therapist.model = "model"
    therapist._is_crisis_or_weight = lambda text: "crisis" in text
    therapist._complete_text = Mock(return_value='{"category":"death","confident":true,"crisis":false,"offer":true}')
    owner.user_consents = {}
    await feature.maybe_offer(msg, therapist, 7)
    owner.bot.send_audio.assert_not_awaited()
    therapist._complete_text.assert_not_called()
    owner.user_consents[7] = {"version": CONSENT_VERSION, "accepted_at": "now"}
    await feature.maybe_offer(msg, therapist, 7)
    assert therapist._complete_text.call_count == 1
    assert owner.bot.send_audio.await_count == 1
    assert len(feature.store().sent(7)) == 1
    await feature.maybe_offer(msg, therapist, 7)
    assert therapist._complete_text.call_count == 1
    therapist.history[-1].content = "crisis"
    assert feature.classify(therapist, initiative=True) is None
    therapist.history[-1].content = "ordinary"
    therapist._complete_text.return_value = '{"category":"death","confident":true,"crisis":true,"offer":true}'
    assert feature.classify(therapist, initiative=True) is None
    feature.enabled = False
    await feature.maybe_offer(msg, therapist, 7)
    assert owner.bot.send_audio.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("lang,expected", [
    ("ru", "Тихая композиция для этого вечера."),
    ("en", "A quiet song for this evening."),
])
@pytest.mark.parametrize("automatic", [False, True])
async def test_localized_caption_in_real_delivery_paths(setup_music, monkeypatch, lang, expected, automatic):
    owner, feature, therapist, tracks, msg = setup_music
    owner.user_langs[7] = lang
    track = {**tracks[0], "description": "Внутреннее описание",
             "caption_ru": "  Тихая композиция для этого вечера.  ",
             "caption_en": "  A quiet song for this evening.  "}
    monkeypatch.setattr(music, "catalogue", lambda *args: ([track], [track]))
    therapist._complete_text = Mock(return_value='{"category":"death","confident":true,"crisis":false,"offer":true}')
    if automatic:
        therapist.history = [Message("user", f"turn {i}") for i in range(6)]
        therapist.client = object()
        therapist.model = "model"
        therapist._is_crisis_or_weight = lambda text: False
        await feature.maybe_offer(msg, therapist, 7)
        assert therapist._complete_text.call_count == 1  # Classification only.
        msg.answer.assert_not_awaited()
    else:
        await feature.command(msg)
        buttons = msg.answer.await_args.kwargs["reply_markup"].inline_keyboard
        assert [button.text for row in buttons for button in row] == [
            music.LABELS[lang][category] for category in music.CATEGORIES]
        callback = SimpleNamespace(from_user=msg.from_user, message=msg,
                                   data=buttons[0][0].callback_data, answer=AsyncMock())
        await feature.callback(callback)
        therapist._complete_text.assert_not_called()
        expected = f"{music.COLLECTION_INTROS[lang]['death']} {expected}"
    sent = owner.bot.send_audio.await_args.kwargs
    assert sent["caption"] == expected
    assert sent["title"] == track["title"] and sent["performer"] == track["artist"]
    assert sent["parse_mode"] is None
    assert feature.store().sent(7) == {track["id"]}
    assert therapist.history[-1].content == f"{expected} [Artist — Title 0; {music.LABELS[lang]['death']}]"


@pytest.mark.asyncio
@pytest.mark.parametrize("lang,expected", [
    ("ru", "Вот песня для тебя."),
    ("en", "Here’s a song for you."),
])
@pytest.mark.parametrize("value", [None, "", "   ", 42])
async def test_missing_caption_never_uses_description_or_other_language(setup_music, monkeypatch, lang, expected, value):
    owner, feature, therapist, tracks, msg = setup_music
    owner.user_langs[7] = lang
    track = {**tracks[0], "description": "Внутреннее описание",
             "caption_en" if lang == "ru" else "caption_ru": "Wrong language"}
    if value is not None:
        track[f"caption_{lang}"] = value
    monkeypatch.setattr(music, "catalogue", lambda *args: ([track], [track]))
    assert await feature.send(msg, 7, "death")
    assert owner.bot.send_audio.await_args.kwargs["caption"] == f"{music.COLLECTION_INTROS[lang]['death']} {expected}"


@pytest.mark.asyncio
@pytest.mark.parametrize("lang,expected", [
    ("ru", "Вот песня для тебя."),
    ("en", "Here’s a song for you."),
])
async def test_automatic_delivery_uses_localized_fallback(setup_music, monkeypatch, lang, expected):
    owner, feature, therapist, tracks, msg = setup_music
    owner.user_langs[7] = lang
    track = {**tracks[0], "description": "Внутреннее описание",
             "caption_en" if lang == "ru" else "caption_ru": "Wrong language"}
    monkeypatch.setattr(music, "catalogue", lambda *args: ([track], [track]))
    therapist.history = [Message("user", f"turn {i}") for i in range(6)]
    therapist.client = object()
    therapist.model = "model"
    therapist._is_crisis_or_weight = lambda text: False
    therapist._complete_text = Mock(return_value='{"category":"death","confident":true,"crisis":false,"offer":true}')
    await feature.maybe_offer(msg, therapist, 7)
    assert owner.bot.send_audio.await_args.kwargs["caption"] == expected
    assert therapist._complete_text.call_count == 1


@pytest.mark.asyncio
async def test_callback_uses_current_language_preference(setup_music, monkeypatch):
    owner, feature, therapist, tracks, msg = setup_music
    track = {**tracks[0], "caption_ru": "Русская подпись.", "caption_en": "English caption."}
    monkeypatch.setattr(music, "catalogue", lambda *args: ([track], [track]))
    await feature.command(msg)
    button = msg.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0]
    owner.user_langs[7] = "ru"
    callback = SimpleNamespace(from_user=msg.from_user, message=msg,
                               data=button.callback_data, answer=AsyncMock())
    await feature.callback(callback)
    assert owner.bot.send_audio.await_args.kwargs["caption"] == "Из моей коллекции о смерти. Русская подпись."


@pytest.mark.asyncio
@pytest.mark.parametrize("lang,category,intro", [
    ("ru", "death", "Из моей коллекции о смерти."),
    ("ru", "freedom", "Из моей коллекции о свободе."),
    ("ru", "isolation", "Из моей коллекции об одиночестве."),
    ("ru", "meaninglessness", "Из моей коллекции о бессмысленности."),
    ("en", "death", "From my collection on death."),
    ("en", "freedom", "From my collection on freedom."),
    ("en", "isolation", "From my collection on loneliness."),
    ("en", "meaninglessness", "From my collection on meaninglessness."),
])
async def test_manual_callback_uses_selected_category_and_manifest_caption(
        setup_music, monkeypatch, lang, category, intro):
    owner, feature, therapist, tracks, msg = setup_music
    owner.user_langs[7] = lang
    manifest = json.loads((music.ROOT / "docs" / "music_manifest.json").read_text(encoding="utf-8"))
    track = {**next(item for item in manifest if item["category"] == category), "path": Path(__file__)}
    monkeypatch.setattr(music, "catalogue", lambda *args: ([track], [track]))
    therapist._complete_text = Mock()
    await feature.command(msg)
    buttons = msg.answer.await_args.kwargs["reply_markup"].inline_keyboard
    button = next(button for row in buttons for button in row if button.callback_data.endswith(f":{category}"))
    callback = SimpleNamespace(from_user=msg.from_user, message=msg,
                               data=button.callback_data, answer=AsyncMock())
    await feature.callback(callback)
    sent = owner.bot.send_audio.await_args.kwargs
    assert sent["caption"] == f"{intro} {track[f'caption_{lang}']}"
    assert not re.search(r"[A-Za-z]" if lang == "ru" else r"[А-Яа-яЁё]", sent["caption"])
    assert sent["title"] == track["title"] and sent["performer"] == track["artist"]
    therapist._complete_text.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("category", music.CATEGORIES)
async def test_manual_and_automatic_sends_share_unique_history(setup_music, monkeypatch, category):
    owner, feature, therapist, tracks, msg = setup_music
    tracks = [{**track, "id": f"{category}-{i}", "category": category,
               "caption_ru": f"Композиция {i}.", "caption_en": f"Song {i}."}
              for i, track in enumerate(tracks)]
    monkeypatch.setattr(music, "catalogue", lambda *args: (tracks, tracks))
    await feature.command(msg)
    buttons = msg.answer.await_args.kwargs["reply_markup"].inline_keyboard
    button = next(button for row in buttons for button in row if button.callback_data.endswith(f":{category}"))
    callback = SimpleNamespace(from_user=msg.from_user, message=msg,
                               data=button.callback_data, answer=AsyncMock())
    await feature.callback(callback)
    therapist.history = [Message("user", f"turn {i}") for i in range(6)]
    therapist.client = object()
    therapist.model = "model"
    therapist._is_crisis_or_weight = lambda text: False
    therapist._complete_text = Mock(return_value=json.dumps(
        {"category": category, "confident": True, "crisis": False, "offer": True}))
    await feature.maybe_offer(msg, therapist, 7)
    for _ in range(5):
        assert await feature.send(msg, 7, category)
    titles = [call.kwargs["title"] for call in owner.bot.send_audio.await_args_list]
    assert len(titles) == len(set(titles)) == 7
    feature.store().reset_session(7)
    restarted = music.Music(owner)
    assert restarted.store().sent(7) == {track["id"] for track in tracks}
    assert not await restarted.send(msg, 7, category)
    assert owner.bot.send_audio.await_count == 7


def test_music_language_uses_saved_preference_and_bot_default(setup_music, monkeypatch):
    owner, feature, therapist, tracks, msg = setup_music
    owner.user_langs.clear()
    assert music.Music(owner, default_lang="en").lang(7) == "en"
    feature = music.Music(owner, default_lang="ru")
    assert feature.lang(7) == "ru"
    owner.user_langs[7] = "en"
    assert feature.lang(7) == "en"


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


def test_default_and_relative_paths_are_unchanged(monkeypatch, tmp_path):
    for name in ("ROI_MUSIC_DIR", "ROI_MUSIC_MANIFEST", "ROI_MUSIC_DB"):
        monkeypatch.delenv(name, raising=False)
    owner = SimpleNamespace(user_langs={})
    feature = music.Music(owner)
    root = Path(__file__).resolve().parents[1]
    assert feature.directory == Path("/opt/roi-media/music")
    assert feature.manifest == root / "docs" / "music_manifest.json"
    assert feature.db_path == root / "data" / "music.sqlite"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ROI_MUSIC_DIR", "private/audio")
    monkeypatch.setenv("ROI_MUSIC_MANIFEST", "private/manifest.json")
    monkeypatch.setenv("ROI_MUSIC_DB", "private/music.sqlite")
    feature = music.Music(owner)
    assert feature.directory == Path("private/audio")
    assert feature.manifest == Path("private/manifest.json")
    assert feature.db_path == Path("private/music.sqlite")
    assert feature.existing_store() is None
    assert not (tmp_path / "private").exists()


def test_music_and_preferences_use_the_same_canonical_key(tmp_path, monkeypatch):
    key = Fernet.generate_key()
    monkeypatch.setenv("USER_PREFS_ENCRYPTION_KEY", f" '{key.decode().rstrip('=')}' ")
    monkeypatch.setenv("ROI_MUSIC_DB", str(tmp_path / "music.sqlite"))
    feature = music.Music(SimpleNamespace(user_langs={}))
    assert tb.get_encryption_key() == key
    assert feature.store().key == key
    encrypted = tb.encrypt_data("private", key)
    assert tb.decrypt_data(encrypted, feature.store().key) == "private"

    monkeypatch.delenv("USER_PREFS_ENCRYPTION_KEY")
    monkeypatch.setenv("ROI_MUSIC_DB", str(tmp_path / "no-key.sqlite"))
    unavailable = music.Music(SimpleNamespace(user_langs={}))
    with pytest.raises(ValueError):
        unavailable.store()
    assert not (tmp_path / "no-key.sqlite").exists()


def test_telegram_composition_supplies_shared_key_and_default_language(tmp_path, monkeypatch):
    key = Fernet.generate_key()
    monkeypatch.setenv("ROI_MUSIC_DB", str(tmp_path / "music.sqlite"))
    monkeypatch.setattr(tb, "Bot", lambda **kwargs: SimpleNamespace())
    monkeypatch.setattr(tb, "get_encryption_key", lambda: key)
    monkeypatch.setattr(tb, "DEFAULT_LANG", "ru")
    monkeypatch.setattr(tb.TelegramTherapistBot, "_load_user_prefs",
                        lambda self: setattr(self, "_prefs_loaded", True))
    owner = tb.TelegramTherapistBot(telegram_token="unused")
    assert isinstance(owner.music, music.Music)
    assert owner.music.lang(7) == "ru"
    monkeypatch.setattr(tb, "DEFAULT_LANG", "en")
    assert owner.music.lang(7) == "en"
    assert owner.music.store().key == key


def test_existing_database_survives_restart_without_schema_changes(tmp_path):
    key = Fernet.generate_key()
    user_key = hmac.new(key, b"roi-music-v1:7", hashlib.sha256).hexdigest()
    verifier = hmac.new(key, b"roi-music-key-check-v1", hashlib.sha256).hexdigest()
    path = tmp_path / "existing.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE music_delivery (
                user_key TEXT NOT NULL, track_id TEXT NOT NULL,
                status TEXT NOT NULL, PRIMARY KEY(user_key, track_id));
            CREATE TABLE music_user (
                user_key TEXT PRIMARY KEY, session INTEGER NOT NULL DEFAULT 0,
                offered_session INTEGER, last_offer REAL,
                initiative_enabled INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE music_button (
                token TEXT PRIMARY KEY, user_key TEXT NOT NULL, session INTEGER NOT NULL,
                kind TEXT NOT NULL, category TEXT, created REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE music_file (
                track_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, file_id TEXT NOT NULL);
            CREATE TABLE music_meta (name TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        db.execute("INSERT INTO music_meta VALUES ('key_check', ?)", (verifier,))
        db.executemany("INSERT INTO music_delivery VALUES (?, ?, ?)", [
            (user_key, "sent-track", "sent"), (user_key, "uncertain-track", "reserved"),
        ])
        db.execute("INSERT INTO music_user VALUES (?, 3, 3, 1234, 0)", (user_key,))
        db.execute("INSERT INTO music_file VALUES ('sent-track', 'fingerprint', 'cached-id')")
        before = db.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name").fetchall()

    owner = SimpleNamespace(user_langs={})
    feature = music.Music(owner, key_provider=lambda: key)
    feature.db_path = path
    store = feature.existing_store()
    assert store.user(7) == user_key
    assert store.sent(7) == {"sent-track"}
    assert store.unavailable(7) == {"sent-track", "uncertain-track"}
    assert not store.may_offer(7, 0)
    assert store.reserve(7, [{"id": "sent-track"}, {"id": "uncertain-track"}]) is None
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name").fetchall() == before
        assert db.execute("SELECT session, offered_session, last_offer, initiative_enabled FROM music_user").fetchone() == (3, 3, 1234, 0)
        assert db.execute("SELECT file_id FROM music_file").fetchone() == ("cached-id",)

    # A fresh interpreter must read the same pseudonymized history after restart.
    env = os.environ.copy()
    env["PYTHONPATH"] = str(music.ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["MUSIC_TEST_KEY"] = key.decode()
    result = subprocess.run(
        [sys.executable, "-c",
         "import os,sys; from app.features.music.store import Store; "
         "s=Store(sys.argv[1], os.environ['MUSIC_TEST_KEY']); "
         "assert s.sent(7)=={'sent-track'}; "
         "assert s.unavailable(7)=={'sent-track','uncertain-track'}; "
         "assert not s.may_offer(7,0)", str(path)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_entrypoints_and_compatibility_import_offline(tmp_path):
    root = Path(__file__).resolve().parents[1]
    manifest = tmp_path / "manifest.json"
    manifest.write_text("[]", encoding="utf-8")
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(root),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "ROI_MUSIC_MANIFEST": str(manifest),
        "ROI_MUSIC_DIR": str(tmp_path / "missing-audio"),
        "ROI_MUSIC_DB": str(tmp_path / "unused.sqlite"),
    })
    commands = [
        [sys.executable, str(root / "src" / "music.py"), "--check"],
        [sys.executable, "-m", "app.features.music", "--check"],
        [sys.executable, str(root / "src" / "telegram_bot.py"), "--help"],
        [sys.executable, "-c",
         "import sys; from app.features.music import Music; "
         "assert 'telegram_bot' not in sys.modules"],
    ]
    results = [subprocess.run(command, cwd=tmp_path, env=env, capture_output=True,
                              text=True, encoding="utf-8", timeout=30)
               for command in commands]
    for result in results:
        assert result.returncode == 0, result.stderr
    assert results[0].stdout == results[1].stdout
    assert "death: 0/0 playable" in results[0].stdout
    assert not (tmp_path / "unused.sqlite").exists()

    import music as legacy
    from app.features.music import Music
    assert legacy.Music is Music
    assert legacy.Store is music.Store
