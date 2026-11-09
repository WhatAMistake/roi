"""The localized help and main keyboard should have no extra entries."""

from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import telegram_bot as tb
from i18n import t


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["ru", "en"])
async def test_help_uses_localized_text_without_appending_commands(lang):
    bot = tb.TelegramTherapistBot.__new__(tb.TelegramTherapistBot)
    bot.user_langs = {7: lang}
    message = SimpleNamespace(from_user=SimpleNamespace(id=7), answer=AsyncMock())

    await bot._handle_help(message)

    message.answer.assert_awaited_once_with(t(lang, "help"), parse_mode="HTML")
    assert t(lang, "help").count("/music") == 1
    assert "/shoot" in t(lang, "help")


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_main_keyboard_has_three_localized_buttons(lang):
    markup = tb.get_main_keyboard(lang)
    buttons = (
        [button for row in markup.keyboard for button in row]
        if tb.AIogram_AVAILABLE else markup.buttons
    )

    assert [button.text for button in buttons] == [
        t(lang, "button_assoc"),
        t(lang, "button_analyze"),
        t(lang, "button_help"),
    ]
