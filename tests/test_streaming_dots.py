import asyncio
from pathlib import Path
import sys
import pytest
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from telegram_bot import TelegramTherapistBot


@pytest.mark.asyncio
async def test_animate_loading_dots_frame_sequence():
    """Verify loading dots cycle: initial 3 -> 2 -> 1 -> 2 -> 3 -> 2 -> ..."""
    bot = TelegramTherapistBot.__new__(TelegramTherapistBot)
    mock_msg = AsyncMock()
    edited_texts = []

    async def fake_edit_text(text):
        edited_texts.append(text)

    mock_msg.edit_text.side_effect = fake_edit_text

    # Run for ~5 ticks with a short interval
    interval = 0.03
    async with bot._animate_loading_dots(mock_msg, interval=interval):
        await asyncio.sleep(interval * 5.5)

    # Initial text was "..." (sent before context manager)
    # The frames emitted by _animate_loading_dots should follow:
    # 2 dots -> 1 dot -> 2 dots -> 3 dots -> 2 dots ...
    assert len(edited_texts) >= 4
    expected_sequence = ["..", ".", "..", "...", "..", "."]
    assert edited_texts[:4] == expected_sequence[:4]


@pytest.mark.asyncio
async def test_animate_loading_dots_stops_cleanly_on_exit():
    """Verify animation stops immediately when context manager exits."""
    bot = TelegramTherapistBot.__new__(TelegramTherapistBot)
    mock_msg = AsyncMock()
    edited_texts = []
    mock_msg.edit_text.side_effect = lambda text: edited_texts.append(text)

    interval = 0.05
    async with bot._animate_loading_dots(mock_msg, interval=interval):
        # Very short sleep, finishes before first tick
        await asyncio.sleep(0.01)

    # After exit, no further edits should occur even if we wait
    count_at_exit = len(edited_texts)
    await asyncio.sleep(0.1)
    assert len(edited_texts) == count_at_exit


def test_animate_loading_dots_default_interval():
    """Verify default interval is tuned to 0.37s."""
    import inspect
    sig = inspect.signature(TelegramTherapistBot._animate_loading_dots)
    assert sig.parameters["interval"].default == 0.37



@pytest.mark.asyncio
async def test_animate_loading_dots_ignores_edit_errors():
    """Verify exceptions in edit_text (e.g. Telegram rate limit) do not crash the runner."""
    bot = TelegramTherapistBot.__new__(TelegramTherapistBot)
    mock_msg = AsyncMock()
    mock_msg.edit_text.side_effect = Exception("Telegram API error / rate limit")

    interval = 0.02
    async with bot._animate_loading_dots(mock_msg, interval=interval):
        await asyncio.sleep(0.08)

    assert mock_msg.edit_text.call_count >= 2
