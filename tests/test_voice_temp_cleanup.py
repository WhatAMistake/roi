"""Voice files are isolated and cleaned, even when download or transcription fails."""

import asyncio
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import telegram_bot as tb


def make_bot(transcribe):
    bot = tb.TelegramTherapistBot.__new__(tb.TelegramTherapistBot)
    bot.user_langs = {7: "en"}
    bot.sessions = {}
    bot.bot = SimpleNamespace(
        get_file=AsyncMock(return_value=SimpleNamespace(file_path="remote")),
        download_file=AsyncMock(),
    )
    bot._get_therapist = lambda uid: SimpleNamespace(transcribe_audio=transcribe)
    bot._process_text_message = AsyncMock()
    bot._save_user_prefs = lambda **kwargs: True
    return bot


def voice():
    return SimpleNamespace(
        from_user=SimpleNamespace(id=7),
        voice=SimpleNamespace(file_id="same-id"),
        answer=AsyncMock(),
    )


def assert_empty(tmp_path):
    assert not list((tmp_path / "temp_audio").iterdir())


@pytest.mark.asyncio
async def test_success_removes_input_and_preserves_other_media(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tb, "detect_language", lambda text: ("en", 0.9))
    media = tmp_path / "music.mp3"
    media.write_bytes(b"permanent")
    paths = []
    bot = make_bot(lambda path: paths.append(Path(path)) or "hello")

    async def download(remote, path):
        path.write_bytes(b"audio")

    bot.bot.download_file.side_effect = download
    await bot._handle_voice(voice())
    assert not paths[0].exists()
    assert_empty(tmp_path)
    assert media.read_bytes() == b"permanent"
    bot._process_text_message.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["telegram", "partial", "recognition", "provider"])
async def test_failures_remove_input(tmp_path, monkeypatch, failure):
    monkeypatch.chdir(tmp_path)
    def transcribe(path):
        if failure == "provider":
            raise TimeoutError()
        return "Ошибка распознавания" if failure == "recognition" else "hello"

    bot = make_bot(transcribe)
    async def download(remote, path):
        if failure == "partial":
            path.write_bytes(b"partial")
            raise OSError("download failed")
        path.write_bytes(b"audio")

    bot.bot.download_file.side_effect = download
    if failure == "telegram":
        bot.bot.get_file.side_effect = RuntimeError("telegram failed")
    if failure in ("telegram", "partial", "provider"):
        with pytest.raises((RuntimeError, OSError, TimeoutError)):
            await bot._handle_voice(voice())
    else:
        await bot._handle_voice(voice())
    assert_empty(tmp_path)
    bot._process_text_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_waits_for_worker_before_removing_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    started, release = threading.Event(), threading.Event()
    seen = []
    def transcribe(path):
        seen.append(Path(path))
        started.set()
        release.wait(5)
        assert Path(path).read_bytes() == b"audio"
        return "hello"

    bot = make_bot(transcribe)
    async def download(remote, path):
        path.write_bytes(b"audio")
    bot.bot.download_file.side_effect = download
    task = asyncio.create_task(bot._handle_voice(voice()))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()
        assert seen[0].exists()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert_empty(tmp_path)
    bot._process_text_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_during_partial_download_removes_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    started = asyncio.Event()
    bot = make_bot(lambda path: "unused")

    async def download(remote, path):
        path.write_bytes(b"partial")
        started.set()
        await asyncio.Event().wait()

    bot.bot.download_file.side_effect = download
    task = asyncio.create_task(bot._handle_voice(voice()))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert_empty(tmp_path)
    bot._process_text_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_parallel_downloads_with_same_file_id_are_isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tb, "detect_language", lambda text: ("en", 0.9))
    paths = []
    both_started = asyncio.Event()
    bot = make_bot(lambda path: Path(path).read_text())
    async def download(remote, path):
        paths.append(path)
        path.write_text(str(len(paths)))
        if len(paths) == 2:
            both_started.set()
        await both_started.wait()
    bot.bot.download_file.side_effect = download
    await asyncio.gather(bot._handle_voice(voice()), bot._handle_voice(voice()))
    assert paths[0] != paths[1]
    assert_empty(tmp_path)
    assert {call.args[1] for call in bot._process_text_message.await_args_list} == {"1", "2"}
