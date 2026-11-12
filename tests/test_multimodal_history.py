"""Dialogue continuity using fake Telegram and LLM calls only."""
import asyncio
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from test_telegram_delivery import owner, message, Therapist
import telegram_bot as tb
from therapist_bot import ExistentialTherapistBot


def media_message(kind="photo", message_id=42, caption="Look"):
    msg = message()
    msg.message_id = message_id
    msg.caption = caption
    msg.photo = [SimpleNamespace(file_id="photo-file")] if kind == "photo" else None
    msg.voice = SimpleNamespace(file_id="voice-file") if kind == "voice" else None
    return msg


def prepare_photo(owner, response):
    therapist = Therapist([])
    therapist.analyze_image_stream = Mock()
    owner.sessions[7] = therapist
    owner.bot.get_file = AsyncMock(return_value=SimpleNamespace(file_path="remote"))
    owner.bot.download_file = AsyncMock(return_value=io.BytesIO(b"image"))
    owner._smooth_stream_generic = AsyncMock(return_value=(response, [response]))
    owner._deliver_response = AsyncMock(side_effect=lambda msg, sent, text, shown: text)
    return therapist


@pytest.mark.asyncio
async def test_photo_then_chat_keeps_caption_observation_and_delivered_response(owner):
    therapist = prepare_photo(owner, "<roi_visual>A red boat on water.</roi_visual>What do you notice?")
    therapist.history.append(tb.Message("assistant", "An old song"))
    photo = media_message()
    await owner._handle_photo(photo)
    assert [m.role for m in therapist.history] == ["assistant", "user", "assistant"]
    assert "Look" in therapist.history[-2].content
    assert "A red boat on water" in therapist.history[-1].content
    assert "What do you notice?" in therapist.history[-1].content
    assert "base64" not in repr(owner.user_recent_messages)
    assert owner.user_recent_messages[7][-2]["photo_description"] == "A red boat on water."
    assert owner.user_recent_messages[7][-2]["message_id"] == 42
    assert therapist.history[-2].music_counted is False
    # A subsequent ordinary message sees the photo context, not only the old song.
    owner._smooth_stream = AsyncMock(return_value=("About the boat", ["About the boat"]))
    owner._deliver_response = AsyncMock(return_value="About the boat")
    await owner._handle_chat(message(), "Tell me about it")
    assert "red boat" in therapist.history[-3].content
    assert therapist.history[-1].content == "About the boat"
    owner.user_recent_messages = {}
    owner._load_user_prefs()
    assert "red boat" in owner.user_recent_messages[7][-3]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("response", ["Ошибка: unavailable", ""])
async def test_photo_generation_failure_does_not_save(owner, response):
    therapist = prepare_photo(owner, response)
    await owner._handle_photo(media_message())
    assert therapist.history == []
    assert 7 not in owner.user_recent_messages


@pytest.mark.asyncio
async def test_photo_partial_delivery_saves_only_confirmed_part(owner):
    therapist = prepare_photo(owner, "<roi_visual>A tree.</roi_visual>Long response")
    owner._deliver_response.side_effect = tb.DeliveryError("Long")
    await owner._handle_photo(media_message())
    assert therapist.history[-1].content.endswith("Long")
    assert "response" not in therapist.history[-1].content


@pytest.mark.asyncio
async def test_photo_uses_single_vision_stream_without_exposing_description(owner):
    therapist = Therapist([])
    calls = []
    def vision(url, instruction):
        calls.append((url, instruction))
        yield "<roi_visual>A green tree.</roi_visual>What does it remind you of?"
    therapist.analyze_image_stream = vision
    owner.sessions[7] = therapist
    owner.bot.get_file = AsyncMock(return_value=SimpleNamespace(file_path="remote"))
    owner.bot.download_file = AsyncMock(return_value=io.BytesIO(b"photo"))
    owner._deliver_response = AsyncMock(return_value="What does it remind you of?")
    await owner._handle_photo(media_message())
    assert len(calls) == 1 and "without psychological interpretation" in calls[0][1]
    assert owner._deliver_response.await_args.args[2] == "What does it remind you of?"
    assert "A green tree" in therapist.history[-1].content


@pytest.mark.asyncio
async def test_photo_reply_passes_original_to_vision(owner):
    therapist = Therapist([])
    calls = []
    def vision(url, instruction):
        calls.append(instruction)
        yield "<roi_visual>A tree.</roi_visual>Answer"
    therapist.analyze_image_stream = vision
    owner.sessions[7] = therapist
    owner.bot.get_file = AsyncMock(return_value=SimpleNamespace(file_path="remote"))
    owner.bot.download_file = AsyncMock(return_value=io.BytesIO(b"photo"))
    owner._deliver_response = AsyncMock(return_value="Answer")
    msg = media_message()
    msg.reply_to_message = SimpleNamespace(
        message_id=1, text="Old message", caption=None, audio=None,
        photo=None, voice=None, from_user=SimpleNamespace(is_bot=True))
    await owner._handle_photo(msg)
    assert "Old message" in calls[0] and "reply to" in calls[0]


@pytest.mark.asyncio
async def test_reply_to_old_text_and_command_result_after_restart(owner):
    old = SimpleNamespace(message_id=1, text="An old interpretation",
                          from_user=SimpleNamespace(is_bot=True), caption=None,
                          photo=None, voice=None, audio=None)
    msg = message()
    msg.reply_to_message = old
    owner.sessions[7] = Therapist([])
    context = await owner._reply_context(msg, owner.sessions[7])
    assert "Source: bot" in context and "An old interpretation" in context
    assembled = ExistentialTherapistBot._add_reply_context(
        [{"role": "system", "content": "system"}, {"role": "user", "content": "Why?"}], context)
    assert assembled[-1] == {"role": "user", "content": "Why?"}
    assert assembled[-2]["role"] == "system"
    assert "not a new user message" in assembled[-2]["content"]
    # Telegram's reply object works even with empty, freshly restored history.
    owner.user_recent_messages = {}
    assert "An old interpretation" in await owner._reply_context(msg, owner.sessions[7])


@pytest.mark.asyncio
async def test_reply_context_reaches_model_but_is_not_saved_as_user_words(owner):
    class ReplyTherapist(Therapist):
        def chat_stream(self, text, *, reply_context=None):
            self.received = (text, reply_context)
            yield "My answer"
    therapist = ReplyTherapist([])
    owner.sessions[7] = therapist
    msg = message()
    msg.reply_to_message = SimpleNamespace(
        message_id=1, text="Old bot answer", caption=None, audio=None,
        photo=None, voice=None, from_user=SimpleNamespace(is_bot=True))
    owner._deliver_response = AsyncMock(return_value="My answer")
    await owner._handle_chat(msg, "Why?")
    assert therapist.received[0] == "Why?"
    assert "Old bot answer" in therapist.received[1]
    assert [item.content for item in therapist.history] == ["Why?", "My answer"]
    assert [item["content"] for item in owner.user_recent_messages[7]] == ["Why?", "My answer"]


@pytest.mark.asyncio
async def test_reply_photo_cached_or_reanalysed(owner):
    therapist = prepare_photo(owner, "<roi_visual>A red boat.</roi_visual>Answer")
    await owner._handle_photo(media_message())
    old = media_message()
    old.from_user.is_bot = False
    msg = message()
    msg.reply_to_message = old
    context = await owner._reply_context(msg, therapist)
    assert "A red boat" in context
    owner.user_recent_messages = {}
    therapist.analyze_image = Mock(return_value="A red boat on a lake.")
    context = await owner._reply_context(msg, therapist)
    assert "Caption: Look" in context and "A red boat on a lake" in context
    therapist.analyze_image.assert_called_once()


@pytest.mark.asyncio
async def test_reply_voice_and_music_metadata(owner):
    therapist = Therapist([])
    therapist.transcribe_audio = Mock(return_value="Spoken words")
    owner.sessions[7] = therapist
    owner.bot.get_file = AsyncMock(return_value=SimpleNamespace(file_path="remote"))
    async def download(remote, path):
        path.write_bytes(b"voice")
    owner.bot.download_file = AsyncMock(side_effect=download)
    msg = message()
    msg.reply_to_message = media_message("voice", caption=None)
    context = await owner._reply_context(msg, therapist)
    assert "Spoken words" in context
    old = SimpleNamespace(from_user=SimpleNamespace(is_bot=True), message_id=90,
                          text=None, caption="Listen", photo=None, voice=None,
                          audio=SimpleNamespace(title="Song", performer="Artist"))
    msg.reply_to_message = old
    context = await owner._reply_context(msg, therapist)
    assert "Artist — Song" in context and "Listen" in context


@pytest.mark.asyncio
async def test_missing_reply_and_reset_never_restore_deleted_local_history(owner):
    owner.sessions[7] = Therapist([])
    owner.user_recent_messages[7] = [{"role": "user", "content": "private", "message_id": 1}]
    msg = message()
    msg.reply_to_message = None
    msg.reply_to_message_id = 1
    assert "private" in await owner._reply_context(msg, owner.sessions[7])
    msg.reply_to_message = SimpleNamespace(from_user=None, text=None, caption=None,
                                           audio=None, photo=None, voice=None, message_id=1)
    assert "private" in await owner._reply_context(msg, owner.sessions[7])
    await owner._handle_reset(message())
    msg.reply_to_message = None
    assert await owner._reply_context(msg, owner.sessions[7]) == "Referenced message content unavailable"
    msg.reply_to_message = SimpleNamespace(from_user=None, text=None, caption=None,
                                           audio=None, photo=None, voice=None, message_id=1)
    assert await owner._reply_context(msg, owner.sessions[7]) == "Referenced message content unavailable"


@pytest.mark.asyncio
async def test_ordering_dedup_and_independent_users(owner):
    entered, release = asyncio.Event(), asyncio.Event()
    order = []
    async def process(msg):
        order.append(("start", msg.message_id))
        if msg.message_id == 1:
            entered.set()
            await release.wait()
        order.append(("end", msg.message_id))
    first, second, third = (media_message(message_id=i) for i in (1, 2, 3))
    a = asyncio.create_task(owner._ordered_message(first, process))
    await entered.wait()
    b = asyncio.create_task(owner._ordered_message(second, process))
    c = asyncio.create_task(owner._ordered_message(third, process))
    await asyncio.sleep(0)
    other = media_message(message_id=1)
    other.from_user.id = 8
    other.message_id = 5
    await owner._ordered_message(other, process)
    assert order == [("start", 1), ("start", 5), ("end", 5)]
    release.set()
    await asyncio.gather(a, b, c)
    assert order == [("start", 1), ("start", 5), ("end", 5), ("end", 1), ("start", 2), ("end", 2),
                     ("start", 3), ("end", 3)]
    await owner._ordered_message(second, process)
    assert len(order) == 8
    owner._privacy_pending = {7: True}
    await owner._ordered_message(media_message(message_id=4), process)
    assert len(order) == 8


@pytest.mark.asyncio
async def test_story_and_associations_results_are_discussable(owner):
    therapist = Therapist([])
    therapist.analyze_story_stream = Mock()
    therapist.analyze_associations_stream = Mock()
    owner.sessions[7] = therapist
    owner.temp_associations = {7: {"freedom": ["sky"], "nonsense": ["void"],
                                   "solitude": ["alone"]}}
    owner._new_delivery_placeholder = AsyncMock(return_value=None)
    owner._smooth_stream_generic = AsyncMock(return_value=("Analysis", ["Analysis"]))
    owner._deliver_response = AsyncMock(side_effect=lambda msg, sent, text, shown, **kw: text)
    await owner._handle_story_input(message(), "This is a sufficiently long story")
    assert therapist.history[-2].content == "This is a sufficiently long story"
    assert therapist.history[-1].content.endswith("Analysis")
    await owner._handle_assoc_input(message(), "assoc_death", "end")
    assert "sky" in therapist.history[-2].content
    assert "end" in therapist.history[-2].content
    assert therapist.history[-1].content.endswith("Analysis")


@pytest.mark.asyncio
async def test_story_partial_and_failure_do_not_invent_delivery(owner):
    therapist = Therapist([])
    therapist.analyze_story_stream = Mock()
    owner.sessions[7] = therapist
    owner._new_delivery_placeholder = AsyncMock(return_value=None)
    owner._smooth_stream_generic = AsyncMock(return_value=("Analysis", ["Analysis"]))
    owner._deliver_response = AsyncMock(side_effect=tb.DeliveryError("Ana"))
    await owner._handle_story_input(message(), "This is a sufficiently long story")
    assert therapist.history[-1].content == "Ana"
    owner._deliver_response.side_effect = tb.DeliveryError("")
    await owner._handle_story_input(message(), "Another sufficiently long story")
    assert len(therapist.history) == 2
